import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.db import table_exists
from app.report_source import NewSource, SourceRow
from app.report_source import apply as apply_report_source
from app.run import apply as apply_run
from app.thread import apply as apply_thread

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "report.sql"

# id, account_id, thread_id, run_id, name, question, body, stop_note, status, content_type,
# byte_size, created_at
ReportRow = tuple[
    uuid.UUID,
    uuid.UUID,
    uuid.UUID | None,
    uuid.UUID,
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    datetime,
]

_COLUMNS = (
    "id, account_id, thread_id, run_id, name, question, body, stop_note, status, "
    "content_type, byte_size, created_at"
)
_FILENAME_LIMIT = 60


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "report"):
        return
    apply_run(connection, force)
    apply_thread(connection, force)
    connection.execute(_MIGRATION.read_text())


def create_report(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    run_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    name: str,
    question: str,
    body: str,
    stop_note: str,
    sources: Sequence[NewSource],
) -> ReportRow | None:
    # The caller holds the transaction, so the report and its sources commit together.
    apply_report_source(connection)
    draft: ReportRow = (
        uuid.uuid4(),
        account_id,
        thread_id,
        run_id,
        name,
        question,
        body,
        stop_note,
        "ready",
        "text/markdown",
        0,
        datetime.now(UTC),
    )
    rows: list[SourceRow] = [
        (position, source.kind, source.title, source.url, source.used_for, source.knowledge_file_id)
        for position, source in enumerate(sources, start=1)
    ]
    byte_size = len(render_download(draft, rows).encode("utf-8"))
    created = connection.execute(
        f"""
        INSERT INTO report (account_id, thread_id, run_id, name, question, body, stop_note,
                            byte_size)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id) DO NOTHING
        RETURNING {_COLUMNS}
        """,
        (account_id, thread_id, run_id, name, question, body, stop_note, byte_size),
    ).fetchone()
    if created is None:
        return None
    for position, source in enumerate(sources, start=1):
        connection.execute(
            """
            INSERT INTO report_source
                (account_id, report_id, position, kind, title, url, used_for, knowledge_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                account_id,
                created[0],
                position,
                source.kind,
                source.title,
                source.url,
                source.used_for,
                source.knowledge_file_id,
            ),
        )
    return created


def list_reports(connection: psycopg.Connection, account_id: uuid.UUID) -> list[ReportRow]:
    apply(connection)
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM report WHERE account_id = %s ORDER BY created_at DESC, id ASC",
        (account_id,),
    ).fetchall()
    return list(rows)


def get_report(
    connection: psycopg.Connection, account_id: uuid.UUID, report_id: uuid.UUID
) -> ReportRow | None:
    apply(connection)
    return connection.execute(
        f"SELECT {_COLUMNS} FROM report WHERE id = %s AND account_id = %s",
        (report_id, account_id),
    ).fetchone()


def rename_report(
    connection: psycopg.Connection, account_id: uuid.UUID, report_id: uuid.UUID, name: str
) -> ReportRow | None:
    apply(connection)
    return connection.execute(
        f"""
        UPDATE report
        SET name = %s
        WHERE id = %s
          AND account_id = %s
        RETURNING {_COLUMNS}
        """,
        (name, report_id, account_id),
    ).fetchone()


def delete_report(
    connection: psycopg.Connection, account_id: uuid.UUID, report_id: uuid.UUID
) -> bool:
    apply(connection)
    deleted = connection.execute(
        "DELETE FROM report WHERE id = %s AND account_id = %s RETURNING id",
        (report_id, account_id),
    ).fetchone()
    return deleted is not None


def render_download(report: ReportRow, sources: Sequence[SourceRow]) -> str:
    lines = [
        f"# {report[4]}",
        "",
        f"Question: {report[5]}",
        "",
        report[6],
        "",
        "## How this research stopped",
        "",
        report[7],
        "",
        "## Sources",
        "",
    ]
    for position, _kind, title, url, used_for, _file_id in sources:
        lines.append(f"{position}. {title}")
        if url:
            lines.append(f"   {url}")
        lines.append(f"   Used for: {used_for}")
    return "\n".join(lines) + "\n"


def download_filename(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:_FILENAME_LIMIT].strip("-")
    return f"{stem or 'report'}.md"
