"""Monitor que observa o opencode e publica mudanças de estado.

Roda em uma :class:`~PySide6.QtCore.QThread` separada: o SSE é bloqueante,
então a thread existe só para não travar a interface.

O ponto delicado é o encerramento. A leitura fica presa em ``readline()`` e
não retorna sozinha, então :meth:`stop` não apenas marca o evento de
parada: ele também desliga o socket, o que faz a leitura voltar
imediatamente e a thread terminar. Sem isso ``shutdown()`` estoura o
``thread.wait()`` e o processo morre com SIGABRT.

**O que o stream não decide.** A documentação v2 avisa que ``/api/event``
é *"volatile by contract: ... events during disconnection are missed"* e
que traz eventos *"across all server locations"*. Por isso este módulo
não publica "aguardando": ele publica :attr:`ask_seen`, que serve para
acordar a consulta de pendência do :mod:`petwatch.pending`. Um
``form.created`` é o **gatilho** de uma checagem, não o estado.
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
from .events import (
    extract_session_id,
    is_ask_event,
    is_release_event,
    state_from_event,
)
from .http import Connection, HttpError
from .sse import consume, read_lines
from .states import STATE_CONNECTING, STATE_IDLE

log = logging.getLogger(__name__)


class OpenCodeMonitor(QObject):
    """Publica :attr:`state_changed` conforme os eventos do opencode."""

    state_changed = Signal(str)

    #: Qualquer evento recebido, mesmo sem mudança de estado. O watchdog
    #: usa para contar o tempo de atividade.
    activity = Signal()

    #: O opencode pediu algo (permissão ou formulário) ou respondeu a um
    #: pedido. Não é estado: é o aviso de que a consulta de pendência
    #: precisa rodar agora. Ver :mod:`petwatch.sessions`.
    ask_seen = Signal()

    #: O stream (re)conectou. Também pede uma consulta: o que aconteceu
    #: durante a queda ninguém sabe, porque o stream é volátil por
    #: contrato.
    reconnected = Signal()

    #: ``(estado, sessionID)`` de um evento que virou estado, para o
    #: balão da instância que o produziu. ``sessionID`` é ``None`` em
    #: evento sem dono (``server.connected``, ``project.updated``), que
    #: não cria balão nenhum.
    session_event = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()

        self.stop_event = threading.Event()

        self.port: int | None = None

        #: Último estado emitido, para não repetir o sinal à toa.
        self._state: str | None = None

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
        """Callback do stream: traduz o evento e emite o estado.

        O sinal de atividade sai antes do estado: mesmo um evento que não
        muda o estado prova que o opencode está ativo, e é isso que
        segura o watchdog.

        O estado só é emitido quando muda de verdade — durante um turno
        chegam dezenas de eventos ``working`` seguidos, e repetir o sinal
        encheria o log sem acrescentar informação. O watchdog continua
        recebendo *todos* os eventos pelo sinal de atividade.

        Além do estado global, cada evento com estado publica também o
        ``sessionID`` dele: é o que dá um balão por instância, em vez de
        um resumo sem dono. Só os eventos que viram estado passam por
        aqui — os deltas de texto, que são a maioria, não interessam a
        nenhum balão.

        Pedido e resposta não viram estado aqui: eles disparam
        :attr:`ask_seen`, e quem diz se o pet está esperando o usuário é
        a consulta ao servidor.
        """

        self.activity.emit()

        if is_ask_event(event_name, data) or is_release_event(event_name, data):
            self.ask_seen.emit()

        state = state_from_event(event_name, data)

        if state:
            session_id = extract_session_id(data)

            self.session_event.emit(state, session_id)

            self.emit_state(state)

    def emit_state(self, state: str) -> None:
        """Publica ``state`` se for diferente do último publicado."""

        if state == self._state:
            return

        self._state = state

        self.state_changed.emit(state)

    def run(self) -> None:
        """Loop de conexão; encerra quando :meth:`stop` é chamado."""

        self.emit_state(STATE_CONNECTING)

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
            # O que aconteceu enquanto não estávamos conectados ninguém
            # sabe: o stream é volátil por contrato. Em vez de adivinhar,
            # o pet **pergunta** ao servidor o que está pendente logo
            # depois de conectar — é a primeira coisa que a thread da
            # pendência faz, e é por isso que uma pergunta que existia
            # antes da queda volta ao balão.
            self.reconnected.emit()

            self.emit_state(STATE_IDLE)

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

        self.emit_state(STATE_CONNECTING)

        self.stop_event.wait(RECONNECT_DELAY)
