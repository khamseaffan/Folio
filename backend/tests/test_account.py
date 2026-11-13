import uuid
from datetime import datetime

from app.account import ensure_account
from app.db import connect
from app.email_limit import apply as apply_email_limit


def _remove(email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if account is not None and account[0] is not None:
            connection.execute(
                "DELETE FROM account WHERE lower(email) = %s",
                (stored,),
            )
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
        if limit is not None and limit[0] is not None:
            connection.execute(
                "DELETE FROM email_limit WHERE lower(email) = %s",
                (stored,),
            )


def test_first_create_writes_both_rows():
    email = "First.Account@Example.com"
    stored = email.lower()
    firebase_uid = "Firebase-UID-1"
    _remove(email)
    try:
        account_id = ensure_account(firebase_uid, email)

        assert isinstance(account_id, uuid.UUID)
        with connect() as connection:
            limit = connection.execute(
                """
                SELECT email, token_cap, stopped
                FROM email_limit
                WHERE email = %s
                """,
                (stored,),
            ).fetchone()
            account = connection.execute(
                """
                SELECT id, email, firebase_uid, display_name, created_at
                FROM account
                WHERE id = %s
                """,
                (account_id,),
            ).fetchone()
            columns = connection.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN ('account', 'email_limit')
                ORDER BY table_name, ordinal_position
                """
            ).fetchall()

        assert limit == (stored, 10_000_000, False)
        assert account is not None
        found_id, found_email, found_uid, display_name, created_at = account
        assert found_id == account_id
        assert found_email == stored
        assert found_uid == firebase_uid
        assert display_name is None
        assert isinstance(created_at, datetime)
        assert created_at.tzinfo is not None
        assert columns == [
            ("account", "id"),
            ("account", "email"),
            ("account", "firebase_uid"),
            ("account", "display_name"),
            ("account", "created_at"),
            ("email_limit", "email"),
            ("email_limit", "token_cap"),
            ("email_limit", "stopped"),
        ]
    finally:
        _remove(email)


def test_second_call_returns_the_same_account():
    email = "Second.Call@Example.com"
    stored = email.lower()
    _remove(email)
    try:
        first = ensure_account("uid-first", email)
        second = ensure_account("uid-second", email)

        assert second == first
        with connect() as connection:
            accounts = connection.execute(
                """
                SELECT id, firebase_uid
                FROM account
                WHERE email = %s
                """,
                (stored,),
            ).fetchall()
        assert accounts == [(first, "uid-first")]
    finally:
        _remove(email)


def test_existing_email_limit_is_kept():
    email = "Kept.Cap@Example.com"
    stored = email.lower()
    _remove(email)
    try:
        with connect() as connection:
            apply_email_limit(connection)
            connection.execute(
                """
                INSERT INTO email_limit (email, token_cap, stopped)
                VALUES (%s, %s, %s)
                """,
                (stored, 42, True),
            )
        account_id = ensure_account("uid-kept", email)
        with connect() as connection:
            limit = connection.execute(
                """
                SELECT token_cap, stopped
                FROM email_limit
                WHERE email = %s
                """,
                (stored,),
            ).fetchone()
            account = connection.execute(
                """
                SELECT id, email, display_name
                FROM account
                WHERE email = %s
                """,
                (stored,),
            ).fetchone()
        assert limit == (42, True)
        assert account == (account_id, stored, None)
    finally:
        _remove(email)


def test_session_table_is_not_created():
    email = "No.Session@Example.com"
    _remove(email)
    with connect() as connection:
        connection.execute("DROP TABLE IF EXISTS public.session")
    try:
        ensure_account("uid-session", email)
        with connect() as connection:
            row = connection.execute("SELECT to_regclass('public.session')").fetchone()
        assert row == (None,)
    finally:
        _remove(email)
