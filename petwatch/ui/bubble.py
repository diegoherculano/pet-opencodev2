"""Desenho do balão de status.

O balão tem duas linhas: um título em negrito e um subtítulo mais claro,
alinhados à esquerda dentro de um retângulo arredondado claro com sombra
suave. Medir e pintar ficam aqui para que o widget só forneça o estado.

Desde a grade (um balão por instância do opencode em ação) há dois
acréscimos:

- **largura fixa e reticências.** A caixa de cada balão tem a largura do
  preset, e o texto que não cabe é cortado com reticências em vez de
  alargar a coluna — alargarfaria a caixa e desalinharia a grade.
- **cor de destaque.** Só o título de quem **precisa de resposta** é
  pintado com :data:`~petwatch.states.ACTION_COLOR`. Os outros
  balões ficam com a cor do tema, porque pintar todos transforma a tela
  num painel de status em vez de num aviso.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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

from ..config import (
    BUBBLE_TOP_MARGIN,
    CARD_LINE_GAP,
    CARD_PADDING_X,
    CARD_PADDING_Y,
)
from ..sizes import PetSize
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


# ------------------------------------------------------------
# Balão empilhado
# ------------------------------------------------------------


def card_title_font(theme: PetTheme, size: PetSize) -> QFont:
    """Fonte do título de um balão da grade."""

    font = QFont(resolve_family(theme.font_family), max(6, size.card_title_font))

    font.setStyleHint(QFont.SansSerif)
    font.setWeight(QFont.Bold)

    return font


def card_subtitle_font(theme: PetTheme, size: PetSize) -> QFont:
    """Fonte do subtítulo de um balão da grade: regular e menor."""

    font = QFont(resolve_family(theme.font_family),
                 max(6, size.card_subtitle_font))

    font.setStyleHint(QFont.SansSerif)
    font.setWeight(QFont.Normal)

    return font


def card_theme(theme: PetTheme, size: PetSize) -> PetTheme:
    """Tema com o padding do balão empilhado.

    Só o padding muda: o raio, a borda e a sombra continuam os do tema —
    são eles que dão a identidade do balão, e um raio menor deixaria as
    caixas retangulares. A fonte não vem daqui: ela é do preset, em
    :func:`card_title_font`.
    """

    scale = size.text_scale

    return replace(
        theme,
        padding_x=max(1, round(CARD_PADDING_X * scale)),
        padding_y=max(1, round(CARD_PADDING_Y * scale)),
        line_gap=max(0, round(CARD_LINE_GAP * scale)),
    )


@lru_cache(maxsize=64)
def card_height(font_family: str, title_px: int, subtitle_px: int,
                padding_y: int, line_gap: int) -> int:
    """Altura de um balão, medida com as fontes pedidas.

    Medir em pixels aqui e não no preset é deliberado: fonte só pode ser
    medida com o Qt de pé, e o preset é construído antes disso. A
    medição é cara e ``paintEvent`` roda a 10 Hz, daí o cache.
    """

    title = QFont(font_family, max(6, title_px))
    subtitle = QFont(font_family, max(6, subtitle_px))

    return (
        QFontMetrics(title).height()
        + max(0, line_gap)
        + QFontMetrics(subtitle).height()
        + padding_y * 2
    )


def card_height_for(theme: PetTheme, size: PetSize) -> int:
    """Altura de um balão empilhado, no tema e preset atuais."""

    card = card_theme(theme, size)

    return card_height(
        resolve_family(card.font_family),
        size.card_title_font,
        size.card_subtitle_font,
        card.padding_y,
        card.line_gap,
    )


def elide(metrics: QFontMetrics, text: str, width: int) -> str:
    """Texto cortado com reticências para caber em ``width``.

    Cortar é o comportamento certo aqui: a largura da coluna é fixa, e o
    que não cabe vira reticências em vez de uma caixa mais larga que
    empurraria as outras para fora do alinhamento.
    """

    if not text:
        return text

    if metrics.horizontalAdvance(text) <= width:
        return text

    return metrics.elidedText(text, Qt.ElideRight, max(1, width))


@dataclass(frozen=True, slots=True)
class BubbleLayout:
    """Retângulos prontos para pintar, já medidos."""

    rect: QRect
    title_rect: QRect
    subtitle_rect: QRect
    title_font: QFont
    subtitle_font: QFont


def card_layout(
    theme: PetTheme,
    size: PetSize,
    title: str,
    subtitle: str,
    rect: QRect,
) -> BubbleLayout:
    """Balão da grade: largura fixa, texto reticado.

    A largura é a do preset e não a do texto: é ela que alinha a grade.
    O que não couber é cortado com reticências por :func:`elide`.
    """

    card = card_theme(theme, size)

    bold = card_title_font(theme, size)
    regular = card_subtitle_font(theme, size)

    inner = max(1, rect.width() - card.padding_x * 2)

    title_height = QFontMetrics(bold).height()
    subtitle_height = QFontMetrics(regular).height()

    text_left = rect.left() + card.padding_x

    return BubbleLayout(
        rect=rect,
        title_rect=QRect(text_left, rect.top() + card.padding_y,
                         inner, title_height),
        subtitle_rect=QRect(
            text_left,
            rect.top() + card.padding_y + title_height + card.line_gap,
            inner,
            subtitle_height,
        ),
        title_font=bold,
        subtitle_font=regular,
    )


def card_rects(
    count: int,
    size: PetSize,
    theme: PetTheme,
    container_width: int,
    container_height: int,
) -> list[QRect]:
    """Posições de ``count`` balões **empilhados**, um acima do outro.

    Uma coluna só, todos com a mesma largura: é a pilha que diz "estas são
    as instâncias em ação", na ordem — a que espera resposta primeiro.

    A pilha é ancorada **embaixo**, logo acima da faixa do sprite, e não em
    cima da janela. A janela é de tamanho fixo (ver ``config.MAX_STACK``), e
    ancorar em cima faria cada balão adder, descer e subir junto com a
    lista; ancorando embaixo, o que sai da lista é espaço vazio em cima e
    o pet não se move.
    """

    if count < 1:
        return []

    height = card_height_for(theme, size)

    floor = max(BUBBLE_TOP_MARGIN,
                container_height - size.sprite_height)

    left = (container_width - size.card_width) // 2

    return [
        QRect(
            left,
            floor - count * height - (count - 1) * size.stack_gap
            + index * (height + size.stack_gap),
            size.card_width,
            height,
        )
        for index in range(count)
    ]


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
    *,
    accent: str | None = None,
) -> None:
    """Pinta sombra, fundo, borda e as duas linhas de texto.

    ``accent`` pinta só o título, e é o que marca "este balão espera
    resposta". O subtítulo continua na cor do tema: ele identifica a
    instância, e colorir os dois dias deixaria a grade colorida demais.

    O texto é reticado antes de ser desenhado, para nunca vazar da
    caixa: ``drawText`` não recorta, ele desenha por cima.
    """

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

    title = elide(QFontMetrics(layout.title_font), title,
                  layout.title_rect.width())
    subtitle = elide(QFontMetrics(layout.subtitle_font), subtitle,
                     layout.subtitle_rect.width())

    painter.setFont(layout.title_font)
    painter.setPen(QPen(QColor(accent or theme.title_color)))

    painter.drawText(layout.title_rect, alignment, title)

    painter.setFont(layout.subtitle_font)
    painter.setPen(QPen(QColor(theme.subtitle_color)))

    painter.drawText(layout.subtitle_rect, alignment, subtitle)


def paint_card(
    painter: QPainter,
    theme: PetTheme,
    size: PetSize,
    title: str,
    subtitle: str,
    rect: QRect,
    *,
    accent: str | None = None,
) -> BubbleLayout:
    """Mede e pinta um balão da grade; devolve a medição.

    Devolver o layout permite ao widget reaproveitar os retângulos — é o
    que o pulso de atenção usa para acender a borda certa.
    """

    layout = card_layout(theme, size, title, subtitle, rect)

    paint(painter, layout, card_theme(theme, size), title, subtitle,
          accent=accent)

    return layout
