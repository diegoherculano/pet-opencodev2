"""Saída do CLI e log nos dois formatos do executável.

O mesmo código é distribuído de duas maneiras, e a diferença não é
cosmética:

- **com console** (``pet.py``, ``--foreground``, ``pip install``) — o que
  o usuário digita no terminal volta no terminal;
- **sem console** (``petwatch.exe`` compilado com ``--noconsole``) — o
  PyInstaller usa a subsistema GUI do Windows, e aí ``sys.stdout`` e
  ``sys.stderr`` chegam ``None``.

O segundo caso é o que este módulo existe para resolver, e ele tem dois
efeitos silenciosos que precisam ser tratados explicitamente:

**O ``print`` estoura.** Todo ``print`` vira :func:`say`, que escreve no
terminal quando existe e, quando não, no log. Quem perde o terminal não
perde a mensagem.

**O ``logging`` perde tudo.** Um ``basicConfig()`` sem argumentos cria um
``StreamHandler`` para ``sys.stderr``; com ``sys.stderr`` ``None``, as
mensagens caem no ``lastResort``, que checa ``if sys.stderr:`` antes de
escrever e descarta a linha **sem aviso nenhum**. O sintoma seria um pet
funcionando com um log vazio e nenhum erro na tela. Por isso
:func:`configure_logging` escolhe o destino explicitamente.

E há o terceiro caso, que é o de um duplo clique: sem terminal e sem
janela, uma mensagem escrita no log não é resposta. :func:`say` aceita
``popup=True`` para os três momentos em que o usuário precisa **ver** o
que aconteceu — ``--status``, ``--stop`` e "já existe um pet rodando".
O lançamento normal não abre popup nenhum: um aviso a cada inicialização
pela Pasta de Inicialização seria pior do que não ter aviso nenhum.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import IS_WINDOWS, LOG_PATH

log = logging.getLogger(__name__)

LOG_FORMAT = "%(message)s"

#: Título das caixas de diálogo. O nome do programa serve de título: quem
#: vê a janela não está olhando o terminal, então é o único contexto que
#: ele tem.
POPUP_TITLE = "petwatch"

#: Ícone de informação (``MB_ICONINFORMATION``) com botão de OK
#: (``MB_OK``). Só o necessário: quem vê a janela não está olhando um
#: terminal, e um "sim"/"não" aqui não teria pergunta por trás.
POPUP_FLAGS = 0x40 | 0x00


def has_console() -> bool:
    """Diz se a saída do processo está ligada a um terminal.

    Não é ``sys.stdout.isatty()``: o que importa é existir. Um
    ``--foreground`` com a saída redirecionada num arquivo ainda é uma
    saída legítima, e ``sys.stdout is None`` é o caso do build sem
    console.
    """

    return sys.stdout is not None


def say(message: str, *, popup: bool = False, error: bool = False) -> None:
    """Fala com quem chamou.

    Terminal quando existe, log quando não, e caixa de diálogo quando o
    usuário não tem como ver nenhum dos dois.

    ``error=True`` manda a frase para o ``stderr`` em vez do ``stdout``.
    Serve para a falha de abertura da janela, que precisa ficar separada
    do resto: quem chama ``python pet.py > saida.txt`` continua vendo o
    traceback e a mensagem de erro na tela, e só o registro do lançamento
    fica no arquivo.
    """

    if sys.stdout is not None:
        stream = sys.stderr if error else sys.stdout

        try:
            print(message, file=stream)

        except Exception as exc:  # stdout fechado é melhor que traceback
            log.debug("[pet] não consegui escrever: %s", exc)

    else:
        _append_to_log(message)

    if popup:
        notify(message)


def _append_to_log(message: str) -> None:
    """Escreve a mensagem no log, sem depender do ``logging``.

    Falhar aqui não pode virar um traceback no lugar da mensagem — quem
    chamou só queria falar com o usuário.
    """

    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")

    except Exception as exc:
        log.debug("[pet] não consegui escrever no log: %s", exc)


def notify(message: str, *, title: str = POPUP_TITLE) -> None:
    """Mostra a mensagem numa caixa de diálogo, quando não há terminal.

    Fora do Windows — e num build com console — isto não faz nada: o
    terminal já é o lugar da resposta, e uma janela a mais seria um
    incômodo.
    """

    if not IS_WINDOWS or has_console():
        return

    # A proteção é aqui, e não dentro de :func:`_message_box`: qualquer
    # falha no caminho até a janela — o ``ctypes`` que não achou o
    # ``user32``, a sessão sem desktop, a caixa que não abriu — precisa
    # ficar longe do ``--stop``, que é um comando cujo único trabalho é
    # dizer que o pet parou.
    try:
        _message_box(title, message)

    except Exception as exc:
        log.debug("[pet] não consegui mostrar a caixa de diálogo: %s", exc)


def _message_box(title: str, text: str) -> None:
    """``MessageBoxW`` via ``ctypes``, que não precisa de dependência."""

    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, title, POPUP_FLAGS)


def configure_logging(
    level: int = logging.INFO,
    log_path: Path | None = None,
) -> None:
    """Aponta o ``logging`` para onde a saída existe.

    Chamar mais de uma vez não duplica handler: quem já configurou não é
    mexido. O ``run_app`` chama isto na partida, que é o primeiro momento
    em que o app existe.

    O handler é montado à mão em vez de ``basicConfig()`` pelo motivo do
    docstring deste módulo: o ``basicConfig`` sem argumentos cria um
    ``StreamHandler`` com ``sys.stderr`` **do momento da chamada**, e num
    build sem console esse é ``None``. O handler existe, falha a cada
    registro, e a mensagem some — com um "--- Logging error ---" que também
    não tem para onde ir.
    """

    root = logging.getLogger()

    if root.handlers:
        return

    root.setLevel(level)

    if sys.stderr is not None:
        handler: logging.Handler = logging.StreamHandler(sys.stderr)

    else:
        # Sem stderr não há para onde ir pelo caminho normal: o destino é
        # o arquivo, e é lá que o log de um ``petwatch.exe`` vai morar.
        path = log_path or LOG_PATH

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            handler = logging.FileHandler(path, encoding="utf-8")

        except Exception as exc:
            # Sem log o app ainda abre e ainda pinta o pet; perder o
            # diagnóstico é melhor que não abrir. O aviso em si vai para
            # o ``lastResort``, que também não tem destino aqui — é por
            # isso que o nível sobe para ``WARNING``: o que restar é
            # problema de verdade.
            log.warning("[pet] sem log em %s: %s", path, exc)

            root.setLevel(logging.WARNING)

            return

    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root.addHandler(handler)