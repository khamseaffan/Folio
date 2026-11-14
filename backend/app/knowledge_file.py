import uuid
from datetime import datetime
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.db import table_exists
from app.email_limit import apply as apply_email_limit

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "knowledge_file.sql"

PROFILE = "profile.md"

# id, account_id, name, body, byte_size, created_at, updated_at
KnowledgeRow = tuple[uuid.UUID, uuid.UUID, str, str, int, datetime, datetime]

_COLUMNS = "id, account_id, name, body, byte_size, created_at, updated_at"


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "knowledge_file"):
        return
    apply_email_limit(connection, force)
    apply_account(connection, force)
    connection.execute(_MIGRATION.read_text())


def list_files(connection: psycopg.Connection, account_id: uuid.UUID) -> list[KnowledgeRow]:
    apply(connection)
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM knowledge_file WHERE account_id = %s ORDER BY name ASC",
        (account_id,),
    ).fetchall()
    return list(rows)


def get_file(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    name: str,
    lock: bool = False,
) -> KnowledgeRow | None:
    apply(connection)
    if lock:
        # A first write has no row to lock, so the name is locked for the transaction instead.
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{account_id}:{name}",),
        )
    suffix = " FOR UPDATE" if lock else ""
    return connection.execute(
        f"SELECT {_COLUMNS} FROM knowledge_file WHERE account_id = %s AND name = %s{suffix}",
        (account_id, name),
    ).fetchone()


def get_file_by_id(
    connection: psycopg.Connection, account_id: uuid.UUID, file_id: uuid.UUID
) -> KnowledgeRow | None:
    apply(connection)
    return connection.execute(
        f"SELECT {_COLUMNS} FROM knowledge_file WHERE account_id = %s AND id = %s",
        (account_id, file_id),
    ).fetchone()


def put_file(
    connection: psycopg.Connection, account_id: uuid.UUID, name: str, body: str
) -> KnowledgeRow:
    apply(connection)
    stored = connection.execute(
        f"""
        INSERT INTO knowledge_file (account_id, name, body, byte_size)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (account_id, name)
        DO UPDATE SET body = EXCLUDED.body, byte_size = EXCLUDED.byte_size, updated_at = now()
        RETURNING {_COLUMNS}
        """,
        (account_id, name, body, len(body.encode("utf-8"))),
    ).fetchone()
    if stored is None:
        raise RuntimeError("knowledge file was not stored")
    return stored


def delete_file(connection: psycopg.Connection, account_id: uuid.UUID, name: str) -> bool:
    apply(connection)
    deleted = connection.execute(
        "DELETE FROM knowledge_file WHERE account_id = %s AND name = %s RETURNING id",
        (account_id, name),
    ).fetchone()
    return deleted is not None


def delete_all(connection: psycopg.Connection, account_id: uuid.UUID) -> int:
    apply(connection)
    cursor = connection.execute("DELETE FROM knowledge_file WHERE account_id = %s", (account_id,))
    return cursor.rowcount


def search_files(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    query: str,
    limit: int = 5,
) -> list[tuple[str, str]]:
    apply(connection)
    # Any word may match, and a file that matches more words ranks higher. The text is only
    # ever a parameter. The name counts too, with punctuation turned to spaces so that
    # "travel.md" is not read as one host name.
    rows = connection.execute(
        """
        WITH q AS (
            SELECT replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery AS query
        )
        SELECT name,
               ts_headline('english', body, q.query, 'MaxWords=30, MinWords=10')
        FROM knowledge_file, q
        WHERE account_id = %s
          AND (
              to_tsvector('english', body) @@ q.query
              OR to_tsvector('english', regexp_replace(name, '[^[:alnum:]]+', ' ', 'g'))
                 @@ q.query
          )
        ORDER BY ts_rank(to_tsvector('english', body), q.query)
                 + ts_rank(
                     to_tsvector('english', regexp_replace(name, '[^[:alnum:]]+', ' ', 'g')),
                     q.query
                 ) DESC,
                 name ASC
        LIMIT %s
        """,
        (query, account_id, limit),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]
