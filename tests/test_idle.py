"""Watchdog que devolve o pet para "pronto" quando o opencode silencia."""

from __future__ import annotations

import os
import time
import unittest
import unittest.mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import IDLE_POLL_MS, IDLE_TIMEOUT  # noqa: E402
from petwatch.idle import IdleWatchdog  # noqa: E402
from petwatch.states import (  # noqa: E402
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_WAITING,
    STATE_WORKING,
)


class DefaultsTests(unittest.TestCase):
    def test_default_timeout(self):
        watchdog = IdleWatchdog()
        watchdog.timer.stop()

        self.assertEqual(watchdog.timeout, IDLE_TIMEOUT)
        self.assertGreater(IDLE_TIMEOUT, 0)

    def test_starts_idle(self):
        watchdog = IdleWatchdog()
        watchdog.timer.stop()

        self.assertEqual(watchdog.state, STATE_IDLE)


class DueTests(unittest.TestCase):
    """A logica pura, sem depender do relogio do event loop."""

    def build(self, timeout: float = 5.0) -> IdleWatchdog:
        watchdog = IdleWatchdog(timeout)
        watchdog.timer.stop()
        return watchdog

    def test_not_due_when_idle(self):
        watchdog = self.build()
        watchdog.state = STATE_IDLE
        watchdog._last_activity = time.monotonic() - 999

        self.assertFalse(watchdog.is_due())

    def test_not_due_while_waiting_for_permission(self):
        """Esperando o usuario pode levar minutos: nao pode virar 'pronto'."""

        watchdog = self.build()
        watchdog.state = STATE_WAITING
        watchdog._last_activity = time.monotonic() - 999

        self.assertFalse(watchdog.is_due())

    def test_not_due_while_connecting(self):
        watchdog = self.build()
        watchdog.state = STATE_CONNECTING
        watchdog._last_activity = time.monotonic() - 999

        self.assertFalse(watchdog.is_due())

    def test_due_after_the_timeout_while_working(self):
        watchdog = self.build(timeout=5.0)
        watchdog.state = STATE_WORKING
        watchdog._last_activity = time.monotonic() - 10

        self.assertTrue(watchdog.is_due())

    def test_not_due_before_the_timeout(self):
        watchdog = self.build(timeout=5.0)
        watchdog.state = STATE_WORKING
        watchdog._last_activity = time.monotonic()

        self.assertFalse(watchdog.is_due())

    def test_activity_postpones_the_deadline(self):
        watchdog = self.build(timeout=5.0)
        watchdog.state = STATE_WORKING
        watchdog._last_activity = time.monotonic() - 10
        self.assertTrue(watchdog.is_due())

        watchdog.note_activity()

        self.assertFalse(watchdog.is_due())

    def test_note_state_resets_the_clock(self):
        watchdog = self.build(timeout=5.0)
        watchdog.state = STATE_WORKING
        watchdog._last_activity = time.monotonic() - 10

        watchdog.note_state(STATE_WORKING)

        self.assertFalse(watchdog.is_due())

    def test_is_due_accepts_an_injected_clock(self):
        watchdog = self.build(timeout=5.0)
        watchdog.state = STATE_WORKING
        watchdog._last_activity = 100.0

        self.assertFalse(watchdog.is_due(now=104.0))
        self.assertTrue(watchdog.is_due(now=105.0))
        self.assertTrue(watchdog.is_due(now=200.0))


class EmissionTests(unittest.TestCase):
    def test_emits_idle_once_when_the_silence_is_enough(self):
        watchdog = IdleWatchdog(timeout=0.05)
        watchdog.timer.stop()

        seen = []
        watchdog.idle_reached.connect(seen.append)

        watchdog.note_state(STATE_WORKING)

        # Três conferências: só a primeira, após o silêncio, dispara.
        watchdog._check()
        time.sleep(0.08)
        watchdog._check()
        watchdog._check()

        self.assertEqual(seen, [STATE_IDLE])

    def test_does_not_emit_while_waiting(self):
        watchdog = IdleWatchdog(timeout=0.05)
        watchdog.timer.stop()

        seen = []
        watchdog.idle_reached.connect(seen.append)

        watchdog.note_state(STATE_WAITING)
        watchdog._check()

        self.assertEqual(seen, [])

    def test_emits_again_after_a_new_burst_of_work(self):
        watchdog = IdleWatchdog(timeout=0.05)
        watchdog.timer.stop()

        seen = []
        watchdog.idle_reached.connect(seen.append)

        for _ in range(2):
            watchdog.note_state(STATE_WORKING)
            time.sleep(0.08)
            watchdog._check()

        self.assertEqual(seen, [STATE_IDLE, STATE_IDLE])

    def test_state_is_reset_to_idle_after_firing(self):
        watchdog = IdleWatchdog(timeout=0.0)
        watchdog.timer.stop()

        watchdog.note_state(STATE_WORKING)
        watchdog._check()

        self.assertEqual(watchdog.state, STATE_IDLE)


class TimerTests(unittest.TestCase):
    def test_timer_polls(self):
        watchdog = IdleWatchdog()

        try:
            self.assertTrue(watchdog.timer.isActive())
            self.assertEqual(watchdog.timer.interval(), IDLE_POLL_MS)
        finally:
            watchdog.timer.stop()


class WiringTests(unittest.TestCase):
    """O monitor tem de emitir atividade alem do estado."""

    def test_monitor_emits_activity_for_every_event(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        activity = []
        states = []

        monitor.activity.connect(lambda: activity.append(1))
        monitor.state_changed.connect(states.append)

        # Evento que muda o estado.
        monitor.process_event(None, {"type": "session.step.started"})
        # Evento que nao muda nada, mas prova que o opencode esta vivo.
        monitor.process_event(None, {"type": "session.text.delta"})

        self.assertEqual(len(activity), 2)
        self.assertEqual(states, [STATE_WORKING])

    def test_activity_covers_unmapped_events(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        activity = []
        monitor.activity.connect(lambda: activity.append(1))

        monitor.process_event(None, {"type": "server.connected"})

        self.assertEqual(len(activity), 1)


class SessionAliveTests(unittest.TestCase):
    """Todo evento publica prova de vida, com o dono que ele tiver.

    O bug 22: publicar só o que vira estado media o trabalho errado. Num
    turno real de 150s vieram 819 eventos e 771 eram
    ``session.reasoning.delta``, que não vira estado porque é instante
    interno do turno. O relógio de silêncio da instância ficava congelado
    durante o raciocínio inteiro, e o balão caía para "Ready" com o agente
    pensando.
    """

    def build(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        seen = []

        def registrar(session_id, directory):
            seen.append((session_id, directory))

        monitor.session_alive.connect(registrar)

        return monitor, seen

    def test_an_unmapped_event_still_counts_as_liveness(self):
        monitor, seen = self.build()

        monitor.process_event(None, {
            "type": "session.reasoning.delta",
            "data": {"sessionID": "ses_a", "delta": " hmm"},
        })

        self.assertEqual(seen, [("ses_a", None)])

    def test_the_reported_stream_dominates_and_is_all_unmapped(self):
        """A medição que motivou a mudança, virada em teste.

        Num turno real o stream é quase todo ``session.reasoning.delta``.
        Se algum deles virar estado, o ``state_from_event`` mudou e o
        problema volta a aparecer de outro jeito.
        """

        monitor, seen = self.build()

        monitor.process_event(None, {"type": "session.reasoning.delta",
                                     "data": {"sessionID": "ses_a", "delta": "x"}})

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], "ses_a")

    def test_an_event_without_a_session_carries_the_location(self):
        """``shell.created`` não tem ``sessionID``, mas tem ``location``."""

        monitor, seen = self.build()

        monitor.process_event(None, {
            "type": "shell.created",
            "location": {"directory": "/projetos/dd"},
            "data": {"info": {"id": "sh_1", "command": "npm test"}},
        })

        self.assertEqual(seen, [(None, "/projetos/dd")])

    def test_a_global_event_has_no_owner_at_all(self):
        monitor, seen = self.build()

        monitor.process_event(None, {"type": "server.connected", "data": {}})

        self.assertEqual(seen, [(None, None)])

    def test_liveness_is_emitted_before_the_state_rules(self):
        """A prova de vida não pode depender da tradução do evento.

        Se dependesse, um evento que nenhuma regra conhece seria
        descartado — que é exatamente o defeito.
        """

        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        order = []
        monitor.session_alive.connect(lambda *_: order.append("alive"))
        monitor.state_changed.connect(lambda _s: order.append("state"))

        monitor.process_event(None, {"type": "session.step.started",
                                     "data": {"sessionID": "ses_a"}})

        self.assertEqual(order, ["alive", "state"])


class EmitStateTests(unittest.TestCase):
    """``state_changed`` só fala quando o estado muda."""

    def build(self):
        from petwatch.monitor import OpenCodeMonitor

        return OpenCodeMonitor()

    def test_repeated_state_is_not_re_emitted(self):
        monitor = self.build()

        seen = []
        monitor.state_changed.connect(seen.append)

        for _ in range(20):
            monitor.process_event(None, {"type": "session.step.started"})

        self.assertEqual(seen, [STATE_WORKING])

    def test_repeat_still_counts_as_activity(self):
        monitor = self.build()

        activity = []
        monitor.activity.connect(lambda: activity.append(1))

        for _ in range(20):
            monitor.process_event(None, {"type": "session.step.started"})

        self.assertEqual(len(activity), 20)

    def test_change_is_emitted(self):
        monitor = self.build()

        seen = []
        monitor.state_changed.connect(seen.append)

        monitor.emit_state(STATE_WORKING)
        monitor.emit_state(STATE_WAITING)
        monitor.emit_state(STATE_IDLE)

        self.assertEqual(seen, [STATE_WORKING, STATE_WAITING, STATE_IDLE])


class SessionTests(unittest.TestCase):
    """A conexão inteira, com o socket trocado por uma leitura falsa.

    Este é o teste que pegou um ``NameError`` que a suíte inteira não
    viu: ``_session`` referencia ``STATE_IDLE``, o import ficou para
    trás numa edição, e a thread do monitor morreu em ``NameError`` a
    cada 2s — o pet ficava preso em ``connecting``, que é o estado
    **mudo**, e o balão sumia. Nenhum teste que publica evento pelo
    ``process_event`` enxergaria isso: eles nem passam por ``_session``.
    """

    def build(self, lines):
        """Monitor com stream falso que devolve ``lines`` e fecha."""

        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        class FakeConnection:
            def close(self):
                pass

            def interrupt(self):
                pass

        def fake_open(_port, _password):
            return FakeConnection()

        def fake_lines(_connection, stop_event):
            return iter(lines)

        monitor_module = __import__("petwatch.monitor", fromlist=["monitor"])

        self._patches = [
            unittest.mock.patch.object(monitor_module, "get_opencode_password",
                                       lambda: "senha"),
            unittest.mock.patch.object(monitor_module, "find_opencode_server",
                                       lambda _password: 4096),
            unittest.mock.patch.object(monitor_module, "open_event_stream",
                                       fake_open),
            unittest.mock.patch.object(monitor_module, "read_lines",
                                       fake_lines),
        ]

        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.addCleanup(monitor.stop)

        return monitor

    def test_connecting_publishes_ready_and_reads_the_stream(self):
        monitor = self.build([
            b'data: {"type":"session.step.started"}\n',
            b"\n",
        ])

        states = []
        monitor.state_changed.connect(states.append)

        monitor._session()

        self.assertEqual(states, [STATE_IDLE, STATE_WORKING])
        self.assertEqual(monitor.port, 4096)

    def test_reconnect_asks_about_pending_work(self):
        """O que houve na queda é desconhecido: o pet pergunta."""

        monitor = self.build([])

        reconnects = []
        monitor.reconnected.connect(lambda: reconnects.append(1))

        monitor._session()

        self.assertEqual(len(reconnects), 1)


class PendingQuestionTests(unittest.TestCase):
    """O monitor não decide "aguardando"; ele só avisa que mudou.

    Antes these tests existiam porque o monitor publicava "Waiting"
    direto do stream, e a trava do ``StateTranslator`` era a defesa
    contra os eventos das outras abas. Hoje o stream é global
    (documentado: *"across all server locations"*) e volátil
    (*"events during disconnection are missed"*), então essa defesa
    virava a própria causa do falso positivo: uma resposta perdida
    deixava o balão travado para sempre.

    O que o monitor faz agora é emitir :attr:`OpenCodeMonitor.ask_seen` e
    :attr:`OpenCodeMonitor.released`, que acordam a consulta de
    pendência — e a resposta do usuário passa a ser lida do servidor, em
    :mod:`petwatch.pending`. Os dois carregam o ``sessionID`` do pedido
    porque o stream é global: sem ele, a espera não tem dono (bug 20).
    """

    def build(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        asked = []
        states = []

        monitor.ask_seen.connect(lambda *_: asked.append(1))
        monitor.state_changed.connect(states.append)

        return monitor, asked, states

    def test_a_question_wakes_the_pet_and_publishes_no_state(self):
        monitor, asked, states = self.build()

        monitor.process_event(None, {"type": "form.created"})

        self.assertEqual(len(asked), 1)
        self.assertEqual(states, [])

    def test_another_tab_working_does_not_touch_the_question(self):
        monitor, asked, states = self.build()

        for event_type in (
            "form.created",
            "shell.exited",
            "session.tool.called",
            "session.step.streamed",
        ):
            monitor.process_event(None, {"type": event_type})

        # Só o pedido acorda a consulta; e o estado publicado é o do
        # stream, que não tem nada a dizer sobre espera.
        self.assertEqual(len(asked), 1)
        self.assertEqual(states, [STATE_WORKING])

    def test_the_answer_wakes_the_pet_as_well(self):
        """A resposta também é hora de consultar.

        Soltar a espera é tão lento quanto criar, e o tique do poll pode
        estar a 20s de distância.
        """

        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        released = []

        monitor.released.connect(lambda *_: released.append(1))

        monitor.process_event(None, {"type": "form.replied"})

        self.assertEqual(len(released), 1)

    def test_the_ask_carries_the_session_that_asked(self):
        """O dono da espera é o que separa uma aba da outra."""

        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        seen = []

        monitor.ask_seen.connect(seen.append)

        monitor.process_event(None, {
            "type": "form.created",
            "data": {"sessionID": "ses_a"},
        })

        self.assertEqual(seen, ["ses_a"])

    def test_an_ask_without_a_session_is_still_an_ask(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        seen = []

        monitor.ask_seen.connect(seen.append)

        monitor.process_event(None, {"type": "form.created"})

        self.assertEqual(seen, [None])

    def test_a_reply_carries_its_session_too(self):
        from petwatch.monitor import OpenCodeMonitor

        monitor = OpenCodeMonitor()

        seen = []

        monitor.released.connect(seen.append)

        monitor.process_event(None, {
            "type": "form.replied",
            "data": {"sessionID": "ses_b"},
        })

        self.assertEqual(seen, ["ses_b"])

    def test_a_permission_request_wakes_the_pet(self):
        monitor, asked, states = self.build()

        monitor.process_event(None, {"type": "permission.asked"})

        self.assertEqual(len(asked), 1)
        self.assertEqual(states, [])

    def test_activity_still_flows_while_the_question_holds(self):
        """O pedido conta como atividade: o opencode está vivo."""

        monitor, _asked, _states = self.build()

        activity = []

        monitor.activity.connect(lambda: activity.append(1))

        for event_type in ("form.created", "shell.exited", "session.tool.called"):
            monitor.process_event(None, {"type": event_type})

        self.assertEqual(len(activity), 3)


if __name__ == "__main__":
    unittest.main()
