import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app import run as run_table
from app.db import table_exists

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "run_activity.sql"

# One line holds a search, a page title, or an address. Longer text is cut, not refused.
MAX_TEXT_CHARS = 300

# at, kind, text, url
ActivityRow = tuple[datetime, str, str, str | None]


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "run_activity"):
        return
    run_table.apply(connection, force)
    connection.execute(_MIGRATION.read_text())


def record_activity(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    run_id: uuid.UUID,
    kind: str,
    text: str,
    url: str | None = None,
) -> None:
    apply(connection)
    connection.execute(
        """
        INSERT INTO run_activity (account_id, run_id, kind, text, url)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (account_id, run_id, kind, text[:MAX_TEXT_CHARS], url),
    )


def list_activity(
    connection: psycopg.Connection, account_id: uuid.UUID, run_id: uuid.UUID
) -> list[ActivityRow]:
    apply(connection)
    rows = connection.execute(
        """
        SELECT at, kind, text, url
        FROM run_activity
        WHERE account_id = %s
          AND run_id = %s
        ORDER BY id ASC
        """,
        (account_id, run_id),
    ).fetchall()
    return list(rows)
