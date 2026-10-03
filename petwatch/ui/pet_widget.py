"""Janela do pet: sprite, balão de status, arrasto e encerramento."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ..assets import FrameSource, resolve_frames
from ..config import (
    ANIMATION_INTERVAL_MS,
    SPRITE_MARGIN_BOTTOM,
    SPRITE_MAX_HEIGHT,
    SPRITE_MAX_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from ..states import (
    STATE_IDLE,
    STATE_WAITING,
    STATE_WORKING,
    VALID_STATES,
    labels_for,
)
from ..theme import PetTheme
from . import bubble

log = logging.getLogger(__name__)

#: Ticks por segundo do timer que redesenha o pet.
TICKS_PER_SECOND = 1000 / ANIMATION_INTERVAL_MS


class PetRenderer(QWidget):
    """Widget transparente que desenha o sprite e o balão."""

    def __init__(self, theme: PetTheme) -> None:
        super().__init__()

        self.theme = theme

        self.state = "connecting"

        #: Contador do bob vertical.
        self.animation_frame = 0

        #: Quadro atual da spritesheet.
        self.sprite_frame = 0

        self.source: FrameSource | None = None

        self.load_asset()

        self.sprite_frame = self.loop_range[0]

        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )

        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        # --------------------------------------------------------
        # Animação
        # --------------------------------------------------------

        self.timer = QTimer(self)

        self.timer.timeout.connect(self.animate)

        self.timer.start(ANIMATION_INTERVAL_MS)

    # ------------------------------------------------------------
    # Asset
    # ------------------------------------------------------------

    def load_asset(self) -> None:
        """Carrega o primeiro asset do tema que o Qt consiga decodificar."""

        self.source, path = resolve_frames(
            self.theme.directory,
            self.theme.asset_path,
            self.theme.grid,
            self.theme.loop,
        )

        if path:
            first, last = self.loop_range

            log.info(
                "[pet] %s usando %s (anima %d..%d de %d)",
                self.theme.name,
                path.name,
                first,
                last,
                self.frame_count,
            )

    @property
    def frame_count(self) -> int:
        return self.source.frame_count if self.source else 0

    @property
    def loop_range(self) -> tuple[int, int]:
        """Intervalo de quadros animados; ``(0, 0)`` sem sprite."""

        return self.source.loop_range if self.source else (0, 0)

    @property
    def frame_step(self) -> int:
        """Quantos quadros o sprite avança por tick do timer."""

        return max(1, round(self.theme.fps / TICKS_PER_SECOND))

    def current_image(self):
        """Quadro que deve ser desenhado agora."""

        if self.source is None:
            return None

        return self.source.frame(self.sprite_frame)

    # ------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state not in VALID_STATES:
            return

        self.state = state

        self.update()

    # ------------------------------------------------------------
    # Animação
    # ------------------------------------------------------------

    def animate(self) -> None:
        self.animation_frame += 1

        self.advance_sprite()

        self.update()

    def advance_sprite(self) -> None:
        """Move o sprite um passo dentro do laço de animação."""

        if self.source is None:
            return

        first, last = self.source.loop_range
        span = last - first + 1

        if span < 2:
            return

        offset = self.sprite_frame - first + self.frame_step

        self.sprite_frame = first + offset % span

    # ------------------------------------------------------------
    # Balão
    # ------------------------------------------------------------

    def draw_status(self, painter: QPainter) -> None:
        if not self.theme.text_enabled:
            return

        title, subtitle = labels_for(self.state)

        layout = bubble.layout_for(
            self.theme,
            title,
            subtitle,
            self.width(),
        )

        bubble.paint(painter, layout, self.theme, title, subtitle)

    # ------------------------------------------------------------
    # Sprite
    # ------------------------------------------------------------

    def bob_offset(self) -> int:
        """Deslocamento vertical conforme o estado."""

        frame = self.animation_frame

        if self.state == STATE_IDLE:
            return 2 if frame % 8 < 4 else 0

        if self.state == STATE_WORKING:
            return -2 if frame % 4 < 2 else 2

        if self.state == STATE_WAITING:
            return 1

        return 0

    def scaled_pixmap(self) -> QPixmap | None:
        image = self.current_image()

        if image is None:
            return None

        pixmap = QPixmap.fromImage(image)

        if pixmap.isNull():
            return None

        # Mantém proporção.
        return pixmap.scaled(
            int(SPRITE_MAX_WIDTH * self.theme.scale),
            int(SPRITE_MAX_HEIGHT * self.theme.scale),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def draw_pet(self, painter: QPainter) -> None:
        pixmap = self.scaled_pixmap()

        if pixmap is None:
            return

        bob = self.bob_offset()

        x = (self.width() - pixmap.width()) // 2
        y = self.height() - pixmap.height() - SPRITE_MARGIN_BOTTOM + bob

        painter.drawPixmap(x, y, pixmap)

    # ------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Primeiro o Pet, depois o texto.
        self.draw_pet(painter)
        self.draw_status(painter)

    # ------------------------------------------------------------
    # Arrastar
    # ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            window = self.windowHandle()

            if window:
                window.startSystemMove()

            event.accept()

            return

        if event.button() == Qt.RightButton:
            self.close()

            event.accept()

            return

        event.ignore()
