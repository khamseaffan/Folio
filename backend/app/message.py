import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.db import table_exists
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


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "message"):
        return
    apply_thread(connection, force)
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


def update_message_body(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    body: str,
) -> MessageRow | None:
    apply(connection)
    updated = connection.execute(
        """
        UPDATE message
        SET body = %s
        WHERE id = %s
          AND account_id = %s
          AND thread_id = %s
          AND role = 'assistant'
          AND kind = 'chat'
        RETURNING id, account_id, thread_id, run_id, role, kind, body, created_at
        """,
        (body, message_id, account_id, thread_id),
    ).fetchone()
    return updated


def delete_failure(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    apply(connection)
    connection.execute(
        """
        DELETE FROM message
        WHERE id = %s
          AND account_id = %s
          AND thread_id = %s
          AND kind = 'failure'
        """,
        (message_id, account_id, thread_id),
    )


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
