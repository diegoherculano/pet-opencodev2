"""Eventos do opencode -> estados do pet."""

from __future__ import annotations

import unittest

from petwatch.events import (
    extract_event_type,
    extract_status,
    state_from_event,
)
from petwatch.states import STATE_IDLE, STATE_WAITING, STATE_WORKING


class ExtractEventTypeTests(unittest.TestCase):
    def test_stream_name_wins(self):
        self.assertEqual(
            extract_event_type("session.idle", {"type": "message.updated"}),
            "session.idle",
        )

    def test_falls_back_to_payload_type(self):
        self.assertEqual(
            extract_event_type(None, {"type": "session.idle"}),
            "session.idle",
        )

    def test_accepts_event_and_name_keys(self):
        self.assertEqual(extract_event_type(None, {"event": "a"}), "a")
        self.assertEqual(extract_event_type(None, {"name": "b"}), "b")

    def test_first_non_string_key_is_skipped(self):
        self.assertEqual(
            extract_event_type(None, {"type": 5, "event": "fallback"}),
            "fallback",
        )

    def test_looks_inside_properties(self):
        self.assertEqual(
            extract_event_type(None, {"properties": {"type": "form.created"}}),
            "form.created",
        )

    def test_non_dict_payload(self):
        for payload in (None, [], "texto", 42, True):
            self.assertIsNone(extract_event_type(None, payload))

    def test_non_dict_properties_ignored(self):
        self.assertIsNone(
            extract_event_type(None, {"properties": "nao-dict"})
        )


class ExtractStatusTests(unittest.TestCase):
    def test_top_level_status_and_state(self):
        self.assertEqual(extract_status({"status": "BUSY"}), "busy")
        self.assertEqual(extract_status({"state": "Idle"}), "idle")

    def test_nested_properties(self):
        self.assertEqual(
            extract_status({"properties": {"status": "ready"}}),
            "ready",
        )

    def test_nested_session(self):
        self.assertEqual(
            extract_status({"session": {"state": "running"}}),
            "running",
        )

    def test_top_level_wins_over_nested(self):
        payload = {
            "status": "busy",
            "session": {"status": "idle"},
        }
        self.assertEqual(extract_status(payload), "busy")

    def test_non_dict_payload(self):
        for payload in (None, [], "idle", 7):
            self.assertIsNone(extract_status(payload))

    def test_non_string_values_skipped(self):
        payload = {"status": 5, "state": None, "session": {"status": "idle"}}
        self.assertEqual(extract_status(payload), "idle")


class StateFromEventTests(unittest.TestCase):
    def test_session_status_uses_payload_status(self):
        busy = {"type": "session.status", "status": "busy"}
        idle = {"type": "session.status", "status": "idle"}
        self.assertEqual(state_from_event(None, busy), STATE_WORKING)
        self.assertEqual(state_from_event(None, idle), STATE_IDLE)

    def test_unknown_session_status_falls_through(self):
        payload = {"type": "session.status", "status": "error"}
        self.assertIsNone(state_from_event(None, payload))

    def test_work_markers(self):
        for event_type in (
            "step.started",
            "tool.called",
            "message.updated",
            "message.part.updated",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_retry_markers(self):
        for event_type in ("retry", "retried", "session.retry"):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_questions_are_waiting(self):
        for event_type in (
            "permission.asked",
            "permission.v2.asked",
            "question.asked",
            "question.v2.asked",
            "form.created",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WAITING,
                )

    def test_replies_are_working(self):
        for event_type in (
            "permission.replied",
            "permission.rejected",
            "form.replied",
            "form.cancelled",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_session_finish_is_idle(self):
        for event_type in ("session.idle", "session.completed"):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_IDLE,
                )

    def test_case_insensitive(self):
        self.assertEqual(
            state_from_event(None, {"type": "Session.Idle"}),
            STATE_IDLE,
        )

    def test_unrelated_event_keeps_state(self):
        self.assertIsNone(state_from_event(None, {"type": "file.edited"}))
        self.assertIsNone(state_from_event(None, {}))
        self.assertIsNone(state_from_event(None, None))

    def test_busy_status_beats_finalization_rule(self):
        # session.idled não casa nada, mas o status manda em session.updated.
        payload = {"type": "session.updated", "status": "retry"}
        self.assertEqual(state_from_event(None, payload), STATE_WORKING)


if __name__ == "__main__":
    unittest.main()
