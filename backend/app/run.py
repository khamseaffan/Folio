import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.db import table_exists
from app.message import apply as apply_message
from app.schedule import apply as apply_schedule
from app.usage import apply as apply_usage

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "run.sql"

# id, account_id, thread_id, schedule_id, kind, status, task, work_ms, attempt_count,
# delivered_at, created_at, started_at
RunRow = tuple[
    uuid.UUID,
    uuid.UUID,
    uuid.UUID | None,
    uuid.UUID | None,
    str,
    str,
    str,
    int,
    int,
    datetime | None,
    datetime,
    datetime | None,
]

# Measured from the start of the latest attempt, so time spent waiting in the queue does not
# count. One attempt is cut off at 30 minutes. The wait before the next attempt is at most
# 5 minutes of backoff, plus up to 30 minutes for a free slot when 3 other runs are going.
# That is 65 minutes in the worst healthy case, and 90 leaves room.
WORKING_GIVE_UP_MINUTES = 90
# A run that never left the queue is given up after this long.
QUEUED_GIVE_UP_MINUTES = 30
# A queued run this old was probably never put on the queue, so the tick puts it there again.
REQUEUE_AFTER_MINUTES = 2

_COLUMNS = (
    "id, account_id, thread_id, schedule_id, kind, status, task, work_ms, attempt_count, "
    "delivered_at, created_at, started_at"
)


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "run"):
        return
    apply_schedule(connection, force)
    apply_message(connection, force)
    apply_usage(connection, force)
    connection.execute(_MIGRATION.read_text())


def create_run(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    schedule_id: uuid.UUID | None,
    kind: str,
    task: str,
) -> RunRow | None:
    apply(connection)
    # The unique index decides, so two ticks at once cannot both insert.
    return connection.execute(
        f"""
        INSERT INTO run (account_id, thread_id, schedule_id, kind, status, task)
        VALUES (%s, %s, %s, %s, 'queued', %s)
        ON CONFLICT DO NOTHING
        RETURNING {_COLUMNS}
        """,
        (account_id, thread_id, schedule_id, kind, task),
    ).fetchone()


def get_run(connection: psycopg.Connection, run_id: uuid.UUID) -> RunRow | None:
    apply(connection)
    return connection.execute(
        f"SELECT {_COLUMNS} FROM run WHERE id = %s",
        (run_id,),
    ).fetchone()


def start_attempt(connection: psycopg.Connection, run_id: uuid.UUID) -> RunRow | None:
    apply(connection)
    return connection.execute(
        f"""
        UPDATE run
        SET status = 'working', attempt_count = attempt_count + 1, started_at = now()
        WHERE id = %s
          AND status IN ('queued', 'working')
          AND delivered_at IS NULL
        RETURNING {_COLUMNS}
        """,
        (run_id,),
    ).fetchone()


def finish_run(
    connection: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    work_ms: int,
    delivered: bool,
) -> None:
    apply(connection)
    connection.execute(
        """
        UPDATE run
        SET status = %s,
            work_ms = %s,
            delivered_at = CASE WHEN %s THEN now() ELSE delivered_at END
        WHERE id = %s
        """,
        (status, work_ms, delivered, run_id),
    )


def finish_working(
    connection: psycopg.Connection,
    run_id: uuid.UUID,
    status: str,
    work_ms: int,
    from_status: str = "working",
) -> bool:
    # Only a run still in from_status can be finished. A run the tick marked dead stays dead.
    apply(connection)
    changed = connection.execute(
        """
        UPDATE run
        SET status = %s, work_ms = %s
        WHERE id = %s
          AND status = %s
        RETURNING id
        """,
        (status, work_ms, run_id, from_status),
    ).fetchone()
    return changed is not None


def hold_working(connection: psycopg.Connection, run_id: uuid.UUID) -> bool:
    # Locks the row for this transaction, so the tick cannot mark it dead before the save ends.
    apply(connection)
    held = connection.execute(
        "SELECT id FROM run WHERE id = %s AND status = 'working' FOR UPDATE",
        (run_id,),
    ).fetchone()
    return held is not None


def in_flight_threads(connection: psycopg.Connection, account_id: uuid.UUID) -> set[uuid.UUID]:
    apply(connection)
    rows = connection.execute(
        """
        SELECT DISTINCT thread_id
        FROM run
        WHERE account_id = %s
          AND thread_id IS NOT NULL
          AND status IN ('queued', 'working')
        """,
        (account_id,),
    ).fetchall()
    return {row[0] for row in rows}


def stuck_runs(
    connection: psycopg.Connection, minutes: int = WORKING_GIVE_UP_MINUTES
) -> list[RunRow]:
    # Across accounts by design: the tick frees runs a crashed worker left behind.
    apply(connection)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS}
        FROM run
        WHERE status = 'working'
          AND COALESCE(started_at, created_at) < now() - make_interval(mins => %s)
        ORDER BY COALESCE(started_at, created_at) ASC
        """,
        (minutes,),
    ).fetchall()
    return list(rows)


def queued_runs(
    connection: psycopg.Connection, minutes: int = REQUEUE_AFTER_MINUTES
) -> list[RunRow]:
    # Across accounts by design: the tick finds runs that never reached the queue.
    apply(connection)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS}
        FROM run
        WHERE status = 'queued'
          AND created_at < now() - make_interval(mins => %s)
        ORDER BY created_at ASC
        """,
        (minutes,),
    ).fetchall()
    return list(rows)
