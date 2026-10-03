"""Escolha e carregamento do asset de um tema.

O ``pet.json`` aponta para uma spritesheet WebP. Nem toda instalação do Qt
tem o plugin de WebP, e nesse caso a imagem volta nula e o pet some. Por
isso o carregamento percorre candidatos e usa o primeiro que o Qt de fato
consegue decodificar.

Uma spritesheet é uma grade de quadros; :class:`SpriteSheet` fatia a imagem
em :class:`~petwatch.theme.SpriteGrid` e o widget anima passando por eles.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QImageReader

from .theme import SpriteGrid

log = logging.getLogger(__name__)

#: Extensões que o Qt sabe decodificar, resolvidas uma única vez.
SUPPORTED_SUFFIXES = frozenset(
    bytes(name).decode("ascii", "ignore")
    for name in QImageReader.supportedImageFormats()
)

#: Nomes testados quando o asset configurado não pode ser usado.
FALLBACK_NAMES = ("preview.gif",)

#: Ordem de preferência ao varrer o diretório do tema.
FALLBACK_SUFFIX_ORDER = (".gif", ".png", ".svg", ".webp", ".apng", ".jpg", ".jpeg")


def is_supported(path: Path) -> bool:
    """Diz se o Qt tem decodificador para a extensão de ``path``."""

    return path.suffix.lstrip(".").lower() in SUPPORTED_SUFFIXES


# ------------------------------------------------------------
# Fontes de quadros
# ------------------------------------------------------------

class FrameSource(ABC):
    """Sequência de quadros que o widget sabe desenhar."""

    @property
    @abstractmethod
    def frame_count(self) -> int:
        """Quantos quadros a sequência tem."""

    @abstractmethod
    def frame(self, index: int) -> QImage:
        """Quadro ``index``, com índice circular."""

    @property
    def loop_range(self) -> tuple[int, int]:
        """Intervalo ``(primeiro, ultimo)`` da animação, inclusivo."""

        return 0, max(0, self.frame_count - 1)

    @property
    def loop_length(self) -> int:
        first, last = self.loop_range

        return max(1, last - first + 1)


class SingleFrame(FrameSource):
    """Uma imagem só: fallback, PNG, ou o primeiro quadro de um GIF."""

    def __init__(self, image: QImage) -> None:
        self._image = image

    @property
    def frame_count(self) -> int:
        return 1

    def frame(self, index: int) -> QImage:
        return self._image


class SpriteSheet(FrameSource):
    """Fatia uma spritesheet em quadros de uma grade.

    Todas as spritesheets do repositório têm 1536x1872 pixels, ou seja,
    8 colunas x 9 linhas de 192x208 — 72 células. As células não formam
    uma animação única: cada linha da grade é uma ação diferente e há
    células vazias de preenchimento. Por isso a animação usa só a
    primeira sequência de células com conteúdo, a menos que o tema
    force um laço.
    """

    def __init__(
        self,
        image: QImage,
        grid: SpriteGrid,
        *,
        loop: tuple[int, int] | None = None,
    ) -> None:
        self._image = image
        self._grid = grid

        self._cell_width, self._cell_height = grid.cell_size(
            image.width(), image.height()
        )

        self._loop = self._resolve_loop(loop)

    @property
    def frame_count(self) -> int:
        return self._grid.frame_count

    @property
    def cell_size(self) -> tuple[int, int]:
        return self._cell_width, self._cell_height

    @property
    def loop_range(self) -> tuple[int, int]:
        return self._loop

    def frame(self, index: int) -> QImage:
        if self._cell_width < 1 or self._cell_height < 1:
            return self._image

        index %= self.frame_count

        column = index % self._grid.columns
        row = index // self._grid.columns

        return self._image.copy(
            QRect(
                column * self._cell_width,
                row * self._cell_height,
                self._cell_width,
                self._cell_height,
            )
        )

    # ------------------------------------------------------------
    # Laço de animação
    # ------------------------------------------------------------

    def _resolve_loop(self, loop: tuple[int, int] | None) -> tuple[int, int]:
        if loop is not None:
            first, last = loop

            if last >= self.frame_count:
                log.warning(
                    "[pet] laço %d..%d passa de %d quadros; ajustando",
                    first,
                    last,
                    self.frame_count,
                )

                first, last = first, min(last, self.frame_count - 1)

            if last < first:
                last = first

            return first, last

        return self._detect_loop()

    def _detect_loop(self) -> tuple[int, int]:
        """Primeira sequência de células consecutivas com conteúdo.

        Varre apenas até o primeiro vazio depois do conteúdo, então é
        barato: normalmente 6 células.
        """

        start: int | None = None

        for index in range(self.frame_count):
            if has_content(self.frame(index)):
                if start is None:
                    start = index

            elif start is not None:
                return start, index - 1

        if start is None:
            log.warning("[pet] spritesheet sem quadros visiveis")

            return 0, 0

        return start, self.frame_count - 1


def has_content(image: QImage, *, alpha_min: int = 8, step: int = 4) -> bool:
    """Diz se a imagem tem algum pixel visível.

    ``step`` amostra uma linha a cada N, o que basta para sprite e é
    bem mais barato que varrer tudo em Python.
    """

    width = image.width()

    if width < 1 or image.height() < 1:
        return False

    mask = image.convertToFormat(QImage.Format_Alpha8)

    for y in range(0, mask.height(), step):
        if max(bytes(mask.constScanLine(y))[:width]) > alpha_min:
            return True

    return False


def build_frames(
    image: QImage,
    grid: SpriteGrid,
    loop: tuple[int, int] | None = None,
) -> FrameSource:
    """Embrulha ``image`` como spritesheet quando a grade serve."""

    if grid.fits(image.width(), image.height()):
        return SpriteSheet(image, grid, loop=loop)

    log.warning(
        "[pet] grade %dx%d nao divide %dx%d; usando imagem inteira",
        grid.columns,
        grid.rows,
        image.width(),
        image.height(),
    )

    return SingleFrame(image)


def load_image(path: Path | None) -> QImage | None:
    """Carrega ``path``; devolve ``None`` se faltar ou não for decodificável."""

    if not path:
        return None

    if not path.exists():
        log.warning("[pet] asset não encontrado: %s", path)
        return None

    image = QImage(str(path))

    if image.isNull():
        log.warning(
            "[pet] não foi possível carregar %s (%s)",
            path,
            QImageReader(str(path)).errorString(),
        )
        return None

    return image


def candidate_paths(
    directory: Path,
    configured: Path | None,
) -> list[Path]:
    """Caminhos a tentar, em ordem de preferência.

    Primeiro o asset configurado, depois ``preview.gif`` e por fim
    qualquer outra imagem do diretório, sempre sem repetir caminhos.
    """

    candidates: list[Path] = []

    if configured:
        candidates.append(configured)

    for name in FALLBACK_NAMES:
        candidates.append(directory / name)

    for suffix in FALLBACK_SUFFIX_ORDER:
        candidates.extend(sorted(directory.glob(f"*{suffix}")))

    return list(dict.fromkeys(candidates))


def resolve_frames(
    directory: Path,
    configured: Path | None,
    grid: SpriteGrid,
    loop: tuple[int, int] | None = None,
) -> tuple[FrameSource | None, Path | None]:
    """Abre o primeiro asset decodificável do tema.

    Devolve a fonte de quadros e o caminho usado. Quando o asset
    configurado é ignorado, o motivo fica no log.
    """

    candidates = candidate_paths(directory, configured)

    if configured and not is_supported(configured):
        log.info("[pet] %s sem decodificador; buscando alternativa", configured.name)

    for path in candidates:
        # Candidatos de fallback que não existem são esperado: só o
        # asset configurado merece aviso quando some.
        if not path.exists():
            if path == configured:
                log.warning("[pet] asset não encontrado: %s", path)

            log.debug("[pet] ignorado (ausente): %s", path)

            continue

        if not is_supported(path):
            log.debug("[pet] ignorado (sem decodificador): %s", path)

            continue

        image = load_image(path)

        if image is not None:
            return build_frames(image, grid, loop), path

    log.warning("[pet] nenhum asset carregável em %s", directory)

    return None, None
