"""Widget do pet: geometria, estados, animacao e balão."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    Qt,
    QTimer,
)
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import (  # noqa: E402
    BUBBLE_TOP_MARGIN,
    SPRITE_MARGIN_BOTTOM,
)
from petwatch.sizes import PET_SIZES  # noqa: E402
from petwatch.states import (  # noqa: E402
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_LABELS,
    STATE_WAITING,
    STATE_WORKING,
    VALID_STATES,
    labels_for,
)
from petwatch.theme import load_theme  # noqa: E402
from petwatch.ui import (  # noqa: E402
    PetRenderer,  # noqa: E402
    bubble,  # noqa: E402
)

# O preset "medio" e o tamanho de referencia dos testes de geometria.
MEDIUM = PET_SIZES["medium"]
MEDIUM_WIDTH = MEDIUM.window_width_for()


class PetRendererTests(unittest.TestCase):
    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)

        # O timer de animação altera animation_frame sozinho; os testes
        # de bob precisam controlar o quadro.
        self.widget.timer.stop()

    def tearDown(self):
        self.widget.timer.stop()
        self.widget.close()
        self.theme.scale = 1.0
        self.theme.text_enabled = True

    def test_window_geometry_comes_from_the_preset(self):
        self.assertEqual(self.widget.width(), self.widget.size.window_width_for())
        self.assertEqual(self.widget.height(), self.widget.window_height_for(0))

    def test_the_window_is_one_balloon_wide(self):
        """A janela tem a largura de **um** balão, não da grade inteira.

        Balões empilhados não precisam de largura para as colunas: 152px
        no preset médio, contra os 472px que três colunas
        exigia. Só a altura acompanha a quantidade.
        """

        self.assertEqual(self.widget.width(), MEDIUM.card_width)
        self.assertGreater(self.widget.width(), MEDIUM.sprite_width)

    def test_the_sprite_stays_mascot_sized(self):
        pixmap = self.widget.scaled_pixmap()

        self.assertLessEqual(pixmap.width(), 160)
        self.assertLessEqual(pixmap.height(), 160)

    def test_starts_connecting_with_a_sprite(self):
        self.assertEqual(self.widget.state, STATE_CONNECTING)
        self.assertIsNotNone(self.widget.source)
        self.assertIsNotNone(self.widget.current_image())

    def test_sprite_scales_keeping_aspect_ratio(self):
        pixmap = self.widget.scaled_pixmap()

        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertLessEqual(pixmap.width(), self.widget.size.sprite_width)
        self.assertLessEqual(pixmap.height(), self.widget.size.sprite_height)

    def test_sprite_is_mascot_sized(self):
        pixmap = self.widget.scaled_pixmap()

        # Não muito menor que ~100px (some) nem maior que ~150px (gigante).
        self.assertGreaterEqual(pixmap.height(), 100)
        self.assertLessEqual(pixmap.height(), 150)

    def test_scale_shrinks_the_sprite(self):
        self.theme.scale = 0.5
        small = self.widget.scaled_pixmap()

        self.theme.scale = 1.0
        full = self.widget.scaled_pixmap()

        self.assertLess(small.width(), full.width())

    def test_set_state_accepts_known_states(self):
        for state in VALID_STATES:
            with self.subTest(state=state):
                self.widget.set_state(state)
                self.assertEqual(self.widget.state, state)

    def test_set_state_ignores_unknown_state(self):
        self.widget.set_state(STATE_IDLE)
        self.widget.set_state("nao-existe")

        self.assertEqual(self.widget.state, STATE_IDLE)

    def test_animate_increments_frame(self):
        before = self.widget.animation_frame

        self.widget.animate()

        self.assertEqual(self.widget.animation_frame, before + 1)

    def test_bob_depends_on_state_and_frame(self):
        def bob_over(state: str, frames: int) -> list[int]:
            self.widget.set_state(state)

            offsets = []

            for frame in range(frames):
                self.widget.animation_frame = frame
                offsets.append(self.widget.bob_offset())

            return offsets

        self.assertEqual(bob_over(STATE_IDLE, 8), [2, 2, 2, 2, 0, 0, 0, 0])
        self.assertEqual(bob_over(STATE_WORKING, 4), [-2, -2, 2, 2])
        self.assertEqual(bob_over(STATE_WAITING, 4), [1, 1, 1, 1])
        self.assertEqual(bob_over(STATE_CONNECTING, 4), [0, 0, 0, 0])


class SpriteAnimationTests(unittest.TestCase):
    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)
        self.widget.timer.stop()

    def tearDown(self):
        self.widget.timer.stop()
        self.widget.close()

    def test_loop_starts_at_first_frame_of_the_loop(self):
        first, _ = self.widget.loop_range

        self.assertEqual(self.widget.sprite_frame, first)

    def test_advance_cycles_inside_the_loop(self):
        first, last = self.widget.loop_range
        span = last - first + 1

        seen = []
        for _ in range(span * 2):
            seen.append(self.widget.sprite_frame)
            self.widget.advance_sprite()

        self.assertEqual(seen[:span], list(range(first, last + 1)))
        self.assertEqual(seen[span:], seen[:span])

    def test_advance_never_leaves_the_loop(self):
        first, last = self.widget.loop_range

        for _ in range(200):
            self.widget.advance_sprite()

            self.assertGreaterEqual(self.widget.sprite_frame, first)
            self.assertLessEqual(self.widget.sprite_frame, last)

    def test_idle_uses_the_first_action_row(self):
        self.widget.set_state(STATE_IDLE)

        self.assertEqual(self.widget.loop_range, (0, 5))

    def test_each_state_uses_its_own_row(self):
        expected = {
            STATE_IDLE: (0, 5),
            STATE_WORKING: (8, 15),
            STATE_WAITING: (24, 27),
            STATE_CONNECTING: (56, 61),
        }

        for state, loop in expected.items():
            with self.subTest(state=state):
                self.widget.set_state(state)

                self.assertEqual(self.widget.loop_range, loop)

    def test_changing_state_repositions_the_frame(self):
        self.widget.set_state(STATE_WORKING)
        self.widget.advance_sprite()
        self.assertNotEqual(self.widget.sprite_frame, 8)

        self.widget.set_state(STATE_WAITING)

        # Comeca no primeiro quadro da nova linha, nao no meio da antiga.
        self.assertEqual(self.widget.sprite_frame, 24)

    def test_frame_never_escapes_the_current_row(self):
        for state in (STATE_IDLE, STATE_WORKING, STATE_WAITING, STATE_CONNECTING):
            with self.subTest(state=state):
                self.widget.set_state(state)

                first, last = self.widget.loop_range

                for _ in range(50):
                    self.widget.advance_sprite()

                    self.assertGreaterEqual(self.widget.sprite_frame, first)
                    self.assertLessEqual(self.widget.sprite_frame, last)

    def test_explicit_loop_in_theme_wins(self):
        """``frames.first/last`` vale para todos os estados."""

        from petwatch.theme import PetTheme

        theme = PetTheme.from_directory(self.theme.directory)
        theme.loop = (16, 23)

        widget = PetRenderer(theme)
        widget.timer.stop()

        try:
            for state in (STATE_IDLE, STATE_WORKING, STATE_WAITING):
                with self.subTest(state=state):
                    widget.set_state(state)

                    self.assertEqual(widget.loop_range, (16, 23))
        finally:
            widget.timer.stop()
            widget.close()

    def test_unknown_state_row_falls_back_to_detected_loop(self):
        self.widget.set_state(STATE_IDLE)

        # Simula um estado fora do mapa: volta ao laço detectado.
        self.widget.state = "inexistente"
        self.widget.apply_action_row()

        self.assertEqual(self.widget.loop_range, (0, 5))

    def test_frame_step_is_at_least_one(self):
        self.assertGreaterEqual(self.widget.frame_step, 1)


class StateLabelsTests(unittest.TestCase):
    def test_every_labelled_state_has_two_lines(self):
        for state in VALID_STATES:
            with self.subTest(state=state):
                title, subtitle = labels_for(state)

                # ``connecting`` é a exceção de propósito: ver abaixo.
                if state == STATE_CONNECTING:
                    continue

                self.assertTrue(title)
                self.assertTrue(subtitle)

    def test_connecting_says_nothing(self):
        """Conectando não tem o que anunciar: o balão inteiro fica de fora."""

        self.assertEqual(labels_for(STATE_CONNECTING), ("", ""))

    def test_unknown_state_is_blank(self):
        self.assertEqual(labels_for("nao-existe"), ("", ""))

    def test_all_states_are_labelled(self):
        self.assertEqual(set(STATE_LABELS), VALID_STATES)


class CardLayoutTests(unittest.TestCase):
    """A medição do balão empilhado: largura fixa, duas linhas."""

    @classmethod
    def setUpClass(cls):
        cls.theme = load_theme("eevee")

        # A janela real do preset médio: é nela que a pilha é ancorada.
        cls.height = PetRenderer(cls.theme, MEDIUM).height()

    def layout(self, title: str = "Thinking", subtitle: str = "pet-opencodev2"):
        size = MEDIUM

        rect = bubble.card_layout(
            self.theme, size, title, subtitle,
            bubble.card_rects(1, size, self.theme, MEDIUM_WIDTH,
                              self.height)[0],
        )

        return rect, size

    def test_two_lines_are_stacked_in_order(self):
        layout, _ = self.layout()

        self.assertLess(layout.title_rect.top(), layout.subtitle_rect.top())
        self.assertEqual(layout.title_rect.left(), layout.subtitle_rect.left())

    def test_title_font_is_bold_and_subtitle_is_not(self):
        layout, _ = self.layout()

        self.assertGreater(layout.title_font.weight(),
                           layout.subtitle_font.weight())
        self.assertEqual(layout.title_font.family(),
                         layout.subtitle_font.family())

    def test_title_is_at_least_as_large_as_subtitle(self):
        layout, _ = self.layout()

        self.assertGreaterEqual(layout.title_font.pointSize(),
                                layout.subtitle_font.pointSize())

    def test_the_width_is_the_preset_not_the_text(self):
        """Largura fixa é o que empilha os balões alinhados."""

        layout, size = self.layout()

        self.assertEqual(layout.rect.width(), size.card_width)

        # E continua fixa com um texto enorme.
        long_layout, _ = self.layout(subtitle="x" * 400)

        self.assertEqual(long_layout.rect.width(), layout.rect.width())

    def test_vertical_stack_accounts_for_gap_and_padding(self):
        layout, size = self.layout()

        card = bubble.card_theme(self.theme, size)

        expected = (
            layout.title_rect.height()
            + card.line_gap
            + layout.subtitle_rect.height()
            + card.padding_y * 2
        )

        self.assertEqual(layout.rect.height(), expected)

    def test_text_starts_at_padding_from_edges(self):
        layout, size = self.layout()

        card = bubble.card_theme(self.theme, size)

        self.assertEqual(layout.title_rect.left(),
                         layout.rect.left() + card.padding_x)
        self.assertEqual(layout.title_rect.top(),
                         layout.rect.top() + card.padding_y)

    def test_the_stack_is_anchored_above_the_sprite(self):
        """O balão fica logo acima do sprite, e não no topo da janela.

        A janela é de tamanho fixo (ver ``config.MAX_STACK``); ancorar a
        pilha no topo faria cada balão subir e descer junto com a lista.
        """

        layout, size = self.layout()

        self.assertLessEqual(
            layout.rect.bottom(),
            self.height - size.sprite_height,
        )

        # Com um balão só, ele fica colado no sprite: é o desenho original.
        self.assertEqual(
            self.height - size.sprite_height - layout.rect.bottom(), 1,
        )

    def test_the_first_balloon_is_centered(self):
        layout, _ = self.layout()

        self.assertLessEqual(
            abs(layout.rect.center().x() - MEDIUM_WIDTH // 2), 1,
        )

    def test_text_gets_the_inner_width(self):
        layout, size = self.layout()

        card = bubble.card_theme(self.theme, size)

        self.assertEqual(layout.title_rect.width(),
                         layout.rect.width() - card.padding_x * 2)

    def test_the_family_falls_back_to_one_that_exists(self):
        from petwatch.ui.bubble import available_families, resolve_family

        available = available_families()

        if not available:
            self.skipTest("sem fontes no sistema")

        name = sorted(available)[0]

        self.assertEqual(resolve_family(name), name)

    def test_an_unavailable_family_falls_back_to_a_real_one(self):
        from petwatch.ui.bubble import available_families, resolve_family

        available = available_families()

        if not available:
            self.skipTest("sem fontes no sistema")

        self.assertIn(resolve_family("fonte-que-nao-existe"), available)


class RenderTests(unittest.TestCase):
    """Verifica a pintura de verdade, rasterizando a janela."""

    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)
        self.widget.timer.stop()
        self.theme.text_enabled = False  # isola o sprite do balao

    def tearDown(self):
        self.widget.timer.stop()
        self.widget.close()
        self.theme.text_enabled = True

    def opaque_rows(self, skip_bubble: bool = False) -> range:
        """Linhas do render que tem algum pixel visivel."""

        image = self.widget.grab().toImage()

        start = 0
        if skip_bubble:
            # O topo do bloco de balões, seja qual for o número deles.
            rects = self.widget.card_rects(max(1, len(self.widget.cards)))
            start = rects[-1].bottom() + 4

        rows = [
            y
            for y in range(start, image.height())
            if any(
                image.pixelColor(x, y).alpha() > 0
                for x in range(image.width())
            )
        ]

        return range(rows[0], rows[-1] + 1) if rows else range(0)

    def opaque_pixels(self) -> int:
        """Quantos pixels do render têm algo visível.

        Conta em vez de comparar imagens: um balão sumido é diferença de
        pixels, e é isso que o teste quer saber.
        """

        image = self.widget.grab().toImage()

        return sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )

    def test_sprite_sits_above_the_bottom_margin(self):
        pixmap = self.widget.scaled_pixmap()
        bob = self.widget.bob_offset()

        expected_top = (
            self.widget.height() - pixmap.height() - SPRITE_MARGIN_BOTTOM + bob
        )
        expected_bottom = expected_top + pixmap.height()

        rows = self.opaque_rows()

        self.assertTrue(len(rows) > 0, "nada foi pintado")

        # O sprite fica dentro da caixa esperada e acima da margem
        # inferior. Pode ser menor que a caixa porque o asset tem
        # bordas transparentes.
        self.assertGreaterEqual(rows[0], expected_top)
        self.assertLess(rows.stop, expected_bottom)

    def test_bottom_margin_is_empty(self):
        self.widget.set_state(STATE_IDLE)

        rows = self.opaque_rows()

        empty_rows = self.widget.height() - rows.stop

        self.assertGreaterEqual(empty_rows, SPRITE_MARGIN_BOTTOM)

    def test_bob_moves_the_sprite_vertically(self):
        self.widget.set_state(STATE_IDLE)
        self.widget.animation_frame = 0
        idle_top = self.opaque_rows()[0]

        self.widget.set_state(STATE_WORKING)
        self.widget.animation_frame = 0
        working_top = self.opaque_rows()[0]

        # idle bob = 2, working bob = -2: quatro pixels acima.
        self.assertEqual(working_top, idle_top - 4)

    def test_bubble_is_drawn_when_enabled(self):
        self.widget.set_state(STATE_IDLE)

        self.theme.text_enabled = False
        without = len(self.opaque_rows())

        self.theme.text_enabled = True
        with_bubble = len(self.opaque_rows())

        self.assertGreater(with_bubble, without)

    def test_connecting_draws_no_bubble_at_all(self):
        """Sem texto nenhum: nem a caixa, nem o pixel.

        O estado inicial é ``connecting``, então é ele que aparece sozinho
        na tela logo depois de abrir. Um retângulo vazio ali seria a
        primeira coisa que o usuário vê.
        """

        self.assertEqual(self.widget.state, STATE_CONNECTING)

        self.theme.text_enabled = False
        without_text = self.opaque_pixels()

        self.theme.text_enabled = True
        with_text = self.opaque_pixels()

        self.assertEqual(with_text, without_text)

        # O sprite continua lá: o que some é o balão, não o pet.
        self.assertGreater(with_text, 0)

    def test_every_state_but_connecting_paints_a_bubble(self):
        for state in (STATE_IDLE, STATE_WORKING, STATE_WAITING):
            with self.subTest(state=state):
                self.widget.set_state(state)

                self.theme.text_enabled = True
                with_text = self.opaque_pixels()

                self.theme.text_enabled = False
                without_text = self.opaque_pixels()

                self.assertGreater(with_text, without_text)

    def test_bubble_does_not_collide_with_the_sprite(self):
        """A grade inteira fica acima do sprite.

        A medição é a do balão **da grade**, não a do balão único: com
        uma caixa de largura fixa e fonte menor, o bloco é outro, e o
        teste precisa olhar para o que é desenhado.
        """

        self.theme.text_enabled = True

        self.widget.set_state(STATE_WORKING)

        rects = self.widget.card_rects(1)

        rows = self.opaque_rows(skip_bubble=True)

        self.assertGreater(rows[0], rects[0].bottom())


class AssetLogTests(unittest.TestCase):
    """O log precisa dizer qual laço entrou no ar.

    Diagnosticar "o pet não anima" começa pelo log, então uma mensagem
    ``anima 0..0`` mente: parece não haver quadros, quando o problema era
    outro (ver bug 11, em que o laço estava certo e faltava o repaint).
    """

    def build(self) -> PetRenderer:
        widget = PetRenderer(load_theme("eevee"))

        self.addCleanup(lambda: (widget.timer.stop(), widget.close()))

        return widget

    def reported_loop(self, widget: PetRenderer) -> tuple[int, int]:
        with self.assertLogs("petwatch.ui.pet_widget", level="INFO") as captured:
            widget.announce_asset()

        message = next(
            record.getMessage()
            for record in captured.records
            if "anima" in record.getMessage()
        )

        # "(anima 0..5 de 72)" -> (0, 5)
        loop = message.split("anima ")[1].split(")")[0].split(" de ")[0]

        first, last = loop.split("..")

        return int(first), int(last)

    def test_the_log_reports_the_real_loop(self):
        first, last = self.reported_loop(self.build())

        # A linha de ação do estado inicial tem mais de um quadro.
        self.assertLess(first, last)

    def test_the_log_matches_the_widget_loop(self):
        widget = self.build()

        self.assertEqual(self.reported_loop(widget), widget.loop_range)


class WindowFlagsTests(unittest.TestCase):
    def test_translucent_always_on_top_and_tool(self):
        widget = PetRenderer(load_theme("eevee"))

        # Fechar o widget destroi o QTimer filiado, entao a limpeza
        # precisa parar o timer antes.
        self.addCleanup(lambda: (widget.timer.stop(), widget.close()))

        self.assertTrue(widget.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(widget.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(widget.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertTrue(widget.windowFlags() & Qt.Tool)


class RepaintCounter(QObject):
    """Conta os ``QEvent.Paint`` recebidos por um widget."""

    def __init__(self) -> None:
        super().__init__()

        self.paints = 0

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Paint:
            self.paints += 1

        return False


class RepaintTests(unittest.TestCase):
    """O timer de animação precisa pedir repaint a cada tique.

    Contador e bob podem mudar à vontade: se a widget não pedir repaint,
    nada disso chega à tela e a janela fica congelada num único quadro.
    """

    def setUp(self):
        self.widget = PetRenderer(load_theme("eevee"))
        self.widget.timer.stop()

        self.addCleanup(lambda: (self.widget.timer.stop(), self.widget.close()))

    def test_animate_asks_for_a_repaint_in_every_state(self):
        for state in VALID_STATES:
            with self.subTest(state=state):
                self.widget.set_state(state)

                # O pulso tem repaint próprio; zerar isola o do sprite.
                self.widget.attention.set_progress(0.0)

                repaints = []
                self.widget.update = lambda r=repaints: r.append(True)

                self.widget.animate()

                self.assertEqual(len(repaints), 1)

    def test_animate_repaints_without_a_sprite_too(self):
        """Sem laço de animação o bob ainda mexe, então ainda há o que pintar."""

        self.widget.source = None
        self.widget.set_state(STATE_IDLE)

        repaints = []
        self.widget.update = lambda: repaints.append(True)

        self.widget.animate()

        self.assertEqual(len(repaints), 1)

    def test_the_window_really_repaints_while_the_timer_runs(self):
        """Pintura de verdade: event loop rodando e ``Paint`` chegando."""

        self.widget.show()

        counter = RepaintCounter()
        self.widget.installEventFilter(counter)

        self.widget.timer.start()

        # 600ms cobrem seis tiques do timer de 100ms.
        QTimer.singleShot(600, _app.quit)
        _app.exec()

        self.assertGreaterEqual(counter.paints, 3)

        # E o quadro realmente andou: paints distintos, nao o mesmo
        # quadro repintado.
        self.assertGreater(self.widget.animation_frame, 1)

    def test_no_paint_without_a_timer_tick(self):
        """Sanidade do teste acima: parado, o widget nao se repinta sozinho."""

        self.widget.show()

        counter = RepaintCounter()
        self.widget.installEventFilter(counter)

        _app.processEvents()

        paints_after_expose = counter.paints

        QTimer.singleShot(600, _app.quit)
        _app.exec()

        self.assertEqual(counter.paints, paints_after_expose)


if __name__ == "__main__":
    unittest.main()


class CardStackTests(unittest.TestCase):
    """Os balões são empilhados: um acima do outro, consecutivos."""

    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)
        self.widget.timer.stop()
        self.addCleanup(lambda: (self.widget.timer.stop(), self.widget.close()))

    def cards(self, count, state=STATE_WORKING, flagged=False):
        return [(state, f"projeto-{index}", flagged) for index in range(count)]

    def image(self):
        return self.widget.grab().toImage()

    # ------------------------------------------------------------
    # Empilhamento
    # ------------------------------------------------------------

    def test_one_above_the_other(self):
        """Nenhuma linha tem dois balões lado a lado."""

        for count in range(1, 6):
            with self.subTest(count=count):
                rects = self.widget.card_rects(count)

                self.assertEqual(len(rects), count)

                for index in range(1, len(rects)):
                    previous, current = rects[index - 1], rects[index]

                    self.assertGreater(current.top(), previous.top())
                    self.assertEqual(current.left(), previous.left())
                    self.assertEqual(current.width(), previous.width())

    def test_the_stack_is_consecutive(self):
        """Sem buraco entre um balão e o outro: só a folga."""

        rects = self.widget.card_rects(3)

        for previous, current in zip(rects, rects[1:], strict=False):
            self.assertEqual(current.top() - previous.bottom() - 1,
                             self.widget.size.stack_gap)

    def test_every_card_has_the_same_width(self):
        """A largura é a do preset: é o que alinha a pilha."""

        rects = self.widget.card_rects(4)

        self.assertEqual({rect.width() for rect in rects},
                         {self.widget.size.card_width})

    def test_the_pile_is_not_wider_than_one_card(self):
        """Uma coluna: a janela não cresce para o lado."""

        rects = self.widget.card_rects(4)

        block = max(rect.right() for rect in rects) - min(
            rect.left() for rect in rects
        ) + 1

        self.assertEqual(block, self.widget.size.card_width)

    def test_the_card_is_centered(self):
        rects = self.widget.card_rects(2)

        self.assertLessEqual(
            abs(rects[0].center().x() - self.widget.width() // 2), 1,
        )

    def test_the_window_never_moves_or_resizes(self):
        """Balão a mais ou a menos não move o pet. **Nunca**.

        Este é o teste do sintoma: a janela mudava de tamanho quando o
        número de balões mudava, e no Wayland o compositor reposiciona a
        superfície — então o pet subia ou descia em relação à tela. Com a
        janela fixa, a geometria é idêntica sempre.
        """

        before = self.widget.geometry()
        sprite = self.sprite_global()

        for count in (1, 2, 3, 6, 0, 5):
            with self.subTest(count=count):
                self.widget.set_cards(self.cards(count))

                QCoreApplication.processEvents()

                self.assertEqual(self.widget.geometry(), before)
                self.assertEqual(self.sprite_global(), sprite)

    def sprite_global(self) -> tuple[int, int]:
        """Posição do sprite na tela — a do pet, de fato."""

        self.widget.animation_frame = 0

        pixmap = self.widget.scaled_pixmap()

        y = (
            self.widget.height()
            - pixmap.height()
            - SPRITE_MARGIN_BOTTOM
            + self.widget.bob_offset()
        )

        point = self.widget.mapToGlobal(QPoint(0, y))

        return point.x(), point.y()

    def test_the_window_grows_only_past_the_capacity(self):
        """Acima da capacidade a janela cresce — e o pet se move.

        É o mal menor entre as duas saídas ruins: a outra era esconder
        uma instância, que é pior do que o pet andar.
        """

        capacity = self.widget.stack_capacity

        before = self.widget.geometry()

        self.widget.set_cards(self.cards(capacity))

        self.assertEqual(self.widget.geometry(), before)

        self.widget.set_cards(self.cards(capacity + 2))

        self.assertNotEqual(self.widget.geometry(), before)
        self.assertEqual(len(self.widget.cards), capacity + 2)

    def test_no_card_is_ever_dropped(self):
        """A pilha mostra todas as instâncias, sem teto.

        Esconder uma seria o pior defeito possível aqui: a aba que some
        do balão é a que o usuário perdeu de vista.
        """

        many = self.cards(9)

        self.widget.set_cards(many)

        self.assertEqual(self.widget.cards, many)
        self.assertEqual(len(self.widget.card_rects(9)), 9)

        # E a janela é alta o bastante para todas elas.
        self.assertEqual(self.widget.height(),
                         self.widget.window_height_for(9))
        self.assertGreaterEqual(
            self.widget.card_rects(9)[-1].bottom(),
            self.widget.card_rects(8)[-1].bottom(),
        )

    def test_the_block_stays_inside_the_window(self):
        for count in range(0, 7):
            with self.subTest(count=count):
                self.widget.set_cards(self.cards(count))

                for rect in self.widget.card_rects(len(self.widget.cards)):
                    self.assertGreaterEqual(rect.left(), 0)
                    self.assertLessEqual(rect.right(), self.widget.width())
                    self.assertGreaterEqual(rect.top(), BUBBLE_TOP_MARGIN)
                    self.assertLess(
                        rect.bottom(),
                        self.widget.height() - self.widget.size.sprite_height,
                    )

    # ------------------------------------------------------------
    # Texto reticado
    # ------------------------------------------------------------

    def test_a_long_name_is_elided(self):
        from petwatch.ui.bubble import elide

        font = bubble.card_subtitle_font(self.theme, self.widget.size)

        metrics = QFontMetrics(font)

        long_name = "um-nome-de-projeto-bem-comprido-de-vez"

        cut = elide(metrics, long_name, 60)

        # O Qt corta com "…" (U+2026); visualmente é o "..." do enunciado.
        self.assertTrue(cut.endswith(("...", "…")), cut)
        self.assertLessEqual(metrics.horizontalAdvance(cut), 60)

    def test_a_short_name_is_kept_whole(self):
        from petwatch.ui.bubble import elide

        metrics = QFontMetrics(
            bubble.card_subtitle_font(self.theme, self.widget.size)
        )

        self.assertEqual(elide(metrics, "dd", 200), "dd")

    def test_text_never_spills_outside_the_card(self):
        """Nome enorme não pode pintar nada fora da caixa.

        Comparado por **diferença de render**, e não por alfa: a borda
        tem antisserrilhado e a sombra escurece as linhas de baixo, então
        um limiar de alfa mediria o desenho da caixa, não o texto. A
        pergunta certa é "o que mudou por causa do texto", e a resposta é
        só o interior do balão.
        """

        # Os dois renders precisam ter a mesma janela: trocar a
        # quantidade de balões muda a altura, e aí a comparação seria do
        # sprite deslocado, não do texto.
        self.widget.set_cards([(STATE_WORKING, "dd", False)])
        short = self.widget.grab().toImage()

        self.widget.set_cards([(STATE_WORKING, "x" * 300, False)])
        long_name = self.widget.grab().toImage()

        self.assertEqual(short.size(), long_name.size())

        rect = self.widget.card_rects(1)[0]

        outside = [
            (x, y)
            for y in range(long_name.height())
            for x in range(long_name.width())
            if not rect.contains(x, y)
            and long_name.pixelColor(x, y).alpha()
            != short.pixelColor(x, y).alpha()
        ]

        self.assertEqual(
            outside, [], f"texto mudou pixels fora do balão: {outside[:5]}"
        )

    def test_the_long_name_is_cut_inside_the_bubble(self):
        """O corte acontece: a linha do nome fica cheia, e não maior que a caixa.

        Contado na **linha do subtítulo** e com um limiar logo abaixo da
        cor do subtítulo: contando a caixa inteira o número seria o
        mesmo, porque o título é idêntico nos dois renders.
        """

        from petwatch.ui import bubble

        rect = self.widget.card_rects(1)[0]

        layout = bubble.card_layout(
            self.theme, self.widget.size, "Thinking", "dd", rect,
        )

        row = layout.subtitle_rect.center().y()

        def ink(image) -> int:
            return sum(
                1
                for x in range(rect.left(), rect.right())
                if image.pixelColor(x, row).lightness() < 200
            )

        self.widget.set_cards([(STATE_WORKING, "dd", False)])
        short = ink(self.widget.grab().toImage())

        self.widget.set_cards([(STATE_WORKING, "x" * 300, False)])
        cut = ink(self.widget.grab().toImage())

        self.assertGreater(cut, short)
        self.assertLessEqual(cut, rect.width())

    # ------------------------------------------------------------
    # Cor de quem precisa de resposta
    # ------------------------------------------------------------

    def count_color(self, hex_color, tolerance=40):
        from PySide6.QtGui import QColor

        target = QColor(hex_color)

        image = self.image()

        found = 0

        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)

                if pixel.alpha() < 200:
                    continue

                if (abs(pixel.red() - target.red()) <= tolerance
                        and abs(pixel.green() - target.green()) <= tolerance
                        and abs(pixel.blue() - target.blue()) <= tolerance):
                    found += 1

        return found

    def test_the_card_that_needs_an_answer_is_colored(self):
        from petwatch.states import ACTION_COLOR

        self.widget.set_cards([
            (STATE_WORKING, "trabalha", False),
            (STATE_WAITING, "espera", True),
        ])

        self.assertGreater(self.count_color(ACTION_COLOR), 10)

    def test_a_card_that_does_not_need_anything_is_not_colored(self):
        """A cor é o aviso. Pintar o resto transformaria a pilha num painel."""

        from petwatch.states import ACTION_COLOR

        self.widget.set_cards([(STATE_WORKING, "trabalha", False)])

        self.assertEqual(self.count_color(ACTION_COLOR), 0)

    def test_only_the_flagged_card_is_colored(self):
        """Numa pilha, a cor precisa dizer *qual* balão espera resposta."""


        self.widget.set_cards([
            (STATE_WAITING, "espera", True),
            (STATE_WORKING, "trabalha", False),
        ])

        image = self.image()

        rects = self.widget.card_rects(2)

        def colored(rect) -> int:
            count = 0

            for y in range(rect.top(), rect.bottom()):
                for x in range(rect.left(), rect.right()):
                    pixel = image.pixelColor(x, y)

                    if pixel.alpha() < 200:
                        continue

                    if (abs(pixel.red() - 0xB4) <= 40
                            and abs(pixel.green() - 0x53) <= 40
                            and abs(pixel.blue() - 0x09) <= 40):
                        count += 1

            return count

        self.assertGreater(colored(rects[0]), 10)
        self.assertEqual(colored(rects[1]), 0)

    def test_the_accent_stays_readable_and_low_key(self):
        """A cor é um aviso, não um alarme.

        Duas propriedades, e as duas importam mais do que "qual tom de
        azul": ela tem de ser legível sobre o fundo do balão (o
        contraste da WCAG AA) e escura o bastante para não ofuscar num
        canto da tela.
        """

        from PySide6.QtGui import QColor

        from petwatch.states import ACTION_COLOR

        color = QColor(ACTION_COLOR)

        def luminance(channel: int) -> float:
            value = channel / 255

            return (
                value / 12.92
                if value <= 0.03928
                else ((value + 0.055) / 1.055) ** 2.4
            )

        def relative(pixel: QColor) -> float:
            return (
                0.2126 * luminance(pixel.red())
                + 0.7152 * luminance(pixel.green())
                + 0.0722 * luminance(pixel.blue())
            )

        # Contraste contra o branco do balão, no formato da WCAG.
        contrast = (1.0 + 0.05) / (relative(color) + 0.05)

        self.assertGreaterEqual(contrast, 4.5, "texto de aviso ilegível")

        # ``lightness`` aqui é o V do HSV, em 0..255: nem ofuscante, nem
        # apagado no fundo claro.
        self.assertLessEqual(color.lightness(), 130)
        self.assertGreaterEqual(color.lightness(), 60)


if __name__ == "__main__":
    unittest.main()
