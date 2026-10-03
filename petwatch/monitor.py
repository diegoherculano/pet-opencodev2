"""Monitor que observa o opencode e publica mudanças de estado.

Roda em uma :class:`~PySide6.QtCore.QThread` separada: o SSE é bloqueante,
então a thread existe só para não travar a interface.

O ponto delicado é o encerramento. A leitura fica presa em ``readline()`` e
não retorna sozinha, então :meth:`stop` não apenas marca o evento de
parada: ele também desliga o socket, o que faz a leitura voltar
imediatamente e a thread terminar. Sem isso ``shutdown()`` estoura o
``thread.wait()`` e o processo morre com SIGABRT.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from .config import RECONNECT_DELAY
from .discovery import (
    find_opencode_server,
    get_opencode_password,
    open_event_stream,
)
from .events import state_from_event
from .http import Connection, HttpError
from .sse import consume, read_lines
from .states import STATE_CONNECTING, STATE_IDLE

log = logging.getLogger(__name__)


class OpenCodeMonitor(QObject):
    """Publica :attr:`state_changed` conforme os eventos do opencode."""

    state_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        self.stop_event = threading.Event()

        self.port: int | None = None

        #: Conexão SSE em uso, para poder ser interrompida de fora.
        self._connection: Connection | None = None

    # ------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------

    def stop(self) -> None:
        """Pede a parada e destrava a leitura em andamento."""

        self.stop_event.set()

        connection = self._connection

        if connection is not None:
            connection.interrupt()

    def process_event(self, event_name: str | None, data) -> None:
        """Callback do stream: traduz o evento e emite o estado."""

        state = state_from_event(event_name, data)

        if state:
            self.state_changed.emit(state)

    def run(self) -> None:
        """Loop de conexão; encerra quando :meth:`stop` é chamado."""

        self.state_changed.emit(STATE_CONNECTING)

        while not self.stop_event.is_set():
            try:
                self._session()
            except (HttpError, OSError) as exc:
                if self.stop_event.is_set():
                    break

                log.debug("[pet] conexão caiu: %s", exc)

                self._wait_retry()
            except Exception as exc:
                if self.stop_event.is_set():
                    break

                log.debug("[pet] erro no monitor: %s", exc)

                self._wait_retry()

    # ------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------

    def _session(self) -> None:
        """Conecta, consome o stream e reconecta quando ele cai."""

        password = get_opencode_password()

        if not password:
            self._wait_retry()
            return

        port = find_opencode_server(password)

        if port is None:
            self.port = None
            self._wait_retry()
            return

        self.port = port

        connection = self._connection = open_event_stream(port, password)

        try:
            self.state_changed.emit(STATE_IDLE)

            consume(
                read_lines(connection, self.stop_event),
                self.stop_event,
                self.process_event,
            )

        finally:
            self._connection = None
            connection.close()

    def _wait_retry(self) -> None:
        """Volta ao estado de conexão e aguarda antes de tentar de novo."""

        self.state_changed.emit(STATE_CONNECTING)

        self.stop_event.wait(RECONNECT_DELAY)
