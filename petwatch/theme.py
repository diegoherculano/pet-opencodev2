"""Carregamento do tema de um pet.

Cada tema é um diretório em :data:`petwatch.config.PETS_DIR` contendo um
``pet.json`` e os assets. Exemplo de ``pets/matrix/pet.json``::

    {
        "id": "matrix",
        "displayName": "Matrix",
        "description": "...",
        "spritesheetPath": "spritesheet.webp",

        "scale": 1.0,

        "text": {
            "enabled": true,
            "position": "top",
            "font_size": 15,
            "font_family": "DejaVu Sans Mono"
        }
    }

Os campos ``name``/``asset`` continuam aceitos como alternativas a
``displayName``/``spritesheetPath``, pois temas antigos podem usá-los.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import PETS_DIR

log = logging.getLogger(__name__)

CONFIG_FILENAME = "pet.json"

#: Chave do bloco que descreve a divisão da spritesheet.
FRAMES_KEY = "frames"

#: Todos os spritesheets do repositório têm 1536x1872 pixels, isto é,
#: 8 colunas x 9 linhas de 192x208 — 72 quadros. Serve de padrão e pode
#: ser sobrescrito no ``pet.json`` de um tema.
DEFAULT_COLUMNS = 8
DEFAULT_ROWS = 9

#: Quadros por segundo desejados na animação do sprite. O timer redesenha
#: a 10 Hz, então um valor até 15 resulta em um quadro por tique.
DEFAULT_FPS = 12

#: Chaves aceitas para o nome do tema, em ordem de preferência.
NAME_KEYS = ("displayName", "name", "id")

#: Chaves aceitas para o asset do sprite, em ordem de preferência.
ASSET_KEYS = ("spritesheetPath", "spritePath", "asset", "assetPath", "image")


class ThemeNotFoundError(FileNotFoundError):
    """Tema ausente em ``pets/``."""


@dataclass(frozen=True, slots=True)
class SpriteGrid:
    """Divisão de uma spritesheet em quadros."""

    columns: int = DEFAULT_COLUMNS
    rows: int = DEFAULT_ROWS

    @property
    def frame_count(self) -> int:
        return self.columns * self.rows

    def cell_size(self, width: int, height: int) -> tuple[int, int]:
        return width // self.columns, height // self.rows

    def fits(self, width: int, height: int) -> bool:
        """Diz se a grade divide a imagem sem sobra."""

        if self.columns < 1 or self.rows < 1:
            return False

        if width < 1 or height < 1:
            return False

        return width % self.columns == 0 and height % self.rows == 0

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SpriteGrid:
        """Lê o bloco ``frames`` do ``pet.json``."""

        frames = config.get(FRAMES_KEY) or {}

        if not isinstance(frames, Mapping):
            frames = {}

        try:
            columns = int(frames.get("columns", DEFAULT_COLUMNS))
            rows = int(frames.get("rows", DEFAULT_ROWS))
        except (TypeError, ValueError):
            log.warning("[pet] grade invalida em 'frames'; usando padrao")

            columns, rows = DEFAULT_COLUMNS, DEFAULT_ROWS

        if columns < 1 or rows < 1:
            log.warning("[pet] grade %sx%s invalida; usando padrao", columns, rows)

            columns, rows = DEFAULT_COLUMNS, DEFAULT_ROWS

        return cls(columns=columns, rows=rows)


def _pick(config: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Devolve o primeiro valor não vazio entre ``keys``."""

    for key in keys:
        value = config.get(key)

        if value:
            return value

    return None


def read_loop(frames: Mapping[str, Any]) -> tuple[int, int] | None:
    """Lê ``{"first": 8, "last": 15}`` do bloco ``frames``.

    Serve para escolher qual ação da spritesheet anima. Ausente ou
    inválido, devolve ``None`` e o tema usa a detecção automática.
    """

    if "first" not in frames and "last" not in frames:
        return None

    try:
        first = int(frames.get("first", 0))
        last = int(frames.get("last", first))

    except (TypeError, ValueError):
        log.warning("[pet] 'frames.first/last' invalido; ignorando")

        return None

    if first < 0 or last < first:
        log.warning(
            "[pet] laço %d..%d invalido; usando deteccao automatica",
            first,
            last,
        )

        return None

    return first, last


@dataclass(slots=True)
class PetTheme:
    """Tema resolvido, com os mesmos campos usados pelo renderizador."""

    directory: Path

    config: dict[str, Any] = field(default_factory=dict)

    name: str = ""

    asset_path: Path | None = None

    scale: float = 1.0

    #: Divisão da spritesheet em quadros.
    grid: SpriteGrid = field(default_factory=SpriteGrid)

    #: Quadros por segundo da animação do sprite.
    fps: int = DEFAULT_FPS

    #: Laço de animação forçado pelo ``pet.json``, em índices de quadro.
    #: ``None`` usa a primeira sequência de quadros com conteúdo.
    loop: tuple[int, int] | None = None

    text_enabled: bool = True
    text_position: str = "top"

    #: Família preferida; se não existir, o balão usa a primeira disponível
    #: em :data:`petwatch.ui.bubble.FONT_PREFERENCE`.
    font_family: str = "Inter"

    title_font_size: int = 13
    subtitle_font_size: int = 12

    title_color: str = "#1F1F1F"
    subtitle_color: str = "#9CA3AF"

    background: str = "#FFFFFF"
    background_alpha: int = 246

    border: str = "#E4E4E7"
    border_width: int = 1

    #: Alfa da sombra projetada logo abaixo do balão.
    shadow_alpha: int = 28

    corner_radius: int = 14

    padding_x: int = 14
    padding_y: int = 9

    #: Espaço entre a linha do título e a do subtítulo.
    line_gap: int = 1

    # ------------------------------------------------------------
    # Construção
    # ------------------------------------------------------------

    @classmethod
    def from_directory(cls, directory: Path | str) -> PetTheme:
        directory = Path(directory)

        theme = cls(directory=directory, name=directory.name)

        theme.config = cls._read_config(theme.config_path)

        theme.name = _pick(theme.config, NAME_KEYS) or theme.name

        asset = _pick(theme.config, ASSET_KEYS)

        if asset:
            theme.asset_path = directory / asset

        theme.scale = float(theme.config.get("scale", 1.0))

        theme.grid = SpriteGrid.from_config(theme.config)

        frames = theme.config.get(FRAMES_KEY) or {}

        if isinstance(frames, Mapping):
            try:
                theme.fps = max(1, int(frames.get("fps", DEFAULT_FPS)))
            except (TypeError, ValueError):
                theme.fps = DEFAULT_FPS

            theme.loop = read_loop(frames)

        cls._apply_text_config(theme, theme.config.get("text") or {})

        return theme

    @property
    def config_path(self) -> Path:
        return self.directory / CONFIG_FILENAME

    # ------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------

    @staticmethod
    def _read_config(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        try:
            return json.loads(path.read_text(encoding="utf-8"))

        except Exception as exc:
            log.warning("[pet] erro lendo %s: %s", path, exc)

            return {}

    @staticmethod
    def _apply_text_config(theme: PetTheme, text: Mapping[str, Any]) -> None:
        """Aplica o bloco ``text``; ausências mantêm o padrão do dataclass."""

        theme.text_enabled = bool(text.get("enabled", theme.text_enabled))
        theme.text_position = text.get("position", theme.text_position)

        theme.font_family = text.get("font_family", theme.font_family)

        theme.title_font_size = int(
            text.get("title_font_size", theme.title_font_size)
        )
        theme.subtitle_font_size = int(
            text.get("subtitle_font_size", theme.subtitle_font_size)
        )

        theme.title_color = text.get("title_color", theme.title_color)
        theme.subtitle_color = text.get("subtitle_color", theme.subtitle_color)

        theme.background = text.get("background", theme.background)
        theme.background_alpha = int(
            text.get("background_alpha", theme.background_alpha)
        )

        theme.border = text.get("border", theme.border)
        theme.border_width = int(text.get("border_width", theme.border_width))

        theme.shadow_alpha = int(text.get("shadow_alpha", theme.shadow_alpha))

        theme.corner_radius = int(text.get("corner_radius", theme.corner_radius))

        theme.padding_x = int(text.get("padding_x", theme.padding_x))
        theme.padding_y = int(text.get("padding_y", theme.padding_y))

        theme.line_gap = int(text.get("line_gap", theme.line_gap))


def load_theme(name: str) -> PetTheme:
    """Carrega o tema ``name`` de ``pets/``."""

    directory = PETS_DIR / name

    if not directory.exists():
        raise ThemeNotFoundError(f"Tema não encontrado: {directory}")

    return PetTheme.from_directory(directory)


def list_themes() -> list[str]:
    """Nomes de todos os temas disponíveis, em ordem alfabética."""

    if not PETS_DIR.exists():
        return []

    return sorted(entry.name for entry in PETS_DIR.iterdir() if entry.is_dir())
