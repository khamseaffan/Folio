import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.db import table_exists
from app.email_limit import apply as apply_email_limit

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "thread.sql"

ThreadRow = tuple[uuid.UUID, uuid.UUID, str, str | None, datetime]


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "thread"):
        return
    apply_email_limit(connection, force)
    apply_account(connection, force)
    connection.execute(_MIGRATION.read_text())


def create_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    name: str | None = None,
) -> ThreadRow:
    apply(connection)
    created = connection.execute(
        """
        INSERT INTO thread (account_id, name)
        VALUES (%s, COALESCE(%s, 'New thread'))
        RETURNING id, account_id, name, summary, created_at
        """,
        (account_id, name),
    ).fetchone()
    if created is None:
        raise RuntimeError("thread was not stored")
    return created


def rename_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    name: str,
) -> ThreadRow | None:
    apply(connection)
    return connection.execute(
        """
        UPDATE thread
        SET name = %s
        WHERE id = %s
          AND account_id = %s
        RETURNING id, account_id, name, summary, created_at
        """,
        (name, thread_id, account_id),
    ).fetchone()


def delete_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> bool:
    apply(connection)
    deleted = connection.execute(
        """
        DELETE FROM thread
        WHERE id = %s
          AND account_id = %s
        RETURNING id
        """,
        (thread_id, account_id),
    ).fetchone()
    return deleted is not None


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
