"""Uma instância de pet, e como cada plataforma garante isso.

A API é a mesma nos dois sistemas, porque é a que :mod:`petwatch.app`
precisa:

===============  ==================================================
``claim()``     toma a instância; ``False`` = já existe outro pet
``running()``   diz se há pet, sem virar o dono
``record_pid()`` grava o pid do processo que ficou de pé
``release()``   devolve
``request_stop()`` pede para o outro encerrar
===============  ==================================================

**POSIX** usa :class:`petwatch.daemon.SingleInstance`: um ``flock`` em
``pet.pid``, e o ``--stop`` manda ``SIGTERM``. Está tudo em ``daemon.py``
porque nasceu lá, e é a referência do comportamento do app.

**Windows** usa o named pipe de :mod:`petwatch.pipe`, porque não há
``fcntl`` e porque ``SIGTERM`` no Windows é ``TerminateProcess`` — uma
morte seca que pula o ``shutdown()`` e a gravação das preferências. No
Windows o pipe faz as duas coisas: o primeiro que abre o nome é o dono da
instância, e é por ele que o ``--stop`` chega ao caminho limpo do menu.

Uma diferença de fluxo que importa. No POSIX o processo **original** toma
o lock e o filho o herança pelo ``fork``; o descrito viaja com o processo.
No Windows o pai apenas *pergunta* e quem toma o pipe é o filho, porque
herdar handle entre processos exigiria o ``STARTUPINFO`` reservado que o
``subprocess`` não expõe. A janela entre a pergunta e a resposta é de
poucos milissegundos e não tem consequência: se dois ``petwatch.exe``
disparados no mesmo instante passarem os dois, o segundo filho falha ao
assumir o pipe e sai com código 1, dizendo que já existe um pet.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .config import PID_PATH, STATE_DIR, ensure_state_dir
from .pipe import PING, STOP, PipeServer, ask

log = logging.getLogger(__name__)


class PipeInstance:
    """Instância única garantida pelo named pipe.

    O pipe é o lock: nome é exclusivo no Windows, então só um processo
    consegue abrir o listener, e o que não consegue é o segundo pet. O
    ``pet.pid`` continua sendo gravado — é o registro legível de quem
    está rodando, e o ``--stop`` do POSIX dependeria dele.
    """

    def __init__(
        self,
        path: Path = PID_PATH,
        state_dir: Path = STATE_DIR,
    ) -> None:
        self.path = path
        self.state_dir = state_dir

        #: Pid de quem está rodando, quando outro processo o tem.
        self.owner: int | None = None

        self._server: PipeServer | None = None

        #: Para onde vai o ``quit()`` quando o outro pede para parar. É
        #: um sinal do Qt, não uma chamada: o pedido chega numa thread
        #: que não é a da interface.
        self._on_stop: Callable[[], None] | None = None

    # ------------------------------------------------------------
    # Instância única
    # ------------------------------------------------------------

    def claim(self) -> bool:
        """Assume o pipe. ``False`` significa que outro pet já o tem."""

        # O diretório de estado é criado aqui, e não em :func:`running`:
        # perguntar se há pet rodando não pode deixar rastro na máquina.
        ensure_state_dir()

        server = PipeServer(self._answer, state_dir=self.state_dir)

        if not server.start():
            # Quem perdeu o bind pergunta quem ganhou, para o erro trazer
            # o pid e não um "já existe um pet" sem dono.
            self.owner = self._ping()

            return False

        self._server = server

        self.owner = None

        return True

    def running(self) -> bool:
        """Diz se há outro processo escutando, sem tomar o pipe."""

        pid = self._ping()

        self.owner = pid

        return pid is not None

    def record_pid(self) -> int:
        """Grava o pid do processo atual e o passa a servir no ``ping``."""

        pid = os.getpid()

        server = self._server

        if server is None:
            raise RuntimeError("claim() precisa vir antes de record_pid()")

        server.record_pid(pid)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            self.path.write_text(f"{pid}\n", encoding="ascii")

        except OSError as exc:
            # O pid no arquivo é registro, não garantia: a garantia é o
            # pipe. Falhar aqui não pode impedir o pet de abrir.
            log.warning("[pet] não consegui gravar %s: %s", self.path, exc)

        self.owner = pid

        return pid

    def release(self) -> None:
        """Fecha o pipe. Idempotente, e seguro sem ``claim()``."""

        server = self._server

        self._server = None

        if server is not None:
            server.stop()

    # ------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------

    def request_stop(self, pid: int | None = None) -> bool:
        """Pede para o outro encerrar, pelo caminho limpo do menu.

        O ``pid`` é aceito e ignorado: existe para a mesma assinatura do
        :meth:`petwatch.daemon.SingleInstance.request_stop`, e é o pipe
        que sabe de quem é o pedido. Passar o pid errado aqui não muda
        nada, porque quem decide é o servidor do outro lado.
        """

        reply = ask(STOP, state_dir=self.state_dir)

        if not reply:
            return False

        ok = reply[0] if isinstance(reply, tuple) else False

        return bool(ok)

    def bind_quit(self, on_stop: Callable[[], None]) -> None:
        """Liga o pipe ao ``quit()`` do app.

        O pedido chega numa thread que não é a da interface, então o
        ``quit()`` é emitido como sinal — é o mesmo encanamento do
        monitor e do poll, pelo mesmo motivo.
        """

        self._on_stop = on_stop

    # ------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------

    def _ping(self) -> int | None:
        """Pid de quem responde, ou ``None``."""

        reply = ask(PING, state_dir=self.state_dir)

        if not reply or not isinstance(reply, tuple) or not reply[0]:
            return None

        pid = reply[1]

        return pid if isinstance(pid, int) and pid > 0 else None

    def _answer(self, request: str) -> tuple[bool, object]:
        """Resposta do servidor para um pedido do cliente."""

        if request == PING:
            pid = self._server.pid if self._server is not None else None

            return (pid is not None, pid)

        if request == STOP:
            pid = self._server.pid if self._server is not None else None

            if self._on_stop is not None:
                self._on_stop()

            return (True, pid)

        return (False, f"pedido desconhecido: {request!r}")


def open_instance(
    path: Path = PID_PATH,
    state_dir: Path = STATE_DIR,
    platform: str | None = None,
):
    """A instância única da plataforma.

    O parâmetro ``platform`` existe para o teste não depender da máquina em
    que roda: o que importa é o caso do Windows, e ele é o que não existe
    no CI Linux.
    """

    windows = platform if platform is not None else sys.platform

    if windows.startswith("win"):
        return PipeInstance(path, state_dir)

    from .daemon import SingleInstance

    return SingleInstance(path)


def serve_quit(instance, on_stop: Callable[[], None]) -> None:
    """Liga o encerramento pedido de fora ao ``quit()`` do app.

    Só o Windows tem o que ligar — no POSIX o caminho é o sinal, e o app já
    o trata. Chamar isto com uma instância de :class:`SingleInstance` não
    faz nada, e é por isso que a função existe em vez de um método: o
    ``app.run_app`` não precisa perguntar de que plataforma veio o pet.
    """

    bind = getattr(instance, "bind_quit", None)

    if bind is not None:
        bind(on_stop)
