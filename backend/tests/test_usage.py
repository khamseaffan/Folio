import uuid
from datetime import datetime
from decimal import Decimal

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.message import insert_message
from app.thread import create_thread
from app.usage import apply, record_usage, token_totals_for_email, tokens_for_email

PROVIDER = "openai"
MODEL = "gpt-6.1-sol"


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


def _delete_usage(account_id: uuid.UUID, email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return
        connection.execute(
            """
            DELETE FROM usage
            WHERE email = %s
               OR account_id = %s
            """,
            (stored, account_id),
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
def account():
    email = f"usage-{uuid.uuid4()}@example.com"
    existed = _account_existed(email)
    account_id = ensure_account(str(uuid.uuid4()), email)
    try:
        yield account_id, email
    finally:
        _delete_usage(account_id, email)
        if not existed:
            _delete_account(account_id, email)


def test_complete_row_stores_counts_and_decimal_cost(account) -> None:
    account_id, email = account
    with connect() as connection:
        stored = record_usage(
            connection,
            account_id,
            email.upper(),
            "chat",
            PROVIDER,
            MODEL,
            1_000_000,
            2,
            1_000_002,
        )
    assert stored[1] == account_id
    assert stored[2] == email
    assert isinstance(stored[3], datetime)
    assert stored[3].tzinfo is not None
    assert stored[4] == PROVIDER
    assert stored[5] == MODEL
    assert stored[6] == 1_000_000
    assert stored[7] == 2
    assert stored[8] == 1_000_002
    assert stored[9] == Decimal("2.00002")
    assert isinstance(stored[9], Decimal)
    assert stored[10] is False
    assert stored[11] is None
    assert stored[12] is None
    assert stored[13] == "chat"


def test_missing_count_is_stored_null(account) -> None:
    account_id, email = account
    cases = ((None, 4, 5), (4, None, 5), (4, 5, None))
    with connect() as connection:
        for given in cases:
            stored = record_usage(
                connection,
                account_id,
                email,
                "chat",
                PROVIDER,
                MODEL,
                given[0],
                given[1],
                given[2],
            )
            row = connection.execute(
                """
                SELECT input_tokens, output_tokens, total_tokens, usage_missing, expected_cost
                FROM usage
                WHERE id = %s
                """,
                (stored[0],),
            ).fetchone()
            assert row == (given[0], given[1], given[2], True, None)
            assert row[0] != 0
            assert row[1] != 0
            assert row[2] != 0


def test_unknown_model_has_null_cost(account) -> None:
    account_id, email = account
    with connect() as connection:
        stored = record_usage(
            connection,
            account_id,
            email,
            "research",
            PROVIDER,
            "not-a-published-model",
            4,
            5,
            9,
        )
    assert stored[6] == 4
    assert stored[7] == 5
    assert stored[8] == 9
    assert stored[9] is None
    assert stored[10] is False


def test_bad_operation_is_rejected(account) -> None:
    account_id, email = account
    with connect() as connection:
        apply(connection)
    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as connection:
            record_usage(
                connection,
                account_id,
                email,
                "summary",
                PROVIDER,
                MODEL,
                1,
                1,
                2,
            )
    with connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM usage WHERE email = %s",
            (email,),
        ).fetchone()
    assert count == (0,)


def test_run_id_stays_null_and_no_run_table_exists(account) -> None:
    account_id, email = account
    run_id = uuid.uuid4()
    with connect() as connection:
        omitted = record_usage(
            connection,
            account_id,
            email,
            "schedule",
            PROVIDER,
            MODEL,
            1,
            1,
            2,
        )
        supplied = record_usage(
            connection,
            account_id,
            email,
            "browser",
            PROVIDER,
            MODEL,
            1,
            1,
            2,
            None,
            run_id,
        )
        run_table = connection.execute("SELECT to_regclass('public.run')").fetchone()
    assert run_table == (None,)
    assert omitted[12] is None
    assert supplied[12] == run_id


def test_delete_message_keeps_usage_account_and_email(account) -> None:
    account_id, email = account
    with connect() as connection:
        thread = create_thread(connection, account_id)
        message = insert_message(
            connection,
            account_id,
            thread[0],
            "assistant",
            "chat",
            "hi",
        )
        assert message is not None
        stored = record_usage(
            connection,
            account_id,
            email,
            "chat",
            PROVIDER,
            MODEL,
            8,
            9,
            17,
            message[0],
        )
        connection.execute(
            "DELETE FROM message WHERE id = %s AND account_id = %s",
            (message[0], account_id),
        )
        kept = connection.execute(
            """
            SELECT account_id, email, message_id
            FROM usage
            WHERE id = %s
            """,
            (stored[0],),
        ).fetchone()
    assert kept == (account_id, email, None)


def test_delete_thread_keeps_usage_account_and_email(account) -> None:
    account_id, email = account
    with connect() as connection:
        thread = create_thread(connection, account_id)
        message = insert_message(
            connection,
            account_id,
            thread[0],
            "assistant",
            "chat",
            "hi",
        )
        assert message is not None
        stored = record_usage(
            connection,
            account_id,
            email,
            "chat",
            PROVIDER,
            MODEL,
            8,
            9,
            17,
            message[0],
        )
        connection.execute(
            "DELETE FROM thread WHERE id = %s AND account_id = %s",
            (thread[0], account_id),
        )
        removed = connection.execute(
            "SELECT count(*) FROM message WHERE id = %s",
            (message[0],),
        ).fetchone()
        kept = connection.execute(
            """
            SELECT account_id, email, message_id
            FROM usage
            WHERE id = %s
            """,
            (stored[0],),
        ).fetchone()
    assert removed == (0,)
    assert kept == (account_id, email, None)


def test_tokens_for_email_sums_only_complete_totals(account) -> None:
    account_id, email = account
    with connect() as connection:
        record_usage(
            connection,
            account_id,
            email,
            "chat",
            PROVIDER,
            MODEL,
            3,
            4,
            10,
        )
        record_usage(
            connection,
            account_id,
            email,
            "transcription",
            PROVIDER,
            MODEL,
            None,
            1,
            99,
        )
        record_usage(
            connection,
            account_id,
            email,
            "chat",
            PROVIDER,
            MODEL,
            1,
            1,
            5,
        )
        assert tokens_for_email(connection, email.upper()) == 15
        assert tokens_for_email(connection, f"none-{uuid.uuid4()}@example.com") == 0


def test_token_totals_for_email_sums_each_count_and_skips_missing(account) -> None:
    account_id, email = account
    with connect() as connection:
        record_usage(connection, account_id, email, "chat", PROVIDER, MODEL, 3, 4, 7)
        record_usage(connection, account_id, email, "chat", PROVIDER, MODEL, 1, 1, 2)
        record_usage(connection, account_id, email, "chat", PROVIDER, MODEL, None, 9, 99)
        assert token_totals_for_email(connection, email.upper()) == (4, 5, 9)
        none = f"none-{uuid.uuid4()}@example.com"
        assert token_totals_for_email(connection, none) == (0, 0, 0)


def test_apply_is_idempotent() -> None:
    with connect() as connection:
        apply(connection)
        apply(connection)
        columns = connection.execute(
            """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage'
            ORDER BY ordinal_position
            """
        ).fetchall()
        checks = connection.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.usage'::regclass
              AND contype = 'c'
            ORDER BY conname
            """
        ).fetchall()
        operation = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.usage'::regclass
              AND conname = 'usage_operation_allowed'
            """
        ).fetchone()
        message_fk = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.usage'::regclass
              AND conname = 'usage_message_account_fkey'
            """
        ).fetchone()
        foreign_keys = connection.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.usage'::regclass
              AND contype = 'f'
            """
        ).fetchall()
        indexes = connection.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'usage'
            """
        ).fetchall()
    by_name = {column[0]: column for column in columns}
    assert [column[0] for column in columns] == [
        "id",
        "account_id",
        "email",
        "at",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "expected_cost",
        "usage_missing",
        "message_id",
        "run_id",
        "operation",
    ]
    assert by_name["account_id"][1] == "YES"
    assert by_name["input_tokens"] == ("input_tokens", "YES", None)
    assert by_name["output_tokens"] == ("output_tokens", "YES", None)
    assert by_name["total_tokens"] == ("total_tokens", "YES", None)
    assert by_name["expected_cost"][1] == "YES"
    assert by_name["expected_cost"][2] is None
    assert by_name["usage_missing"] == ("usage_missing", "NO", None)
    assert by_name["run_id"] == ("run_id", "YES", None)
    assert by_name["id"][2] is not None
    assert "gen_random_uuid()" in by_name["id"][2]
    assert by_name["at"][2] is not None
    assert "now()" in by_name["at"][2]
    assert checks == [("usage_email_lowercase",), ("usage_operation_allowed",)]
    assert operation is not None
    for name in ("chat", "research", "schedule", "transcription", "browser"):
        assert name in operation[0]
    assert "summary" not in operation[0]
    assert message_fk is not None
    assert "FOREIGN KEY (message_id, account_id)" in message_fk[0]
    assert "REFERENCES message" in message_fk[0]
    assert "ON DELETE SET NULL (message_id)" in message_fk[0]
    assert "SET NULL (account_id" not in message_fk[0]
    assert all("run_id" not in definition[0] for definition in foreign_keys)
    index_by_name = {row[0]: row[1] for row in indexes}
    assert "usage_email" in index_by_name
    assert "usage_account_id_at" in index_by_name
    assert "usage_account_id_provider_model" in index_by_name
    assert "WHERE (account_id IS NOT NULL)" in index_by_name["usage_account_id_at"]
