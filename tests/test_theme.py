"""Leitura do ``pet.json``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from petwatch.theme import (
    DEFAULT_FPS,
    PetTheme,
    SpriteGrid,
    ThemeNotFoundError,
    load_theme,
    read_loop,
)


def make_theme_dir(files: dict[str, str]) -> Path:
    directory = Path(tempfile.mkdtemp())

    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")

    return directory


class ThemeSchemaTests(unittest.TestCase):
    """Os pet.json reais usam id/displayName/spritesheetPath."""

    def test_reads_repository_schema(self):
        theme = load_theme("eevee")

        self.assertEqual(theme.name, "Eevee")
        self.assertIsNotNone(theme.asset_path)
        self.assertTrue(theme.asset_path.exists())
        self.assertEqual(theme.asset_path.name, "spritesheet.webp")

    def test_accepts_legacy_name_and_asset_keys(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({"name": "Legacy", "asset": "a.gif"}),
            "a.gif": "",
        })

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.name, "Legacy")
        self.assertEqual(theme.asset_path, directory / "a.gif")

    def test_prefers_display_name_over_id(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({"id": "x", "displayName": "X Nice"}),
        })

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.name, "X Nice")

    def test_falls_back_to_directory_name(self):
        directory = make_theme_dir({"pet.json": "{}"})
        directory = directory / "pikachu"
        directory.mkdir()

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.name, "pikachu")
        self.assertIsNone(theme.asset_path)

    def test_missing_config_yields_defaults(self):
        directory = make_theme_dir({})

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.name, directory.name)
        self.assertEqual(theme.scale, 1.0)
        self.assertEqual(theme.grid, SpriteGrid())
        self.assertEqual(theme.fps, DEFAULT_FPS)
        self.assertIsNone(theme.loop)

        self.assertTrue(theme.text_enabled)
        self.assertEqual(theme.text_position, "top")
        self.assertEqual(theme.font_family, "Inter")
        self.assertEqual(theme.title_color, "#1F1F1F")
        self.assertEqual(theme.subtitle_color, "#9CA3AF")
        self.assertEqual(theme.background, "#FFFFFF")
        self.assertEqual(theme.background_alpha, 246)
        self.assertEqual(theme.border, "#E4E4E7")
        self.assertEqual(theme.border_width, 1)
        self.assertEqual(theme.shadow_alpha, 28)
        self.assertEqual(theme.corner_radius, 14)
        self.assertEqual(theme.padding_x, 14)
        self.assertEqual(theme.padding_y, 9)
        self.assertEqual(theme.line_gap, 1)

    def test_broken_config_is_ignored(self):
        directory = make_theme_dir({"pet.json": "{ nao é json"})

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.config, {})
        self.assertEqual(theme.name, directory.name)

    def test_text_block_overrides_defaults(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({
                "scale": 2.5,
                "text": {
                    "enabled": False,
                    "position": "bottom",
                    "font_family": "Fira Code",
                    "title_color": "#ffffff",
                    "subtitle_color": "#cccccc",
                    "background": "#222222",
                    "background_alpha": 128,
                    "border": "#111111",
                    "border_width": 3,
                    "shadow_alpha": 0,
                    "padding_x": 20,
                    "padding_y": 10,
                    "corner_radius": 4,
                    "line_gap": 6,
                },
            }),
        })

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.scale, 2.5)
        self.assertFalse(theme.text_enabled)
        self.assertEqual(theme.text_position, "bottom")
        self.assertEqual(theme.font_family, "Fira Code")
        self.assertEqual(theme.title_color, "#ffffff")
        self.assertEqual(theme.subtitle_color, "#cccccc")
        self.assertEqual(theme.background, "#222222")
        self.assertEqual(theme.background_alpha, 128)
        self.assertEqual(theme.border, "#111111")
        self.assertEqual(theme.border_width, 3)
        self.assertEqual(theme.shadow_alpha, 0)
        self.assertEqual(theme.padding_x, 20)
        self.assertEqual(theme.padding_y, 10)
        self.assertEqual(theme.corner_radius, 4)
        self.assertEqual(theme.line_gap, 6)

    def test_the_font_size_comes_from_the_preset_not_the_theme(self):
        """A chave saiu do ``pet.json``, e um tema antigo não quebra por isso.

        O tamanho da fonte do balão é do preset de tamanho, que já sabe a
        escala; nenhum dos 1738 temas definia essas chaves.
        """

        directory = make_theme_dir({
            "pet.json": json.dumps({"text": {"title_font_size": 30}}),
        })

        theme = PetTheme.from_directory(directory)

        self.assertFalse(hasattr(theme, "title_font_size"))

        from petwatch.sizes import get_size

        self.assertEqual(get_size("medium").card_title_font, 11)

    def test_partial_text_block_keeps_other_defaults(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({"text": {"title_color": "#123456"}}),
        })

        theme = PetTheme.from_directory(directory)

        self.assertEqual(theme.title_color, "#123456")
        self.assertEqual(theme.subtitle_color, "#9CA3AF")
        self.assertTrue(theme.text_enabled)


class LoopConfigTests(unittest.TestCase):
    def test_absent_block(self):
        self.assertIsNone(read_loop({}))

    def test_absent_keys(self):
        self.assertIsNone(read_loop({"fps": 12}))

    def test_explicit_range(self):
        self.assertEqual(read_loop({"first": 8, "last": 15}), (8, 15))

    def test_only_first_defaults_last(self):
        self.assertEqual(read_loop({"first": 8}), (8, 8))

    def test_invalid_values_are_ignored(self):
        for frames in (
            {"first": "oito", "last": "quinze"},
            {"first": -1, "last": 5},
            {"first": 9, "last": 2},
        ):
            with self.subTest(frames=frames):
                self.assertIsNone(read_loop(frames))

    def test_theme_reads_frames_block(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({
                "frames": {
                    "columns": 4,
                    "rows": 5,
                    "fps": 30,
                    "first": 8,
                    "last": 15,
                },
            }),
        })

        theme = PetTheme.from_directory(directory)

        self.assertEqual((theme.grid.columns, theme.grid.rows), (4, 5))
        self.assertEqual(theme.fps, 30)
        self.assertEqual(theme.loop, (8, 15))

    def test_theme_rejects_bad_fps(self):
        directory = make_theme_dir({
            "pet.json": json.dumps({"frames": {"fps": "rapido"}}),
        })

        self.assertEqual(PetTheme.from_directory(directory).fps, DEFAULT_FPS)


class LoadThemeTests(unittest.TestCase):
    def test_unknown_theme(self):
        with self.assertRaises(ThemeNotFoundError):
            load_theme("nao-existe-mesmo")

    def test_theme_not_found_is_a_file_not_found(self):
        self.assertTrue(issubclass(ThemeNotFoundError, FileNotFoundError))


class RepositoryThemesTests(unittest.TestCase):
    """Todos os temas do repositório devem carregar sem asset resolúvel."""

    def test_every_theme_resolves_a_sprite_path(self):
        from petwatch.theme import list_themes

        names = list_themes()

        self.assertGreater(len(names), 1000)

        for name in names:
            with self.subTest(theme=name):
                theme = load_theme(name)

                self.assertTrue(theme.name)
                self.assertIsNotNone(theme.asset_path, name)


if __name__ == "__main__":
    unittest.main()
