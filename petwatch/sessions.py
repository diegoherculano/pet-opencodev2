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

**A espera também é por instância.** Quando o servidor não tem as rotas
do v2, o que se sabe sobre pedidos vem do stream — que é global e volátil.
Aí a espera vira uma trava, e a trava guarda **de quem** foi o pedido
(:meth:`SessionBoard.note_ask`), porque sem dono qualquer evento de
qualquer aba a solta. Ver o bug 20 em ``docs/BUGS.md``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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
    watched_directories,
    watched_directory,
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

    #: Quando foi o último **evento** do stream desta sessão.
    #:
    #: Separado de :attr:`touched_at` porque os dois medem coisas
    #: diferentes, e a confusão entre os dois é o bug 21: ``touched_at`` é
    #: reescrito a cada consulta de ``/api/session/active``, enquanto este
    #: só anda quando o opencode realmente emite algo. Uma sessão que o
    #: servidor continua listando como ``running`` mas que não emite evento
    #: nenhum tem ``touched_at`` sempre fresco e :attr:`evented_at` parado —
    #: só o segundo campo conta silêncio de verdade.
    #:
    #: **Qualquer** evento da sessão o move, não só os que viram estado:
    #: ver :meth:`SessionBoard.note_alive` e o bug 22.
    evented_at: float = 0.0

    #: O "pronto" deste balão foi um palpite nosso, derivado do silêncio,
    #: e não algo que o servidor disse. Ver :meth:`SessionBoard.demote_stale`.
    demoted: bool = False

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
        """Se o servidor disse que esta instância espera resposta.

        Depende **só** de ``needs_action``, e não do estado: o estado de
        uma instância nunca é "aguardando" (nenhuma regra do stream
        produz isso, por decisão — ver :mod:`petwatch.events`). Quem diz
        que há espera é o servidor, e é o ``needs_action`` que veio de lá.

        A rede de segurança da degradação (o stream viu um pedido sem o
        servidor responder) mora em :meth:`SessionBoard._wants`, porque
        ela é do quadro e não da instância: depende do estado do servidor
        no momento.
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

    #: Estado do stream sem dono — o que o pet mostra sem balão nenhum.
    stream_state: str

    #: Estado visível do pet, já arbitrado.
    state: str

    #: Sessões que pediram algo e ainda não responderam, pela rede de
    #: segurança do stream. Só é consultado quando o servidor não
    #: responde — ver :attr:`_orphan_latch`.
    _latched: set[str]

    #: Um pedido cujo evento não traz ``sessionID`` não pode ser atribuído
    #: a uma sessão, e a espera é de todo mundo.
    _orphan_latch: bool

    _order: int

    _now: float

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        """``clock`` existe para os testes: a folga de esquecimento é a
        única coisa do quadro que depende de tempo, e testá-la com o
        ``time.monotonic`` de verdade exigiria dormir."""

        self.instances = {}
        self.supported = True
        self.pending_answered = None
        self.stream_state = STATE_CONNECTING
        self.state = STATE_CONNECTING

        self._latched = set()
        self._orphan_latch = False

        self._order = 0
        self._now = 0.0

        self._clock = clock or time.monotonic

    # ------------------------------------------------------------
    # Degradação: a espera que o stream deixou ver
    # ------------------------------------------------------------

    @property
    def latched(self) -> bool:
        """Há algum pedido pendente pelo stream?

        Só é levado em conta quando o servidor não responde nas rotas do
        v2 (:attr:`pending_answered` é ``None``). Com o servidor
        respondendo, quem decide é ele — e um evento perdido não pode
        virar um "aguardando" que não existe.
        """

        return bool(self._latched or self._orphan_latch)

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

    def note_event(self, session_id: str | None, state: str | None,
                   directory: str | None = None) -> None:
        """Um evento que traduziu para ``state``, naquela sessão.

        ``session_id`` é ``None`` para evento sem dono — global do
        servidor (``server.connected``, ``project.updated``). Esses não
        criam balão: são ruído entre as abas.

        O fim de turno de uma sessão solta a espera **dela**, e só dela: um
        ``session.idle`` de outra aba não pode derrubar o pedido de uma
        pergunta que continua aberta. A trava global que existia antes
        disso é o bug 15 de novo, e ele só não aparece quando a degradação
        está desligada.

        ``directory`` é o *location* do evento, e é anotado aqui porque é
        ele que dá dono aos eventos que não trazem ``sessionID`` —
        ``shell.created``, ``file.edited`` e companhia — quando o
        :meth:`note_alive` precisar atribuir por projeto.
        """

        if state:
            self.stream_state = state

            if state == STATE_IDLE:
                self._release(session_id)

        if session_id is not None and state:
            instance = self._touch(session_id)

            if directory:
                instance.directory = directory

            instance.state = state

            # Um estado que veio do servidor vale mais do que qualquer
            # palpite nosso: o palpite do silêncio está desatualizado.
            instance.demoted = False

            # Só o stream anda este relógio. `_touch` acima já refrescou
            # `touched_at`, que a consulta de ativas reescreve a cada ciclo
            # — ver `Instance.evented_at`.
            #
            # O carimbo é `self._clock()` e **não** `self._now`: `self._now`
            # só anda quando a consulta de status passa, então usá-lo aqui
            # atrasava a prova de vida em até um ciclo do poll (20s) e
            # encurtava o timeout na mesma medida. Era o bug 22.
            self._prove_alive(instance, self._clock())

        self._refresh()

    def note_alive(self, session_id: str | None,
                   directory: str | None = None) -> None:
        """Prova de vida do stream, **sem** estado.

        É a entrada que segura o balão em "Thinking". ``demote_stale``
        decide que uma instância parou de trabalhar olhando quanto tempo
        faz que ela não fala — e, sem esta entrada, o que contava como
        "falar" era só o evento que virava estado **e** trazia
        ``sessionID``. Medido num turno real de 150s: 819 eventos, dos
        quais **771 eram ``session.reasoning.delta``** (nem viram estado,
        porque são instante interno do turno) e os ``shell.*`` não trazem
        ``sessionID`` nenhum. O relógio ficava congelado durante o
        raciocínio inteiro e o balão caía para "Ready" com o agente
        pensando — bug 22.

        O dono é procurado nesta ordem:

        - ``session_id``, quando o evento traz — é o dono preciso;
        - ``directory``, quando não traz. `shell.created`, ``shell.exited``
          e ``file.edited`` são eventos de *location*, e o envelope carrega
          o diretório; o esquema do opencode confirma que o payload deles
          não tem ``sessionID`` (ver
          :func:`petwatch.events.extract_location_directory`).

        Nenhum dos dois: o evento é global do servidor e não diz nada sobre
        uma instância específica — aí nada é movido, porque atribuir por
        palpite seria inventar dono.

        **Não muda estado.** O que muda é o relógio, e é
        :meth:`demote_stale` que age sobre ele. A única exceção é
        desfazer um rebaixamento anterior por silêncio (ver
        :attr:`Instance.demoted`): a inferência é nossa, e a prova de vida
        é do servidor, então a prova vence.
        """

        if session_id is None and not directory:
            return

        now = self._clock()

        spoken = False

        for instance in self.instances.values():
            if not self._owned_by(instance, session_id, directory):
                continue

            spoken = True

            self._prove_alive(instance, now)

        if spoken:
            self._refresh()

    def note_global_state(self, state: str) -> None:
        """Estado do stream sem balão: o "pronto" geral, e a degradação.

        Aqui o "pronto" é global de verdade — é o monitor reconectando, e o
        que passou na queda ninguém sabe. Por isso esta solta **todas** as
        esperas, e não só uma.
        """

        self.stream_state = state

        if state == STATE_IDLE:
            self._release(None)

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

        **Mede :attr:`Instance.evented_at`, não ``touched_at``.** Esta é a
        correção do bug 21: ``touched_at`` é reescrito por
        :meth:`note_active` a cada ciclo do ``StatusPoller``, então uma
        instância que o servidor continua listando como ``running`` — e que
        não emite evento nenhum — tinha ``touched_at`` sempre fresco e
        ``demote_stale`` nunca a alcançava. O balão ficaria em "Thinking"
        para sempre, que é o sintoma reportado.

        A exceção é a instância que **nunca** foi vista trabalhando: ela
        ainda está em ``connecting``, que o laço acima já pula, e não tem
        relógio de silêncio — porque ainda não começou.

        **O "pronto" daqui é um palpite, e por isso é revogável.** Ele sai
        do silêncio, não do servidor, e é anotado em
        :attr:`Instance.demoted` para que :meth:`note_alive` o desfaça no
        primeiro evento que chegar. Sem essa marca o rebaixamento era
        permanente, porque :meth:`note_active` só promove a partir de
        ``connecting`` — e um único silêncio mal medido deixava o balão em
        "Ready" pelo resto do turno. Era a segunda metade do bug 22.
        """

        deadline = self._clock() - timeout

        for instance in self.instances.values():
            if self._wants(instance) or instance.state != STATE_WORKING:
                continue

            # `evented_at` em zero é "ninguém falou ainda", e não "falou há muito
            # tempo": antes do primeiro carimbo não há silêncio para medir.
            # `note_active` já escreve o relógio na promoção, então uma
            # instância em "working" sempre tem carimbo — a guarda é para o
            # caso de uma `Instance` montada à mão, que não passa por lá.
            if instance.evented_at and instance.evented_at < deadline:
                instance.state = STATE_IDLE

                instance.demoted = True

        self._refresh()

    def note_ask(self, session_id: str | None = None) -> None:
        """O stream viu um pedido.

        Não decide nada: acende a rede de segurança da degradação, anotando
        **de quem** foi o pedido. Quem confirma é a consulta ao servidor —
        e é ela que vale assim que o servidor responder.
        """

        if session_id is None:
            self._orphan_latch = True
        else:
            self._latched.add(session_id)

        self._refresh()

    def note_release(self, session_id: str | None = None) -> None:
        """O stream viu a resposta a um pedido."""

        self._release(session_id)

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

            # O servidor lista aqui as sessões "*running*" em primeiro
            # plano, então uma sessão que ainda não gerou evento não
            # está parada esperando nada: está trabalhando. Sem isso o
            # balão dela nasceria sem título (o estado ``connecting`` é
            # mudo por contrato).
            #
            # ``evented_at`` é inicializado **aqui**, no momento em que a
            # instância é declarada trabalhando, e não no primeiro evento:
            # uma sessão que trava antes de emitir qualquer coisa nunca
            # passaria por `note_event`, e sem este `evented_at` ela
            # ficaria em zero para sempre — o que `demote_stale` lê como
            # "nunca falou" e deixa em "Thinking" indefinidamente (bug 21).
            #
            # ``_clock()`` e não ``self._now`` pela mesma razão do bug 22:
            # este relógio mede silêncio **real**, e `_now` só anda quando a
            # consulta de status passa — usá-lo aqui dava ao silêncio uma
            # carimboada de até um ciclo de poll mais antigo.
            if instance.state == STATE_CONNECTING:
                instance.state = STATE_WORKING

                instance.evented_at = self._clock()

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
        self._release(None)

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
                or self._wants(instance)
                or instance.touched_at > deadline
            ),
            key=lambda i: (
                0 if self._wants(i) else 1 if i.state == STATE_WORKING else 2,
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

        if any(self._wants(i) for i in visible):
            return STATE_WAITING

        if any(i.state == STATE_WORKING for i in visible):
            return STATE_WORKING

        return STATE_IDLE

    def cards(self) -> list[tuple[str, str, bool]]:
        """Balões a desenhar: ``(estado, nome, espera_resposta)``.

        Vive aqui, e não na interface, porque é o quadro que sabe se uma
        instância está esperando: no caminho normal isso veio do servidor,
        e na degradação veio da trava do stream (ver :meth:`_wants`). A
        interface só desenha o que o quadro arbitrou — foi exatamente essa
        separação que faltava no bug 20, em que o sprite dizia "Waiting" e
        o balão dizia "Thinking" ao mesmo tempo.
        """

        result: list[tuple[str, str, bool]] = []

        for instance in self.visible():
            wants = self._wants(instance)

            state = STATE_WAITING if wants else instance.state

            result.append((state, instance.label, wants))

        return result

    # ------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------

    @staticmethod
    def _owned_by(instance: Instance, session_id: str | None,
                  directory: str | None) -> bool:
        """Este evento tem esta instância como dona?

        ``session_id`` manda quando vem: é o dono exato. Sem ele, o
        *location* é o melhor que o evento oferece — e as instâncias que
        o compartilham contam todas, porque um evento de arquivo não diz
        qual das abas do projeto escreveu o arquivo.
        """

        if session_id is not None:
            return instance.session_id == session_id

        if not directory:
            return False

        return instance.directory == directory

    def _prove_alive(self, instance: Instance, now: float) -> None:
        """A instância falou em ``now``; desfaz o rebaixamento por silêncio.

        A prova de vida é do servidor; o "pronto" que :meth:`demote_stale`
        deduz do silêncio é nosso. Quando as duas coisas discordam, quem
        sabe é o servidor.
        """

        instance.evented_at = now

        if instance.demoted:
            instance.demoted = False

            instance.state = STATE_WORKING

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

    def _release(self, session_id: str | None) -> None:
        """Solta a espera que o stream deixou ver.

        ``session_id`` é ``None`` para soltar todas — usado pela resposta do
        servidor (que é autoritativa), por um evento de resposta que não
        trouxe dono, e pelo ``STATE_IDLE`` global da reconexão. Com uma
        sessão, solta só a dela: o ``session.idle`` de outra aba não diz
        nada sobre a pergunta que continua aberta aqui.
        """

        if session_id is None:
            self._latched.clear()

            self._orphan_latch = False

            return

        self._latched.discard(session_id)

    def _wants(self, instance: Instance) -> bool:
        """Esta instância está esperando resposta do usuário?

        Duas fontes, e a ordem é o ponto. Primeiro o servidor: um pedido
        que ``GET /api/form`` ou ``/api/permission/request`` devolvem é
        fato, e vale sempre. Depois, e **só** quando o servidor não
        respondeu nunca (``pending_answered is None``), o que o stream
        disse: aí não há nada melhor, e um balão de "Thinking" com uma
        pergunta aberta na tela é o pior resultado possível.

        Fica aqui, e não em :attr:`Instance.wants_attention`, porque a
        segunda metade depende do quadro — é a degradação estar ligada ou
        não — e a instância não sabe disso.
        """

        if instance.needs_action:
            return True

        return self.pending_answered is None and (
            instance.session_id in self._latched
        )

    def _refresh(self) -> str | None:
        """Estado novo do pet, ou ``None`` se não mudou."""

        wanted = self._wanted()

        if wanted == self.state:
            return None

        self.state = wanted

        return wanted

    def _wanted(self) -> str:
        if any(self._wants(i) for i in self.instances.values()):
            return STATE_WAITING

        # Sem as rotas do v2, ou antes da primeira resposta, a trava do
        # stream assume: é imperfeita, mas melhor que um pet mudo. Um
        # pedido que não trouxe ``sessionID`` não tem balão próprio, então
        # ele só consegue mexer no sprite.
        if self.pending_answered is None and self._orphan_latch:
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

        #: Locations que o servidor respondeu que não existem mais.
        #: Vive até a lista de projetos ser relida — ver
        #: :meth:`StatusPoller._directories`.
        self._missing: set[str] = set()

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
        que já têm pedido aberto são consultadas. O ciclo lento
        (``fast=False``) varre todos os projetos, e é ele que pega um
        pedido que apareceu sem o stream contar.
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

            # ``missing`` é preenchido pela própria consulta: um
            # *location* que o servidor disse que não existe sai da
            # próxima varredura, porque voltar a perguntar a ele seriam dois
            # GETs por ciclo para sempre. A lista de projetos é relida no
            # TTL, então um diretório que reaparecer é pego de volta sem
            # reiniciar o pet.
            asks = pending_asks(port, password, directories,
                                missing=self._missing)

        except Exception as exc:  # rede, HTTP, location inválida
            # Falhou não é "não há pendência": manter o estado é a
            # escolha que não inventa resposta do usuário.
            log.debug("[pet] consulta de pendência falhou: %s", exc)

            self.answers.emit(None)

            return PENDING_ERROR_RETRY

        if asks is None:
            if self.supported:
                # Só chega aqui quando a **rota** não existe, e não quando
                # um *location* some: um 404 de diretório morto é isolado
                # em `pending_asks` e nunca chega como degradação.
                #
                # É ``warning`` e não ``info`` porque é um aviso de
                # ambiente de que vale a pena saber: ele diz ao usuário por
                # que o balão pode atrasar, e o modo solto descarta
                # ``INFO``. O aviso já estava no log desde o bug 18 e
                # ninguém o viu — ver o bug 20.
                log.warning(
                    "[pet] este servidor não tem as rotas de pendência "
                    "do v2 (404 com corpo vazio); 'aguardando' volta a vir "
                    "do stream, que é mais fraco",
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

            # A lista mudou, então o julgamento sobre quais projects
            # existem mudou também. ``/api/project`` guarda diretórios que
            # já não têm pasta, e um 404 desses custaria dois GETs a cada
            # ciclo para sempre se ninguém o anotasse.
            self._missing.clear()

        # Um *location* que o servidor disse que não existe não volta a ser
        # consultado até a lista de projetos ser relida — e ela é relida a
        # cada ``PROJECTS_TTL_SECONDS``, então um diretório que reaparecer
        # é pego de volta sem precisar de reiniciar o pet.
        live = [d for d in projects if d not in self._missing]

        if not live:
            # Todos os projetos guardados sumiram. Não é "não há nada
            # pendente" — é "não há o que perguntar", e a lista vazia que
            # `pending_asks` devolve nesse caso é a resposta certa.
            return []

        # No ciclo rápido, só as locations que já têm pedido aberto. O
        # balão é um só por instância, então consultar as outras não
        # mudaria nada na tela — e são dois GETs por projeto a cada 2s.
        # O que a varredura completa pega é o caminho inverso: um pedido
        # novo, que chega antes por ``poke()``, vinda do próprio stream.
        if fast and self._hot:
            hot = [d for d in self._hot if d in live]

            if hot:
                return hot

        return live
