from pathlib import Path

import psycopg

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "email_limit.sql"


def apply(connection: psycopg.Connection) -> None:
    connection.execute(_MIGRATION.read_text())


def insert_if_absent(connection: psycopg.Connection, email: str) -> None:
    connection.execute(
        """
        INSERT INTO email_limit (email)
        VALUES (%s)
        ON CONFLICT (email) DO NOTHING
        """,
        (email.lower(),),
    )
