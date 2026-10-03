"""Constantes e caminhos usados pelo aplicativo.

Os valores aqui são os mesmos do monólito original (``pet.py``); a
separação existe apenas para que cada módulo dependa do que precisa.
"""

from __future__ import annotations

from pathlib import Path

# ------------------------------------------------------------
# Caminhos
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

PETS_DIR = BASE_DIR / "pets"

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
# Tema
# ------------------------------------------------------------

DEFAULT_THEME = "eevee"

# ------------------------------------------------------------
# Janela
# ------------------------------------------------------------

#: A janela encolhe para abraçar o sprite: o balão fica no topo e o pet
#: apoiado no rodapé, com folga nas laterais. A altura já conta o padding
#: transparente que a célula da spritesheet traz ao redor do desenho.
WINDOW_WIDTH = 250
WINDOW_HEIGHT = 216

#: Folga entre a janela e a borda direita / inferior da área de trabalho.
SCREEN_GAP_X = 30
SCREEN_GAP_Y = 50

# ------------------------------------------------------------
# Sprite
# ------------------------------------------------------------

#: O sprite é exibido pequeno, como mascote ao lado do balão. A célula da
#: spritesheet tem 192x208, então sobra padding transparente ao redor.
SPRITE_MAX_WIDTH = 132
SPRITE_MAX_HEIGHT = 144

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
