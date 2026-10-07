"""Parser do stream SSE (``text/event-stream``).

Um evento é formado por linhas ``event:``/``data:`` e encerrado por uma
linha vazia; linhas iniciadas por ``:`` são comentários (o opencode manda
``: heartbeat``). O parser é um gerador, então o consumidor decide quando
parar e o stream fecha junto.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SseEvent:
    """Um evento já despachado pelo stream."""

    name: str | None
    data: Any


def read_lines(
    source,
    stop_event: threading.Event,
) -> Iterator[bytes]:
    """Itera as linhas de ``source`` até parar ou o stream acabar.

    ``source`` só precisa de um ``readline()`` que devolve bytes — serve
    tanto para uma resposta HTTP quanto para
    :class:`petwatch.http.Connection`.
    """

    while not stop_event.is_set():
        raw = source.readline()

        if not raw:
            return

        yield raw


def decode(raw: bytes | str) -> str:
    """Normaliza uma linha do stream para ``str``."""

    try:
        return raw.decode("utf-8", errors="replace")

    except AttributeError:
        return raw


def parse_payload(data: str) -> Any:
    """JSON quando possível, texto puro caso contrário."""

    try:
        return json.loads(data)

    except Exception:
        return data


def parse_sse_stream(
    lines,
    stop_event: threading.Event,
) -> Iterator[SseEvent]:
    """Gera os eventos presentes em ``lines``.

    ``lines`` deve ser um iterável de ``bytes``/``str`` — use
    :func:`read_lines` para extrair isso de uma resposta HTTP.
    """

    event_name: str | None = None
    data_lines: list[str] = []

    for raw in lines:
        if stop_event.is_set():
            return

        line = decode(raw).rstrip("\r\n")

        if not line:
            if data_lines:
                yield SseEvent(
                    name=event_name,
                    data=parse_payload("\n".join(data_lines)),
                )

            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    log.debug("[pet] stream encerrado sem linha vazia final")


def consume(
    lines,
    stop_event: threading.Event,
    on_event: Callable[[str | None, Any], None],
) -> None:
    """Consome o stream chamando ``on_event(nome, dados)`` por evento."""

    for event in parse_sse_stream(lines, stop_event):
        on_event(event.name, event.data)
