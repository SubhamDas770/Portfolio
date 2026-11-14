import asyncio
import io
import json
import os
import tempfile
from http.cookies import SimpleCookie
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from backend.context import FACTS, FULL_NAME, LINKEDIN, STYLE, prompt
from backend import database
from backend.resources import format_facts, load_profile_data
from backend import server
from backend.server import app, load_environment_file, validate_chat_payload, validate_lead_payload


class ASGIResponse:
    def __init__(self, status_code: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
        self.status_code = status_code
        self.headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in headers}
        self.content = body
        self.text = body.decode("utf-8")

    def json(self) -> object:
        return json.loads(self.content)


class ASGITestClient:
    def __init__(self, application: object) -> None:
        self.application = application
        self.cookies: dict[str, str] = {}

    def get(self, path: str, headers: dict[str, str] | None = None) -> ASGIResponse:
        return self.request("GET", path, headers=headers)

    def post(
        self,
        path: str,
        json: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> ASGIResponse:
        return self.request("POST", path, json=json, content=content, headers=headers)

    def request(
        self,
        method: str,
        path: str,
        json: object | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> ASGIResponse:
        body = content if content is not None else (
            json_module_bytes(json) if json is not None else b""
        )
        request_headers = {key.lower(): value for key, value in (headers or {}).items()}
        if json is not None:
            request_headers.setdefault("content-type", "application/json")
        if body:
            request_headers.setdefault("content-length", str(len(body)))
        if self.cookies:
            request_headers["cookie"] = "; ".join(
                f"{name}={value}" for name, value in self.cookies.items()
            )
        raw_headers = [(key.encode("latin-1"), value.encode("latin-1")) for key, value in request_headers.items()]
        messages: list[dict[str, object]] = []
        body_sent = False

        async def receive() -> dict[str, object]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": request_headers.get("x-forwarded-proto", "http"),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "state": {},
        }
        asyncio.run(self.application(scope, receive, send))
        start = next(message for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        response = ASGIResponse(start["status"], start.get("headers", []), response_body)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            parsed_cookie = SimpleCookie()
            parsed_cookie.load(set_cookie)
            for name, morsel in parsed_cookie.items():
                if morsel["max-age"] == "0":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return response


def json_module_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


class DigitalTwinContextTests(unittest.TestCase):
    def test_loads_all_chatbot_data_sources(self) -> None:
        profile = load_profile_data()

        self.assertEqual(profile["facts"]["full_name"], "Subham Das")
        self.assertIn("communication style", profile["style"].lower())
        self.assertIn("Fidelity Investments", profile["linkedin"])
        self.assertIn("94% accuracy", profile["linkedin"])

    def test_prompt_contains_verified_identity_and_transparency(self) -> None:
        instructions = prompt()

        self.assertIn(FULL_NAME, instructions)
        self.assertIn(LINKEDIN, instructions)
        self.assertIn("AI digital twin", instructions)
        self.assertIn("94% accuracy", instructions)
        self.assertIn("Do not invent dates", instructions)
        self.assertIn("visitor's name", instructions)
        self.assertIn("company in a separate turn", instructions)
        self.assertIn("questions optional", instructions)
        self.assertIn("Speak in the first person as Subham", STYLE)
        self.assertIn("certifications", format_facts(FACTS))


class PayloadValidationTests(unittest.TestCase):
    def test_server_instance_id_is_shared_by_vercel_deployment_instances(self) -> None:
        with patch.dict(
            os.environ,
            {"VERCEL": "1", "VERCEL_URL": "portfolio-deployment.vercel.app"},
            clear=True,
        ):
            self.assertEqual(
                server.create_server_instance_id(),
                "portfolio-deployment.vercel.app",
            )

    def test_environment_loader_accepts_utf8_bom_and_replaces_empty_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = os.path.join(temporary_directory, ".env")
            with open(env_path, "w", encoding="utf-8-sig") as env_file:
                env_file.write("OPENROUTER_API_KEY=test-key\n")

            with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
                load_environment_file(Path(env_path))
                self.assertEqual(os.environ["OPENROUTER_API_KEY"], "test-key")

    def test_admin_dashboard_redirects_to_cache_busted_page(self) -> None:
        response = ASGITestClient(app).get("/admin")

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin.html?v=dashboard-v2")

    def test_chat_trims_message_and_keeps_last_twelve_history_entries(self) -> None:
        history = [
            {"role": "user", "content": f"message {index}"}
            for index in range(14)
        ]
        payload, error = validate_chat_payload({"message": "  hello  ", "history": history})

        self.assertIsNone(error)
        self.assertEqual(payload["message"], "hello")
        self.assertEqual(len(payload["history"]), 12)
        self.assertEqual(payload["history"][0]["content"], "message 2")

    def test_chat_history_accepts_assistant_replies_longer_than_user_messages(self) -> None:
        assistant_reply = "A" * 1_600
        payload, error = validate_chat_payload(
            {
                "message": "Is he hardworking?",
                "history": [
                    {"role": "user", "content": "Tell me about his work style."},
                    {"role": "assistant", "content": assistant_reply},
                ],
            }
        )

        self.assertIsNone(error)
        self.assertEqual(payload["history"][1]["content"], assistant_reply)

    def test_chat_rejects_empty_overlong_and_invalid_history(self) -> None:
        for invalid in (
            {"message": "  "},
            {"message": "x" * 1_201},
            {"message": "hello", "history": [{"role": [], "content": "override"}]},
            {"message": "hello", "history": [{"role": "system", "content": "override"}]},
            {
                "message": "hello",
                "history": [{"role": "user", "content": "x" * 1_201}],
            },
            {
                "message": "hello",
                "history": [{"role": "assistant", "content": "x" * 4_001}],
            },
        ):
            with self.subTest(payload=invalid):
                payload, error = validate_chat_payload(invalid)
                self.assertIsNone(payload)
                self.assertIsNotNone(error)

    def test_lead_requires_explicit_consent_and_valid_contact_data(self) -> None:
        lead = {
            "name": "  Avery Example  ",
            "email": "avery@example.com",
            "company": "Example Co",
            "message": "Please get in touch.",
            "consent": True,
            "website": "",
        }
        valid, error = validate_lead_payload(lead)
        self.assertIsNone(error)
        self.assertEqual(valid["name"], "Avery Example")

        for invalid in (
            {**lead, "consent": False},
            {**lead, "email": "not-an-email"},
            {**lead, "website": "spam"},
        ):
            with self.subTest(payload=invalid):
                valid, error = validate_lead_payload(invalid)
                self.assertIsNone(valid)
                self.assertIsNotNone(error)


class PostgresPersistenceTests(unittest.TestCase):
    def test_conversation_messages_append_as_jsonb_by_session_id(self) -> None:
        connection = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = connection
        session_id = uuid4()
        messages = [{"role": "user", "content": "Hello"}]

        with patch("backend.database._connection", return_value=context):
            database.append_conversation_messages(session_id, messages)

        query, parameters = connection.execute.call_args.args
        self.assertIn("conversation_sessions", query)
        self.assertIn("messages || EXCLUDED.messages", query)
        self.assertEqual(parameters, (session_id, json.dumps(messages, ensure_ascii=False)))

    def test_local_conversations_append_list_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = os.path.join(temporary_directory, "conversations.sqlite3")
            session_id = uuid4()
            first = [{"role": "user", "content": "Hello"}]
            second = [{"role": "assistant", "content": "Hi there"}]
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("POSTGRES_URL", None)
                os.environ.pop("DATABASE_URL", None)
                os.environ.pop("VERCEL", None)
                with patch("backend.database.LOCAL_DATABASE_PATH", database_path):
                    database.append_conversation_messages(session_id, first)
                    database.append_conversation_messages(session_id, second)
                    conversations = database.list_conversations()
                    self.assertEqual(len(conversations), 1)
                    self.assertEqual(conversations[0]["session_id"], str(session_id))
                    self.assertEqual(conversations[0]["messages"], first + second)
                    self.assertTrue(database.delete_conversation(session_id))
                    self.assertEqual(database.list_conversations(), [])

    def test_lead_values_are_inserted_as_parameters(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (
            UUID("c8a9d8bf-8ccf-4dcf-aa8d-81df872ab11a"),
        )
        context = MagicMock()
        context.__enter__.return_value = connection
        lead = {
            "name": "Avery",
            "email": "avery@example.com",
            "company": "Example Co",
            "message": "Hello",
        }

        with patch("backend.database._connection", return_value=context):
            lead_id = database.save_lead(lead)

        query, parameters = connection.execute.call_args.args
        self.assertIn("INSERT INTO visitor_leads", query)
        self.assertEqual(parameters, ("Avery", "avery@example.com", "Example Co", "Hello"))
        self.assertEqual(lead_id, "c8a9d8bf-8ccf-4dcf-aa8d-81df872ab11a")

    def test_dashboard_and_cleanup_queries_share_retention_days(self) -> None:
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = []
        context = MagicMock()
        context.__enter__.return_value = connection

        with patch("backend.database._connection", return_value=context):
            database.list_leads(90)
            database.purge_expired_leads(90)

        parameterized_calls = [
            call for call in connection.execute.call_args_list if len(call.args) > 1
        ]
        self.assertEqual(len(parameterized_calls), 2)
        for call in parameterized_calls:
            self.assertIn("(%s * INTERVAL '1 day')", call.args[0])
            self.assertEqual(call.args[1], (90,))


class LocalDatabaseTests(unittest.TestCase):
    def test_local_sqlite_persists_lists_and_deletes_leads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = os.path.join(temporary_directory, "leads.sqlite3")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("POSTGRES_URL", None)
                os.environ.pop("DATABASE_URL", None)
                os.environ.pop("VERCEL", None)
                with patch("backend.database.LOCAL_DATABASE_PATH", database_path):
                    lead = {
                        "name": "Avery",
                        "email": "avery@example.com",
                        "company": "Example Co",
                        "message": "Hello",
                    }
                    lead_id = database.save_lead(lead)
                    leads = database.list_leads(90)

                    self.assertEqual(len(leads), 1)
                    self.assertEqual(leads[0]["id"], lead_id)
                    self.assertEqual(leads[0]["name"], "Avery")
                    self.assertTrue(database.delete_lead(UUID(lead_id)))
                    self.assertEqual(database.list_leads(90), [])

    def test_vercel_requires_managed_postgres_instead_of_local_sqlite(self) -> None:
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=False):
            os.environ.pop("POSTGRES_URL", None)
            os.environ.pop("DATABASE_URL", None)
            with self.assertRaises(database.DatabaseUnavailable):
                database.list_leads(90)


class PortfolioAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ASGITestClient(app)
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        for key in (
            "OPENROUTER_API_KEY",
            "POSTGRES_URL",
            "DATABASE_URL",
            "ADMIN_PASSWORD",
            "ADMIN_SESSION_SECRET",
            "CRON_SECRET",
            "GITHUB_USERNAME",
        ):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self.environment.stop()

    def test_github_api_uses_optional_server_side_token(self) -> None:
        upstream_response = MagicMock()
        upstream_response.__enter__.return_value.read.return_value = b"[]"
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-read-only-token"}):
            with patch("backend.server.urlopen", return_value=upstream_response) as open_url:
                response = server._github_request("https://api.github.com/users/SubhamDas770/repos")
        self.assertEqual(response, [])
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-read-only-token")

    def test_readme_is_fetched_from_raw_github_without_rest_calls(self) -> None:
        upstream_response = MagicMock()
        upstream_response.__enter__.return_value.read.return_value = b"# Current README"
        with patch("backend.server.urlopen", return_value=upstream_response) as open_url:
            readme = server._github_readme("SubhamDas770", "example-repo", "main")
        self.assertEqual(readme, "# Current README")
        request = open_url.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://raw.githubusercontent.com/SubhamDas770/example-repo/main/README.md",
        )

    def test_projects_fall_back_to_public_profile_when_api_rate_limited(self) -> None:
        repositories = [
            {
                "name": "example-repo",
                "html_url": "https://github.com/SubhamDas770/example-repo",
                "default_branch": "main",
            }
        ]
        with patch.dict(os.environ, {"GITHUB_USERNAME": "SubhamDas770"}):
            with patch.object(server, "_projects_cache", None):
                with patch(
                    "backend.server._github_request",
                    side_effect=HTTPError(
                        "https://api.github.com/users/SubhamDas770/repos",
                        403,
                        "rate limit exceeded",
                        {},
                        io.BytesIO(),
                    ),
                ):
                    with patch.object(
                        server,
                        "_github_repository_listing",
                        return_value=repositories,
                    ) as profile_listing:
                        with patch.object(
                            server,
                            "_github_readme",
                            return_value="# Cached from public pages",
                        ):
                            response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["projects"],
            [
                {
                    "name": "example-repo",
                    "url": "https://github.com/SubhamDas770/example-repo",
                    "readme": "# Cached from public pages",
                }
            ],
        )
        profile_listing.assert_called_once_with("SubhamDas770")

    def test_serves_public_site_and_does_not_expose_private_backend_data(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Subham Das", response.text)

        for path in (
            "/backend/server.py",
            "/backend/data/facts.json",
            "/backend/data/linkedin.pdf",
            "/backend/data/style.txt",
            "/backend/.env.example",
            "/data/facts.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_chat_endpoint_rejects_invalid_and_oversized_requests(self) -> None:
        invalid = self.client.post("/api/chat", json={"message": " "})
        oversized = self.client.post("/api/chat", content=b"x" * 20_001, headers={"content-type": "application/json"})

        self.assertEqual(invalid.status_code, 400)
        self.assertIn("1,200 characters", invalid.json()["error"])
        self.assertEqual(oversized.status_code, 413)

    def test_chat_uses_openrouter_and_loaded_profile_context(self) -> None:
        upstream_body = {
            "choices": [{"message": {"content": "I built Dora AI."}}],
        }
        session_id = str(uuid4())
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "openai/gpt-4.1-mini"},
        ):
            with patch("backend.server.append_conversation_messages") as archive:
                with patch(
                    "backend.server.urlopen",
                    return_value=io.BytesIO(json.dumps(upstream_body).encode("utf-8")),
                ) as openrouter_request:
                    response = self.client.post(
                        "/api/chat",
                        json={
                            "message": "Tell me about your projects",
                            "session_id": session_id,
                            "server_instance_id": server.SERVER_INSTANCE_ID,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"reply": "I built Dora AI."})
        archive.assert_called_once_with(
            UUID(session_id),
            [
                {"role": "user", "content": "Tell me about your projects"},
                {"role": "assistant", "content": "I built Dora AI."},
            ],
        )
        sent_request = openrouter_request.call_args.args[0]
        self.assertEqual(sent_request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer test-key")
        sent_body = json.loads(sent_request.data)
        self.assertEqual(sent_body["model"], "openai/gpt-4.1-mini")
        instructions = sent_body["messages"][0]["content"]
        self.assertIn("Dora AI", instructions)
        self.assertIn("BCG - Data Science Job Simulation", instructions)
        self.assertIn("Speak in the first person as Subham", instructions)
        self.assertIn("Fidelity Investments", instructions)

    def test_chat_reports_missing_openrouter_configuration(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "session_id": str(uuid4()),
                "server_instance_id": server.SERVER_INSTANCE_ID,
            },
        )
        self.assertEqual(response.status_code, 503)

    def test_chat_requires_a_fresh_server_session(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "message": "Hello",
                "session_id": str(uuid4()),
                "server_instance_id": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_conversation_session_and_archive_endpoints(self) -> None:
        session_response = self.client.get("/api/conversations/session")
        self.assertEqual(session_response.status_code, 200)
        session = session_response.json()
        self.assertTrue(UUID(session["session_id"]))
        self.assertEqual(session["server_instance_id"], server.SERVER_INSTANCE_ID)

        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        with patch("backend.server.append_conversation_messages") as archive:
            response = self.client.post(
                f"/api/conversations/{session['session_id']}/messages",
                json={
                    "server_instance_id": server.SERVER_INSTANCE_ID,
                    "messages": messages,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"saved": True})
        archive.assert_called_once_with(UUID(session["session_id"]), messages)

    def test_admin_conversations_requires_login_and_returns_archived_sessions(self) -> None:
        unauthorized = self.client.get("/api/admin/conversations")
        self.assertEqual(unauthorized.status_code, 401)
        session_id = str(uuid4())
        with patch.dict(
            os.environ,
            {
                "ADMIN_PASSWORD": "a-long-test-password",
                "ADMIN_SESSION_SECRET": "s" * 48,
            },
        ):
            login = self.client.post(
                "/api/admin/login",
                json={"password": "a-long-test-password"},
            )
            self.assertEqual(login.status_code, 200)
            with patch(
                "backend.server.list_conversations",
                return_value=[
                    {
                        "session_id": session_id,
                        "messages": [{"role": "user", "content": "Hi"}],
                        "created_at": "2026-11-03T12:00:00",
                        "updated_at": "2026-11-03T12:00:00",
                    }
                ],
            ):
                response = self.client.get("/api/admin/conversations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["conversations"][0]["session_id"], session_id)

    def test_admin_conversation_deletion_requires_login(self) -> None:
        session_id = str(uuid4())
        unauthorized = self.client.request("DELETE", f"/api/admin/conversations/{session_id}")
        self.assertEqual(unauthorized.status_code, 401)

        with patch.dict(
            os.environ,
            {
                "ADMIN_PASSWORD": "a-long-test-password",
                "ADMIN_SESSION_SECRET": "s" * 48,
            },
        ):
            login = self.client.post(
                "/api/admin/login",
                json={"password": "a-long-test-password"},
            )
            self.assertEqual(login.status_code, 200)
            with patch("backend.server.delete_conversation", return_value=True) as delete:
                response = self.client.request(
                    "DELETE",
                    f"/api/admin/conversations/{session_id}",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Conversation deleted."})
        delete.assert_called_once_with(UUID(session_id))

    def test_lead_submission_requires_consent_before_database_access(self) -> None:
        response = self.client.post(
            "/api/leads",
            json={
                "name": "Avery",
                "email": "avery@example.com",
                "consent": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("consent", response.json()["error"].lower())

    def test_lead_submission_saves_only_valid_consented_data(self) -> None:
        with patch("backend.server.save_lead", return_value="lead-id") as save:
            response = self.client.post(
                "/api/leads",
                json={
                    "name": "Avery",
                    "email": "avery@example.com",
                    "company": "Example Co",
                    "message": "Contact me.",
                    "consent": True,
                    "website": "",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["id"], "lead-id")
        self.assertEqual(
            save.call_args.args[0],
            {
                "name": "Avery",
                "email": "avery@example.com",
                "company": "Example Co",
                "message": "Contact me.",
            },
        )

    def test_dashboard_requires_login_and_returns_leads_after_login(self) -> None:
        with patch("backend.server.list_leads", return_value=[]):
            unauthorized = self.client.get("/api/admin/leads")
            self.assertEqual(unauthorized.status_code, 401)

            with patch.dict(
                os.environ,
                {
                    "ADMIN_PASSWORD": "a-long-test-password",
                    "ADMIN_SESSION_SECRET": "s" * 48,
                },
            ):
                login = self.client.post(
                    "/api/admin/login",
                    json={"password": "a-long-test-password"},
                )
                self.assertEqual(login.status_code, 200)
                response = self.client.get("/api/admin/leads")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"leads": []})

    def test_admin_login_uses_password_updated_in_local_env_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text("ADMIN_PASSWORD=old-admin-password-value\n", encoding="utf-8")
            with patch.object(server, "ADMIN_ENV_FILE_PATH", env_path):
                with patch.object(server, "_admin_password_file_value", None):
                    with patch.dict(
                        os.environ,
                        {"ADMIN_PASSWORD": "", "ADMIN_SESSION_SECRET": "s" * 48},
                    ):
                        load_environment_file(env_path)
                        old_password_login = self.client.post(
                            "/api/admin/login",
                            json={"password": "old-admin-password-value"},
                        )
                        self.assertEqual(old_password_login.status_code, 200)

                        env_path.write_text(
                            "ADMIN_PASSWORD=new-admin-password-value\n",
                            encoding="utf-8",
                        )
                        stale_session_response = self.client.get("/api/admin/leads")
                        rejected_old_password = self.client.post(
                            "/api/admin/login",
                            json={"password": "old-admin-password-value"},
                        )
                        accepted_new_password = self.client.post(
                            "/api/admin/login",
                            json={"password": "new-admin-password-value"},
                        )

        self.assertEqual(stale_session_response.status_code, 401)
        self.assertEqual(rejected_old_password.status_code, 401)
        self.assertEqual(accepted_new_password.status_code, 200)

    def test_cleanup_requires_secret_and_uses_ninety_day_retention(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "cron-secret"}):
            unauthorized = self.client.get("/api/cron/cleanup")
            self.assertEqual(unauthorized.status_code, 401)
            with patch("backend.server.purge_expired_leads", return_value=3) as purge:
                response = self.client.get(
                    "/api/cron/cleanup",
                    headers={"Authorization": "Bearer cron-secret"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": 3})
        self.assertEqual(purge.call_args.args[0], 90)

    def test_projects_are_empty_until_a_username_is_configured(self) -> None:
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"configured": False, "projects": []})

    def test_startup_forces_a_github_project_refresh(self) -> None:
        with patch.dict(
            os.environ,
            {"GITHUB_USERNAME": "SubhamDas770", "VERCEL": ""},
        ):
            with patch("backend.server.load_github_projects", return_value=[]) as refresh:
                async def run_lifespan() -> None:
                    async with server.app_lifespan(app):
                        pass

                asyncio.run(run_lifespan())
        refresh.assert_called_once_with(force_refresh=True)

    def test_vercel_startup_does_not_wait_for_github_project_refresh(self) -> None:
        with patch.dict(
            os.environ,
            {"GITHUB_USERNAME": "SubhamDas770", "VERCEL": "1"},
        ):
            with patch("backend.server.load_github_projects") as refresh:
                async def run_lifespan() -> None:
                    async with server.app_lifespan(app):
                        pass

                asyncio.run(run_lifespan())
        refresh.assert_not_called()

    def test_github_refresh_cron_requires_auth_and_forces_refresh(self) -> None:
        with patch.dict(
            os.environ,
            {"CRON_SECRET": "cron-secret", "GITHUB_USERNAME": "SubhamDas770"},
        ):
            unauthorized = self.client.get("/api/cron/refresh-github")
            self.assertEqual(unauthorized.status_code, 401)
            with patch("backend.server.load_github_projects", return_value=[{}, {}]) as refresh:
                response = self.client.get(
                    "/api/cron/refresh-github",
                    headers={"Authorization": "Bearer cron-secret"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"refreshed": True, "count": 2})
        refresh.assert_called_once_with(force_refresh=True)

    def test_github_projects_use_two_day_cache_unless_forced(self) -> None:
        cached_projects = [{"name": "cached"}]
        with patch.dict(os.environ, {"GITHUB_USERNAME": "SubhamDas770"}):
            with patch.object(server, "_projects_cache", (100.0, "SubhamDas770", cached_projects)):
                with patch("backend.server.time.monotonic", return_value=101.0):
                    with patch("backend.server._github_request", return_value=[]) as github_request:
                        self.assertEqual(server.load_github_projects(), cached_projects)
                        github_request.assert_not_called()
                        self.assertEqual(
                            server.load_github_projects(force_refresh=True),
                            [],
                        )
                        github_request.assert_called_once()

    def test_github_project_cache_expires_after_two_days(self) -> None:
        with patch.dict(os.environ, {"GITHUB_USERNAME": "SubhamDas770"}):
            with patch.object(server, "_projects_cache", (100.0, "SubhamDas770", [{"name": "old"}])):
                with patch(
                    "backend.server.time.monotonic",
                    return_value=100.0 + server.GITHUB_PROJECTS_REFRESH_INTERVAL_SECONDS,
                ):
                    with patch("backend.server._github_request", return_value=[]) as github_request:
                        self.assertEqual(server.load_github_projects(), [])
                        github_request.assert_called_once()

    def test_projects_include_all_repositories_even_without_a_readme(self) -> None:
        repository_names = [
            "Cybersecurity-Analyzer",
            "digital-twin",
            "FINRAG",
            "ai-reviewer-test",
            "multi-agent-trading-assistant",
            "ML_project-",
            "BCG-X-Project",
            "machinelearning",
            "E-commerce-DATA-Analysis",
            "Credit_card_Customer_Weeky_Report",
            "SubhamDas770",
            "CNN-Dog-Cat-image-classification",
            "Capstone-project",
        ]
        repositories = [
            {
                "name": name,
                "html_url": f"https://github.com/SubhamDas770/{name}",
                "fork": name == "ML_project-",
                "default_branch": "main",
            }
            for name in repository_names
        ]

        def github_response(url: str) -> dict[str, object] | list[object]:
            self.assertIn("per_page=100", url)
            return repositories

        def readme_response(username: str, repository: str, branch: str) -> str:
            self.assertEqual(username, "SubhamDas770")
            self.assertEqual(branch, "main")
            if repository == "BCG-X-Project":
                return ""
            if repository == "FINRAG":
                return "# " + ("x" * 9_000)
            return "# Repository README"

        with patch.dict(os.environ, {"GITHUB_USERNAME": "SubhamDas770"}):
            with patch.object(server, "_projects_cache", None):
                with patch.object(server, "_github_request", side_effect=github_response):
                    with patch.object(server, "_github_readme", side_effect=readme_response):
                        response = self.client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertIsInstance(result, dict)
        projects = result["projects"]
        self.assertEqual(len(projects), 13)
        self.assertEqual([project["name"] for project in projects], repository_names)
        self.assertEqual(projects[6]["readme"], "")
        self.assertEqual(projects[5]["name"], "ML_project-")
        self.assertEqual(projects[0]["readme"], "# Repository README")
        self.assertEqual(len(projects[2]["readme"]), 9_002)


if __name__ == "__main__":
    unittest.main()
