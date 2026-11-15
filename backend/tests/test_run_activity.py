import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.run import create_run
from app.run_activity import MAX_TEXT_CHARS, list_activity, record_activity
from app.thread import create_thread


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"activity-{uuid.uuid4().hex}@example.com"
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


def _run(connection, account_id):
    thread = create_thread(connection, account_id)
    run = create_run(connection, account_id, thread[0], None, "research", "question")
    assert run is not None
    return run[0]


def test_lines_come_back_in_the_order_they_were_written(connection, account):
    account_id, _ = account
    run_id = _run(connection, account_id)
    record_activity(connection, account_id, run_id, "search", "four day week")
    record_activity(connection, account_id, run_id, "read", "Pilot results", "https://a.example/x")
    record_activity(connection, account_id, run_id, "skipped", "https://b.example/y")

    lines = list_activity(connection, account_id, run_id)
    assert [(line[1], line[2], line[3]) for line in lines] == [
        ("search", "four day week", None),
        ("read", "Pilot results", "https://a.example/x"),
        ("skipped", "https://b.example/y", None),
    ]
    assert lines[0][0] <= lines[1][0] <= lines[2][0]


def test_another_account_never_sees_the_lines(connection, account):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        run_id = _run(connection, account_id)
        record_activity(connection, account_id, run_id, "search", "private words")
        assert list_activity(connection, other_id, run_id) == []
        # A line cannot be written under another account's run either.
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            record_activity(connection, other_id, run_id, "search", "x")
    finally:
        _remove(other_id, other_email)


def test_a_kind_outside_the_list_is_refused(connection, account):
    account_id, _ = account
    run_id = _run(connection, account_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        record_activity(connection, account_id, run_id, "thinking", "x")


def test_a_long_line_is_cut(connection, account):
    account_id, _ = account
    run_id = _run(connection, account_id)
    record_activity(connection, account_id, run_id, "search", "w" * (MAX_TEXT_CHARS + 500))
    (line,) = list_activity(connection, account_id, run_id)
    assert len(line[2]) == MAX_TEXT_CHARS


def test_the_lines_go_with_the_run_and_with_the_account(connection, account):
    account_id, _ = account
    run_id = _run(connection, account_id)
    record_activity(connection, account_id, run_id, "search", "x")
    connection.execute("DELETE FROM run WHERE id = %s", (run_id,))
    assert connection.execute("SELECT count(*) FROM run_activity").fetchone() is not None
    assert list_activity(connection, account_id, run_id) == []

    second = _run(connection, account_id)
    record_activity(connection, account_id, second, "search", "y")
    connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
    count = connection.execute(
        "SELECT count(*) FROM run_activity WHERE run_id = %s", (second,)
    ).fetchone()
    assert count == (0,)
