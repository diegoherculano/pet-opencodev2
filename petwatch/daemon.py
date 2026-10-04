"""Rodar o pet solto do terminal.

Um ``python pet.py`` comum fica preso ao terminal: o prompt não volta
enquanto a janela estiver aberta, e o ``Ctrl+C`` encerra o aplicativo. Este
módulo faz o oposto — dois ``fork``, um ``setsid``, os descritores padrão
apontados para um arquivo de log, e o prompt de volta na hora.

O que muda para quem usa:

- ``python pet.py`` devolve o terminal imediatamente e sai com código 0;
- ``Ctrl+C`` deixa de valer: o processo não está mais no grupo de jobs do
  shell. Quem encerra é o **Fechar** do menu ou ``pet.py --stop``, que
  manda ``SIGTERM`` — exatamente o caminho limpo do menu;
- tudo que ia aparecer na tela vai para ``pet.log``;
- ``pet.py --status`` diz se há pet rodando e qual é o pid;
- ``pet.py --foreground`` desfaz tudo e é o modo de depurar.

Duas decisões que não são óbvias:

**O desvio vem antes do ``QApplication``.** O Qt precisa abrir a conexão
com o servidor de display (X11 ou Wayland) *depois* do ``setsid``; um
``QApplication`` construído antes do fork não pode ser usado nos dois
processos, e o filho herdaria um objeto já ligado ao socket do pai. Por
isso este módulo não importa o Qt: ele recebe a função que sobe o app e a
chama, lá no processo solto.

**Só uma instância por máquina.** Nada impede ``python pet.py`` duas vezes,
e agora nada impede porque o segundo comando volta na hora. O processo
solto toma um ``flock`` em ``pet.pid`` (:class:`SingleInstance`); o segundo
comando percebe o lock ocupado, diz quem está rodando e sai. O lock
pertence ao descritor aberto, não ao processo: um ``SIGKILL`` no pet faz o
kernel soltá-lo, e o ``pet.pid`` deixado para trás vira só um número velho
em vez de um bloqueio eterno. Por isso o arquivo nunca é apagado, e por
isso a presença do pet é decidida pelo lock, nunca pelo conteúdo.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import logging
import os
import select
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .config import LOG_PATH, PID_PATH

log = logging.getLogger(__name__)

#: Os ``errno`` que significam "outro processo segura o lock". No Linux
#: ``EWOULDBLOCK`` e ``EAGAIN`` são o mesmo número, e o ``EACCES`` é o
#: que o BSD devolve; os três entram porque o ``flock`` é do kernel e o
#: sistema de arquivos pode responder com qualquer um deles.
LOCK_BUSY_ERRNOS = frozenset({errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES})

#: O que o processo solto escreve para o processo original logo depois de
#: subir a janela. O original está esperando isso para devolver o terminal.
READY_OK = "ok"

READY_ERROR = "erro"

#: Quanto tempo o processo original espera por essa confirmação antes de
#: desistir e devolver o terminal mesmo assim. Só o pior caso paga isto: se
#: a janela abriu, a resposta chega em milissegundos.
READY_TIMEOUT = 20.0

#: De quanto em quanto tempo ``--stop`` confere se o processo morreu.
STOP_POLL = 0.1

#: Quanto tempo ``--stop`` espera pelo encerramento antes de desistir.
STOP_TIMEOUT = 10.0

#: O log é virado (uma geração guardada) quando passa deste tamanho. Sem
#: isso um processo que fica dias no ar enche o disco de mensagens.
LOG_MAX_BYTES = 1 << 20


# ------------------------------------------------------------
# Linha de comando
# ------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pet de desktop que observa o opencode.",
        epilog=(
            "Sem opção nenhuma o pet vai para segundo plano e o terminal "
            "volta na hora; --foreground é o modo de depurar."
        ),
    )

    mode = parser.add_mutually_exclusive_group()

    # A segunda opção existe por clareza de quem chama: ``--background`` é
    # o padrão, mas num script é bom escrever isso explicitamente. As duas
    # são ``store_true`` justamente para o padrão do argparse não atrapalhar
    # — quem decide é :func:`petwatch.app.main`.
    mode.add_argument(
        "-b",
        "--background",
        action="store_true",
        help="solta o terminal e manda a saída para o log (padrão)",
    )

    mode.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="fica preso ao terminal, com log e traceback na tela",
    )

    parser.add_argument(
        "--log",
        type=Path,
        default=LOG_PATH,
        metavar="ARQUIVO",
        help=f"onde gravar a saída em segundo plano (padrão: {LOG_PATH})",
    )

    parser.add_argument(
        "--stop",
        action="store_true",
        help="encerra o pet que está rodando",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="diz se há pet rodando; código 1 se não houver",
    )

    return parser.parse_args(argv)


# ------------------------------------------------------------
# Instância única
# ------------------------------------------------------------


class SingleInstance:
    """Um pet por máquina, garantido por ``flock`` em ``pet.pid``.

    O lock decide quem está rodando; o conteúdo do arquivo diz qual é o
    pid. Os dois papéis são separados de propósito: um arquivo com um pid
    velho não bloqueia ninguém (basta o lock estar livre), e um lock não
    deixa resíduo quando o processo morre de ``SIGKILL`` — quem vira lixo é
    o arquivo, e ele é reescrito no próximo arranque.
    """

    def __init__(self, path: Path = PID_PATH) -> None:
        self.path = path

        #: Descritor com o lock, enquanto este objeto o estiver segurando.
        self._fd: int | None = None

        #: Pid de quem está rodando, quando outro processo o tem.
        self.owner: int | None = None

    def claim(self, *, create: bool = True) -> bool:
        """Toma o lock. ``False`` significa que outro pet já o tem."""

        flags = os.O_RDWR | (os.O_CREAT if create else 0)

        try:
            fd = os.open(self.path, flags, 0o644)

        except FileNotFoundError:
            self._fd = None

            return False

        if not _try_lock(fd):
            # Só volta sem lock se outro estiver segurando. O pid vem do
            # conteúdo, que o dono escreveu depois do fork.
            self.owner = _read_pid(fd)

            os.close(fd)

            self._fd = None

            return False

        self._fd = fd
        self.owner = None

        return True

    def running(self) -> bool:
        """Diz se outro processo segura o lock, sem ficar com ele."""

        held, pid = lock_is_held(self.path)

        self.owner = pid

        return held

    def record_pid(self) -> int:
        """Grava o pid do processo atual dentro do arquivo travado.

        Só depois do ``fork``, para o número gravado ser o do processo que
        fica rodando — e não o do comando que devolveu o terminal.
        """

        if self._fd is None:
            raise RuntimeError("claim() precisa vir antes de record_pid()")

        pid = os.getpid()

        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, f"{pid}\n".encode("ascii"))

        self.owner = pid

        return pid

    def release(self) -> None:
        """Solta o lock. Idempotente, e seguro sem ``claim()``."""

        if self._fd is None:
            return

        # Fechar o descritor é o que solta o lock; não há unlock a fazer.
        os.close(self._fd)

        self._fd = None


def _read_pid(fd: int) -> int | None:
    """Pid gravado no arquivo, ou ``None`` se não houver um válido."""

    try:
        os.lseek(fd, 0, os.SEEK_SET)

        raw = os.read(fd, 64).decode("ascii", "replace").strip()

    except OSError:
        return None

    try:
        pid = int(raw)

    except ValueError:
        return None

    return pid if pid > 0 else None


def lock_is_held(path: Path) -> tuple[bool, int | None]:
    """Se alguma coisa segura o lock de ``path``, e de quem.

    Devolve ``(False, None)`` quando o arquivo não existe **ou** está
    livre: os dois casos são "ninguém rodando", e são diferentes de "outro
    processo segura o lock" — que é o único caso em que há pid para ler.

    A sondagem toma o lock só para ver se consegue e solta na hora. Sem
    esse cuidado, ``--status`` viraria o dono da instância e bloquearia o
    próprio pet.
    """

    try:
        fd = os.open(path, os.O_RDWR)

    except FileNotFoundError:
        return False, None

    if not _try_lock(fd):
        try:
            return True, _read_pid(fd)

        finally:
            os.close(fd)

    # O lock estava livre, e esta cópia é a nova dona: fechar devolve.
    os.close(fd)

    return False, None


def _try_lock(fd: int) -> bool:
    """Pega o lock de ``fd``. ``False`` **só** quando outro o tem.

    Qualquer outro erro do ``flock`` — ``ENOLCK`` em sistema de arquivos
    sem suporte a lock, ``EINVAL`` de plataforma exótica — é falha do
    ambiente, não presença de outra instância, e é tratado como "deu": o
    app sobe sem a garantia de instância única, com um aviso no log.

    O contrário seria pior. Confundir "não sei" com "já tem" produz o
    sintoma de ``já existe um pet rodando`` com exit 0, para sempre, sem
    pet nenhum na tela — e sem deixar pista de onde veio.
    """

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    except OSError as exc:
        if exc.errno in LOCK_BUSY_ERRNOS:
            return False

        log.warning(
            "[pet] flock indisponível (%s: %s); o pet vai rodar sem "
            "a garantia de instância única",
            errno.errorcode.get(exc.errno, exc.errno),
            exc.strerror or exc,
        )

        return True

    return True


def status_line(instance: SingleInstance, log_path: Path = LOG_PATH) -> str:
    """Frase de ``--status``."""

    if instance.running():
        return f"[pet] rodando (pid {instance.owner}). Log: {log_path}"

    return f"[pet] não está rodando. Log: {log_path}"


def stop_instance(instance: SingleInstance) -> int:
    """Manda ``SIGTERM`` no pet que está rodando e espera ele sair.

    ``SIGTERM`` e não ``SIGKILL`` porque o app tem handler para ele: o
    mesmo caminho de ``quit()`` que o item **Fechar** do menu usa, com a
    gravação das preferências e a parada da thread do monitor.
    """

    if not instance.running():
        print("[pet] não há pet rodando")

        return 1

    pid = instance.owner

    if pid is None:
        print("[pet] o pet está rodando, mas o pid não está no arquivo")

        return 1

    try:
        os.kill(pid, signal.SIGTERM)

    except ProcessLookupError:
        print(f"[pet] o pid {pid} morreu no meio do caminho")

        return 1

    except PermissionError:
        print(f"[pet] sem permissão para mandar sinal ao pid {pid}")

        return 1

    deadline = time.monotonic() + STOP_TIMEOUT

    while time.monotonic() < deadline:
        if not instance.running():
            print(f"[pet] encerrado (pid {pid})")

            return 0

        time.sleep(STOP_POLL)

    print(f"[pet] o pid {pid} continua de pé depois de {STOP_TIMEOUT:g}s")

    return 1


# ------------------------------------------------------------
# Log
# ------------------------------------------------------------


def rotate_log(log_path: Path, max_bytes: int = LOG_MAX_BYTES) -> None:
    """Manda o log antigo para ``<arquivo>.1`` quando o atual está grande."""

    try:
        if log_path.stat().st_size < max_bytes:
            return

        os.replace(log_path, log_path.with_name(log_path.name + ".1"))

    except FileNotFoundError:
        return

    except OSError as exc:
        # Um log que não gira é um incômodo; um crash por causa disso seria
        # um problema bem maior.
        log.warning("[pet] não consegui girar o log %s: %s", log_path, exc)


def redirect_standard_streams(log_path: Path) -> None:
    """``stdin`` some; ``stdout`` e ``stderr`` passam a ser o log.

    O ``stdin`` vai para o ``/dev/null`` porque um processo sem terminal
    controlante não deve ficar esperando leitura de teclado. E como o
    redirecionamento é no descritor, e não no objeto ``sys.stdout``, tudo o
    que escrever fora do Python também cai no mesmo arquivo na ordem em
    que aconteceu: os avisos do Qt e do loader de plugins, o traceback do
    interpretador, os ``log`` deste pacote.
    """

    log_path.parent.mkdir(parents=True, exist_ok=True)

    rotate_log(log_path)

    null_fd = os.open(os.devnull, os.O_RDONLY)

    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    try:
        # 0, 1 e 2 já estão abertos no processo original, então ``log_fd``
        # nunca é um deles: fechar aqui não derruba nenhuma das cópias.
        os.dup2(null_fd, 0)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)

    finally:
        os.close(null_fd)
        os.close(log_fd)


# ------------------------------------------------------------
# O desvio
# ------------------------------------------------------------


def spawn(log_path: Path, work: Callable[[int | None], int]) -> int:
    """Roda ``work`` fora do terminal.

    Devolve o código de saída do **processo original**; ele nunca chega a
    executar ``work``. Quem executa é o processo solto, que herda o lock de
    instância única e o grava com o pid dele.

    ``work`` recebe o descritor para falar com o processo original, e é
    dele a obrigação de chamar :func:`report_startup` — sem isso o
    processo original ficaria esperando até :data:`READY_TIMEOUT` sem saber
    de nada.
    """

    # O que já saiu para a tela não pode aparecer duas vezes: o buffer do
    # Python sobrevive ao fork, e quem herda ele escreveria no log.
    _flush_streams()

    ready_read, ready_write = os.pipe()

    try:
        first_child = os.fork()

    except OSError as exc:
        os.close(ready_read)
        os.close(ready_write)

        raise OSError(f"não consegui desviar o processo: {exc}") from exc

    if first_child > 0:
        # Processo original: espera a janela abrir e devolve o terminal.
        os.close(ready_write)

        return _report_result(ready_read, log_path)

    # Primeiro filho. Este fork existe só para abandonar o grupo de jobs do
    # shell: um fork sozinho deixaria o processo como líder de sessão do
    # terminal, que continuaria mandando Ctrl+C para ele.
    os.close(ready_read)

    os.setsid()

    try:
        second_child = os.fork()

    except OSError as exc:
        os._exit(_fail(ready_write, f"segundo fork falhou: {exc}"))

    if second_child > 0:
        # Processo intermediário: existia só para virar líder de sessão, e
        # some agora para não deixar um processo a mais solto por aí.
        #
        # Quem decide o lado do fork é o valor devolvido, nunca uma
        # comparação de pids: assim que este processo sai, o neto é
        # reparentado e ``getppid()`` deixa de valer como resposta.
        os._exit(0)

    # Processo solto. O lock do ``SingleInstance`` veio por herança e
    # continua valendo: ele pertence à cópia do descritor, não ao processo
    # que a abriu, então o pai pode fechar o dele sem soltar o lock.
    os.chdir("/")
    os.umask(0o022)

    try:
        redirect_standard_streams(log_path)

    except OSError as exc:
        os._exit(_fail(ready_write, f"não consegui abrir o log {log_path}: {exc}"))

    return work(ready_write)


def _fail(ready_write: int, message: str) -> int:
    report_startup(ready_write, message)

    return 1


def report_startup(ready: int | None, error: str | None = None) -> None:
    """Fala com o processo original, que ficou esperando a confirmação.

    Fecha o descritor: é o EOF que libera a espera do outro lado, e um
    descritor que ficasse aberto transformaria a confirmação em espera de
    :data:`READY_TIMEOUT`.

    Pode ser chamada duas vezes (o caminho feliz e o do erro): da segunda o
    descritor já foi fechado e a escrita falha, o que é ignorado.
    """

    if ready is None:
        return

    if error is None:
        message = f"{READY_OK} {os.getpid()}"

    else:
        # A mensagem de uma exceção pode ter quebras de linha, que
        # separariam a linha do protocolo em duas.
        detail = " ".join(str(error).split()) or "erro desconhecido"

        message = f"{READY_ERROR} {detail}"

    try:
        os.write(ready, (message + "\n").encode("utf-8", "replace"))

        os.close(ready)

    except OSError:
        pass


def _report_result(ready_read: int, log_path: Path) -> int:
    """Imprime o que o processo solto disse. Só o original chega aqui."""

    message = _read_until_eof(ready_read, READY_TIMEOUT)

    if message is None:
        print(
            "[pet] em segundo plano, sem confirmação da janela. "
            f"Log: {log_path}"
        )

        return 0

    keyword, _, detail = message.partition(" ")

    if keyword == READY_OK:
        print(f"[pet] rodando em segundo plano (pid {detail}). Log: {log_path}")

        print(f"[pet] para encerrar: {_program_name()} --stop")

        return 0

    print(f"[pet] não consegui abrir a janela: {detail}", file=sys.stderr)

    print(f"[pet] log: {log_path}", file=sys.stderr)

    return 1


def _read_until_eof(fd: int, timeout: float) -> str | None:
    """Lê uma linha até o EOF. ``None`` se o prazo estourar primeiro."""

    deadline = time.monotonic() + timeout

    chunks: list[bytes] = []

    with os.fdopen(fd, "rb") as stream:
        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                return None

            readable, _, _ = select.select([stream], [], [], remaining)

            if not readable:
                return None

            # ``read1`` devolve assim que há algo, sem esperar encher o
            # buffer — a mensagem é uma linha só.
            chunk = stream.read1(4096)

            if not chunk:
                break

            chunks.append(chunk)

    return "".join(
        chunk.decode("utf-8", "replace") for chunk in chunks
    ).strip() or None


def _flush_streams() -> None:
    """Esvazia os buffers do Python antes do fork."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()

        except Exception as exc:
            log.debug("[pet] não consegui esvaziar %s: %s", stream, exc)


def _program_name() -> str:
    """Nome pelo qual o usuário chamou o programa."""

    return Path(sys.argv[0]).name or "pet.py"
