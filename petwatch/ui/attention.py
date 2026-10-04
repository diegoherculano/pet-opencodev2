"""Animação de "precisa de você".

O opencode sinaliza um pedido de permissão ou de formulário com
``session.permission.create`` / ``session.form.create``, e a resposta só
vem quando o usuário age. Esse estado é o único em que o pet está de fato
esperando algo, então é o único que merece um destaque.

A animação é um pulso do balão e do sprite: nada de piscar, nada de
mover a janela. É um "respiro" — Cresce e volta, como alguém chamando a
atenção semutter.

Roda na thread da interface, porque é ``QTimer`` + ``QPropertyAnimation``
e precisa de event loop.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    Property,
    QObject,
    QPropertyAnimation,
    QRect,
    QTimer,
    QEasingCurve,
    Qt,
    Signal,
)

from . import bubble

log = logging.getLogger(__name__)


class AttentionPulse(QObject):
    """Pulso suave no balão, com repetição enquanto a espera dura."""

    #: Mudou o valor da animação. O widget escuta para pedir repaint,
    #: porque o pulso anda fora do tique do sprite.
    progress_changed = Signal(float)

    #: O pulso terminou ou foi interrompido.
    finished = Signal()

    #: Intervalo entre repetições. Dois segundos de pausa entre picos
    #: parece um pedido, e não um alarme.
    REPEAT_MS = 2000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._progress = 0.0

        #: Multiplica os efeitos em pixels, para acompanhar o tamanho.
        self._amplitude = 1.0

        self._cycle = QTimer(self)
        self._cycle.setSingleShot(True)
        self._cycle.timeout.connect(self._run_once)

        self._animation = QPropertyAnimation(self, b"progress")
        self._animation.setDuration(760)
        self._animation.setKeyValueAt(0.0, 0.0)
        self._animation.setKeyValueAt(0.45, 1.0)
        self._animation.setKeyValueAt(1.0, 0.0)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

        # O pulso mexe no desenho fora do tique do sprite, então o
        # repaint vem do próprio sinal.
        self.progress_changed.connect(lambda _value: self._on_changed())

    def _on_changed(self) -> None:
        """Alguém connected(): redesenha."""
        parent = self.parent()

        if parent is not None and hasattr(parent, "update"):
            parent.update()

    # ------------------------------------------------------------
    # Propriedade
    # ------------------------------------------------------------

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        value = max(0.0, min(1.0, value))

        if value != self._progress:
            self._progress = value

            self.progress_changed.emit(value)

    progress = Property(float, get_progress, set_progress)

    # ------------------------------------------------------------
    # Controle
    # ------------------------------------------------------------

    def start(self, *, immediately: bool = True) -> None:
        """Começa (ou recomeça) a animação."""

        self._animation.stop()

        self.set_progress(0.0)

        if immediately:
            self._run_once()

    def stop(self) -> None:
        """Para e devolve tudo ao repouso."""

        self._cycle.stop()
        self._animation.stop()

        was_running = self._progress > 0.0

        self.set_progress(0.0)

        if was_running:
            self.finished.emit()

    def _run_once(self) -> None:
        self._animation.start()

        # Agenda o próximo pico; o timer é zerado em ``stop``.
        self._cycle.start(self.REPEAT_MS)

    def is_running(self) -> bool:
        return self._animation.state() == self._animation.State.Running

    # ------------------------------------------------------------
    # Efeito visual
    # ------------------------------------------------------------

    def set_amplitude(self, amplitude: float = 1.0) -> None:
        """Ajusta a intensidade do pulso ao preset de tamanho.

        Os efeitos são em pixels absolutos, então num pet pequeno um
        salto de 5px seria proporcionalmente gigante. Com 1.0 no preset
        "medio", o "pequeno" reduz e o "grande" amplia.
        """

        self._amplitude = max(0.0, amplitude)

    def bubble_growth(self) -> float:
        """Quanto o balão cresce, em pixels."""

        return self._progress * self._amplitude * 3.0

    def sprite_lift(self) -> float:
        """Quanto o sprite sobe, em pixels."""

        return self._progress * self._amplitude * 5.0

    def glow_alpha(self) -> int:
        """Alfa extra da borda, para o balão parecer ativo."""

        return round(self._progress * 90)

    # ------------------------------------------------------------
    # Integração com o balão
    # ------------------------------------------------------------

    def decorate(self, painter, layout, theme) -> None:
        """Desenha o contorno pulsante em volta do balão.

        Vem depois de :func:`petwatch.ui.bubble.paint`, então o contorno
        fica por cima sem alterar o que já foi pintado.
        """

        if self._progress <= 0.01:
            return

        from PySide6.QtGui import QColor, QPen

        glow = QColor(theme.title_color)
        glow.setAlpha(self.glow_alpha())

        painter.setPen(QPen(glow, 2, Qt.SolidLine))
        painter.setBrush(Qt.NoBrush)

        rect = QRect(layout.rect)
        rect.adjust(
            -round(self.bubble_growth()),
            -round(self.bubble_growth()),
            round(self.bubble_growth()),
            round(self.bubble_growth()),
        )

        painter.drawPath(bubble.build_path(rect, theme))