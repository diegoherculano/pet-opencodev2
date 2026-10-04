"""Constantes e caminhos usados pelo aplicativo.

Os valores aqui são os mesmos do monólito original (``pet.py``); a
separação existe apenas para que cada módulo dependa do que precisa.
"""

from __future__ import annotations

import os
from pathlib import Path

# ------------------------------------------------------------
# Caminhos
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

PETS_DIR = BASE_DIR / "pets"

#: Estado do processo solto: o lock/pid da instância e o log. Fora de
#: ``BASE_DIR`` de propósito — o estado é do usuário, não do código, e o
#: diretório do projeto pode ser somente leitura.
STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    / "petwatch"
)

#: Lock de instância única; o conteúdo é o pid de quem está rodando.
PID_PATH = STATE_DIR / "pet.pid"

#: Onde vai a saída quando o processo não tem mais terminal.
LOG_PATH = STATE_DIR / "pet.log"

# ------------------------------------------------------------
# Conexão com o opencode
# ------------------------------------------------------------

HOST = "127.0.0.1"

EVENT_PATH = "/api/event"

USERNAME = "opencode"

RECONNECT_DELAY = 2.0

#: Comando usado para obter a senha do serviço local.
PASSWORD_COMMAND = ("opencode2", "service", "get", "password")

PASSWORD_TIMEOUT = 5.0

#: Timeout do ``ss -ltnp`` usado na descoberta de portas.
PORT_SCAN_TIMEOUT = 3.0

#: Timeout do socket na checagem de vida do servidor.
PROBE_CONNECT_TIMEOUT = 1.5

#: Timeout do GET de sondagem em ``/api/event``.
PROBE_REQUEST_TIMEOUT = 3.0

#: Timeout do stream SSE de eventos.
STREAM_TIMEOUT = 30.0

# ------------------------------------------------------------
# O que está esperando o usuário (opencode v2)
# ------------------------------------------------------------

#: "GET /api/event" é documentado como * volatile by contract: ... events
#: during disconnection are missed"*, e como um stream **de todas as
#: locations** do servidor. Duas consultas HTTP resolvem o que o stream
#: não pode: o que está de fato pendente, e por projeto.
#:
#: As duas rotas são de leitura e escopadas por *location* — a doc diz
#: "retrieve pending forms/permission requests for a location" — e
#: devolvem ``{location, data}``.
FORM_PATH = "/api/form"

PERMISSION_PATH = "/api/permission/request"

#: "GET /api/project" devolve os projetos conhecidos, cada um com o
#: diretório canônico que a *location* usa.
PROJECT_PATH = "/api/project"

#: Timeout de cada consulta de pendência.
PENDING_TIMEOUT = 3.0

#: De quanto em quanto tempo o pet reconfirma que ainda há algo
#: pendente. Rápido porque é o que segura o falso positivo: a resposta
#: pode ter se perdido no caminho e o servidor continua sendo a
#:authority.
PENDING_POLL_SECONDS = 2.0

#: Reconfirmação bem mais lenta quando **não** há nada pendente. O
#: estado "aguardando" é o único que precisa de relógio apertado; o
#: resto é só rede de segurança para uma pergunta que ainda não
#: apareceu no stream.
PENDING_IDLE_POLL_SECONDS = 20.0

#: Nova tentativa depois de uma consulta que falhou. A falha **não**
#: muda o estado: o pet prefere um "Thinking" possivelmente atrasado a
#: um "aguardando" inventado.
PENDING_ERROR_RETRY = 5.0

#: Teto de projetos consultados por ciclo. Cada projeto custa dois
#: GETs; a lista vem do próprio servidor e cresce com o tempo.
MAX_WATCHED_PROJECTS = 12

#: Tempo sem reler a lista de projetos. Ela muda quando o usuário abre
#: um projeto novo, e um ciclo de 2s bastaria para perceber.
PROJECTS_TTL_SECONDS = 60.0

#: Diretório observado, quando o usuário quer **um** projeto em vez de
#: todos. Vazio (padrão) = a lista de projetos do servidor.
WATCH_DIRECTORY_ENV = "PETWATCH_DIRECTORY"

# ------------------------------------------------------------
# Tema
# ------------------------------------------------------------

DEFAULT_THEME = "eevee"

# ------------------------------------------------------------
# Janela
# ------------------------------------------------------------

#: A janela encolhe para abraçar o sprite: o balão fica no topo e o pet
#: apoiado no rodapé, com folga nas laterais. O tamanho em si vem dos
#: presets de :mod:`petwatch.sizes`; aqui ficam só as folgas e o que não
#: muda com o preset.

#: Folga entre a janela e a borda direita / inferior da área de trabalho.
SCREEN_GAP_X = 30
SCREEN_GAP_Y = 50

# ------------------------------------------------------------
# Sprite
# ------------------------------------------------------------

#: As dimensões do sprite também vêm dos presets; o que fica aqui é o
#: que é comum a todos.

#: Folga entre a base do sprite e o rodapé da janela. Pequena de propósito:
#: a célula tem ~9px transparentes abaixo do desenho, que já servem de
#: respiro.
SPRITE_MARGIN_BOTTOM = 6

#: Intervalo do timer que redesenha o pet.
ANIMATION_INTERVAL_MS = 100

# ------------------------------------------------------------
# Balão de status
# ------------------------------------------------------------

BUBBLE_TOP_MARGIN = 10

# ------------------------------------------------------------
# Balões empilhados (um por instância do opencode em ação)
# ------------------------------------------------------------

#: Os balões são empilhados um acima do outro; a largura da janela é a de
#: um só.

#: Quantos balões a janela comporta, **fixo desde a abertura**.
#:
#: A janela não pode mudar de tamanho depois de colocada: no Wayland (e no
#: WSLg, que é o caso daqui) o compositor decide onde a superfície fica, e
#: o ``move()`` do cliente é praticamente ignorado. Redimensionar a janela
#: para acomodar mais um balão faria o pet subir ou descer em relação ao
#: resto da tela — foi o que o usuário viu.
#:
#: A pilha é desenhada de baixo para cima, ancorada logo acima do sprite, de
#: modo que ficar com menos balões só deixa espaço transparente: o pet
#: continua no mesmo pixel.
#:
#: Sópassando disso a janela cresce, e aí o pet se move — melhor isso do que
#: esconder uma instância, que era a outra opção.
MAX_STACK = 6

#: Padding interno do balão. Menor que o do balão único: são caixas
#: pequenas, e o padding largo comia a largura toda.
CARD_PADDING_X = 9

CARD_PADDING_Y = 6

#: Espaço entre a linha do título e a do subtítulo dentro do balão.
CARD_LINE_GAP = 1

# ------------------------------------------------------------
# Ações da spritesheet
# ------------------------------------------------------------

#: Todas as spritesheets do repositório são uma grade de 8 colunas por 9
#: linhas, e cada linha é uma animação curta diferente. O padrão de
#: células por linha é sempre ``6, 8, 8, 4, 5, 8, 6, 6, 6`` — as 15 que
#: faltam são preenchimento vazio.
#:
#: Estes índices escolhem a linha que anima em cada estado:
#:
#: ====  ========  ================================================
#: :in:  quadros:  o que a pose mostra
#: ====  ========  ================================================
#:  0       6      parado, orelha e olho mexendo
#:  1       8      andando, patas alternando
#:  2       8      andando de costas
#:  3       4      cabeça caída, orelhas baixas
#:  4       5      olhando para baixo, sonolento
#:  5       8      escurecendo até quase preto (evitar)
#:  6       6      orelhas para trás, alerta
#:  7       6      abaixando
#:  8       6      cabeça erguida, alerta
#: ====  ========  ================================================
#:
#: Os nomes das poses são leitura visual, não metadado do asset; a
#: estrutura é que é garantida. Se um tema tiver menos linhas que o
#: esperado, cai para a detecção automática da primeira sequência.
ACTION_ROW_BY_STATE = {
    "connecting": 7,   # abaixando
    "idle": 0,         # parado
    "working": 1,      # andando
    "waiting": 3,      # cabeça caída
}

#: Linha usada quando o estado não tem uma linha própria.
DEFAULT_ACTION_ROW = 0

# ------------------------------------------------------------
# Voltar para "pronto"
# ------------------------------------------------------------

#: Sem atividade por este tempo, o pet volta para "pronto" sozinho.
#:
#: O opencode emite ``session.idle`` no fim do turno, mas o pet não pode
#: depender disso: se o evento se perder, se a versão do opencode não o
#: mandar, ou se ele simplesmente não chegar, o pet ficaria preso em
#: "Thinking" para sempre. O timeout é a rede de segurança.
#:
#: O valor veio de medir os intervalos reais entre eventos num turno de
#: trabalho de verdade (3084 eventos). O maior silêncio foi de 39s, e
#: houve 20 intervalos acima de 5s — quase todos entre o fim de um passo e
#: o começo do próximo, ou durante o processamento do modelo. Com 5s o pet
#: mostrava "pronto" no meio do trabalho. Com 45s só um turno realmente
#: encerrado sem evento escapa.
#:
#: Só age quando o estado é "working"; esperando permissão o usuário pode
#: levar minutos, e aí o texto correto continua sendo "Waiting".
IDLE_TIMEOUT = 45.0

#: De quanto em quanto tempo o watchdog confere o tempo sem atividade.
IDLE_POLL_MS = 500
