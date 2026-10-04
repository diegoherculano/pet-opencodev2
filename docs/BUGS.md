# Histórico de bugs

Bugs corrigidos, com sintoma, causa e verificação. Visão geral em [README](../README.md).


### 1. Nenhum sprite era desenhado

O código lia `config["asset"]` e `config["name"]`, mas os 1738 `pet.json` do
repositório usam `spritesheetPath` e `displayName`. `asset_path` ficava
`None` e aparecia só o balão de texto. `theme.py` agora aceita as duas
grafias.

### 2. WebP sem decodificador

Todas as spritesheets são `spritesheet.webp`, mas a build do PySide6
instalada aqui traz o WebP como plugin, e o pacote não estava instalado:

```
>>> [bytes(f).decode() for f in QImageReader.supportedImageFormats()]
[... 'png', 'svg', ...]      # sem 'webp'
```

`assets.py` contorna isso percorendo candidatos — o asset configurado,
`preview.gif` e qualquer outra imagem do diretório — e usa o primeiro que o
Qt de fato decodifica, dizendo no log o que pulou. A correção definitiva é
`sudo apt install qt6-image-formats-plugins` (ver seção acima).

### 3. A spritesheet inteira era desenhada

Com o WebP funcionando, o asset passou a carregar, e a folha inteira de
1536×1872 era desenhada espremida na caixa de 330×270 — 72 Eevees
minúsculos lado a lado, em vez de um.

A grade não é adivinhada: foi medida por projeção de alpha, e o resultado é
constante nos 1738 temas. `assets.SpriteSheet` fatia a imagem em células e
o widget anima dentro do laço detectado (ver "Como a spritesheet é lida").

### 4. SIGABRT ao fechar o pet

Sair com o stream aberto derrubava o processo com `exit 134` e
`QThread: Destroyed while thread is still running`. Acontecia em 100% dos
fechamentos — ou seja, o botão direito sempre matava o aplicativo.

`shutdown()` chamava `thread.quit()`, mas `monitor.run()` estava preso em
`readline()` do socket e não retornava por causa do `quit()`. O
`thread.wait(5000)` estourava e a thread era destruída em execução.

Duas mudanças resolveram:

- **`http.py`** — o stream passou de `urllib.request` para
  `http.client.HTTPConnection`, porque `conn.sock` é público e
  `sock.shutdown(SHUT_RDWR)` destrava a leitura na hora. Fechar a resposta
  (`response.close()`) não resolve: medido aqui, leva 8,48s e ainda falha.
- **`monitor.py`** — `stop()` agora também desliga o socket, e não apenas
  marca o `stop_event`, que a thread bloqueada nunca chega a checar.

Medido nos mesmos 8 instantes de fechamento, com o servidor real:

| | antes | depois |
| --- | --- | --- |
| exit code | `134` (SIGABRT) | `0` |
| thread ao sair | ainda rodando | parada |
| atraso no fechamento | estourava o `wait(5000)` | imediato |

### 5. Ctrl+C não fechava

`Ctrl+C` imprimia `KeyboardInterrupt` em `animate` e o processo continuava
rodando. Causa: o `SIGINT` levanta `KeyboardInterrupt` dentro do slot que o
Qt está executando, o PySide6 imprime o traceback e o event loop segue.

`install_quit_signals()` registra um handler para `SIGINT` e `SIGTERM` que
chama `quit()`, que passa pelo mesmo `aboutToQuit` → `shutdown()` limpo.
Responde em no máximo um tique do timer (100ms).

Medido com `SIGINT` e `SIGTERM` reais no processo:

| | antes | depois |
| --- | --- | --- |
| exit code | `130`, traceback, seguia rodando | `0` |
| tempo até sair | não saía | 0,08s |

`PetApplication` passou a herdar de `QApplication` e sobrescreve
`notify()`, que registra com contexto erros de *event handlers*
(`paintEvent`, `mousePressEvent`…) em vez de imprimi-los soltos.

### 6. O pet ficava preso em "Thinking"

Depois que o turno terminava, o pet não voltava para "pronto".

Duas causas, nenhuma delas introduzida pelo refactor — o monólito original
tinha exatamente as mesmas regras:

**O estado "aguardando" nunca acontecia.** As regras do monólito
procuravam `permission.asked` e `form.created` **por substring**, e era
por aí que elas pegavam: só por acaso, e só porque a lista de
compatibilidade estava no fim da tabela.

**"working" só entrava por acaso.** Duas regras por substring —
`step.started` e `tool.called` — casavam com `session.step.started` e
`session.tool.called` por contenção. Todo o resto da atividade real
(`session.text.started`, `session.reasoning.started`,
`session.execution.started`, `session.compaction.started`…) passava
batido.

E, mais importante: **não havia como sair de "working"**. As regras de
volta dependiam de `session.idle` / `session.updated`, e o stream é
documentado como volátil — *"events during disconnection are missed"*.
Um evento perdido e o pet ficava em "Thinking" para sempre.

Corrigido em duas frentes:

- `events.py` reescrito com o vocabulário real (ver "Eventos e estados").
- `petwatch/idle.py`, um watchdog que volta para "pronto" após 5s sem
  nenhum evento, como rede de segurança.

Verificado com o pet real contra o servidor real, num turno inteiro:

```
 0.08s  idle
30.69s  working
36.54s  WATCHDOG -> idle
```

### 7. "Pronto" aparecia durante edição e execução

Eventos de edição de arquivo e de shell não tinham regra, então não
contavam como trabalho. Corrigido em `events.py` (ver "Eventos e estados").

### 8. O watchdog de 5s cortava trabalho real

Medindo os intervalos reais entre eventos, 20 silêncios passaram de 5s e o
maior durou 39s. Subiu para 45s.

### 9. `progress_changed` declarado como `Property` em vez de `Signal`

`petwatch/idle.py` declarava ``progress_changed = Property(...)``, o que
sobrescreve o nome do atributo por um ``float`` — e ``set_progress`` tentava
chamar ``.emit()`` nele. Isso só aparecia quando o estado era
``waiting``. Agora ``progress_changed`` é um ``Signal(float)`` e a
``Property`` se chama ``progress``.

Causado por mim: eu declarei a propriedade fora do bloco ``__init__`` da
classe, no nível do módulo, onde ``Property`` perde o nome associado.

### 10. Não dava para voltar ao tamanho anterior

Trocar de tamanho funcionava, mas **voltar ao preset que já estava
selecionado** não: o item marcava no menu e o pet não redimensionava.

`PetMenu._pick_size()` ignora o clique no tamanho ativo, o que é correto
— só que comparava com `MenuHandlers.current_size`, e `MenuHandlers` é um
`dataclass(frozen=True)` montado uma vez em `PetApplication.__init__`. O
valor era o do **tamanho inicial do processo** e nunca mais mudava. O
preset de partida ficava inalcançável: `small → large → small` era engolido
pela guarda.

A falha se disfarçava de sucesso porque o `QActionGroup` é exclusivo e marca
o item no clique, antes do handler: o menu confirmava uma troca que nunca
aconteceu. `save()` também não era chamado, então o tamanho errado voltava
no próximo arranque.

O menu agora guarda o preset vigente em `self._current_size`, atualizado
pelo `sync_size()` que já existia. `sync_size()` também passou a casar por
chave em vez de pelo texto do rótulo.

Coberto por `tests/test_menu.py`, que fecha o ciclo em todos os presets a
partir dos três pontos de partida.

### 11. O pet parou de se mexer

O timer de animação continuava disparando a 10 Hz e os contadores
continuavam andando, mas a janela não redesenhava: `animate()` só pedia
repaint quando o pulso de "precisa de você" estava no ar.

```python
    def animate(self) -> None:
        self.animation_frame += 1
        self.advance_sprite()

        if self.attention.progress > 0.0:   # <- a condição que congelou o pet
            self.update()
```

`sprite_frame` e `animation_frame` mudavam, mas `paintEvent` não era
chamado — o quadro da tela só mudava quando algo mais pedia repaint
(mudar de estado, trocar de tema, mexer no tamanho). O sintoma era o pet
parado no meio da tela, sem o bob e sem a caminhada.

**Só o estado `waiting` continuava se mexendo**, e é isso que escondia o
defeito: o `QPropertyAnimation` do pulso emite `progress_changed` a cada
quadro, e esse sinal termina em `update()`. O teste errado foi tratar o
repaint do pulso como se cobrisse o do sprite — são dois ritmos
diferentes, e o do pulso só existe enquanto o pulso roda.

Medido com o event loop real, contando `QEvent.Paint` na janela em 800ms
(8 tiques do timer):

| estado | antes | depois |
| --- | --- | --- |
| `connecting` | 1 | 8 |
| `idle` | 1 | 8 |
| `working` | 1 | 8 |
| `waiting` | 56 | 56 |

O `waiting` é o mesmo antes e depois porque o pulso já repintava sozinho;
a diferença é que agora o sprite também anda ali.

`animate()` voltou a pedir repaint sempre. Repaints duplicados não custam
nada: o Qt junta a região suja antes de pintar.

A suíte não pegou isso porque `tests/test_ui.py` só conferia os
contadores depois de `animate()` — e os contadores estavam certos. O que
falta era o teste de ponta a ponta, em `tests/test_ui.py::RepaintTests`,
que conta `QEvent.Paint` com o event loop rodando, mais o teste do
`update()` em si para os quatro estados.

O log também ajudava a confundir: `load_asset()` logava o laço **antes**
de `apply_action_row()` definir o laço do estado, então imprimia sempre
`(anima 0..0 de 72)` — a leitura de que não havia quadros, quando o
problema era outro. O log foi movido para `announce_asset()`, que roda
depois da linha de ação: agora abre com `anima 56..61 de 72`.
`tests/test_ui.py::AssetLogTests` confere que a mensagem bate com o laço
real do widget.

### 12. A suíte dependia do `~/.config` de quem a rodava

`test_default_theme_is_loaded` falhava na máquina do usuário e passava
onde não há `prefs.json`. Com o arquivo apontando para `pikachu-3d`, o app
de teste abria o Pikachu e o teste comparava com `eevee`:

```
AssertionError: 'Pikachu (3D)' != 'Eevee'
```

O app estava certo — respeitar as preferências é o que ele deve fazer. O
erro era o teste depender do ambiente, e havia um efeito colateral pior:
`shutdown()` chama `save()`, então **cada execução da suíte sobrescrevia o
`prefs.json` do usuário** com o tema, o tamanho e a flag de topo do app
de teste. Rodar os testes apagava a preferência de quem estava testando.

`PetApplication` agora aceita `prefs_path`, repassado ao `load_prefs()` e
ao `save_prefs()` — que já aceitavam caminho desde o começo, mas o app não
usava. `tests/test_app.py` aponta para um `mkdtemp()`, então a suíte não lê
nem escreve o arquivo real.

Comprovado nos dois sentidos: a suíte fica verde com o `prefs.json` real
apontando para outro pet, e o `md5sum` do
`~/.config/petwatch/prefs.json` é idêntico antes e depois dos 359 testes.

`tests/test_app.py::PrefsIsolationTests` cobre os dois lados: o arquivo
isolado é lido e gravado, e o arquivo do usuário não muda — inclusive
através de `shutdown()`.

### 13. `flock` indisponível virava "já existe um pet rodando"

O `claim()` da instância única tratava **qualquer** `OSError` do
`fcntl.flock` como "outro processo segura o lock". Não é assim: o lock
ocupado responde `EWOULDBLOCK`, e um erro de ambiente responde outra coisa —
`ENOLCK` em sistema de arquivos sem suporte a lock (rede, DrvFs do WSL,
alguns fuse). O sintoma era o pior possível para diagnosticar:

```
$ python pet.py
[pet] já existe um pet rodando (pid None). Para encerrá-lo: pet.py --stop
$ python pet.py
[pet] já existe um pet rodando (pid None). …
```

Exit 0, nenhum pet na tela, um `pid None` que não é de ninguém — e nada
diz onde veio. Foi exatamente o que aconteceu na primeira execução manual
deste recurso, antes de a causa aparecer.

Agora só `LOCK_BUSY_ERRNOS` significa "ocupado". Qualquer outro erro é
falha do ambiente: o app sobe **sem** a garantia de instância única, com um
aviso no log dizendo exatamente isso. Confundir "não sei" com "já tem"
traz um pet que nunca abre; o outro erro só traz um segundo pet em
excepcional caso.

### 14. Dois bugs da sonda de lock, achados pelos testes

**A sonda ficava com o lock.** `running()` tentava o lock para saber se
alguém segurava e, quando conseguia — isto é, quando *ninguém* segurava —
segurava para sempre. O próprio `--status` virava a instância e bloqueava
o app; na suíte, um teste contaminava todos os outros da classe.

**Arquivo ausente virava "rodando".** `claim(create=False)` devolvia
`False` tanto para "outro segura o lock" quanto para "o arquivo não
existe", e `running()` lia esse `False` como "tem pet". O sintoma era o
`--status` dizer que havia um pet para sempre, mesmo com o `pet.pid`
inexistente. Agora quem responde é `lock_is_held()`, que distingue os três
casos: ocupado, livre e inexistente.

Os dois apareceram na primeira rodada da suíte, logo depois de escritos.
Vale o registro porque nenhum deles é visível olhando o código:
`running()` é uma linha, e o bug do `pid None` nem acontece na máquina em
que o `flock` funciona — só onde ele não existe.

### 15. O balão mostrava "Thinking" no meio da pergunta

Sintoma reportado: o opencode fazia uma pergunta, e o pet ficava dizendo
"Thinking" com a pergunta aberta na tela.

**Não era o evento da pergunta.** `form.created` chegava e o balão mostrava
"Waiting" — medido no servidor real, 0,18s depois da pergunta. Less de um
segundo depois, um `shell.exited` de **outra aba** devolvia tudo para
"Thinking":

```
18:29:35.448  form.created       -> waiting    bubble=('Waiting', 'needs your answer')
18:29:36.081  shell.exited       -> working    bubble=('Thinking', 'working on it')
```

A causa é estrutural, não um nome errado. `state_from_event` é um
dicionário puro: cada evento é julgado sozinho e o último que casar vence.
O pet escuta **um** servidor e o uso normal é ter várias abas do opencode
abertas, então qualquer atividade de qualquer aba sobrescreve o
"aguardando". Um evento que não devia ser estado (`shell.exited` marca o
*fim* de um comando) virava estado e derrubava a trava.

Corrigido com `StateTranslator` em `events.py`, que é o que o monitor passa
a usar: a resposta pendente vira **trava**, e só a resposta ou o fim do
turno a solta. A trava é solta também quando o stream reconecta, porque ele
é volátil e o que ficou para trás é desconhecido.

> **A trava virou o defeito — ver [bug 18](#18-waiting-falso-positivo).**
> Ela resolvia este bug e criava o oposto: como o stream é volátil por
> contrato, uma resposta perdida deixava o balão travado para sempre, e um
> `form.created` de outro projeto travava o balão deste. O que substituiu
> a trava é a consulta de pendência ao servidor (`pending.py`), e não uma
> regra melhor no stream.

O que continua valendo é o watchdog: ele conta todo evento, inclusive os
que a trava ignora — uma aba trabalhando sustenta o "Thinking" se a outra
for a que está esperando.

O README afirmava, antes, que o opencode **não** emitia
`permission.asked` nem `form.created`, e que os nomes reais eram
`session.permission.create` / `session.form.create`. Está de cabeça
trocada: o barramento emite `permission.asked` e `form.created` — foi
verificado no stream de um 2.0.22 rodando, criando uma pergunta de
verdade — e `session.*.create` são as **rotas HTTP** que criam esses
pedidos. As tabelas foram corrigidas e os dois vocabulários seguem vivos:
os nomes novos como exatos, os antigos nas regras por substring.

### 16. O pulso de "aguardando" rodava na thread errada

Achado junto com o bug 15, ao instrumentar o pet de verdade:

```
[pet] working -> waiting   bubble=('Waiting', 'needs your answer')
QObject::startTimer: Timers cannot be started from another thread
```

`state_changed` é emitido na thread do monitor e `set_state` mexe no
`QTimer` e no `QPropertyAnimation` do pulso de atenção — que só podem ser
tocados na thread que os criou. Ligados com `AutoConnection`, PySide6
chama método Python puro **na thread que emitiu**, porque não há como
descobrir a thread dona de um método que não é `Slot`. Resultado: o
`QTimer.start()` era recusado, o pulso nunca rodava — e o pulso é a razão
de o estado `waiting` existir.

Duas mudanças, e a segunda é a que importa:

- `PetRenderer.set_state` passou a ser um `@Slot(str)`, que é o que dá ao
  PySide6 a thread dona do receptor.
- A conexão passou a pedir `Qt.ConnectionType.QueuedConnection`
  explicitamente, para a intenção não depender de inferência.

Só o `@Slot` não bastava para o teste passar, e o motivo é o mesmo do
outro lado: sem ele o PySide6 não consegue a quem entregar o evento
enfileirado, e a entrega ia para a thread do monitor — que não tem event
loop — e o estado nunca chegava ao balão. O sintoma mudava de "chama na
thread errada" para "não chama", o que é pior de diagnosticar porque não
imprime nada.

`tests/test_app.py::WiringTests` confere que `set_state` roda na thread
principal quando o sinal vem de outra.

### 17. O balão falava antes de haver o que dizer

O `connecting` tinha rótulo (`"Connecting" / "starting up"`), e como é o
estado inicial do widget **e** o estado de reconexão, ele era a primeira
coisa na tela a cada abertura do pet: um balão dizendo o que ainda não
sabe. Pior de diagnosticar, era o estado em que a reconexão acontece —
cada queda do stream apagava a mensagem que o usuário estava lendo e
trocava por "Connecting", mesmo com o opencode ligado e sem nada errado.

Silêncio também não é de graça. Esvaziar só o par de rótulos não resolve:
`layout_for` continuaria medindo um texto de largura zero e `paint`
desenharia a caixa de qualquer forma, com o fundo, a borda e a sombra. O
que aparece é um retângulo claro vazio no alto do sprite, que parece bem
pior que o texto que se queria tirar.

A correção é das duas metades:

- `STATE_LABELS["connecting"]` é `("", "")`, e um estado com as duas linhas
  vazias é **mudo por contrato** — vale para qualquer estado que alguém
  quiser silenciar depois.
- `PetRenderer.draw_status` volta antes de medir quando não há texto
  algum, então nem caixa nem contorno de pulso são pintados.

O que continua igual é o sprite: ele tem a linha de ação do `connecting`
(56..61, andando para baixo), então o pet continua visível e se mexendo
enquanto conecta. É o sprite que diz "estou vivo"; o balão só entra
depois, com `Ready`.

`tests/test_ui.py` confere as duas metades: `StateLabelsTests` para o
rótulo vazio e `RenderTests::test_connecting_draws_no_bubble_at_all`
contando pixels do render com o balão ligado e desligado — com o estado
mudo os dois renders são idênticos, e o render ainda tem pixels, que são
os do sprite.

### 18. "Waiting" falso positivo

Sintoma reportado depois do bug 15: o balão entrava em "Waiting / needs
your answer" — e pulsava — **sem haver nada para o usuário responder**.

A correção do bug 15 (a trava de `StateTranslator`) tinha trocado um
defeito por outro, porque as duas coisas que ela protegia são
documentadas como não-confiáveis:

- **`/api/event` perde eventos.** A spec v2 diz, na própria rota:
  *"Volatile by contract: a slow consumer overflows and fails the stream,
  and events during disconnection are missed."* A trava só era solta por
  um evento de resposta (`form.replied`, `form.cancelled`,
  `permission.replied`) ou por reconexão. Perdido o evento de resposta e
  mantida a conexão, o balão ficava travado **para sempre** — e o
  watchdog não salva, porque `IdleWatchdog.is_due()` só age no estado
  `working`, por decisão deliberada (esperar o usuário pode levar
  minutos).
- **`/api/event` é de todas as locations.** A mesma frase da spec:
  *"across all server locations"*. A documentação da CLI v2 diz que há
  *"one shared background server"* e que *"every local OpenCode client
  connects"* a ele. Então um `form.created` de qualquer projeto — uma
  pergunta em uma aba em segundo plano — travava o balão de todos os
  outros, e o pet ignorava o `location.directory` que vem no payload.

Um evento que chega não é evidência de que algo está esperando: é
evidência de que algo **chegou para ser verificado**. A correção troca a
fonte do estado em vez de tentar consertar a heurística:

| Antes | Agora |
| --- | --- |
| `form.created` → `waiting`, direto | `form.created` → `poke()` |
| trava solta por evento de resposta | `GET /api/form?location[directory]=…` e `GET /api/permission/request?location[directory]=…` |
| sem prazo: resposta perdida = espera eterna | ciclo de 2s enquanto há pendência, 20s quando não há |
| ignora o projeto do evento | consulta por *location*, com a lista em `GET /api/project` |

O que isso compra, em ordem:

1. **Resposta perdida não trava mais.** Quem responde é o servidor, e ele
   responde "não há nada" mesmo que o stream nunca tenha contado a
   resposta.
2. **Um `form.created` de outro projeto não trava este.** A consulta é por
   *location*; se o pedido é de outro projeto, ele aparece no balão
   daquele projeto — com a lista de projetos do próprio servidor, e não
   com a aba barulhenta.
3. **Reconexão deixa de ser um palpite.** O pet pergunta assim que conecta
   em vez de assumir que não há nada pendente (ver `reconnected`).
4. **Falha não vira resposta.** `note_pending(None)` é no-op: se a
   consulta falhou, o estado anterior continua. Inverter "aguardando" para
   "trabalhando" sem saber seria inventar uma resposta do usuário.
5. **Degradação explícita.** Num servidor sem as rotas (v1), o watcher
   desliga, avisa uma vez no log, e a trava por stream assume sozinha.

`StateTranslator` e `awaiting_answer` foram removidos: a memória sobre o
que está pendente era a causa, não a cura. O watchdog passou a seguir o
estado **visível** (o que o quadro escolheu), e não o do stream — assim
uma espera real continua imune ao timeout de 45s.

O contrato novo está em `tests/test_pending.py` (as consultas) e
`tests/test_sessions.py` (o quadro e o laço): envelope `{location, data}`,
`location[directory]` no formato `deepObject` da spec, 404 como degradação,
500 de um *location* isolado em vez de derrubar a consulta, `None` de uma
consulta falha como "não deu para saber", e os dois falsos positivos como
testes que falhavam com o código anterior.

### 19. Um balão para cada instância do opencode em ação

O balão era um resumo sem dono: ele dizia "Thinking" e não dizia **de qual
aba**. Com várias abas em projetos diferentes — o uso normal — a única
informação útil sumia.

A correção foi fazer o balão ser por instância, o que puxou três
mudanças que não são cosméticas:

- **`monitor.session_event`** passa a carregar o `sessionID` do evento
  (`events.extract_session_id`). O estado é por sessão, e não global.
- **`SessionBoard`** (`sessions.py`) substituiu o `StateArbiter`: é ele
  que tem a lista de instâncias e o estado do pet. A regra do bug 18 foi
  mantida palavra por palavra — uma consulta que falha não muda o estado.
- **`StatusPoller`** (o laço, que era o `PendingWatcher`) ganhou duas
  consultas: `/api/session/active` (quem está em ação) e
  `/api/session/{id}` (o nome), ambas em cache — o nome de uma sessão não
  muda enquanto ela dura.

Os balões foram empilhados numa coluna, e não lado a lado: a janela ficou
com a largura de um balão (152px no preset médio, contra os 472px que três
colunas exigiriam) e o que passa a crescer é só a altura — até seis
balões, porque a janela cresce para cima a partir do canto inferior
direito e o sprite fica no rodapé.

Um detalhe que só a leitura da spec e uma verificação contra o servidor
v2 de verdade revelaram: um *location* que não é um projeto responde
**500**, não 404. Se a consulta de um location apenas desse erro, um
projeto apagado da lista calaria o pet inteiro — então `pending_asks`
isola a falha por *location* e só trata como erro quando nenhum responde.

