import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.db import table_exists
from app.email_limit import apply as apply_email_limit

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "ability.sql"

# id, account_id, name, body, created_at, updated_at
AbilityRow = tuple[uuid.UUID, uuid.UUID, str, str, datetime, datetime]

_COLUMNS = "id, account_id, name, body, created_at, updated_at"


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "ability"):
        return
    apply_email_limit(connection, force)
    apply_account(connection, force)
    connection.execute(_MIGRATION.read_text())


def list_abilities(connection: psycopg.Connection, account_id: uuid.UUID) -> list[AbilityRow]:
    apply(connection)
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM ability WHERE account_id = %s ORDER BY created_at ASC, id ASC",
        (account_id,),
    ).fetchall()
    return list(rows)


def create_ability(
    connection: psycopg.Connection, account_id: uuid.UUID, name: str, body: str
) -> AbilityRow:
    apply(connection)
    created = connection.execute(
        f"INSERT INTO ability (account_id, name, body) VALUES (%s, %s, %s) RETURNING {_COLUMNS}",
        (account_id, name, body),
    ).fetchone()
    if created is None:
        raise RuntimeError("ability was not stored")
    return created


def update_ability(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    ability_id: uuid.UUID,
    name: str,
    body: str,
) -> AbilityRow | None:
    apply(connection)
    return connection.execute(
        f"""
        UPDATE ability
        SET name = %s, body = %s, updated_at = now()
        WHERE id = %s AND account_id = %s
        RETURNING {_COLUMNS}
        """,
        (name, body, ability_id, account_id),
    ).fetchone()


def delete_ability(
    connection: psycopg.Connection, account_id: uuid.UUID, ability_id: uuid.UUID
) -> bool:
    apply(connection)
    deleted = connection.execute(
        "DELETE FROM ability WHERE id = %s AND account_id = %s RETURNING id",
        (ability_id, account_id),
    ).fetchone()
    return deleted is not None
