"""Encerramento: sinais, notify() e montagem do aplicativo.

``PetApplication`` herda de ``QApplication``, então só pode existir uma
instância por processo. Este módulo cria a sua no import — antes dos outros
módulos, por ordem alfabética — e o monitor é parado na hora para não
sair para a rede durante a suíte.

Precisa de ``QT_QPA_PLATFORM=offscreen``.

O app é construído com um ``prefs_path`` temporário. Sem isso a suíte lê
o ``prefs.json`` de quem a roda — o tema viria do ``~/.config``, não do
código — e, pior, cada ``save()`` sobrescreveria esse arquivo com o
estado do app de teste.
"""

from __future__ import annotations

import os
import signal
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QRect, QTimer  # noqa: E402
from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from petwatch.app import (  # noqa: E402
    QUIT_SIGNALS,
    PetApplication,
    install_quit_signals,
    run_app,
)
from petwatch.config import (  # noqa: E402
    DEFAULT_THEME,
    SCREEN_GAP_X,
    SCREEN_GAP_Y,
)
from petwatch.daemon import SingleInstance, parse_args  # noqa: E402
from petwatch.pending import PendingAsk  # noqa: E402

#: Preferências descartáveis, uma por execução da suíte.
PREFS_PATH = Path(tempfile.mkdtemp(prefix="petwatch-tests-")) / "prefs.json"

#: A unica instancia possivel; outros modulos da suite reaproveitam.
APP = QApplication.instance()

if not isinstance(APP, PetApplication):
    # ``pending=False``: a consulta de pendência fala com o opencode de
    # verdade, e a suíte não deve tocar na rede. A política do estado
    # "aguardando" é testada em ``tests/test_pending.py``, que não
    # precisa de servidor nenhum.
    APP = PetApplication(prefs_path=PREFS_PATH, pending=False)

# Isola a suite da rede: sem isso o monitor ficaria sondando o opencode.
APP.monitor.stop()
APP.thread.quit()
APP.thread.wait(5000)


def paint_event() -> QPaintEvent:
    return QPaintEvent(QRect(0, 0, 1, 1))


class BrokenWidget(QWidget):
    """Widget cujo ``paintEvent`` pode falhar de propósito."""

    def __init__(self, raise_on_paint: bool = False):
        super().__init__()
        self.raise_on_paint = raise_on_paint

    def paintEvent(self, event):  # noqa: N802
        if self.raise_on_paint:
            raise ValueError("erro proposital no paintEvent")

        super().paintEvent(event)


class WiringTests(unittest.TestCase):
    """A montagem une tema, janela e monitor."""

    def setUp(self):
        self.sync_state()

        # Um registro só: um ``addCleanup`` dentro do próprio reset
        # registraria outro a cada chamada, e a limpeza nunca acabaria.
        self.addCleanup(self.sync_state)

    def sync_state(self) -> None:
        """Deixa o quadro e o pet num estado conhecido.

        ``_sync`` publica só o que **muda**, então cada teste precisa
        partir de um estado que o quadro conheça — senão o teste
        anterior deixa o pet fora de sincronia e a falha aponta para o
        lugar errado.
        """

        # Quadro zerado: uma instância deixada de um teste anterior
        # mudaria o estado de partida e a falha apontaria para o lugar
        # errado.
        APP.board.instances.clear()
        APP.board.note_pending([])
        APP.board.note_release()
        APP.on_stream_state("idle")
        APP._sync()

        QCoreApplication.processEvents()

    def test_default_theme_is_loaded(self):
        """Precisa de preferências isoladas: senão o tema vem do ``~/.config``.

        Com um ``prefs.json`` do usuário apontando para outro pet, este
        teste falhava mesmo com o código certo — o app estava obeying as
        preferências, é o teste que estava errado.
        """

        from petwatch.theme import load_theme

        self.assertIsNone(APP.prefs["theme"])
        self.assertEqual(APP.theme.name, load_theme(DEFAULT_THEME).name)
        self.assertEqual(APP.pet.theme.name, APP.theme.name)

    def test_pet_window_is_the_grid(self):
        size = APP.pet.size

        self.assertEqual(APP.pet.width(), size.window_width_for())
        self.assertEqual(APP.pet.height(), APP.pet.window_height_for(0))

    def test_monitor_runs_on_its_own_thread(self):
        # ``APP.thread`` e o atributo que guardamos; ``monitor.thread()`` e
        # o metodo do QObject que devolve a thread atual.
        self.assertIs(APP.monitor.thread(), APP.thread)
        self.assertIsNot(APP.thread, APP)

    def test_state_signal_reaches_the_pet(self):
        self.sync_state()

        APP.monitor.state_changed.emit("working")

        # A conexão é enfileirada (ver ``test_state_is_applied_on_the_gui_thread``),
        # então o slot só roda quando o event loop entrega o evento.
        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "working")

    def test_state_is_applied_on_the_gui_thread(self):
        """``set_state`` mexe no ``QTimer`` do pulso, que é single-thread.

        Emitido da thread do monitor, um slot ligado direto rodava lá
        dentro — o Qt recusava o timer com ``QObject::startTimer: Timers
        cannot be started from another thread`` e o pulso de "aguardando"
        nunca animava.
        """

        self.sync_state()

        threads = []

        original = APP.pet.set_state

        def spy(state):
            threads.append(threading.current_thread())
            return original(state)

        APP.pet.set_state = spy
        self.addCleanup(setattr, APP.pet, "set_state", original)

        APP.monitor.state_changed.emit("waiting")

        QCoreApplication.processEvents()

        self.assertEqual(threads, [threading.main_thread()])
        self.assertEqual(APP.pet.state, "waiting")

    def test_about_to_quit_triggers_shutdown(self):
        # Comportamento em vez de introspecção: ``receivers()`` não conta
        # conexões para método do próprio objeto no PySide6.
        APP.monitor.stop_event.clear()

        APP.aboutToQuit.emit()

        self.assertTrue(APP.monitor.stop_event.is_set())

    def test_the_stream_state_goes_through_the_board(self):
        """O estado visível é o do quadro, não o do monitor.

        ``/api/event`` é global e perde eventos (documentação v2), então
        o stream só diz se há trabalho; quem decide se o pet espera o
        usuário é a consulta ao servidor — ver ``tests/test_pending.py``.
        """

        self.sync_state()

        APP.on_ask_seen("ses_a")

        APP.monitor.state_changed.emit("working")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "working")

    def test_a_pending_answer_wins_over_the_stream(self):
        """A pergunta fica na tela mesmo com outra aba trabalhando."""

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_answers([PendingAsk("form", "frm_1", "ses_a", "/p", "Questions")])

        APP.monitor.state_changed.emit("working")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "waiting")

        APP.on_answers([])

        APP.monitor.state_changed.emit("working")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "working")

    def test_an_unknown_answer_keeps_the_state(self):
        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_answers([PendingAsk("form", "frm_1", "ses_a", "/p")])

        APP.on_answers(None)

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "waiting")

    def test_the_watchdog_cannot_cut_a_real_wait(self):
        """O watchdog segue o estado visível: com espera de verdade, não age."""

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_answers([PendingAsk("form", "frm_1", "ses_a", "/p")])

        APP.watchdog.note_state(APP.pet.state)

        self.assertEqual(APP.pet.state, "waiting")
        self.assertFalse(APP.watchdog.is_due())

    def test_the_watchdog_also_frees_the_cards(self):
        """Silêncio é turno encerrado no balão, e não só no sprite.

        Com o estado por sessão, o watchdog mudava o sprite para "Ready" e
        deixava o balão dizendo "Thinking" — duas verdades na tela.
        """

        self.sync_state()

        APP.board.note_active(["ses_a"])

        APP.on_session_event("working", "ses_a")

        self.assertEqual([card[0] for card in APP.pet.cards], ["working"])

        # Finge o silêncio: o watchdog dispara depois de ``timeout`` sem
        # atividade, e o teste não vai dormir 45s para isso.
        #
        # Envelhece ``evented_at`` e não ``touched_at``: é o relógio do
        # stream que diz que a instância calou — ``touched_at`` é reescrito
        # a cada consulta de ativas e nunca envelhece sozinho (bug 21).
        APP.board.instances["ses_a"].evented_at -= APP.watchdog.timeout + 1

        APP.watchdog.note_state(APP.pet.state)

        APP.on_watchdog_idle("idle")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "idle")
        self.assertEqual([card[0] for card in APP.pet.cards], ["idle"])

    def test_a_ready_card_keeps_the_instance_name(self):
        """O nome da instância é a segunda linha, em qualquer estado."""

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_named(("ses_a", {
            "title": "Corrigindo o falso positivo",
            "location": {"directory": "/home/diego/projetos/dd"},
        }))

        APP.on_session_event("idle", "ses_a")

        QCoreApplication.processEvents()

        self.assertEqual(
            APP.pet.cards,
            [("idle", "Corrigindo o falso positivo", False)],
        )

    def test_one_card_per_active_instance(self):
        """Um balão por instância do opencode em ação."""

        from petwatch.sessions import Instance

        self.sync_state()

        APP.board.instances = {
            "ses_a": Instance(session_id="ses_a", title="pet-opencodev2",
                              state="working", active=True),
            "ses_b": Instance(session_id="ses_b", title="dd",
                              state="idle", active=True),
        }

        APP.board.pending_answered = True
        APP.board._refresh()
        APP._sync()

        QCoreApplication.processEvents()

        self.assertEqual(
            APP.pet.cards,
            [("working", "pet-opencodev2", False), ("idle", "dd", False)],
        )

    def test_a_card_is_flagged_only_where_the_answer_is_needed(self):
        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_answers([
            PendingAsk("permission", "per_1", "ses_b", "/p", "shell"),
        ])

        QCoreApplication.processEvents()

        # Uma sessão que espera resposta nasce com balão, mesmo sem
        # estar na lista de ativas: o pedido é a evidência de que ela
        # existe e quer algo.
        self.assertTrue(APP.board.instances["ses_b"].needs_action)

        flagged = [card for card in APP.pet.cards if card[2]]

        self.assertEqual([card[0] for card in flagged], ["waiting"])

    def test_the_card_agrees_with_the_sprite_in_degraded_mode(self):
        """O bug 20, visto pela interface.

        Degradado, o pedido só existe no stream. O sprite mudava para
        "waiting", mas o balão continuava dizendo "Thinking" — o usuário
        via exatamente isso, com a pergunta aberta na tela do opencode.
        """

        self.sync_state()

        APP.board.note_active(["ses_pergunta", "ses_outra"])
        APP.on_session_event("working", "ses_pergunta")

        # Degradação: o servidor não tem as rotas do v2.
        APP.on_answers(None)
        APP.on_ask_seen("ses_pergunta")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "waiting")
        self.assertIn("waiting", [card[0] for card in APP.pet.cards])

    def test_another_tab_going_idle_keeps_the_card_saying_waiting(self):
        self.sync_state()

        APP.board.note_active(["ses_pergunta", "ses_outra"])
        APP.on_session_event("working", "ses_pergunta")
        APP.on_answers(None)
        APP.on_ask_seen("ses_pergunta")

        APP.on_session_event("idle", "ses_outra")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "waiting")
        self.assertEqual(
            [card[0] for card in APP.pet.cards if card[2]], ["waiting"],
        )

    def test_the_reply_releases_the_card_as_soon_as_the_stream_says_so(self):
        """A resposta não espera o tique do poll para limpar a tela."""

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_answers(None)
        APP.on_ask_seen("ses_a")

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "waiting")

        APP.on_released("ses_a")

        QCoreApplication.processEvents()

        self.assertNotEqual(APP.pet.state, "waiting")
        self.assertFalse([card for card in APP.pet.cards if card[2]])

    def test_a_session_stuck_while_another_tab_works_still_goes_ready(self):
        """A segunda metade do bug 21: o watchdog global não pode ser o único guarda.

        O sintoma reportado é o balão preso em "Thinking" com o agente
        parado. A rede de segurança existia, mas só era acionada por
        ``on_watchdog_idle`` — e o watchdog global exige ``IDLE_TIMEOUT``
        de silêncio **do servidor inteiro**. Com outra aba do opencode
        trabalhando, o que é o uso normal, ele nunca dispara, e a sessão
        travada ficava em "Thinking" para sempre.

        Aqui a outra aba continua eventos durante toda a janela, e mesmo
        assim a travada tem de virar "Ready".
        """

        self.sync_state()

        APP.board.note_active(["ses_travada", "ses_ocupada"])

        # A aba que trabalha fala sem parar; a travada nunca fala.
        APP.on_session_event("working", "ses_ocupada")

        self.assertEqual(APP.pet.state, "working")

        # Silêncio só na travada. Envelhece o relógio de evento em relação
        # ao relógio do próprio quadro — `note_active` chama `tick()`, que
        # reescreve `_now`, então mexer em `_now` não serviria.
        APP.board.instances["ses_travada"].evented_at = (
            APP.board._clock() - APP.watchdog.timeout - 1
        )

        # A consulta de ativas continua devolvendo as duas — é o que o
        # servidor faz com uma sessão travada.
        APP.on_active(["ses_travada", "ses_ocupada"])

        QCoreApplication.processEvents()

        # O quadro é a fonte da verdade; o balão mostra o que ele arbitrou.
        states = {
            session_id: instance.state
            for session_id, instance in APP.board.instances.items()
        }

        self.assertEqual(states["ses_travada"], "idle")
        self.assertEqual(states["ses_ocupada"], "working")

        self.assertEqual(
            sorted(state for state, _label, _wants in APP.board.cards()),
            ["idle", "working"],
        )

    def test_the_reasoning_deltas_keep_the_bubble_in_thinking(self):
        """O sintoma reportado do bug 22, no caminho da tela inteira.

        O agente estava pensando e o balão dizia "Ready". A causa era o
        relógio de silêncio: só o evento que virava estado o movia, e
        ``session.reasoning.delta`` — que é a esmagadora maioria do
        stream — não vira estado. Com o poll seguindo, o balão caía no
        meio do raciocínio.
        """

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_session_event("working", "ses_a", "/p")

        self.assertEqual(APP.pet.state, "working")

        for _ in range(10):
            APP.board._now += APP.watchdog.timeout / 2

            # O que o stream emite de verdade durante o raciocínio.
            APP.on_session_alive("ses_a", "/p")
            APP.on_active(["ses_a"])

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "working")
        self.assertEqual([card[0] for card in APP.pet.cards], ["working"])

    def test_a_shell_event_keeps_the_bubble_in_thinking(self):
        """``shell.created`` não traz ``sessionID``; o ``location`` é o dono."""

        self.sync_state()

        APP.board.note_active(["ses_a"])
        APP.on_session_event("working", "ses_a", "/p")

        for _ in range(10):
            APP.board._now += APP.watchdog.timeout / 2

            APP.on_session_alive(None, "/p")
            APP.on_active(["ses_a"])

        QCoreApplication.processEvents()

        self.assertEqual(APP.pet.state, "working")

    def test_liveness_does_not_invent_a_bubble(self):
        """Evento de servidor global não vira balão nem estado."""

        self.sync_state()

        APP.on_session_alive(None, None)
        APP.on_session_alive("ses_nao_existe", None)

        QCoreApplication.processEvents()

        self.assertEqual(list(APP.board.instances), [])
        self.assertEqual(APP.pet.cards, [])

    def test_a_session_that_keeps_working_is_not_released_by_the_poll(self):
        """O contrário: o poll não pode rebaixar quem está de fato trabalhando."""

        self.sync_state()

        APP.board.note_active(["ses_a"])

        APP.on_session_event("working", "ses_a")

        for _ in range(5):
            APP.board._now += APP.watchdog.timeout / 2

            APP.on_session_event("working", "ses_a")

            APP.on_active(["ses_a"])

        QCoreApplication.processEvents()

        self.assertEqual([card[0] for card in APP.pet.cards], ["working"])

    def test_everything_published_sees_the_same_state(self):
        """Balão, bandeja e watchdog recebem o estado **visível**.

        A bandeja ligada direto ao `state_changed` do monitor mostraria
        "trabalhando" no ícone enquanto o balão diz "aguardando" — dois
        estados para a mesma coisa, e o usuário não sabe em qual
        acreditar.
        """

        seen = []

        class Spy:
            def on_state(self, state: str) -> None:
                seen.append(state)

        self.sync_state()

        original = APP.tray
        APP.tray = Spy()

        self.addCleanup(setattr, APP, "tray", original)

        APP.board.note_active(["ses_a"])
        APP.on_answers([PendingAsk("form", "frm_1", "ses_a", "/p")])

        QCoreApplication.processEvents()

        APP.on_answers([])

        QCoreApplication.processEvents()

        # A instância continua ativa e trabalhando, então o que
        # aparece depois de responder é "trabalhando" — e é isso que a
        # bandeja e o watchdog têm que ver.
        self.assertEqual(seen, ["waiting", "working"])
        self.assertEqual(APP.watchdog.state, "working")


class PrefsIsolationTests(unittest.TestCase):
    """A suíte não pode ler nem escrever o ``prefs.json`` de quem a roda.

    Sem o ``prefs_path`` temporário o app de teste lia as preferências
    reais — e cada ``save()`` sobrescrevia o arquivo do usuário com o
    estado do app de teste. Ambos os lados são checados aqui.
    """

    def setUp(self):
        from petwatch import prefs

        self.real_path = prefs.PREFS_PATH

    def fingerprint(self, path: Path) -> tuple:
        """Estado do arquivo, para comparar antes e depois."""

        if not path.exists():
            return (False, None, None)

        stat = path.stat()

        return (True, stat.st_mtime_ns, path.read_bytes())

    def test_the_app_reads_the_isolated_file(self):
        self.assertEqual(APP.prefs_path, PREFS_PATH)
        self.assertNotEqual(APP.prefs_path, self.real_path)

    def test_without_the_argument_the_app_keeps_the_user_file(self):
        """O isolamento é do teste, não uma mudança de comportamento."""

        import inspect

        default = inspect.signature(PetApplication.__init__).parameters["prefs_path"]

        self.assertIsNone(default.default)

    def test_saving_writes_the_isolated_file(self):
        APP.save()

        self.assertTrue(PREFS_PATH.exists())

        from petwatch.prefs import load_prefs

        written = load_prefs(PREFS_PATH)

        self.assertEqual(written["theme"], APP.theme_name)
        self.assertEqual(written["size"], APP.size.key)
        self.assertEqual(written["always_on_top"], APP.always_on_top)

    def test_saving_never_touches_the_user_file(self):
        before = self.fingerprint(self.real_path)

        APP.save()

        self.assertEqual(self.fingerprint(self.real_path), before)

    def test_shutdown_does_not_touch_the_user_file_either(self):
        """``shutdown()`` chama ``save()``, então é o caminho mais usado."""

        before = self.fingerprint(self.real_path)

        APP.shutdown()

        self.assertEqual(self.fingerprint(self.real_path), before)

    def test_a_missing_isolated_file_gives_defaults(self):
        """A suíte não pode carregar o estado de uma execução anterior."""

        self.assertEqual(APP.prefs, {
            "version": 1,
            "theme": None,
            "size": "medium",
            "always_on_top": True,
        })


class PlaceOnScreenTests(unittest.TestCase):
    def test_moves_the_pet_into_the_available_geometry(self):
        screen = APP.primaryScreen()

        self.assertIsNotNone(screen)

        APP.place_on_screen()

        geometry = screen.availableGeometry()

        self.assertEqual(
            APP.pet.x(),
            geometry.right() - APP.pet.width() - SCREEN_GAP_X,
        )
        self.assertEqual(
            APP.pet.y(),
            geometry.bottom() - APP.pet.height() - SCREEN_GAP_Y,
        )


class QuitSignalTests(unittest.TestCase):
    def test_sigint_and_sigterm_are_handled(self):
        self.assertIn(signal.SIGINT, QUIT_SIGNALS)
        self.assertIn(signal.SIGTERM, QUIT_SIGNALS)

    def test_handlers_are_installed(self):
        for number in QUIT_SIGNALS:
            with self.subTest(signal=number):
                self.assertTrue(callable(signal.getsignal(number)))

    def _send_sigint(self):
        """Roda o event loop com um SIGINT real e devolve se chegou."""

        calls = []

        installed = signal.getsignal(signal.SIGINT)

        def forward(signum, frame):
            calls.append(signum)
            installed(signum, frame)

        previous = signal.signal(signal.SIGINT, forward)

        try:
            QTimer.singleShot(0, lambda: os.kill(os.getpid(), signal.SIGINT))
            QTimer.singleShot(3000, APP.quit)

            # Sem handler instalado, KeyboardInterrupt escaparia daqui.
            APP.exec()

        finally:
            signal.signal(signal.SIGINT, previous)

        return calls

    def test_sigint_reaches_the_handler_and_leaves_the_loop(self):
        self.assertEqual(self._send_sigint(), [signal.SIGINT])

    def test_quit_after_a_signal_still_shuts_the_monitor_down(self):
        self._send_sigint()

        self.assertFalse(APP.thread.isRunning())


class ShutdownTests(unittest.TestCase):
    def test_quit_stops_the_monitor_thread(self):
        APP.monitor.stop_event.clear()

        QTimer.singleShot(0, APP.quit)

        APP.exec()

        self.assertTrue(APP.monitor.stop_event.is_set())
        self.assertFalse(APP.thread.isRunning())

    def test_shutdown_is_safe_to_repeat(self):
        APP.shutdown()
        APP.shutdown()

        self.assertFalse(APP.thread.isRunning())


class NotifyTests(unittest.TestCase):
    def test_raising_handler_is_logged_and_swallowed(self):
        broken = BrokenWidget(raise_on_paint=True)

        with self.assertLogs("petwatch.app", level="ERROR"):
            handled = APP.notify(broken, paint_event())

        self.assertFalse(handled)

    def test_log_names_the_event_and_the_receiver(self):
        broken = BrokenWidget(raise_on_paint=True)

        with self.assertLogs("petwatch.app", level="ERROR") as captured:
            APP.notify(broken, paint_event())

        self.assertIn("QPaintEvent", captured.output[0])
        self.assertIn("BrokenWidget", captured.output[0])

    def test_healthy_handler_logs_nothing(self):
        with self.assertNoLogs("petwatch.app", level="ERROR"):
            APP.notify(BrokenWidget(), paint_event())


class InstallQuitSignalsTests(unittest.TestCase):
    def test_survives_a_non_main_thread(self):
        """Fora da thread principal ``signal.signal`` não pode ser usado."""

        errors = []

        def worker():
            try:
                install_quit_signals(APP)

            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)

        self.assertEqual(errors, [])


class DetachedStartupTests(unittest.TestCase):
    """O que o processo solto conta ao processo original.

    O processo original fica esperando a confirmação da janela antes de
    devolver o terminal. Se a janela não abrir, quem precisa saber é ele —
    o traceback sozinho iria para o log, e ninguém foi avisado de que
    existe um log. Aqui a janela não abre de propósito: a suíte já tem um
    ``QApplication`` neste processo, e um segundo levanta ``RuntimeError``.
    """

    def setUp(self):
        self.instance = SingleInstance(
            Path(tempfile.mkdtemp(prefix="petwatch-detached-")) / "pet.pid",
        )

        self.assertTrue(self.instance.claim())

        self.read_fd, self.write_fd = os.pipe()

        self.addCleanup(os.close, self.read_fd)

    def message(self) -> str:
        with os.fdopen(os.dup(self.read_fd), "rb") as stream:
            return stream.read().decode("utf-8", "replace")

    def test_a_failed_startup_is_reported_through_the_pipe(self):
        with self.assertRaises(RuntimeError):
            run_app(parse_args([]), self.instance, self.write_fd)

        message = self.message()

        self.assertTrue(message.startswith("erro RuntimeError:"), message)

        # Uma linha só: o traceback inteiro viraria uma mensagem só.
        self.assertEqual(message.count("\n"), 1)

    def test_a_failed_startup_releases_the_instance(self):
        """O lock fica preso, o segundo ``pet.py`` nunca mais roda."""

        with self.assertRaises(RuntimeError):
            run_app(parse_args([]), self.instance, self.write_fd)

        self.assertFalse(self.instance.running())

    def test_without_a_pipe_nothing_is_reported_and_nothing_breaks(self):
        """``--foreground`` passa ``None``; não há processo esperando."""

        with self.assertRaises(RuntimeError):
            run_app(parse_args(["--foreground"]), self.instance)


if __name__ == "__main__":
    unittest.main()
