"""Desenho do balão de status.

O balão tem duas linhas: um título em negrito e um subtítulo mais claro,
alinhados à esquerda dentro de um retângulo arredondado claro com sombra
suave. Medir e pintar ficam aqui para que o widget só forneça o estado.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)

from ..config import BUBBLE_TOP_MARGIN
from ..theme import PetTheme

#: Famílias tentadas em ordem quando o tema não pede uma disponível.
#: A primeira que existir no sistema é usada, então o balão fica bonito em
#: máquinas com Inter/Roboto e ainda funciona nas outras.
FONT_PREFERENCE = (
    "Inter",
    "Roboto",
    "Segoe UI",
    "Helvetica Neue",
    "Ubuntu Sans",
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
)

#: Camadas da sombra: (deslocamento, fração do alfa).
SHADOW_LAYERS = ((3, 0.30), (2, 0.55), (1, 1.0))


@lru_cache(maxsize=1)
def available_families() -> frozenset[str]:
    return frozenset(QFontDatabase.families())


@lru_cache(maxsize=16)
def resolve_family(preferred: str) -> str:
    """Primeira família disponível, honrando ``preferred`` se existir."""

    families = available_families()

    if preferred and preferred in families:
        return preferred

    for name in FONT_PREFERENCE:
        if name in families:
            return name

    return "sans-serif"


def title_font(theme: PetTheme) -> QFont:
    """Fonte do título: negrito, mesma família do subtítulo."""

    font = QFont(resolve_family(theme.font_family), theme.title_font_size)

    font.setStyleHint(QFont.SansSerif)
    font.setWeight(QFont.Bold)

    return font


def subtitle_font(theme: PetTheme) -> QFont:
    """Fonte do subtítulo: peso normal."""

    font = QFont(resolve_family(theme.font_family), theme.subtitle_font_size)

    font.setStyleHint(QFont.SansSerif)
    font.setWeight(QFont.Normal)

    return font


@dataclass(frozen=True, slots=True)
class BubbleLayout:
    """Retângulos prontos para pintar, já medidos."""

    rect: QRect
    title_rect: QRect
    subtitle_rect: QRect
    title_font: QFont
    subtitle_font: QFont


def layout_for(
    theme: PetTheme,
    title: str,
    subtitle: str,
    container_width: int,
) -> BubbleLayout:
    """Mede o balão e devolve os retângulos das duas linhas.

    A largura segue a linha mais longa e o balão fica centralizado
    horizontalmente, colado ao topo da janela.
    """

    bold = title_font(theme)
    regular = subtitle_font(theme)

    bold_metrics = QFontMetrics(bold)
    regular_metrics = QFontMetrics(regular)

    text_width = max(
        bold_metrics.horizontalAdvance(title),
        regular_metrics.horizontalAdvance(subtitle),
    )

    title_height = bold_metrics.height()
    subtitle_height = regular_metrics.height()

    width = text_width + theme.padding_x * 2
    height = title_height + theme.line_gap + subtitle_height + theme.padding_y * 2

    left = (container_width - width) // 2
    top = BUBBLE_TOP_MARGIN

    text_left = left + theme.padding_x
    text_width_inner = width - theme.padding_x * 2

    return BubbleLayout(
        rect=QRect(left, top, width, height),
        title_rect=QRect(text_left, top + theme.padding_y,
                         text_width_inner, title_height),
        subtitle_rect=QRect(
            text_left,
            top + theme.padding_y + title_height + theme.line_gap,
            text_width_inner,
            subtitle_height,
        ),
        title_font=bold,
        subtitle_font=regular,
    )


def build_path(rect: QRect, theme: PetTheme) -> QPainterPath:
    """Caminho arredondado do balão."""

    path = QPainterPath()

    path.addRoundedRect(
        rect,
        theme.corner_radius,
        theme.corner_radius,
    )

    return path


def paint(
    painter: QPainter,
    layout: BubbleLayout,
    theme: PetTheme,
    title: str,
    subtitle: str,
) -> None:
    """Pinta sombra, fundo, borda e as duas linhas de texto."""

    path = build_path(layout.rect, theme)

    # Sombra suave: camadas de deslocamento decrescente.
    if theme.shadow_alpha > 0:
        for offset, factor in SHADOW_LAYERS:
            shadow = QColor(0, 0, 0, round(theme.shadow_alpha * factor))

            painter.setPen(Qt.NoPen)
            painter.setBrush(shadow)
            painter.drawPath(path.translated(0, offset))

    # Fundo
    background = QColor(theme.background)
    background.setAlpha(theme.background_alpha)

    painter.setPen(Qt.NoPen)
    painter.setBrush(background)
    painter.drawPath(path)

    # Borda
    painter.setPen(QPen(QColor(theme.border), theme.border_width))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)

    # Texto
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    alignment = Qt.AlignLeft | Qt.AlignVCenter

    painter.setFont(layout.title_font)
    painter.setPen(QPen(QColor(theme.title_color)))

    painter.drawText(layout.title_rect, alignment, title)

    painter.setFont(layout.subtitle_font)
    painter.setPen(QPen(QColor(theme.subtitle_color)))

    painter.drawText(layout.subtitle_rect, alignment, subtitle)
