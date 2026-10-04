"""Estados possíveis do pet e os rótulos do balão.

O balão tem duas linhas, como na referência visual: um título em negrito e
um subtítulo mais claro e discreto.

Um estado com as duas linhas vazias é mudo: nada é escrito e nem o balão é
desenhado, porque uma caixa vazia no lugar de um texto parece defeito e não
silêncio. É o caso de ``connecting`` — até o servidor responder não há
nada de verdade a dizer, e o sprite sozinho já avisa que o pet está vivo.
"""

from __future__ import annotations

from typing import Mapping

STATE_CONNECTING = "connecting"
STATE_IDLE = "idle"
STATE_WORKING = "working"
STATE_WAITING = "waiting"

#: ``estado -> (titulo em negrito, subtítulo)``. Linhas vazias significam
#: "não escreve nada" e o balão nem chega a ser pintado.
STATE_LABELS: Mapping[str, tuple[str, str]] = {
    STATE_CONNECTING: ("", ""),
    STATE_IDLE: ("Ready", "waiting for you"),
    STATE_WORKING: ("Thinking", "working on it"),
    STATE_WAITING: ("Waiting", "needs your answer"),
}

#: Estados aceitos por :meth:`petwatch.ui.pet_widget.PetRenderer.set_state`.
VALID_STATES = frozenset(STATE_LABELS)

#: Cor do texto quando o opencode **precisa do usuário**. Âmcar escuro
#: (#B45309): é a única cor do conjunto que pede olhada, e continua
#: legível sobre o fundo claro do balão. Um vermelho ou um laranja
#: saturado-gritariam a cada tique de animação; um verde "tudo certo"
#: competiria com o "aguardando" e trocaria um alarme por outro.
ACTION_COLOR = "#B45309"

#: ``estado -> cor do título``. Estados sem cor usam a cor do tema.
#:
#: Só o "aguardando" é colorido, de propósito: a cor é o sinal de "aqui
#: tem algo para você responder", e pintar os outros transformaria a tela
#: num painel de status em vez de num aviso.
STATE_ACCENTS: Mapping[str, str] = {
    STATE_WAITING: ACTION_COLOR,
}


def accent_for(state: str) -> str | None:
    """Cor de destaque do estado, ou ``None`` para o tema decidir."""

    return STATE_ACCENTS.get(state)


def labels_for(state: str) -> tuple[str, str]:
    """Título e subtítulo de ``state``; vazio para estado desconhecido."""

    return STATE_LABELS.get(state, ("", ""))
