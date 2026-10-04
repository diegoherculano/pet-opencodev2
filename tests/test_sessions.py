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


class DegradedTests(unittest.TestCase):
    """Servidor sem as rotas do v2: a trava do stream assume."""

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

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with self.patch_pending([ask()]):
                with self.patch_active([]):
                    with self.no_names():
                        seen: list[object] = []
                        poller.answers.connect(seen.append)

                        delay = poller._cycle()

        self.assertTrue(seen[0])
        self.assertEqual(delay, sessions.PENDING_POLL_SECONDS)

    def test_nothing_pending_means_a_slow_poll(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with self.patch_pending([]):
                with self.patch_active([]):
                    with self.no_names():
                        seen: list[object] = []
                        poller.answers.connect(seen.append)

                        delay = poller._cycle()

        self.assertEqual(seen[0], [])
        self.assertEqual(delay, sessions.PENDING_IDLE_POLL_SECONDS)

    def test_a_query_error_publishes_none(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with unittest.mock.patch.object(
                sessions, "pending_asks",
                unittest.mock.Mock(side_effect=OSError("boom")),
            ):
                with self.patch_active([]):
                    with self.no_names():
                        seen: list[object] = []
                        poller.answers.connect(seen.append)

                        delay = poller._cycle()

        self.assertEqual(seen, [None])
        self.assertEqual(delay, sessions.PENDING_ERROR_RETRY)

    def test_an_old_server_turns_the_feature_off_once(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with self.patch_pending(None):
                with self.patch_active([]):
                    with self.assertLogs("petwatch.sessions", level="INFO"):
                        poller._cycle()

        self.assertFalse(poller.supported)

    def test_the_active_list_is_published(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with self.patch_pending([]):
                with self.patch_active(["ses_a"]):
                    with self.no_names():
                        seen: list[object] = []
                        poller.active.connect(seen.append)

                        poller._cycle()

        self.assertEqual(seen[0], ["ses_a"])

    def test_a_failed_active_query_publishes_none(self):
        """Não pode virar lista vazia: apagaria os balões."""

        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/projetos/dd"):
            with self.patch_pending([]):
                with self.patch_active(None):
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

        with unittest.mock.patch.object(sessions, "session_info",
                                        lambda *a, **k: info):
            with unittest.mock.patch.object(sessions, "watched_directory",
                                            lambda: "/p"):
                with self.patch_pending([]):
                    with self.patch_active(["ses_a"]):
                        poller._cycle()
                        poller._cycle()

        self.assertEqual(len(named), 1)
        self.assertEqual(named[0][0], "ses_a")

    def test_a_session_without_info_is_not_retried_endlessly(self):
        poller = self.build()

        fetch = unittest.mock.Mock(return_value=None)

        with unittest.mock.patch.object(sessions, "session_info", fetch):
            with unittest.mock.patch.object(sessions, "watched_directory",
                                            lambda: "/p"):
                with self.patch_pending([]):
                    with self.patch_active(["ses_a"]):
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

        with unittest.mock.patch.object(sessions, "watched_directory",
                                        lambda: "/p"):
            with self.patch_pending([]):
                with self.patch_active([]):
                    poller._cycle()
                    poller._cycle()
                    poller._cycle()

        self.assertEqual(len(reads), 1)

    def test_the_project_list_is_cached(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory", lambda: None):
            with unittest.mock.patch.object(
                sessions, "watched_directories", return_value=["/projetos/dd"],
            ) as projects:
                with self.patch_pending([]):
                    with self.patch_active([]):
                        poller._cycle()
                        poller._cycle()

        self.assertEqual(projects.call_count, 1)

    def test_the_fast_cycle_only_asks_where_something_is_pending(self):
        """Dois GETs por projeto a cada 2s é custo demais numa lista grande."""

        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory", lambda: None):
            with unittest.mock.patch.object(
                sessions, "watched_directories",
                return_value=["/projetos/dd", "/projetos/outro"],
            ):
                with self.patch_pending([ask(directory="/projetos/dd")]) as query:
                    with self.patch_active([]):
                        with self.no_names():
                            poller._cycle(fast=True)
                            poller._cycle(fast=True)

        self.assertEqual(query.call_args.args[2], ["/projetos/dd"])

    def test_the_slow_cycle_sweeps_every_project(self):
        poller = self.build()

        with unittest.mock.patch.object(sessions, "watched_directory", lambda: None):
            with unittest.mock.patch.object(
                sessions, "watched_directories",
                return_value=["/projetos/dd", "/projetos/outro"],
            ):
                with self.patch_pending([ask(directory="/projetos/dd")]) as query:
                    with self.patch_active([]):
                        with self.no_names():
                            poller._cycle(fast=False)

        self.assertEqual(query.call_args.args[2],
                         ["/projetos/dd", "/projetos/outro"])


if __name__ == "__main__":
    unittest.main()