# Arquitetura

Pipeline, eventos, sessões, watchdog e sprites. Visão geral em [README](../README.md).


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
| `daemon.py` | Segundo plano: fork (POSIX) ou `Popen` detached (Windows), log e `--stop` |
| `states.py` | Os quatro estados e seus rótulos |
| `theme.py` | Lê `pet.json` e resolve o caminho do sprite |
| `assets.py` | Escolhe o primeiro asset que o Qt consegue decodificar |
| `http.py` | Transporte do stream, com leitura interrompível |
| `discovery.py` | Senha via CLI, portas em escuta, sondagem do servidor |
| `sse.py` | Parser do stream `text/event-stream` |
| `events.py` | Regras declarativas de evento → estado, e os gatilhos de pedido |
| `pending.py` | Consultas de pendência e de sessão ao servidor v2 |
| `sessions.py` | Quadro de instâncias (um balão cada) e o laço que pergunta |
| `idle.py` | Watchdog: volta para "pronto" se o opencode silenciar |
| `sizes.py` | Presets de tamanho, com a pilha de balões e a escala do texto |
| `prefs.py` | Preferências persistidas |
| `console.py` | Saída e log quando não há terminal (o `.exe` é sem console) |
| `pipe.py` | Named pipe: instância única e encerramento no Windows |
| `instance.py` | A estratégia de instância única da plataforma |
| `ui/attention.py` | Pulso de "precisa de você" no estado de espera |
| `ui/menu.py` | Menu do botão direito |
| `ui/pet_picker.py` | Seletor de pet em grade de miniaturas, com busca e paginação |
| `monitor.py` | Loop de conexão e reconexão (roda em `QThread`) |
| `ui/bubble.py` | Medição, empilhamento e pintura dos balões |
| `ui/pet_widget.py` | Janela, sprite, arrasto |
| `ui/tray.py` | Ícone na bandeja (a janela é `Qt.Tool`, sem barra de tarefas) |
| `app.py` | Montagem, posicionamento e encerramento |

### Onde a porta do opencode vem

A porta não é configurada em lugar nenhum, e o nome do processo não é usado
para achá-la. `discovery.py` lista as **portas em escuta** da máquina —
`/proc/net/tcp` no Linux, `ss -ltn` nos outros POSIX, `netstat -ano -p tcp`
no Windows — e pergunta a cada uma se é o opencode: um `GET /api/event` com
a senha que responda `200` **e** `Content-Type: text/event-stream`.

A pergunta é melhor que a alternativa por três motivos, e vale nas duas
plataformas. O nome do processo muda entre instalações (`opencode`,
`opencode2`, `opencode.exe`, `bun.exe`) e o serviço pode estar em outro
namespace de rede (WSL, container), então casar por ele erra justo nos casos
que mais importam. E o tipo de conteúdo separa o opencode de qualquer
outro servidor local: sem ele, o primeiro dev server que dissesse "ok" em
toda rota seria adotado como opencode.

A lista tem TTL de 10 s e a porta que respondeu é a primeira da próxima
varredura, porque o monitor reconecta a cada 2 s — reler a lista toda vez
custaria um subprocesso por ciclo no Windows sem comprar nada. O teto de
sondagens mantém o pior caso previsível numa máquina com muitos servidores.
`PETWATCH_PORT` pula a busca inteira.

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
  stream diz só se há trabalho ou turno encerrado, e por sessão.
- Uma consulta que falha **não** muda o estado (`note_pending(None)` é
  no-op). Trocar "aguardando" por "trabalhando" sem saber seria inventar
  uma resposta do usuário.
- O watchdog agora segue o estado **visível**, não o do stream: com uma
  espera real ele não age, e o que ele devolve passa pela arbitragem.

### O 404 que significa duas coisas

A degradação acima — servidor sem as rotas do v2 — se distingue por um
**404**, e é o único jeito de desligar o recurso. Só que o servidor v2
responde 404 por duas coisas, e o status não separa nenhuma:

| 404 | corpo | o que é |
| --- | --- | --- |
| `LocationNotFoundError` | `{"_tag":"LocationNotFoundError", ...}` | o *location* não existe — projeto apagado que `GET /api/project` ainda guarda |
| rota ausente | **vazio** | servidor v1, sem a rota: degradação de verdade |

Ler os dois pelo status desligava o "aguardando" por causa de um diretório
que não existe mais, e o pet voltava ao modo degradado — cujo único recurso
é a trava do stream, e é o mais fraco dos dois. A distinção é o `_tag`
(`pending.is_location_not_found`), e um *location* morto é isolado como já
era o 500. Ver o [bug 20](BUGS.md#20-thinking-com-a-pergunta-aberta-na-tela).

O que sobra da degradação também é por instância: a trava guarda **de quem**
foi o pedido (`SessionBoard._latched`), porque o stream é global e o
`session.idle` de uma aba não diz nada sobre a pergunta de outra. É o bug
15, que só não aparecia enquanto a degradação nunca acontecia de verdade.

E quem decide o que o balão escreve é o quadro (`SessionBoard.cards()`),
não a interface: no caminho normal isso veio do servidor, e na degradação
veio da trava, e as duas metades juntas é o que faz o sprite e o texto do
balão concordarem.
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

**O watchdog global não é a única rede de segurança do balão.** Ele exige
silêncio do servidor **inteiro**, então com outra aba do opencode
trabalhando — o uso normal — ele nunca dispara. Por isso `on_active` também
envelhece as instâncias: `SessionBoard.demote_stale` roda a cada consulta de
`/api/session/active`, e é ela que descobre que uma instância continua
listada como `running` mesmo sem emitir evento. Ver o
[bug 21](BUGS.md#21-thinking-para-sempre-com-o-agente-parado).

O relógio do silêncio é `Instance.evented_at`, e **não** `touched_at`:
`touched_at` responde "o servidor ainda lista esta sessão", o que não prova
que ela esteja trabalhando — a prova é o stream, e só ele move
`evented_at`. Confundir os dois é o que prendia o balão em "Thinking".

### Prova de vida é diferente de estado

`Instance.evented_at` é movido por **todo** evento do stream, não só pelos que
viram estado. As duas coisas são diferentes:

- **estado** é o que o balão escreve, e é filtrado por regras: um evento que
  acontece *dentro* do turno (`session.reasoning.delta`, `session.step.ended`)
  não pode virar estado, ou o balão pisca;
- **prova de vida** é só "isto ainda está falando", e vale para tudo.

A confusão entre as duas dava o bug 22 — "Ready" no meio do raciocínio. Num
turno real de 150 s, **771 dos 819 eventos eram
`session.reasoning.delta`**, que não vira estado por desenho:

```
stream ─► monitor.session_alive(sessionID, diretório) ─► board.note_alive()
                                                             └─ evented_at
```

O `sessionID` é o dono preciso, mas não vem em todo evento: `shell.created`,
`shell.exited` e `file.edited` são eventos de *location*, e o **envelope**
traz o diretório (`events.extract_location_directory`). Sem isso, um comando
longo de shell — que só emite `shell.*` e `file.edited` — congelava o relógio
e o balão caía no meio dele.

Dois detalhes que não são óbvios:

- O carimbo é o relógio **real** (`_clock()`), não o do último ciclo de poll
  (`_now`). Com o segundo o timeout deixa de ser um número e vira uma faixa de
  40 s a 65 s — e 45 s é o que foi medido.
- `Instance.demoted` marca que o "pronto" saiu de uma inferência de silêncio.
  É revogável, e `note_alive` o desfaz no primeiro evento. Um `session.idle`
  dito pelo servidor não é — quem afirma que o turno acabou é o servidor.

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

