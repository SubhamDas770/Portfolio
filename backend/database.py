from __future__ import annotations

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator
from uuid import UUID, uuid4


BACKEND_DIRECTORY = Path(__file__).resolve().parent
LOCAL_DATABASE_PATH = Path(
    os.environ.get("PORTFOLIO_DATABASE_PATH")
    or BACKEND_DIRECTORY / "visitor_leads.sqlite3"
).expanduser()

LEADS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS visitor_leads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

LOCAL_LEADS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS visitor_leads (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    consented_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id UUID PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

LOCAL_CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_sessions (
    id TEXT PRIMARY KEY,
    messages TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class DatabaseUnavailable(RuntimeError):
    """Raised when the managed Postgres store is missing or unreachable."""


@contextmanager
def _connection() -> Generator[Any, None, None]:
    database_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        if os.environ.get("VERCEL") == "1":
            raise DatabaseUnavailable("Postgres is not configured.")
        connection = sqlite3.connect(LOCAL_DATABASE_PATH, timeout=5)
        try:
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise DatabaseUnavailable("The local contact database is unavailable.") from error
        finally:
            connection.close()
        return

    try:
        import psycopg
    except ImportError as error:
        raise DatabaseUnavailable("The Postgres driver is not installed.") from error
    try:
        with psycopg.connect(database_url, connect_timeout=5) as connection:
            yield connection
    except psycopg.Error as error:
        raise DatabaseUnavailable("Postgres is unavailable.") from error


def _ensure_schema(connection: Any) -> None:
    if isinstance(connection, sqlite3.Connection):
        connection.execute(LOCAL_LEADS_TABLE_SQL)
        connection.execute(LOCAL_CONVERSATIONS_TABLE_SQL)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS visitor_leads_created_at_idx "
            "ON visitor_leads (created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS conversation_sessions_updated_at_idx "
            "ON conversation_sessions (updated_at DESC)"
        )
        return
    connection.execute(LEADS_TABLE_SQL)
    connection.execute(CONVERSATIONS_TABLE_SQL)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS visitor_leads_created_at_idx "
        "ON visitor_leads (created_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS conversation_sessions_updated_at_idx "
        "ON conversation_sessions (updated_at DESC)"
    )


def append_conversation_messages(
    session_id: UUID,
    messages: list[dict[str, str]],
) -> None:
    serialized_messages = json.dumps(messages, ensure_ascii=False)
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT messages FROM conversation_sessions WHERE id = ?",
                (str(session_id),),
            ).fetchone()
            existing_messages = json.loads(row[0]) if row else []
            existing_messages.extend(messages)
            connection.execute(
                """
                INSERT INTO conversation_sessions (id, messages)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    messages = excluded.messages,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (str(session_id), json.dumps(existing_messages, ensure_ascii=False)),
            )
            return
        connection.execute(
            """
            INSERT INTO conversation_sessions (id, messages)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                messages = conversation_sessions.messages || EXCLUDED.messages,
                updated_at = NOW()
            """,
            (session_id, serialized_messages),
        )


def list_conversations(limit: int = 200) -> list[dict[str, Any]]:
    with _connection() as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            """
            SELECT id, messages, created_at, updated_at
            FROM conversation_sessions
            ORDER BY updated_at DESC
            LIMIT %s
            """ if not isinstance(connection, sqlite3.Connection) else """
            SELECT id, messages, created_at, updated_at
            FROM conversation_sessions
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conversations: list[dict[str, Any]] = []
    for row in rows:
        messages = row[1] if isinstance(row[1], list) else json.loads(row[1])
        conversations.append(
            {
                "session_id": str(row[0]),
                "messages": messages,
                "created_at": row[2].isoformat() if isinstance(row[2], datetime) else row[2],
                "updated_at": row[3].isoformat() if isinstance(row[3], datetime) else row[3],
            }
        )
    return conversations


def delete_conversation(session_id: UUID) -> bool:
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            cursor = connection.execute(
                "DELETE FROM conversation_sessions WHERE id = ?",
                (str(session_id),),
            )
            return cursor.rowcount > 0
        row = connection.execute(
            "DELETE FROM conversation_sessions WHERE id = %s RETURNING id",
            (session_id,),
        ).fetchone()
        return row is not None


def save_lead(lead: dict[str, str]) -> str:
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            lead_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO visitor_leads (id, name, email, company, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (lead_id, lead["name"], lead["email"], lead["company"], lead["message"]),
            )
            return lead_id
        row = connection.execute(
            """
            INSERT INTO visitor_leads (name, email, company, message)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (lead["name"], lead["email"], lead["company"], lead["message"]),
        ).fetchone()
        return str(row[0])


def list_leads(retention_days: int) -> list[dict[str, str]]:
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            rows = connection.execute(
                """
                SELECT id, name, email, company, message, created_at
                FROM visitor_leads
                WHERE created_at >= datetime('now', ?)
                ORDER BY created_at DESC
                """,
                (f"-{retention_days} days",),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id::text, name, email, company, message, created_at
                FROM visitor_leads
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                ORDER BY created_at DESC
                """,
                (retention_days,),
            ).fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "company": row[3],
            "message": row[4],
            "created_at": row[5].isoformat() if isinstance(row[5], datetime) else row[5],
        }
        for row in rows
    ]


def delete_lead(lead_id: UUID) -> bool:
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            cursor = connection.execute(
                "DELETE FROM visitor_leads WHERE id = ?",
                (str(lead_id),),
            )
            return cursor.rowcount > 0
        row = connection.execute(
            "DELETE FROM visitor_leads WHERE id = %s RETURNING id",
            (lead_id,),
        ).fetchone()
        return row is not None


def purge_expired_leads(retention_days: int) -> int:
    with _connection() as connection:
        _ensure_schema(connection)
        if isinstance(connection, sqlite3.Connection):
            cursor = connection.execute(
                """
                DELETE FROM visitor_leads
                WHERE created_at < datetime('now', ?)
                """,
                (f"-{retention_days} days",),
            )
            return cursor.rowcount
        rows = connection.execute(
            """
            DELETE FROM visitor_leads
            WHERE created_at < NOW() - (%s * INTERVAL '1 day')
            RETURNING id
            """,
            (retention_days,),
        ).fetchall()
        return len(rows)
