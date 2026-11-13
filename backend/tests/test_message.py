import uuid
from datetime import datetime

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.message import apply, insert_message, list_messages
from app.thread import create_thread

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
    owner_email = f"message-{uuid.uuid4()}@example.com"
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


def test_chat_messages_keep_a_null_run_id(accounts) -> None:
    owner_id, _allowed_id = accounts
    with connect() as connection:
        thread = create_thread(connection, owner_id)
        assert list_messages(connection, owner_id, thread[0]) == []
        person = insert_message(
            connection,
            owner_id,
            thread[0],
            "person",
            "chat",
            "hello",
        )
        assistant = insert_message(
            connection,
            owner_id,
            thread[0],
            "assistant",
            "chat",
            "hi",
        )
        connection.execute(
            "UPDATE message SET created_at = timestamptz '2020-01-02' WHERE id = %s",
            (person[0],),
        )
        connection.execute(
            "UPDATE message SET created_at = timestamptz '2020-01-01' WHERE id = %s",
            (assistant[0],),
        )
        listed = list_messages(connection, owner_id, thread[0])
    assert person is not None
    assert assistant is not None
    assert person[3] is None
    assert assistant[3] is None
    assert person[4] == "person"
    assert assistant[4] == "assistant"
    assert person[5] == "chat"
    assert assistant[5] == "chat"
    assert person[6] == "hello"
    assert assistant[6] == "hi"
    assert isinstance(person[7], datetime)
    assert person[7].tzinfo is not None
    assert listed is not None
    assert [row[0] for row in listed] == [assistant[0], person[0]]


def test_run_id_can_be_stored_without_a_run_table(accounts) -> None:
    owner_id, _allowed_id = accounts
    run_id = uuid.uuid4()
    with connect() as connection:
        thread = create_thread(connection, owner_id)
        run_table = connection.execute("SELECT to_regclass('public.run')").fetchone()
        stored = insert_message(
            connection,
            owner_id,
            thread[0],
            "assistant",
            "result",
            "done",
            run_id,
        )
    assert run_table == (None,)
    assert stored is not None
    assert stored[3] == run_id
    assert stored[5] == "result"


def test_bad_role_and_kind_are_rejected(accounts) -> None:
    owner_id, _allowed_id = accounts
    with connect() as connection:
        thread = create_thread(connection, owner_id)
    thread_id = thread[0]

    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as connection:
            insert_message(connection, owner_id, thread_id, "user", "chat", "no")

    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as connection:
            insert_message(connection, owner_id, thread_id, "person", "note", "no")

    with connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM message WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
    assert count == (0,)


def test_delete_thread_removes_its_messages(accounts) -> None:
    owner_id, _allowed_id = accounts
    with connect() as connection:
        thread = create_thread(connection, owner_id)
        other = create_thread(connection, owner_id)
        insert_message(connection, owner_id, thread[0], "person", "chat", "gone")
        insert_message(connection, owner_id, thread[0], "assistant", "failure", "also gone")
        kept = insert_message(connection, owner_id, other[0], "person", "question", "stays")
        connection.execute(
            "DELETE FROM thread WHERE id = %s AND account_id = %s",
            (thread[0], owner_id),
        )
        removed = connection.execute(
            "SELECT count(*) FROM message WHERE thread_id = %s",
            (thread[0],),
        ).fetchone()
        remaining = connection.execute(
            "SELECT id FROM message WHERE thread_id = %s",
            (other[0],),
        ).fetchone()
    assert removed == (0,)
    assert kept is not None
    assert remaining == (kept[0],)


def test_other_account_cannot_list_or_insert(accounts) -> None:
    owner_id, allowed_id = accounts
    with connect() as connection:
        thread = create_thread(connection, owner_id)
        insert_message(connection, owner_id, thread[0], "person", "chat", "private")
        assert list_messages(connection, allowed_id, thread[0]) is None
        assert (
            insert_message(connection, allowed_id, thread[0], "person", "chat", "stolen")
            is None
        )
        assert insert_message(
            connection,
            owner_id,
            uuid.uuid4(),
            "person",
            "chat",
            "missing",
        ) is None
        rows = connection.execute(
            "SELECT body FROM message WHERE thread_id = %s",
            (thread[0],),
        ).fetchall()
    assert rows == [("private",)]


def test_apply_is_idempotent() -> None:
    with connect() as connection:
        apply(connection)
        apply(connection)
        columns = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'message'
            ORDER BY ordinal_position
            """
        ).fetchall()
        checks = connection.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.message'::regclass
              AND contype = 'c'
            ORDER BY conname
            """
        ).fetchall()
        thread_fk = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.message'::regclass
              AND conname = 'message_thread_account_fkey'
            """
        ).fetchone()
        foreign_keys = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.message'::regclass
              AND contype = 'f'
            """
        ).fetchall()
        index = connection.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'message'
              AND indexname = 'message_thread_id_created_at'
            """
        ).fetchone()
    assert columns == [
        ("id",),
        ("account_id",),
        ("thread_id",),
        ("run_id",),
        ("role",),
        ("kind",),
        ("body",),
        ("created_at",),
    ]
    assert checks == [("message_kind_allowed",), ("message_role_allowed",)]
    assert thread_fk is not None
    assert "FOREIGN KEY (thread_id, account_id)" in thread_fk[0]
    assert "REFERENCES thread" in thread_fk[0]
    assert "ON DELETE CASCADE" in thread_fk[0]
    assert all("run_id" not in definition[0] for definition in foreign_keys)
    assert index == ("message_thread_id_created_at",)
