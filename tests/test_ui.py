"""Widget do pet: geometria, estados, animacao e balão."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import (  # noqa: E402
    BUBBLE_TOP_MARGIN,
    SPRITE_MARGIN_BOTTOM,
    SPRITE_MAX_HEIGHT,
    SPRITE_MAX_WIDTH,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
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
from petwatch.ui import PetRenderer  # noqa: E402
from petwatch.ui import bubble  # noqa: E402


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

    def test_window_geometry_is_compact(self):
        self.assertEqual(
            (self.widget.width(), self.widget.height()),
            (WINDOW_WIDTH, WINDOW_HEIGHT),
        )

    def test_window_is_small_enough_to_be_a_mascot(self):
        # A janela abraça o sprite; antes ela media 420x360.
        self.assertLessEqual(WINDOW_WIDTH, 280)
        self.assertLessEqual(WINDOW_HEIGHT, 240)

    def test_starts_connecting_with_a_sprite(self):
        self.assertEqual(self.widget.state, STATE_CONNECTING)
        self.assertIsNotNone(self.widget.source)
        self.assertIsNotNone(self.widget.current_image())

    def test_sprite_scales_keeping_aspect_ratio(self):
        pixmap = self.widget.scaled_pixmap()

        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertLessEqual(pixmap.width(), SPRITE_MAX_WIDTH)
        self.assertLessEqual(pixmap.height(), SPRITE_MAX_HEIGHT)

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

    def test_loop_is_a_known_idle_cycle(self):
        # A primeira sequencia de celulas com conteudo da spritesheet.
        self.assertEqual(self.widget.loop_range, (0, 5))

    def test_frame_step_is_at_least_one(self):
        self.assertGreaterEqual(self.widget.frame_step, 1)


class StateLabelsTests(unittest.TestCase):
    def test_every_state_has_two_lines(self):
        for state in VALID_STATES:
            with self.subTest(state=state):
                title, subtitle = labels_for(state)

                self.assertTrue(title)
                self.assertTrue(subtitle)

    def test_unknown_state_is_blank(self):
        self.assertEqual(labels_for("nao-existe"), ("", ""))

    def test_all_states_are_labelled(self):
        self.assertEqual(set(STATE_LABELS), VALID_STATES)


class BubbleLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.theme = load_theme("eevee")

    def layout(self, title: str, subtitle: str, container_width: int = WINDOW_WIDTH):
        return bubble.layout_for(self.theme, title, subtitle, container_width)

    def test_two_lines_are_stacked_in_order(self):
        layout = self.layout("Thinking", "working on it")

        self.assertLess(layout.title_rect.top(), layout.subtitle_rect.top())
        self.assertEqual(layout.title_rect.left(), layout.subtitle_rect.left())

    def test_title_font_is_bold_and_subtitle_is_not(self):
        layout = self.layout("Thinking", "working on it")

        self.assertGreater(layout.title_font.weight(), layout.subtitle_font.weight())
        self.assertEqual(layout.title_font.family(), layout.subtitle_font.family())

    def test_title_is_at_least_as_large_as_subtitle(self):
        layout = self.layout("Thinking", "working on it")

        self.assertGreaterEqual(
            layout.title_font.pointSize(),
            layout.subtitle_font.pointSize(),
        )

    def test_bubble_fits_the_longer_line_plus_padding(self):
        title, subtitle = "Thinking", "working on it"

        layout = self.layout(title, subtitle)

        metrics_title = QFontMetrics(layout.title_font)
        metrics_sub = QFontMetrics(layout.subtitle_font)

        widest = max(
            metrics_title.horizontalAdvance(title),
            metrics_sub.horizontalAdvance(subtitle),
        )

        self.assertEqual(layout.rect.width(), widest + self.theme.padding_x * 2)

    def test_vertical_stack_accounts_for_gap_and_padding(self):
        layout = self.layout("Thinking", "working on it")

        expected = (
            layout.title_rect.height()
            + self.theme.line_gap
            + layout.subtitle_rect.height()
            + self.theme.padding_y * 2
        )

        self.assertEqual(layout.rect.height(), expected)

    def test_text_starts_at_padding_from_edges(self):
        layout = self.layout("Thinking", "working on it")

        padding_x = self.theme.padding_x
        padding_y = self.theme.padding_y

        self.assertEqual(layout.title_rect.left(), layout.rect.left() + padding_x)
        self.assertEqual(layout.title_rect.top(), layout.rect.top() + padding_y)

    def test_pinned_to_top(self):
        self.assertEqual(self.layout("a", "b").rect.top(), BUBBLE_TOP_MARGIN)

    def test_horizontally_centered(self):
        for width in (200, WINDOW_WIDTH, 400):
            with self.subTest(container=width):
                layout = self.layout("Thinking", "working on it", width)

                self.assertEqual(layout.rect.left(), (width - layout.rect.width()) // 2)

    def test_bubble_is_narrower_than_the_window(self):
        for state in VALID_STATES:
            with self.subTest(state=state):
                title, subtitle = labels_for(state)
                layout = self.layout(title, subtitle)

                self.assertLess(layout.rect.width(), WINDOW_WIDTH)

    def test_unavailable_family_falls_back_to_a_real_one(self):
        from petwatch.ui.bubble import available_families, resolve_family

        available = available_families()

        if "Inter" in available:
            self.skipTest("Inter esta instalada neste sistema")

        self.assertEqual(
            resolve_family("Inter"),
            resolve_family(""),
        )
        self.assertIn(resolve_family("Inter"), available)

    def test_resolve_family_honours_an_installed_family(self):
        from petwatch.ui.bubble import available_families, resolve_family

        available = available_families()

        if not available:
            self.skipTest("sem fontes no sistema")

        name = sorted(available)[0]

        self.assertEqual(resolve_family(name), name)


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
            layout = bubble.layout_for(
                self.theme, *labels_for(self.widget.state), self.widget.width()
            )
            start = layout.rect.y() + layout.rect.height() + 4

        rows = [
            y
            for y in range(start, image.height())
            if any(
                image.pixelColor(x, y).alpha() > 0
                for x in range(image.width())
            )
        ]

        return range(rows[0], rows[-1] + 1) if rows else range(0)

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
        self.theme.text_enabled = False
        without = len(self.opaque_rows())

        self.theme.text_enabled = True
        with_bubble = len(self.opaque_rows())

        self.assertGreater(with_bubble, without)

    def test_bubble_does_not_collide_with_the_sprite(self):
        self.theme.text_enabled = True

        title, subtitle = labels_for(STATE_WORKING)
        layout = bubble.layout_for(
            self.theme, title, subtitle, self.widget.width()
        )

        rows = self.opaque_rows(skip_bubble=True)

        self.assertGreater(rows[0], layout.rect.y() + layout.rect.height())


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


if __name__ == "__main__":
    unittest.main()
