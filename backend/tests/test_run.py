import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.message import insert_message
from app.run import (
    create_run,
    finish_run,
    get_run,
    in_flight_since,
    in_flight_threads,
    start_attempt,
    stuck_runs,
)
from app.schedule import create_schedule, delete_schedule
from app.thread import create_thread, delete_thread
from app.usage import record_usage


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"run-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


def _setup(connection, account_id):
    thread = create_thread(connection, account_id)
    schedule = create_schedule(
        connection, account_id, thread[0], "t", "w", datetime.now(UTC) + timedelta(hours=1)
    )
    assert schedule is not None
    return thread[0], schedule[0]


def test_deleting_the_thread_clears_the_run_thread(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "do it")
    assert run is not None

    assert delete_thread(connection, account_id, thread_id) is True

    kept = get_run(connection, run[0])
    assert kept is not None and kept[2] is None and kept[3] is None


def test_deleting_the_schedule_clears_the_run_schedule(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "do it")
    assert run is not None

    assert delete_schedule(connection, account_id, schedule_id) is True

    kept = get_run(connection, run[0])
    assert kept is not None and kept[2] == thread_id and kept[3] is None


def test_only_one_run_in_flight_per_schedule(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    first = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert first is not None

    assert create_run(connection, account_id, thread_id, schedule_id, "schedule", "b") is None

    start_attempt(connection, first[0])
    assert create_run(connection, account_id, thread_id, schedule_id, "schedule", "b") is None

    finish_run(connection, first[0], "ready", 5, True)
    assert create_run(connection, account_id, thread_id, schedule_id, "schedule", "b") is not None


def test_a_run_without_a_schedule_is_not_limited(connection, account):
    account_id, _ = account
    thread_id, _ = _setup(connection, account_id)

    assert create_run(connection, account_id, thread_id, None, "research", "a") is not None
    assert create_run(connection, account_id, thread_id, None, "research", "b") is not None


def test_start_attempt_counts_and_stops_once_delivered(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None

    first = start_attempt(connection, run[0])
    second = start_attempt(connection, run[0])
    assert first is not None and first[5] == "working" and first[8] == 1
    assert second is not None and second[8] == 2

    finish_run(connection, run[0], "ready", 12, True)
    done = get_run(connection, run[0])
    assert done is not None and done[5] == "ready" and done[7] == 12 and done[9] is not None
    assert start_attempt(connection, run[0]) is None
    assert start_attempt(connection, uuid.uuid4()) is None


def test_in_flight_threads_lists_queued_and_working_only(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    assert in_flight_threads(connection, account_id) == {thread_id}

    start_attempt(connection, run[0])
    assert in_flight_threads(connection, account_id) == {thread_id}

    finish_run(connection, run[0], "ready", 1, True)
    assert in_flight_threads(connection, account_id) == set()


def test_in_flight_since_gives_when_and_what_kind_for_each_busy_thread(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    assert in_flight_since(connection, account_id) == {}

    run = create_run(connection, account_id, thread_id, None, "research", "look into it")
    assert run is not None
    busy = in_flight_since(connection, account_id)
    assert set(busy) == {thread_id}
    since, kind = busy[thread_id]
    assert kind == "research"
    assert since == run[10]

    finish_run(connection, run[0], "ready", 1, True)
    assert in_flight_since(connection, account_id) == {}


def test_stuck_runs_are_working_runs_older_than_the_limit(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    start_attempt(connection, run[0])
    assert run[0] not in {row[0] for row in stuck_runs(connection)}

    connection.execute(
        "UPDATE run SET started_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
    )
    assert run[0] in {row[0] for row in stuck_runs(connection)}
    assert run[0] not in {row[0] for row in stuck_runs(connection, minutes=300)}


def test_a_new_run_has_no_started_at_and_each_attempt_sets_it(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    assert run[11] is None

    first = start_attempt(connection, run[0])
    assert first is not None and first[11] is not None

    connection.execute(
        "UPDATE run SET started_at = now() - interval '1 hour' WHERE id = %s", (run[0],)
    )
    second = start_attempt(connection, run[0])
    assert second is not None and second[8] == 2
    age = connection.execute(
        "SELECT now() - started_at FROM run WHERE id = %s", (run[0],)
    ).fetchone()[0]
    assert age.total_seconds() < 60


def test_a_run_is_stuck_by_when_its_attempt_started_not_when_it_was_made(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    start_attempt(connection, run[0])

    # Made 5 hours ago, but this attempt began 10 minutes ago: it is working, not stuck.
    connection.execute(
        """
        UPDATE run
        SET created_at = now() - interval '5 hours', started_at = now() - interval '10 minutes'
        WHERE id = %s
        """,
        (run[0],),
    )
    assert run[0] not in {row[0] for row in stuck_runs(connection)}

    connection.execute(
        "UPDATE run SET started_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
    )
    assert run[0] in {row[0] for row in stuck_runs(connection)}


def test_a_working_run_with_no_started_at_falls_back_to_created_at(connection, account):
    # A run that was working before the column existed has no started_at.
    account_id, _ = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    connection.execute(
        """
        UPDATE run
        SET status = 'working', started_at = NULL, created_at = now() - interval '10 minutes'
        WHERE id = %s
        """,
        (run[0],),
    )
    assert run[0] not in {row[0] for row in stuck_runs(connection)}
    connection.execute(
        "UPDATE run SET created_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
    )
    assert run[0] in {row[0] for row in stuck_runs(connection)}


def test_deleting_a_run_clears_message_and_usage_run_ids(connection, account):
    account_id, email = account
    thread_id, schedule_id = _setup(connection, account_id)
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", "a")
    assert run is not None
    message = insert_message(
        connection, account_id, thread_id, "assistant", "result", "done", run[0]
    )
    usage = record_usage(
        connection, account_id, email, "schedule", "openai", "m", 1, 1, 2, None, run[0]
    )
    assert message is not None and message[3] == run[0] and usage[12] == run[0]

    connection.execute("DELETE FROM run WHERE id = %s", (run[0],))

    after_message = connection.execute(
        "SELECT run_id FROM message WHERE id = %s", (message[0],)
    ).fetchone()
    after_usage = connection.execute(
        "SELECT run_id FROM usage WHERE id = %s", (usage[0],)
    ).fetchone()
    assert after_message == (None,) and after_usage == (None,)


def test_the_database_refuses_an_unknown_status_or_kind(connection, account):
    account_id, _ = account
    thread_id, _ = _setup(connection, account_id)

    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO run (account_id, thread_id, kind, status, task) "
            "VALUES (%s, %s, 'schedule', 'bogus', 't')",
            (account_id, thread_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO run (account_id, thread_id, kind, status, task) "
            "VALUES (%s, %s, 'bogus', 'queued', 't')",
            (account_id, thread_id),
        )


def test_a_run_of_another_accounts_thread_is_refused(connection, account):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        thread = create_thread(connection, other_id)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            create_run(connection, account_id, thread[0], None, "research", "a")
    finally:
        _remove(other_id, other_email)
