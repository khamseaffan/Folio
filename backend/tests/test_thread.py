import uuid
from datetime import datetime

import pytest
from app.account import ensure_account
from app.db import connect
from app.thread import apply, create_thread, get_thread, list_threads

ALLOWED_EMAIL = "khamseaffan@gmail.com"


def _account_existed(email: str) -> bool:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if present is None or present[0] is None:
            return False
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s",
            (stored,),
        ).fetchone()
    return row is not None


def _thread_ids(account_id: uuid.UUID) -> set[uuid.UUID]:
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.thread')").fetchone()
        if present is None or present[0] is None:
            return set()
        rows = connection.execute(
            "SELECT id FROM thread WHERE account_id = %s",
            (account_id,),
        ).fetchall()
    return {row[0] for row in rows}


def _delete_threads(account_id: uuid.UUID, thread_ids: set[uuid.UUID]) -> None:
    if not thread_ids:
        return
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.thread')").fetchone()
        if present is None or present[0] is None:
            return
        connection.execute(
            """
            DELETE FROM thread
            WHERE account_id = %s
              AND id = ANY(%s)
            """,
            (account_id, list(thread_ids)),
        )


def _delete_account(account_id: uuid.UUID, email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if account is not None and account[0] is not None:
            connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
        if limit is not None and limit[0] is not None:
            connection.execute(
                "DELETE FROM email_limit WHERE email = %s",
                (stored,),
            )


@pytest.fixture
def accounts():
    owner_email = f"thread-{uuid.uuid4()}@example.com"
    owner_existed = _account_existed(owner_email)
    allowed_existed = _account_existed(ALLOWED_EMAIL)
    owner_id = ensure_account(str(uuid.uuid4()), owner_email)
    allowed_id = ensure_account(str(uuid.uuid4()), ALLOWED_EMAIL)
    allowed_before = _thread_ids(allowed_id)
    try:
        yield owner_id, allowed_id
    finally:
        if allowed_existed:
            _delete_threads(allowed_id, _thread_ids(allowed_id) - allowed_before)
        else:
            _delete_account(allowed_id, ALLOWED_EMAIL)
        if owner_existed:
            _delete_threads(owner_id, _thread_ids(owner_id))
        else:
            _delete_account(owner_id, owner_email)


def test_create_thread_uses_the_default_name(accounts) -> None:
    owner_id, _allowed_id = accounts
    with connect() as connection:
        created = create_thread(connection, owner_id)
        stored = connection.execute(
            """
            SELECT id, account_id, name, summary, created_at
            FROM thread
            WHERE id = %s
            """,
            (created[0],),
        ).fetchone()
    assert created[0] == stored[0]
    assert created[1] == owner_id
    assert created[2] == "New thread"
    assert created[3] is None
    assert isinstance(created[4], datetime)
    assert created[4].tzinfo is not None
    assert stored == created


def test_list_threads_returns_only_that_account_oldest_first(accounts) -> None:
    owner_id, allowed_id = accounts
    with connect() as connection:
        newer = create_thread(connection, owner_id)
        older = create_thread(connection, owner_id)
        allowed_thread = create_thread(connection, allowed_id)
        connection.execute(
            "UPDATE thread SET created_at = timestamptz '2020-01-02' WHERE id = %s",
            (newer[0],),
        )
        connection.execute(
            "UPDATE thread SET created_at = timestamptz '2020-01-01' WHERE id = %s",
            (older[0],),
        )
        owner_threads = list_threads(connection, owner_id)
        allowed_threads = list_threads(connection, allowed_id)
    assert [row[0] for row in owner_threads] == [older[0], newer[0]]
    assert all(row[1] == owner_id for row in owner_threads)
    assert allowed_thread[0] in {row[0] for row in allowed_threads}
    assert newer[0] not in {row[0] for row in allowed_threads}
    assert older[0] not in {row[0] for row in allowed_threads}


def test_get_thread_for_another_account_is_missing(accounts) -> None:
    owner_id, allowed_id = accounts
    with connect() as connection:
        owned = create_thread(connection, owner_id)
        allowed_thread = create_thread(connection, allowed_id)
        assert get_thread(connection, allowed_id, owned[0]) is None
        assert get_thread(connection, owner_id, allowed_thread[0]) is None
        found = get_thread(connection, owner_id, owned[0])
    assert found is not None
    assert found[0] == owned[0]
    assert found[1] == owner_id
    assert found[2] == "New thread"


def test_apply_is_idempotent() -> None:
    with connect() as connection:
        apply(connection)
        apply(connection)
        columns = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'thread'
            ORDER BY ordinal_position
            """
        ).fetchall()
        index = connection.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'thread'
              AND indexname = 'thread_account_id_created_at'
            """
        ).fetchone()
    assert columns == [
        ("id",),
        ("account_id",),
        ("name",),
        ("summary",),
        ("created_at",),
    ]
    assert index == ("thread_account_id_created_at",)
