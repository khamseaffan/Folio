import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psycopg

from app.account import apply as apply_account
from app.db import table_exists
from app.storage import Storage

_MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "file.sql"

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ACCOUNT_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
STALE_HOURS = 24
_NAME_CHARS = 200

BAD_FILE_TYPE = (
    "That file type is not supported. Use a PDF, text, Markdown, CSV, XLSX, JPEG, PNG, "
    "WebP, or HEIC file."
)
EMPTY_FILE = "That file is empty."
TOO_BIG = "A file can be at most 50 MB."
FILES_FULL = "Your files are full. The limit is 5 GB. Ask Folio to delete some."
NOT_UPLOADED = "The file did not arrive in full. Try again."

# The type comes from the name, never from what the browser says.
TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
}

# id, account_id, kind, name, content_type, byte_size, status, created_at
FileRow = tuple[uuid.UUID, uuid.UUID, str, str, str, int, str, datetime]

_COLUMNS = "id, account_id, kind, name, content_type, byte_size, status, created_at"


class FileRefused(Exception):
    """A sentence for the person: why the file was not taken."""


@dataclass(frozen=True)
class Begun:
    file_id: uuid.UUID
    url: str
    content_type: str


def apply(connection: psycopg.Connection, force: bool = False) -> None:
    # CREATE statements take table locks, and held inside a request they block other writers.
    # The migration job passes force, so a change to an existing table still lands.
    if not force and table_exists(connection, "file"):
        return
    apply_account(connection, force)
    connection.execute(_MIGRATION.read_text())


def _pending_path(file_id: uuid.UUID) -> str:
    return f"pending/{file_id}"


def _final_path(account_id: uuid.UUID, file_id: uuid.UUID, name: str) -> str:
    return f"accounts/{account_id}/files/{file_id}/{name}"


def stored_path(row: FileRow) -> str:
    return _final_path(row[1], row[0], row[3])


def _clean_name(name: str) -> str:
    last = name.replace("\\", "/").split("/")[-1]
    cleaned = "".join(char for char in last if ord(char) >= 32 and char != "\x7f").strip()
    return cleaned[-_NAME_CHARS:]


def _content_type(name: str) -> str | None:
    return TYPES.get(Path(name).suffix.lower()) if Path(name).suffix else None


def byte_count(connection: psycopg.Connection, account_id: uuid.UUID) -> int:
    # Uploads and task files, including an upload whose bytes are still on the way. Knowledge
    # files and Drive copies are outside the count.
    apply(connection)
    row = connection.execute(
        """
        SELECT COALESCE(SUM(byte_size), 0)
        FROM file
        WHERE account_id = %s
          AND kind IN ('upload', 'task')
        """,
        (account_id,),
    ).fetchone()
    return int(row[0]) if row is not None else 0


def begin_upload(
    connection: psycopg.Connection,
    storage: Storage,
    account_id: uuid.UUID,
    name: str,
    size: int,
) -> Begun:
    clean = _clean_name(name)
    content_type = _content_type(clean)
    if clean == "" or content_type is None:
        raise FileRefused(BAD_FILE_TYPE)
    if size < 1:
        raise FileRefused(EMPTY_FILE)
    if size > MAX_UPLOAD_BYTES:
        raise FileRefused(TOO_BIG)
    apply(connection)
    # One at a time for an account, so two uploads cannot both pass the limit.
    connection.execute("SELECT 1 FROM account WHERE id = %s FOR UPDATE", (account_id,))
    if byte_count(connection, account_id) + size > ACCOUNT_LIMIT_BYTES:
        raise FileRefused(FILES_FULL)
    row = connection.execute(
        """
        INSERT INTO file (account_id, kind, name, content_type, byte_size, status)
        VALUES (%s, 'upload', %s, %s, %s, 'pending')
        RETURNING id
        """,
        (account_id, clean, content_type, size),
    ).fetchone()
    assert row is not None
    file_id = row[0]
    return Begun(file_id, storage.upload_url(_pending_path(file_id), content_type), content_type)


def complete_upload(
    connection: psycopg.Connection,
    storage: Storage,
    account_id: uuid.UUID,
    file_id: uuid.UUID,
) -> FileRow | None:
    apply(connection)
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM file WHERE id = %s AND account_id = %s FOR UPDATE",
        (file_id, account_id),
    ).fetchone()
    if row is None:
        return None
    if row[6] == "ready":
        return row
    arrived = storage.size(_pending_path(file_id))
    if arrived != row[5]:
        if arrived is not None:
            # Bytes of another size are not the file that was announced.
            storage.delete(_pending_path(file_id))
            connection.execute("DELETE FROM file WHERE id = %s", (file_id,))
            # The refusal below ends the request, and the row must not come back with it.
            connection.commit()
        raise FileRefused(NOT_UPLOADED)
    storage.move(_pending_path(file_id), _final_path(account_id, file_id, row[3]))
    connection.execute("UPDATE file SET status = 'ready' WHERE id = %s", (file_id,))
    return (*row[:6], "ready", row[7])


def list_files(connection: psycopg.Connection, account_id: uuid.UUID) -> list[FileRow]:
    apply(connection)
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS}
        FROM file
        WHERE account_id = %s
          AND status = 'ready'
        ORDER BY created_at DESC, id ASC
        """,
        (account_id,),
    ).fetchall()
    return list(rows)


def get_file(
    connection: psycopg.Connection, account_id: uuid.UUID, file_id: uuid.UUID
) -> FileRow | None:
    apply(connection)
    return connection.execute(
        f"SELECT {_COLUMNS} FROM file WHERE id = %s AND account_id = %s AND status = 'ready'",
        (file_id, account_id),
    ).fetchone()


def delete_file(
    connection: psycopg.Connection,
    storage: Storage,
    account_id: uuid.UUID,
    file_id: uuid.UUID,
) -> bool:
    row = get_file(connection, account_id, file_id)
    if row is None:
        return False
    storage.delete(_final_path(account_id, file_id, row[3]))
    connection.execute("DELETE FROM file WHERE id = %s", (file_id,))
    return True


def discard_stale(
    connection: psycopg.Connection, storage: Storage, hours: int = STALE_HOURS
) -> int:
    # Across accounts by design: an upload that never finished is nobody's file.
    apply(connection)
    rows = connection.execute(
        """
        SELECT id
        FROM file
        WHERE status = 'pending'
          AND created_at < now() - make_interval(hours => %s)
        """,
        (hours,),
    ).fetchall()
    for (file_id,) in rows:
        storage.delete(_pending_path(file_id))
        connection.execute("DELETE FROM file WHERE id = %s", (file_id,))
    return len(rows)


def delete_account_bytes(
    storage: Storage, account_id: uuid.UUID, connection: psycopg.Connection | None = None
) -> None:
    """Remove the person's whole folder, and with a connection also their unfinished uploads."""
    if connection is not None:
        apply(connection)
        rows = connection.execute(
            "SELECT id FROM file WHERE account_id = %s AND status = 'pending'", (account_id,)
        ).fetchall()
        for (file_id,) in rows:
            storage.delete(_pending_path(file_id))
    storage.delete_prefix(f"accounts/{account_id}/")
