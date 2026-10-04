"""Rede de segurança do estado "working".

O opencode sinaliza o fim de um turno com ``session.idle``, e
``events.py`` traduz isso para o estado "pronto". Mas o pet não pode
depender de um único evento: se ele se perder — o próprio opencode avisa
que o stream é volátil e que "events during disconnection are missed" —
o pet ficaria preso em "Thinking" indefinidamente.

:class:`IdleWatchdog` observa que houve atividade e, se ela parar, devolve
o estado para "pronto". Ele não tenta adivinhar o que o opencode está
fazendo: só conta o tempo.

Roda na thread da interface, porque precisa de um ``QTimer`` — a thread do
monitor fica bloqueada na leitura do socket e não tem event loop.
"""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .config import IDLE_POLL_MS, IDLE_TIMEOUT
from .states import STATE_IDLE, STATE_WORKING

log = logging.getLogger(__name__)


class IdleWatchdog(QObject):
    """Volta para "pronto" depois de um tempo sem atividade."""

    #: Emitido uma vez por vez que o silêncio devolve para "pronto".
    idle_reached = Signal(str)

    def __init__(
        self,
        timeout: float = IDLE_TIMEOUT,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self.timeout = timeout

        #: Estado que o watchdog considera atual.
        self.state = STATE_IDLE

        self._last_activity = time.monotonic()

        self.timer = QTimer(self)

        self.timer.timeout.connect(self._check)

        self.timer.start(IDLE_POLL_MS)

    # ------------------------------------------------------------
    # Entradas
    # ------------------------------------------------------------

    def note_activity(self) -> None:
        """Um evento chegou do opencode."""

        self._last_activity = time.monotonic()

    def note_state(self, state: str) -> None:
        """O estado mudou; conta como atividade."""

        self.state = state
        self._last_activity = time.monotonic()

    # ------------------------------------------------------------
    # Lógica
    # ------------------------------------------------------------

    def seconds_since_activity(self) -> float:
        return time.monotonic() - self._last_activity

    def is_due(self, now: float | None = None) -> bool:
        """Diz se o silêncio já deu para voltar para "pronto"."""

        if self.state != STATE_WORKING:
            return False

        since = (
            time.monotonic() - self._last_activity
            if now is None
            else now - self._last_activity
        )

        return since >= self.timeout

    def _check(self) -> None:
        if not self.is_due():
            return

        self.state = STATE_IDLE

        log.debug("[pet] %ss sem atividade; voltando para pronto", self.timeout)

        self.idle_reached.emit(STATE_IDLE)
