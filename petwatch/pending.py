"""O que está esperando o usuário, segundo o **servidor**.

Este módulo existe porque o stream de eventos não serve para decidir
isso. A documentação do opencode v2 é explícita em dois pontos:

- ``GET /api/event`` — *"Subscribe to native events and plugin RPC
  events **across all server locations**. Volatile by contract: a slow
  consumer overflows and fails the stream, and **events during
  disconnection are missed**."*
- ``GET /api/form`` / ``GET /api/permission/request`` — *"Retrieve
  pending forms/permission requests **for a location**."*

Ou seja: o stream é **global** (um servidor compartilhado serve todos
os projetos) e **perde eventos**. Deduzir "o pet está esperando o
usuário" dele produz falso positivo em dois jeitos, e os dois foram
observados:

1. A resposta (``form.replied``, ``form.cancelled``,
   ``permission.replied``) se perde no caminho. A trava que existia no
   :mod:`petwatch.events` só era solta por evento de resposta ou por
   reconexão, e o watchdog por desenho nunca age em ``waiting`` — então
   o balão ficava em "Waiting / needs your answer", pulsando, sem nada
   pendente.
2. Um ``form.created`` de **outro projeto** chega no mesmo stream (ele é
   de todas as locations) e trava o balão de um projeto em que o
   usuário não está.

A correção é trocar a fonte: o evento só **acorda** o pet, e a
consulta ao servidor **decide**. Enquanto o servidor responder que há
formulário ou permissão pendente, o pet espera; quando responder que
não há, ele solta — mesmo que o stream nunca tenha contado que a
resposta aconteceu.

Escopo por projeto: as duas rotas são escopadas por *location*, e
``GET /api/project`` diz quais são. Quem escolhe o que observar é
:func:`watched_directories` — a variável ``PETWATCH_DIRECTORY`` para
assistir um projeto só, ou a lista de projetos do servidor.

Este módulo são só as **consultas**. Quem as chama em laço é
:class:`petwatch.sessions.StatusPoller`, porque o mesmo laço também
pergunta ``GET /api/session/active`` (quem está em ação) e
``GET /api/session/{id}`` (o nome de cada uma).

Degradação: um servidor mais antigo (v1, sem essas rotas) responde 404.
Aí o poller desliga e a trava por stream assume sozinha.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from .config import (
    FORM_PATH,
    HOST,
    MAX_WATCHED_PROJECTS,
    PENDING_TIMEOUT,
    PERMISSION_PATH,
    PROJECT_PATH,
    WATCH_DIRECTORY_ENV,
)
from .discovery import make_auth_header
from .http import Connection, HttpError

log = logging.getLogger(__name__)

#: Tipos de pedido que o opencode v2 mantém pendentes: um formulário
#: (é assim que a ferramenta ``question`` pergunta) e um pedido de
#: permissão.
KIND_FORM = "form"
KIND_PERMISSION = "permission"


@dataclass(frozen=True, slots=True)
class PendingAsk:
    """Um pedido esperando o usuário, como o servidor o descreve."""

    kind: str

    id: str

    session_id: str | None

    directory: str | None

    title: str | None = None

    def describe(self) -> str:
        """Texto curto para o log."""

        what = self.title or self.kind

        return f"{self.kind} {self.id} ({what})"


# ------------------------------------------------------------
# Transporte
# ------------------------------------------------------------


def json_headers(password: str) -> dict[str, str]:
    """Headers de uma consulta JSON ao servidor local."""

    return {
        "Authorization": make_auth_header(password),
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }


def get_json(
    port: int,
    password: str,
    path: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> tuple[int, Any]:
    """``GET`` que devolve ``(status, corpo)``.

    Devolve o status mesmo com erro: um 404 nestas rotas significa
    "servidor antigo, sem a rota", que é informação — não exceção.
    """

    connection = Connection(HOST, port, path, json_headers(password),
                            timeout=timeout)

    try:
        connection.open()

        return connection.status, connection.read_json()

    finally:
        connection.close()


def location_query(directory: str) -> str:
    """Query de *location* no formato ``deepObject`` da documentação.

    A spec declara ``style: deepObject`` com ``explode: true``, que é
    ``?location[directory]=...``.
    """

    return f"?location[directory]={quote(directory, safe='')}"


def _envelope_list(payload: Any) -> list[Any]:
    """Lista do corpo ``{location, data}``; tolera lista solta."""

    if isinstance(payload, list):
        return payload

    if isinstance(payload, Mapping):
        data = payload.get("data")

        if isinstance(data, list):
            return data

    return []


def _envelope_directory(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        location = payload.get("location")

        if isinstance(location, Mapping):
            directory = location.get("directory")

            if isinstance(directory, str):
                return directory

    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _pending_ask(kind: str, item: Any, directory: str | None) -> PendingAsk | None:
    if not isinstance(item, Mapping):
        return None

    identifier = _text(item.get("id"))

    if identifier is None:
        return None

    return PendingAsk(
        kind=kind,
        id=identifier,
        session_id=_text(item.get("sessionID")),
        directory=directory,
        title=_text(item.get("title")),
    )


# ------------------------------------------------------------
# As três consultas
# ------------------------------------------------------------


def known_projects(
    port: int,
    password: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> list[str]:
    """Diretórios dos projetos que o servidor conhece.

    ``GET /api/project`` → ``Project[]``, cada um com ``canonical``.
    É de onde saem as locations a consultar; um diretório que o servidor
    recuse não derruba os outros.
    """

    status, payload = get_json(port, password, PROJECT_PATH, timeout=timeout)

    if status != 200:
        log.debug("[pet] %s respondeu %s", PROJECT_PATH, status)

        return []

    if not isinstance(payload, list):
        return []

    directories: list[str] = []

    for project in payload:
        if not isinstance(project, Mapping):
            continue

        canonical = _text(project.get("canonical"))

        if canonical and canonical not in directories:
            directories.append(canonical)

    return directories


def pending_forms(
    port: int,
    password: str,
    directory: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> list[PendingAsk] | None:
    """Formulários pendentes de um projeto.

    ``None`` quando a rota não existe (servidor antigo) — que é
    diferente de lista vazia, que quer dizer "nada esperando".
    """

    path = FORM_PATH + location_query(directory)

    status, payload = get_json(port, password, path, timeout=timeout)

    if status == 404:
        return None

    if status != 200:
        raise HttpError(f"{FORM_PATH}: status {status}")

    resolved = _envelope_directory(payload) or directory

    return [
        ask
        for item in _envelope_list(payload)
        if (ask := _pending_ask(KIND_FORM, item, resolved)) is not None
    ]


def pending_permissions(
    port: int,
    password: str,
    directory: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> list[PendingAsk] | None:
    """Pedidos de permissão pendentes de um projeto.

    ``Permission.Request`` traz ``id``, ``sessionID``, ``action`` e
    ``resources``; não tem título, então a descrição do log cai para a
    ação.
    """

    path = PERMISSION_PATH + location_query(directory)

    status, payload = get_json(port, password, path, timeout=timeout)

    if status == 404:
        return None

    if status != 200:
        raise HttpError(f"{PERMISSION_PATH}: status {status}")

    resolved = _envelope_directory(payload) or directory

    asks: list[PendingAsk] = []

    for item in _envelope_list(payload):
        ask = _pending_ask(KIND_PERMISSION, item, resolved)

        if ask is None:
            continue

        action = _text(item.get("action"))

        asks.append(
            ask if ask.title is not None
            else PendingAsk(ask.kind, ask.id, ask.session_id, ask.directory,
                            action or "permissão"),
        )

    return asks


def pending_asks(
    port: int,
    password: str,
    directories: Iterable[str],
    *,
    timeout: float = PENDING_TIMEOUT,
) -> list[PendingAsk] | None:
    """Todos os pedidos pendentes dos projetos observados.

    ``None`` quando alguma das rotas não existe — o sinal para o pet
    parar de consultar e voltar ao que o stream diz.

    Um *location* que o servidor rejeita (500 para diretório que não é
    mais um projeto, por exemplo) é **ignorado**, e não derruba a
    consulta dos outros: a lista de projetos envelhece, e um projeto
    apagado não pode fazer o pet parar de avisar sobre os que existem.
    Se *todos* falharem, aí sim é erro — e erro mantém o estado.
    """

    directories = list(directories)

    if not directories:
        # Sem projeto nenhum não há o que perguntar, e isso não é "rota
        # ausente": um servidor novo, sem projeto registrado, é um "não
        # há nada pendente" e não uma degradação.
        return []

    asks: list[PendingAsk] = []

    failed = 0

    for directory in directories:
        try:
            forms = pending_forms(port, password, directory, timeout=timeout)

            if forms is None:
                return None

            permissions = pending_permissions(port, password, directory,
                                              timeout=timeout)

            if permissions is None:
                return None

        except (HttpError, OSError) as exc:
            failed += 1

            log.debug("[pet] location %s ignorada: %s", directory, exc)

            continue

        asks.extend(forms)
        asks.extend(permissions)

    if failed == len(directories):
        raise HttpError("nenhum location respondeu")

    return asks


def watched_directory() -> str | None:
    """Diretório pedido pelo usuário, ou ``None`` para "todos"."""

    raw = os.environ.get(WATCH_DIRECTORY_ENV, "").strip()

    if not raw:
        return None

    return os.path.abspath(os.path.expanduser(raw))


def watched_directories(
    port: int,
    password: str,
    *,
    timeout: float = PENDING_TIMEOUT,
    limit: int = MAX_WATCHED_PROJECTS,
) -> list[str]:
    """Quais locations observar, nesta ordem de preferência."""

    explicit = watched_directory()

    if explicit is not None:
        return [explicit]

    return known_projects(port, password, timeout=timeout)[:limit]


# ------------------------------------------------------------
# Quem está em ação
# ------------------------------------------------------------

#: "Retrieve foreground Session drains currently owned by this OpenCode
#: process. **Sessions absent from the result are inactive.**" É esta
#: rota que define quem merece balão.
ACTIVE_PATH = "/api/session/active"

SESSION_PATH = "/api/session/{sessionID}"


def active_sessions(
    port: int,
    password: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> list[str] | None:
    """IDs das sessões em primeiro plano.

    ``None`` quando a rota não existe ou a consulta falhou: a diferença
    importa, porque "não deu para saber" não pode virar "não há sessão
    nenhuma" — senão o pet apagaria os balões por causa de um timeout.
    """

    status, payload = get_json(port, password, ACTIVE_PATH, timeout=timeout)

    if status != 200 or not isinstance(payload, Mapping):
        log.debug("[pet] %s respondeu %s", ACTIVE_PATH, status)

        return None

    data = payload.get("data")

    if not isinstance(data, Mapping):
        return []

    return [session_id for session_id in data if isinstance(session_id, str)]


def session_info(
    port: int,
    password: str,
    session_id: str,
    *,
    timeout: float = PENDING_TIMEOUT,
) -> dict[str, Any] | None:
    """``GET /api/session/{id}``: título e location de uma instância.

    É o que dá nome ao balão. ``Session.Info`` traz ``title`` e
    ``location`` — e ``location`` é o mesmo diretório que as consultas de
    pendência usam, então o nome do balão e o projeto do pedido batem.
    """

    path = SESSION_PATH.format(sessionID=session_id)

    status, payload = get_json(port, password, path, timeout=timeout)

    if status != 200:
        return None

    if not isinstance(payload, Mapping):
        return None

    data = payload.get("data")

    if isinstance(data, Mapping):
        return dict(data)

    # Alguns clientes embrulham direto; aceitar os dois custa uma linha
    # e evita um balão sem nome.
    return dict(payload)
