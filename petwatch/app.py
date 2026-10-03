"""Montagem e ciclo de vida do aplicativo."""

from __future__ import annotations

import logging
import signal

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from .config import (
    DEFAULT_THEME,
    SCREEN_GAP_X,
    SCREEN_GAP_Y,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from .monitor import OpenCodeMonitor
from .theme import load_theme
from .ui import PetRenderer

log = logging.getLogger(__name__)

#: Tempo máximo de espera da thread do monitor no encerramento.
SHUTDOWN_GRACE_MS = 5000
SHUTDOWN_FORCE_MS = 1000

#: Sinais tratados para encerrar em vez de levantar traceback.
QUIT_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class PetApplication(QApplication):
    """Aplica o tema, liga o monitor e cuida do encerramento."""

    def __init__(self, theme_name: str = DEFAULT_THEME) -> None:
        super().__init__([])

        # --------------------------------------------------------
        # Tema
        # --------------------------------------------------------

        self.theme = load_theme(theme_name)

        # --------------------------------------------------------
        # Pet
        # --------------------------------------------------------

        self.pet = PetRenderer(self.theme)

        # --------------------------------------------------------
        # Monitor
        # --------------------------------------------------------

        self.monitor = OpenCodeMonitor()

        self.thread = QThread()

        self.monitor.moveToThread(self.thread)

        self.thread.started.connect(self.monitor.run)

        self.monitor.state_changed.connect(self.pet.set_state)

        # --------------------------------------------------------
        # Thread
        # --------------------------------------------------------

        self.thread.start()

        # --------------------------------------------------------
        # Tela
        # --------------------------------------------------------

        self.place_on_screen()

        self.pet.show()

        # --------------------------------------------------------
        # Encerramento
        # --------------------------------------------------------

        self.aboutToQuit.connect(self.shutdown)

        install_quit_signals(self)

    # ------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------

    def place_on_screen(self) -> None:
        """Ancora o pet no canto inferior direito da tela principal."""

        screen = self.primaryScreen()

        if screen is None:
            return

        geometry = screen.availableGeometry()

        self.pet.move(
            geometry.right() - WINDOW_WIDTH - SCREEN_GAP_X,
            geometry.bottom() - WINDOW_HEIGHT - SCREEN_GAP_Y,
        )

    def shutdown(self) -> None:
        """Para o monitor e espera a thread terminar.

        ``stop()`` desliga o socket do stream, então a leitura bloqueada
        volta na hora e ``wait()`` completa dentro do primeiro prazo.
        """

        self.monitor.stop()

        if not self.thread.isRunning():
            return

        self.thread.quit()

        if self.thread.wait(SHUTDOWN_GRACE_MS):
            return

        log.warning("[pet] thread do monitor não parou a tempo; forçando")

        self.thread.requestInterruption()

        if not self.thread.wait(SHUTDOWN_FORCE_MS):
            log.error("[pet] thread do monitor segue ativa")

    def run(self) -> int:
        return self.exec()

    # ------------------------------------------------------------
    # Robustez
    # ------------------------------------------------------------

    def notify(self, receiver, event) -> bool:
        """Erro em um handler de evento não pode derrubar o programa.

        Cobre o código chamado pelo Qt como *evento* — ``paintEvent``,
        ``mousePressEvent``, ``resizeEvent`` — que é onde roda quase tudo
        aqui. Sem esta volta, o PySide6 imprime o traceback e segue com o
        event loop; com ela, o erro vai para o log com o evento e o
        receptor no contexto.

        Exceções em *slots de sinal* (o ``timeout`` do timer, por
        exemplo) não passam por aqui: o PySide6 as imprime e continua, o
        que também preserva a janela.
        """

        try:
            return super().notify(receiver, event)

        except Exception:
            log.exception(
                "[pet] erro tratando %s em %s",
                type(event).__name__,
                type(receiver).__name__,
            )

            return False


def install_quit_signals(app: QApplication) -> None:
    """Faz ``Ctrl+C`` e ``kill`` encerrarem o aplicativo.

    Sem isso o ``SIGINT`` levanta ``KeyboardInterrupt`` dentro do slot que
    o Qt está executando; o PySide6 imprime o traceback e o event loop
    continua, então o processo não sai. O handler chama ``quit()``, que
    dispara ``aboutToQuit`` e o mesmo caminho limpo do botão direito.

    O handler roda na thread principal entre um tique do timer e o
    seguinte, então a resposta leva no máximo ``ANIMATION_INTERVAL_MS``.
    """

    def request_quit(signum, frame) -> None:
        log.info("[pet] %s recebido; encerrando", signal.Signals(signum).name)

        app.quit()

    for number in QUIT_SIGNALS:
        try:
            signal.signal(number, request_quit)
        except (ValueError, OSError) as exc:
            # Só é possível instalar na thread principal.
            log.debug("[pet] sinal %s não registrado: %s", number, exc)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    return PetApplication().run()
