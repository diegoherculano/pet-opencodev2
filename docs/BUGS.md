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


### 20. "Thinking" com a pergunta aberta na tela

Sintoma reportado depois do bug 18: o opencode fazia uma pergunta — a janela
parava nela, esperando o usuário — e o pet ficava dizendo "Thinking".
NÃO era o evento da pergunta, e não era o fim de turno: eram **três defeitos
que só apareciam juntos**, e cada um escondia o próximo.

**Um projeto morto desligava o "aguardando" inteiro.** O servidor v2
responde **404 por duas coisas diferentes**, e o status não distingue
nenhuma:

```
$ curl -i .../api/form?location\[directory\]=/projetos/que-nao-existe
HTTP/1.1 404 Not Found
content-type: application/json

{"_tag":"LocationNotFoundError","location":{...},"message":"Location not found: ..."}

$ curl -i .../api/formXYZ
HTTP/1.1 404 Not Found
                       # corpo vazio — a rota não existe
```

`GET /api/project` guarda diretórios que já não têm pasta, e
`/projetos/que-nao-existe` estava na lista da máquina onde o bug foi
achado. `pending_asks` tratava qualquer 404 como "servidor v1, sem a
rota" e devolvia `None` — que significa *degradação*. Então:

```
>>> pending_asks(49374, senha, watched_directories(...))
None          # degradação: o recurso inteiro desligado
```

A distinção agora é o `_tag` do corpo (`pending.is_location_not_found`): a
rota ausente vem com o corpo **vazio**, e o *location* morto vem com
`LocationNotFoundError`. O segundo é isolado como já era o 500 — o
diretório não tem nada pendente, e os outros continuam sendo consultados.

**A degradação que sobrou tinha um defeito próprio.** Com o recurso
desligado, quem decide "aguardando" é a trava do stream — e a trava era um
booleano global, exatamente o defeito do bug 15, que só não aparecia
porque a degradação nunca acontecia de verdade:

```
>>> b.note_ask()                          # form.created
>>> b.state
'waiting'
>>> b.note_event("ses_outra_aba", "idle")  # fim de turno de OUTRA aba
>>> b.state
'working'                                  # <-- a pergunta continua aberta
```

O stream é global (*"across all server locations"*), então o
`session.idle` de uma aba não diz nada sobre a pergunta de outra. A trava
agora guarda **de quem** foi o pedido (`SessionBoard._latched`), e o
`monitor` extrai o `sessionID` uma vez e o entrega em `ask_seen` e no
`released` novo. Um pedido sem dono continua valendo — ele não tem a qual
balão se atribuir, então só mexe no sprite.

**O sprite e o balão discordavam.** `app._cards()` decidia sozinha quando
pintar "Waiting", usando `needs_action`, que só o servidor preenche. Na
degradação o quadro dizia "esperando" e o balão dizia "Thinking": o
primeiro mudava, o segundo não. A regra mora agora em
`SessionBoard.cards()` e a interface só desenha o que o quadro arbitrou.

Verificado contra o servidor 2.0.23 rodando, com o projeto morto na lista:

| | antes | depois |
| --- | --- | --- |
| `pending_asks` com 8 projetos | `None` (degradação) | `[]` |
| `session.idle` de outra aba, degradado | `working` | `waiting` |
| balão da aba que perguntou, degradado | `thinking` | `waiting` |
| projetos mortos reconsultados | 2 GETs a cada ciclo | 0 (lembrados) |

Um *location* morto é anotado (`StatusPoller._missing`) e sai da varredura
— reconsultá-lo a cada ciclo são dois GETs para sempre. A lista de projetos
é relida no TTL, então um diretório que reaparecer é pego de volta sem
reiniciar o pet. `PETWATCH_DIRECTORY` nunca é filtrada: ali é o usuário
escolhendo o lugar, e o pet não discorda.

O aviso de degradação também subiu de `INFO` para `WARNING`: ele estava no
log desde o primeiro bug 18, e em `--foreground` — que é onde o log é
lido — ele é a pista de que o balão pode atrasar.

Coberto por `tests/test_pending.py` (`LocationNotFoundTagTests`,
`PendingAsksTests`), `tests/test_sessions.py` (`DegradedTests`,
`Bug20DeadLocationTests`), `tests/test_idle.py` (`PendingQuestionTests`) e
`tests/test_app.py` (`CardTests`), que é onde o sintoma era visto.

### 21. "Thinking" para sempre, com o agente parado

Sintoma reportado: *"sempre que eu entro com o pet, me parece que ele trava a
comunicação do opencode e preciso falar com o agente para continuar de onde
parou, mas ele fica no modo como se tivesse pensando mas parece parado"*.

**Não é o pet que trava o opencode.** Isso foi medido antes de qualquer
mudança, e o resultado importa porque é o que descartou a hipótese mais
óbvia:

| medição, contra o servidor real | resultado |
| --- | --- |
| latência de `/api/session/active` com o pet rodando | 0,5–1,2 ms |
| 1200 GETs do pet em sequência | 2201 req/s, `active` **inalterado** |
| 40 assinantes de `/api/event` simultâneos | assinante anterior **não** caiu |
| 60 assinaturas de `/api/event` abertas e **descartadas** sem ler (o que o monitor faz a cada reconexão) | observador **continuou** recebendo, maior silêncio 5,47 s |
| stream do observador durante 90 s de monitor + poller reais | **1** conexão, **0** exceções, nada perdido |

O pet só faz `GET`, e nenhuma delas tem efeito colateral. A comunicação do
opencode não é bloqueada por ele.

**O que trava é o balão, não o opencode.** O agente de fato congela — e o
`opencode.log` mostra por quê: `Failed to drain Session`, o *drain* da
sessão morre. Aí o servidor **continua listando a sessão** em
`/api/session/active` como `running`, e nenhuma requisição volta a mexer
nisso. Medido: uma sessão ficou 90 s na lista de ativas com **nenhum evento
no stream**.

E o pet tratava essa sessão como trabalhando para sempre. **Duas falhas
independentes**, e cada uma sozinha bastava:

**1. `demote_stale` media o relógio errado.** Ele comparava
`Instance.touched_at` com o prazo, e `touched_at` é reescrito por
`note_active` **a cada ciclo** do `StatusPoller` (2 s a 20 s):

```
>>> b.note_active([SID])          # touched_at = 1.0
>>> for _ in range(90): b.tick(); b.demote_stale(45)
...  # 90 ciclos: touched_at = agora, sempre. prazo nunca alcançado.
```

`touched_at` media "o servidor ainda lista esta sessão", que é justamente o
que **não** prova que ela está trabalhando. Agora existe
`Instance.evented_at`, que só o stream move — `note_event` o atualiza, e
`note_active` o inicializa no momento em que promove a instância de
`connecting` para `working`. Esse último detalhe é o que cobre a sessão que
trava **antes** do primeiro evento: sem ele o relógio nunca começaria, e
`demote_stale` a leria como "ainda não falou" para sempre.

**2. `demote_stale` só rodava por trás do watchdog global.** A única chamada
era `app.on_watchdog_idle`, e o watchdog global exige `IDLE_TIMEOUT` de
silêncio **do servidor inteiro**. Com outra aba do opencode trabalhando — o
uso normal, várias abas abertas — `note_activity` chega o tempo todo e ele
**nunca dispara**:

```
=== com outra aba ativa, antes da correção ===
  t= 120.0s  pet='working'  aba travada='working'
  t= 300.0s  pet='working'  aba travada='working'   <- 300 s, e contando
  idle_reached disparou: []
```

Agora `on_active` também envelhece o balão, que é o lugar natural: é a
consulta de ativas que descobre que a instância continua listada, então é
ela que pode conferir que a instância de fato calou.

O efeito, na mesma simulação, com a outra aba realmente ativa:

| | antes | depois |
| --- | --- | --- |
| aba travada, 300 s depois | `working` | `idle` |
| aba que trabalha, 300 s depois | `working` | `working` |

O contra-teste importa mais que o teste: `test_a_session_that_keeps_working_is_not_released_by_the_poll`
e `test_a_session_that_keeps_speaking_stays_working` garantem que o poll não
rebaixe quem está trabalhando de verdade — senão a correção trocaria "Thinking
eterno" por "Ready" falso, que é o defeito do bug 11 de outro jeito.

Uma nota sobre o que **não** se mexeu: `demote_stale` pulando
`self._wants(instance)` continua valendo. Uma instância esperando resposta
não "calou" — está esperando o usuário, e o `waiting` continua certo, mesmo
que o silêncio passe de 45 s.

Coberto por `tests/test_sessions.py` (`DemoteStaleTests`, quatro casos) e
`tests/test_app.py` (`WiringTests`, dois). Verificado que os seis falham com
cada metade da correção revertida.

### 22. "Ready" no meio do trabalho

Sintoma reportado, e o **oposto** do bug 21: o agente estava pensando e o
balão dizia "Ready / waiting for you". O que corrigiu o 21 — rebaixar a
instância que calou, com `demote_stale` rodando a cada consulta de ativas —
passou a derrubar trabalho real.

**O relógio de silêncio media a coisa errada.** `Instance.evented_at` só era
movido por `note_event`, e `note_event` só era chamado quando o evento **vira
estado** *e* traz `sessionID`. Medindo o stream de verdade num turno real de
150 s, esse recorte cobre quase nada:

```
=== tipos mais frequentes, 819 eventos em 150s ===
   771  session.reasoning.delta   sessionID=771   estado={None}
     9  session.text.delta        sessionID=  9   estado={None}
     3  shell.created             sessionID=  0   estado={'working'}
     3  shell.exited              sessionID=  0   estado={'working'}
     3  session.tool.called       sessionID=  3   estado={'working'}
```

Duas das três condições falhavam ao mesmo tempo:

1. **94% do stream não vira estado.** `session.reasoning.delta` é um
   instante interno do turno — por desenho ele não pode virar estado, ou o
   balão pisca — e ainda assim é a maior parte de tudo que o opencode emite.
   É o agente pensando, e é o que mais dura.
2. **Os eventos de arquivo e shell não têm `sessionID`.** O *schema* do
   opencode confirma, e o stream capturado também:

   ```
   {"type": "shell.created", "location": {"directory": "/tmp/opencode"},
    "data": {"info": {"id": "sh_…", "command": "sleep 1", …}}}    # sem sessionID
   {"type": "shell.exited",  "location": {"directory": "/tmp/opencode"},
    "data": {"id": "sh_…", "exit": 0, "status": "exited"}}          # sem sessionID
   ```

   O dono deles é o *location*, e ele está no **envelope** — o que o
   `extract_session_id` nunca lia, porque ele desce para `data`.

3. **O carimbo vinha do relógio errado.** `note_event` gravava `self._now`, e
   `self._now` só anda quando a consulta de status passa. Isso não encurta o
   timeout para 25 s — o carimbo anda no tique do poll, então ele só o
   **torna impreciso**: varrendo a fase do último evento dentro do ciclo, o
   silêncio real que derruba o balão vai de **40 s a 60 s** em vez dos 45 s
   que o número significa. Medido:

   ```
   carimbo no relógio do poll : de 40s a 60s
   carimbo no relógio real    : de 46s a 65s
   ```

   É o segundo defeito, e sozinho ele não produz o sintoma: os 39,06 s que o
   projeto mediu para calibrar o timeout continuam acima do pior caso. Ele
   importa porque o timeout deixa de ser um número, e é o número que foi
   medido.

**A segunda metade: o rebaixamento era permanente.** `note_active` só promove
a partir de `STATE_CONNECTING`, então uma vez em `STATE_IDLE` por inferência
de silêncio, **nada** trazia a instância de volta. O balão ficava em "Ready"
pelo resto do turno, mesmo com o agente trabalhando o tempo todo:

```
-- antes --
  t= 41.0s  pet=working   baloes=[('working', 'Taxr', False)]
  t= 51.0s  pet=idle      baloes=[('idle', 'Taxr', False)]     <- o agente segue pensando
  t= 71.0s  pet=idle      baloes=[('idle', 'Taxr', False)]
  stream_state='working'
```

**A correção** separa *prova de vida* de *estado*, porque são coisas
diferentes:

- `monitor.session_alive` publica **todo** evento, com `(sessionID,
  diretório)`. Sai antes das regras, porque prova de vida não pode depender
  da tradução do evento.
- `events.extract_location_directory` lê o `location` do envelope, que dá
  dono aos eventos que não trazem `sessionID`.
- `SessionBoard.note_alive` move o relógio, e **não** muda estado.
- O carimbo passa a ser `self._clock()` (tempo real) em vez de `self._now`
  (tempo do último poll), para o timeout voltar a ser um número só.
- `Instance.demoted` marca que o "pronto" foi um palpite nosso, e
  `note_alive` o desfaz no primeiro evento. O "pronto" que o **servidor**
  disse (`session.idle`) não é desfazível por um delta.

Quando duas instâncias dividem um *location*, as duas contam como vivas: um
evento de arquivo não diz qual das abas do projeto escreveu o arquivo, e errar
para "Thinking" é melhor do que errar para "Ready" no meio do trabalho.

```
-- depois --
  80s com o agente produzindo : ['working'] × 8
  80s de silêncio real        : ['working','working','working','working',
                                 'idle','idle','idle','idle']
  ao falar de novo            : working   (o palpite foi desfeito)
  shell.created (sem sessão)  : working   (dono pelo location)
```

O silêncio de verdade continua voltando para "Ready" — a correção do bug 21
segue valendo, e o simetrico é testado.

Uma nota sobre o que **não** mudou: o `IDLE_TIMEOUT` continua 45 s. Com o
carimbo certo ele volta a valer o que foi medido, e reduzir agora seria trocar
uma medição por um palpite.

**O turno que medi não chega a derrubar o balão, e é por isso que ele está
aqui como número e não como reprodução.** Com 832 eventos em 179,6 s, a maior
lacuna entre eventos *de estado* foi de 40,16 s — e o orçamento sem a correção
era de até 60 s. A simulação só mostra a queda quando o raciocínio passa de
~60 s:

```
  pensando por      sem a correção      com a correção
          45s             não cai             não cai
          60s          cai em 60s             não cai
         120s          cai em 60s             não cai
         200s          cai em 60s             não cai
```

O sintoma reportado é justamente esse: raciocínio longo. E com duas abas
abertas — o uso normal — o *sprite* ainda segura "Thinking" (ele soma as
instâncias), então o defeito aparece **por balão**, o que é onde o usuário
lê.

Coberto por `tests/test_sessions.py` (`NoteAliveTests`, sete casos;
`DemotedIsRevocableTests`, cinco; `NeverSpokeTests`, dois),
`tests/test_idle.py` (`SessionAliveTests`, cinco),
`tests/test_events.py` (`ExtractLocationDirectoryTests`, quatro) e
`tests/test_app.py` (`WiringTests`, três), que é onde o sintoma era visto.
