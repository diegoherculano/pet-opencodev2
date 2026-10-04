# petwatch

Pet de desktop que observa um servidor local do [opencode](../) e mostra o
que está acontecendo: conectando, pronto, trabalhando ou aguardando resposta.
Ao abrir, o pet já aparece animando e **sem texto** — o balão só entra
quando há estado de verdade para contar (ver [bug 17](#17-o-balão-falava-antes-de-haver-o-que-dizer)).

![petwatch em ação: balão "Thinking" sobre o sprite do Eevee](docs/demo.png)

## Instalação

Requer Python >= 3.11 e um servidor `opencode` local. No Ubuntu, o Qt
precisa do plugin de WebP (ver
[Dependência do sistema](#dependência-do-sistema-plugin-de-webp)).

```bash
pip install -e .          # pet + comando `petwatch`
pip install -e ".[dev]"   # + ruff, para contribuir
```

## Como rodar

```bash
python pet.py            # ou: python -m petwatch
```

O comando **não fica preso no terminal**: o processo vai para segundo
plano, a saída vai para `~/.local/state/petwatch/pet.log` e o prompt volta
na hora. Ver [Segundo plano](#segundo-plano).

- **Clique esquerdo** — arrasta o pet pela tela
- **Clique direito** — abre o menu
- **Fechar** (menu) ou `python pet.py --stop` — fecha o pet
- `Ctrl+C` — só funciona em `--foreground`

## Segundo plano

```bash
python pet.py              # segundo plano; prompt na hora, exit 0
python pet.py -b           # idem, explícito (útil em scripts/autostart)
python pet.py --status     # diz se há pet rodando e qual o pid (exit 1 se não)
python pet.py --stop       # encerra o que estiver rodando
python pet.py --foreground # colado no terminal, log e traceback na tela
python pet.py --help
```

O modo normal é segundo plano porque o pet é um serviço de janela, não um
programa de terminal. Como implementar isso **dentro** do app — e não com
`nohup`/`setsid` na linha de comando — dá três coisas que um wrapper não
daria: o processo solto sabe que já existe outro (e diz qual é o pid), o
`--stop` sabe para onde mandar o sinal, e a falha de abertura da janela
chega no terminal em vez de sumir num log.

**O que muda para quem usa.** O prompt volta em milissegundos e o `Ctrl+C`
deixa de valer — o processo não está mais no grupo de jobs do shell, e é
por isso que ele não morreria junto com o shell. Quem encerra é o item
**Fechar** do menu ou o `--stop`. Tudo que ia aparecer na tela vai para
`pet.log`, incluindo o que não é Python: os avisos do Qt e do loader de
plugins escrevem no descritor 2, que agora é o log.

**Como é feito.** Dois `fork` e um `setsid` (um fork só deixaria o processo
como líder de sessão do terminal, que continuaria mandando `Ctrl+C` para
ele), `stdin` no `/dev/null`, e `stdout`/`stderr` apontados para o log.
O desvio vem **antes** do `QApplication`: o Qt precisa abrir a conexão com
o servidor de display depois do `setsid`, e um `QApplication` construído
antes do fork não pode ser usado nos dois processos.

O processo original não some às cegas: ele fica esperando o filho dizer
que a janela abriu, por um pipe, e só então devolve o terminal. Se a janela
não abrir, o motivo chega na tela — do contrário o sintoma de "não aconteceu
nada" seria o silêncio, e o traceback estaria num arquivo que ninguém foi
avisado de que existe.

**Só uma instância.** O processo solto toma um `flock` em `pet.pid`, e o
segundo `python pet.py` percebe o lock ocupado e sai. O lock pertence ao
descritor, não ao processo: um `SIGKILL` faz o kernel soltá-lo, e o
`pet.pid` que sobra vira só um número velho em vez de um bloqueio eterno —
por isso a presença do pet é decidida pelo lock, nunca pelo conteúdo do
arquivo. Verificado com um `kill -9` no meio:

```
$ kill -9 $(cat ~/.local/state/petwatch/pet.pid)
$ python pet.py --status
[pet] não está rodando. Log: /home/…/petwatch/pet.log   # exit 1
$ python pet.py
[pet] rodando em segundo plano (pid 339527). Log: …     # exit 0
```

**Onde o log mora.** `~/.local/state/petwatch/pet.log`, respeitando
`XDG_STATE_HOME`, com `--log` para mudar. Ele passa de 1 MiB e é virado para
`pet.log.1` — uma geração só, o suficiente para o log do processo anterior.

## Menu (botão direito)

| Item | O que faz |
| --- | --- |
| **Tamanho** | Pequeno, Médio, Grande. A fonte do balão escala junto |
| **Selecionar pet…** | Janela com busca e paginação; a troca é em tempo real |
| **Sempre no topo** | Marca/desmarca; lembra entre sessões |
| **Fechar** | Sai do aplicativo |

O seletor lista os **nomes das pastas** em `pets/`, sem ler nenhum
`pet.json`: quem decide quantos pets existem é o usuário, então a pasta é a
fonte da verdade. São 1738 pastas, 10 por página, 174 páginas.

A troca só acontece num clique explícito. Digitar na busca, mudar de página
e filtrar não trocam o pet — do contrário, digitar "pi" já trocaria o pet
para o primeiro resultado.

**"Sempre no topo" não funciona no WSLg.** O Wayland não tem z-order:
`xdg-shell` não tem um pedido de "manter acima" e o plugin do Qt ignora
`Qt.WindowStaysOnTopHint`, então o flag é aceito e descartado. No X11 ele
vira `_NET_WM_STATE_ABOVE` e passa a depender do gerenciador de janelas — o
do WSLg não anuncia o átomo em `_NET_SUPPORTED` e o ignora, mas um
i3/mutter/kwin normal honra. Por isso o item do menu fica visível e
desativado nesses casos, com o motivo na dica, e o app avisa no log em vez
de aceitar a opção em silêncio. Não há como contornar: as janelas do WSLg são
sintetizadas como janelas filhas do Windows, e a ordem na área de trabalho é
decidida pelo compositor do Windows.

Preferências em `~/.config/petwatch/prefs.json` (tema, tamanho, topo).
Gravação atômica e tolerante a arquivo corrompido: o app sempre abre.

`Ctrl+C` e `kill` passam pelo mesmo caminho limpo do botão direito: um
handler de sinal chama `quit()`, que dispara `aboutToQuit` e o
`shutdown()`. Sem esse handler o `SIGINT` levanta `KeyboardInterrupt`
dentro do slot que o Qt está executando; o PySide6 imprime o traceback e o
event loop continua, então o processo não sai. O `SIGTERM` é o que o
`--stop` usa; o `Ctrl+C` só chega ao app em `--foreground`.

## Como funciona

O `opencode` expõe um stream SSE autenticado em `/api/event`. O pet fica
ouvindo esse stream numa thread separada e traduz cada evento em um de
quatro estados. O balão é **um por instância do opencode em ação** — o
estado vem do evento *filtrado pelo `sessionID` que o produziu*, e a lista
de instâncias vem de `GET /api/session/active`.

O "aguardando" não vem do stream: vem de consultas HTTP ao servidor. Ver
[A pergunta decide sozinha](#a-pergunta-decide-sozinha).

```
pets/<tema>/pet.json ──► theme.py ──► PetTheme
                                    │
                          assets.py ─┴─► imagem decodificável
                                    │
discovery.py (senha + porta) ──► sse.py ──► events.py ──┬─► estado da sessão
                                                          │   (com sessionID)
                                        monitor.py (thread)
                                                          │
                            sessions.py (SessionBoard) ◄──┘
                                    │
             pending.py (consultas) ┴─► quem está em ação, e o que espera
                                    │
                                        ui/pet_widget.py ◄─┘
                                        (pilha, um acima do outro)
```

Quem decide o estado visível é o `SessionBoard`: "aguardando" ganha de
tudo enquanto o servidor confirmar, e o stream diz o resto. O **pulso** é
do sprite, e o **aviso colorido** é só do balão que espera resposta.

## Módulos

| Módulo | Responsabilidade |
| --- | --- |
| `config.py` | Constantes, timeouts e caminhos |
| `daemon.py` | Segundo plano: fork, log, instância única, `--stop` |
| `states.py` | Os quatro estados e seus rótulos |
| `theme.py` | Lê `pet.json` e resolve o caminho do sprite |
| `assets.py` | Escolhe o primeiro asset que o Qt consegue decodificar |
| `http.py` | Transporte do stream, com leitura interrompível |
| `discovery.py` | Senha via CLI, portas via `ss`, sondagem do servidor |
| `sse.py` | Parser do stream `text/event-stream` |
| `events.py` | Regras declarativas de evento → estado, e os gatilhos de pedido |
| `pending.py` | Consultas de pendência e de sessão ao servidor v2 |
| `sessions.py` | Quadro de instâncias (um balão cada) e o laço que pergunta |
| `idle.py` | Watchdog: volta para "pronto" se o opencode silenciar |
| `sizes.py` | Presets de tamanho, com a pilha de balões e a escala do texto |
| `prefs.py` | Preferências persistidas |
| `ui/attention.py` | Pulso de "precisa de você" no estado de espera |
| `ui/menu.py` | Menu do botão direito |
| `ui/pet_picker.py` | Seletor de pet com busca e paginação |
| `monitor.py` | Loop de conexão e reconexão (roda em `QThread`) |
| `ui/bubble.py` | Medição, empilhamento e pintura dos balões |
| `ui/pet_widget.py` | Janela, sprite, arrasto |
| `ui/tray.py` | Ícone na bandeja (a janela é `Qt.Tool`, sem barra de tarefas) |
| `app.py` | Montagem, posicionamento e encerramento |

`events.py` é a parte mais sensível: os nomes dos eventos foram conferidos
contra os literais do bundle do opencode instalado (não contra
documentação), e as regras ficam numa tupla ordenada (`STATE_RULES`) onde
a primeira que casa vence. Se nenhuma casa, o estado atual é preservado.

## Eventos e estados

Os nomes abaixo foram conferidos no **stream de verdade** — o SSE de
`/api/event` de um servidor 2.0.22 rodando — e não nos literais do bundle.
A diferença importa: o barramento emite `permission.asked` e
`form.created`, e o que se chama `session.permission.create` /
`session.form.create` é a **rota HTTP** que cria esses pedidos, não o
evento. Uma versão anterior deste README affirmava o contrário, e foi
justamente esse o erro que escondia o estado "aguardando".

| Evento | Estado |
| --- | --- |
| `session.step.started`, `session.text.started`, `session.tool.called`, `session.reasoning.started`, `session.compaction.started`, `session.shell.started`, `session.retry.scheduled` | trabalhando |
| `file.edited`, `fs.write`, `shell.created`, `shell.output`, `shell.exited`, `opencode.tool.write` | trabalhando |
| `permission.asked`, `form.created` | **gatilho** — acorda a consulta de pendência, não vira estado |
| `permission.replied`, `form.replied`, `form.cancelled` | trabalhando (e solta a espera) |
| `session.idle`, `turn.idle`, `session.execution.succeeded` | pronto |
| `session.step.ended`, `session.tool.success`, `session.text.ended`, `session.text.delta` | *(nenhum — instante interno do turno)* |

A pergunta que a ferramenta `question` do opencode faz ao usuário é um
formulário, então chega como `form.created` — mas o **estado**
"aguardando" não vem desse evento. Ver
[A pergunta decide sozinha](#a-pergunta-decide-sozinha).

Três linhas merecem explicação:

**A do gatilho.** `form.created` e `permission.asked` não produzem estado:
eles acordam `GET /api/form` e `GET /api/permission/request`. Um evento de
pedido que vira estado direto é justamente o falso positivo — o stream é
global e perde eventos (doc v2).

**A segunda.** `file.edited`, `fs.write` e `shell.*` não têm o prefixo
`session.` no opencode. Sem eles, editar um arquivo ou rodar um comando
não contava como trabalho e o pet voltava a "pronto" no meio da tarefa.

**A última.** `step.ended` e `tool.success` disparam **entre passos** do
turno, não no fim dele. Se fossem mapeados para "pronto", o pet piscaria
várias vezes por turno.

`session.execution.failed` e `session.execution.interrupted` ficam sem
estado de propósito: uma falha não é ociosidade, mas também não é
trabalho em andamento. O estado atual é preservado e o texto da falha não
é um estado do pet.

### A pergunta decide sozinha

O estado "aguardando" **não sai do stream de eventos**. Isso mudou, e a
razão está na documentação do opencode v2:

| Rota | O que a doc diz |
| --- | --- |
| `GET /api/event` | *"Subscribe to native events and plugin RPC events **across all server locations**. Volatile by contract: a slow consumer overflows and fails the stream, and **events during disconnection are missed**."* |
| `GET /api/form` | *"Retrieve pending forms **for a location**."* → `{location, data: Form.Info[]}` |
| `GET /api/permission/request` | *"Retrieve pending permission requests **for a location**."* → `{location, data: Permission.Request[]}` |
| `GET /api/project` | Lista de projetos, cada um com o diretório canônico da *location* |

Ou seja: o stream é **global** (um servidor compartilhado serve todos os
projetos — a doc da CLI v2 fala em *"one shared background server"* ao qual
*"every local OpenCode client connects"*) e **perde eventos** por
contrato. Deduzir "o pet está esperando o usuário" a partir dele dava
falso positivo de dois jeitos, e os dois aconteciam:

1. **Resposta perdida.** O `form.replied`/`permission.replied` não chega
   (o stream é volátil). A trava só era solta por evento de resposta ou
   por reconexão, e o watchdog por desenho nunca age em `waiting`
   (`idle.py`: só age com `working`). O balão ficava em "Waiting / needs
   your answer", pulsando, sem nada pendente.
2. **Outro projeto.** Um `form.created` de qualquer projeto chega no mesmo
   stream, porque ele é de todas as *locations*. A pergunta de um
   projeto em segundo plano travava o balão do projeto em que o usuário
   estava.

A correção troca a fonte, não o sintoma:

```
stream: form.created ──► poke() ──► GET /api/form?location[directory]=…
                                        GET /api/permission/request?location[directory]=…
                                                  │
                                            há pendente? ──► "aguardando"
```

O evento **acorda** o pet (é o que mantém o pulso instantâneo); a
consulta ao servidor **decide**. E ela decide por *location*, que é a
unica unidade em que "o usuário tem algo a responder" faz sentido.

Consequências no código:

- `events.py` não produz mais `waiting`: `is_ask_event()` e
  `is_release_event()` são só gatilhos. `StateTranslator` e a trava foram
  removidos — a memória sobre o que está pendente é justamente o que
  produzia o defeito.
- `sessions.py` tem o `SessionBoard`: "aguardando" ganha de tudo enquanto
  o servidor confirmar, e cada balão diz de **qual** instância é. O
  stream diz só se há trabalho ou turno Finished, e por sessão.
- Uma consulta que falha **não** muda o estado (`note_pending(None)` é
  no-op). Trocar "aguardando" por "trabalhando" sem saber seria inventar
  uma resposta do usuário.
- O watchdog agora segue o estado **visível**, não o do stream: com uma
  espera real ele não age, e o que ele devolve passa pela arbitragem.
- Ao reconectar, o pet pergunta na hora. Antes ele chutava "não há nada
  pendente" e reiniciava a trava; agora o que existia durante a queda
  volta ao balão.

### Escopo: um projeto ou todos

Por padrão o pet observa **todos** os projetos que o servidor conhece
(`GET /api/project`), porque é o que o balão global sempre fez — e, com
a consulta por *location*, "a pergunta existe" passou a ser um fato
verificado, não uma inferência.

Para observar só um projeto:

```
PETWATCH_DIRECTORY=/caminho/do/projeto python pet.py
```

Os custos e o ritmo estão em `config.py`: o ciclo rápido (2s) só
consulta as *locations* que têm pedido aberto, o ciclo lento (20s) varre
todos, e um *location* que o servidor rejeita é isolado em vez de
derrubar a consulta dos outros. A senha é lida uma vez e a lista de
projetos fica em cache por 60s — o `subprocess` do `opencode2 service get
password` não roda a cada tique.

### Um balão por instância

Quem tem balão é quem o servidor diz que está em ação, e a rota é
explícita na spec v2:

> `GET /api/session/active` — *"Retrieve foreground Session drains
> currently owned by this OpenCode process. **Sessions absent from the
> result are inactive.**"*

O quadro é o `SessionBoard`, em `petwatch/sessions.py`, e ele junta três
fontes que antes eram uma só:

| Fonte | O que traz |
| --- | --- |
| eventos do stream, filtrados por `sessionID` | o estado **daquela** instância |
| `GET /api/session/active` | quem merece balão |
| `GET /api/form`, `GET /api/permission/request` | quem espera resposta — cada pedido traz o `sessionID` dele |
| `GET /api/session/{id}` | o nome da instância (título e `location`) |

Duas propriedades que mudam o comportamento:

- **O estado é por sessão.** O `session.tool.called` de uma aba não vira
  o estado da outra. Antes o estado era global e o balão mentia sobre a
  origem.
- **O "aguardando" é por sessão.** É o `sessionID` do pedido que diz qual
  balão fica colorido; antes ele dizia que *alguma* coisa em *algum*
  lugar estava esperando, e o bug 18 era a consequência.

Uma instância que sai da lista de ativas continua com balão por alguns
segundos (`QUIET_GRACE_SECONDS`): `/api/session/active` e o stream não
contam a mesma coisa no mesmo instante, e sem folga o balão piscaria a cada
tique da consulta.

### Degradação: servidor sem as rotas

Um servidor mais antigo (v1, que expõe `/event` e não `/api/form`)
responde 404. Aí o `StatusPoller` desliga, avisa uma vez no log, e a trava
por stream assume sozinha — imperfeita, mas melhor do que nunca mostrar
"aguardando". `tests/test_pending.py` e `tests/test_sessions.py` cobrem as
duas metades.

### O watchdog

O opencode avisa que o stream é **volátil**: *"events during disconnection
are missed"*. Se o `session.idle` se perder, o pet fica preso em
"Thinking" indefinidamente — que era o sintoma reportado.

`petwatch/idle.py` conta o tempo: se passar `IDLE_TIMEOUT` sem nenhum
evento, volta para "pronto". Age só quando o estado é "working" —
esperando permissão o usuário pode levar minutos, e aí "aguardando
resposta" continua sendo o texto certo.

Ele acompanha o estado **visível** (o que o `SessionBoard` escolheu), não
o do stream: é essa distinção que impede o timeout de cortar uma espera
real, agora que "aguardando" vem da consulta de pendência.

**O timeout é 45s porque foi medido, não chutado.** Capturei 3084 eventos
de um turno real e medi os intervalos: o maior silêncio foi de **39,06s** e
houve **20 intervalos acima de 5s** — quase todos entre o fim de um passo e
o começo do próximo, ou durante o processamento do modelo. Com 5s o pet
mostrava "pronto" no meio do trabalho. Com 45s, só um turno realmente
encerrado sem evento escapa.

### O pulso de "precisa de você"

O estado `waiting` é o único em que o pet está de fato esperando algo: o
servidor respondeu que há um formulário ou uma permissão pendente, e nada
acontece até o usuário responder. Por isso ele tem destaque: um pulso de
760ms que levanta o sprite ~1,5px, aumenta o balão ~1px e acende a borda,
repetindo a cada 2s enquanto a espera dura.

Nada pisca, nada treme, a janela não se move. A amplitude acompanha o
preset de tamanho (0,82× no pequeno, 1,24× no grande) para o salto não
virar caricatura. Só o estado `waiting` anima — `idle`, `working` e
`connecting` não pulsam.

Ele roda na thread da interface porque precisa de `QTimer`; a thread do
monitor fica bloqueada na leitura do socket e não tem event loop. Isso
exige que a **conexão** entre o sinal do monitor e `set_state` seja
enfileirada — ver o bug 16, em que ela não era, e o pulso nunca chegava a
rodar.

O pulso **não** substitui o repaint do tique do sprite: ele tem o próprio
ciclo, com os próprios repaints, e só existe no estado `waiting`. Quem
desenha o quadro do sprite e o bob a cada tique é `animate()`
(ver bug 11).

## Dependência do sistema: plugin de WebP

As spritesheets são `spritesheet.webp`. Nesta instalação do Ubuntu o WebP
**não** é interno ao Qt, é um plugin — e `libQt6Gui.so.6` não linka
`libwebp`. Sem o pacote abaixo, nenhuma imagem carrega e o pet some:

```bash
sudo apt install qt6-image-formats-plugins
```

São 253 KB, todas as dependências já estão presentes e o Qt já procura em
`/usr/lib/x86_64-linux-gnu/qt6/plugins/`, então **nenhuma variável de
ambiente é necessária**.

Sem root, dá para apontar o Qt para uma cópia local:

```bash
mkdir -p ~/.local/share/petwatch/qt-plugins
cd /tmp && apt-get download qt6-image-formats-plugins
dpkg-deb -x qt6-image-formats-plugins_*.deb /tmp/qtif
cp /tmp/qtif/usr/lib/x86_64-linux-gnu/qt6/plugins/imageformats/libqwebp.so \
   ~/.local/share/petwatch/qt-plugins/
QT_PLUGIN_PATH=~/.local/share/petwatch/qt-plugins python pet.py
```

## Como a spritesheet é lida

Todas as 1738 spritesheets têm **1536×1872 pixels** e são fatiadas numa
grade de **8 colunas × 9 linhas**, ou seja, células de **192×208** — 72
células. A grade padrão é sobrescrevível por tema.

As células **não** formam uma animação única: cada linha da grade é uma
ação diferente e há células vazias de preenchimento. O padrão de células
vazias é idêntico em todos os temas:

```
# # # # # # . .
# # # # # # # #
# # # # # # # #
# # # # . . . .
# # # # # . . .
# # # # # # # #
# # # # # # . .
# # # # # # . .
# # # # # # . .
```

Por isso a animação usa só a **primeira sequência de células com
conteúdo** (as 6 do canto superior esquerdo, que é o idle) em vez de varrer
as 72 — varrer causaria saltos entre ações diferentes eeria por células
vazias. Para escolher outra ação:

```json
"frames": { "columns": 8, "rows": 9, "fps": 12, "first": 8, "last": 15 }
```

Sem `first`/`last`, vale a detecção automática. `fps` é em quadros por
segundo (o timer redesenha a 10 Hz).

## Tema

Cada tema é um diretório em `pets/` com um `pet.json`:

```json
{
  "id": "eevee",
  "displayName": "Eevee",
  "description": "...",
  "spritesheetPath": "spritesheet.webp",
  "scale": 1.0,
  "frames": {
    "columns": 8,
    "rows": 9,
    "fps": 12
  },
  "text": {
    "enabled": true,
    "position": "top",
    "font_family": "Inter",
    "title_font_size": 13,
    "subtitle_font_size": 12,
    "title_color": "#1F1F1F",
    "subtitle_color": "#9CA3AF",
    "background": "#FFFFFF",
    "background_alpha": 246,
    "border": "#E4E4E7",
    "border_width": 1,
    "shadow_alpha": 28,
    "padding_x": 14,
    "padding_y": 9,
    "corner_radius": 14,
    "line_gap": 1
  }
}
```

`name` e `asset` são aceitos como alternativa a `displayName` e
`spritesheetPath`. Tudo dentro de `text` e de `frames` é opcional.

O `font_family` é uma preferência: se a família pedida não existir no
sistema, o balão usa a primeira disponível de
`petwatch.ui.bubble.FONT_PREFERENCE` (Inter → Roboto → Segoe UI → Ubuntu
Sans → DejaVu Sans → Liberation Sans → Arial).

## Balão e tamanho

Cada balão tem duas linhas alinhadas à esquerda: **título em negrito** e
**subtítulo em cinza**, como na referência. O título é o estado e o
subtítulo é o nome da instância que o balão representa — com uma aba só,
é a única diferença em relação ao balão antigo.

```python
STATE_LABELS = {
    "connecting": ("", ""),                # mudo: nem balão, ver bug 17
    "idle":       ("Ready",      "waiting for you"),
    "working":    ("Thinking",   "working on it"),
    "waiting":    ("Waiting",    "needs your answer"),
}
```

Num balão da pilha o subtítulo do estado é substituído pelo nome da
instância: o título continua dizendo o que está acontecendo e a segunda
linha diz **de quem** é. Um estado com as duas linhas vazias **não escreve
nada e nem desenha o balão**: um retângulo claro sem letra dentro parece
defeito, não silêncio.

### A pilha: um balão por instância, um acima do outro

Balões **empilhados**, um acima do outro, consecutivos, cada um com a
largura fixa do preset e o texto reticado.

```
┌──────────────────┐
│ Waiting          │  ← âmbar: espera resposta
│ pet-opencodev2   │
└──────────────────┘
┌──────────────────┐
│ Thinking         │
│ arbimax-ex…      │
└──────────────────┘
        🐇  ← sprite, no rodapé
```

### O pet nunca se move

**A janela é dimensionada uma vez e nunca mais muda** enquanto o preset for
o mesmo — nem quando um balão entra, nem quando sai.

Isso não é preferência estética. No Wayland (e no WSLg, que é o caso desta
máquina) **o compositor decide onde a superfície fica**, e o `move()` do
cliente é praticamente ignorado. Redimensionar a janela para acomodar mais
um balão fazia o pet subir ou descer em relação ao resto da tela — foi o
sintoma reportado, e nenhuma reposição depois disso resolve, porque o
cliente não manda na posição.

Duas decisões que explicam os números:

- **A janela nasce com a capacidade da pilha** (`config.MAX_STACK = 6`).
  Ficar com menos balões deixa espaço transparente em cima, porque a pilha
  é **ancorada embaixo**, logo acima do sprite.
- **A troca de preset redimensiona**, num passo só: `setGeometry` com a
  geometria final. Redimensionar e depois mover deixa o compositor ver um
  estado intermediário em que a janela cresceu para baixo, e é nele que o
  pet anda.

Passando da capacidade a janela cresce de verdade, e o pet se move. É o
mal menor: a outra saída era esconder uma instância, que é pior. O caso é
raro (sete abas do opencode ao mesmo tempo) e avisa no log.

A pilha mostra no máximo 6 instâncias (`config.MAX_STACK` /
`sessions.MAX_INSTANCES`, cobertos por teste em `test_sessions.py`): o que
encurta a lista no dia a dia é o esquecimento
(`sessions.QUIET_GRACE_SECONDS`), e o sétimo em diante fica fora da grade.
Passando da capacidade em chamada direta a janela cresce de verdade (com
aviso no log) — ver `set_cards`.

`tests/test_ui.py::CardStackTests::test_the_window_never_moves_or_resizes`
é o teste do sintoma: compara a **geometria da janela e a posição do sprite
na tela** (`mapToGlobal`) para 0, 1, 2, 3, 6 e 5 balões, e exige identidade.

Com uma coluna a janela ficou mais estreita que a do balão único antigo
(250px → 152px de largura) e mais alta (466px no médio), do tamanho da
pilha cheia — o espaço extra é transparente e não ocupa tela de verdade.

O texto que não cabe é cortado com reticências (`QFontMetrics.elidedText`)
em vez de alargar a caixa — alargar tiraria a pilha do alinhamento.

### A cor do aviso

Só o título de quem **precisa de resposta** é pintado, com
`states.ACTION_COLOR` (`#B45309`, âmbar escuro):

| | |
| --- | --- |
| Contraste contra o fundo do balão | ≥ 4.5:1 (WCAG AA) |
| Por que âmbar escuro | quente o bastante para dizer "aqui", escuro o bastante para não doer a cada tique |
| Por que não os outros | pintar os quatro estados transformaria a tela num painel de status em vez de num aviso |

`tests/test_ui.py::CardStackTests` confere o contraste, a saturação, e que
um balão que **não** espera nada não tem pixel dessa cor.

A janela deixou de "abraçar o sprite" (250×216) e passou a ter a largura
de um balão (152px no médio): empilhados, os balões não precisam de
largura, e o sprite continua com no máximo 132×144.

Há 1738 temas em `pets/`. Para ver o nome de exibição de um:

```bash
python -c "from petwatch import load_theme; print(load_theme('eevee').name)"
```

## Testes

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

478 testes cobrindo o mapeamento de eventos, o parser SSE, a leitura do
`pet.json`, a escolha do asset, o transporte HTTP, as consultas de
pendência e de sessão, o quadro de instâncias, a pilha de balões
(empilhamento ancorado embaixo, janela fixa, reticências, cor de aviso), a
prova de que o pet não se move ao entrar ou sair um balão, a geometria da
pintura, o repaint da animação, o isolamento das preferências, o
encerramento por sinal e a saída do terminal.

Os testes rodam nos dois cenários: com e sem o plugin de WebP. Alguns
detalhes que valem fora do código:

- `test_interrupt_unblocks_a_stuck_read` sobe um servidor HTTP local que
  serve um stream SSE aberto e confirma que `Connection.interrupt()`
  destrava uma leitura realmente presa.
- `tests/test_app.py` sobe um `PetApplication` de verdade e dispara um
  `SIGINT` no próprio processo.
- `tests/test_assets.py` confere que a grade 8x9 divide a spritesheet dos
  1738 temas e que nenhuma linha de ação cai numa célula vazia.
- `tests/test_ui.py::RepaintTests` roda o event loop de verdade contando
  `QEvent.Paint`: é o que pega um `animate()` que conta os quadros sem
  pedir repaint, ou seja, o pet parado na tela.
- `tests/test_app.py` monta o `PetApplication` com um `prefs_path`
  temporário. Sem isso a suíte lê o tema escolhido no
  `~/.config/petwatch/prefs.json` de quem a roda — e cada `save()`
  sobrescreveria esse arquivo com o estado do app de teste
  (ver bug 12).
- `tests/test_attention.py` rasteriza o widget e garante que o pulso muda
  a pintura sem mudar as medidas do balão.
- `tests/test_menu.py` percorre o menu de tamanho fechando o ciclo em todos
  os presets, a partir dos três pontos de partida.
- `tests/test_daemon.py::DetachTests` roda o desvio num **processo
  separado** — `fork` dentro do runner trocaria os descritores da própria
  suíte. Confere que o comando volta com 0, que a saída do processo vai
  para o log em vez da tela, que o processo continua vivo depois de o pai
  sair, e que o lock de instância atravessa o fork.
- `tests/test_app.py::DetachedStartupTests` faz a janela **falhar** de
  propósito (a suíte já tem um `QApplication`, então um segundo levanta
  `RuntimeError`) e confere que o motivo chega pelo pipe do processo
  original, em uma linha só, e que o lock é solto mesmo assim.

## Bugs corrigidos nesta versão

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

## Notas

- O `python pet.py` sem opção vai para segundo plano; `--foreground` é o
  modo colado no terminal, e é onde o `Ctrl+C` funciona. Quem quiser
  começar no login pode apontar para `python pet.py -b`.
- `PETWATCH_DIRECTORY` limita o "aguardando" a um projeto; sem ela o pet
  observa todos os projetos que o servidor conhece.
- `PetTheme.text_position` é lido do `pet.json` mas não é usado: a posição
  do balão é fixa (`config.BUBBLE_TOP_MARGIN`), como no original.
- O `IDLE_TIMEOUT` de 45s é folga sobre o pior caso medido (39s). Uma
  ferramenta que roda mais de 45s sem emitir nada ainda mostra "pronto" e
  volta para "Thinking" quando retorna; aumente o valor em `config.py` se
  usar comandos longos.
- O pet **só lê**. As requisições HTTP são `GET`s — em `/api/event`
  (sondagem e stream), `/api/project`, `/api/form`,
  `/api/permission/request`, `/api/session/active` e `/api/session/{id}`
  — e os `subprocess` são `ss -ltnp` e
  `opencode2 service get password` (este último lido uma vez e reaproveitado
  enquanto o serviço estiver de pé). Não há POST/PUT/DELETE nem chamada de
  abort, então o pet não pode responder uma pergunta nem interromper uma
  tarefa: ele só diz que existe uma.
- O estado é **por sessão**, não global: cada evento vira estado só na
  instância que o produziu (`sessionID`), e o "aguardando" é por sessão —
  é o `sessionID` do pedido (`GET /api/form`,
  `GET /api/permission/request`) que diz qual balão fica colorido. O sprite
  resume o conjunto (aguardando ganha de trabalhando, que ganha de pronto).
  A arbitragem antiga de balão único virou o `SessionBoard` (ver
  [bug 18](#18-waiting-falso-positivo) e
  [bug 19](#19-um-balão-para-cada-instância-do-opencode-em-ação)).
- A janela é `Qt.Tool` (sem entrada na barra de tarefas / Alt-Tab) e o
  acesso fica pelo ícone na bandeja (`ui/tray.py`), que usa o mesmo menu
  do botão direito: o clique esquerdo mostra/esconde o pet e a dica
  acompanha o estado publicado.

## Contribuindo

Ver [`CONTRIBUTING.md`](CONTRIBUTING.md): setup, testes (`QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -t .`), `ruff check .` e o processo de PR.
Toda interação segue o [`Código de Conduta`](CODE_OF_CONDUCT.md).

## Segurança

Não abra issue pública para vulnerabilidades — siga o
[`SECURITY.md`](SECURITY.md).

## Changelog

Mudanças notáveis em [`CHANGELOG.md`](CHANGELOG.md) (Keep a Changelog, SemVer).

## Licença

MIT — ver [`LICENSE`](LICENSE).


