"""Estados possíveis do pet e os rótulos do balão.

O balão tem duas linhas, como na referência visual: um título em negrito e
um subtítulo mais claro e discreto.
"""

from __future__ import annotations

from typing import Mapping

STATE_CONNECTING = "connecting"
STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_WAITING = "waiting"

#: ``estado -> (titulo em negrito, subtítulo)``.
STATE_LABELS: Mapping[str, tuple[str, str]] = {
    STATE_CONNECTING: ("Connecting", "starting up"),
    STATE_IDLE: ("Ready", "waiting for you"),
    STATE_WORKING: ("Thinking", "working on it"),
    STATE_WAITING: ("Waiting", "needs your answer"),
}

#: Estados aceitos por :meth:`petwatch.ui.pet_widget.PetRenderer.set_state`.
VALID_STATES = frozenset(STATE_LABELS)


def labels_for(state: str) -> tuple[str, str]:
    """Título e subtítulo de ``state``; vazio para estado desconhecido."""

    return STATE_LABELS.get(state, ("", ""))
