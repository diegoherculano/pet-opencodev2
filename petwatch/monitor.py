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
não publica "aguardando": ele publica :attr:`ask_seen` e
:attr:`released`, que servem para acordar a consulta de pendência do
:mod:`petwatch.pending`. Um ``form.created`` é o **gatilho** de uma
checagem, não o estado.

Os dois carregam o ``sessionID`` do pedido. O stream é global, então o
``form.created`` de uma aba e o ``session.idle`` de outra são eventos de
abas diferentes; sem dono, a espera que eles desenham não tem de quem ser, e
qualquer evento de qualquer aba a derruba. Ver o bug 20 em ``docs/BUGS.md``.
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
    extract_location_directory,
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

    #: O opencode pediu algo (permissão ou formulário). Não é estado: é o
    #: aviso de que a consulta de pendência precisa rodar agora, e de que
    #: uma espera começou. Carrega o ``sessionID`` do pedido — ou ``None``
    #: quando o evento não diz de quem é. Ver :mod:`petwatch.sessions`.
    ask_seen = Signal(object)

    #: O opencode respondeu a um pedido. Mesmo formato de
    #: :attr:`ask_seen`: quem respondeu, ou ``None``.
    released = Signal(object)

    #: O stream (re)conectou. Também pede uma consulta: o que aconteceu
    #: durante a queda ninguém sabe, porque o stream é volátil por
    #: contrato.
    reconnected = Signal()

    #: ``(estado, sessionID)`` de um evento que virou estado, para o
    #: balão da instância que o produziu. ``sessionID`` é ``None`` em
    #: evento sem dono (``server.connected``, ``project.updated``), que
    #: não cria balão nenhum.
    session_event = Signal(str, object)

    #: **Todo** evento do stream, com o dono que ele tiver:
    #: ``(sessionID|None, diretório|None)``.
    #:
    #: Este sinal é a prova de vida, e é separado do
    #: :attr:`session_event` por um motivo medido: num turno real de 150s
    #: chegaram 819 eventos e **771 deles eram
    #: ``session.reasoning.delta``** — que não vira estado porque é um
    #: instante interno do turno. Publicar só o que vira estado deixava o
    #: relógio de silêncio de :class:`~petwatch.sessions.SessionBoard`
    #: congelado durante o raciocínio inteiro, e o balão caía para "Ready"
    #: com o agente pensando (bug 22).
    #:
    #: O segundo campo existe porque nem todo evento traz ``sessionID``:
    #: ``shell.created``, ``shell.exited`` e ``file.edited`` são do
    #: *location*, e o envelope traz o diretório — ver
    #: :func:`petwatch.events.extract_location_directory`.
    session_alive = Signal(object, object)

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

        A prova de vida por sessão sai logo depois, com **todo** evento:
        ``session.reasoning.delta`` e ``session.text.delta`` são a maior
        parte do stream e nenhum dos dois vira estado, mas os dois dizem
        que aquela sessão está viva. Publicar só o que vira estado media
        o trabalho errado e produzia "Ready" no meio do raciocínio
        (bug 22).

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
        :attr:`ask_seen` e :attr:`released`, e quem diz se o pet está
        esperando o usuário é a consulta ao servidor.

        O ``sessionID`` é extraído uma vez só e serve aos três sinais. Ele
        importa porque o stream é global: um ``form.created`` de uma aba e
        um ``session.idle`` de outra são eventos de abas diferentes, e
        tratar os dois como "o mesmo estado global" é o bug 15.
        """

        self.activity.emit()

        session_id = extract_session_id(data)

        # Antes de qualquer regra: a prova de vida não depende do estado.
        self.session_alive.emit(session_id, extract_location_directory(data))

        if is_ask_event(event_name, data):
            self.ask_seen.emit(session_id)

        if is_release_event(event_name, data):
            self.released.emit(session_id)

        state = state_from_event(event_name, data)

        if state:
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
