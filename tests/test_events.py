"""Eventos do opencode -> estados do pet."""

from __future__ import annotations

import unittest

from petwatch.events import (
    extract_event_type,
    extract_status,
    is_ask_event,
    is_release_event,
    state_from_event,
)
from petwatch.states import STATE_IDLE, STATE_WORKING


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

    def test_questions_are_not_states(self):
        """Nenhum pedido vira estado.

        O "aguardando" é decidido pela consulta ao servidor
        (``GET /api/form`` / ``GET /api/permission/request``), e não pelo
        stream: ele é global (todos os projetos) e perde eventos, então
        deduzir daqui era o que produzia falso positivo.
        """

        for event_type in (
            "permission.asked",
            "permission.v2.asked",
            "question.asked",
            "question.v2.asked",
            "form.created",
        ):
            with self.subTest(event_type=event_type):
                self.assertIsNone(state_from_event(None, {"type": event_type}))

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

    def test_finish_is_idle(self):
        for event_type in ("session.idle", "turn.idle", "session.execution.succeeded"):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_IDLE,
                )

    def test_permission_request_is_not_a_state_either(self):
        """O nome real é ``permission.asked``, e mesmo assim não é estado."""

        for event_type in ("session.permission.create", "session.form.create"):
            with self.subTest(event_type=event_type):
                self.assertIsNone(state_from_event(None, {"type": event_type}))

    def test_permission_answer_resumes(self):
        for event_type in (
            "session.permission.reply",
            "session.permission.reject",
            "session.form.reply",
            "session.form.cancel",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_agent_activity_is_working(self):
        for event_type in (
            "session.step.started",
            "session.step.streamed",
            "session.text.started",
            "session.reasoning.started",
            "session.tool.called",
            "session.tool.progress",
            "session.compaction.started",
            "session.shell.started",
            "session.execution.started",
            "session.retry.scheduled",
        ):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_instant_events_leave_state_untouched(self):
        """Eventos que occurrem no meio do turno nao devem virar estado.

        ``session.step.ended`` e ``session.tool.success`` disparam entre
        passos; se virassem estado, o pet piscaria entre "working" e
        "idle" varias vezes por turno.
        """

        for event_type in (
            "session.step.ended",
            "session.tool.success",
            "session.text.ended",
            "session.text.delta",
            "session.usage.updated",
        ):
            with self.subTest(event_type=event_type):
                self.assertIsNone(state_from_event(None, {"type": event_type}))

    def test_session_completed_does_not_exist_anymore(self):
        """O opencode instalado nao emite esse evento."""

        self.assertIsNone(state_from_event(None, {"type": "session.completed"}))

    def test_case_insensitive(self):
        self.assertEqual(
            state_from_event(None, {"type": "Session.Idle"}),
            STATE_IDLE,
        )

    def test_unrelated_event_keeps_state(self):
        # `file.edited` entrou em WORKING_EVENTS de propósito: a IA
        # editando um arquivo é trabalho em andamento, não ociosidade.
        for event_type in ("server.connected", "installation.updated",
                           "lsp.updated", "session.metadata.updated"):
            with self.subTest(event_type=event_type):
                self.assertIsNone(state_from_event(None, {"type": event_type}))

        self.assertIsNone(state_from_event(None, {}))
        self.assertIsNone(state_from_event(None, None))

    def test_editing_a_file_is_working(self):
        """Editar arquivo ou rodar comando é trabalho, não 'pronto'."""

        for event_type in ("file.edited", "fs.write", "shell.created",
                           "shell.output", "shell.exited"):
            with self.subTest(event_type=event_type):
                self.assertEqual(
                    state_from_event(None, {"type": event_type}),
                    STATE_WORKING,
                )

    def test_busy_status_beats_finalization_rule(self):
        # session.idled não casa nada, mas o status manda em session.updated.
        payload = {"type": "session.updated", "status": "retry"}
        self.assertEqual(state_from_event(None, payload), STATE_WORKING)


class AskTriggerTests(unittest.TestCase):
    """O evento de pedido é **gatilho**, nunca estado.

    O ``form.created`` só diz "vale conferir no servidor". Quem decide
    é ``GET /api/form`` / ``GET /api/permission/request`` — que são
    escopados por projeto e não perdem o que já foi respondido.
    """

    def test_a_question_wakes_the_pet(self):
        for event_type in (
            "permission.asked",
            "form.created",
            "session.form.create",
            "session.permission.create",
            "permission.v2.asked",
            "question.asked",
        ):
            with self.subTest(event_type=event_type):
                self.assertTrue(is_ask_event(None, {"type": event_type}))

    def test_the_answer_wakes_the_pet_too(self):
        """Soltar a trava depressa também é um gatilho."""

        for event_type in (
            "form.replied",
            "form.cancelled",
            "permission.replied",
            "session.form.reply",
            "question.cancelled",
        ):
            with self.subTest(event_type=event_type):
                self.assertTrue(is_release_event(None, {"type": event_type}))

    def test_work_events_are_neither(self):
        for event_type in (
            "session.step.started",
            "session.tool.called",
            "shell.exited",
            "file.edited",
            "session.execution.succeeded",
            "server.connected",
        ):
            with self.subTest(event_type=event_type):
                self.assertFalse(is_ask_event(None, {"type": event_type}))
                self.assertFalse(is_release_event(None, {"type": event_type}))

    def test_case_insensitive_and_stream_name(self):
        self.assertTrue(is_ask_event(None, {"type": "Form.Created"}))
        self.assertTrue(is_ask_event("permission.asked", {"type": "outro"}))

    def test_garbage_in_is_not_a_trigger(self):
        for payload in (None, [], 42, "form.created", {}):
            with self.subTest(payload=payload):
                self.assertFalse(is_ask_event(None, payload))
                self.assertFalse(is_release_event(None, payload))


if __name__ == "__main__":
    unittest.main()
