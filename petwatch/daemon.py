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

Quem não tem terminal — o ``petwatch.exe`` compilado sem console — recebe
as mesmas frases numa caixa de diálogo em vez de vê-las passar depressa.
Ver :mod:`petwatch.console`.

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
isso a presença do pet é decidido pelo lock, nunca pelo conteúdo.

No Windows
----------

Não há ``fork``, nem ``setsid``, nem ``fcntl``, e ``select.select`` no
Windows só aceita socket — a espera pelo pipe do processo original morreria
ali. O desvio equivalente é :func:`spawn_detached`: um ``Popen`` com
``DETACHED_PROCESS`` e ``CREATE_NO_WINDOW``, que faz a mesma coisa (o
processo deixa de pertencer ao terminal) com o que a plataforma oferece.

Duas coisas mudam em volta disso, e as duas são consequência de como o
Windows passa handle entre processos:

- **A confirmação da janela vai por arquivo**, não por pipe. Um handle
  herdado pelo filho não aparece como descritor nele (o CRT reconstrói a
  tabela a partir do ``STARTUPINFO``, e só dos três descritores padrão), e
  um socket exigiria uma porta que o próprio pet precisaria escolher. Um
  arquivo com um nome só não depende de nenhum desses detalhes.
- **Quem assume a instância única é o filho**, não o pai. O ``flock``
  atravessa o ``fork`` por herança; no Windows o caminho é o named pipe
  (:class:`petwatch.instance.PipeInstance`), e o pai apenas pergunta se
  há pet antes de lançar o filho.

As duas funções de fluxo ficam em :mod:`petwatch.app`, porque a escolha
depende de quando o processo assume a instância — que é uma decisão de
montagem do app, não deste módulo.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import logging
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from .config import BASE_DIR, LOG_PATH, PID_PATH
from .console import say

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

#: Opção interna do processo solto: "você já é o filho, não desvie de
#: novo". Fica escondida do ``--help`` porque ninguém deve digitar isso —
#: quem digitar sobe um pet colado no terminal e sem instância única, que é
#: exatamente o que o modo solto evita.
CHILD_FLAG = "--child"

#: Variável de ambiente que diz ao filho onde confirmar a abertura. Um
#: caminho, e não um descritor: ver o docstring do módulo.
READY_ENV = "PETWATCH_READY_FILE"

#: De quanto em quanto tempo o processo original olha o arquivo de
#: confirmação. Só o pior caso paga o prazo inteiro; quando a mensagem já
#: está no arquivo, a leitura é imediata.
READY_POLL = 0.05

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

    # Só o processo solto usa isto, e ele não tem terminal para perguntar:
    # esconder da ajuda é o que impede alguém de digitar.
    parser.add_argument(CHILD_FLAG, action="store_true", help=argparse.SUPPRESS)

    return parser.parse_args(argv)


def ready_file() -> Path | None:
    """Arquivo onde o filho deve confirmar a abertura, se for o caso."""

    raw = os.environ.get(READY_ENV, "").strip()

    return Path(raw) if raw else None


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
            self.path.parent.mkdir(parents=True, exist_ok=True)

            fd = os.open(self.path, flags, 0o644)

        except FileNotFoundError:
            self._fd = None

            return False

        except OSError as exc:
            log.warning("[pet] não consegui abrir %s: %s", self.path, exc)

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

    def request_stop(self, pid: int | None = None) -> bool:
        """Manda o ``SIGTERM`` no dono do lock.

        O pedido vai pelo mesmo método que o Windows usa pelo pipe (ver
        :class:`petwatch.instance.PipeInstance`), para que o ``--stop``
        seja o mesmo código nas duas plataformas e cada estratégia resolva
        só a parte dela: como alcançar o processo.

        ``False`` significa "não consegui falar com ele", e o chamador
        trata cada motivo com a frase que cabe.
        """

        target = pid if pid is not None else self.owner

        if target is None:
            return False

        try:
            os.kill(target, signal.SIGTERM)

        except ProcessLookupError:
            log.debug("[pet] o pid %s morreu no meio do caminho", target)

            return False

        except PermissionError:
            log.debug("[pet] sem permissão para o pid %s", target)

            return False

        except OSError as exc:
            log.debug("[pet] não consegui sinalizar o pid %s: %s", target, exc)

            return False

        return True


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


def status_line(instance, log_path: Path = LOG_PATH) -> str:
    """Frase de ``--status``."""

    if instance.running():
        return f"[pet] rodando (pid {instance.owner}). Log: {log_path}"

    return f"[pet] não está rodando. Log: {log_path}"


def stop_instance(instance, log_path: Path = LOG_PATH) -> int:
    """Pede para o pet que está rodando encerrar, e espera ele sair.

    O encerramento em si é específico da estratégia
    (:meth:`~petwatch.daemon.SingleInstance.request_stop` no POSIX, o pipe
    no Windows), mas a espera é a mesma: o pedido só conta como cumprido
    quando a instância deixa de estar rodando.

    No ``petwatch.exe`` sem console não há terminal para receber a frase,
    então ``popup=True``: quem abriu o programa por duplo clique precisa
    **ver** que o pet parou.
    """

    if not instance.running():
        say("[pet] não há pet rodando", popup=True)

        return 1

    pid = instance.owner

    if pid is None:
        say(
            "[pet] o pet está rodando, mas o pid não está no arquivo",
            popup=True,
        )

        return 1

    if not instance.request_stop(pid):
        say(f"[pet] não consegui pedir para o pid {pid} encerrar", popup=True)

        return 1

    deadline = time.monotonic() + STOP_TIMEOUT

    while time.monotonic() < deadline:
        if not instance.running():
            say(f"[pet] encerrado (pid {pid})", popup=True)

            return 0

        time.sleep(STOP_POLL)

    say(
        f"[pet] o pid {pid} continua de pé depois de {STOP_TIMEOUT:g}s. "
        f"Log: {log_path}",
        popup=True,
    )

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

        return _report_result(_read_until_eof(ready_read, READY_TIMEOUT), log_path)

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


def report_startup(
    ready: int | Path | None,
    error: str | None = None,
) -> None:
    """Fala com o processo original, que ficou esperando a confirmação.

    O canal é um descritor no POSIX e um caminho de arquivo no Windows
    (ver o docstring do módulo): os dois carregam a mesma linha.

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

    if isinstance(ready, (str, Path)):
        _report_startup_file(Path(ready), message)

        return

    try:
        os.write(ready, (message + "\n").encode("utf-8", "replace"))

        os.close(ready)

    except OSError:
        pass


def _report_startup_file(path: Path, message: str) -> None:
    """Escreve a confirmação no arquivo que o pai está olhando.

    Escreve ao lado e troca, pelo mesmo motivo do log: um arquivo pela
    metade seria lido pelo pai como "falhou" e producing um
    ``não consegui abrir a janela: petwatch`` sem sentido nenhum.
    """

    temporary = path.with_name(path.name + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        temporary.write_text(message + "\n", encoding="utf-8")

        os.replace(temporary, path)

    except OSError as exc:
        log.debug("[pet] não consegui confirmar a abertura em %s: %s", path, exc)

        try:
            temporary.unlink(missing_ok=True)

        except OSError:
            pass


def _report_result(message: str | None, log_path: Path) -> int:
    """Imprime o que o processo solto disse. Só o original chega aqui.

    A falha de abertura é o único caso que abre ``popup``: ela precisa
    aparecer *agora*, com o processo ainda na tela, senão o sintoma de "não
    aconteceu nada" seria um duplo clique sem nenhuma pista. Já a
    confirmação de sucesso é só registro — um popup a cada inicialização
    seria ruído.
    """

    if message is None:
        say(
            "[pet] em segundo plano, sem confirmação da janela. "
            f"Log: {log_path}"
        )

        return 0

    keyword, _, detail = message.partition(" ")

    if keyword == READY_OK:
        say(f"[pet] rodando em segundo plano (pid {detail}). Log: {log_path}")

        say(f"[pet] para encerrar: {_program_name()} --stop")

        return 0

    say(f"[pet] não consegui abrir a janela: {detail}", popup=True, error=True)

    say(f"[pet] log: {log_path}", popup=True, error=True)

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

    # Congelado, quem chamou o programa é o executável — e é ele que o
    # usuário digita no ``--stop``. Testar ``sys.argv[0]`` aqui devolveria
    # o caminho temporário que o PyInstaller desempacotou.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name or "petwatch.exe"

    return Path(sys.argv[0]).name or "pet.py"


# ------------------------------------------------------------
# O desvio no Windows
# ------------------------------------------------------------

#: Cria o processo sem console próprio. ``DETACHED_PROCESS`` é o que tira
#: o pet do grupo do terminal — é o equivalente ao ``setsid``, e sem ele o
#: pet morreria junto com a janela do console de quem o abriu.
DETACHED_PROCESS = 0x00000008

#: Processo novo em grupo próprio. Não é usado para mandar ``Ctrl+Break``
#: (o pet não tem console, e esse caminho foi descartado por isso), e sim
#: para o ``--stop`` não precisar de um identificador de job.
CREATE_NEW_PROCESS_GROUP = 0x00000200

#: Referência aos processos soltos já lançados. Ver :func:`spawn_detached`.
_detached: list[subprocess.Popen] = []


def spawn_detached(log_path: Path) -> int:
    """Sobe o pet fora do terminal, no Windows.

    É o equivalente de :func:`spawn` com o que a plataforma tem: um
    ``Popen`` com ``DETACHED_PROCESS``. O filho é **outro processo do mesmo
    executável** (``--child``), e não uma cópia do atual — no Windows não
    existe outra forma de ir para segundo plano, e é isso que faz o
    ``petwatch.exe`` conseguir rodar sem console nenhum.

    O pai espera a confirmação da janela pelo arquivo apontado em
    :data:`READY_ENV` e só então devolve o terminal; o ``stdin`` do filho
    aponta para o ``NUL`` do Windows (o :data:`os.devnull` da plataforma),
    porque um processo sem console de controle não deve ficar esperando
    leitura de teclado.
    """

    log_path.parent.mkdir(parents=True, exist_ok=True)

    rotate_log(log_path)

    confirmation = log_path.with_name(f"pet.ready.{os.getpid()}")

    confirmation.unlink(missing_ok=True)

    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    try:
        environment = dict(os.environ)

        environment[READY_ENV] = str(confirmation)

        process = subprocess.Popen(
            detached_command(),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )

    except OSError as exc:
        os.close(log_fd)

        raise OSError(f"não consegui desviar o processo: {exc}") from exc

    os.close(log_fd)

    # O pai sai logo depois, e o ``Popen`` não é esperado — quem manda na
    # vida do pet é o pet. A referência fica em uma lista do módulo pelo
    # mesmo motivo pelo qual o POSIX nunca chama ``wait()``: um objeto
    # ``Popen`` recolhido com o filho vivo emite ``ResourceWarning``, e o
    # processo solto é justamente a coisa que o app vai passar a vida
    # fazendo.
    _detached.append(process)

    return _report_result(
        _await_confirmation(confirmation, READY_TIMEOUT),
        log_path,
    )


def detached_command() -> list[str]:
    """Comando que sobe o processo solto.

    Numa instalação congelada é o próprio ``petwatch.exe``: não há script
    para executar, e ``sys.executable`` é o ``.exe``. Numa instalação
    normal é o ``pet.py`` ao lado do pacote, e num ``pip install``, onde
    esse arquivo não existe, o que roda é ``-m petwatch`` — por isso o
    terceiro caminho, e não um caminho único que pareceria mais limpo.
    """

    if getattr(sys, "frozen", False):
        return [sys.executable, CHILD_FLAG]

    entry = BASE_DIR / "pet.py"

    if entry.is_file():
        return [sys.executable, str(entry), CHILD_FLAG]

    return [sys.executable, "-m", "petwatch", CHILD_FLAG]


def _await_confirmation(path: Path, timeout: float) -> str | None:
    """Espera o arquivo de confirmação e devolve a linha.

    ``None`` se o prazo estourar, que é o mesmo resultado do pipe do POSIX:
    o pet pode ter subido mesmo assim, e a mensagem diz exatamente isso em
    vez de fingir que falhou.
    """

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()

        except FileNotFoundError:
            content = ""

        except OSError as exc:
            log.debug("[pet] não consegui ler %s: %s", path, exc)

            return None

        if content:
            path.unlink(missing_ok=True)

            return content

        time.sleep(READY_POLL)

    return None
