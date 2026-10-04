"""Um balão por instância do opencode que está em ação.

Uma sessão do opencode não é um balão só: o usuário costuma ter várias
abas em projetos diferentes ao mesmo tempo, e o balão único era um
resumo que não dizia **de qual** instância o resumo era. Este módulo é
a lista de instâncias que deservecem um balão, e o estado de cada uma.

**O que é "em ação"** vem da própria documentação v2, na rota
``GET /api/session/active``: *"Retrieve foreground Session drains
currently owned by this OpenCode process. Sessions absent from the
result are inactive."* Ou seja: o conjunto de sessões que o servidor
diz estar em primeiro plano. Quem não está nessa lista não tem balão.

Sobre isso há duas correções, na mesma linha do bug 18:

- O **estado** de cada instância vem dos eventos, filtrados por
  ``sessionID`` — o ``session.tool.called`` de uma aba não pode virar o
  estado da outra. Antes o estado era global e o balão mentia sobre a
  origem.
- O "aguardando" **não** vem de evento nenhum (o stream é volátil e
  global): vem de ``GET /api/form`` e ``GET /api/permission/request``,
  e cada pedido vem com o ``sessionID`` dele — ``Form.Info`` e
  ``Permission.Request`` têm esse campo obrigatório na spec. É o que
  permite dizer *qual* instância está esperando resposta, e não "alguma
  coisa em algum lugar".

Cada instância também carrega um nome. Ele sai do próprio evento, quando
o opencode manda ``session.updated``/``session.viewed`` (o payload traz
``info.title`` e ``info.location.directory``), e de ``GET
/api/session/{id}`` para as que só aparecem na lista de ativas. O
fallback final é o diretório do projeto, e sem nada disso o prefixo do
``sessionID`` — porque "ses_1047…" ainda diz mais do que um balão
anônimo.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from PySide6.QtCore import QObject, Signal

from .config import (
    PENDING_ERROR_RETRY,
    PENDING_IDLE_POLL_SECONDS,
    PENDING_POLL_SECONDS,
    PROJECTS_TTL_SECONDS,
)
from .http import HttpError
from .pending import (
    active_sessions,
    pending_asks,
    session_info,
    watched_directory,
    watched_directories,
)
from .states import STATE_CONNECTING, STATE_IDLE, STATE_WAITING, STATE_WORKING

log = logging.getLogger(__name__)

#: Teto de balões desenhados. Quem passa disso não vira balão: a janela
#: ficaria alta demais e o que é urgente se perderia no meio do resto.
MAX_INSTANCES = 6

#: Sessão que não é vista nem é exigida por esse tempo sai da lista.
#: Existe folga de propósito: ``/api/session/active`` e o stream não
#: contam a mesma coisa no mesmo instante, e sem folga o balão pisca
#: conforme a ordem de leitura das respostas.
QUIET_GRACE_SECONDS = 12.0

def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def short_session_id(session_id: str) -> str:
    """``ses_1049…`` — o último pedaço é o que distingue duas abas."""

    return session_id[-4:] if len(session_id) > 6 else session_id


def project_name(directory: str | None) -> str | None:
    """Nome da pasta do projeto; ``None`` sem diretório.

    Separa os dois estilos de separador à mão em vez de confiar no
    ``os.path``: o servidor pode reportar ``C:\\projetos\\dd`` para um
    cliente em WSL, e aí o nome do balão viraria o caminho inteiro.
    """

    if not directory:
        return None

    trimmed = directory.rstrip("/\\")

    name = trimmed.replace("\\", "/").rsplit("/", 1)[-1]

    return name or None


@dataclass(slots=True)
class Instance:
    """Uma instância do opencode com balão próprio."""

    session_id: str

    #: Estado visível do balão.
    state: str = STATE_CONNECTING

    #: Nome da sessão, quando o opencode mandou.
    title: str | None = None

    #: Projeto onde ela roda.
    directory: str | None = None

    #: O servidor diz que ela está em primeiro plano.
    active: bool = False

    #: Há pedido esperando resposta **nesta** instância.
    needs_action: bool = False

    #: Quando foi a última vez que algo aconteceu nela.
    touched_at: float = 0.0

    #: Ordem de chegada, para desempatar sem mexer na lista toda.
    order: int = 0

    @property
    def label(self) -> str:
        """Nome de exibição: título, projeto ou fim do id da sessão."""

        for candidate in (self.title, project_name(self.directory),
                          short_session_id(self.session_id)):
            if candidate:
                return candidate

        return self.session_id

    @property
    def wants_attention(self) -> bool:
        """Se o balão desta instância deve sair na cor de alerta.

        Depende **só** de ``needs_action``, e não do estado: o estado de
        uma instância nunca é "aguardando" (nenhuma regra do stream
        produz isso, por decisão — ver :mod:`petwatch.events`). Quem
        diz que há espera é o servidor, e é o ``needs_action`` que veio
        de lá.
        """

        return self.needs_action


class SessionBoard:
    """As instâncias em ação, e o estado visível do pet.

    Junta as duas metades que antes eram uma só: quem decide "o pet está
    esperando o usuário" (a arbitragem) e quem decide "para **quem**"
    (este quadro). A regra do bug 18 continua valendo palavra por
    palavra: uma consulta que falha **não** muda o estado, e a trava por
    stream só entra quando o servidor não tem as rotas.

    Classe comum e não ``dataclass`` porque o ``__init__`` é escrito à
    mão: o relógio é injetável, e um ``__init__`` gerado por dataclass
    com ``slots=True`` não aceitaria um campo a mais sem ceremony.
    """

    #: ``sessionID -> Instance``.
    instances: dict[str, Instance]

    #: Verdadeiro enquanto o servidor responder nas rotas do v2.
    supported: bool

    #: ``None`` = o servidor ainda não respondeu.
    pending_answered: bool | None

    #: Trava por stream, só para a degradação.
    latched: bool

    #: Estado do stream sem dono — o que o pet mostra sem balão nenhum.
    stream_state: str

    #: Estado visível do pet, já arbitrado.
    state: str

    _order: int

    _now: float

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        """``clock`` existe para os testes: a folga de esquecimento é a
        única coisa do quadro que depende de tempo, e testá-la com o
        ``time.monotonic`` de verdade exigiria dormir."""

        self.instances = {}
        self.supported = True
        self.pending_answered = None
        self.latched = False
        self.stream_state = STATE_CONNECTING
        self.state = STATE_CONNECTING

        self._order = 0
        self._now = 0.0

        self._clock = clock or time.monotonic

    # ------------------------------------------------------------
    # Entradas: o stream
    # ------------------------------------------------------------

    def tick(self, value: float | None = None) -> float:
        """Avança o relógio do quadro.

        Só as entradas **do servidor** chamam isto sem argumento, e é de
        propósito: o relógio existe para a folga de esquecimento, que
        precisa da cadência da consulta (2s a 20s). Eventos do stream
        usam o último valor conhecido, o que também torna a ordem dos
        balões determinística em vez de depender de quantos eventos
        chegaram entre uma consulta e outra.
        """

        self._now = self._clock() if value is None else value

        return self._now

    def note_event(self, session_id: str | None, state: str | None) -> None:
        """Um evento que traduziu para ``state``, naquela sessão.

        ``session_id`` é ``None`` para evento sem dono — global do
        servidor (``server.connected``, ``project.updated``). Esses não
        criam balão: são ruído entre as abas.
        """

        if state:
            self.stream_state = state

            if state == STATE_IDLE:
                self.latched = False

        if session_id is not None and state:
            instance = self._touch(session_id)

            instance.state = state

        self._refresh()

    def note_global_state(self, state: str) -> None:
        """Estado do stream sem balão: o "pronto" geral, e a degradação."""

        self.stream_state = state

        if state == STATE_IDLE:
            self.latched = False

        self._refresh()

    def demote_stale(self, timeout: float) -> None:
        """Instância que calou há mais que ``timeout`` não está trabalhando.

        Sem isso o watchdog perde o sentido: ele existe para o caso de o
        opencode **parar de mandar eventos**, mas com o estado por sessão
        um balão ficaria em "Thinking" para sempre — o watchdog mudava o
        sprite para "Ready" e o balão continuava dizendo "Thinking", que
        é pior que qualquer um dos dois.

        Uma instância esperando resposta é exceção: ela não calou, está
        esperando o usuário, e o ``waiting`` continua certo.
        """

        deadline = self._clock() - timeout

        for instance in self.instances.values():
            if instance.needs_action or instance.state != STATE_WORKING:
                continue

            if instance.touched_at < deadline:
                instance.state = STATE_IDLE

        self._refresh()

    def note_ask(self) -> None:
        """O stream viu um pedido.

        Não decide nada: só acende a rede de segurança da degradação. Quem
        confirma é a consulta ao servidor.
        """

        self.latched = True

        self._refresh()

    def note_release(self) -> None:
        """O stream viu a resposta a um pedido."""

        self.latched = False

        self._refresh()

    # ------------------------------------------------------------
    # Entradas: o servidor
    # ------------------------------------------------------------

    def note_active(self, active: Iterable[str] | None) -> None:
        """Sessões em primeiro plano, de ``GET /api/session/active``.

        ``None`` = a consulta falhou, e aí nada muda: as instâncias que
        estão na lista continuam, porque o usuário não pode ver um pet
        vazio por causa de um GET que deu timeout.
        """

        self.tick()

        if active is None:
            return

        current = list(dict.fromkeys(active))

        for session_id in current:
            instance = self._touch(session_id)

            instance.active = True

            current = list(dict.fromkeys(active))

        for session_id in current:
            instance = self._touch(session_id)

            instance.active = True

            # O servidor lista aqui as sessões "*running*" em primeiro
            # plano, então uma sessão que ainda não gerou evento não
            # está parada esperando nada: está trabalhando. Sem isso o
            # balão dela nasceria sem título (o estado ``connecting`` é
            # mudo por contrato).
            if instance.state == STATE_CONNECTING:
                instance.state = STATE_WORKING

        active_set = set(current)

        for session_id, instance in self.instances.items():
            instance.active = session_id in active_set

        self._prune()

        self._refresh()

    def note_pending(self, asks: Sequence[Any] | None) -> None:
        """Pedidos pendentes de ``GET /api/form`` e ``/api/permission/request``.

        Cada pedido carrega o ``sessionID`` dele (``PendingAsk``), então
        "aguardando" é do balão certo — não de qualquer aba.

        ``None`` = consulta falhou, ou o servidor não tem a rota. Nos
        dois casos o estado anterior continua valendo.
        """

        self.tick()

        if asks is None:
            self.pending_answered = None

            self._refresh()

            return

        self.pending_answered = True

        # Resposta autoritativa: a trava do stream não vale mais. Ela só
        # existe para quando o servidor não tem as rotas, e mantê-la acesa
        # depois de uma resposta real seria um "aguardando" inventado.
        self.latched = False

        waiting = {ask.session_id for ask in asks if ask.session_id}

        for session_id, instance in self.instances.items():
            instance.needs_action = session_id in waiting

        # Um pedido de uma sessão que ainda não tem balão cria o balão:
        # é a única evidência de que ela existe e de que quer algo.
        for session_id in waiting:
            self._touch(session_id).needs_action = True

        for ask in asks:
            if not ask.session_id:
                continue

            instance = self.instances.get(ask.session_id)

            if instance is not None and not instance.directory:
                instance.directory = ask.directory

        self._prune()

        self._refresh()

    def note_session_info(self, session_id: str, info: Mapping[str, Any]) -> None:
        """``GET /api/session/{id}``: nome e projeto de uma instância."""

        self.tick()

        instance = self._touch(session_id)

        title = _text(info.get("title"))

        if title:
            instance.title = title

        location = info.get("location")

        if isinstance(location, Mapping):
            instance.directory = _text(location.get("directory")) or instance.directory

    def note_session_meta(self, session_id: str, title: str | None,
                          directory: str | None) -> None:
        """Nome e projeto vindos de um evento (``session.updated``)."""

        instance = self._touch(session_id)

        if title:
            instance.title = title

        if directory:
            instance.directory = directory

    # ------------------------------------------------------------
    # Saída
    # ------------------------------------------------------------

    def visible(self) -> list[Instance]:
        """Instâncias que deservecem balão, na ordem em que vão aparecer.

        A ordem é por urgência, não por chegada: o que espera resposta
        vem primeiro, depois o que trabalha, e dentro de cada grupo o mais
        recente. Balão de espera escondido atrás de um "trabalhando" é
        exatamente o defeito que o bug 18 corrigiu, só que na grade.

        Uma instância que saiu da lista de ativas **continua** aparecendo
        durante a folga: ``/api/session/active`` e o stream não contam a
        mesma coisa no mesmo instante, e sem isso o balão piscaria a cada
        tique da consulta.
        """

        deadline = self._clock() - QUIET_GRACE_SECONDS

        ranked = sorted(
            (
                instance
                for instance in self.instances.values()
                if instance.active
                or instance.needs_action
                or instance.touched_at > deadline
            ),
            key=lambda i: (
                0 if i.wants_attention else 1 if i.state == STATE_WORKING else 2,
                -i.touched_at,
                i.order,
            ),
        )

        return ranked[:MAX_INSTANCES]

    def headline(self) -> str:
        """Estado do pet: o que o sprite faz, somando as instâncias."""

        visible = self.visible()

        if not visible:
            return self.stream_state

        if any(i.wants_attention for i in visible):
            return STATE_WAITING

        if any(i.state == STATE_WORKING for i in visible):
            return STATE_WORKING

        return STATE_IDLE

    # ------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------

    def _touch(self, session_id: str) -> Instance:
        instance = self.instances.get(session_id)

        if instance is None:
            self._order += 1

            instance = Instance(
                session_id=session_id,
                title=None,
                directory=None,
                order=self._order,
                touched_at=self._now,
            )

            self.instances[session_id] = instance

        instance.touched_at = self._now

        return instance

    def _prune(self) -> None:
        """Esquece a instância que não está em ação e ninguém mais pede."""

        deadline = self._clock() - QUIET_GRACE_SECONDS

        for session_id in [
            session_id
            for session_id, instance in self.instances.items()
            if not instance.active
            and not instance.needs_action
            and instance.touched_at < deadline
        ]:
            del self.instances[session_id]

    def _refresh(self) -> str | None:
        """Estado novo do pet, ou ``None`` se não mudou."""

        wanted = self._wanted()

        if wanted == self.state:
            return None

        self.state = wanted

        return wanted

    def _wanted(self) -> str:
        if any(i.wants_attention for i in self.instances.values()):
            return STATE_WAITING

        # Sem as rotas do v2, ou antes da primeira resposta, a trava do
        # stream assume: é imperfeita, mas melhor que um pet mudo.
        if self.pending_answered is None and self.latched:
            return STATE_WAITING

        return self.headline()


# ------------------------------------------------------------
# O laço que pergunta ao servidor
# ------------------------------------------------------------


class StatusPoller(QObject):
    """Pergunta ao servidor, num laço próprio, o quadro de instâncias.

    Três consultas por ciclo, e todas documentadas:

    - ``GET /api/session/active`` — quem está em primeiro plano, ou seja,
      quem merece balão;
    - ``GET /api/session/{id}`` — o nome de cada uma, uma vez só (fica em
      cache: o título de uma sessão não muda enquanto ela dura);
    - ``GET /api/form`` e ``GET /api/permission/request`` — o que está
      esperando resposta, por *location*.

    Thread própria (``threading``, não ``QThread``) porque só precisa de
    ``Event.wait`` e de GETs curtos: a thread do monitor fica bloqueada
    em ``readline()`` do stream, e a da interface não pode esperar por
    socket.

    Três sinais, porque o app reage a cada coisa separadamente:
    ``active`` (a lista mudou), ``answers`` (o que está pendente) e
    ``named`` (uma instância ganhou nome).
    """

    #: ``[sessionID, ...]`` das sessões em primeiro plano.
    active = Signal(object)

    #: Lista de :class:`~petwatch.pending.PendingAsk`, ou ``None`` quando
    #: a consulta falhou / a rota não existe.
    answers = Signal(object)

    #: ``(sessionID, info)`` de uma instância que acabou de ser nomeada.
    named = Signal(object)

    def __init__(
        self,
        port_provider: Callable[[], int | None],
        password_provider: Callable[[], str | None],
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._port_provider = port_provider
        self._password_provider = password_provider

        self.stop_event = threading.Event()

        #: Verdadeiro enquanto o servidor responder nas rotas do v2.
        self.supported = True

        self._thread: threading.Thread | None = None

        self._woken = threading.Event()

        self._projects: list[str] = []

        self._projects_read_at = 0.0

        #: Locations que têm pedido aberto. No ciclo rápido só elas são
        #: consultadas: os GETs são locais e baratos, mas um servidor
        #: compartilhado com meia dúzia de projetos somaria duas
        #: consultas por projeto a cada 2s.
        self._hot: list[str] = []

        #: ``sessionID -> info`` já buscado. O nome de uma sessão é
        #: estável, e refazer o GET a cada ciclo seria desperdício.
        self._names: dict[str, dict[str, Any]] = {}

        #: A senha vem de um ``subprocess`` (``opencode service get
        #: password``), e ela não muda enquanto o serviço roda. Consultar
        #: de novo a cada ciclo seria um processo a cada 2s por nada.
        self._password: str | None = None

    # ------------------------------------------------------------
    # Entradas
    # ------------------------------------------------------------

    def poke(self) -> None:
        """Um pedido apareceu no stream (ou o stream reconectou).

        É o que mantém o pulso instantâneo. O evento sozinho não decide
        o estado — ele só evita que o pet espere o tique do poll para
        perguntar ao servidor.
        """

        self._woken.set()

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self.run, name="petwatch-status", daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._woken.set()

    # ------------------------------------------------------------
    # Laço
    # ------------------------------------------------------------

    def run(self) -> None:
        """Pergunta, publica e dorme."""

        log.debug("[pet] consulta de status iniciada")

        while not self.stop_event.is_set():
            delay = self._cycle(fast=self._hot != [])

            self._woken.wait(delay)
            self._woken.clear()

        log.debug("[pet] consulta de status encerrada")

    def _cycle(self, *, fast: bool = True) -> float:
        """Um ciclo inteiro; devolve quanto dormir depois.

        ``fast`` é o ciclo de quem **tem** algo pendente: só as locations
        queuosas são consultadas. O ciclo lento (``fast=False``) varre
        todos os projetos, e é ele que pega um pedido que apareceu sem o
        stream contar.
        """

        port = self._port_provider()

        if port is None:
            self.answers.emit(None)
            self.active.emit(None)

            return PENDING_ERROR_RETRY

        password = self._service_password()

        if not password:
            return PENDING_ERROR_RETRY

        self._collect_active(port, password)

        try:
            directories = self._directories(port, password, fast=fast)

            asks = pending_asks(port, password, directories)

        except Exception as exc:  # rede, HTTP, location inválida
            # Falhou não é "não há pendência": manter o estado é a
            # escolha que não inventa resposta do usuário.
            log.debug("[pet] consulta de pendência falhou: %s", exc)

            self.answers.emit(None)

            return PENDING_ERROR_RETRY

        if asks is None:
            if self.supported:
                log.info(
                    "[pet] este servidor não tem as rotas de pendência "
                    "do v2; 'aguardando' volta a vir do stream",
                )

            self.supported = False

            self.answers.emit(None)

            return PENDING_ERROR_RETRY

        self.supported = True

        self._hot = sorted({ask.directory for ask in asks
                            if ask.directory is not None})

        pending = bool(asks)

        self.answers.emit(asks)

        if pending:
            log.debug("[pet] pendente: %s", ", ".join(a.describe() for a in asks))

        return PENDING_POLL_SECONDS if pending else PENDING_IDLE_POLL_SECONDS

    # ------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------

    def _service_password(self) -> str | None:
        """Senha do serviço, lida uma vez e reaproveitada."""

        if self._password is None:
            self._password = self._password_provider()

        return self._password

    def _collect_active(self, port: int, password: str) -> None:
        """``GET /api/session/active`` e o nome das que entraram."""

        sessions = active_sessions(port, password)

        # ``None`` = não deu para saber. Emitir ``[]`` aqui apagaria os
        # balões por causa de um GET que deu timeout.
        if sessions is None:
            self.active.emit(None)

            return

        self.active.emit(sessions)

        for session_id in sessions:
            if session_id in self._names:
                continue

            try:
                info = session_info(port, password, session_id)

            except (HttpError, OSError) as exc:
                # Nomear uma instância é um extra: falhar aqui não pode
                # derrubar o ciclo, que ainda tem pendências e ativas
                # para publicar. O balão fica sem nome e usa o id.
                log.debug("[pet] nome de %s indisponível: %s", session_id, exc)

                continue

            if info is None:
                continue

            self._names[session_id] = info

            self.named.emit((session_id, info))

    def _directories(self, port: int, password: str, *,
                     fast: bool) -> list[str]:
        """Locations a consultar, com a lista de projetos em cache."""

        explicit = watched_directory()

        if explicit is not None:
            return [explicit]

        now = time.monotonic()

        if self._projects and now - self._projects_read_at < PROJECTS_TTL_SECONDS:
            projects = self._projects

        else:
            projects = self._projects = watched_directories(port, password)
            self._projects_read_at = now

        # No ciclo rápido, só as locations que já têm pedido aberto. O
        # balão é um só por instância, então consultar as outras não
        # mudaria nada na tela — e são dois GETs por projeto a cada 2s.
        # O que a varredura completa pega é o caminho inverso: um pedido
        # novo, que chega antes por ``poke()``, vinda do próprio stream.
        if fast and self._hot:
            hot = [d for d in self._hot if d in projects]

            if hot:
                return hot

        return projects