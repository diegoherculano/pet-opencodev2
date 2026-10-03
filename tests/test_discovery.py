"""Transporte HTTP e descoberta do servidor."""

from __future__ import annotations

import http.server
import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from petwatch.config import EVENT_PATH, HOST  # noqa: E402
from petwatch.discovery import (  # noqa: E402
    connect,
    event_headers,
    find_opencode_server,
    make_auth_header,
    port_accepts_connections,
    server_is_alive,
)
from petwatch.http import Connection, HttpError  # noqa: E402


class SilentHandler(http.server.BaseHTTPRequestHandler):
    """Servidor de teste que serve um stream SSE que nunca termina."""

    protocol_version = "HTTP/1.1"

    #: Caminhos que devem responder 200.
    ok_path = EVENT_PATH

    def do_GET(self):  # noqa: N802
        if self.path != self.ok_path:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for line in (
                b'data: {"type":"server.connected"}\n',
                b"\n",
                b": heartbeat\n",
                b"\n",
            ):
                self.wfile.write(b"%X\r\n%s\r\n" % (len(line), line))
                self.wfile.flush()

            # Sem chunk final a resposta continua aberta, então a próxima
            # leitura do cliente fica presa até o socket ser desligado.
            # Só termina quando o cliente for embora.
            while self.rfile.readline():
                pass

        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass

    def log_message(self, *args):
        pass


class ServerFixture:
    """Sobe um servidor local em porta livre e devolve a porta."""

    def __init__(self, handler=SilentHandler):
        self.server = http.server.ThreadingHTTPServer((HOST, 0), handler)
        self.port = self.server.server_address[1]

        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class AuthHeaderTests(unittest.TestCase):
    def test_basic_scheme(self):
        header = make_auth_header("segredo")

        self.assertTrue(header.startswith("Basic "))

        import base64

        decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
        self.assertEqual(decoded, "opencode:segredo")

    def test_headers_shape(self):
        probe = event_headers("p")
        stream = event_headers("p", keep_alive=True)

        self.assertEqual(probe["Accept"], "text/event-stream")
        self.assertEqual(probe["Cache-Control"], "no-cache")
        self.assertNotIn("Connection", probe)
        self.assertEqual(stream["Connection"], "keep-alive")


class PortTests(unittest.TestCase):
    def test_dead_port(self):
        self.assertFalse(port_accepts_connections(9, timeout=0.2))

    def test_refuses_unparseable_password_gracefully(self):
        # fetch_opencode_password falha devolve None e nada mais acontece.
        from petwatch.discovery import get_opencode_password

        self.assertIsInstance(get_opencode_password(), (str, type(None)))


class ConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = ServerFixture()
        cls.password = "senha-de-teste"

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    def connection(self, password: str | None = None) -> Connection:
        return connect(
            self.fixture.port,
            password or self.password,
            timeout=5,
        )

    def open(self, password: str | None = None) -> Connection:
        return self.connection(password).open()

    def test_status_and_headers(self):
        with self.connection() as conn:
            self.assertEqual(conn.status, 200)
            self.assertEqual(
                conn.headers.get("Content-Type"), "text/event-stream"
            )

    def test_reads_sse_lines(self):
        with self.connection() as conn:
            self.assertEqual(conn.readline(), b'data: {"type":"server.connected"}\n')
            self.assertEqual(conn.readline(), b"\n")
            self.assertEqual(conn.readline(), b": heartbeat\n")

    def test_interrupt_unblocks_a_stuck_read(self):
        conn = self.open()

        try:
            # Consome exatamente o que o servidor enviou; a próxima
            # leitura não tem dado e trava.
            for expected in (
                b'data: {"type":"server.connected"}\n',
                b"\n",
                b": heartbeat\n",
                b"\n",
            ):
                self.assertEqual(conn.readline(), expected)

            result: list[tuple[str, object]] = []

            def reader():
                try:
                    result.append(("linha", conn.readline()))
                except Exception as exc:
                    result.append(("excecao", f"{type(exc).__name__}: {exc}"))

            worker = threading.Thread(target=reader, daemon=True)
            worker.start()

            # Dá tempo da thread travar de fato na leitura.
            threading.Event().wait(0.3)
            self.assertTrue(worker.is_alive(), "leitura não travou")
            self.assertEqual(result, [])

            conn.interrupt()

            worker.join(5)

            self.assertFalse(worker.is_alive(), "interrupt não destravou")
            self.assertEqual(result, [("linha", b"")])
        finally:
            conn.close()

    def test_interrupt_is_safe_before_open_and_after_close(self):
        conn = connect(self.fixture.port, self.password, timeout=5)

        # Antes de abrir: sem socket, não deve estourar.
        conn.interrupt()

        conn.open()
        conn.close()

        # Depois de fechar: idem.
        conn.interrupt()
        conn.interrupt()

    def test_close_is_idempotent(self):
        conn = self.open()

        conn.close()
        conn.close()

    def test_readline_before_open(self):
        conn = connect(self.fixture.port, self.password, timeout=5)

        with self.assertRaises(HttpError):
            conn.readline()

        with self.assertRaises(HttpError):
            _ = conn.status

    def test_context_manager_closes(self):
        with self.connection() as conn:
            self.assertEqual(conn.status, 200)

        with self.assertRaises(HttpError):
            conn.readline()

    def test_unreachable_port_raises_http_error(self):
        conn = connect(9, self.password, timeout=0.3)

        with self.assertRaises(HttpError):
            conn.open()

        # Fechar depois da falha não pode estourar.
        conn.close()

    def test_wrong_path_returns_non_200(self):
        conn = Connection(
            HOST, self.fixture.port, "/nao-existe", {}, timeout=5
        ).open()

        try:
            self.assertEqual(conn.status, 404)
        finally:
            conn.close()


class ServerIsAliveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = ServerFixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    def test_alive(self):
        self.assertTrue(server_is_alive(self.fixture.port, "senha"))

    def test_not_alive_on_closed_port(self):
        self.assertFalse(server_is_alive(9, "senha"))

    def test_find_opencode_server(self):
        found = find_opencode_server("senha", ports=[9, self.fixture.port])

        self.assertEqual(found, self.fixture.port)

    def test_find_opencode_server_none(self):
        self.assertIsNone(find_opencode_server("senha", ports=[9]))


if __name__ == "__main__":
    unittest.main()
