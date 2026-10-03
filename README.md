# petwatch

Pet de desktop que observa um servidor local do [opencode](../) e mostra o
que está acontecendo: conectando, pronto, trabalhando ou aguardando resposta.

## Como rodar

```bash
python pet.py            # ou: python -m petwatch
```

- **Clique esquerdo** — arrasta o pet pela tela
- **Clique direito** — fecha o pet
- **Ctrl+C** — fecha o pet

`Ctrl+C` e `kill` passam pelo mesmo caminho limpo do botão direito: um
handler de sinal chama `quit()`, que dispara `aboutToQuit` e o
`shutdown()`. Sem esse handler o `SIGINT` levanta `KeyboardInterrupt`
dentro do slot que o Qt está executando; o PySide6 imprime o traceback e o
event loop continua, então o processo não sai.

## Como funciona

O `opencode` expõe um stream SSE autenticado em `/api/event`. O pet fica
ouvindo esse stream numa thread separada e traduz cada evento em um de
quatro estados, desenhados no balão acima do sprite.

```
pets/<tema>/pet.json ──► theme.py ──► PetTheme
                                    │
                          assets.py ─┴─► imagem decodificável
                                    │
discovery.py (senha + porta) ──► sse.py ──► events.py ──► estados
                                                              │
                                        monitor.py (thread) ──┘
                                                              │
                                        ui/pet_widget.py ◄───┘
```

## Módulos

| Módulo | Responsabilidade |
| --- | --- |
| `config.py` | Constantes, timeouts e caminhos |
| `states.py` | Os quatro estados e seus rótulos |
| `theme.py` | Lê `pet.json` e resolve o caminho do sprite |
| `assets.py` | Escolhe o primeiro asset que o Qt consegue decodificar |
| `http.py` | Transporte do stream, com leitura interrompível |
| `discovery.py` | Senha via CLI, portas via `ss`, sondagem do servidor |
| `sse.py` | Parser do stream `text/event-stream` |
| `events.py` | Regras declarativas de evento → estado |
| `monitor.py` | Loop de conexão e reconexão (roda em `QThread`) |
| `ui/bubble.py` | Medição e pintura do balão de duas linhas |
| `ui/pet_widget.py` | Janela, sprite, arrasto |
| `app.py` | Montagem, posicionamento e encerramento |

`events.py` é a parte mais sensível: as regras ficam numa tupla ordenada
(`STATE_RULES`) e a primeira que casa vence. Se nenhuma casa, o estado atual
é preservado.

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

O balão tem duas linhas alinhadas à esquerda: **título em negrito** e
**subtítulo em cinza**, como na referência. Os rótulos por estado ficam em
`petwatch.states.STATE_LABELS` e são editáveis:

```python
STATE_LABELS = {
    "connecting": ("Connecting", "starting up"),
    "idle":       ("Ready",      "waiting for you"),
    "working":    ("Thinking",   "working on it"),
    "waiting":    ("Waiting",    "needs your answer"),
}
```

A janela é pequena de propósito (250×216) e encolhe em volta do sprite, que
aparece com no máximo 132×144. A altura da janela já conta o padding
transparente que a célula da spritesheet traz ao redor do desenho, para o
balão não ficar longe demais do pet.

Há 1738 temas em `pets/`. Para ver o nome de exibição de um:

```bash
python -c "from petwatch import load_theme; print(load_theme('eevee').name)"
```

## Testes

```bash
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
```

169 testes cobrindo o mapeamento de eventos, o parser SSE, a leitura do
`pet.json`, a escolha do asset, o transporte HTTP, a geometria da pintura e
o encerramento por sinal.

Os testes rodam nos dois cenários: com e sem o plugin de WebP. O
`test_interrupt_unblocks_a_stuck_read` sobe um servidor HTTP local que
serve um stream SSE aberto e confirma que `Connection.interrupt()` destrava
uma leitura realmente presa. O `tests/test_app.py` sobe um `PetApplication`
de verdade e dispara um `SIGINT` no próprio processo.

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

## Notas

- `PetTheme.text_position` é lido do `pet.json` mas não é usado: a posição
  do balão é fixa (`config.BUBBLE_TOP_MARGIN`), como no original.


