"""Parser do stream SSE."""

from __future__ import annotations

import io
import threading
import unittest

from petwatch.sse import consume, parse_sse_stream


def collect(blob: bytes) -> list[tuple[str | None, object]]:
    """Roda o parser sobre um blob de bytes e devolve os eventos."""

    events: list[tuple[str | None, object]] = []
    stop = threading.Event()
    stream = io.BytesIO(blob)

    consume(iter(stream.readline, b""), stop,
            lambda name, data: events.append((name, data)))

    return events


class SseStreamTests(unittest.TestCase):
    def test_json_data(self):
        blob = b'data: {"type":"server.connected","data":{}}\n\n'

        self.assertEqual(
            collect(blob),
            [(None, {"type": "server.connected", "data": {}})],
        )

    def test_named_event(self):
        blob = b'event: ping\ndata: {"a":1}\n\n'
        self.assertEqual(collect(blob), [("ping", {"a": 1})])

    def test_multiple_events(self):
        blob = b'event: a\ndata: 1\n\nevent: b\ndata: 2\n\n'
        self.assertEqual(collect(blob), [("a", 1), ("b", 2)])

    def test_multiline_data_is_joined_with_newline(self):
        blob = b"data: linha1\ndata: linha2\n\n"
        self.assertEqual(collect(blob), [(None, "linha1\nlinha2")])

    def test_comments_are_ignored(self):
        # ': heartbeat' sozinho não gera evento, mesmo seguido de linha vazia.
        self.assertEqual(collect(b": heartbeat\n\n"), [])
        blob = b': heartbeat\n\ndata: {"a":1}\n\n: heartbeat\n\n'
        self.assertEqual(collect(blob), [(None, {"a": 1})])

    def test_non_json_data(self):
        self.assertEqual(collect(b"data: nao-json\n\n"), [(None, "nao-json")])
        self.assertEqual(collect(b"data: [1,2]\n\n"), [(None, [1, 2])])
        self.assertEqual(collect(b"data: null\n\n"), [(None, None)])

    def test_empty_data_line(self):
        self.assertEqual(collect(b"data:\n\n"), [(None, "")])

    def test_no_space_after_colon(self):
        self.assertEqual(collect(b"data:{}\n\n"), [(None, {})])

    def test_unknown_fields_ignored(self):
        blob = b'id: 1\nretry: 5000\ndata: {"a":1}\n\n'
        self.assertEqual(collect(blob), [(None, {"a": 1})])

    def test_crlf_line_endings(self):
        blob = b'event: e\r\ndata: {"a":1}\r\n\r\n'
        self.assertEqual(collect(blob), [("e", {"a": 1})])

    def test_incomplete_event_not_emitted(self):
        # Sem linha vazia final o evento não é despachado.
        self.assertEqual(collect(b'data: {"a":1}'), [])
        self.assertEqual(collect(b'data: {"a":1}\n'), [])

    def test_utf8_and_invalid_bytes(self):
        self.assertEqual(collect("data: café\n\n".encode()), [(None, "café")])
        self.assertEqual(collect(b"data: \xff\n\n"), [(None, "\ufffd")])

    def test_empty_input(self):
        self.assertEqual(collect(b""), [])
        self.assertEqual(collect(b"\n"), [])

    def test_stop_event_halts_iteration(self):
        stop = threading.Event()
        stop.set()

        lines = [b'data: 1\n', b'\n']

        events = list(parse_sse_stream(lines, stop))

        self.assertEqual(events, [])

    def test_str_lines_are_accepted(self):
        events = list(parse_sse_stream(['data: 1\n', '\n'], threading.Event()))
        self.assertEqual([(e.name, e.data) for e in events], [(None, 1)])


if __name__ == "__main__":
    unittest.main()
