"""Janela do pet: sprite, balão de status, arrasto e encerramento."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ..assets import FrameSource, SpriteSheet, resolve_frames
from ..config import (
    ACTION_ROW_BY_STATE,
    ANIMATION_INTERVAL_MS,
    BUBBLE_TOP_MARGIN,
    DEFAULT_ACTION_ROW,
    MAX_STACK,
    SCREEN_GAP_X,
    SCREEN_GAP_Y,
    SPRITE_MARGIN_BOTTOM,
)
from ..sizes import DEFAULT_SIZE_KEY, PetSize, get_size
from .attention import AttentionPulse
from ..states import (
    ACTION_COLOR,
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

#: Backends que não sabem manter uma janela na frente das outras. O
#: Wayland não tem z-order: ``xdg-shell`` não tem "manter acima" e o
#: plugin do Qt ignora ``Qt.WindowStaysOnTopHint``. O WSLg cai aqui — o
#: flag é aceito pelo Qt e nunca chega a ninguém.
PLATFORMS_WITHOUT_Z_ORDER = frozenset({"wayland"})


def always_on_top_supported(platform: str | None = None) -> bool:
    """Se o backend da tela consegue manter o pet à frente das janelas.

    Só o Wayland é descartado de cara. No X11 o flag vira
    ``_NET_WM_STATE_ABOVE``, e aí quem decide é o gerenciador de janelas:
    o do WSLg não anuncia o átomo em ``_NET_SUPPORTED`` e o ignora, mas
    um i3/mutter/kwin normal honra — então afirmar o contrário também
    seria mentira.

    O parâmetro existe para o teste não depender do backend real: a
    suíte roda em ``offscreen``, e o caso que importa é o ``wayland``.
    """

    name = QGuiApplication.platformName() if platform is None else platform

    return name not in PLATFORMS_WITHOUT_Z_ORDER


class PetRenderer(QWidget):
    """Widget transparente que desenha o sprite e os balões.

    Um balão por instância do opencode em ação, em grade de até três
    colunas. O **sprite é um só** e reage ao conjunto: o que precisa de
    resposta ganha o pulso, o resto é o sprite andando. Um pet por aba
    faria da mesa uma colônia de bichinhos — o que o usuário pediu foi
    saber *quantas* abas estão em ação e *qual* delas espera resposta.
    """

    def __init__(self, theme: PetTheme, size: PetSize | None = None) -> None:
        super().__init__()

        self.theme = theme

        #: Preset de tamanho ativo.
        self.size = size or get_size(DEFAULT_SIZE_KEY)

        self.state = "connecting"

        #: Balões a desenhar: ``(título do estado, nome da instância,
        #: precisa_de_resposta)``. É uma tupla de strings e um booleano
        #: em vez de um objeto do opencode porque o widget não deve
        #: depender do modelo — quem decide o que é uma instância é
        #: :mod:`petwatch.sessions`.
        self.cards: list[tuple[str, str, bool]] = []

        #: Contador do bob vertical.
        self.animation_frame = 0

        #: Quadro atual da spritesheet.
        self.sprite_frame = 0

        #: Intervalo de quadros animado. Segue a linha do estado atual,
        #: a menos que o tema force um laço próprio.
        self._loop: tuple[int, int] = (0, 0)

        #: Pulso de "precisa de você"; só roda no estado de espera.
        self.attention = AttentionPulse(self)

        self.source: FrameSource | None = None

        #: Arquivo de onde o sprite veio, já filtrado pelos decodificadores.
        self.loaded_asset: Path | None = None

        self.load_asset()

        self._loop = (
            self.theme.loop
            if self.theme.loop is not None
            else self.source.loop_range if self.source else (0, 0)
        )

        self.sprite_frame = self._loop[0]

        self.apply_action_row()

        self.announce_asset()

        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # sem entrada na barra de tarefas / Alt-Tab; o
                       # acesso fica pelo QSystemTrayIcon (ver ui/tray.py)
        )

        self.resize(
            self.size.window_width_for(),
            self.window_height_for(self.stack_capacity),
        )

        # --------------------------------------------------------
        # Animação
        # --------------------------------------------------------

        self.timer = QTimer(self)

        self.timer.timeout.connect(self.animate)

        self.timer.start(ANIMATION_INTERVAL_MS)

    # ------------------------------------------------------------
    # Tamanho e tema
    # ------------------------------------------------------------

    def set_size(self, size: PetSize) -> None:
        """Troca o preset de tamanho, mantendo o canto da tela fixo.

        A janela muda de tamanho aqui — é uma troca de preset, dita pelo
        usuário — mas num passo só, com a geometria final. Redimensionar
        e depois mover deixa o compositor ver um estado intermediário em
        que a janela cresceu para baixo, e é nesse estado que o pet anda.
        """

        self.size = size

        width = size.window_width_for()
        height = self.window_height_for(len(self.cards))

        # O pulso é medido em pixels absolutos; sem isso o salto de 5px
        # seria proporcionalmente enorme num pet pequeno.
        self.attention.set_amplitude(size.text_scale)

        anchor = self._corner(width, height)

        if anchor is None:
            self.resize(width, height)

        else:
            self.setGeometry(*anchor, width, height)

        self.update()

    # ------------------------------------------------------------
    # Geometria da grade
    # ------------------------------------------------------------

    def window_height_for(self, count: int) -> int:
        """Altura da janela para ``count`` balões empilhados.

        A janela é dimensionada **uma vez**, para a capacidade da pilha
        (:data:`petwatch.config.MAX_STACK`), e nunca mais muda enquanto o
        preset for o mesmo. Isso não é economia de pixels: no Wayland o
        compositor decide onde a superfície fica e o ``move()`` do cliente
        é ignorado, então **qualquer** ``resize()`` reposiciona o pet. Era o
        que o usuário via: um balão a mais, e o pet subia ou descia em
        relação ao resto da tela.

        Ficar com menos balões deixa espaço transparente em cima, porque a
        pilha é ancorada embaixo (ver :func:`bubble.card_rects`).

        Passando da capacidade, a janela cresce de verdade — e o pet se
        move. É o mal menor: a outra opção era esconder uma instância.
        """

        card_h = bubble.card_height_for(self.theme, self.size)

        shown = max(count, self.stack_capacity)

        stack = shown * card_h + max(0, shown - 1) * self.size.stack_gap

        return BUBBLE_TOP_MARGIN + stack + self.size.sprite_height + SPRITE_MARGIN_BOTTOM

    @property
    def stack_capacity(self) -> int:
        """Quantos balões a janela comporta sem ter que crescer."""

        return MAX_STACK

    def card_rects(self, count: int) -> list:
        """Posições dos ``count`` balões na janela atual."""

        return bubble.card_rects(
            count, self.size, self.theme, self.width(), self.height(),
        )

    def screen_anchor(self) -> tuple[int, int] | None:
        """Posição do canto inferior direito com a folga configurada.

        Calculado a partir do tamanho **atual** do widget. Para um
        preset ainda não aplicado, use os números dele.
        """

        return self._corner(self.width(), self.height())

    def _corner(self, width: int, height: int) -> tuple[int, int] | None:
        screen = self.screen()

        if screen is None:
            return None

        geometry = screen.availableGeometry()

        return (
            geometry.right() - width - SCREEN_GAP_X,
            geometry.bottom() - height - SCREEN_GAP_Y,
        )

    def move_to_screen_corner(self) -> bool:
        """Reposiciona no canto da tela; devolve ``False`` sem tela."""

        target = self._corner(self.width(), self.height())

        if target is None:
            return False

        self.move(*target)

        return True

    def apply_theme(self, theme: PetTheme) -> None:
        """Troca o pet em tempo real, sem perder tamanho nem posição."""

        self.theme = theme

        self.load_asset()

        self._loop = (
            self.theme.loop
            if self.theme.loop is not None
            else self.source.loop_range if self.source else (0, 0)
        )

        self.apply_action_row()

        self.announce_asset()

        # O tamanho não muda ao trocar de pet, mas o canto da tela é
        # reaplicado para o widget não sair de lugar se a janela não
        # estava visível durante a troca.
        self.move_to_screen_corner()

        self.update()

    # ------------------------------------------------------------
    # Asset
    # ------------------------------------------------------------

    def load_asset(self) -> None:
        """Carrega o primeiro asset do tema que o Qt consiga decodificar."""

        self.source, self.loaded_asset = resolve_frames(
            self.theme.directory,
            self.theme.asset_path,
            self.theme.grid,
            self.theme.loop,
        )

    def announce_asset(self) -> None:
        """Loga o que está no ar: arquivo, laço e tamanho da sequência.

        Fica separado de :meth:`load_asset` porque o laço só fica
        definitivo depois de :meth:`apply_action_row`. Logar dentro do
        carregamento imprimia sempre ``anima 0..0`` — o valor inicial do
        atributo — e levava à conclusão errada de que nada anima.
        """

        if self.loaded_asset is None:
            return

        first, last = self.loop_range

        log.info(
            "[pet] %s usando %s (anima %d..%d de %d)",
            self.theme.name,
            self.loaded_asset.name,
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

        return self._loop

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

    @Slot(str)
    def set_state(self, state: str) -> None:
        """Vira o balão para ``state``.

        decorated com ``@Slot`` de propósito: o sinal vem da thread do
        monitor, e só um ``Slot`` de verdade diz ao PySide6 qual
        ``QObject`` receptor é este — sem isso ele não descobre a thread
        dona e a entrega enfileirada ia para a thread do monitor, que
        não tem event loop, e o estado nunca chegava ao balão.
        """

        if state not in VALID_STATES:
            return

        self.state = state

        self.apply_action_row()

        if state == STATE_WAITING:
            self.attention.start()

        else:
            self.attention.stop()

        self.update()

    @Slot(object)
    def set_cards(self, cards: object) -> None:
        """Recebe a lista de balões: ``(estado, nome, espera_resposta)``.

        A lista é usada como veio: **nenhuma instância é escondida**. A
        janela só muda de tamanho quando a pilha passa da capacidade — ver
        :meth:`window_height_for` — porque mudar de tamanho faz o pet se
        mover, e o pet não pode se mover.
        """

        self.cards = list(cards or [])

        height = self.window_height_for(len(self.cards))

        if height != self.height():
            log.warning(
                "[pet] %d balões passam da capacidade de %d; "
                "a janela vai crescer e o pet se move",
                len(self.cards), self.stack_capacity,
            )

            # Um passo só, com a geometria final: ``resize()`` seguido de
            # ``move()`` deixa o compositor ver um estado intermediário em
            # que a janela cresceu para baixo, e é nele que o pet anda.
            anchor = self._corner(self.width(), height)

            if anchor is not None:
                self.setGeometry(*anchor, self.width(), height)
            else:
                self.resize(self.width(), height)

        self.update()

    # ------------------------------------------------------------
    # Ação da spritesheet
    # ------------------------------------------------------------

    def apply_action_row(self) -> None:
        """Aponta o sprite para a linha da spritesheet deste estado.

        Só faz alguma coisa quando o tema não força um laço próprio: um
        ``frames.first/last`` explícito vale para todos os estados. E
        ``row_loop`` devolve ``None`` para linhas vazias ou ausentes, caso
        em que o laço detectado continua valendo.
        """

        if self.theme.loop is not None:
            return

        if not isinstance(self.source, SpriteSheet):
            return

        row = ACTION_ROW_BY_STATE.get(self.state, DEFAULT_ACTION_ROW)

        loop = self.source.row_loop(row)

        if loop is not None:
            self._loop = loop
            self.sprite_frame = loop[0]

    # ------------------------------------------------------------
    # Animação
    # ------------------------------------------------------------

    def animate(self) -> None:
        self.animation_frame += 1

        self.advance_sprite()

        # O sprite só aparece na tela se a widget pedir repaint. Sem isto
        # o quadro e o bob andam só nos contadores e a janela fica
        # congelada num único quadro — o pet para de se mexer.
        #
        # O pulso não é uma exceção: ele traz repaints próprios (via
        # ``progress_changed``), o que é exatamente o que escondia o
        # problema — em ``waiting`` a janela continuava viva, nos outros
        # estados não.
        self.update()

    def advance_sprite(self) -> None:
        """Move o sprite um passo dentro do laço de animação."""

        if self.source is None:
            return

        first, last = self._loop
        span = last - first + 1

        if span < 2:
            return

        offset = self.sprite_frame - first + self.frame_step

        self.sprite_frame = first + offset % span

    def set_always_on_top(self, enabled: bool) -> None:
        """Liga ou desliga o flag "sempre no topo".

        Mudar um WindowFlag esconde e recria a janela nativa, então
        ``show()`` é preciso para o novo valor valer.
        """

        enabled = bool(enabled)

        # A comparação precisa do ``bool`` em volta da máscara: em Python
        # ``&`` liga mais forte que ``==``, e o valor do hint é
        # ``0x40000``. Comparado com ``True`` direto, o resultado nunca
        # batia, a guarda nunca segurava e o ``show()`` abaixo recriava a
        # janela nativa toda vez que o estado era ligado de novo. Era o
        # que fazia o app parecer estar forçando o flag sem conseguir.
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) == enabled:
            return

        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)

        self.show()

    # ------------------------------------------------------------
    # Balões
    # ------------------------------------------------------------

    def draw_status(self, painter: QPainter) -> None:
        """Pinta um balão por instância em ação, empilhados um abaixo do outro.

        Cada balão tem o **estado** como título e o **nome da
        instância** como subtítulo. Só o título de quem precisa de
        resposta recebe a cor de destaque; os outros ficam com a cor do
        tema, porque pintar todos transformaria a tela num painel de
        status em vez de num aviso.

        Sem nenhuma instância conhecida — servidor fora do ar, ainda
        conectando — cai no balão único com o estado geral, que é o que o
        pet mostrava antes.
        """

        if not self.theme.text_enabled:
            return

        # ``(estado, nome, espera_resposta)``. O estado dá o título; o
        # nome da instância ocupa a segunda linha, no lugar do subtítulo
        # do balão único — é ela que diz *de quem* é o balão.
        cards = self.cards or [(self.state, "", self.state == STATE_WAITING)]

        texts = [
            (labels_for(state)[0], name)
            for state, name, _ in cards
        ]

        # Estado mudo: nem o texto nem a caixa. Pintar o balão vazio
        # deixaria um retângulo claro no alto do sprite sem nenhuma letra
        # dentro, que parece defeito — e o ``connecting``, justamente o
        # estado em que a janela acabou de abrir, não tem nada a dizer.
        if not any(title or subtitle for (title, subtitle), _ in
                   zip(texts, cards)):
            return

        rects = self.card_rects(len(cards))

        for ((title, subtitle), (_, _, needs_action), rect) in zip(
            texts, cards, rects,
        ):
            layout = bubble.paint_card(
                painter,
                self.theme,
                self.size,
                title,
                subtitle,
                rect,
                accent=ACTION_COLOR if needs_action else None,
            )

            # Contorno pulsante por cima, só no balão que espera algo.
            if needs_action:
                self.attention.decorate(
                    painter, layout,
                    bubble.card_theme(self.theme, self.size),
                )

    # ------------------------------------------------------------
    # Sprite
    # ------------------------------------------------------------

    def bob_offset(self) -> int:
        """Deslocamento vertical conforme o estado.

        O pulso de atenção soma ao bob: no estado de espera o sprite sobe
        um pouco a cada repetição, o que chama o olho sem mover a janela.
        """

        frame = self.animation_frame

        if self.state == STATE_IDLE:
            base = 2 if frame % 8 < 4 else 0

        elif self.state == STATE_WORKING:
            base = -2 if frame % 4 < 2 else 2

        elif self.state == STATE_WAITING:
            base = 1

        else:
            base = 0

        return base - round(self.attention.sprite_lift())

    def scaled_pixmap(self) -> QPixmap | None:
        image = self.current_image()

        if image is None:
            return None

        pixmap = QPixmap.fromImage(image)

        if pixmap.isNull():
            return None

        # Mantém proporção.
        return pixmap.scaled(
            int(self.size.sprite_width * self.theme.scale),
            int(self.size.sprite_height * self.theme.scale),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def draw_pet(self, painter: QPainter) -> None:
        """Desenha o sprite no rodapé, centrado sob a pilha de balões.

        Com os balões empilhados numa coluna, a janela tem a largura de um
        balão: centralizar deixa o pet no meio do queixo, e o canto da tela
        continua a distância de sempre, porque é a janela que está
        ancorada nele.
        """

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
            # O menu é montado pelo PetApplication; aqui só pedimos.
            menu = getattr(self, "menu", None)

            if menu is not None:
                menu.exec(event.globalPosition().toPoint())

            event.accept()

            return

        event.ignore()
