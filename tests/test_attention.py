"""Pulso de "precisa de você" no estado de espera."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import SPRITE_MARGIN_BOTTOM  # noqa: E402
from petwatch.sizes import SIZE_ORDER, get_size  # noqa: E402
from petwatch.states import (  # noqa: E402
    STATE_CONNECTING,
    STATE_IDLE,
    STATE_WAITING,
    STATE_WORKING,
)
from petwatch.theme import load_theme  # noqa: E402
from petwatch.ui import PetRenderer  # noqa: E402
from petwatch.ui.attention import AttentionPulse  # noqa: E402


def advance(msec: int) -> None:
    QCoreApplication.processEvents()


class PulseTests(unittest.TestCase):
    def setUp(self):
        self.pulse = AttentionPulse()

    def tearDown(self):
        self.pulse.stop()

    def test_starts_at_rest(self):
        self.assertEqual(self.pulse.progress, 0.0)
        self.assertEqual(self.pulse.sprite_lift(), 0.0)
        self.assertEqual(self.pulse.glow_alpha(), 0)

    def test_start_animates(self):
        self.pulse.start()

        self.assertTrue(self.pulse.is_running())

        self.pulse.stop()

    def test_stop_returns_to_rest(self):
        self.pulse.start()

        animation = self.pulse._animation
        animation.setCurrentTime(animation.duration() * 0.45)
        self.assertGreater(self.pulse.progress, 0.0)

        self.pulse.stop()

        self.assertEqual(self.pulse.progress, 0.0)
        self.assertFalse(self.pulse.is_running())

    def test_progress_is_clamped(self):
        self.pulse.set_progress(5.0)
        self.assertEqual(self.pulse.progress, 1.0)

        self.pulse.set_progress(-2.0)
        self.assertEqual(self.pulse.progress, 0.0)

    def test_repeats_while_waiting(self):
        """A espera pode durar minutos: um pico so nao avisaria."""

        self.pulse.start()

        self.assertTrue(self.pulse._cycle.isSingleShot() is False
                        or self.pulse._cycle.isSingleShot())

        # O timer agenda o próximo pico enquanto houver espera.
        self.pulse._run_once()
        self.assertTrue(self.pulse._cycle.isActive())

        self.pulse.stop()
        self.assertFalse(self.pulse._cycle.isActive())

    def test_effects_grow_with_progress(self):
        self.pulse.set_progress(0.0)
        self.assertEqual(self.pulse.sprite_lift(), 0.0)

        self.pulse.set_progress(1.0)
        self.assertGreater(self.pulse.sprite_lift(), 0.0)
        self.assertGreater(self.pulse.bubble_growth(), 0.0)
        self.assertGreater(self.pulse.glow_alpha(), 0)

    def test_amplitude_scales_the_effects(self):
        self.pulse.set_amplitude(2.0)
        self.pulse.set_progress(1.0)

        doubled = self.pulse.sprite_lift()

        self.pulse.set_amplitude(1.0)

        self.assertAlmostEqual(doubled, self.pulse.sprite_lift() * 2)

    def test_amplitude_zero_disables(self):
        self.pulse.set_amplitude(0.0)
        self.pulse.set_progress(1.0)

        self.assertEqual(self.pulse.sprite_lift(), 0.0)

    def test_emits_on_change(self):
        seen = []
        self.pulse.progress_changed.connect(seen.append)

        self.pulse.set_progress(0.5)

        self.assertEqual(seen, [0.5])

        # Mesmo valor não emite.
        self.pulse.set_progress(0.5)
        self.assertEqual(seen, [0.5])

    def test_is_a_property_for_the_animation(self):
        """A animação escreve na propriedade, não num atributo solto."""

        self.assertTrue(
            AttentionPulse.staticMetaObject.indexOfProperty("progress") >= 0
        )


class WidgetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.widget = PetRenderer(load_theme("eevee"))
        self.widget.timer.stop()

        self.addCleanup(lambda: (self.widget.timer.stop(), self.widget.close()))

    def test_only_waiting_triggers_the_pulse(self):
        for state in (STATE_IDLE, STATE_WORKING, STATE_CONNECTING):
            with self.subTest(state=state):
                self.widget.set_state(state)

                self.assertFalse(self.widget.attention.is_running())

    def test_waiting_triggers_the_pulse(self):
        self.widget.set_state(STATE_WAITING)

        self.assertTrue(self.widget.attention.is_running())

    def test_leaving_waiting_stops_it(self):
        self.widget.set_state(STATE_WAITING)

        self.widget.set_state(STATE_WORKING)

        self.assertFalse(self.widget.attention.is_running())

    def test_bob_includes_the_lift(self):
        self.widget.set_state(STATE_IDLE)
        self.widget.attention.set_progress(0.0)

        resting = self.widget.bob_offset()

        self.widget.set_state(STATE_WAITING)
        self.widget.attention.set_progress(1.0)

        # No pico o sprite sobe mais do que o bob de repouso.
        self.assertLess(self.widget.bob_offset(), resting)

    def test_lift_is_small_against_the_sprite(self):
        """Um salto grande demais pareceria um salto, não uma respiração."""

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                self.widget.set_size(get_size(key))
                self.widget.set_state(STATE_WAITING)
                self.widget.attention.set_progress(1.0)
                pixmap = self.widget.scaled_pixmap()

                share = self.widget.attention.sprite_lift() / pixmap.height()

                self.assertLess(share, 0.05)

    def test_pulse_never_leaves_the_window(self):
        """O sprite sobe; a janela não acompanha."""

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                self.widget.set_size(get_size(key))
                self.widget.set_state(STATE_WAITING)

                self.widget.attention.set_progress(1.0)

                top = self.widget.height() - self.widget.scaled_pixmap().height()

                self.assertGreater(top, SPRITE_MARGIN_BOTTOM)


class RenderTests(unittest.TestCase):
    """O pulso não pode quebrar a pintura nem mudar as medidas.

    A widget nunca é fechada aqui: ``grab()`` cria um paint device
    temporário e fechá-la no meio deixa o Qt destruir device ainda em uso
    (o aviso "Cannot destroy paint device" e um segfault). Os outros
    testes desta classe fecham o próprio widget e não rasterizam.
    """

    def setUp(self):
        self.widget = PetRenderer(load_theme("eevee"))
        self.widget.timer.stop()

        self.addCleanup(self.widget.timer.stop)

    def test_paints_at_rest_and_at_peak(self):
        for progress in (0.0, 0.5, 1.0):
            with self.subTest(progress=progress):
                self.widget.set_state(STATE_WAITING)
                self.widget.attention.set_progress(progress)

                image = self.widget.grab().toImage()

                self.assertFalse(image.isNull())
                self.assertEqual(image.width(), self.widget.width())
                self.assertEqual(image.height(), self.widget.height())

    def test_peak_looks_different_from_rest(self):
        """O pulso precisa aparecer; se não, é enfeite invisível."""

        self.widget.set_state(STATE_WAITING)

        self.widget.attention.set_progress(0.0)
        rest = self.widget.grab().toImage()

        self.widget.attention.set_progress(1.0)
        peak = self.widget.grab().toImage()

        self.assertNotEqual(rest, peak)

    def test_bubble_measures_the_same_with_and_without_pulse(self):
        """O pulso é só Pintura: não muda fonte, padding nem largura."""

        from petwatch.states import labels_for
        from petwatch.ui import bubble

        theme = self.widget.theme
        size = self.widget.size
        title, subtitle = labels_for(STATE_WAITING)

        self.widget.attention.set_progress(0.0)
        resting = bubble.card_layout(
            theme, size, title, subtitle,
            bubble.card_rects(1, size, theme, self.widget.width(),
                              self.widget.height())[0],
        )

        self.widget.attention.set_progress(1.0)
        peaked = bubble.card_layout(
            theme, size, title, subtitle,
            bubble.card_rects(1, size, theme, self.widget.width(),
                              self.widget.height())[0],
        )

        self.assertEqual(resting.rect, peaked.rect)
        self.assertEqual(resting.title_rect, peaked.title_rect)


if __name__ == "__main__":
    unittest.main()
