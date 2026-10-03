"""Descoberta do servidor local do opencode e credenciais.

A senha vem do próprio CLI. As portas candidatas vêm do ``ss``, e cada
candidata é validada com um GET em ``/api/event`` — o mesmo endpoint que o
monitor consome depois.
"""

from __future__ import annotations

import base64
import logging
import re
import socket
import subprocess
from typing import Iterable

from .config import (
    EVENT_PATH,
    HOST,
    PASSWORD_COMMAND,
    PASSWORD_TIMEOUT,
    PORT_SCAN_TIMEOUT,
    PROBE_CONNECT_TIMEOUT,
    PROBE_REQUEST_TIMEOUT,
    STREAM_TIMEOUT,
    USERNAME,
)
from .http import Connection, HttpError

log = logging.getLogger(__name__)

#: Extrai ``:porta`` de uma linha do ``ss -ltnp``.
PORT_PATTERN = re.compile(r":(\d+)\b")


def find_opencode_ports() -> list[int]:
    """Portas em escuta de processos opencode, ordenadas."""

    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            timeout=PORT_SCAN_TIMEOUT,
            check=False,
        )

    except Exception as exc:
        log.debug("[pet] ss indisponível: %s", exc)
        return []

    ports: set[int] = set()

    for line in result.stdout.splitlines():
        if "opencode" not in line:
            continue

        for value in PORT_PATTERN.findall(line):
            try:
                port = int(value)

            except ValueError:
                continue

            if 1 <= port <= 65535:
                ports.add(port)

    return sorted(ports)


def get_opencode_password() -> str | None:
    """Senha do serviço, ou ``None`` se o CLI falhar."""

    try:
        result = subprocess.run(
            list(PASSWORD_COMMAND),
            capture_output=True,
            text=True,
            timeout=PASSWORD_TIMEOUT,
            check=False,
        )

    except Exception as exc:
        log.debug("[pet] CLI indisponível: %s", exc)
        return None

    if result.returncode != 0:
        return None

    return result.stdout.strip() or None


def make_auth_header(password: str) -> str:
    """Header ``Authorization`` Basic para o usuário configurado."""

    raw = f"{USERNAME}:{password}".encode("utf-8")

    return "Basic " + base64.b64encode(raw).decode("ascii")


def event_headers(password: str, *, keep_alive: bool = False) -> dict[str, str]:
    """Headers da requisição de ``/api/event``."""

    headers = {
        "Authorization": make_auth_header(password),
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }

    if keep_alive:
        headers["Connection"] = "keep-alive"

    return headers


def connect(
    port: int,
    password: str,
    *,
    keep_alive: bool = False,
    timeout: float = STREAM_TIMEOUT,
) -> Connection:
    """Prepara uma conexão com o stream de eventos de ``port``.

    Não abre nada ainda; use como gerenciador de contexto ou chame
    :meth:`~petwatch.http.Connection.open`.
    """

    return Connection(
        HOST,
        port,
        EVENT_PATH,
        event_headers(password, keep_alive=keep_alive),
        timeout=timeout,
    )


def open_event_stream(port: int, password: str) -> Connection:
    """Abre o stream SSE que o monitor consome."""

    return connect(port, password, keep_alive=True).open()


def port_accepts_connections(
    port: int,
    *,
    timeout: float = PROBE_CONNECT_TIMEOUT,
) -> bool:
    """Verifica se ``port`` aceita conexão TCP em ``HOST``."""

    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True

    except Exception:
        return False


def server_is_alive(port: int, password: str) -> bool:
    """Diz se a porta responde 200 em ``/api/event``."""

    if not port_accepts_connections(port):
        return False

    connection = connect(port, password, timeout=PROBE_REQUEST_TIMEOUT)

    try:
        return connection.open().status == 200

    except (HttpError, OSError) as exc:
        log.debug("[pet] porta %s não respondeu: %s", port, exc)

        return False

    finally:
        connection.close()


def find_opencode_server(
    password: str,
    ports: Iterable[int] | None = None,
) -> int | None:
    """Primeira porta que responde, ou ``None``."""

    if ports is None:
        ports = find_opencode_ports()

    for port in ports:
        if server_is_alive(port, password):
            return port

    return None
