import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.schedule import (
    create_schedule,
    delete_schedule,
    due_schedules,
    get_schedule,
    list_schedules,
    set_next_run,
    update_schedule,
)
from app.thread import create_thread, delete_thread


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"sched-{uuid.uuid4().hex}@example.com"
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
def other() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


def _in(hours: float) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def test_create_list_update_delete(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    later = create_schedule(connection, account_id, thread[0], "later", "daily", _in(5))
    sooner = create_schedule(connection, account_id, thread[0], "sooner", "daily", _in(1))
    never = create_schedule(connection, account_id, thread[0], "never", "once", None)
    assert later is not None and sooner is not None and never is not None

    listed = [row[3] for row in list_schedules(connection, account_id)]
    assert listed == ["sooner", "later", "never"]

    updated = update_schedule(connection, account_id, later[0], "changed", "weekly", None)
    assert updated is not None
    assert updated[3:6] == ("changed", "weekly", None)
    assert updated[7] >= later[7]
    fetched = get_schedule(connection, account_id, later[0])
    assert fetched is not None and fetched[3] == "changed"

    assert delete_schedule(connection, account_id, later[0]) is True
    assert get_schedule(connection, account_id, later[0]) is None
    assert delete_schedule(connection, account_id, later[0]) is False


def test_create_needs_a_thread_of_the_account(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    thread = create_thread(connection, other_id)

    assert create_schedule(connection, account_id, thread[0], "t", "w", _in(1)) is None
    assert create_schedule(connection, account_id, uuid.uuid4(), "t", "w", _in(1)) is None


def test_another_account_cannot_reach_a_schedule(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    thread = create_thread(connection, account_id)
    made = create_schedule(connection, account_id, thread[0], "mine", "daily", _in(1))
    assert made is not None

    assert get_schedule(connection, other_id, made[0]) is None
    assert update_schedule(connection, other_id, made[0], "x", "y", None) is None
    assert delete_schedule(connection, other_id, made[0]) is False
    assert list_schedules(connection, other_id) == []
    assert set_next_run(connection, other_id, made[0], made[5], None) is False
    still = get_schedule(connection, account_id, made[0])
    assert still is not None and still[3] == "mine" and still[5] == made[5]


def test_deleting_the_thread_deletes_its_schedules(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = create_schedule(connection, account_id, thread[0], "t", "w", _in(1))
    assert made is not None

    assert delete_thread(connection, account_id, thread[0]) is True

    assert get_schedule(connection, account_id, made[0]) is None


def test_set_next_run_only_when_the_time_matches(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = create_schedule(connection, account_id, thread[0], "t", "w", _in(1))
    assert made is not None and made[5] is not None
    new_time = _in(2)

    assert set_next_run(connection, account_id, made[0], _in(9), new_time) is False
    unchanged = get_schedule(connection, account_id, made[0])
    assert unchanged is not None and unchanged[5] == made[5]

    assert set_next_run(connection, account_id, made[0], made[5], new_time) is True
    moved = get_schedule(connection, account_id, made[0])
    assert moved is not None and moved[5] == new_time

    assert set_next_run(connection, account_id, made[0], new_time, None) is True
    cleared = get_schedule(connection, account_id, made[0])
    assert cleared is not None and cleared[5] is None


def test_due_schedules_returns_only_rows_whose_time_has_come(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    past = create_schedule(connection, account_id, thread[0], "past", "w", _in(-1))
    future = create_schedule(connection, account_id, thread[0], "future", "w", _in(1))
    none = create_schedule(connection, account_id, thread[0], "none", "w", None)
    assert past is not None and future is not None and none is not None

    due = {row[0] for row in due_schedules(connection, limit=500) if row[1] == account_id}

    assert due == {past[0]}
