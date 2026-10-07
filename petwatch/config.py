"""Constantes e caminhos usados pelo aplicativo.

Os valores aqui são os mesmos do monólito original (``pet.py``); a
separação existe apenas para que cada módulo dependa do que precisa.

Os caminhos seguem a plataforma em duas regras:

- **POSIX** continua no XDG, como sempre (``$XDG_STATE_HOME``,
  ``$XDG_DATA_HOME``, ``~/.config``);
- **Windows** usa os diretórios que o próprio sistema já cria
  (``%LOCALAPPDATA%``, ``%APPDATA%``), porque ``~/.config`` ali é uma
  convenção que ninguém adota — e ``%LOCALAPPDATA%`` é o lugar que o
  explorador de arquivos abre por padrão.

O que decide o resto é o caminho dos pets, que é o único dado que o
usuário instala à mão (ver :func:`pets_candidates`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Nome da pasta do usuário, em todas as plataformas.
APP_NAME = "petwatch"

#: Windows? Decide ``msvcrt``, named pipe e ``DETACHED_PROCESS``.
IS_WINDOWS = sys.platform == "win32"

#: O app está congelado num ``petwatch.exe`` pelo PyInstaller.
FROZEN = bool(getattr(sys, "frozen", False))

#: Onde o PyInstaller desempacota o executável congelado. Só existe no
#: build — é o que permite embutir o tema padrão dentro do ``.exe``.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else None


# ------------------------------------------------------------
# Caminhos
# ------------------------------------------------------------

def _windows_appdata(variable: str, *fallback: str) -> Path:
    """``%LOCALAPPDATA%`` / ``%APPDATA%``, com um caminho de reserva.

    A reserva cobre a máquina sem as variáveis — elas existem desde o
    Vista, mas um serviço pode rodar sem o perfil do usuário montado.
    """

    value = os.environ.get(variable, "").strip()

    if value:
        return Path(value)

    return Path.home().joinpath(*fallback)


def user_state_dir() -> Path:
    """Onde fica o estado do processo solto (lock, log, chave do pipe).

    Fora de :data:`BASE_DIR` de propósito — o estado é do usuário, não do
    código, e o diretório do projeto pode ser somente leitura.
    """

    if IS_WINDOWS:
        return _windows_appdata("LOCALAPPDATA", "AppData", "Local") / APP_NAME

    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"

    return Path(base) / APP_NAME


def user_config_dir() -> Path:
    """Onde ficam as preferências."""

    if IS_WINDOWS:
        return _windows_appdata("APPDATA", "AppData", "Roaming") / APP_NAME

    return Path.home() / ".config" / APP_NAME


def user_data_dir() -> Path:
    """Onde o app guarda dados que o usuário pode substituir.

    No Windows é o mesmo ``%LOCALAPPDATA%`` do estado: os pets são
    descartáveis e pesados, e o usuário espera que "apagar dados do app"
    leve junto.
    """

    if IS_WINDOWS:
        return _windows_appdata("LOCALAPPDATA", "AppData", "Local") / APP_NAME

    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"

    return Path(base) / APP_NAME


#: A raiz do código. Num build congelado é a pasta do próprio ``.exe`` —
#: ``__file__`` aponta para dentro de ``_MEIPASS``, que é temporário e não
#: serve para procurar dados do usuário.
BASE_DIR = (
    Path(sys.executable).resolve().parent
    if FROZEN
    else Path(__file__).resolve().parent.parent
)

STATE_DIR = user_state_dir()


def ensure_state_dir() -> Path:
    """Cria o diretório de estado, se ainda não existir.

    Quem chama é quem **assume** a instância — nunca quem só pergunta.
    Sem isto, o primeiro ``pet.py`` numa máquina nova recebia
    ``FileNotFoundError`` ao abrir o ``pet.pid``, e o ``claim()`` traduzia
    isso em "outro pet está rodando": a mensagem ``já existe um pet rodando
    (pid None)``, sem pet na tela e sem pista do motivo. O mesmo valeria no
    Windows, onde ``%LOCALAPPDATA%\\petwatch`` só existe depois do primeiro
    arranque.
    """

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    return STATE_DIR

#: Lock de instância única; o conteúdo é o pid de quem está rodando.
PID_PATH = STATE_DIR / "pet.pid"

#: Onde vai a saída quando o processo não tem mais terminal.
LOG_PATH = STATE_DIR / "pet.log"

# ------------------------------------------------------------
# Os pets
# ------------------------------------------------------------

#: Aponta a pasta de pets para outro lugar, para quem instala o
#: ``.exe`` num disco que não é o do perfil.
PETS_DIR_ENV = "PETWATCH_PETS_DIR"


def pets_candidates() -> list[Path]:
    """Pastas onde os pets podem estar, em ordem de preferência.

    A primeira que existir vence, e as outras continuam servindo como
    *fallback* para :func:`petwatch.theme.load_theme` — é o que permite
    embutir o tema padrão no ``.exe`` sem tirar a coleção do usuário do
    lugar dela.
    """

    candidates: list[Path] = []

    override = os.environ.get(PETS_DIR_ENV, "").strip()

    if override:
        candidates.append(Path(override).expanduser())

    candidates.append(BASE_DIR / "pets")

    candidates.append(user_data_dir() / "pets")

    if BUNDLE_DIR is not None:
        candidates.append(BUNDLE_DIR / "pets")

    # A primeira pasta sempre é a resposta mesmo que não exista: assim o
    # erro de "pets não instalados" aponta para um caminho real em vez de
    # ``None``.
    return list(dict.fromkeys(candidates))


PETS_DIR = next(
    (path for path in pets_candidates() if path.is_dir()),
    pets_candidates()[0],
)

#: Os pets que vieram dentro do executável. Só existe no build congelado,
#: e carrega o tema padrão (ver ``petwatch.spec``): um ``.exe`` sem nenhum
#: pet não abre, porque :func:`petwatch.app.PetApplication` cai no tema
#: padrão justamente quando o escolhido não existe.
BUNDLED_PETS_DIR = BUNDLE_DIR / "pets" if BUNDLE_DIR is not None else None

# ------------------------------------------------------------
# Conexão com o opencode
# ------------------------------------------------------------

HOST = "127.0.0.1"

EVENT_PATH = "/api/event"

USERNAME = "opencode"

RECONNECT_DELAY = 2.0

#: Comando usado para obter a senha do serviço local. É o *nome* do
#: executável: o :mod:`petwatch.discovery` resolve o caminho real, porque
#: no Windows o opencode chega como ``opencode2.exe`` (ou ``.cmd``, que o
#: ``CreateProcess`` não executa sem shell) e o ``PATH`` do usuário não é o
#: do processo solto.
PASSWORD_COMMAND = ("opencode2", "service", "get", "password")

PASSWORD_TIMEOUT = 5.0

#: Timeout da listagem de portas em escuta (``ss``, ``netstat`` ou a
#: leitura de ``/proc/net/tcp``).
PORT_SCAN_TIMEOUT = 3.0

#: Timeout do socket na checagem de vida do servidor.
PROBE_CONNECT_TIMEOUT = 1.5

#: Timeout do GET de sondagem em ``/api/event``, para uma porta já
#: conhecida — por exemplo a que respondeu na tentativa anterior.
PROBE_REQUEST_TIMEOUT = 3.0

#: Timeout do GET de sondagem durante a varredura. Menor, porque aqui a
#: porta é uma aposta, e quem responde rápido corta a varredura: a
#: diferença entre um ciclo de dois segundos e um de meio minuto.
SWEEP_REQUEST_TIMEOUT = 1.0

#: De quanto em quanto tempo a lista de portas em escuta é relida. Ela
#: muda quando o opencode abre ou fecha uma *location*, e o monitor
#: reconecta a cada ``RECONNECT_DELAY`` — reler a cada tentativa custaria
#: um subprocesso por ciclo no Windows, sem comprar nada.
PORTS_TTL_SECONDS = 10.0

#: Teto de portas sondadas por varredura. A sondagem é uma aposta, e
#: ganha quem responde: um teto mantém o pior caso previsível numa máquina
#: com muitos servidores locais, sem custo no caso normal (o opencode
#: responde na primeira ou na segunda).
MAX_SWEEP_PORTS = 48

#: Teto de linhas de escuta lidas antes de desistir da varredura. Só o
#: teto de leitura é generoso: ele segura o ``/proc/net/tcp`` de uma
#: máquina com NAT e o ``netstat`` de um servidor cheio.
MAX_LISTENERS = 512

#: Porta fixa, para quem não quer que o pet procure nada (o opencode num
#: host de outro lado de um port-forward, por exemplo).
PORT_ENV = "PETWATCH_PORT"

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
#:
#: É também o prazo de recuperação de um *location* anotado como morto:
#: ele sai da varredura — dois GETs por ciclo para sempre é desperdício — e
#: volta sozinho quando a lista é relida, sem precisar reiniciar o pet.
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
