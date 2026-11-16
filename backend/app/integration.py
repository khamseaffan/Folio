import uuid
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.db import table_exists

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "integration.sql"

GOOGLE_DRIVE = "google_drive"


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "integration"):
        return
    apply_account(connection, force)
    connection.execute(_MIGRATION.read_text())


def save_integration(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    provider: str,
    secret: bytes,
    label: str | None,
) -> None:
    apply(connection)
    connection.execute(
        """
        INSERT INTO integration (account_id, provider, secret, label)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (account_id, provider)
        DO UPDATE SET secret = EXCLUDED.secret, label = EXCLUDED.label, created_at = now()
        """,
        (account_id, provider, secret, label),
    )


def load_integration(
    connection: psycopg.Connection, account_id: uuid.UUID, provider: str
) -> tuple[bytes, str | None] | None:
    """The encrypted secret and the label, or None when the person has not connected."""
    apply(connection)
    row = connection.execute(
        "SELECT secret, label FROM integration WHERE account_id = %s AND provider = %s",
        (account_id, provider),
    ).fetchone()
    if row is None:
        return None
    return bytes(row[0]), row[1]


def remove_integration(
    connection: psycopg.Connection, account_id: uuid.UUID, provider: str
) -> None:
    apply(connection)
    connection.execute(
        "DELETE FROM integration WHERE account_id = %s AND provider = %s",
        (account_id, provider),
    )
