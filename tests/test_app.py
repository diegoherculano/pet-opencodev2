"""Encerramento: sinais, notify() e montagem do aplicativo.

``PetApplication`` herda de ``QApplication``, então só pode existir uma
instância por processo. Este módulo cria a sua no import — antes dos outros
módulos, por ordem alfabética — e o monitor é parado na hora para não
sair para a rede durante a suíte.

Precisa de ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import os
import signal
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QTimer  # noqa: E402
from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from petwatch.app import (  # noqa: E402
    QUIT_SIGNALS,
    PetApplication,
    install_quit_signals,
)
from petwatch.config import (  # noqa: E402
    DEFAULT_THEME,
    SCREEN_GAP_X,
    SCREEN_GAP_Y,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)

#: A unica instancia possivel; outros modulos da suite reaproveitam.
APP = QApplication.instance()

if not isinstance(APP, PetApplication):
    APP = PetApplication()

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

    def test_default_theme_is_loaded(self):
        from petwatch.theme import load_theme

        self.assertEqual(APP.theme.name, load_theme(DEFAULT_THEME).name)
        self.assertEqual(APP.pet.theme.name, APP.theme.name)

    def test_pet_window_is_the_configured_size(self):
        self.assertEqual(
            (APP.pet.width(), APP.pet.height()),
            (WINDOW_WIDTH, WINDOW_HEIGHT),
        )

    def test_monitor_runs_on_its_own_thread(self):
        # ``APP.thread`` e o atributo que guardamos; ``monitor.thread()`` e
        # o metodo do QObject que devolve a thread atual.
        self.assertIs(APP.monitor.thread(), APP.thread)
        self.assertIsNot(APP.thread, APP)

    def test_state_signal_reaches_the_pet(self):
        APP.pet.set_state("idle")

        APP.monitor.state_changed.emit("working")

        self.assertEqual(APP.pet.state, "working")

    def test_about_to_quit_triggers_shutdown(self):
        # Comportamento em vez de introspecção: ``receivers()`` não conta
        # conexões para método do próprio objeto no PySide6.
        APP.monitor.stop_event.clear()

        APP.aboutToQuit.emit()

        self.assertTrue(APP.monitor.stop_event.is_set())


class PlaceOnScreenTests(unittest.TestCase):
    def test_moves_the_pet_into_the_available_geometry(self):
        screen = APP.primaryScreen()

        self.assertIsNotNone(screen)

        APP.place_on_screen()

        geometry = screen.availableGeometry()

        self.assertEqual(
            APP.pet.x(),
            geometry.right() - WINDOW_WIDTH - SCREEN_GAP_X,
        )
        self.assertEqual(
            APP.pet.y(),
            geometry.bottom() - WINDOW_HEIGHT - SCREEN_GAP_Y,
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


if __name__ == "__main__":
    unittest.main()
