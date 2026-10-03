"""petwatch — pet de desktop que reage ao estado do opencode."""

from __future__ import annotations

from .states import (
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_LABELS,
    STATE_WAITING,
    STATE_WORKING,
    VALID_STATES,
)
from .theme import PetTheme, ThemeNotFoundError, list_themes, load_theme

__all__ = [
    "PetTheme",
    "STATE_CONNECTING",
    "STATE_IDLE",
    "STATE_LABELS",
    "STATE_WAITING",
    "STATE_WORKING",
    "ThemeNotFoundError",
    "VALID_STATES",
    "list_themes",
    "load_theme",
]

__version__ = "2.0.0"
