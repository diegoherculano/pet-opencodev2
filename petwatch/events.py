"""Tradução de eventos do opencode para estados do pet.

A ordem das regras importa: as primeiras que casam vencem e, se nenhuma
casa, o estado atual é preservado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .states import (
    STATE_IDLE,
    STATE_WAITING,
    STATE_WORKING,
)

# ------------------------------------------------------------
# Tipos de evento conhecidos
# ------------------------------------------------------------

#: Eventos de sessão cujo estado vem do payload, não do nome.
SESSION_STATUS_EVENTS = frozenset({"session.status", "session.updated"})

BUSY_STATUSES = frozenset({"busy", "working", "running", "retry"})
IDLE_STATUSES = frozenset({"idle", "ready"})

#: Campos que podem conter o tipo do evento, em ordem de preferência.
TYPE_KEYS = ("type", "event", "name")

#: Campos que podem conter o status da sessão, em ordem de preferência.
STATUS_KEYS = ("status", "state")


@dataclass(frozen=True, slots=True)
class StateRule:
    """Casamento de um evento com um estado.

    A regra casa quando ``event_type`` está em ``exact`` **ou** contém
    algum item de ``contains``. Quando ``statuses`` está preenchido, o
    status do payload precisa pertencer ao conjunto.
    """

    state: str
    exact: frozenset[str] = frozenset()
    contains: tuple[str, ...] = ()
    statuses: frozenset[str] = frozenset()

    def matches(self, event_type: str, status: str | None) -> bool:
        if event_type not in self.exact:
            if not any(fragment in event_type for fragment in self.contains):
                return False

        if self.statuses:
            return status in self.statuses

        return True


#: Regras em ordem de precedência.
STATE_RULES: tuple[StateRule, ...] = (
    # Sessão
    StateRule(STATE_WORKING, exact=SESSION_STATUS_EVENTS, statuses=BUSY_STATUSES),
    StateRule(STATE_IDLE, exact=SESSION_STATUS_EVENTS, statuses=IDLE_STATUSES),
    # Trabalho
    StateRule(
        STATE_WORKING,
        contains=(
            "step.started",
            "tool.called",
            "message.updated",
            "message.part.updated",
        ),
    ),
    # Retry
    StateRule(STATE_WORKING, contains=("retry", "retried")),
    # Perguntas / permissões
    StateRule(
        STATE_WAITING,
        contains=(
            "permission.asked",
            "permission.v2.asked",
            "question.asked",
            "question.v2.asked",
            "form.created",
        ),
    ),
    # Resposta
    StateRule(
        STATE_WORKING,
        contains=(
            "permission.replied",
            "permission.rejected",
            "form.replied",
            "form.cancelled",
        ),
    ),
    # Finalização
    StateRule(STATE_IDLE, exact=frozenset({"session.idle", "session.completed"})),
)


def _nested(data: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = data.get(key)

    return value if isinstance(value, Mapping) else None


def _first_string(data: Mapping[str, Any], keys) -> str | None:
    for key in keys:
        value = data.get(key)

        if isinstance(value, str):
            return value

    return None


def extract_event_type(
    event_name: str | None,
    data: Any,
) -> str | None:
    """Nome do evento: o do stream, senão o do payload."""

    if event_name:
        return event_name

    if not isinstance(data, Mapping):
        return None

    found = _first_string(data, TYPE_KEYS)

    if found:
        return found

    properties = _nested(data, "properties")

    if properties is not None:
        return _first_string(properties, ("type",))

    return None


def extract_status(data: Any) -> str | None:
    """Status da sessão, procurado no payload e em ``properties``/``session``."""

    if not isinstance(data, Mapping):
        return None

    sources: list[Mapping[str, Any]] = [data]

    for key in ("properties", "session"):
        nested = _nested(data, key)

        if nested is not None:
            sources.append(nested)

    for source in sources:
        found = _first_string(source, STATUS_KEYS)

        if found:
            return found.lower()

    return None


def state_from_event(
    event_name: str | None,
    data: Any,
) -> str | None:
    """Estado correspondente ao evento, ou ``None`` para manter o atual."""

    event_type = extract_event_type(event_name, data)

    if not event_type:
        return None

    event_type = event_type.lower()
    status = extract_status(data)

    for rule in STATE_RULES:
        if rule.matches(event_type, status):
            return rule.state

    return None
