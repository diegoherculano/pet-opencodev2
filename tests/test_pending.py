"""As consultas de pendência e de sessão, contra o servidor do opencode v2.

Cobre a leitura do corpo ``{location, data}`` das rotas ``/api/form`` e
``/api/permission/request``, o ``location[directory]`` no formato
``deepObject`` da spec, o isolamento de uma *location* rejeitada, a
degradação (404 = servidor antigo) e as duas rotas que dizem **quem** está
em ação: ``/api/session/active`` e ``/api/session/{id}``.

A arbitragem e o laço de consulta vivem em :mod:`petwatch.sessions` e são
testados em ``tests/test_sessions.py``.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from petwatch import pending
from petwatch.http import HttpError
from petwatch.pending import (
    KIND_FORM,
    KIND_PERMISSION,
    LocationNotFound,
    active_sessions,
    is_location_not_found,
    known_projects,
    location_query,
    pending_asks,
    pending_forms,
    pending_permissions,
    session_info,
    watched_directory,
    watched_directories,
)

PORT = 4096

FORM_BODY = {
    "location": {"directory": "/projetos/dd"},
    "data": [
        {
            "id": "frm_1",
            "sessionID": "ses_1",
            "title": "Questions",
            "metadata": {"kind": "question"},
            "fields": [],
        },
    ],
}

PERMISSION_BODY = {
    "location": {"directory": "/projetos/dd"},
    "data": [
        {
            "id": "per_1",
            "sessionID": "ses_1",
            "action": "shell",
            "resources": ["git push"],
        },
    ],
}


def fake_get(responses):
    """Dicionário ``(caminho, status, corpo)`` como resposta de ``get_json``."""

    calls: list[str] = []

    def _get(port, password, path, *, timeout=pending.PENDING_TIMEOUT):
        calls.append(path)

        for prefix, status, payload in responses:
            if path.startswith(prefix):
                return status, payload

        raise AssertionError(f"rota inesperada: {path}")

    return _get, calls


class LocationNotFoundTagTests(unittest.TestCase):
    """O corpo do 404 é o que separa *location* morta de rota ausente.

    As duas coisas chegam com o mesmo status, e a diferença entre elas é
    "o pet continua avisando" e "o pet desliga o recurso" — ver
    :class:`Bug20LocationTests`.
    """

    def test_the_tag_is_recognized(self):
        body = {"_tag": "LocationNotFoundError", "location": {"directory": "/p"}}

        self.assertTrue(is_location_not_found(body))

    def test_an_empty_body_is_a_missing_route(self):
        self.assertFalse(is_location_not_found(None))

    def test_another_error_tag_is_a_missing_route(self):
        self.assertFalse(is_location_not_found({"_tag": "NotFoundError"}))

    def test_a_body_without_a_tag_is_a_missing_route(self):
        self.assertFalse(is_location_not_found({"error": "not found"}))


class LocationQueryTests(unittest.TestCase):
    def test_deep_object_format(self):
        """A spec declara ``style: deepObject`` com ``explode: true``."""

        self.assertEqual(
            location_query("/projetos/dd"),
            "?location[directory]=%2Fprojetos%2Fdd",
        )

    def test_spaces_are_encoded(self):
        self.assertNotIn(" ", location_query("/meus projetos/dd"))


class PendingAsksTests(unittest.TestCase):
    def test_a_pending_form_becomes_an_ask(self):
        get, calls = fake_get([("/api/form", 200, FORM_BODY)])

        with mock.patch.object(pending, "get_json", get):
            asks = pending_forms(PORT, "senha", "/projetos/dd")

        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0].kind, KIND_FORM)
        self.assertEqual(asks[0].id, "frm_1")
        self.assertEqual(asks[0].session_id, "ses_1")
        self.assertEqual(asks[0].directory, "/projetos/dd")
        self.assertEqual(asks[0].title, "Questions")
        self.assertEqual(calls, ["/api/form?location[directory]=%2Fprojetos%2Fdd"])

    def test_an_empty_list_means_nothing_pending(self):
        get, _ = fake_get([("/api/form", 200, {"data": []})])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(pending_forms(PORT, "s", "/projetos/dd"), [])

    def test_a_permission_uses_the_action_as_title(self):
        get, _ = fake_get([("/api/permission/request", 200, PERMISSION_BODY)])

        with mock.patch.object(pending, "get_json", get):
            asks = pending_permissions(PORT, "s", "/projetos/dd")

        self.assertEqual(len(asks), 1)
        self.assertEqual(asks[0].kind, KIND_PERMISSION)
        self.assertEqual(asks[0].title, "shell")

    def test_missing_route_is_none_not_empty(self):
        """404 = servidor antigo. Isso é degradação, não "nada pendente"."""

        get, _ = fake_get([("/api/form", 404, {"error": "not found"})])

        with mock.patch.object(pending, "get_json", get):
            self.assertIsNone(pending_forms(PORT, "s", "/projetos/dd"))

    def test_server_error_is_an_error(self):
        get, _ = fake_get([("/api/form", 500, None)])

        with mock.patch.object(pending, "get_json", get):
            with self.assertRaises(HttpError):
                pending_forms(PORT, "s", "/projetos/dd")

    def test_items_without_id_are_ignored(self):
        body = {"data": [{"sessionID": "ses_1"}, "lixo", None]}

        get, _ = fake_get([("/api/form", 200, body)])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(pending_forms(PORT, "s", "/p"), [])

    def test_bare_list_is_accepted(self):
        """Tolerar a lista solta evita perder o estado por um envelope."""

        get, _ = fake_get([("/api/form", 200, FORM_BODY["data"])])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(len(pending_forms(PORT, "s", "/p")), 1)

    def test_one_project_costs_two_queries(self):
        get, calls = fake_get([
            ("/api/form", 200, {"data": []}),
            ("/api/permission/request", 200, PERMISSION_BODY),
        ])

        with mock.patch.object(pending, "get_json", get):
            asks = pending_asks(PORT, "s", ["/projetos/dd"])

        self.assertEqual(len(calls), 2)
        self.assertEqual([a.kind for a in asks], [KIND_PERMISSION])

    def test_no_project_means_nothing_pending(self):
        """Servidor novo, sem projeto: não é degradação."""

        get, calls = fake_get([])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(pending_asks(PORT, "s", []), [])

        self.assertEqual(calls, [])

    def test_a_missing_location_is_not_a_missing_route(self):
        """O 404 de *location* morta não é degradação.

        O opencode responde as duas coisas com 404 e o que as separa é o
        ``_tag`` do corpo. Ler o status sozinho desligava o "aguardando"
        inteiro por causa de um diretório que não existe mais — o bug 20.
        """

        body = {
            "_tag": "LocationNotFoundError",
            "location": {"directory": "/projetos/dd"},
            "message": "Location not found: /projetos/dd",
        }

        get, _ = fake_get([("/api/form", 404, body)])

        with mock.patch.object(pending, "get_json", get):
            with self.assertRaises(LocationNotFound):
                pending_forms(PORT, "s", "/projetos/dd")

    def test_the_permission_route_tells_the_two_aparts_too(self):
        body = {"_tag": "LocationNotFoundError"}

        get, _ = fake_get([("/api/permission/request", 404, body)])

        with mock.patch.object(pending, "get_json", get):
            with self.assertRaises(LocationNotFound):
                pending_permissions(PORT, "s", "/projetos/dd")

    def test_a_dead_location_does_not_turn_the_feature_off(self):
        """Um projeto apagado não pode calar o pet inteiro.

        Reproduzido no servidor v2 de verdade: ``/api/project`` guarda
        diretórios que já não têm pasta, e eles respondem 404. Com o 404
        lido como "rota ausente", o recurso inteiro desligava e o pet
        voltava a depender do stream — o que produz "Thinking" com a
        pergunta aberta na tela.
        """

        def _get(_port, _password, path, *, timeout=pending.PENDING_TIMEOUT):
            if "morto" in path:
                return 404, {"_tag": "LocationNotFoundError"}

            if path.startswith("/api/form"):
                return 200, FORM_BODY

            return 200, {"data": []}

        missing: set[str] = set()

        with mock.patch.object(pending, "get_json", _get):
            asks = pending_asks(PORT, "s", ["/projetos/morto", "/projetos/dd"],
                                missing=missing)

        # Degradação desliga o recurso e devolve ``None``.
        self.assertIsNotNone(asks)
        self.assertEqual([a.id for a in asks], ["frm_1"])
        self.assertEqual(missing, {"/projetos/morto"})

    def test_a_location_that_came_back_is_asked_again(self):
        """``missing`` é preenchido no caminho, não é um filtro de entrada."""

        def _get(_port, _password, path, *, timeout=pending.PENDING_TIMEOUT):
            if "morto" in path:
                return 200, {"data": []}

            if path.startswith("/api/form"):
                return 200, {"data": []}

            return 200, {"data": []}

        missing: set[str] = set()

        with mock.patch.object(pending, "get_json", _get):
            pending_asks(PORT, "s", ["/projetos/morto"], missing=missing)

        self.assertEqual(missing, set())

    def test_only_missing_locations_mean_nothing_is_pending(self):
        """Não sobrou nenhum *location* vivo, e nenhum falhou.

        Um diretório que não existe não tem nada pendente, então a lista
        vazia é a resposta certa — e não um erro, nem uma degradação.
        """

        def _get(_port, _password, path, *, timeout=pending.PENDING_TIMEOUT):
            return 404, {"_tag": "LocationNotFoundError"}

        with mock.patch.object(pending, "get_json", _get):
            asks = pending_asks(PORT, "s", ["/a", "/b"])

        self.assertEqual(asks, [])

    def test_a_missing_location_alongside_a_failure_is_an_error(self):
        """Aí já não deu para saber de ninguém, e erro mantém o estado."""

        def _get(_port, _password, path, *, timeout=pending.PENDING_TIMEOUT):
            if "sumiu" in path:
                return 404, {"_tag": "LocationNotFoundError"}

            return 500, None

        with mock.patch.object(pending, "get_json", _get):
            with self.assertRaises(HttpError):
                pending_asks(PORT, "s", ["/projetos/sumiu", "/quebrou"])

    def test_the_tag_is_what_separates_them(self):
        """Um 404 com corpo de JSON qualquer continua sendo rota ausente."""

        get, _ = fake_get([("/api/form", 404, {"error": "not found"})])

        with mock.patch.object(pending, "get_json", get):
            self.assertIsNone(pending_forms(PORT, "s", "/projetos/dd"))

    def test_a_dead_location_does_not_hide_the_others(self):
        """Um projeto apagado da lista não pode calar o pet inteiro.

        Verificado no servidor v2 de verdade: um *location* que não é um
        projeto responde **500**, não 404 — então ele se manifesta como
        erro de consulta, e erro de uma location tem de ser isolado.
        """

        def _get(_port, _password, path, *, timeout=pending.PENDING_TIMEOUT):
            if "morto" in path:
                return 500, None

            if path.startswith("/api/form"):
                return 200, {"data": []}

            return 200, {"data": PERMISSION_BODY["data"]}

        with mock.patch.object(pending, "get_json", _get):
            asks = pending_asks(PORT, "s", ["/projetos/morto", "/projetos/dd"])

        self.assertEqual([a.id for a in asks], ["per_1"])

    def test_every_location_failing_is_an_error(self):
        def _get(_port, _password, _path, *, timeout=pending.PENDING_TIMEOUT):
            return 500, None

        with mock.patch.object(pending, "get_json", _get):
            with self.assertRaises(HttpError):
                pending_asks(PORT, "s", ["/a", "/b"])

    def test_projects_come_canonical_and_without_repeats(self):
        body = [
            {"id": "a", "canonical": "/projetos/dd"},
            {"id": "b", "canonical": "/projetos/dd"},
            {"id": "c", "canonical": "/projetos/pet"},
            {"id": "d"},
        ]

        get, _ = fake_get([("/api/project", 200, body)])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(
                known_projects(PORT, "s"),
                ["/projetos/dd", "/projetos/pet"],
            )

    def test_watched_directory_wins_over_the_project_list(self):
        get, calls = fake_get([("/api/project", 200, [{"canonical": "/outro"}])])

        with mock.patch.dict(os.environ, {"PETWATCH_DIRECTORY": "/projetos/dd"}):
            with mock.patch.object(pending, "get_json", get):
                self.assertEqual(watched_directories(PORT, "s"), ["/projetos/dd"])

        # Nem chegou a consultar a lista de projetos.
        self.assertEqual(calls, [])

    def test_environment_directory_is_absolute(self):
        with mock.patch.dict(os.environ, {"PETWATCH_DIRECTORY": "~/codigo"}):
            self.assertTrue(os.path.isabs(watched_directory()))

    def test_without_environment_every_project_is_watched(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(watched_directory())

class ActiveSessionsTests(unittest.TestCase):
    """``/api/session/active`` é quem define "em ação".

    A doc é explícita: *"Retrieve foreground Session drains currently
    owned by this OpenCode process. Sessions absent from the result are
    inactive."*
    """

    def test_ids_are_read_from_the_data_map(self):
        get, _ = fake_get([
            ("/api/session/active", 200,
             {"data": {"ses_a": {"type": "running"}, "ses_b": {"type": "running"}}}),
        ])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(
                sorted(active_sessions(PORT, "s") or []),
                ["ses_a", "ses_b"],
            )

    def test_an_empty_map_means_nobody_is_working(self):
        get, _ = fake_get([("/api/session/active", 200, {"data": {}})])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(active_sessions(PORT, "s"), [])

    def test_a_failure_is_none_not_empty(self):
        """``None`` = não deu para saber.

        A diferença é a que impede o pet de apagar todos os balões por
        causa de um GET que deu timeout.
        """

        for status in (401, 404, 500, 502):
            with self.subTest(status=status):
                get, _ = fake_get([("/api/session/active", status, None)])

                with mock.patch.object(pending, "get_json", get):
                    self.assertIsNone(active_sessions(PORT, "s"))

    def test_a_body_without_data_is_not_a_list(self):
        get, _ = fake_get([("/api/session/active", 200, {"outro": 1})])

        with mock.patch.object(pending, "get_json", get):
            self.assertEqual(active_sessions(PORT, "s"), [])

    def test_the_session_name_comes_from_the_session_route(self):
        """``Session.Info`` traz ``title`` e ``location``."""

        body = {
            "data": {
                "id": "ses_a",
                "projectID": "p",
                "title": "Corrigindo o falso positivo",
                "location": {"directory": "/home/diego/projetos/dd"},
            },
        }

        get, calls = fake_get([("/api/session/ses_a", 200, body)])

        with mock.patch.object(pending, "get_json", get):
            info = session_info(PORT, "s", "ses_a")

        self.assertEqual(info["title"], "Corrigindo o falso positivo")
        self.assertEqual(info["location"]["directory"], "/home/diego/projetos/dd")
        self.assertEqual(calls, ["/api/session/ses_a"])

    def test_a_missing_session_is_none(self):
        get, _ = fake_get([("/api/session/ses_a", 404, None)])

        with mock.patch.object(pending, "get_json", get):
            self.assertIsNone(session_info(PORT, "s", "ses_a"))


if __name__ == "__main__":
    unittest.main()
