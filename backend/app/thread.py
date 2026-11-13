import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.email_limit import apply as apply_email_limit

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "thread.sql"

ThreadRow = tuple[uuid.UUID, uuid.UUID, str, str | None, datetime]


def apply(connection: psycopg.Connection) -> None:
    apply_email_limit(connection)
    apply_account(connection)
    connection.execute(_MIGRATION.read_text())


def create_thread(connection: psycopg.Connection, account_id: uuid.UUID) -> ThreadRow:
    apply(connection)
    created = connection.execute(
        """
        INSERT INTO thread (account_id)
        VALUES (%s)
        RETURNING id, account_id, name, summary, created_at
        """,
        (account_id,),
    ).fetchone()
    if created is None:
        raise RuntimeError("thread was not stored")
    return created


def list_threads(connection: psycopg.Connection, account_id: uuid.UUID) -> list[ThreadRow]:
    apply(connection)
    rows = connection.execute(
        """
        SELECT id, account_id, name, summary, created_at
        FROM thread
        WHERE account_id = %s
        ORDER BY created_at ASC
        """,
        (account_id,),
    ).fetchall()
    return list(rows)


def set_summary(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    summary: str,
) -> None:
    apply(connection)
    updated = connection.execute(
        """
        UPDATE thread
        SET summary = %s
        WHERE id = %s
          AND account_id = %s
        RETURNING id
        """,
        (summary, thread_id, account_id),
    ).fetchone()
    if updated is None:
        raise RuntimeError("thread is missing")


def get_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> ThreadRow | None:
    apply(connection)
    return connection.execute(
        """
        SELECT id, account_id, name, summary, created_at
        FROM thread
        WHERE id = %s
          AND account_id = %s
        """,
        (thread_id, account_id),
    ).fetchone()
