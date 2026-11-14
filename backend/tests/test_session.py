import uuid

import pytest
from app.account import ensure_account
from app.db import connect
from app.session import (
    end_account_session,
    end_session,
    list_open_sessions,
    open_session,
    session_handle,
    start_session,
)


@pytest.fixture
def stored_account():
    email = f"session-{uuid.uuid4()}@example.com"
    account_id = ensure_account(str(uuid.uuid4()), email)
    try:
        yield account_id, email
    finally:
        with connect() as connection:
            with connection.transaction():
                connection.execute(
                    "DELETE FROM session WHERE account_id = %s",
                    (account_id,),
                )
                connection.execute(
                    "DELETE FROM account WHERE id = %s",
                    (account_id,),
                )
                connection.execute(
                    "DELETE FROM email_limit WHERE email = %s",
                    (email,),
                )


def test_two_start_session_calls_leave_two_open(stored_account) -> None:
    account_id, _email = stored_account
    first = start_session(account_id)
    second = start_session(account_id)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id
            FROM session
            WHERE account_id = %s
              AND ended_at IS NULL
            """,
            (account_id,),
        ).fetchall()
    assert {row[0] for row in rows} == {first, second}


def test_third_start_session_ends_the_oldest(stored_account) -> None:
    account_id, _email = stored_account
    oldest = start_session(account_id)
    middle = start_session(account_id)
    newest = start_session(account_id)
    with connect() as connection:
        open_rows = connection.execute(
            """
            SELECT id
            FROM session
            WHERE account_id = %s
              AND ended_at IS NULL
            ORDER BY created_at ASC
            """,
            (account_id,),
        ).fetchall()
        ended = connection.execute(
            "SELECT ended_at FROM session WHERE id = %s",
            (oldest,),
        ).fetchone()
    assert [row[0] for row in open_rows] == [middle, newest]
    assert ended is not None
    assert ended[0] is not None


def test_open_session_ends_session_idle_for_31_minutes(stored_account) -> None:
    account_id, _email = stored_account
    session_id = start_session(account_id)
    with connect() as connection:
        connection.execute(
            """
            UPDATE session
            SET last_active_at = now() - interval '31 minutes'
            WHERE id = %s
            """,
            (session_id,),
        )
    assert open_session(session_id) is None
    with connect() as connection:
        row = connection.execute(
            "SELECT ended_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert row is not None
    assert row[0] is not None


def test_open_session_keeps_session_active_29_minutes_ago(stored_account) -> None:
    account_id, _email = stored_account
    session_id = start_session(account_id)
    with connect() as connection:
        connection.execute(
            """
            UPDATE session
            SET last_active_at = now() - interval '29 minutes'
            WHERE id = %s
            """,
            (session_id,),
        )
        before = connection.execute(
            "SELECT last_active_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert open_session(session_id) == account_id
    with connect() as connection:
        after = connection.execute(
            "SELECT last_active_at, ended_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert before is not None
    assert after is not None
    assert after[0] == before[0]
    assert after[1] is None


def test_end_session_sets_ended_at_once(stored_account) -> None:
    account_id, _email = stored_account
    session_id = start_session(account_id)
    end_session(session_id)
    with connect() as connection:
        first = connection.execute(
            "SELECT ended_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    end_session(session_id)
    with connect() as connection:
        second = connection.execute(
            "SELECT ended_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert first is not None
    assert second is not None
    assert first[0] is not None
    assert second[0] == first[0]


def test_list_open_sessions_skips_ended_and_idle_oldest_first(stored_account) -> None:
    account_id, _email = stored_account
    first = start_session(account_id)
    second = start_session(account_id)
    listed = list_open_sessions(account_id)
    assert [row[0] for row in listed] == [session_handle(first), session_handle(second)]
    assert listed[0][1] <= listed[1][1]
    with connect() as connection:
        connection.execute(
            "UPDATE session SET last_active_at = now() - interval '31 minutes' WHERE id = %s",
            (first,),
        )
    assert [row[0] for row in list_open_sessions(account_id)] == [session_handle(second)]
    end_session(second)
    assert list_open_sessions(account_id) == []


def test_end_account_session_only_ends_an_open_session_of_that_account(
    stored_account,
) -> None:
    account_id, _email = stored_account
    other_email = f"session-other-{uuid.uuid4()}@example.com"
    other_id = ensure_account(str(uuid.uuid4()), other_email)
    try:
        mine = start_session(account_id)
        theirs = start_session(other_id)
        assert end_account_session(account_id, session_handle(theirs)) is False
        assert end_account_session(account_id, str(mine)) is False
        assert open_session(theirs) == other_id
        assert end_account_session(account_id, session_handle(mine)) is True
        assert end_account_session(account_id, session_handle(mine)) is False
        assert open_session(mine) is None
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM account WHERE id = %s", (other_id,))
            connection.execute("DELETE FROM email_limit WHERE email = %s", (other_email,))
