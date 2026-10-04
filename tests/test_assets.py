"""Escolha, fatiamento e animacao do asset."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.assets import (  # noqa: E402
    SUPPORTED_SUFFIXES,
    SingleFrame,
    SpriteSheet,
    build_frames,
    candidate_paths,
    has_content,
    is_supported,
    load_image,
    resolve_frames,
)
from petwatch.theme import (  # noqa: E402
    DEFAULT_COLUMNS,
    DEFAULT_ROWS,
    SpriteGrid,
    list_themes,
    load_theme,
)


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp())


def write_png(path: Path, size: int = 4) -> Path:
    """Escreve um PNG valido usando o proprio Qt."""

    path.parent.mkdir(parents=True, exist_ok=True)

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(0xFF00FF00)

    image.save(str(path), "PNG")

    return path


def signature(image: QImage) -> bytes:
    """Conteudo bruto da imagem, para comparar quadros por valor."""

    return bytes(image.convertToFormat(QImage.Format_ARGB32).constBits())


def blank(width: int, height: int) -> QImage:
    """Imagem totalmente transparente (QImage cru tem memoria indefinida)."""

    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))

    return image


def filled(width: int, height: int, alpha: int = 255) -> QImage:
    """Imagem totalmente opaca."""

    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, alpha))

    return image


def solid_sheet(
    columns: int,
    rows: int,
    *,
    filled_cells: set[int] | None = None,
    cell: int = 8,
) -> QImage:
    """Grade em que so os indices de ``filled_cells`` tem pixel opaco."""

    image = blank(columns * cell, rows * cell)

    targets = range(columns * rows) if filled_cells is None else filled_cells

    painter = QPainter(image)

    for index in targets:
        col, row = index % columns, index // columns

        # Cor distinta por celula, para que dois quadros nunca sejam
        # iguais por acaso.
        painter.fillRect(
            QRect(col * cell, row * cell, cell, cell),
            QColor((index * 37) % 256, (index * 91) % 256, index % 256, 255),
        )

    painter.end()

    return image


GRID = SpriteGrid(columns=2, rows=2)


class SupportedSuffixTests(unittest.TestCase):
    def test_known_formats_are_listed(self):
        for suffix in ("png", "gif", "jpg", "svg"):
            self.assertIn(suffix, SUPPORTED_SUFFIXES, suffix)

    def test_is_supported_is_case_insensitive(self):
        directory = temp_dir()

        self.assertTrue(is_supported(directory / "a.PNG"))
        self.assertFalse(is_supported(directory / "a.txt"))


class LoadImageTests(unittest.TestCase):
    def test_none_path(self):
        self.assertIsNone(load_image(None))

    def test_missing_file(self):
        self.assertIsNone(load_image(temp_dir() / "sumiu.png"))

    def test_file_that_is_not_an_image(self):
        path = temp_dir() / "falso.png"
        path.write_bytes(b"isto nao e uma imagem")

        self.assertIsNone(load_image(path))

    def test_valid_png(self):
        image = load_image(write_png(temp_dir() / "ok.png"))

        self.assertIsNotNone(image)
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (4, 4))


class HasContentTests(unittest.TestCase):
    def test_opaque_image(self):
        self.assertTrue(has_content(filled(4, 4)))

    def test_faintly_opaque_image_counts(self):
        self.assertTrue(has_content(filled(4, 4, alpha=40)))

    def test_fully_transparent(self):
        self.assertFalse(has_content(blank(16, 16)))

    def test_zero_sized(self):
        self.assertFalse(has_content(blank(0, 0)))

    def test_sparse_content_is_found_despite_sampling(self):
        image = blank(64, 64)
        image.setPixelColor(1, 60, QColor(0, 0, 0, 255))

        self.assertTrue(has_content(image, step=4))


class SpriteGridTests(unittest.TestCase):
    def test_defaults_match_the_asset_format(self):
        grid = SpriteGrid()

        self.assertEqual(grid.columns, DEFAULT_COLUMNS)
        self.assertEqual(grid.rows, DEFAULT_ROWS)
        self.assertEqual(grid.frame_count, 72)

    def test_cell_size(self):
        grid = SpriteGrid(columns=8, rows=9)

        self.assertEqual(grid.cell_size(1536, 1872), (192, 208))

    def test_fits(self):
        grid = SpriteGrid(columns=8, rows=9)

        self.assertTrue(grid.fits(1536, 1872))
        self.assertFalse(grid.fits(100, 100))
        self.assertFalse(grid.fits(0, 0))

    def test_rejects_zero_or_negative(self):
        self.assertFalse(SpriteGrid(columns=0, rows=9).fits(10, 10))
        self.assertFalse(SpriteGrid(columns=8, rows=-1).fits(10, 10))

    def test_from_config(self):
        grid = SpriteGrid.from_config({"frames": {"columns": 4, "rows": 3}})

        self.assertEqual((grid.columns, grid.rows), (4, 3))

    def test_from_config_defaults_when_missing(self):
        self.assertEqual(SpriteGrid.from_config({}), SpriteGrid())

    def test_from_config_ignores_garbage(self):
        for frames in (
            {"columns": "oito", "rows": "nove"},
            {"columns": 0, "rows": 0},
            {"columns": -2, "rows": 9},
            "nao-dict",
        ):
            with self.subTest(frames=frames):
                self.assertEqual(
                    SpriteGrid.from_config({"frames": frames}),
                    SpriteGrid(),
                )


class SpriteSheetTests(unittest.TestCase):
    def test_frame_count_and_cell_size(self):
        sheet = SpriteSheet(solid_sheet(8, 9), SpriteGrid(8, 9))

        self.assertEqual(sheet.frame_count, 72)
        self.assertEqual(sheet.cell_size, (8, 8))

    def test_every_frame_has_cell_size(self):
        sheet = SpriteSheet(solid_sheet(4, 4), SpriteGrid(4, 4))

        for index in range(sheet.frame_count):
            with self.subTest(index=index):
                frame = sheet.frame(index)

                self.assertEqual(frame.width(), sheet.cell_size[0])
                self.assertEqual(frame.height(), sheet.cell_size[1])

    def test_frame_index_wraps(self):
        sheet = SpriteSheet(solid_sheet(2, 2), SpriteGrid(2, 2))

        self.assertEqual(
            signature(sheet.frame(0)),
            signature(sheet.frame(4)),
        )

    def test_distinct_cells_give_distinct_frames(self):
        sheet = SpriteSheet(solid_sheet(2, 2), SpriteGrid(2, 2))

        self.assertEqual(
            len({signature(sheet.frame(i)) for i in range(4)}),
            4,
        )

    def test_loop_detection_stops_at_first_gap(self):
        # Celulas 0,1,2 tem conteudo; a 3 esta vazia.
        sheet = SpriteSheet(
            solid_sheet(4, 1, filled_cells={0, 1, 2}),
            SpriteGrid(4, 1),
        )

        self.assertEqual(sheet.loop_range, (0, 2))
        self.assertEqual(sheet.loop_length, 3)

    def test_loop_detection_skips_leading_empty_cells(self):
        sheet = SpriteSheet(
            solid_sheet(4, 1, filled_cells={1, 2}),
            SpriteGrid(4, 1),
        )

        self.assertEqual(sheet.loop_range, (1, 2))

    def test_loop_detection_uses_everything_when_full(self):
        sheet = SpriteSheet(solid_sheet(2, 2), SpriteGrid(2, 2))

        self.assertEqual(sheet.loop_range, (0, 3))

    def test_row_loop_finds_the_used_cells(self):
        # Linha 1 com as tres primeiras celulas, linha 2 com todas.
        sheet = SpriteSheet(
            solid_sheet(3, 3, filled_cells={0, 1, 2, 3, 4, 5, 6, 7, 8}),
            SpriteGrid(3, 3),
        )

        self.assertEqual(sheet.row_loop(0), (0, 2))
        self.assertEqual(sheet.row_loop(1), (3, 5))
        self.assertEqual(sheet.row_loop(2), (6, 8))

    def test_row_loop_of_an_empty_row(self):
        sheet = SpriteSheet(
            solid_sheet(3, 3, filled_cells={0, 1, 2, 3, 4, 5}),
            SpriteGrid(3, 3),
        )

        self.assertIsNone(sheet.row_loop(2))

    def test_row_loop_skips_gaps_inside_the_row(self):
        # Celulas 0 e 3 da linha 0; 1 e 2 vazias no meio.
        sheet = SpriteSheet(
            solid_sheet(4, 1, filled_cells={0, 3}),
            SpriteGrid(4, 1),
        )

        self.assertEqual(sheet.row_loop(0), (0, 3))

    def test_row_loop_out_of_range(self):
        sheet = SpriteSheet(solid_sheet(2, 2), SpriteGrid(2, 2))

        self.assertIsNone(sheet.row_loop(9))
        self.assertIsNone(sheet.row_loop(-1))

    def test_row_loop_is_memoized(self):
        sheet = SpriteSheet(solid_sheet(4, 1), SpriteGrid(4, 1))

        self.assertEqual(sheet.row_loop(0), (0, 3))
        self.assertIn(0, sheet._row_cache)

        # Segunda chamada vem do cache, com o mesmo resultado.
        self.assertEqual(sheet.row_loop(0), (0, 3))

    def test_repository_rows_match_the_documented_layout(self):
        """O padrao ``6, 8, 8, 4, 5, 8, 6, 6, 6`` vale para o eevee."""

        from petwatch.theme import load_theme

        theme = load_theme("eevee")

        image = QImage(str(theme.asset_path))

        if image.isNull():
            self.skipTest("sem WebP neste Qt")

        sheet = SpriteSheet(image, theme.grid)

        counts = []
        for row in range(theme.grid.rows):
            loop = sheet.row_loop(row)

            counts.append(0 if loop is None else loop[1] - loop[0] + 1)

        self.assertEqual(counts, [6, 8, 8, 4, 5, 8, 6, 6, 6])

    def test_action_rows_of_the_repository_are_not_empty(self):
        """Nenhum estado pode cair numa linha vazia."""

        from petwatch.config import ACTION_ROW_BY_STATE
        from petwatch.theme import load_theme

        theme = load_theme("eevee")

        image = QImage(str(theme.asset_path))

        if image.isNull():
            self.skipTest("sem WebP neste Qt")

        sheet = SpriteSheet(image, theme.grid)

        for state, row in ACTION_ROW_BY_STATE.items():
            with self.subTest(state=state):
                self.assertIsNotNone(sheet.row_loop(row))

    def test_row_loop_detection_on_blank_sheet(self):
        sheet = SpriteSheet(
            solid_sheet(2, 2, filled_cells=set()),
            SpriteGrid(2, 2),
        )

        self.assertEqual(sheet.loop_range, (0, 0))
        self.assertEqual(sheet.loop_length, 1)

    def test_explicit_loop_is_respected(self):
        sheet = SpriteSheet(
            solid_sheet(4, 2),
            SpriteGrid(4, 2),
            loop=(4, 7),
        )

        self.assertEqual(sheet.loop_range, (4, 7))

    def test_explicit_loop_is_clamped_to_the_sheet(self):
        sheet = SpriteSheet(
            solid_sheet(4, 2),
            SpriteGrid(4, 2),
            loop=(4, 99),
        )

        self.assertEqual(sheet.loop_range, (4, 7))

    def test_explicit_loop_reversed_keeps_one_frame(self):
        sheet = SpriteSheet(solid_sheet(4, 2), SpriteGrid(4, 2), loop=(5, 5))

        self.assertEqual(sheet.loop_range, (5, 5))


class SingleFrameTests(unittest.TestCase):
    def test_always_one_frame(self):
        source = SingleFrame(blank(4, 4))

        self.assertEqual(source.frame_count, 1)
        self.assertEqual(source.loop_range, (0, 0))
        self.assertEqual(source.loop_length, 1)


class BuildFramesTests(unittest.TestCase):
    def test_uses_sheet_when_grid_fits(self):
        source = build_frames(solid_sheet(8, 9), SpriteGrid(8, 9))

        self.assertIsInstance(source, SpriteSheet)

    def test_falls_back_to_single_frame(self):
        # 100x100 nao e divisivel por 8x9.
        image = blank(100, 100)

        source = build_frames(image, SpriteGrid(8, 9))

        self.assertIsInstance(source, SingleFrame)
        self.assertEqual(source.frame_count, 1)


class CandidatePathsTests(unittest.TestCase):
    def test_configured_asset_comes_first(self):
        directory = Path("/pets/eevee")

        paths = candidate_paths(directory, directory / "spritesheet.webp")

        self.assertEqual(paths[0], directory / "spritesheet.webp")

    def test_preview_precedes_other_discovered_images(self):
        directory = temp_dir()
        write_png(directory / "zzz-extra.png")

        paths = candidate_paths(directory, None)

        self.assertLess(
            paths.index(directory / "preview.gif"),
            paths.index(directory / "zzz-extra.png"),
        )

    def test_paths_are_unique(self):
        directory = Path("/pets/eevee")

        paths = candidate_paths(directory, directory / "preview.gif")

        self.assertEqual(len(paths), len(set(paths)))

    def test_works_without_configured_asset(self):
        paths = candidate_paths(Path("/pets/eevee"), None)

        self.assertNotIn(Path("/pets/eevee/spritesheet.webp"), paths)


class ResolveFramesTests(unittest.TestCase):
    def test_uses_configured_asset_when_supported(self):
        directory = temp_dir()
        sprite = write_png(directory / "spritesheet.png")

        source, path = resolve_frames(directory, sprite, SpriteGrid(1, 1))

        self.assertIsNotNone(source)
        self.assertEqual(path, sprite)
        self.assertEqual(source.frame_count, 1)

    def test_falls_back_when_format_is_unsupported(self):
        directory = temp_dir()

        webp = directory / "spritesheet.webp"
        webp.write_bytes(b"RIFF....WEBP")

        write_png(directory / "preview.png")

        source, path = resolve_frames(directory, webp, SpriteGrid(1, 1))

        self.assertIsNotNone(source)
        self.assertEqual(path, directory / "preview.png")

    def test_falls_back_when_configured_asset_is_missing(self):
        directory = temp_dir()
        write_png(directory / "preview.png")

        source, path = resolve_frames(directory, directory / "sumiu.webp",
                                      SpriteGrid(1, 1))

        self.assertIsNotNone(source)
        self.assertEqual(path, directory / "preview.png")

    def test_falls_back_to_any_image_in_directory(self):
        directory = temp_dir()
        write_png(directory / "qualquer-coisa.png")

        source, path = resolve_frames(directory, None, SpriteGrid(1, 1))

        self.assertIsNotNone(source)
        self.assertEqual(path, directory / "qualquer-coisa.png")

    def test_empty_directory_yields_nothing(self):
        source, path = resolve_frames(temp_dir(), None, SpriteGrid(1, 1))

        self.assertIsNone(source)
        self.assertIsNone(path)

    def test_loop_is_forwarded(self):
        directory = temp_dir()
        sheet = write_png(directory / "spritesheet.png", size=64)

        source, _ = resolve_frames(directory, sheet, SpriteGrid(2, 2), (2, 3))

        self.assertEqual(source.loop_range, (2, 3))


class RepositoryAssetTests(unittest.TestCase):
    """Todos os temas do repositorio precisam renderizar nesta maquina."""

    def test_default_theme_produces_an_image(self):
        theme = load_theme("eevee")

        source, path = resolve_frames(
            theme.directory, theme.asset_path, theme.grid, theme.loop
        )

        self.assertIsNotNone(source)
        self.assertIsNotNone(path)

    def test_every_theme_has_a_decodable_asset(self):
        undecodable = []

        for name in list_themes():
            theme = load_theme(name)

            candidates = candidate_paths(theme.directory, None)

            if not any(
                is_supported(path) and path.exists() for path in candidates
            ):
                undecodable.append(name)

        self.assertEqual(undecodable, [])

    def test_every_theme_grid_divides_its_sheet(self):
        """A grade 8x9 tem de servir para todas as spritesheets."""

        broken = []

        for name in list_themes():
            theme = load_theme(name)

            if theme.asset_path is None:
                continue

            image = QImage(str(theme.asset_path))

            if image.isNull():
                continue  # sem WebP neste Qt; coberto por outro teste

            if not theme.grid.fits(image.width(), image.height()):
                broken.append(
                    f"{name}: {image.width()}x{image.height()}"
                )

        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
