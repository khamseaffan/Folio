import uuid
from pathlib import Path

import psycopg

from app import email_limit
from app.db import connect

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "account.sql"


def apply(connection: psycopg.Connection) -> None:
    connection.execute(_MIGRATION.read_text())


def ensure_account(firebase_uid: str, email: str) -> uuid.UUID:
    stored_email = email.lower()
    with connect() as connection:
        with connection.transaction():
            email_limit.apply(connection)
            apply(connection)
            email_limit.insert_if_absent(connection, stored_email)
            created = connection.execute(
                """
                INSERT INTO account (email, firebase_uid)
                VALUES (%s, %s)
                ON CONFLICT (email) DO NOTHING
                RETURNING id
                """,
                (stored_email, firebase_uid),
            ).fetchone()
            if created is None:
                created = connection.execute(
                    "SELECT id FROM account WHERE email = %s",
                    (stored_email,),
                ).fetchone()
            if created is None:
                raise RuntimeError("account was not stored")
            account_id = created[0]
    return account_id


class _SessionClosed(Exception):
    pass


def set_display_name(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    display_name: str,
) -> tuple[str, str] | None:
    try:
        with connect() as connection:
            with connection.transaction():
                named = connection.execute(
                    """
                    UPDATE account
                    SET display_name = %s
                    WHERE id = %s
                    RETURNING email, display_name
                    """,
                    (display_name, account_id),
                ).fetchone()
                if named is None or not isinstance(named[1], str):
                    raise RuntimeError("account is missing")
                active = connection.execute(
                    """
                    UPDATE session
                    SET last_active_at = now()
                    WHERE id = %s
                      AND account_id = %s
                      AND ended_at IS NULL
                    RETURNING id
                    """,
                    (session_id, account_id),
                ).fetchone()
                if active is None:
                    raise _SessionClosed()
                email = named[0]
                stored_name = named[1]
        return email, stored_name
    except _SessionClosed:
        return None


def load_account(account_id: uuid.UUID) -> tuple[str, str | None]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT email, display_name
            FROM account
            WHERE id = %s
            """,
            (account_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError("account is missing")
    return row[0], row[1]


def load_firebase_uid(account_id: uuid.UUID) -> str | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT firebase_uid FROM account WHERE id = %s",
            (account_id,),
        ).fetchone()
    if row is None:
        return None
    return row[0]


def delete_account(account_id: uuid.UUID) -> bool:
    with connect() as connection:
        deleted = connection.execute(
            "DELETE FROM account WHERE id = %s RETURNING id",
            (account_id,),
        ).fetchone()
    return deleted is not None
