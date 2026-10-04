"""Tamanhos do pet e preferências persistidas."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import SCREEN_GAP_X, SCREEN_GAP_Y  # noqa: E402
from petwatch.prefs import (  # noqa: E402
    PREFS_VERSION,
    default_prefs,
    load_prefs,
    save_prefs,
)
from petwatch.sizes import (  # noqa: E402
    DEFAULT_SIZE_KEY,
    PET_SIZES,
    SIZE_ORDER,
    get_size,
)
from petwatch.theme import load_theme  # noqa: E402
from petwatch.ui import PetRenderer, always_on_top_supported  # noqa: E402


def widget_stack_fits(widget, count) -> None:
    """Nenhum balão da pilha pode invadir a faixa do sprite."""

    rects = widget.card_rects(count)

    assert rects, "sem balões para conferir"

    assert rects[-1].bottom() <= widget.height() - widget.size.sprite_height


class SizeTests(unittest.TestCase):
    def test_three_presets(self):
        self.assertEqual(set(PET_SIZES), {"small", "medium", "large"})
        self.assertEqual(SIZE_ORDER, ("small", "medium", "large"))

    def test_medium_is_the_default(self):
        self.assertEqual(get_size(DEFAULT_SIZE_KEY).key, "medium")

    def test_presets_grow(self):
        sizes = [PET_SIZES[key] for key in SIZE_ORDER]

        for smaller, bigger in zip(sizes, sizes[1:]):
            with self.subTest(smaller=smaller.key):
                self.assertLess(smaller.sprite_width, bigger.sprite_width)
                self.assertLess(smaller.card_width, bigger.card_width)
                self.assertLess(smaller.window_width_for(), bigger.window_width_for())

    def test_medium_keeps_the_previous_sprite_size(self):
        """O preset "medio" preserva o sprite que o projeto usava.

        A janela mudou de propósito: com um balão por instância em ação,
        quem dita a largura é a de um balão, não a do sprite.
        """

        medium = get_size("medium")

        self.assertEqual(medium.sprite_width, 132)
        self.assertEqual(medium.sprite_height, 144)

    def test_the_window_is_one_balloon_wide(self):
        """A largura é a de **um** balão, não a da pilha.

        Balões empilhados não precisam de largura para caber: o que
        cresce é a altura. E a largura ser fixa impede o sprite de saltar
        de lugar a cada aba que abre.
        """

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                # No "grande" o sprite (200px) é mais largo que o balão
                # (188px), e quem manda é o sprite.
                self.assertEqual(size.window_width_for(),
                                 max(size.sprite_width, size.card_width))

    def test_there_is_no_cap_on_the_stack(self):
        """Nenhuma instância pode ser escondida por um teto.

        Uma aba que o usuário não vê é exatamente a que ele precisa
        notar; o que encurta a pilha é o esquecimento
        (``sessions.QUIET_GRACE_SECONDS``), não um número.
        """

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                self.assertFalse(hasattr(size, "max_cards"))

    def test_window_fits_the_stack_and_the_sprite(self):
        """A janela precisa comportar a pilha inteira e o sprite.

        Medido com o preset aplicado, porque a fonte muda junto.
        """

        from petwatch.ui import bubble

        from petwatch.config import BUBBLE_TOP_MARGIN, SPRITE_MARGIN_BOTTOM

        theme = load_theme("eevee")

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                height = bubble.card_height_for(theme, size)

                shown = 7

                stack = shown * height + (shown - 1) * size.stack_gap

                widget = PetRenderer(theme, size)

                widget.timer.stop()

                self.addCleanup(widget.close)

                # A janela só cresce quando os balões chegam: um widget
                # recém-aberto tem a altura de um balão só.
                widget.set_cards([("working", f"p-{index}", False)
                                  for index in range(shown)])

                self.assertEqual(len(widget.cards), shown)

                self.assertEqual(widget.height(),
                                 BUBBLE_TOP_MARGIN + stack
                                 + size.sprite_height + SPRITE_MARGIN_BOTTOM)

                # E nenhum balão invade a faixa do sprite.
                widget_stack_fits(widget, shown)

    def test_the_card_is_narrow_but_readable(self):
        """Estreito o bastante para reticenciar, largo o bastante para ler."""

        from petwatch.ui import bubble

        theme = load_theme("eevee")

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                width = size.card_width
                height = bubble.card_height_for(theme, size)

                self.assertGreater(height, 0)
                self.assertLess(width, 200)
                self.assertGreater(width, 100)

    def test_card_text_is_bold_and_smaller(self):
        """O título é menor que o do balão único antigo (13px no médio)."""

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                # Comparado com o balão único antigo (13px no médio),
                # já escalado pelo preset: no "grande" os dois sobem, e é
                # a proporção que importa.
                self.assertLessEqual(
                    size.card_title_font, round(13 * size.text_scale),
                )
                self.assertLess(size.card_subtitle_font, size.card_title_font)

    def test_unknown_size_falls_back(self):
        self.assertEqual(get_size("gigante").key, DEFAULT_SIZE_KEY)


class WidgetSizeTests(unittest.TestCase):
    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)

        self.widget.timer.stop()

        self.addCleanup(self._teardown)

    def _teardown(self):
        self.widget.timer.stop()
        self.widget.close()

    def test_default_is_medium(self):
        self.assertEqual(self.widget.size.key, "medium")
        self.assertEqual(self.widget.width(), self.widget.size.window_width_for())
        self.assertEqual(self.widget.height(), self.widget.window_height_for(0))

    def test_set_size_resizes_the_window(self):
        self.widget.set_size(get_size("large"))

        self.assertEqual(self.widget.size.key, "large")
        self.assertEqual(self.widget.width(),
                         PET_SIZES["large"].window_width_for())
        self.assertEqual(self.widget.height(), self.widget.window_height_for(0))

    def test_the_window_never_changes_with_the_card_count(self):
        """Balão a mais ou a menos **não** muda a janela.

        No Wayland o compositor decide onde a superfície fica, então um
        ``resize()`` faz o pet subir ou descer em relação à tela — foi o
        sintoma reportado. A janela nasce com a capacidade da pilha e
        ponto final.
        """

        from petwatch.states import STATE_IDLE, STATE_WORKING

        before = self.widget.geometry()

        for count in (0, 1, 3, 6, 2):
            with self.subTest(count=count):
                self.widget.set_cards(
                    [(STATE_WORKING if index % 2 else STATE_IDLE,
                      str(index), False) for index in range(count)]
                )

                self.assertEqual(self.widget.geometry(), before)

        # Só a troca de preset redimensiona a janela — e ela é dita pelo
        # usuário, não pelo estado do opencode.
        self.widget.set_size(get_size("large"))

        self.assertEqual(self.widget.width(),
                         get_size("large").window_width_for())

    def test_set_size_rescales_the_sprite(self):
        small = self.widget.scaled_pixmap()

        self.widget.set_size(get_size("large"))
        large = self.widget.scaled_pixmap()

        self.assertGreater(large.width(), small.width())
        self.assertGreater(large.height(), small.height())

    def test_set_size_keeps_the_screen_corner(self):
        """Redimensionar não pode empurrar o pet para fora da tela."""

        screen = self.widget.screen()

        if screen is None:
            self.skipTest("sem tela no offscreen")

        geometry = screen.availableGeometry()

        # Preso à mesma folga da tela, seja qual for o tamanho. A folga
        # é SCREEN_GAP_X/Y, não zero.
        for key in SIZE_ORDER:
            with self.subTest(size=key):
                size = get_size(key)

                self.widget.set_size(size)

                self.assertEqual(
                    self.widget.x(),
                    geometry.right() - self.widget.width() - SCREEN_GAP_X,
                )
                self.assertEqual(
                    self.widget.y(),
                    geometry.bottom() - self.widget.height() - SCREEN_GAP_Y,
                )

    def test_set_size_never_goes_off_screen(self):
        screen = self.widget.screen()

        if screen is None:
            self.skipTest("sem tela no offscreen")

        geometry = screen.availableGeometry()

        # Pior caso: o pet já estava no canto sem folga.
        self.widget.move(
            geometry.right() - self.widget.width(),
            geometry.bottom() - self.widget.height(),
        )

        for key in SIZE_ORDER:
            with self.subTest(size=key):
                self.widget.set_size(get_size(key))

                self.assertLessEqual(
                    self.widget.geometry().right(),
                    geometry.right(),
                )
                self.assertLessEqual(
                    self.widget.geometry().bottom(),
                    geometry.bottom(),
                )

    def test_sprite_fits_the_window_in_every_size(self):
        for key in SIZE_ORDER:
            with self.subTest(size=key):
                self.widget.set_size(get_size(key))

                pixmap = self.widget.scaled_pixmap()

                self.assertLessEqual(pixmap.width(), self.widget.width())
                self.assertLessEqual(pixmap.height(), self.widget.height())


class AlwaysOnTopTests(unittest.TestCase):
    def setUp(self):
        self.widget = PetRenderer(load_theme("eevee"))

        self.widget.timer.stop()

        #: ``show()`` é o efeito observável da troca de flag: mudar um
        #: WindowFlag recria a janela nativa. Espiar o método é mais
        #: direto que contar eventos, que o próprio Qt emite durante a
        #: recriação.
        self.shows: list[bool] = []

        self.widget.show = lambda *args: self.shows.append(True)

        self.addCleanup(lambda: (self.widget.timer.stop(), self.widget.close()))

    def on_top_flag(self) -> bool:
        return bool(self.widget.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_default_on(self):
        self.assertTrue(self.on_top_flag())

    def test_toggles_off_and_on(self):
        self.widget.set_always_on_top(False)
        self.assertFalse(self.on_top_flag())

        self.widget.set_always_on_top(True)
        self.assertTrue(self.on_top_flag())

    def test_setting_the_current_value_recreates_nothing(self):
        """Regressão: a guarda nunca segurava ao ligar o flag.

        ``flags & hint == enabled`` comparava o inteiro do hint
        (``0x40000``) com ``True``, porque ``&`` liga mais forte que
        ``==`` — o resultado era sempre falso e o ``show()`` final
        recriava a janela nativa a cada chamada.
        """

        # O widget já nasce ligado, pelo construtor.
        self.widget.set_always_on_top(True)

        self.assertEqual(self.shows, [])

    def test_the_guard_is_symmetric(self):
        """O mesmo valor não pode custar uma recriação nos dois sentidos.

        Antes, desligar duas vezes era o único caminho que a guarda
        segurava — ``0 == False`` é verdadeiro em Python.
        """

        self.widget.set_always_on_top(False)
        self.widget.set_always_on_top(False)

        self.assertEqual(self.shows, [True])

    def test_a_real_change_still_recreates_the_window(self):
        """A guarda corrigida não pode engolir a troca de verdade."""

        self.widget.set_always_on_top(False)

        self.assertEqual(self.shows, [True])
        self.assertFalse(self.on_top_flag())


class AlwaysOnTopSupportTests(unittest.TestCase):
    def test_wayland_has_no_z_order(self):
        """É o caso do WSLg: o flag é aceito e nunca enviado."""

        self.assertFalse(always_on_top_supported("wayland"))

    def test_x11_is_left_to_the_window_manager(self):
        """Um i3/mutter/kwin honra o átomo; só o Wayland é certeza."""

        self.assertTrue(always_on_top_supported("xcb"))

    def test_the_current_platform_is_detected(self):
        """Chamar sem argumento olha o backend de verdade."""

        self.assertEqual(
            always_on_top_supported(),
            QGuiApplication.platformName() != "wayland",
        )


class ThemeSwapTests(unittest.TestCase):
    def setUp(self):
        self.theme = load_theme("eevee")
        self.widget = PetRenderer(self.theme)

        self.widget.timer.stop()

        self.addCleanup(lambda: (self.widget.timer.stop(), self.widget.close()))

    def test_apply_theme_reloads_the_sprite(self):
        from petwatch.theme import load_theme as load

        before = self.widget.scaled_pixmap().cacheKey()

        self.widget.apply_theme(load("pikachu"))

        after = self.widget.scaled_pixmap()

        self.assertNotEqual(after.cacheKey(), before)
        self.assertEqual(self.widget.theme.name, "Pikachu")

    def test_apply_theme_keeps_the_size(self):
        from petwatch.theme import load_theme as load

        self.widget.set_size(get_size("large"))

        self.widget.apply_theme(load("pikachu"))

        self.assertEqual(self.widget.size.key, "large")
        self.assertEqual(self.widget.width(),
                         PET_SIZES["large"].window_width_for())

    def test_apply_theme_resets_the_frame_into_range(self):
        from petwatch.theme import load_theme as load

        self.widget.set_state("working")

        for _ in range(30):
            self.widget.advance_sprite()

        self.widget.apply_theme(load("mewtwo"))

        first, last = self.widget.loop_range

        self.assertGreaterEqual(self.widget.sprite_frame, first)
        self.assertLessEqual(self.widget.sprite_frame, last)


class PrefsTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "prefs.json"

    def test_defaults(self):
        prefs = default_prefs()

        self.assertEqual(prefs["size"], DEFAULT_SIZE_KEY)
        self.assertTrue(prefs["always_on_top"])
        self.assertIsNone(prefs["theme"])

    def test_missing_file_gives_defaults(self):
        prefs = load_prefs(self.path)

        self.assertEqual(prefs["size"], DEFAULT_SIZE_KEY)
        self.assertIsNone(prefs["theme"])

    def test_round_trip(self):
        self.assertTrue(save_prefs({
            "theme": "pikachu",
            "size": "large",
            "always_on_top": False,
        }, self.path))

        prefs = load_prefs(self.path)

        self.assertEqual(prefs["theme"], "pikachu")
        self.assertEqual(prefs["size"], "large")
        self.assertFalse(prefs["always_on_top"])

    def test_corrupted_file_gives_defaults(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ nao é json", encoding="utf-8")

        prefs = load_prefs(self.path)

        self.assertEqual(prefs["size"], DEFAULT_SIZE_KEY)

    def test_other_version_gives_defaults(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"version": 999, "size": "large"}),
            encoding="utf-8",
        )

        prefs = load_prefs(self.path)

        self.assertEqual(prefs["size"], DEFAULT_SIZE_KEY)

    def test_wrong_types_are_ignored(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "version": PREFS_VERSION,
            "size": 123,
            "theme": ["x"],
            "always_on_top": "sim",
        }), encoding="utf-8")

        prefs = load_prefs(self.path)

        self.assertEqual(prefs["size"], DEFAULT_SIZE_KEY)
        self.assertIsNone(prefs["theme"])
        self.assertTrue(prefs["always_on_top"])

    def test_save_creates_the_directory(self):
        nested = self.path.parent / "a" / "b" / "prefs.json"

        self.assertTrue(save_prefs({"size": "small"}, nested))
        self.assertTrue(nested.exists())

    def test_save_is_atomic(self):
        """Sobrescrever não pode deixar lixo para trás."""

        save_prefs({"size": "small"}, self.path)
        save_prefs({"size": "large"}, self.path)

        leftovers = list(self.path.parent.glob("*.tmp"))

        self.assertEqual(leftovers, [])
        self.assertEqual(load_prefs(self.path)["size"], "large")

    def test_unknown_size_is_kept_and_normalized_on_use(self):
        """Um valor antigo não impede o app de abrir."""

        save_prefs({"size": "enorme"}, self.path)

        prefs = load_prefs(self.path)

        self.assertEqual(prefs["size"], "enorme")
        self.assertEqual(get_size(prefs["size"]).key, DEFAULT_SIZE_KEY)


if __name__ == "__main__":
    unittest.main()