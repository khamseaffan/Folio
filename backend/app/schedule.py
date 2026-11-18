import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.db import table_exists
from app.thread import apply as apply_thread

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "schedule.sql"

# id, account_id, thread_id, task, when_text, next_run_at, created_at, updated_at
ScheduleRow = tuple[uuid.UUID, uuid.UUID, uuid.UUID, str, str, datetime | None, datetime, datetime]

_COLUMNS = "id, account_id, thread_id, task, when_text, next_run_at, created_at, updated_at"


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "schedule"):
        return
    apply_thread(connection, force)
    connection.execute(_MIGRATION.read_text())


def list_schedules(connection: psycopg.Connection, account_id: uuid.UUID) -> list[ScheduleRow]:
    apply(connection)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS}
        FROM schedule
        WHERE account_id = %s
        ORDER BY next_run_at ASC NULLS LAST, created_at ASC
        """,
        (account_id,),
    ).fetchall()
    return list(rows)


def get_schedule(
    connection: psycopg.Connection, account_id: uuid.UUID, schedule_id: uuid.UUID
) -> ScheduleRow | None:
    apply(connection)
    return connection.execute(
        f"SELECT {_COLUMNS} FROM schedule WHERE id = %s AND account_id = %s",
        (schedule_id, account_id),
    ).fetchone()


def create_schedule(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    task: str,
    when_text: str,
    next_run_at: datetime | None,
) -> ScheduleRow | None:
    apply(connection)
    owned = connection.execute(
        "SELECT id FROM thread WHERE id = %s AND account_id = %s",
        (thread_id, account_id),
    ).fetchone()
    if owned is None:
        return None
    return connection.execute(
        f"""
        INSERT INTO schedule (account_id, thread_id, task, when_text, next_run_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING {_COLUMNS}
        """,
        (account_id, thread_id, task, when_text, next_run_at),
    ).fetchone()


def update_schedule(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    schedule_id: uuid.UUID,
    task: str,
    when_text: str,
    next_run_at: datetime | None,
) -> ScheduleRow | None:
    apply(connection)
    return connection.execute(
        f"""
        UPDATE schedule
        SET task = %s, when_text = %s, next_run_at = %s, updated_at = now()
        WHERE id = %s
          AND account_id = %s
        RETURNING {_COLUMNS}
        """,
        (task, when_text, next_run_at, schedule_id, account_id),
    ).fetchone()


def delete_schedule(
    connection: psycopg.Connection, account_id: uuid.UUID, schedule_id: uuid.UUID
) -> bool:
    apply(connection)
    deleted = connection.execute(
        "DELETE FROM schedule WHERE id = %s AND account_id = %s RETURNING id",
        (schedule_id, account_id),
    ).fetchone()
    return deleted is not None


def due_schedules(connection: psycopg.Connection, limit: int = 50) -> list[ScheduleRow]:
    # Across accounts by design: the tick looks for work and returns nothing to a person.
    apply(connection)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS}
        FROM schedule
        WHERE next_run_at IS NOT NULL
          AND next_run_at <= now()
        ORDER BY next_run_at ASC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return list(rows)


def set_next_run(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    schedule_id: uuid.UUID,
    expected: datetime,
    next_run_at: datetime | None,
) -> bool:
    apply(connection)
    changed = connection.execute(
        """
        UPDATE schedule
        SET next_run_at = %s, updated_at = now()
        WHERE id = %s
          AND account_id = %s
          AND next_run_at = %s
        RETURNING id
        """,
        (next_run_at, schedule_id, account_id, expected),
    ).fetchone()
    return changed is not None
