"""Descoberta do servidor local do opencode e credenciais.

A senha vem do próprio CLI. **A porta não vem de lugar nenhum**: ela é
descoberta por quem está escutando, e cada candidata é validada com um GET
em ``/api/event`` — o mesmo endpoint que o monitor consome depois.

Por que a lista de portas em vez do nome do processo. A primeira versão
deste módulo rodava ``ss -ltnp`` e casava a linha que continha
``"opencode"``. Isso funciona no Linux, mas é frágil mesmo lá: o
serviço pode ser um processo com outro nome (um wrapper, um serviço
empacotado), estar em outro namespace de rede (WSL, container), ou a
permissão de ver o processo pode faltar. No Windows não existe
equivalente nenhum — ``netstat -ano`` devolve PID, não nome — e casar por
nome exigiria um ``tasklist`` por PID para chegar no mesmo lugar.

A pergunta boa é outra, e vale nas duas plataformas: **qual das portas em
escuta é o opencode?** A resposta vem do próprio servidor, que exige a
senha e responde ``text/event-stream`` em ``/api/event``. Quem não for o
opencode devolve 404, 401 ou algum HTML — e o que estiver em outra máquina
nem é candidato, porque a sondagem é sempre em ``127.0.0.1``.

Três leituras, por plataforma, todas devolvendo a mesma coisa:

=====================  ==========================================
plataforma             fonte
=====================  ==========================================
Linux                  ``/proc/net/tcp`` e ``tcp6`` (sem subprocesso)
outro POSIX            ``ss -ltn``
Windows                ``netstat -ano -p tcp``
=====================  ==========================================

As três são funções puras que recebem texto e devolvem portas, então a
suíte testa o formato de cada uma sem depender do sistema em que roda.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from .config import (
    EVENT_PATH,
    HOST,
    IS_WINDOWS,
    MAX_LISTENERS,
    MAX_SWEEP_PORTS,
    PASSWORD_COMMAND,
    PASSWORD_COMMANDS,
    PASSWORD_ENV,
    PASSWORD_TIMEOUT,
    PORT_ENV,
    PORT_SCAN_TIMEOUT,
    PORTS_TTL_SECONDS,
    PROBE_CONNECT_TIMEOUT,
    PROBE_REQUEST_TIMEOUT,
    STREAM_TIMEOUT,
    SWEEP_REQUEST_TIMEOUT,
    USERNAME,
)
from .http import Connection, HttpError

log = logging.getLogger(__name__)

#: Extrai a porta decimal de ``127.0.0.1:4096`` e de ``[::]:4096``.
PORT_PATTERN = re.compile(r":(\d+)\s*$")

#: O ``/proc`` grava tudo em hexadecimal, inclusive a porta — ``1F90`` são
#: 8080. Tratar como decimal seria silenciosamente errado, e o erro não
#: apareceria como "porta não encontrada": a porta 16 do exemplo viraria
#: uma candidata real, sempre.
HEX_PORT_PATTERN = re.compile(r"^[0-9a-fA-F]{1,4}$")

#: Estado ``0A`` do ``/proc/net/tcp``: LISTEN.
TCP_LISTEN = "0A"

#: O que o opencode tem que responder para ser reconhecido: não basta um
#: 200, porque qualquer servidor local que sirva ``/api/event`` — ou um
#: servidor de dev que responda 200 em tudo — seria adotado como opencode.
SSE_CONTENT_TYPE = "text/event-stream"

#: Cache da última listagem, com o instante em que foi lida.
_listeners: tuple[float, list[int]] | None = None

#: Última porta que respondeu. Fica em memória porque o ciclo do monitor
#: reconecta a cada ``RECONNECT_DELAY`` e a porta quase nunca muda: sondar
#: a conhecida primeiro transforma a reconexão num único GET.
_last_good_port: int | None = None

#: Senha do WSL já resolvida, com o instante da leitura. O ``wsl.exe``
#: custa ~1s por chamada, e o monitor pede a senha a cada reconexão —
#: sem cache seriam 4 spawn por ciclo, para sempre.
_wsl_password: tuple[float, str | None] | None = None

#: Quanto tempo a senha do WSL vale sem reler. Ela não muda enquanto o
#: serviço roda; o TTL existe só para um WSL que subiu depois do pet.
WSL_PASSWORD_TTL_SECONDS = 60.0


def _silent_kwargs() -> dict:
    """Sem janela de console nos filhos, no Windows.

    Sem ``CREATE_NO_WINDOW`` cada ``netstat``/``wsl`` aberto pelo pet pisca
    um terminal na tela — e o monitor reconecta a cada 2s, então o sintoma
    é um terminal que não para de aparecer.
    """

    if os.name != "nt":
        return {}

    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    return {"creationflags": flag} if flag else {}


# ------------------------------------------------------------
# Leitura das portas em escuta
# ------------------------------------------------------------

def parse_proc_net_tcp(text: str) -> list[tuple[int, bool]]:
    """Portas em escuta de um ``/proc/net/tcp``, com a marca de loopback.

    O endereço local vem em hexadecimal, **little-endian por palavra de 32
    bits**: ``0100007F`` é ``127.0.0.1`` e ``00000000`` é ``0.0.0.0``. Só
    interessa o estado ``0A`` (LISTEN); as demais linhas são conexões
    estabelecidas, TIME_WAIT e afins, cujas portas não aceitam nada.
    """

    found: list[tuple[int, bool]] = []

    for line in text.splitlines():
        fields = line.split()

        # ``sl`` + ``local_address`` + ``rem_address`` + ``st``.
        if len(fields) < 4 or fields[3] != TCP_LISTEN:
            continue

        address, _, raw_port = fields[1].partition(":")

        if not HEX_PORT_PATTERN.match(raw_port):
            continue

        try:
            port = int(raw_port, 16)

        except ValueError:
            continue

        if 1 <= port <= 65535:
            found.append((port, _is_loopback_hex(address)))

    return found


def _is_loopback_hex(address: str) -> bool:
    """``True`` para ``127.0.0.1``, ``0.0.0.0``, ``::1`` e ``::``."""

    try:
        packed = bytes.fromhex(address)

    except ValueError:
        return False

    if not packed:
        return False

    # Zero é "todos os endereços": escutando ali, o loopback também é
    # alcançado, então vale como candidato.
    if not any(packed):
        return True

    # IPv4 gravado little-endian: 127.0.0.1.
    if len(packed) == 4:
        return packed[::-1] == b"\x7f\x00\x00\x01"

    # IPv6: ::1 e ::ffff:127.0.0.1 têm os 12 primeiros bytes zerados, e a
    # diferença está na palavra final — que o ``/proc`` grava em
    # little-endian por palavra de 32 bits, como o IPv4. Por isso as duas
    # ordenações entram: ``00000001`` é ::1 no ``/proc``, e ``01000000`` é
    # ::1 escrito em byte big-endian.
    if packed[:-4] != b"\x00" * (len(packed) - 4):
        return False

    tail = packed[-4:]

    return tail in (
        b"\x00\x00\x00\x00",
        b"\x00\x00\x00\x01",
        b"\x01\x00\x00\x00",
    )


def parse_ss_listen(text: str) -> list[tuple[int, bool]]:
    """Portas em escuta de um ``ss -ltn``.

    A coluna ``State`` vem antes do endereço e é ela que separa escuta de
    conexão: uma linha ``ESTAB`` também tem endereço e porta, e tratá-la
    como candidata faria o pet sondar portas que não aceitam nada.
    """

    found: list[tuple[int, bool]] = []

    for line in text.splitlines():
        fields = line.split()

        if len(fields) < 4 or fields[0].upper() != "LISTEN":
            continue

        # O endereço local é a quarta coluna: ``State Recv-Q Send-Q
        # Local Address:Port``.
        found.extend(_endpoint(fields[3]))

    return found


def _endpoint(local: str) -> list[tuple[int, bool]]:
    """Porta e loopback de um ``endereço:porta`` decimal.

    Serve ao ``ss`` e ao ``netstat``, que escrevem do mesmo jeito — e que
    se diferenciam só pela coluna que vem antes.
    """

    match = PORT_PATTERN.search(local)

    if not match:
        return []

    try:
        port = int(match.group(1))

    except ValueError:
        return []

    if not 1 <= port <= 65535:
        return []

    return [(port, _is_loopback_text(local.rpartition(":")[0]))]


def parse_netstat_ano(text: str) -> list[tuple[int, bool]]:
    """Portas em escuta de um ``netstat -ano -p tcp``.

    Só as linhas em ``LISTENING``; o resto é conexão aberta. A coluna do
    PID — que é o que o ``netstat`` tem de útil — é ignorada de propósito,
    porque o nome do processo é justamente o que não se quer casar: ele
    mudaria entre instalações (``opencode.exe``, ``opencode-ai.exe``,
    ``bun.exe``), e a identidade real do opencode é a senha e o tipo de
    conteúdo da resposta.
    """

    found: list[tuple[int, bool]] = []

    for line in text.splitlines():
        fields = line.split()

        if len(fields) < 4 or fields[0].upper() != "TCP":
            continue

        if fields[3].upper() != "LISTENING":
            continue

        found.extend(_endpoint(fields[1]))

    return found


def _is_loopback_text(address: str) -> bool:
    """``True`` para ``127.0.0.1``, ``0.0.0.0``, ``::1``, ``[::]``."""

    return address in {
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "[::]",
        "::",
        "*",
    }


def read_proc_listeners() -> list[tuple[int, bool]]:
    """Portas em escuta lendo o ``/proc``. Sem subprocesso nenhum."""

    names = [Path("/proc/net/tcp")]

    if Path("/proc/net/tcp6").exists():
        names.append(Path("/proc/net/tcp6"))

    found: list[tuple[int, bool]] = []

    for name in names:
        try:
            found.extend(parse_proc_net_tcp(name.read_text(encoding="ascii")))

        except OSError as exc:
            log.debug("[pet] %s indisponível: %s", name, exc)

    return found


def run_scan(command: list[str]) -> str:
    """Roda a listagem de portas e devolve a saída bruta."""

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PORT_SCAN_TIMEOUT,
            check=False,
            **_silent_kwargs(),
        )

    except Exception as exc:
        log.debug("[pet] %s indisponível: %s", command[0], exc)

        return ""

    return result.stdout


def ss_listeners() -> list[tuple[int, bool]]:
    """Portas em escuta via ``ss``."""

    if not shutil.which("ss"):
        return []

    return parse_ss_listen(run_scan(["ss", "-ltn"]))


def netstat_listeners() -> list[tuple[int, bool]]:
    """Portas em escuta via ``netstat``."""

    if not shutil.which("netstat"):
        return []

    return parse_netstat_ano(run_scan(["netstat", "-ano", "-p", "tcp"]))


def raw_listeners() -> list[tuple[int, bool]]:
    """Escuta da máquina, na fonte que a plataforma oferece.

    O Linux vai pelo ``/proc`` porque é mais rápido que ``ss`` e não custa
    um processo: o ciclo do monitor reconecta a cada dois segundos, e um
    ``fork`` a cada ciclo seria desperdício. O ``/proc`` também dispensa a
    permissão que o ``ss -p`` pede.
    """

    if IS_WINDOWS:
        return netstat_listeners()

    found = read_proc_listeners()

    return found if found else ss_listeners()


def listening_ports(platform: str | None = None) -> list[int]:
    """Portas que estão aceitando conexão agora, loopback primeiro.

    O loopback vem primeiro porque é onde a sondagem acontece: um servidor
    preso numa interface de VPN responde na interface, não em
    ``127.0.0.1``, e sozinho ele não teria para onde ser sondado.
    """

    name = platform if platform is not None else sys.platform

    if name.startswith("win"):
        found = netstat_listeners()

    elif name.startswith("linux"):
        found = read_proc_listeners() or ss_listeners()

    else:
        found = ss_listeners()

    found = found[:MAX_LISTENERS]

    # Uma mesma porta pode estar em escuta em duas linhas — em
    # ``127.0.0.1`` e em ``192.168.0.14``, por exemplo, que é o que o
    # Windows faz com serviços que escutam em todo endereço. Os dois
    # conjuntos são somados antes, não depois: repetir a porta custaria
    # uma sondagem inteira a cada ciclo.
    loopback = {port for port, is_loop in found if is_loop}

    return sorted(loopback) + sorted(
        {port for port, is_loop in found if not is_loop} - loopback
    )


# ------------------------------------------------------------
# A lista, com cache
# ------------------------------------------------------------

def reset_cache() -> None:
    """Esquece a última listagem e a última porta boa.

    Existe para os testes: sem isto a lista lida no teste anterior
    valeria para o seguinte, e a ordem da sondagem deixaria de ser
    verificável.
    """

    global _listeners, _last_good_port, _wsl_password

    _listeners = None

    _last_good_port = None

    _wsl_password = None


def env_port() -> int | None:
    """Porta pedida na variável de ambiente, ou ``None``."""

    raw = os.environ.get(PORT_ENV, "").strip()

    if not raw:
        return None

    try:
        port = int(raw)

    except ValueError:
        log.warning("[pet] %s=%r não é uma porta; ignorando", PORT_ENV, raw)

        return None

    if not 1 <= port <= 65535:
        log.warning("[pet] %s=%d está fora da faixa; ignorando", PORT_ENV, port)

        return None

    return port


def find_opencode_ports(*, fresh: bool = False) -> list[int]:
    """Portas candidatas, na ordem em que vale a pena tentar.

    A ordem é: porta fixada pelo usuário, a que respondeu por último, e
    então as em escuta. A primeira é uma instrução; a segunda é um palpite
    com base; a terceira é a varredura.
    """

    global _listeners

    fixed = env_port()

    if fixed is not None:
        return [fixed]

    if not fresh and _listeners is not None:
        age = time.monotonic() - _listeners[0]

        if age < PORTS_TTL_SECONDS:
            return _ordered(_listeners[1])

    ports = listening_ports()

    _listeners = (time.monotonic(), ports)

    return _ordered(ports)


def _ordered(ports: list[int]) -> list[int]:
    """A última que respondeu primeiro, sem repetir."""

    ordered = list(ports)

    if _last_good_port in ordered:
        ordered.remove(_last_good_port)
        ordered.insert(0, _last_good_port)

    return ordered


# ------------------------------------------------------------
# Senha
# ------------------------------------------------------------

def _resolve_one(name: str, arguments: list[str]) -> list[str] | None:
    """Resolve um nome de CLI, com os sufixos do Windows."""

    if os.name == "nt":
        for suffix in (".exe", ".cmd", ".bat"):
            found = shutil.which(name + suffix)

            if found:
                return _with_shell(found, arguments)

    found = shutil.which(name)

    if found is None:
        return None

    return _with_shell(found, arguments)


def _with_shell(found: str, arguments: list[str]) -> list[str]:
    """Envolve ``.cmd``/``.bat`` com o interpretador.

    O ``CreateProcess`` não executa script em lote sem shell — sem isto o
    ``subprocess`` levanta ``WinError 193`` em vez de rodar o CLI do npm.
    """

    if os.name == "nt" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/d", "/s", "/c", found, *arguments]

    return [found, *arguments]


def resolve_password_command() -> list[str] | None:
    """Caminho do executável do CLI, ou ``None`` se não estiver no PATH.

    Tenta ``opencode2`` antes de ``opencode``: o primeiro é o binário novo
    (o que ``~/.opencode/bin`` instala) e o segundo é o que gerenciadores
    como o scoop colocam no Windows. No Windows o opencode costuma ser um
    ``.cmd`` (o npm põe assim) ou um ``.exe``.
    """

    for command in PASSWORD_COMMANDS:
        resolved = _resolve_one(command[0], list(command[1:]))

        if resolved is not None:
            return resolved

    # Compatibilidade: quem importava a constante antiga continua caindo
    # aqui quando ela não está na lista nova.
    if PASSWORD_COMMAND not in PASSWORD_COMMANDS:
        resolved = _resolve_one(PASSWORD_COMMAND[0], list(PASSWORD_COMMAND[1:]))

        if resolved is not None:
            return resolved

    return None


def env_password() -> str | None:
    """Senha pedida na variável de ambiente, ou ``None``.

    É a saída para o pet no Windows com o servidor no WSL: a senha do WSL
    mora em outro sistema de arquivos, e copiar uma vez vale mais que
    chamar o CLI errado a cada reconexão.
    """

    raw = os.environ.get(PASSWORD_ENV, "").strip()

    return raw or None


def _run_password_command(command: list[str]) -> str | None:
    """Roda um comando de senha e devolve o stdout limpo, ou ``None``."""

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PASSWORD_TIMEOUT,
            check=False,
            **_silent_kwargs(),
        )

    except Exception as exc:
        log.debug("[pet] CLI indisponível: %s", exc)
        return None

    if result.returncode != 0:
        return None

    return result.stdout.strip() or None


def get_wsl_password() -> str | None:
    """Senha do opencode que roda dentro do WSL, ou ``None``.

    Só faz sentido no Windows: o ``wslrelay`` expõe a porta do servidor do
    WSL em ``127.0.0.1``, então a descoberta por ``netstat`` já enxerga —
    o que falta é a senha, que é outra (cada sistema tem o seu
    ``service.json``). A ordem é o CLI pelo caminho completo (o ``PATH``
    do ``wsl`` não tem o ``~/.opencode/bin``) e, por último, o arquivo.
    """

    global _wsl_password

    if os.name != "nt":
        return None

    if _wsl_password is not None:
        age = time.monotonic() - _wsl_password[0]

        if age < WSL_PASSWORD_TTL_SECONDS:
            return _wsl_password[1]

    found = _read_wsl_password()

    _wsl_password = (time.monotonic(), found)

    return found


def _read_wsl_password() -> str | None:
    """Lê a senha do WSL sem cache, ou ``None``."""

    if os.name != "nt":
        return None

    wsl = shutil.which("wsl") or shutil.which("wsl.exe")

    if wsl is None:
        return None

    candidates = [
        [wsl, "--", "~/.opencode/bin/opencode2", "service", "get", "password"],
        [wsl, "--", "opencode2", "service", "get", "password"],
        [wsl, "--", "~/.opencode/bin/opencode", "service", "get", "password"],
    ]

    for command in candidates:
        password = _run_password_command(command)

        if password:
            return password

    service = _run_password_command(
        [wsl, "--", "cat", "~/.config/opencode/service.json"]
    )

    if not service:
        return None

    try:
        payload = json.loads(service)

    except ValueError as exc:
        log.debug("[pet] service.json do WSL não é JSON: %s", exc)
        return None

    if isinstance(payload, dict):
        password = payload.get("password")

        if isinstance(password, str) and password.strip():
            return password.strip()

    return None


def get_opencode_password() -> str | None:
    """Senha do serviço, ou ``None`` se nada responder.

    O pet é WSL-only no Windows: ali o único servidor que interessa é o
    do WSL, então o CLI local nem é consultado — a ordem é variável de
    ambiente e WSL. Fora do Windows vale o CLI local, como sempre.
    """

    direct = env_password()

    if direct:
        return direct

    if os.name == "nt":
        return get_wsl_password()

    command = resolve_password_command()

    if command is None:
        log.debug("[pet] CLI do opencode não está no PATH")

        return None

    password = _run_password_command(command)

    if password:
        return password

    log.debug("[pet] CLI local não respondeu")

    return None


def make_auth_header(password: str) -> str:
    """Header ``Authorization`` Basic para o usuário configurado."""

    raw = f"{USERNAME}:{password}".encode()

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


def server_is_alive(
    port: int,
    password: str,
    *,
    timeout: float = PROBE_REQUEST_TIMEOUT,
) -> bool:
    """Diz se a porta é o opencode.

    Exige ``200`` **e** ``Content-Type: text/event-stream``. O tipo de
    conteúdo é o que separa o opencode de um servidor qualquer na mesma
    máquina: sem ele, a varredura adotaria como opencode o primeiro
    servidor que respondesse "ok" em qualquer rota.
    """

    if not port_accepts_connections(port):
        return False

    connection = connect(port, password, timeout=timeout)

    try:
        response = connection.open()

        if response.status != 200:
            return False

        return SSE_CONTENT_TYPE in (response.headers.get("Content-Type") or "")

    except (HttpError, OSError) as exc:
        log.debug("[pet] porta %s não respondeu: %s", port, exc)

        return False

    finally:
        connection.close()


def find_opencode_server(
    password: str,
    ports: Iterable[int] | None = None,
    *,
    timeout: float = SWEEP_REQUEST_TIMEOUT,
) -> int | None:
    """Primeira porta que responde, ou ``None``.

    A porta que respondeu fica em memória e é a primeira da próxima
    varredura; o teto de :data:`~petwatch.config.MAX_SWEEP_PORTS` mantém o
    pior caso previsível.
    """

    global _last_good_port

    candidates = list(ports) if ports is not None else find_opencode_ports()

    for port in candidates[:MAX_SWEEP_PORTS]:
        if server_is_alive(port, password, timeout=timeout):
            _last_good_port = port

            return port

    return None
