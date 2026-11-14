from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from fastapi import FastAPI, Request as FastAPIRequest
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

if __package__:
    from .context import prompt as build_digital_twin_prompt
    from .database import (
        DatabaseUnavailable,
            append_conversation_messages,
            delete_conversation,
            delete_lead,
            list_conversations,
            list_leads,
            purge_expired_leads,
            save_lead,
    )
else:
    from context import prompt as build_digital_twin_prompt
    from database import (
        DatabaseUnavailable,
        append_conversation_messages,
        delete_conversation,
        delete_lead,
        list_conversations,
        list_leads,
        purge_expired_leads,
        save_lead,
    )


BACKEND_DIRECTORY = Path(__file__).resolve().parent
SITE_DIRECTORY = BACKEND_DIRECTORY.parent
PUBLIC_DIRECTORY = SITE_DIRECTORY / "public"
ADMIN_ENV_FILE_PATH = BACKEND_DIRECTORY / ".env"
MAXIMUM_REQUEST_BYTES = 20_000
MAXIMUM_MESSAGE_LENGTH = 1_200
MAXIMUM_HISTORY_MESSAGE_LENGTH = 4_000
MAXIMUM_GITHUB_README_LENGTH = 20_000
GITHUB_PROJECTS_REFRESH_INTERVAL_SECONDS = 2 * 24 * 60 * 60
LEAD_RETENTION_DAYS = 90
RATE_LIMIT_WINDOW_SECONDS = 10 * 60
RATE_LIMIT_REQUESTS = 15
ADMIN_COOKIE_NAME = "portfolio_admin"
ADMIN_COOKIE_MAX_AGE = 8 * 60 * 60
def create_server_instance_id() -> str:
    if os.environ.get("VERCEL") == "1":
        deployment_url = os.environ.get("VERCEL_URL", "").strip()
        if deployment_url:
            return deployment_url
    return str(uuid4())


SERVER_INSTANCE_ID = create_server_instance_id()

_rate_lock = threading.Lock()
_recent_requests: dict[str, deque[float]] = defaultdict(deque)
_projects_cache: tuple[float, str, list[dict[str, str]]] | None = None
_projects_cache_lock = threading.Lock()
_admin_password_file_value: str | None = None


@asynccontextmanager
async def app_lifespan(_: FastAPI) -> AsyncIterator[None]:
    refresh_task: asyncio.Task[None] | None = None
    if os.environ.get("GITHUB_USERNAME", "").strip():
        try:
            await asyncio.to_thread(load_github_projects, force_refresh=True)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logging.exception("GitHub project data could not be refreshed at startup.")
        if os.environ.get("VERCEL") != "1":
            refresh_task = asyncio.create_task(refresh_github_projects_periodically())
    try:
        yield
    finally:
        if refresh_task:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass


async def refresh_github_projects_periodically() -> None:
    while True:
        await asyncio.sleep(GITHUB_PROJECTS_REFRESH_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(load_github_projects, force_refresh=True)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logging.exception("Scheduled GitHub project refresh failed.")


app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=app_lifespan,
)


def read_environment_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}

    values: dict[str, str] = {}
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_environment_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting existing environment."""
    global _admin_password_file_value
    values = read_environment_file(path)
    for key, value in values.items():
        if not os.environ.get(key):
            os.environ[key] = value
    if path == ADMIN_ENV_FILE_PATH:
        _admin_password_file_value = values.get("ADMIN_PASSWORD")


def refresh_local_admin_password() -> None:
    global _admin_password_file_value
    if os.environ.get("VERCEL") == "1":
        return
    password = read_environment_file(ADMIN_ENV_FILE_PATH).get("ADMIN_PASSWORD")
    if password == _admin_password_file_value:
        return
    if password is None:
        os.environ.pop("ADMIN_PASSWORD", None)
    else:
        os.environ["ADMIN_PASSWORD"] = password
    _admin_password_file_value = password


def validate_chat_payload(payload: object) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "Send a JSON object with a message."

    message = payload.get("message")
    if not isinstance(message, str):
        return None, "Enter a message of up to 1,200 characters."
    message = message.strip()
    if not message or len(message) > MAXIMUM_MESSAGE_LENGTH:
        return None, "Enter a message of up to 1,200 characters."

    raw_history = payload.get("history", [])
    if not isinstance(raw_history, list):
        return None, "Conversation history must be a list."

    history: list[dict[str, str]] = []
    for item in raw_history[-12:]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or item["role"] not in {"user", "assistant"}
            or not isinstance(item.get("content"), str)
            or len(item["content"])
            > (
                MAXIMUM_MESSAGE_LENGTH
                if item["role"] == "user"
                else MAXIMUM_HISTORY_MESSAGE_LENGTH
            )
        ):
            return None, "Conversation history contains an invalid message."
        history.append({"role": item["role"], "content": item["content"]})

    return {"message": message, "history": history}, None


def validate_conversation_session(payload: object) -> tuple[UUID | None, str | None]:
    if not isinstance(payload, dict):
        return None, "A conversation session is required."
    try:
        session_id = UUID(str(payload.get("session_id", "")))
    except (ValueError, TypeError, AttributeError):
        return None, "Refresh the page to start a new conversation session."
    if payload.get("server_instance_id") != SERVER_INSTANCE_ID:
        return None, "The server restarted. Start a fresh conversation and try again."
    return session_id, None


def validate_conversation_messages(payload: object) -> tuple[list[dict[str, str]] | None, str | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return None, "Send conversation messages to archive."
    raw_messages = payload["messages"]
    if not raw_messages or len(raw_messages) > 2:
        return None, "Archive one or two messages at a time."
    messages: list[dict[str, str]] = []
    for item in raw_messages:
        if (
            not isinstance(item, dict)
            or item.get("role") not in {"user", "assistant"}
            or not isinstance(item.get("content"), str)
        ):
            return None, "Conversation messages must have a valid role and text."
        content = item["content"].strip()
        limit = MAXIMUM_MESSAGE_LENGTH if item["role"] == "user" else MAXIMUM_HISTORY_MESSAGE_LENGTH
        if not content or len(content) > limit:
            return None, "A conversation message is empty or too long."
        messages.append({"role": item["role"], "content": content})
    return messages, None


def is_rate_limited(address: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        requests = _recent_requests[address]
        while requests and requests[0] <= cutoff:
            requests.popleft()
        if len(requests) >= RATE_LIMIT_REQUESTS:
            return True
        requests.append(now)
        return False


async def parse_json_request(
    request: FastAPIRequest, maximum_bytes: int = MAXIMUM_REQUEST_BYTES
) -> tuple[object | None, JSONResponse | None]:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        return None, JSONResponse({"error": "Send the request as JSON."}, status_code=415)

    content_length_header = request.headers.get("content-length")
    if content_length_header is not None:
        try:
            content_length = int(content_length_header)
        except ValueError:
            return None, JSONResponse({"error": "Send a valid Content-Length header."}, status_code=400)
        if content_length < 0:
            return None, JSONResponse({"error": "Request length cannot be negative."}, status_code=400)
        if content_length > maximum_bytes:
            return None, JSONResponse({"error": "Request body is too large."}, status_code=413)

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum_bytes:
            return None, JSONResponse({"error": "Request body is too large."}, status_code=413)
        body.extend(chunk)
    try:
        return json.loads(body), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JSONResponse({"error": "Send a valid JSON request."}, status_code=400)


def request_chat_reply(payload: dict[str, Any]) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key == "replace_with_your_openrouter_api_key":
        raise RuntimeError("The OpenRouter chat service is not configured.")

    messages = [
        {"role": "system", "content": build_digital_twin_prompt()},
        *payload["history"],
        {"role": "user", "content": payload["message"]},
    ]
    request_body = json.dumps(
        {
            "model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
            "messages": messages,
            "max_tokens": 350,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("PUBLIC_SITE_URL", "http://localhost:3000"),
        "X-Title": "Subham Das Portfolio Digital Twin",
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=request_body,
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as upstream:
            result = json.loads(upstream.read())
    except HTTPError as error:
        logging.error("OpenRouter request failed with status %s.", error.code)
        raise RuntimeError("The AI service could not answer right now.") from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        logging.error("OpenRouter request could not be completed.")
        raise RuntimeError("The AI service could not be reached.") from error

    if not isinstance(result, dict):
        raise RuntimeError("The AI service returned an invalid answer.")
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        reply = content.strip()
    elif isinstance(content, list):
        reply = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ).strip()
    else:
        reply = ""
    if not reply:
        logging.error("OpenRouter returned no text for the chat response.")
        raise RuntimeError("The AI service returned an empty answer.")
    return reply


def validate_lead_payload(payload: object) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "Send a valid contact form."
    if payload.get("website", ""):
        return None, "Unable to accept this submission."
    if payload.get("consent") is not True:
        return None, "Please confirm consent before sharing your details."

    name = payload.get("name")
    email = payload.get("email")
    company = payload.get("company", "")
    message = payload.get("message", "")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        return None, "Enter your name (up to 120 characters)."
    if not isinstance(email, str) or len(email.strip()) > 254:
        return None, "Enter a valid email address."
    email = email.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, "Enter a valid email address."
    if not isinstance(company, str) or not isinstance(message, str):
        return None, "Company and message must be text."
    if len(company.strip()) > 120 or len(message.strip()) > 800:
        return None, "Company or message is too long."
    return {
        "name": name.strip(),
        "email": email,
        "company": company.strip(),
        "message": message.strip(),
    }, None


def session_cookie_value() -> str:
    secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if len(secret) < 32 or len(password) < 16:
        raise RuntimeError("Admin session signing is not configured.")
    expires_at = int(time.time()) + ADMIN_COOKIE_MAX_AGE
    payload = str(expires_at)
    signing_key = hmac.new(
        secret.encode("utf-8"),
        password.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = hmac.new(signing_key, payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def is_admin_authenticated(cookie: str | None) -> bool:
    refresh_local_admin_password()
    secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not cookie or len(secret) < 32 or len(password) < 16:
        return False
    try:
        expires_text, supplied_signature = cookie.split(".", 1)
        expires_at = int(expires_text)
    except (ValueError, TypeError):
        return False
    if expires_at <= int(time.time()):
        return False
    signing_key = hmac.new(
        secret.encode("utf-8"),
        password.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_signature = hmac.new(
        signing_key, expires_text.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied_signature, expected_signature)


def _github_request(url: str) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SubhamDasPortfolio",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        headers=headers,
    )
    with urlopen(request, timeout=12) as response:
        result = json.loads(response.read())
    if not isinstance(result, (dict, list)):
        raise ValueError("GitHub returned an unexpected response.")
    return result


def _github_readme(username: str, repository: str, branch: str) -> str:
    filenames = (
        "README.md",
        "Readme.md",
        "README",
        "readme.md",
        "readme",
        "README.MD",
        "Readme.MD",
        "README.rst",
        "Readme.rst",
        "README.txt",
        "README.markdown",
        "Readme.markdown",
    )
    for filename in filenames:
        url = (
            "https://raw.githubusercontent.com/"
            f"{quote(username, safe='')}/{quote(repository, safe='')}/"
            f"{quote(branch, safe='/')}/{filename}"
        )
        request = Request(url, headers={"User-Agent": "SubhamDasPortfolio"})
        try:
            with urlopen(request, timeout=12) as response:
                content = response.read(MAXIMUM_GITHUB_README_LENGTH * 4)
            return content.decode("utf-8", errors="replace")[:MAXIMUM_GITHUB_README_LENGTH]
        except HTTPError as error:
            if error.code != 404:
                raise
            error.close()
    return ""


def _github_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "SubhamDasPortfolio"})
    with urlopen(request, timeout=12) as response:
        return response.read(2_000_000).decode("utf-8", errors="replace")


class _RepositoryLinkParser(HTMLParser):
    def __init__(self, username: str) -> None:
        super().__init__()
        self.username = username.casefold()
        self.repositories: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        if attributes.get("itemprop") != "name codeRepository":
            return
        href = attributes.get("href")
        if not isinstance(href, str):
            return
        parts = href.strip("/").split("/")
        if len(parts) == 2 and parts[0].casefold() == self.username:
            self.repositories.add(parts[1])


def _github_repository_listing(username: str) -> list[dict[str, str]]:
    profile_url = f"https://github.com/{quote(username, safe='')}?tab=repositories&per_page=100"
    parser = _RepositoryLinkParser(username)
    parser.feed(_github_html(profile_url))
    repositories: list[dict[str, str]] = []
    for name in sorted(parser.repositories, key=str.casefold):
        repository_url = (
            f"https://github.com/{quote(username, safe='')}/{quote(name, safe='')}"
        )
        repository_page = _github_html(repository_url)
        branch_match = re.search(r'"defaultBranch"\s*:\s*"([^"]+)"', repository_page)
        if not branch_match:
            raise ValueError(f"GitHub did not expose a default branch for {name}.")
        repositories.append(
            {
                "name": name,
                "html_url": repository_url,
                "default_branch": branch_match.group(1),
            }
        )
    return repositories


def load_github_projects(*, force_refresh: bool = False) -> list[dict[str, str]]:
    global _projects_cache
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    if not username:
        return []
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
        raise ValueError("GITHUB_USERNAME is not a valid GitHub username.")

    now = time.monotonic()
    with _projects_cache_lock:
        if (
            not force_refresh
            and _projects_cache
            and _projects_cache[1] == username
            and now - _projects_cache[0] < GITHUB_PROJECTS_REFRESH_INTERVAL_SECONDS
        ):
            return _projects_cache[2]

    repositories: list[Any] = []
    page = 1
    try:
        while True:
            page_repositories = _github_request(
                f"https://api.github.com/users/{quote(username, safe='')}/repos"
                f"?sort=updated&per_page=100&type=owner&page={page}"
            )
            if not isinstance(page_repositories, list):
                raise ValueError("GitHub returned an unexpected repository list.")
            repositories.extend(page_repositories)
            if len(page_repositories) < 100:
                break
            page += 1
    except HTTPError as error:
        if error.code not in {403, 429}:
            raise
        error.close()
        logging.warning("GitHub API rate limit reached; falling back to public profile pages.")
        repositories = _github_repository_listing(username)

    projects: list[dict[str, str]] = []
    for repository in repositories:
        if not isinstance(repository, dict):
            continue
        name = repository.get("name")
        html_url = repository.get("html_url")
        default_branch = repository.get("default_branch")
        if not all(isinstance(value, str) for value in (name, html_url, default_branch)):
            continue
        readme_text = _github_readme(username, name, default_branch)
        projects.append(
            {
                "name": name,
                "url": html_url,
                "readme": readme_text[:MAXIMUM_GITHUB_README_LENGTH],
            }
        )
    with _projects_cache_lock:
        _projects_cache = (time.monotonic(), username, projects)
    return projects


@app.middleware("http")
async def add_security_headers(request: FastAPIRequest, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/admin", include_in_schema=False)
async def admin_dashboard() -> RedirectResponse:
    return RedirectResponse("/admin.html?v=dashboard-v2", status_code=307)


@app.get("/api/conversations/session")
async def create_conversation_session() -> JSONResponse:
    return JSONResponse(
        {
            "session_id": str(uuid4()),
            "server_instance_id": SERVER_INSTANCE_ID,
        }
    )


@app.get("/api/server-instance")
async def server_instance() -> JSONResponse:
    return JSONResponse({"server_instance_id": SERVER_INSTANCE_ID})


@app.post("/api/conversations/{session_id}/messages")
async def archive_conversation_messages(
    session_id: str,
    request: FastAPIRequest,
) -> JSONResponse:
    if request.client and is_rate_limited(request.client.host):
        return JSONResponse(
            {"error": "Too many messages. Please try again in a few minutes."},
            status_code=429,
        )
    payload, error_response = await parse_json_request(request, maximum_bytes=10_000)
    if error_response:
        return error_response
    try:
        parsed_session_id = UUID(session_id)
    except ValueError:
        return JSONResponse({"error": "Invalid conversation session."}, status_code=400)
    if not isinstance(payload, dict) or payload.get("server_instance_id") != SERVER_INSTANCE_ID:
        return JSONResponse(
            {"error": "The server restarted. Start a fresh conversation and try again."},
            status_code=409,
        )
    messages, validation_error = validate_conversation_messages(payload)
    if validation_error:
        return JSONResponse({"error": validation_error}, status_code=400)
    if messages is None:
        return JSONResponse({"error": "Conversation messages are invalid."}, status_code=400)
    try:
        await asyncio.to_thread(append_conversation_messages, parsed_session_id, messages)
    except DatabaseUnavailable:
        logging.exception("Conversation transcript could not be archived.")
        return JSONResponse(
            {"error": "The conversation archive is temporarily unavailable."},
            status_code=503,
        )
    return JSONResponse({"saved": True})


@app.post("/api/chat")
async def chat(request: FastAPIRequest) -> JSONResponse:
    if request.client and is_rate_limited(request.client.host):
        return JSONResponse(
            {"error": "Too many messages. Please try again in a few minutes."},
            status_code=429,
        )
    payload, error_response = await parse_json_request(request)
    if error_response:
        return error_response
    clean_payload, validation_error = validate_chat_payload(payload)
    if validation_error:
        return JSONResponse({"error": validation_error}, status_code=400)
    if clean_payload is None:
        return JSONResponse({"error": "Send a valid message."}, status_code=400)
    if isinstance(payload, dict) and payload.get("server_instance_id") != SERVER_INSTANCE_ID:
        return JSONResponse(
            {"error": "The server restarted. Start a fresh conversation and try again."},
            status_code=409,
        )
    session_id, session_error = validate_conversation_session(payload)
    if session_error:
        return JSONResponse({"error": session_error}, status_code=400)
    if session_id is None:
        return JSONResponse({"error": "Conversation session is invalid."}, status_code=400)
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key == "replace_with_your_openrouter_api_key":
        return JSONResponse(
            {"error": "The OpenRouter chat service is not configured."},
            status_code=503,
        )
    try:
        reply = await asyncio.to_thread(request_chat_reply, clean_payload)
    except RuntimeError as error:
        return JSONResponse({"error": str(error)}, status_code=502)
    try:
        await asyncio.to_thread(
            append_conversation_messages,
            session_id,
            [
                {"role": "user", "content": clean_payload["message"]},
                {"role": "assistant", "content": reply},
            ],
        )
    except DatabaseUnavailable:
        logging.exception("Chat response could not be archived.")
        return JSONResponse(
            {"error": "The conversation archive is temporarily unavailable; the response was not delivered."},
            status_code=503,
        )
    return JSONResponse({"reply": reply})


@app.post("/api/leads", status_code=201)
async def submit_lead(request: FastAPIRequest) -> JSONResponse:
    payload, error_response = await parse_json_request(request, maximum_bytes=10_000)
    if error_response:
        return error_response
    lead, validation_error = validate_lead_payload(payload)
    if validation_error:
        return JSONResponse({"error": validation_error}, status_code=400)
    if lead is None:
        return JSONResponse({"error": "Send a valid contact form."}, status_code=400)
    try:
        lead_id = await asyncio.to_thread(save_lead, lead)
    except DatabaseUnavailable:
        logging.error("Visitor lead could not be stored because Postgres is unavailable.")
        return JSONResponse(
            {"error": "The contact form is temporarily unavailable. Please email Subham directly."},
            status_code=503,
        )
    return JSONResponse(
        {"message": "Thanks. Your details have been shared with Subham.", "id": lead_id},
        status_code=201,
    )


@app.post("/api/admin/login")
async def admin_login(request: FastAPIRequest) -> Response:
    payload, error_response = await parse_json_request(request, maximum_bytes=2_000)
    if error_response:
        return error_response
    refresh_local_admin_password()
    password = payload.get("password") if isinstance(payload, dict) else None
    expected_password = os.environ.get("ADMIN_PASSWORD", "")
    secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    if len(expected_password) < 16 or len(secret) < 32:
        return JSONResponse({"error": "Admin access is not configured."}, status_code=503)
    if not isinstance(password, str) or not hmac.compare_digest(
        password.encode("utf-8"), expected_password.encode("utf-8")
    ):
        return JSONResponse({"error": "Incorrect password."}, status_code=401)

    response = JSONResponse({"message": "Signed in."})
    forwarded_protocol = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        session_cookie_value(),
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        secure=forwarded_protocol == "https",
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/admin/logout")
async def admin_logout() -> Response:
    response = JSONResponse({"message": "Signed out."})
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


@app.get("/api/admin/leads")
async def admin_leads(request: FastAPIRequest) -> JSONResponse:
    if not is_admin_authenticated(request.cookies.get(ADMIN_COOKIE_NAME)):
        return JSONResponse({"error": "Sign in to view submissions."}, status_code=401)
    try:
        leads = await asyncio.to_thread(list_leads, LEAD_RETENTION_DAYS)
    except DatabaseUnavailable:
        logging.error("Visitor leads could not be loaded because Postgres is unavailable.")
        return JSONResponse({"error": "The contact database is temporarily unavailable."}, status_code=503)
    return JSONResponse({"leads": leads})


@app.get("/api/admin/conversations")
async def admin_conversations(request: FastAPIRequest) -> JSONResponse:
    if not is_admin_authenticated(request.cookies.get(ADMIN_COOKIE_NAME)):
        return JSONResponse({"error": "Sign in to view conversations."}, status_code=401)
    try:
        conversations = await asyncio.to_thread(list_conversations)
    except DatabaseUnavailable:
        logging.exception("Archived conversations could not be loaded.")
        return JSONResponse({"error": "The conversation archive is temporarily unavailable."}, status_code=503)
    return JSONResponse({"conversations": conversations})


@app.delete("/api/admin/conversations/{session_id}")
async def admin_delete_conversation(session_id: str, request: FastAPIRequest) -> JSONResponse:
    if not is_admin_authenticated(request.cookies.get(ADMIN_COOKIE_NAME)):
        return JSONResponse({"error": "Sign in to manage conversations."}, status_code=401)
    try:
        parsed_session_id = UUID(session_id)
    except ValueError:
        return JSONResponse({"error": "Invalid conversation session."}, status_code=400)
    try:
        deleted = await asyncio.to_thread(delete_conversation, parsed_session_id)
    except DatabaseUnavailable:
        logging.exception("Archived conversation could not be deleted.")
        return JSONResponse({"error": "The conversation archive is temporarily unavailable."}, status_code=503)
    if not deleted:
        return JSONResponse({"error": "Conversation not found."}, status_code=404)
    return JSONResponse({"message": "Conversation deleted."})


@app.delete("/api/admin/leads/{lead_id}")
async def admin_delete_lead(lead_id: str, request: FastAPIRequest) -> JSONResponse:
    if not is_admin_authenticated(request.cookies.get(ADMIN_COOKIE_NAME)):
        return JSONResponse({"error": "Sign in to manage submissions."}, status_code=401)
    try:
        parsed_id = UUID(lead_id)
    except ValueError:
        return JSONResponse({"error": "Invalid submission ID."}, status_code=400)
    try:
        deleted = await asyncio.to_thread(delete_lead, parsed_id)
    except DatabaseUnavailable:
        logging.error("Visitor lead could not be deleted because Postgres is unavailable.")
        return JSONResponse({"error": "The contact database is temporarily unavailable."}, status_code=503)
    if not deleted:
        return JSONResponse({"error": "Submission not found."}, status_code=404)
    return JSONResponse({"message": "Submission deleted."})


@app.get("/api/cron/cleanup")
async def cleanup_expired_leads(request: FastAPIRequest) -> JSONResponse:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return JSONResponse({"error": "Retention job is not configured."}, status_code=503)
    expected = f"Bearer {secret}"
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"error": "Unauthorized."}, status_code=401)
    try:
        deleted_count = await asyncio.to_thread(purge_expired_leads, LEAD_RETENTION_DAYS)
    except DatabaseUnavailable:
        logging.error("Expired visitor leads could not be purged because Postgres is unavailable.")
        return JSONResponse({"error": "The contact database is temporarily unavailable."}, status_code=503)
    return JSONResponse({"deleted": deleted_count})


@app.get("/api/cron/refresh-github")
async def refresh_github_projects(request: FastAPIRequest) -> JSONResponse:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        return JSONResponse({"error": "GitHub refresh job is not configured."}, status_code=503)
    expected = f"Bearer {secret}"
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"error": "Unauthorized."}, status_code=401)
    if not os.environ.get("GITHUB_USERNAME", "").strip():
        return JSONResponse({"error": "GitHub projects are not configured."}, status_code=503)
    try:
        project_list = await asyncio.to_thread(load_github_projects, force_refresh=True)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logging.exception("GitHub project data could not be refreshed by the scheduled job.")
        return JSONResponse({"error": "GitHub projects are temporarily unavailable."}, status_code=502)
    return JSONResponse({"refreshed": True, "count": len(project_list)})


@app.get("/api/projects")
async def projects() -> JSONResponse:
    if not os.environ.get("GITHUB_USERNAME", "").strip():
        return JSONResponse({"configured": False, "projects": []})
    try:
        project_list = await asyncio.to_thread(load_github_projects)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        logging.exception("GitHub project data could not be loaded.")
        return JSONResponse({"error": "GitHub projects are temporarily unavailable."}, status_code=502)
    return JSONResponse({"configured": True, "projects": project_list})


if os.environ.get("VERCEL") != "1":
    app.mount("/", StaticFiles(directory=PUBLIC_DIRECTORY, html=True), name="public")


def main() -> None:
    load_environment_file(ADMIN_ENV_FILE_PATH)
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run(app, host=host, port=port)


load_environment_file(BACKEND_DIRECTORY / ".env")

if __name__ == "__main__":
    main()
