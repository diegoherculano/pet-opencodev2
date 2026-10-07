"""O quadro de instâncias: quem tem balão e o que ele diz.

Cobre :mod:`petwatch.sessions`:

- a :class:`SessionBoard`, onde o falso positivo do bug 18 morre — e
  onde "aguardando" passa a ser **de uma instância**, não do servidor
  inteiro;
- o :class:`StatusPoller`, o laço que pergunta ao servidor.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch import sessions  # noqa: E402
from petwatch.pending import PendingAsk  # noqa: E402
from petwatch.sessions import (  # noqa: E402
    MAX_INSTANCES,
    QUIET_GRACE_SECONDS,
    Instance,
    SessionBoard,
    StatusPoller,
    project_name,
    short_session_id,
)
from petwatch.states import (  # noqa: E402
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_WAITING,
    STATE_WORKING,
)

PORT = 4096


def ask(session_id="ses_a", directory="/p"):
    return PendingAsk("form", f"frm_{session_id}", session_id, directory, "Questions")


class LabelTests(unittest.TestCase):
    def test_the_title_comes_first(self):
        instance = Instance(session_id="ses_a", title="Corrigindo o bug",
                            directory="/home/diego/projetos/dd")

        self.assertEqual(instance.label, "Corrigindo o bug")

    def test_the_project_is_the_fallback(self):
        instance = Instance(session_id="ses_a",
                            directory="/home/diego/projetos/pet-opencodev2")

        self.assertEqual(instance.label, "pet-opencodev2")

    def test_the_session_id_is_the_last_resort(self):
        """Melhor "ses_1047" do que um balão anônimo."""

        instance = Instance(session_id="ses_10492f2a5001")

        self.assertEqual(instance.label, "5001")
        self.assertTrue(instance.label)

    def test_project_name_handles_edges(self):
        self.assertEqual(project_name("/a/b/c/"), "c")
        self.assertEqual(project_name("C:\\projetos\\dd"), "dd")
        self.assertIsNone(project_name(""))
        self.assertIsNone(project_name("/"))

    def test_short_id_keeps_short_ids_intact(self):
        self.assertEqual(short_session_id("ses_ab"), "ses_ab")


class ClockedBoard:
    """Quadro com relógio de mentira, que se comporta como o quadro.

    A folga de esquecimento é a única parte do quadro que depende de
    tempo; testá-la com ``time.monotonic`` exigiria dormir, e misturar os
    dois relógios quebraria a ordenação dos balões.

    O resto da API é a do quadro, sem proxy visível nos testes:
    ``__getattr__`` repassa para ele.
    """

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.board = SessionBoard(clock=lambda: self.now)

    def __getattr__(self, name):
        return getattr(self.board, name)

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ActiveTests(unittest.TestCase):
    """Quem tem balão é quem o servidor diz que está em ação."""

    def build(self) -> ClockedBoard:
        return ClockedBoard()

    def test_an_active_session_gets_a_bubble(self):
        board = self.build()

        board.note_active(["ses_a"])

        self.assertEqual([i.session_id for i in board.visible()], ["ses_a"])

    def test_an_active_session_with_no_event_still_reads_as_working(self):
        """O estado ``connecting`` é mudo; um balão sem título parece erro.

        ``/api/session/active`` lista sessões "*running*", então uma
        sessão que ainda não gerou evento está trabalhando — não parada.
        """

        board = self.build()

        board.note_active(["ses_a"])

        self.assertEqual(board.visible()[0].state, STATE_WORKING)

    def test_a_session_that_left_the_list_keeps_its_bubble_for_a_while(self):
        """A folga existe porque as respostas não chegam na mesma ordem.

        ``/api/session/active`` e o stream não são a mesma fonte: sem
        folga, o balão piscaria a cada tique da consulta, que é a cada 2s
        enquanto há espera.
        """

        board = self.build()

        board.note_active(["ses_a"])
        board.note_active([])

        self.assertEqual([i.session_id for i in board.visible()], ["ses_a"])

    def test_and_is_forgotten_after_the_grace(self):
        board = self.build()

        board.note_active(["ses_a"])
        board.note_active([])

        board.advance(QUIET_GRACE_SECONDS + 1)

        self.assertEqual(board.visible(), [])

    def test_a_failed_query_keeps_the_bubbles(self):
        """``None`` = não deu para saber. Esvaziar a lista aqui apagaria
        o pet inteiro por causa de um GET que deu timeout."""

        board = self.build()

        board.note_active(["ses_a"])
        board.note_active(None)

        self.assertEqual(len(board.visible()), 1)

    def test_several_sessions_get_several_bubbles(self):
        board = self.build()

        board.note_active(["ses_a", "ses_b", "ses_c"])

        self.assertEqual(len(board.visible()), 3)


class PendingTests(unittest.TestCase):
    """O "aguardando" é da instância que tem pedido aberto."""

    def build(self) -> ClockedBoard:
        return ClockedBoard(2000.0)

    def test_a_pending_ask_flags_that_session_only(self):
        board = self.build()

        board.note_active(["ses_a", "ses_b"])
        board.note_pending([ask(session_id="ses_b")])

        flagged = [i.session_id for i in board.visible() if i.wants_attention]

        self.assertEqual(flagged, ["ses_b"])

    def test_the_ask_says_the_project_when_the_stream_did_not(self):
        board = self.build()

        board.note_pending([ask(directory="/home/diego/projetos/dd")])

        self.assertEqual(
            board.instances["ses_a"].directory, "/home/diego/projetos/dd",
        )

    def test_a_session_with_a_pending_ask_gets_a_bubble_without_being_active(self):
        """O pedido é evidência de que a sessão existe e quer algo.

        Uma sessão que espera resposta pode não estar "drenando" — e é
        justamente ela que o usuário não pode deixar de ver.
        """

        board = self.build()

        board.note_active([])
        board.note_pending([ask(session_id="ses_a")])

        self.assertEqual([i.session_id for i in board.visible()], ["ses_a"])

    def test_answering_releases_only_that_bubble(self):
        board = self.build()

        board.note_pending([ask(session_id="ses_a"), ask(session_id="ses_b")])

        self.assertEqual(len([i for i in board.visible() if i.wants_attention]), 2)

        board.note_pending([ask(session_id="ses_b")])

        flagged = [i.session_id for i in board.visible() if i.wants_attention]

        self.assertEqual(flagged, ["ses_b"])

    def test_a_failed_query_keeps_the_flag(self):
        board = self.build()

        board.note_pending([ask(session_id="ses_a")])
        board.note_pending(None)

        self.assertTrue(board.instances["ses_a"].needs_action)
        self.assertEqual(board.state, STATE_WAITING)


class PerSessionStateTests(unittest.TestCase):
    """O estado vem do evento, filtrado pela sessão que o produziu."""

    def build(self) -> ClockedBoard:
        return ClockedBoard(3000.0)

    def test_each_session_keeps_its_own_state(self):
        board = self.build()

        board.note_active(["ses_a", "ses_b"])
        board.note_event("ses_a", STATE_WORKING)
        board.note_event("ses_b", STATE_IDLE)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states, {"ses_a": STATE_WORKING, "ses_b": STATE_IDLE})

    def test_an_event_without_a_session_creates_nothing(self):
        """``server.connected`` e ``project.updated`` são do servidor."""

        board = self.build()

        board.note_active(["ses_a"])
        board.note_event(None, STATE_WORKING)

        self.assertEqual(list(board.instances), ["ses_a"])

    def test_the_stream_still_feeds_the_general_state(self):
        board = self.build()

        board.note_event(None, STATE_WORKING)

        self.assertEqual(board.stream_state, STATE_WORKING)
        self.assertEqual(board.headline(), STATE_WORKING)

    def test_the_idle_event_clears_the_degraded_latch(self):
        board = self.build()

        board.note_ask()

        self.assertEqual(board.state, STATE_WAITING)

        board.note_event(None, STATE_IDLE)

        self.assertFalse(board.latched)
        self.assertNotEqual(board.state, STATE_WAITING)


class OrderingTests(unittest.TestCase):
    """A ordem é por urgência: um aviso não pode ficar atrás de outro."""

    def build(self) -> ClockedBoard:
        board = ClockedBoard(4000.0)
        board.note_active(["ses_a", "ses_b", "ses_c"])
        return board

    def test_the_one_waiting_comes_first(self):
        board = self.build()

        board.note_event("ses_a", STATE_WORKING)
        board.note_event("ses_b", STATE_WORKING)
        board.note_event("ses_c", STATE_IDLE)
        board.note_pending([ask(session_id="ses_c")])

        self.assertEqual(board.visible()[0].session_id, "ses_c")

    def test_then_the_working_ones(self):
        board = self.build()

        board.note_event("ses_b", STATE_IDLE)
        board.note_event("ses_a", STATE_WORKING)

        self.assertEqual(
            [i.session_id for i in board.visible()],
            ["ses_a", "ses_c", "ses_b"],
        )

    def test_the_grid_never_grows_past_the_cap(self):
        board = ClockedBoard(5000.0)
        board.note_active([f"ses_{index}" for index in range(MAX_INSTANCES + 5)])

        self.assertEqual(len(board.visible()), MAX_INSTANCES)

    def test_headline_is_waiting_if_any_instance_waits(self):
        board = self.build()

        board.note_event("ses_a", STATE_WORKING)
        board.note_pending([ask(session_id="ses_c")])

        self.assertEqual(board.state, STATE_WAITING)
        self.assertEqual(board.headline(), STATE_WAITING)

    def test_headline_is_working_when_nobody_waits(self):
        board = self.build()

        board.note_event("ses_a", STATE_WORKING)

        self.assertEqual(board.headline(), STATE_WORKING)

    def test_a_freshly_active_session_counts_as_working(self):
        """``/api/session/active`` lista sessões *running*."""

        board = self.build()

        self.assertEqual(board.headline(), STATE_WORKING)

    def test_headline_is_idle_when_everyone_is_idle(self):
        board = self.build()

        for session_id in ("ses_a", "ses_b", "ses_c"):
            board.note_event(session_id, STATE_IDLE)

        self.assertEqual(board.headline(), STATE_IDLE)

    def test_without_instances_the_headline_is_the_stream(self):
        board = SessionBoard()

        self.assertEqual(board.headline(), STATE_CONNECTING)


class DemoteStaleTests(unittest.TestCase):
    """O watchdog precisa valer também para o balão.

    Ele existe para o caso de o opencode **parar de mandar eventos**. Com o
    estado por sessão, sem isto o sprite voltaria para "Ready" e o balão
    continuaria dizendo "Thinking" — duas verdades na tela.
    """

    IDLE_TIMEOUT = 45.0

    def build(self) -> ClockedBoard:
        board = ClockedBoard()

        board.note_active(["ses_a", "ses_b"])
        board.note_event("ses_a", STATE_WORKING)
        board.note_event("ses_b", STATE_WORKING)

        return board

    def test_a_silent_instance_becomes_ready(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states, {"ses_a": STATE_IDLE, "ses_b": STATE_IDLE})
        self.assertEqual(board.state, STATE_IDLE)

    def test_an_instance_that_spoke_recently_keeps_working(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT - 5)
        board.note_event("ses_a", STATE_WORKING)

        board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states["ses_a"], STATE_WORKING)

    def test_waiting_for_an_answer_is_not_silence(self):
        """Uma instância esperando o usuário não "calou": ela espera."""

        board = self.build()

        board.note_pending([ask(session_id="ses_a")])

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        flagged = [i.session_id for i in board.visible() if i.wants_attention]

        self.assertEqual(flagged, ["ses_a"])
        self.assertEqual(board.state, STATE_WAITING)

    def test_demoting_nothing_is_harmless(self):
        board = self.build()

        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.state, STATE_WORKING)

    def test_a_session_the_server_still_lists_but_which_is_silent_goes_ready(self):
        """O bug 21: o servidor lista, o stream não fala, o balão trava.

        É o sintoma reportado — "Thinking" para sempre, e só uma nova
        mensagem do agente é que volta a mexer. O que reproduz é o
        ``/api/session/active`` devolvendo a sessão como ``running``
        enquanto nenhum evento chega, que é o que o quadro recebe a cada
        ciclo do ``StatusPoller``.
        """

        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)

        # A consulta de ativas continua de pé: a sessão não saiu da lista.
        board.note_active(["ses_a", "ses_b"])
        board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states["ses_a"], STATE_IDLE)

    def test_the_poll_alone_does_not_revive_a_silent_session(self):
        """``touched_at`` é reescrito pelo poll; ``evented_at`` não.

        A distinção é a correção: se ``demote_stale`` medisse ``touched_at``,
        o poll de 20s atrás seguraria a instância em "Working" para sempre.
        """

        board = self.build()

        for _ in range(10):
            board.advance(self.IDLE_TIMEOUT / 2)
            board.note_active(["ses_a", "ses_b"])

        board.demote_stale(self.IDLE_TIMEOUT)

        instance = board.instances["ses_a"]

        self.assertEqual(instance.state, STATE_IDLE)

    def test_a_brand_new_instance_gets_a_full_timeout_before_being_called_silent(self):
        """Uma instância recém-aparecida não é rebaixada no primeiro ciclo.

        Quem declara a sessão ``working`` é o próprio poll, e é aí que o
        relógio de silêncio nasce (``Instance.evented_at``): a sessão ainda
        não teve chance de falar, e ser rebaixada para "Ready" logo que
        nasce seria mentira tanto quanto ficar em "Thinking" para sempre.
        """

        board = ClockedBoard()

        board.note_active(["ses_nova"])

        instance = board.instances["ses_nova"]

        self.assertEqual(instance.state, STATE_WORKING)
        self.assertEqual(instance.evented_at, board.now)

        # Ainda dentro da janela: não pode ser rebaixada.
        board.advance(self.IDLE_TIMEOUT - 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.instances["ses_nova"].state, STATE_WORKING)

    def test_a_session_that_stalls_before_its_first_event_is_still_released(self):
        """A contraparte: quem nunca emitiu evento também trava.

        É o caso em que o servidor passa a listar a sessão como ``running``
        e nenhum evento chega nunca — nem o primeiro. O relógio nasce na
        promoção a "working", e não no primeiro evento, por isso ela
        também volta para "Ready" (bug 21).
        """

        board = ClockedBoard()

        board.note_active(["ses_nova"])

        board.advance(self.IDLE_TIMEOUT + 1)
        board.note_active(["ses_nova"])
        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.instances["ses_nova"].state, STATE_IDLE)

    def test_a_session_that_keeps_speaking_stays_working(self):
        """O contrário também vale: quem fala não é rebaixado."""

        board = self.build()

        for _ in range(10):
            board.advance(self.IDLE_TIMEOUT / 2)
            board.note_event("ses_a", STATE_WORKING)
            board.note_active(["ses_a", "ses_b"])

        board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states["ses_a"], STATE_WORKING)


class NoteAliveTests(unittest.TestCase):
    """Prova de vida do stream, sem estado — o bug 22.

    ``demote_stale`` decide que uma instância parou de trabalhar olhando
    quanto tempo faz que ela não fala. Antes, o que contava como "falar"
    era só o evento que virava estado **e** trazia ``sessionID``, e a
    medição no stream de verdade mostrou que isso é quase nada: de 819
    eventos num turno de 150s, 771 eram ``session.reasoning.delta``, que
    não vira estado porque é instante interno do turno. Os eventos de
    arquivo e shell nem trazem ``sessionID``.

    Resultado: o relógio de silêncio congelava durante o raciocínio
    inteiro e o balão virava "Ready" com o agente pensando.
    """

    IDLE_TIMEOUT = 45.0

    DIRECTORY = "/home/diego/projetos/dd"

    def build(self) -> ClockedBoard:
        board = ClockedBoard()

        board.note_active(["ses_a", "ses_b"])
        board.note_session_info("ses_a", {"location": {"directory": self.DIRECTORY}})
        board.note_session_info("ses_b", {"location": {"directory": self.DIRECTORY}})
        board.note_event("ses_a", STATE_WORKING, self.DIRECTORY)
        board.note_event("ses_b", STATE_WORKING, self.DIRECTORY)

        return board

    def test_a_delta_keeps_the_session_working(self):
        """``session.reasoning.delta`` não vira estado e mesmo assim é vida."""

        board = self.build()

        for _ in range(20):
            board.advance(self.IDLE_TIMEOUT / 2)
            board.note_alive("ses_a", None)
            board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states["ses_a"], STATE_WORKING)

    def test_the_stamp_is_the_real_clock_and_not_the_poll_clock(self):
        """O carimbo é o relógio real, não o do último ciclo de poll.

        ``self._now`` só anda quando ``/api/session/active`` passa. Usá-lo
        como carimbo não encurta o timeout, mas o torna uma **faixa**: o
        silêncio real que derruba o balão vai de 40s a 65s em vez dos 45s
        que o número significa. Medido varrendo a fase do último evento
        dentro do ciclo.

        O sintoma do bug 22 não vem daqui — os 39,06s que o projeto mediu
        continuam acima do pior caso. Vem da prova de vida ausente, que é o
        resto desta classe. Aqui o que se exige é que o carimbo seja o
        instante real do evento.
        """

        board = self.build()

        # O último evento foi no `build`; desde então o tempo passou e só o
        # relógio real andou — `_now` continua no último ciclo de poll.
        board.advance(10.0)

        self.assertNotEqual(board._now, board.now)

        board.note_alive("ses_a", None)

        self.assertEqual(board.instances["ses_a"].evented_at, board.now)

        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.instances["ses_a"].state, STATE_WORKING)

    def test_a_turn_that_really_ended_always_takes_the_full_timeout(self):
        """Com o relógio certo o "Ready" chega sempre depois de 45s.

        Antes, um turno que acabava logo depois de um tique do poll voltava
        para "Ready" em 40s, e um que acabava logo antes voltava em 65s. O
        timeout tinha de ser um número, e é o número que foi medido.
        """

        board = ClockedBoard()

        # O último evento de estado; depois, só o poll.
        board.note_active(["ses_a"])
        board.note_event("ses_a", STATE_WORKING, self.DIRECTORY)

        caiu_em = None

        for passo in range(1, 41):
            board.advance(2.0)
            board.note_active(["ses_a"])
            board.demote_stale(self.IDLE_TIMEOUT)

            if board.instances["ses_a"].state == STATE_IDLE:
                caiu_em = passo * 2.0
                break

        # Passou dos 45s de silêncio: tem de ter caído, e não antes.
        self.assertIsNotNone(caiu_em)
        self.assertGreaterEqual(caiu_em, self.IDLE_TIMEOUT)

    def test_an_event_without_a_session_is_attributed_by_location(self):
        """``shell.created`` e ``file.edited`` são do *location*, não da sessão."""

        board = self.build()

        for _ in range(20):
            board.advance(self.IDLE_TIMEOUT / 2)
            board.note_alive(None, self.DIRECTORY)
            board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states, {"ses_a": STATE_WORKING, "ses_b": STATE_WORKING})

    def test_liveness_does_not_change_state_on_its_own(self):
        """``note_alive`` move o relógio; quem decide estado é o stream."""

        board = self.build()

        board.note_event("ses_a", STATE_IDLE)
        board.note_alive("ses_a", self.DIRECTORY)

        self.assertEqual(board.instances["ses_a"].state, STATE_IDLE)
        self.assertFalse(board.instances["ses_a"].demoted)

    def test_an_orphan_event_moves_nothing(self):
        """Sem dono não há o que atribuir, e inventar dono seria pior."""

        board = self.build()

        board.advance(self.IDLE_TIMEOUT / 2)

        before = board.instances["ses_a"].evented_at

        board.note_alive(None, None)

        self.assertEqual(board.instances["ses_a"].evented_at, before)

    def test_liveness_of_another_project_does_not_reach_this_one(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT / 2)

        before = board.instances["ses_a"].evented_at

        board.note_alive(None, "/outro/projeto")

        self.assertEqual(board.instances["ses_a"].evented_at, before)

    def test_a_real_still_session_still_goes_ready(self):
        """A correção do bug 22 não pode desfazer a do bug 21."""

        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        states = {i.session_id: i.state for i in board.visible()}

        self.assertEqual(states, {"ses_a": STATE_IDLE, "ses_b": STATE_IDLE})


class DemotedIsRevocableTests(unittest.TestCase):
    """O "pronto" por silêncio é palpite nosso, então é revogável.

    Antes, um rebaixamento era permanente: ``note_active`` só promove a
    partir de ``connecting``, então um único silêncio mal medido deixava o
    balão em "Ready" pelo resto do turno, mesmo com o agente trabalhando.
    """

    IDLE_TIMEOUT = 45.0

    DIRECTORY = "/home/diego/projetos/dd"

    def build(self) -> ClockedBoard:
        board = ClockedBoard()

        board.note_active(["ses_a"])
        board.note_session_info("ses_a", {"location": {"directory": self.DIRECTORY}})
        board.note_event("ses_a", STATE_WORKING, self.DIRECTORY)

        return board

    def test_silence_marks_the_bubble_as_our_guess(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.instances["ses_a"].state, STATE_IDLE)
        self.assertTrue(board.instances["ses_a"].demoted)

    def test_the_first_event_afterwards_takes_it_back(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        self.assertEqual(board.headline(), STATE_IDLE)

        board.note_alive("ses_a", self.DIRECTORY)

        self.assertEqual(board.headline(), STATE_WORKING)
        self.assertFalse(board.instances["ses_a"].demoted)
        self.assertEqual([card[0] for card in board.cards()], [STATE_WORKING])

    def test_a_shell_event_also_takes_it_back(self):
        board = self.build()

        board.advance(self.IDLE_TIMEOUT + 1)
        board.demote_stale(self.IDLE_TIMEOUT)

        board.note_alive(None, self.DIRECTORY)

        self.assertEqual(board.headline(), STATE_WORKING)

    def test_an_idle_the_server_reported_is_not_ours_to_revoke(self):
        """``session.idle`` é o servidor falando; um delta não o contradiz."""

        board = self.build()

        board.note_event("ses_a", STATE_IDLE)

        self.assertEqual(board.instances["ses_a"].state, STATE_IDLE)
        self.assertFalse(board.instances["ses_a"].demoted)

        board.note_alive("ses_a", self.DIRECTORY)

        self.assertEqual(board.instances["ses_a"].state, STATE_IDLE)

    def test_a_repeated_demotion_keeps_being_revocable(self):
        """O defeito era cumulativo: cada silêncioava deixava o balão para trás."""

        board = self.build()

        for _ in range(6):
            board.advance(self.IDLE_TIMEOUT + 1)
            board.demote_stale(self.IDLE_TIMEOUT)

            self.assertEqual(board.headline(), STATE_IDLE)

            board.advance(1)
            board.note_alive("ses_a", self.DIRECTORY)

            self.assertEqual(board.headline(), STATE_WORKING)


class NeverSpokeTests(unittest.TestCase):
    """``evented_at`` em zero é "ninguém falou", não "falou há muito tempo"."""

    def test_an_instance_without_a_stamp_is_not_released(self):
        board = ClockedBoard()

        instance = Instance(session_id="ses_a", state=STATE_WORKING)

        board.instances["ses_a"] = instance

        board.demote_stale(45.0)

        self.assertEqual(instance.state, STATE_WORKING)

    def test_the_stamp_is_written_the_moment_it_is_promoted(self):
        """Sem isto a sessão que trava antes do primeiro evento nunca sai.

        É a segunda metade do bug 21, e o motivo de o carimbo nascer em
        ``note_active`` em vez de esperar o primeiro evento.
        """

        board = ClockedBoard()

        board.note_active(["ses_nova"])

        self.assertEqual(board.instances["ses_nova"].evented_at, board.now)

        board.advance(45.0 - 1)
        board.demote_stale(45.0)

        self.assertEqual(board.instances["ses_nova"].state, STATE_WORKING)


class DegradedTests(unittest.TestCase):
    """Servidor sem as rotas do v2: a trava do stream assume.

    A trava é **por sessão**. Ela já foi um booleano global, e aí o
    ``session.idle`` de uma aba derrubava a espera de outra que continuava
    com a pergunta aberta — o bug 15, que voltou inteiro quando a
    degradação passou a ser disparada por engano (bug 20).
    """

    def build(self):
        board = SessionBoard()

        board.note_active(["ses_a", "ses_b"])
        board.note_event("ses_a", STATE_WORKING)

        return board

    def test_the_latch_shows_waiting_when_nobody_answered(self):
        board = SessionBoard()

        board.note_ask()

        self.assertEqual(board.state, STATE_WAITING)

    def test_once_the_server_answers_the_latch_is_ignored(self):
        board = SessionBoard()

        board.note_ask()
        board.note_pending([])

        self.assertFalse(board.latched)
        self.assertNotEqual(board.state, STATE_WAITING)

    def test_another_tab_going_idle_does_not_release_it(self):
        """O defeito reportado: "Thinking" com a pergunta aberta.

        Uma aba termina o turno dela enquanto a outra tem um formulário
        esperando. O fim de turno é da aba que terminou — usá-lo para soltar
        a espera da outra é o que fazia o balão voltar para "Thinking".
        """

        board = self.build()

        board.note_ask("ses_a")

        self.assertEqual(board.state, STATE_WAITING)

        board.note_event("ses_b", STATE_IDLE)

        self.assertEqual(board.state, STATE_WAITING)
        self.assertEqual(board.cards()[0][0], STATE_WAITING)

    def test_another_tab_working_does_not_release_it(self):
        board = self.build()

        board.note_ask("ses_a")
        board.note_event("ses_b", STATE_WORKING)

        self.assertEqual(board.state, STATE_WAITING)

    def test_the_own_tab_going_idle_releases_it(self):
        board = self.build()

        board.note_ask("ses_a")
        board.note_event("ses_a", STATE_IDLE)

        self.assertFalse(board.latched)
        self.assertNotEqual(board.state, STATE_WAITING)

    def test_the_reply_releases_only_its_own_latch(self):
        board = self.build()

        board.note_ask("ses_a")
        board.note_ask("ses_b")
        board.note_release("ses_a")

        self.assertTrue(board.latched)
        self.assertEqual(board.state, STATE_WAITING)

    def test_a_reconnection_releases_everything(self):
        """O que passou na queda ninguém sabe, e o stream é volátil."""

        board = self.build()

        board.note_ask("ses_a")
        board.note_ask("ses_b")
        board.note_global_state(STATE_IDLE)

        self.assertFalse(board.latched)

    def test_an_ask_without_a_session_still_latches(self):
        """Falta o dono, não a evidência: o sprite tem de avisar."""

        board = self.build()

        board.note_ask(None)

        self.assertEqual(board.state, STATE_WAITING)

    def test_an_orphan_latch_has_no_bubble_of_its_own(self):
        """Sem sessão não há balão a que atribuir — só o sprite muda."""

        board = self.build()

        board.note_ask(None)

        self.assertEqual(board.state, STATE_WAITING)
        self.assertNotIn(STATE_WAITING, [card[0] for card in board.cards()])

    def test_a_pending_form_alone_is_not_a_bubble(self):
        """O pedido cria balão, e o nome vem depois.

        Um pedido de uma sessão que ainda não tem nome é a única evidência
        de que ela existe, então o balão nasce — mas sem título, e o
        ``short_session_id`` cobre isso.
        """

        board = self.build()

        board.note_pending([ask(session_id="ses_nova")])

        self.assertIn("ses_nova", board.instances)
        self.assertTrue(board.cards())

    def test_the_server_wins_over_the_latch(self):
        """Respondendo o servidor, o stream só antecipa.

        E o contrário também vale, e é o que segura o bug 18: um
        ``form.created`` de outra aba — ou de outra sessão do mesmo
        projeto — não pode virar um "aguardando" quando o servidor já
        disse que não há nada esperando.
        """

        board = self.build()

        board.note_ask("ses_a")
        board.note_pending([])

        self.assertNotEqual(board.state, STATE_WAITING)

    def test_a_working_server_is_never_overruled_by_the_stream(self):
        board = self.build()

        board.note_pending([])

        self.assertTrue(board.pending_answered)

        board.note_ask("ses_a")

        self.assertNotEqual(board.state, STATE_WAITING)
        self.assertNotIn(STATE_WAITING, [card[0] for card in board.cards()])

    def test_the_latch_survives_only_while_the_server_is_silent(self):
        """``pending_answered`` é o que separa "não sei" de "sei que não".

        Um GET que deu timeout devolve ``None``, que é justamente o estado
        em que a trava pode valer: ainda não houve resposta do servidor.
        """

        board = self.build()

        board.note_ask("ses_a")
        board.note_pending(None)

        self.assertEqual(board.state, STATE_WAITING)

    def test_a_waiting_instance_is_never_demoted_by_the_watchdog(self):
        """Esperar o usuário pode levar minutos, e silêncio não é o fim."""

        board = self.build()

        board.note_ask("ses_a")

        board.demote_stale(0.0)

        self.assertEqual(board.state, STATE_WAITING)
        self.assertEqual(board.instances["ses_a"].state, STATE_WORKING)

    def test_the_bubble_says_waiting_and_not_thinking(self):
        """O sprite e o balão não podem discordar.

        Era isso que o usuário via: o sprite mudava, o texto do balão não.
        """

        board = self.build()

        board.note_ask("ses_a")

        waiting = [card for card in board.cards() if card[0] == STATE_WAITING]

        self.assertEqual(len(waiting), 1)
        self.assertTrue(waiting[0][2])


class Bug20DeadLocationTests(unittest.TestCase):
    """O bug 20 no laço de consulta.

    Um *location* que o servidor disse que não existe não pode voltar a
    ser consultado a cada ciclo: são dois GETs por ciclo para sempre, e um
    projeto morto é um estado permanente, não uma notícia.
    """

    def build(self, port=PORT):
        poller = StatusPoller(lambda: port, lambda: "senha")
        self.addCleanup(poller.stop)
        return poller

    def test_a_missing_location_is_asked_once(self):
        poller = self.build()

        calls: list[str] = []

        def query(_port, _password, directories, **kwargs):
            calls.append(tuple(directories))
            kwargs.get("missing", set()).add("/projetos/morto")
            return []

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions,
                "watched_directories",
                return_value=["/projetos/morto", "/projetos/dd"],
            ),
            unittest.mock.patch.object(
                sessions, "active_sessions", lambda *a, **k: []
            ),
            unittest.mock.patch.object(sessions, "pending_asks", query),
        ):
            poller._cycle(fast=False)
            poller._cycle(fast=False)

        self.assertEqual(calls[0], ("/projetos/morto", "/projetos/dd"))
        self.assertEqual(calls[1], ("/projetos/dd",))

    def test_rereading_the_project_list_retries_it(self):
        """A pasta pode voltar, e o pet não pode precisar de reinício."""

        poller = self.build()

        projects = ["/projetos/morto"]

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions,
                "watched_directories",
                side_effect=lambda *a, **k: list(projects),
            ),
        ):
            # Primeira leitura: a lista entra no cache.
            self.assertEqual(
                poller._directories(PORT, "s", fast=False), projects
            )

            poller._missing.add("/projetos/morto")

            # Com a lista em cache, o julgamento anterior vale.
            self.assertEqual(poller._directories(PORT, "s", fast=False), [])

            # Passado o TTL, a lista é relida e o julgamento refeito.
            poller._projects_read_at = 0.0

            queried = poller._directories(PORT, "s", fast=False)

        self.assertEqual(queried, ["/projetos/morto"])
        self.assertEqual(poller._missing, set())

    def test_only_dead_projects_left_is_not_a_failure(self):
        """Não sobrou ninguém: a lista vazia é a resposta, não um erro."""

        poller = self.build()

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions,
                "watched_directories",
                return_value=["/projetos/morto"],
            ),
            unittest.mock.patch.object(
                sessions, "active_sessions", lambda *a, **k: []
            ),
            unittest.mock.patch.object(
                sessions, "pending_asks", lambda *a, **k: [],
            ),
        ):
            delay = poller._cycle(fast=False)

        self.assertEqual(delay, sessions.PENDING_IDLE_POLL_SECONDS)

    def test_the_watched_directory_is_never_filtered(self):
        """``PETWATCH_DIRECTORY`` é uma escolha explícita do usuário.

        Se aquele diretório não existe para o servidor, não é para o pet
        decidir o contrário: a variável existe para observar um lugar só, e
        filtrá-la por baixo dos panos seria o pet discordando do usuário.
        """

        poller = self.build()

        poller._missing.add("/projetos/dd")

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            self.assertEqual(poller._directories(PORT, "s", fast=False),
                             ["/projetos/dd"])


class PollerTests(unittest.TestCase):
    """O laço de consulta, sem rede."""

    def build(self, port=PORT):
        poller = StatusPoller(lambda: port, lambda: "senha")
        self.addCleanup(poller.stop)
        return poller

    def patch_pending(self, result):
        """``Mock`` de propósito: os testes inspecionam os argumentos."""

        return unittest.mock.patch.object(
            sessions, "pending_asks",
            unittest.mock.Mock(return_value=result),
        )

    def patch_active(self, result):
        return unittest.mock.patch.object(sessions, "active_sessions",
                                          lambda *a, **k: result)

    def no_names(self):
        """Sem ``GET /api/session/{id}``: nenhum teste toca na rede."""

        return unittest.mock.patch.object(sessions, "session_info",
                                         lambda *a, **k: None)

    def test_a_poke_wakes_the_loop(self):
        poller = self.build()

        poller.poke()

        self.assertTrue(poller._woken.is_set())

    def test_without_a_port_it_does_not_invent_anything(self):
        poller = self.build(port=None)

        seen: list[object] = []
        poller.answers.connect(seen.append)

        self.assertEqual(poller._cycle(), sessions.PENDING_ERROR_RETRY)
        self.assertEqual(seen, [None])

    def test_pending_asks_mean_a_fast_poll(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending([ask()]),
            self.patch_active([]),
            self.no_names(),
        ):
            seen: list[object] = []
            poller.answers.connect(seen.append)

            delay = poller._cycle()

        self.assertTrue(seen[0])
        self.assertEqual(delay, sessions.PENDING_POLL_SECONDS)

    def test_nothing_pending_means_a_slow_poll(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending([]),
            self.patch_active([]),
            self.no_names(),
        ):
            seen: list[object] = []
            poller.answers.connect(seen.append)

            delay = poller._cycle()

        self.assertEqual(seen[0], [])
        self.assertEqual(delay, sessions.PENDING_IDLE_POLL_SECONDS)

    def test_a_query_error_publishes_none(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            unittest.mock.patch.object(
                sessions,
                "pending_asks",
                unittest.mock.Mock(side_effect=OSError("boom")),
            ),
            self.patch_active([]),
            self.no_names(),
        ):
            seen: list[object] = []
            poller.answers.connect(seen.append)

            delay = poller._cycle()

        self.assertEqual(seen, [None])
        self.assertEqual(delay, sessions.PENDING_ERROR_RETRY)

    def test_an_old_server_turns_the_feature_off_once(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending(None),
            self.patch_active([]),
            self.assertLogs("petwatch.sessions", level="WARNING"),
        ):
            poller._cycle()

        self.assertFalse(poller.supported)

    def test_the_degradation_is_a_warning(self):
        """A degradação é um aviso de ambiente, e aviso vai para o log.

        A mensagem é o que diz ao usuário que o balão pode atrasar, e o
        nível é o que a torna visível sem ``--foreground``. O sintoma do
        bug 20 foi ter esse aviso no log e ninguém ver: ele estava em
        ``INFO``, que o modo solto descarta.
        """

        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending(None),
            self.patch_active([]),
            self.assertLogs("petwatch.sessions") as caught,
        ):
            poller._cycle()

        self.assertTrue(any(r.levelname == "WARNING" for r in caught.records))

    def test_the_degradation_is_logged_only_once(self):
        """Um aviso por ciclo seria o mesmo defeito com outra roupa."""

        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending(None),
            self.patch_active([]),
            self.assertLogs("petwatch.sessions") as caught,
        ):
            poller._cycle()
            poller._cycle()

        warnings = [r for r in caught.records if r.levelname == "WARNING"]

        self.assertEqual(len(warnings), 1)

    def test_the_active_list_is_published(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending([]),
            self.patch_active(["ses_a"]),
            self.no_names(),
        ):
            seen: list[object] = []
            poller.active.connect(seen.append)

            poller._cycle()

        self.assertEqual(seen[0], ["ses_a"])

    def test_a_failed_active_query_publishes_none(self):
        """Não pode virar lista vazia: apagaria os balões."""

        poller = self.build()

        with (
            unittest.mock.patch.object(
                sessions, "watched_directory", lambda: "/projetos/dd"
            ),
            self.patch_pending([]),
            self.patch_active(None),
        ):
            seen: list[object] = []
            poller.active.connect(seen.append)

            poller._cycle()

        self.assertEqual(seen, [None])

    def test_a_new_session_is_named_once(self):
        """``GET /api/session/{id}`` por sessão, não por ciclo."""

        poller = self.build()

        info = {"title": "Corrigindo o bug", "location": {"directory": "/p"}}

        named: list[object] = []
        poller.named.connect(named.append)

        with (
            unittest.mock.patch.object(
                sessions, "session_info", lambda *a, **k: info
            ),
            unittest.mock.patch.object(sessions, "watched_directory", lambda: "/p"),
            self.patch_pending([]),
            self.patch_active(["ses_a"]),
        ):
            poller._cycle()
            poller._cycle()

        self.assertEqual(len(named), 1)
        self.assertEqual(named[0][0], "ses_a")

    def test_a_session_without_info_is_not_retried_endlessly(self):
        poller = self.build()

        fetch = unittest.mock.Mock(return_value=None)

        with (
            unittest.mock.patch.object(sessions, "session_info", fetch),
            unittest.mock.patch.object(sessions, "watched_directory", lambda: "/p"),
            self.patch_pending([]),
            self.patch_active(["ses_a"]),
        ):
            poller._cycle()
            poller._cycle()

        # Sem nome não há o que buscar de novo, mas também não é para
        # insistir a cada 2s num GET que não volta.
        self.assertLessEqual(fetch.call_count, 2)

    def test_the_password_is_read_once(self):
        """Um ``subprocess`` a cada ciclo seria um processo a cada 2s."""

        reads: list[int] = []

        def password():
            reads.append(1)
            return "senha"

        poller = StatusPoller(lambda: PORT, password)

        self.addCleanup(poller.stop)

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: "/p"),
            self.patch_pending([]),
            self.patch_active([]),
        ):
            poller._cycle()
            poller._cycle()
            poller._cycle()

        self.assertEqual(len(reads), 1)

    def test_the_project_list_is_cached(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions, "watched_directories", return_value=["/projetos/dd"],
            ) as projects,
            self.patch_pending([]),
            self.patch_active([]),
        ):
            poller._cycle()
            poller._cycle()

        self.assertEqual(projects.call_count, 1)

    def test_the_fast_cycle_only_asks_where_something_is_pending(self):
        """Dois GETs por projeto a cada 2s é custo demais numa lista grande."""

        poller = self.build()

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions,
                "watched_directories",
                return_value=["/projetos/dd", "/projetos/outro"],
            ),
            self.patch_pending([ask(directory="/projetos/dd")]) as query,
            self.patch_active([]),
            self.no_names(),
        ):
            poller._cycle(fast=True)
            poller._cycle(fast=True)

        self.assertEqual(query.call_args.args[2], ["/projetos/dd"])

    def test_the_slow_cycle_sweeps_every_project(self):
        poller = self.build()

        with (
            unittest.mock.patch.object(sessions, "watched_directory", lambda: None),
            unittest.mock.patch.object(
                sessions,
                "watched_directories",
                return_value=["/projetos/dd", "/projetos/outro"],
            ),
            self.patch_pending([ask(directory="/projetos/dd")]) as query,
            self.patch_active([]),
            self.no_names(),
        ):
            poller._cycle(fast=False)

        self.assertEqual(query.call_args.args[2],
                         ["/projetos/dd", "/projetos/outro"])


if __name__ == "__main__":
    unittest.main()
