"""O que é específico de plataforma: parsers, pipe, instância e console.

Nenhum destes testes precisa de Windows para rodar. É a razão de o
``petwatch`` não ter um diretório de código por sistema:

- os **parsers** recebem o texto da listagem de portas e devolvem inteiros,
  então o formato do ``netstat`` é testado a partir de uma amostra, no
  Linux;
- o **pipe** usa ``AF_UNIX`` quando a plataforma não é o Windows, e o
  protocolo inteiro (cliente e servidor) é exercitado de verdade no
  diretório temporário;
- o **console** troca ``sys.stdout`` por ``None``, que é exatamente o que o
  ``--noconsole`` do PyInstaller faz.

O que fica de fora, e por quê: ``DETACHED_PROCESS``, o ``msvcrt.locking`` e
os caminhos do registro do Windows precisam da plataforma para ter
significado. Esses são conferidos no build, não na suíte.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from petwatch import console, discovery, pipe
from petwatch.daemon import SingleInstance, detached_command, report_startup
from petwatch.instance import PipeInstance, open_instance, serve_quit

#: Amostra de ``ss -ltn`` no Linux. A coluna ``State`` vem antes do
#: endereço e é o que separa escuta de conexão, e o nome do processo não
#: aparece — é justamente por isso que o parser só olha o endereço.
SS_SAMPLE = """
State  Recv-Q Send-Q Local Address:Port  Peer Address:Port
LISTEN 0      511    127.0.0.1:4096      0.0.0.0:*
LISTEN 0      128    0.0.0.0:22           0.0.0.0:*
LISTEN 0      511    [::]:631             [::]:*
LISTEN 0      128    [::1]:5432           [::]:*
ESTAB  0      0      127.0.0.1:52000      127.0.0.1:4096
LISTEN 0      128    192.168.0.14:8080    0.0.0.0:*
"""

#: Amostra de ``netstat -ano -p tcp``. A coluna do PID é o que o Windows
#: oferece de útil, e é de propósito ignorada: o que interessa é a porta,
#: não de quem é.
NETSTAT_SAMPLE = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:4096         0.0.0.0:0              LISTENING       12345
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1024
  TCP    [::]:445               [::]:0                 LISTENING       4
  TCP    [::1]:5432             [::]:0                 LISTENING       5678
  TCP    127.0.0.1:52000        127.0.0.1:4096         ESTABLISHED     9999
  TCP    0.0.0.0:3000           0.0.0.0:0              LISTENING       4321
  TCP    192.168.0.14:8080      0.0.0.0:0              LISTENING       4322
"""

#: Amostra de ``/proc/net/tcp``, com as colunas que o parser usa: ``sl``,
#: ``local_address``, ``rem_address`` e ``st``. O endereço local é
#: hexadecimal little-endian — ``0100007F`` é 127.0.0.1, ``00000000`` é
#: 0.0.0.0 — e a **porta também**: ``1F90`` são 8080.
PROC_SAMPLE = """
  sl  local_address rem_address   st tx_queue rx_queue
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000
   1: 00000000:0016 00000000:0000 0A 00000000:00000000
   2: 0100007F:CF90 0100007F:1F90 01 00000000:00000000
"""

#: Loopback primeiro, depois os demais — é a ordem que
#: ``listening_ports`` devolve.
LOOPBACK_THEN_OTHERS = [4096, 22, 8080]


# ------------------------------------------------------------
# Parsers de porta
# ------------------------------------------------------------

class ProcNetParserTests(unittest.TestCase):
    def test_only_listening_lines(self):
        ports = [port for port, _ in discovery.parse_proc_net_tcp(PROC_SAMPLE)]

        # A terceira linha é uma conexão estabelecida (st=01) e não entra.
        self.assertEqual(sorted(ports), [22, 8080])

    def test_the_port_is_read_as_hexadecimal(self):
        """``1F90`` são 8080, e não 1. ``0.0016`` são 22, e não 16.

        Ler como decimal não falharia de forma visível: as portas erradas
        virariam candidatas reais, e a varredura só estaria mais lenta.
        """

        found = dict(discovery.parse_proc_net_tcp(PROC_SAMPLE))

        self.assertIn(8080, found)
        self.assertIn(22, found)
        self.assertNotIn(8088, found)

    def test_loopback_is_told_apart_from_the_rest(self):
        found = dict(discovery.parse_proc_net_tcp(PROC_SAMPLE))

        self.assertTrue(found[8080])
        self.assertTrue(found[22], "0.0.0.0 também é alcançado por loopback")

    def test_a_truncated_line_does_not_raise(self):
        for sample in (
            "",
            "sl local",
            "0: 0100007F:1F90 00000000:0000",
        ):
            with self.subTest(sample=sample):
                self.assertEqual(discovery.parse_proc_net_tcp(sample), [])

    def test_a_port_that_is_not_hex_is_dropped(self):
        sample = "   0: 0100007F:ZZZZ 00000000:0000 0A 00000000:00000000"

        self.assertEqual(discovery.parse_proc_net_tcp(sample), [])

    def test_ipv6_loopback(self):
        sample = (
            "  sl  local_address rem_address   st tx_queue rx_queue\n"
            "   0: 00000000000000000000000001000000:1538"
            " 00000000000000000000000000000000:0000 0A 00000000:00000000\n"
        )

        found = discovery.parse_proc_net_tcp(sample)

        self.assertEqual([port for port, _ in found], [5432])
        self.assertTrue(found[0][1], "::1 é loopback")

    def test_port_out_of_range_is_dropped(self):
        sample = "   0: 0100007F:0000 00000000:0000 0A 00000000:00000000\n"

        self.assertEqual(discovery.parse_proc_net_tcp(sample), [])


class SsParserTests(unittest.TestCase):
    def test_listening_ports(self):
        ports = [port for port, _ in discovery.parse_ss_listen(SS_SAMPLE)]

        # ESTAB fora, e a linha em 192.168.0.14 também: é escuta, mas não
        # é alcançável por 127.0.0.1, que é onde a sondagem acontece.
        self.assertEqual(sorted(ports), [22, 631, 4096, 5432, 8080])

    def test_loopback_is_told_apart_from_a_bound_address(self):
        found = dict(discovery.parse_ss_listen(SS_SAMPLE))

        # 0.0.0.0 e [::] são "todos os endereços", então o loopback também
        # chega neles.
        self.assertTrue(found[4096], "127.0.0.1")
        self.assertTrue(found[22], "0.0.0.0")
        self.assertTrue(found[631], "[::]")

        # Preso à interface da LAN, o loopback não chega.
        self.assertFalse(found[8080], "192.168.0.14")

    def test_an_established_connection_is_not_a_candidate(self):
        """A linha ESTAB tem endereço e porta, e não aceita nada."""

        found = dict(discovery.parse_ss_listen(SS_SAMPLE))

        self.assertNotIn(52000, found)

    def test_an_empty_output_is_no_ports(self):
        self.assertEqual(discovery.parse_ss_listen(""), [])


class NetstatParserTests(unittest.TestCase):
    def test_only_listening(self):
        ports = [
            port for port, _ in discovery.parse_netstat_ano(NETSTAT_SAMPLE)
        ]

        # ESTABLISHED fora; os headers, fora.
        self.assertEqual(
            sorted(ports),
            [135, 445, 3000, 4096, 5432, 8080],
        )

    def test_loopback_is_told_apart(self):
        found = dict(discovery.parse_netstat_ano(NETSTAT_SAMPLE))

        self.assertTrue(found[4096], "127.0.0.1")
        self.assertTrue(found[5432], "[::1]")
        self.assertTrue(found[135], "0.0.0.0 é alcançado por loopback")
        self.assertFalse(found[8080], "192.168.0.14 não é")

    def test_the_pid_column_is_not_used(self):
        """O PID não entra no desenho.

        O Windows entrega o PID, mas casar por nome exigiria um ``tasklist``
        por processo — e o que identifica o opencode é a senha e o tipo de
        conteúdo da resposta, não o nome do executável.
        """

        # Duas linhas iguais, PIDs muito diferentes: mesmo resultado.
        one = "  TCP    127.0.0.1:4096  0.0.0.0:0  LISTENING  111\n"
        two = "  TCP    127.0.0.1:4096  0.0.0.0:0  LISTENING  999\n"

        self.assertEqual(
            discovery.parse_netstat_ano(one),
            discovery.parse_netstat_ano(two),
        )

    def test_a_foreign_language_line_is_ignored(self):
        """``netstat`` localize as colunas, mas os estados traduzidos.

        O que não muda é o ``TCP`` e o número da porta, e é só isso que o
        parser usa — por isso ele não depende nem de idioma nem de versão.
        """

        sample = (
            "  Proto  Endereço Local          Endereço Remoto"
            "        Estado           PID\n"
            "  TCP    0.0.0.0:135            0.0.0.0:0"
            "              ESCUTANDO       1024\n"
        )

        self.assertEqual(
            discovery.parse_netstat_ano(sample),
            [],
            "estado traduzido não é reconhecido, e a porta é ignorada",
        )

    def test_ipv6_addresses(self):
        found = dict(discovery.parse_netstat_ano(NETSTAT_SAMPLE))

        self.assertIn(445, found)
        self.assertTrue(found[445], "[::] é alcançado por loopback")

    def test_the_bound_address_is_last_in_the_listing(self):
        """A ordem não é detalhe: é o que decide o custo da varredura."""

        with mock.patch.object(
            discovery,
            "netstat_listeners",
            return_value=[(8080, False), (4096, True)],
        ):
            self.assertEqual(discovery.listening_ports("win32"), [4096, 8080])


class PlatformSelectionTests(unittest.TestCase):
    """``listening_ports`` escolhe a fonte certa em cada plataforma."""

    def setUp(self):
        discovery.reset_cache()

        self.addCleanup(discovery.reset_cache)

    def test_windows_uses_netstat(self):
        with mock.patch.object(
            discovery, "netstat_listeners", return_value=[(4096, True)]
        ) as netstat, mock.patch.object(discovery, "ss_listeners") as ss:
            self.assertEqual(
                discovery.listening_ports("win32"),
                [4096],
            )

        netstat.assert_called_once()

        ss.assert_not_called()

    def test_windows_does_not_touch_proc(self):
        """``/proc`` não existe, e procurar por ele seria ruído."""

        with mock.patch.object(
            discovery, "read_proc_listeners"
        ) as proc, mock.patch.object(discovery, "netstat_listeners", return_value=[]):
            discovery.listening_ports("win32")

        proc.assert_not_called()

    def test_linux_prefers_proc_over_ss(self):
        with mock.patch.object(
            discovery, "read_proc_listeners", return_value=[(4096, True)]
        ), mock.patch.object(discovery, "ss_listeners") as ss:
            self.assertEqual(discovery.listening_ports("linux"), [4096])

        ss.assert_not_called()

    def test_linux_falls_back_to_ss_without_proc(self):
        with mock.patch.object(
            discovery, "read_proc_listeners", return_value=[]
        ), mock.patch.object(discovery, "ss_listeners", return_value=[(22, False)]):
            self.assertEqual(discovery.listening_ports("linux"), [22])

    def test_loopback_comes_before_the_rest(self):
        with mock.patch.object(
            discovery,
            "netstat_listeners",
            return_value=[(3000, False), (22, True), (4096, True)],
        ):
            self.assertEqual(
                discovery.listening_ports("win32"),
                [22, 4096, 3000],
            )

    def test_duplicates_collapse(self):
        """A mesma porta em duas linhas custa uma sondagem a mais.

        É o caso comum de um serviço que escuta em todo endereço: o
        ``netstat`` mostra uma linha em ``127.0.0.1`` e outra em
        ``192.168.0.14``, e as duas são a mesma porta.
        """

        with mock.patch.object(
            discovery,
            "netstat_listeners",
            return_value=[(4096, True), (4096, True), (4096, False)],
        ):
            self.assertEqual(discovery.listening_ports("win32"), [4096])

    def test_a_port_bound_twice_is_still_one_candidate(self):
        with mock.patch.object(
            discovery,
            "netstat_listeners",
            return_value=[(4096, True), (4096, False), (8080, False)],
        ):
            self.assertEqual(discovery.listening_ports("win32"), [4096, 8080])


class SweepTests(unittest.TestCase):
    """A ordem em que as portas são tentadas."""

    def setUp(self):
        discovery.reset_cache()

        self.addCleanup(discovery.reset_cache)

    def test_the_last_good_port_goes_first(self):
        discovery._last_good_port = 5432

        with mock.patch.object(
            discovery, "env_port", return_value=None
        ), mock.patch.object(
            discovery, "listening_ports", return_value=[22, 4096, 5432]
        ):
            self.assertEqual(
                discovery.find_opencode_ports(),
                [5432, 22, 4096],
            )

    def test_the_env_var_overrides_everything(self):
        discovery._last_good_port = 5432

        with mock.patch.dict(os.environ, {"PETWATCH_PORT": "9999"}):
            self.assertEqual(discovery.find_opencode_ports(), [9999])

    def test_an_invalid_env_var_is_ignored_with_a_warning(self):
        with mock.patch.dict(
            os.environ, {"PETWATCH_PORT": "abc"}
        ), self.assertLogs("petwatch.discovery", level="WARNING"):
            self.assertIsNone(discovery.env_port())

    def test_a_port_out_of_range_is_refused(self):
        with mock.patch.dict(
            os.environ, {"PETWATCH_PORT": "70000"}
        ), self.assertLogs("petwatch.discovery", level="WARNING"):
            self.assertIsNone(discovery.env_port())

    def test_the_listing_is_cached(self):
        with mock.patch.object(
            discovery, "listening_ports", return_value=[4096]
        ) as listing:
            discovery.find_opencode_ports()
            discovery.find_opencode_ports()

        listing.assert_called_once()

    def test_fresh_ignores_the_cache(self):
        with mock.patch.object(
            discovery, "listening_ports", return_value=[4096]
        ) as listing:
            discovery.find_opencode_ports()
            discovery.find_opencode_ports(fresh=True)

        self.assertEqual(listing.call_count, 2)

    def test_the_sweep_is_capped(self):
        """A varredura não pode varrer a faixa efêmera inteira."""

        many = list(range(40000, 40000 + discovery.MAX_SWEEP_PORTS + 20))

        with mock.patch.object(
            discovery, "find_opencode_ports", return_value=many
        ), mock.patch.object(discovery, "server_is_alive", return_value=False) as alive:
            self.assertIsNone(discovery.find_opencode_server("senha"))

        self.assertEqual(alive.call_count, discovery.MAX_SWEEP_PORTS)

    def test_the_sweep_uses_the_shorter_timeout(self):
        """Na varredura a porta é uma aposta, e aposta pede resposta rápida."""

        with mock.patch.object(
            discovery, "find_opencode_ports", return_value=[4096]
        ), mock.patch.object(discovery, "server_is_alive", return_value=True) as alive:
            discovery.find_opencode_server("s")

        self.assertEqual(
            alive.call_args.kwargs["timeout"],
            discovery.SWEEP_REQUEST_TIMEOUT,
        )

    def test_the_first_answer_wins(self):
        with mock.patch.object(
            discovery, "find_opencode_ports", return_value=[9, 4096]
        ), mock.patch.object(
            discovery, "server_is_alive", side_effect=[False, True]
        ):
            self.assertEqual(discovery.find_opencode_server("s"), 4096)

    def test_the_answer_is_remembered_for_the_next_sweep(self):
        with mock.patch.object(
            discovery, "find_opencode_ports", return_value=[4096]
        ), mock.patch.object(discovery, "server_is_alive", return_value=True):
            discovery.find_opencode_server("s")

        self.assertEqual(discovery._last_good_port, 4096)


class ContentTypeTests(unittest.TestCase):
    """Um 200 sozinho não é o opencode."""

    def connection(self, status: int, content_type: str):
        """Uma :class:`~petwatch.http.Connection` de mentira.

        O que interessa é que ``open()`` devolve algo com ``status`` e
        ``headers`` — o resto é o que ``server_is_alive`` não toca.
        """

        response = mock.Mock()

        response.status = status

        response.headers = {"Content-Type": content_type}

        connection = mock.Mock()

        connection.open.return_value = response

        return connection

    def alive(self, connection) -> bool:
        with mock.patch.object(
            discovery, "port_accepts_connections", return_value=True
        ), mock.patch.object(discovery, "connect", return_value=connection):
            return discovery.server_is_alive(4096, "senha")

    def test_a_plain_200_is_not_enough(self):
        """O sintoma seria um dev server virando "o opencode"."""

        self.assertFalse(
            self.alive(self.connection(200, "text/html; charset=utf-8")),
        )

    def test_a_404_is_not_enough(self):
        self.assertFalse(
            self.alive(self.connection(404, "text/event-stream")),
        )

    def test_the_sse_content_type_is_accepted(self):
        self.assertTrue(
            self.alive(self.connection(200, "text/event-stream")),
        )

    def test_a_charset_after_the_type_still_counts(self):
        self.assertTrue(
            self.alive(self.connection(200, "text/event-stream; charset=utf-8")),
        )


class PasswordCommandTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(discovery.reset_cache)

    def test_a_resolved_executable_is_used_as_is(self):
        with mock.patch.object(
            discovery.shutil, "which", return_value="/usr/bin/opencode2"
        ):
            self.assertEqual(
                discovery.resolve_password_command(),
                ["/usr/bin/opencode2", "service", "get", "password"],
            )

    def test_a_missing_cli_is_not_an_error(self):
        with mock.patch.object(discovery.shutil, "which", return_value=None):
            self.assertIsNone(discovery.resolve_password_command())

    def test_without_the_cli_the_password_is_none(self):
        with mock.patch.object(discovery.shutil, "which", return_value=None):
            self.assertIsNone(discovery.get_opencode_password())

    @mock.patch.dict(os.environ, {}, clear=False)
    def test_on_windows_the_exe_suffix_is_tried_first(self):
        """``opencode2.exe`` antes de ``opencode2``: o que o PATH devolve."""

        def which(name):
            return "C:\\bin\\opencode2.exe" if name.endswith(".exe") else None

        with mock.patch.object(
            discovery.os, "name", "nt"
        ), mock.patch.object(discovery.shutil, "which", side_effect=which):
            self.assertEqual(
                discovery.resolve_password_command()[0],
                "C:\\bin\\opencode2.exe",
            )

    @mock.patch.dict(os.environ, {}, clear=False)
    def test_a_cmd_fallback_is_found(self):
        """O npm instala como ``.cmd``, que existe mas não é executável
        sem shell — achá-lo é melhor do que dizer que o CLI não existe."""

        def which(name):
            return "C:\\bin\\opencode2.cmd" if name.endswith(".cmd") else None

        with mock.patch.object(
            discovery.os, "name", "nt"
        ), mock.patch.object(discovery.shutil, "which", side_effect=which):
            self.assertEqual(
                discovery.resolve_password_command()[0],
                "C:\\bin\\opencode2.cmd",
            )


# ------------------------------------------------------------
# Pipe
# ------------------------------------------------------------

class PipeNameTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-pipe-"))

    def test_the_name_is_unique_per_state_dir(self):
        other = self.directory / "outro"

        self.assertNotEqual(
            pipe.pipe_name(self.directory),
            pipe.pipe_name(other),
        )

    def test_the_name_is_stable(self):
        """O ``--stop`` precisa achar o mesmo pipe do pet."""

        self.assertEqual(
            pipe.pipe_name(self.directory),
            pipe.pipe_name(self.directory),
        )

    def test_the_name_carries_the_user(self):
        """Pipes são globais na máquina: sem o usuário, dois usuários no
        mesmo Windows colidiriam."""

        with mock.patch.dict(os.environ, {"USERNAME": "digo"}):
            self.assertIn("digo", pipe.pipe_name(self.directory))


class AuthkeyTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-key-"))

    def test_the_key_is_created_once(self):
        first = pipe.ensure_authkey(self.directory)

        self.assertIsInstance(first, bytes)

        self.assertEqual(first, pipe.ensure_authkey(self.directory))

    def test_a_race_loser_reads_the_winner_key(self):
        """Dois processos podem pedir a chave no mesmo instante."""

        pipe.ensure_authkey(self.directory)

        def already_exists(*_args, **_kwargs):
            raise FileExistsError

        with mock.patch.object(pipe.os, "open", already_exists):
            self.assertEqual(
                pipe.ensure_authkey(self.directory),
                pipe.load_authkey(self.directory),
            )

    def test_reading_without_a_key_does_not_create_one(self):
        self.assertIsNone(pipe.load_authkey(self.directory))

        self.assertFalse(pipe.authkey_path(self.directory).exists())

    def test_an_empty_key_is_not_a_key(self):
        pipe.authkey_path(self.directory).write_bytes(b"")

        self.assertIsNone(pipe.load_authkey(self.directory))


class PipeProtocolTests(unittest.TestCase):
    """O protocolo inteiro, exercitado de verdade com ``AF_UNIX``.

    É o mesmo caminho que o ``--stop`` usa no Windows — cliente, servidor,
    handshake — só que o transporte é um socket de arquivo, porque named
    pipe não existe no Linux. Se o protocolo quebrar, quebra aqui.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-proto-"))

        self.address = str(self.directory / "pet.sock")

        self.family = pipe.AF_UNIX

        self.received: list[str] = []

    def server(self, handler=None):
        handler = handler or (lambda request: (True, "ok"))

        server = pipe.PipeServer(
            handler,
            self.address,
            self.family,
            state_dir=self.directory,
        )

        self.assertTrue(server.start())

        self.addCleanup(server.stop)

        return server

    def test_a_request_gets_its_answer(self):
        self.server()

        self.assertEqual(
            pipe.ask(
                "ping",
                self.address,
                self.family,
                state_dir=self.directory,
            ),
            (True, "ok"),
        )

    def test_the_server_sees_the_request(self):
        self.server(lambda request: (True, request))

        reply = pipe.ask(
            "stop",
            self.address,
            self.family,
            state_dir=self.directory,
        )

        self.assertEqual(reply, (True, "stop"))

    def test_a_client_without_a_key_asks_nothing(self):
        self.server()

        self.assertIsNone(
            pipe.ask(
                "ping",
                self.address,
                self.family,
                state_dir=self.directory,
                authkey=b"errado",
            )
        )

    def test_a_server_without_a_key_does_not_start(self):
        with mock.patch.object(
            pipe, "ensure_authkey", return_value=None
        ), self.assertLogs("petwatch.pipe", level="WARNING"):
            server = pipe.PipeServer(
                lambda request: (True, None),
                self.address,
                self.family,
                state_dir=self.directory,
            )

            self.assertFalse(server.start())

    def test_an_exception_in_the_handler_answers_error(self):
        """Um handler que estoura não pode derrubar o servidor.

        O ``--stop`` depende de uma resposta: sem ela, o ``--status`` do
        usuário seguinte ficaria esperando até o prazo.
        """

        def explode(request):
            raise RuntimeError("deu ruim")

        self.server(explode)

        self.assertEqual(
            pipe.ask(
                "ping",
                self.address,
                self.family,
                state_dir=self.directory,
            ),
            (False, "erro"),
        )

    def test_asking_a_nobody_is_none(self):
        """Nenhum pet rodando é a resposta normal, não um erro."""

        self.assertIsNone(
            pipe.ask(
                "ping",
                self.address,
                self.family,
                state_dir=self.directory,
            )
        )


# ------------------------------------------------------------
# Instância
# ------------------------------------------------------------

class OpenInstanceTests(unittest.TestCase):
    def test_posix_gets_the_file_lock(self):
        self.assertIsInstance(open_instance(platform="linux"), SingleInstance)

    def test_windows_gets_the_pipe(self):
        self.assertIsInstance(open_instance(platform="win32"), PipeInstance)


class PipeInstanceTests(unittest.TestCase):
    """A instância única do Windows, com ``AF_UNIX`` de transporte."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-inst-"))

        self.path = self.directory / "pet.pid"

        self.socket = self.directory / "pet.sock"

        self.instances: list[PipeInstance] = []

    def tearDown(self):
        for instance in self.instances:
            instance.release()

    def instance(self) -> PipeInstance:
        instance = PipeInstance(self.path, self.directory)

        # O endereço vem do AF_UNIX do diretório temporário; a estratégia é
        # a mesma, só o transporte muda.
        instance.pipe_address = str(self.socket)

        self.instances.append(instance)

        return instance

    def patched_pipe(self):
        """Faz o :class:`PipeInstance` falar pelo socket temporário."""

        original = pipe.PipeServer

        def build(handler, address=None, family=None, **kwargs):
            return original(
                handler,
                str(self.socket),
                pipe.AF_UNIX,
                **kwargs,
            )

        patch = mock.patch.object(pipe, "PipeServer", build)

        patch.start()

        self.addCleanup(patch.stop)

    def test_the_first_claim_wins(self):
        self.patched_pipe()

        self.assertTrue(self.instance().claim())

    def test_a_second_claim_names_the_owner(self):
        self.patched_pipe()

        first = self.instance()

        self.assertTrue(first.claim())

        pid = first.record_pid()

        second = self.instance()

        self.assertFalse(second.claim())
        self.assertEqual(second.owner, pid)

    def test_running_is_false_when_nobody_listens(self):
        self.patched_pipe()

        self.assertFalse(self.instance().running())

    def test_running_sees_the_owner(self):
        self.patched_pipe()

        first = self.instance()

        first.claim()

        pid = first.record_pid()

        watcher = self.instance()

        self.assertTrue(watcher.running())
        self.assertEqual(watcher.owner, pid)

    def test_release_lets_the_next_one_in(self):
        self.patched_pipe()

        first = self.instance()

        first.claim()

        first.release()

        self.assertTrue(self.instance().claim())

    def test_record_pid_writes_the_file(self):
        """O arquivo é registro, não garantia — mas é o que se lê."""

        self.patched_pipe()

        instance = self.instance()

        instance.claim()

        pid = instance.record_pid()

        self.assertEqual(self.path.read_text(encoding="ascii"), f"{pid}\n")

    def test_record_pid_without_a_claim_is_an_error(self):
        with self.assertRaises(RuntimeError):
            self.instance().record_pid()

    def test_a_failure_writing_the_file_does_not_stop_the_pet(self):
        """A garantia é o pipe; o arquivo é só o registro."""

        self.patched_pipe()

        instance = self.instance()

        instance.claim()

        with mock.patch.object(
            Path, "write_text", side_effect=OSError("sem espaço")
        ), self.assertLogs("petwatch.instance", level="WARNING"):
            pid = instance.record_pid()

        self.assertEqual(pid, os.getpid())

    def test_request_stop_calls_the_bound_quit(self):
        self.patched_pipe()

        stopped: list[int] = []

        instance = self.instance()

        instance.claim()

        instance.record_pid()

        instance.bind_quit(lambda: stopped.append(1))

        self.assertTrue(self.instance().request_stop())

        self.assertEqual(stopped, [1])

    def test_request_stop_without_a_server_says_no(self):
        self.patched_pipe()

        self.assertFalse(self.instance().request_stop())

    def test_an_unknown_request_is_refused(self):
        self.patched_pipe()

        instance = self.instance()

        instance.claim()

        ok, payload = instance._answer("desligar-tudo-agora")

        self.assertFalse(ok)
        self.assertIn("desligar", payload)


class PipeInstanceStateDirTests(unittest.TestCase):
    """O Windows começa de uma máquina sem ``%LOCALAPPDATA%\\petwatch``.

    O diretório é criado por quem **assume** a instância, e não por quem
    pergunta: perguntar não pode deixar rastro na máquina.
    """

    def test_claiming_creates_the_state_directory(self):
        from petwatch.instance import ensure_state_dir

        self.assertTrue(callable(ensure_state_dir))

        with mock.patch("petwatch.instance.ensure_state_dir") as ensure:
            directory = Path(tempfile.mkdtemp(prefix="petwatch-statedir-"))

            instance = PipeInstance(directory / "pet.pid", directory)

            with mock.patch.object(pipe, "PipeServer", lambda *a, **k: _Fake()):
                instance.claim()

            instance.release()

        ensure.assert_called_once()

    def test_a_directory_that_cannot_be_created_does_not_crash(self):
        """Sem chave nem pipe, a instância única desliga — mas o app abre."""

        blocker = Path(tempfile.mkdtemp(prefix="petwatch-blocked-")) / "arquivo"

        blocker.write_text("sou um arquivo", encoding="utf-8")

        instance = PipeInstance(blocker / "pet.pid", blocker.parent)

        with mock.patch(
            "petwatch.instance.ensure_state_dir"
        ), mock.patch.object(pipe, "PipeServer", lambda *a, **k: _Fake()):
            self.assertTrue(instance.claim())

        instance.release()


class _Fake:
    """Servidor de pipe que não escuta nada.

    O que o teste quer é o ``claim`` do :class:`PipeInstance` — a criação
    do diretório e o tratamento de falha — sem abrir um socket de verdade.
    """

    def start(self) -> bool:
        return True

    def record_pid(self, pid=None) -> None:
        return None

    def stop(self) -> None:
        return None


class ServeQuitTests(unittest.TestCase):
    def test_the_posix_instance_ignores_it(self):
        """No POSIX o caminho é o sinal; ligar o pipe não faz sentido."""

        serve_quit(SingleInstance(Path(tempfile.mkdtemp()) / "pet.pid"), lambda: None)

    def test_the_pipe_instance_is_bound(self):
        directory = Path(tempfile.mkdtemp(prefix="petwatch-serve-"))

        instance = PipeInstance(directory / "pet.pid", directory)

        calls: list[str] = []

        serve_quit(instance, lambda: calls.append("quit"))

        instance._on_stop()

        self.assertEqual(calls, ["quit"])


class StopSignalTests(unittest.TestCase):
    """O pedido do pipe chega como sinal, e não como ``quit()`` direto.

    ``QCoreApplication.quit()`` toca no event loop, e o event loop é da
    thread da interface — o pedido sai de outra. É o mesmo motivo que faz
    o ``state_changed`` do monitor ser ``QueuedConnection``.
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_the_signal_exists(self):
        from petwatch.app import PetApplication

        self.assertTrue(hasattr(PetApplication, "stop_requested"))


# ------------------------------------------------------------
# Confirmação de abertura por arquivo (o handshake do Windows)
# ------------------------------------------------------------

class ReadyFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-ready-"))

        self.path = self.directory / "pet.ready"

    def test_ok_carries_the_pid(self):
        report_startup(self.path)

        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            f"ok {os.getpid()}\n",
        )

    def test_the_error_is_a_single_line(self):
        """Um traceback com quebras viraria duas mensagens."""

        report_startup(self.path, "RuntimeError: qt\nnão achou xcb")

        content = self.path.read_text(encoding="utf-8")

        self.assertEqual(content.count("\n"), 1)
        self.assertTrue(content.startswith("erro RuntimeError:"))

    def test_no_file_written_atomicamente(self):
        """O pai pode estar olhando o arquivo a qualquer instante.

        Sem o "escreve ao lado e troca", ele leria um arquivo pela metade e
        contaria isso como falha de abertura — com a mensagem ``não consegui
        abrir a janela`` e nenhuma janela no lugar.
        """

        report_startup(self.path)

        self.assertFalse((self.directory / "pet.ready.tmp").exists())

    def test_reporting_twice_does_not_raise(self):
        report_startup(self.path)

        report_startup(self.path)
        report_startup(self.path, "segunda vez")

    def test_awaiting_a_missing_file_gives_up(self):
        from petwatch.daemon import _await_confirmation

        with mock.patch("petwatch.daemon.READY_POLL", 0.01):
            self.assertIsNone(_await_confirmation(self.path, 0.05))

    def test_awaiting_finds_the_message(self):
        from petwatch.daemon import _await_confirmation

        self.path.write_text(f"ok {os.getpid()}\n", encoding="utf-8")

        self.assertEqual(
            _await_confirmation(self.path, 0.5),
            f"ok {os.getpid()}",
        )

    def test_the_confirmation_file_is_cleaned_up(self):
        """Um arquivo ``.ready`` velho faria o pet seguinte se anunciar
        antes de abrir."""

        from petwatch.daemon import _await_confirmation

        self.path.write_text("ok 123\n", encoding="utf-8")

        _await_confirmation(self.path, 0.5)

        self.assertFalse(self.path.exists())


class DetachedCommandTests(unittest.TestCase):
    def test_a_frozen_app_reruns_the_executable(self):
        """Numa instalação congelada quem chama é o próprio ``.exe``."""

        with mock.patch.object(
            sys, "frozen", True, create=True
        ), mock.patch.object(sys, "executable", "C:\\Pet\\petwatch.exe"):
            self.assertEqual(
                detached_command(),
                ["C:\\Pet\\petwatch.exe", "--child"],
            )

    def test_a_normal_installation_reruns_pet_py(self):
        command = detached_command()

        self.assertEqual(command[1].endswith("pet.py"), True, command)

        self.assertEqual(command[-1], "--child")


# ------------------------------------------------------------
# Console
# ------------------------------------------------------------

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-console-"))

        patch = mock.patch.object(console, "LOG_PATH", self.directory / "pet.log")

        patch.start()

        self.addCleanup(patch.stop)

    def say_without_stdout(self, message: str, **kwargs) -> None:
        """Executa um ``say`` como o ``--noconsole`` o faria."""

        with mock.patch.object(sys, "stdout", None):
            console.say(message, **kwargs)

    def test_a_message_without_console_goes_to_the_log(self):
        self.say_without_stdout("[pet] encerrado (pid 1)")

        self.assertIn(
            "encerrado (pid 1)",
            (self.directory / "pet.log").read_text(encoding="utf-8"),
        )

    def test_nothing_is_raised_without_stdout(self):
        """O sintoma seria um ``AttributeError`` no lugar da mensagem."""

        self.say_without_stdout("[pet] qualquer")

    def test_error_goes_to_stderr(self):
        """Com a saída redirecionada num arquivo, o erro tem de ficar
        separado do registro normal."""

        import contextlib
        import io

        said = io.StringIO()

        complained = io.StringIO()

        with contextlib.redirect_stdout(
            said
        ), contextlib.redirect_stderr(complained):
            console.say("falhou", error=True)

        self.assertEqual(complained.getvalue().strip(), "falhou")
        self.assertEqual(said.getvalue(), "")

    def test_the_popup_is_not_shown_with_a_console(self):
        """Um terminal já é o lugar da resposta."""

        import contextlib
        import io

        with contextlib.redirect_stdout(
            io.StringIO()
        ), mock.patch.object(console, "_message_box") as box:
            console.say("oi", popup=True)

        box.assert_not_called()

    def test_the_popup_is_shown_without_a_console(self):
        """O duplo clique não tem terminal: a caixa é a resposta."""

        with mock.patch.object(
            sys, "stdout", None
        ), mock.patch.object(
            console, "IS_WINDOWS", True
        ), mock.patch.object(console, "_message_box") as box:
            console.say("encerrado", popup=True)

        box.assert_called_once()

        self.assertIn("encerrado", box.call_args[0])

    def test_the_popup_does_not_happen_off_windows(self):
        """No Linux não há MessageBox, e o terminal já responde."""

        with mock.patch.object(
            sys, "stdout", None
        ), mock.patch.object(
            console, "IS_WINDOWS", False
        ), mock.patch.object(console, "_message_box") as box:
            console.say("encerrado", popup=True)

        box.assert_not_called()

    def test_a_broken_popup_does_not_raise(self):
        with mock.patch.object(
            sys, "stdout", None
        ), mock.patch.object(
            console, "IS_WINDOWS", True
        ), mock.patch.object(
            console, "_message_box", side_effect=OSError("sem display")
        ):
            console.say("oi", popup=True)

    def test_a_closed_stdout_is_not_a_traceback(self):
        import contextlib
        import io

        closed = io.StringIO()

        closed.close()

        with contextlib.redirect_stdout(closed):
            console.say("oi")


class LoggingTests(unittest.TestCase):
    """O log sem console, que é o que faz um ``.exe`` valer a pena.

    Um ``basicConfig()`` sem argumentos cria um handler para ``sys.stderr``;
    com ``sys.stderr`` ``None`` as mensagens caem no ``lastResort``, que
    descarta a linha **sem avisar**. O sintoma seria um pet funcionando
    com um log vazio e nenhum erro na tela.
    """

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="petwatch-logcfg-"))

        self.path = self.directory / "pet.log"

        root = logging.getLogger()

        saved = list(root.handlers)

        saved_level = root.level

        def restore():
            for handler in list(root.handlers):
                handler.close()

                root.removeHandler(handler)

            for handler in saved:
                root.addHandler(handler)

            root.setLevel(saved_level)

        self.addCleanup(restore)

        root.handlers.clear()

    def test_without_stderr_the_log_file_is_used(self):
        with mock.patch.object(sys, "stderr", None):
            console.configure_logging(log_path=self.path)

        # Um logger fora de ``petwatch``: o ``tests/__init__.py`` cala esse
        # pacote inteiro para a saída da suíte não ficar poluída.
        logging.getLogger("teste-configuracao").info("no log")

        self.assertIn("no log", self.path.read_text(encoding="utf-8"))

    def test_with_stderr_the_stream_is_used(self):
        import io

        stream = io.StringIO()

        with mock.patch.object(sys, "stderr", stream):
            console.configure_logging(log_path=self.path)

        logging.getLogger("teste-configuracao").info("na tela")

        self.assertIn("na tela", stream.getvalue())

        self.assertFalse(self.path.exists())

    def test_configuring_twice_does_not_duplicate(self):
        with mock.patch.object(sys, "stderr", None):
            console.configure_logging(log_path=self.path)

            first = len(logging.getLogger().handlers)

            console.configure_logging(log_path=self.path)

        self.assertEqual(len(logging.getLogger().handlers), first)

    def test_a_log_that_cannot_be_written_does_not_raise(self):
        """Perder o diagnóstico é melhor que não abrir o app."""

        blocker = self.directory / "bloqueio"

        blocker.write_text("sou um arquivo", encoding="utf-8")

        with mock.patch.object(
            sys, "stderr", None
        ), self.assertLogs("petwatch.console", level="WARNING"):
            console.configure_logging(log_path=blocker / "pet.log")


class QuitSignalTests(unittest.TestCase):
    def test_the_list_depends_on_the_platform(self):
        from petwatch.app import QUIT_SIGNALS
        from petwatch.config import IS_WINDOWS

        self.assertIn(signal.SIGINT, QUIT_SIGNALS)

        if IS_WINDOWS:
            self.assertNotIn(
                signal.SIGTERM,
                QUIT_SIGNALS,
                "SIGTERM no Windows é TerminateProcess, nunca handler",
            )

        else:
            self.assertIn(signal.SIGTERM, QUIT_SIGNALS)


if __name__ == "__main__":
    unittest.main()
