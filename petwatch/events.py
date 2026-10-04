"""Tradução de eventos do opencode para estados do pet.

A ordem das regras importa: as primeiras que casam vencem e, se nenhuma
casa, o estado atual é preservado.

Os nomes foram conferidos contra o **stream de verdade** (o SSE de
``/api/event`` de um servidor 2.0.22 rodando, não os literais do bundle):
o barramento emite ``permission.asked`` e ``form.created`` — a *rota* HTTP
que cria esses pedidos é que se chama ``session.permission.create`` /
``session.form.create``, e foi ela que quase entrou aqui como se fosse o
nome do evento. As regras por substring no fim do arquivo mantêm
compatibilidade com versões anteriores.

A pergunta que a ferramenta ``question`` do opencode faz ao usuário é um
formulário, então chega como ``form.created``.

**O estado "aguardando" não sai daqui.** A documentação v2 diz que
``GET /api/event`` é *"volatile by contract: ... events during
disconnection are missed"* e que ele traz eventos *"across all server
locations"* — o servidor é compartilhado e serve todos os projetos.
Dizer "o pet está esperando o usuário" a partir de um stream que é
global e perde eventos é o que produzia falso positivo: uma resposta
perdida deixava o balão travado para sempre, e um ``form.created`` de
outro projeto travava o balão deste.

O evento de pedido aqui é só **gatilho**: :func:`is_ask_event` e
:func:`is_release_event` dizem que algo mudou, e quem decide é
:mod:`petwatch.pending`, perguntando ``GET /api/form`` e
``GET /api/permission/request``. Ver
:class:`~petwatch.sessions.SessionBoard`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .states import (
    STATE_IDLE,
    STATE_WORKING,
)

# ------------------------------------------------------------
# Eventos com nome exato, conferidos no opencode instalado
# ------------------------------------------------------------

#: O agente está produzindo algo: texto, raciocínio, chamada de
#: ferramenta, execução, compactação, edição de arquivo ou comando de
#: shell. As três últimas famílias (file/fs/shell/process) não têm o
#: prefixo ``session.`` no opencode, e sem elas o pet voltaria a "pronto"
#: enquanto a IA edita arquivos ou roda comandos.
WORKING_EVENTS = frozenset({
    # Turno e execução
    "session.execution.started",
    "session.step.started",
    "session.step.streamed",
    "session.step.failed",
    "session.compaction.started",
    "session.retry.scheduled",
    # Texto e raciocínio
    "session.text.started",
    "session.reasoning.started",
    # Ferramentas
    "session.tool.called",
    "session.tool.progress",
    "session.tool.input.started",
    "session.tool.input.delta",
    "session.tool.failed",
    # Shell e processos
    "session.shell.started",
    "shell.created",
    "shell.output",
    "shell.exited",
    # Edição de arquivos
    "file.edited",
    "fs.write",
    "opencode.tool.write",
    # Mensagens (versões anteriores)
    "message.updated",
    "message.part.updated",
})

#: O opencode pediu algo e a sessão está parada esperando o usuário: um
#: pedido de permissão ou um formulário (que é como a ferramenta
#: ``question`` pergunta). Nomeado ``*.asked`` / ``*.created``, e não
#: ``session.*.create`` — esse é o caminho da rota HTTP, não o evento.
#:
#: Este conjunto **não vira estado**: ele acorda a consulta de
#: pendência. Ver :func:`is_ask_event`.
ASK_EVENTS = frozenset({
    "permission.asked",
    "form.created",
})

#: O usuário respondeu; a sessão volta a trabalhar.
RESUME_EVENTS = frozenset({
    "permission.replied",
    "permission.rejected",
    "form.replied",
    "form.cancelled",
})

#: Nomes antigos de pedido, que o opencode emitia em vez dos de cima.
#: Ficam na busca por substring, nunca como nome exato: os
#: ``session.*`` são o caminho da rota HTTP, e é fácil confundi-los com
#: o evento — já foi.
LEGACY_ASK_NAMES = (
    "session.permission.create",
    "session.form.create",
    "permission.v2.asked",
    "question.asked",
    "question.v2.asked",
)

LEGACY_RESUME_NAMES = (
    "session.permission.reply",
    "session.permission.reject",
    "session.form.reply",
    "session.form.cancel",
    "question.replied",
    "question.cancelled",
)

#: Sinais explícitos de que a sessão terminou o turno. Este é o **único**
#: caminho para "pronto" dentro das regras: não existe regra que chegue
#: aqui por falta de caso, porque evento sem regra mantém o estado atual.
IDLE_EVENTS = frozenset({
    "session.idle",
    "turn.idle",
    "session.execution.succeeded",
})

#: Eventos de sessão cujo estado vem do payload, não do nome.
SESSION_STATUS_EVENTS = frozenset({"session.status", "session.updated"})

BUSY_STATUSES = frozenset({"busy", "working", "running", "retry"})
IDLE_STATUSES = frozenset({"idle", "ready"})

#: Campos que podem conter o tipo do evento, em ordem de preferência.
TYPE_KEYS = ("type", "event", "name")

#: Campos que podem conter o status da sessão, em ordem de preferência.
STATUS_KEYS = ("status", "state")

#: Campos que podem conter o ``sessionID``, em ordem de preferência.
SESSION_KEYS = ("sessionID", "sessionId", "session_id")


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


#: Regras em ordem de precedência. Os nomes exatos vêm primeiro, porque
#: são precisos; as regras por substring ficam no fim, só para
#: compatibilidade com versões antigas do opencode.
#:
#: **Nenhuma regra produz ``waiting``**: quem decide o "aguardando" é a
#: consulta de pendência ao servidor, não o stream — ver
#: :mod:`petwatch.pending`.
STATE_RULES: tuple[StateRule, ...] = (
    # Resposta a um pedido: a espera acabou e a sessão volta a trabalhar
    StateRule(STATE_WORKING, exact=RESUME_EVENTS),
    StateRule(STATE_WORKING, contains=LEGACY_RESUME_NAMES),
    # Fim de turno
    StateRule(STATE_IDLE, exact=IDLE_EVENTS),
    # Sessão com status explícito no payload
    StateRule(STATE_WORKING, exact=SESSION_STATUS_EVENTS, statuses=BUSY_STATUSES),
    StateRule(STATE_IDLE, exact=SESSION_STATUS_EVENTS, statuses=IDLE_STATUSES),
    # Atividade do agente
    StateRule(STATE_WORKING, exact=WORKING_EVENTS),
    # Retry
    StateRule(STATE_WORKING, contains=("retry", "retried")),
    # Compatibilidade: nomes de versões anteriores do opencode
    StateRule(
        STATE_WORKING,
        contains=(
            "step.started",
            "tool.called",
            "message.updated",
            "message.part.updated",
        ),
    ),
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


def rule_for_event(
    event_name: str | None,
    data: Any,
) -> StateRule | None:
    """Primeira regra que casa com o evento, ou ``None``."""

    event_type = extract_event_type(event_name, data)

    if not event_type:
        return None

    event_type = event_type.lower()
    status = extract_status(data)

    for rule in STATE_RULES:
        if rule.matches(event_type, status):
            return rule

    return None


def state_from_event(
    event_name: str | None,
    data: Any,
) -> str | None:
    """Estado correspondente ao evento, ou ``None`` para manter o atual.

    Função pura: cada evento é julgado sozinho, e nenhum evento vira
    ``waiting`` — o "aguardando" vem da consulta de pendência ao
    servidor (ver :mod:`petwatch.pending`).
    """

    rule = rule_for_event(event_name, data)

    return rule.state if rule is not None else None


# ------------------------------------------------------------
# Gatilhos: o pedido chegou, o pedido foi respondido
# ------------------------------------------------------------


def _event_type(event_name: str | None, data: Any) -> str | None:
    found = extract_event_type(event_name, data)

    return found.lower() if found else None


def extract_session_id(data: Any) -> str | None:
    """Sessão dona do evento, quando houver.

    É o que permite um balão por instância: o mesmo ``session.tool.called``
    é "trabalhando" na aba que chamou a ferramenta e nada nas outras. O
    campo vem dentro de ``data`` — o envelope do stream é
    ``{id, created, type, location, data}``.
    """

    if not isinstance(data, Mapping):
        return None

    nested = _nested(data, "data")

    if nested is not None:
        found = _first_string(nested, SESSION_KEYS)

        if found:
            return found

    return _first_string(data, SESSION_KEYS)


def is_ask_event(event_name: str | None, data: Any) -> bool:
    """O stream trouxe um pedido novo do opencode.

    Não diz que o pet deve esperar: diz que **vale conferir**. Quem
    responde é ``GET /api/form`` / ``GET /api/permission/request``, e é
    essa consulta que impede o falso positivo — um ``form.created`` de
    outro projeto, ou de um formulário que já foi respondido sem o
    stream ter contado, não viram "aguardando".
    """

    event_type = _event_type(event_name, data)

    if not event_type:
        return False

    if event_type in ASK_EVENTS:
        return True

    return any(fragment in event_type for fragment in LEGACY_ASK_NAMES)


def is_release_event(event_name: str | None, data: Any) -> bool:
    """O stream trouxe a resposta a um pedido.

    Também é só um gatilho: solta a trava de degradação do
    :class:`~petwatch.sessions.SessionBoard` depressa, mas quem confirma
    que a espera acabou é a mesma consulta de pendência.
    """

    event_type = _event_type(event_name, data)

    if not event_type:
        return False

    if event_type in RESUME_EVENTS:
        return True

    return any(fragment in event_type for fragment in LEGACY_RESUME_NAMES)
