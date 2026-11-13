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
