"""Preferências do usuário, guardadas em ``~/.config/petwatch/prefs.json``.

Escopo propositalmente pequeno: só o que o usuário escolhe pelo menu.
Se o arquivo não existir, estiver corrompido ou for de outra versão, o
padrão é usado — o pet sempre abre.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .sizes import DEFAULT_SIZE_KEY

log = logging.getLogger(__name__)

PREFS_PATH = Path.home() / ".config" / "petwatch" / "prefs.json"

#: Versão do formato; incompatível cai no padrão em vez de quebrar.
PREFS_VERSION = 1


def default_prefs() -> dict[str, Any]:
    return {
        "version": PREFS_VERSION,
        "theme": None,
        "size": DEFAULT_SIZE_KEY,
        "always_on_top": True,
    }


def load_prefs(path: Path | None = None) -> dict[str, Any]:
    """Lê as preferências; devolve os padrões se não der para ler."""

    path = path or PREFS_PATH

    prefs = default_prefs()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))

    except FileNotFoundError:
        return prefs

    except Exception as exc:
        log.warning("[pet] preferências ilegíveis em %s: %s", path, exc)

        return prefs

    if not isinstance(raw, dict):
        return prefs

    if raw.get("version") != PREFS_VERSION:
        log.info("[pet] preferências em outra versão; usando padrão")

        return prefs

    theme = raw.get("theme")

    if isinstance(theme, str) and theme:
        prefs["theme"] = theme

    size = raw.get("size")

    if isinstance(size, str) and size:
        # A validação acontece na leitura; um valor desconhecido cai no
        # preset padrão sem quebrar o app.
        prefs["size"] = size

    on_top = raw.get("always_on_top")

    if isinstance(on_top, bool):
        prefs["always_on_top"] = on_top

    return prefs


def save_prefs(prefs: dict[str, Any], path: Path | None = None) -> bool:
    """Grava as preferências. Devolve ``False`` se não conseguiu."""

    path = path or PREFS_PATH

    payload = default_prefs()
    payload.update(prefs)
    payload["version"] = PREFS_VERSION

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Escreve ao lado e troca, para não deixar arquivo pela metade.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    except Exception as exc:
        log.warning("[pet] não consegui salvar %s: %s", path, exc)
        return False

    return True
