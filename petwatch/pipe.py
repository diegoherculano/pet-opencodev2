"""O canal entre o pet rodando e quem o mandou parar.

No POSIX isso é um ``SIGTERM``: o processo que roda recebe o sinal, o
handler chama ``quit()`` e o app encerra pelo mesmo caminho limpo do item
**Fechar** do menu. No Windows não existe equivalente. ``os.kill(pid,
SIGTERM)`` é ``TerminateProcess`` — o processo morre na hora, sem handler,
sem ``shutdown()``, sem gravar as preferências — e o
``CTRL_BREAK_EVENT``, que seria o sinal de verdade, exige que o filho
divide o console do pai, o que abre uma janela preta ao lado do pet.

O que substitui os dois é um **named pipe**, e ele resolve também a
instância única: pipe é nome, nome é exclusivo. Quem consegue abrir o
nome é o pet; quem não consegue é o segundo ``petwatch.exe``, e a resposta
que ele recebe é o pid de quem está rodando.

O protocolo é uma linha de requisição e uma de resposta, e o servidor vive
numa thread daemon separada do event loop do Qt — a mesma razão pela qual
o monitor do opencode mora em ``QThread``.

Nomes
----

``\\\\.\\pipe\\petwatch-<usuário>-<hash do STATE_DIR>``

O sufixo existe por dois motivos: pipes são **globais na máquina**, e não
por usuário, então dois usuários no mesmo Windows colidiriam; e duas
cópias com diretórios de estado diferentes (perfil de teste, portable)
não podem se enganar sobre quem está rodando.

Chave
-----

O handshake do ``multiprocessing.connection`` é HMAC, e a chave padrão é o
``authkey`` do processo — que é aleatório por processo. Cliente e servidor
precisam da **mesma** chave, então ela é gerada uma vez e guardada em
``pet.key``, com permissão só para o dono. Sem ela o handshake falha e o
único sintoma seria um ``--stop`` que nunca acha o pet.

Testabilidade
-------------

As funções de cliente e servidor recebem ``address`` e ``family``, sem
decidir nada da plataforma. No Windows quem monta é um pipe nomeado; no
Linux os testes usam ``AF_UNIX`` num diretório temporário e exercitam o
protocolo inteiro de verdade — inclusive o caminho do cliente, que é o que
``--stop`` usa.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import secrets
import threading
from collections.abc import Callable
from multiprocessing.connection import Client, Listener
from pathlib import Path

from .config import APP_NAME, IS_WINDOWS, STATE_DIR

log = logging.getLogger(__name__)

#: Nome do arquivo com a chave do handshake.
AUTHKEY_FILENAME = "pet.key"

#: Tamanho da chave: 256 bits, o mesmo do ``authkey`` padrão do Python.
AUTHKEY_BYTES = 32

#: Pedidos que o pet entende.
PING = "ping"

STOP = "stop"

#: Quanto tempo o cliente espera por uma resposta antes de desistir. O
#: pipe responde em milissegundos; o prazo existe para o caso do pet estar
#: com a interface ocupada, e é ele que impede um ``--status`` de ficar
#: pendurado no terminal.
CLIENT_TIMEOUT = 2.0

#: Prefixo do nome do pipe no Windows.
PIPE_PREFIX = "\\\\.\\pipe"

#: Família de socket usada no destino: o pipe nomeado no Windows, o
#: socket de arquivo nos testes (e em qualquer POSIX que queira exercitar
#: o mesmo caminho).
AF_PIPE = "AF_PIPE"

AF_UNIX = "AF_UNIX"


def default_endpoint(state_dir: Path = STATE_DIR) -> tuple[str, str]:
    """Endereço e família do canal na plataforma atual."""

    if IS_WINDOWS:
        return pipe_name(state_dir), AF_PIPE

    return str(state_dir / "pet.sock"), AF_UNIX


def pipe_name(state_dir: Path = STATE_DIR) -> str:
    """Nome do pipe, único por usuário e por diretório de estado."""

    try:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"

    except Exception:  # ambiente sem variável nenhuma é melhor que exceção
        user = "user"

    digest = hashlib.sha256(str(state_dir).encode("utf-8")).hexdigest()[:8]

    return f"{PIPE_PREFIX}\\{APP_NAME}-{user}-{digest}"


def authkey_path(state_dir: Path = STATE_DIR) -> Path:
    return state_dir / AUTHKEY_FILENAME


def load_authkey(state_dir: Path = STATE_DIR) -> bytes | None:
    """Chave existente, ou ``None``.

    Devolve ``None`` em vez de gerar: quem só quer perguntar (``--status``,
    ``--stop``) não cria arquivo nenhum, e o servidor é quem garante que a
    chave exista.
    """

    try:
        raw = authkey_path(state_dir).read_bytes()

    except OSError:
        return None

    return raw or None


def ensure_authkey(state_dir: Path = STATE_DIR) -> bytes | None:
    """Chave do handshake, gerando na primeira chamada.

    A criação é exclusiva (``O_EXCL``) porque dois processos podem pedir a
    chave no mesmo instante — o que perde a corrida relê a do outro em vez
    de sobrescrever, e as duas pontas continuam com a mesma.
    """

    path = authkey_path(state_dir)

    try:
        raw = path.read_bytes()

        if raw:
            return raw

    except FileNotFoundError:
        pass

    except OSError as exc:
        log.debug("[pet] não consegui ler %s: %s", path, exc)

        return None

    candidate = secrets.token_bytes(AUTHKEY_BYTES)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

        try:
            os.write(fd, candidate)

        finally:
            os.close(fd)

    except FileExistsError:
        return load_authkey(state_dir)

    except OSError as exc:
        log.debug("[pet] não consegui criar %s: %s", path, exc)

        return None

    return candidate


# ------------------------------------------------------------
# Cliente
# ------------------------------------------------------------

def ask(
    request: str,
    address: str | None = None,
    family: str | None = None,
    *,
    state_dir: Path = STATE_DIR,
    authkey: bytes | None = None,
    timeout: float = CLIENT_TIMEOUT,
):
    """Manda um pedido e devolve a resposta, ou ``None``.

    ``None`` é a resposta para quase tudo: não há pet rodando, o pipe
    pertence a outro usuário, o handshake falhou, o pet está travado. O
    chamador não precisa separar os casos — para ``--status`` e ``--stop``
    todos significam a mesma coisa, que é "não deu para falar com ele".
    """

    if address is None or family is None:
        default_address, default_family = default_endpoint(state_dir)

        address = address or default_address

        family = family or default_family

    key = authkey if authkey is not None else load_authkey(state_dir)

    if not key:
        log.debug("[pet] sem chave de pipe em %s", state_dir)

        return None

    connection = None

    try:
        # ``Client`` não aceita prazo de espera: um pipe que não existe
        # falha na hora (``FileNotFoundError``) e um que existe atende. O
        # prazo é do ``poll``, que é onde a espera de verdade acontece — o
        # pet ocupado para de responder, e é isso que não pode prender o
        # terminal de quem chamou ``--status``.
        connection = Client(address, family=family, authkey=key)

        connection.send(request)

        if not connection.poll(timeout):
            log.debug("[pet] pipe %s não respondeu", address)

            return None

        return connection.recv()

    except Exception as exc:
        # FileNotFoundError = ninguém está escutando; AuthenticationError
        # = o pipe é de outra instalação; ConnectionRefusedError = o pet
        # está no meio do encerramento. Todos são "não".
        log.debug("[pet] pipe %s indisponível: %s", address, exc)

        return None

    finally:
        if connection is not None:
            try:
                connection.close()

            except Exception as exc:
                log.debug("[pet] erro fechando o cliente: %s", exc)


# ------------------------------------------------------------
# Servidor
# ------------------------------------------------------------

class PipeServer:
    """Escuta o pipe e entrega cada pedido ao handler.

    A thread é daemon de propósito: ela fica bloqueada em
    ``accept()`` enquanto o pet roda, e o encerramento do app não depende
    dela — o processo morre com o listener junto.
    """

    def __init__(
        self,
        handler: Callable[[str], tuple[bool, object]],
        address: str | None = None,
        family: str | None = None,
        *,
        state_dir: Path = STATE_DIR,
        authkey: bytes | None = None,
    ) -> None:
        self.handler = handler

        if address is None or family is None:
            default_address, default_family = default_endpoint(state_dir)

            address = address if address is not None else default_address

            family = family if family is not None else default_family

        self.address = address
        self.family = family

        self.authkey = authkey

        self.state_dir = state_dir

        #: PID registrado pelo dono do pipe, o que o ``ping`` devolve.
        self.pid: int | None = None

        self._listener: Listener | None = None

        self._thread: threading.Thread | None = None

        self._stopped = threading.Event()

    def start(self) -> bool:
        """Assume o pipe. ``False`` quando outro pet já o tem."""

        key = self.authkey or ensure_authkey(self.state_dir)

        if not key:
            log.warning("[pet] sem chave de pipe; instância única desligada")

            return False

        self.authkey = key

        try:
            self._listener = Listener(self.address, family=self.family, authkey=key)

        except Exception as exc:
            # ERROR_PIPE_BUSY é o "já existe" do Windows; FileExistsError
            # é o mesmo em AF_UNIX.
            log.debug("[pet] pipe %s ocupado: %s", self.address, exc)

            return False

        self._thread = threading.Thread(
            target=self._serve,
            name="petwatch-pipe",
            daemon=True,
        )

        self._thread.start()

        log.info("[pet] escutando em %s", self.address)

        return True

    def record_pid(self, pid: int | None = None) -> None:
        """Anota o pid que o ``ping`` devolve."""

        self.pid = os.getpid() if pid is None else pid

    def stop(self) -> None:
        """Fecha o listener; a thread morre junto com o processo."""

        self._stopped.set()

        listener = self._listener

        self._listener = None

        if listener is None:
            return

        try:
            listener.close()

        except Exception as exc:
            log.debug("[pet] erro fechando o pipe: %s", exc)

    def _serve(self) -> None:
        while not self._stopped.is_set():
            listener = self._listener

            if listener is None:
                return

            request = None
            connection = None

            try:
                connection = listener.accept()

                request = connection.recv()

                ok, payload = self.handler(request)

                connection.send((ok, payload))

            except Exception as exc:
                if not self._stopped.is_set():
                    log.debug("[pet] falha atendendo %r: %s", request, exc)

                if connection is not None:
                    with contextlib.suppress(Exception):
                        connection.send((False, "erro"))

            finally:
                if connection is not None:
                    try:
                        connection.close()

                    except Exception as exc:
                        log.debug("[pet] erro fechando a conexão: %s", exc)
