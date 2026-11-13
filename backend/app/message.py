import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.thread import apply as apply_thread

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "message.sql"

MessageRow = tuple[
    uuid.UUID,
    uuid.UUID,
    uuid.UUID,
    uuid.UUID | None,
    str,
    str,
    str,
    datetime,
]


def apply(connection: psycopg.Connection) -> None:
    apply_thread(connection)
    connection.execute(_MIGRATION.read_text())


def insert_message(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    role: str,
    kind: str,
    body: str,
    run_id: uuid.UUID | None = None,
) -> MessageRow | None:
    apply(connection)
    owned = connection.execute(
        """
        SELECT id
        FROM thread
        WHERE id = %s
          AND account_id = %s
        """,
        (thread_id, account_id),
    ).fetchone()
    if owned is None:
        return None
    created = connection.execute(
        """
        INSERT INTO message (account_id, thread_id, role, kind, body, run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, account_id, thread_id, run_id, role, kind, body, created_at
        """,
        (account_id, thread_id, role, kind, body, run_id),
    ).fetchone()
    if created is None:
        raise RuntimeError("message was not stored")
    return created


def list_messages(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> list[MessageRow] | None:
    apply(connection)
    owned = connection.execute(
        """
        SELECT id
        FROM thread
        WHERE id = %s
          AND account_id = %s
        """,
        (thread_id, account_id),
    ).fetchone()
    if owned is None:
        return None
    rows = connection.execute(
        """
        SELECT id, account_id, thread_id, run_id, role, kind, body, created_at
        FROM message
        WHERE thread_id = %s
          AND account_id = %s
        ORDER BY created_at ASC
        """,
        (thread_id, account_id),
    ).fetchall()
    return list(rows)
