from pathlib import Path

import psycopg

from app.db import table_exists

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "email_limit.sql"


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "email_limit"):
        return
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
