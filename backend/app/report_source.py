import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.db import table_exists
from app.knowledge_file import apply as apply_knowledge_file

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "report_source.sql"

# position, kind, title, url, used_for, knowledge_file_id
SourceRow = tuple[int, str, str, str | None, str, uuid.UUID | None]


@dataclass(frozen=True)
class NewSource:
    kind: str
    title: str
    url: str | None
    used_for: str
    knowledge_file_id: uuid.UUID | None = None


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "report_source"):
        return
    # Imported here because report imports this module for NewSource.
    from app.report import apply as apply_report

    apply_report(connection, force)
    apply_knowledge_file(connection, force)
    connection.execute(_MIGRATION.read_text())


def list_sources(
    connection: psycopg.Connection, account_id: uuid.UUID, report_id: uuid.UUID
) -> list[SourceRow]:
    apply(connection)
    rows = connection.execute(
        """
        SELECT position, kind, title, url, used_for, knowledge_file_id
        FROM report_source
        WHERE account_id = %s
          AND report_id = %s
        ORDER BY position ASC
        """,
        (account_id, report_id),
    ).fetchall()
    return list(rows)
