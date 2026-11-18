import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.file import (
    ACCOUNT_LIMIT_BYTES,
    BAD_FILE_TYPE,
    EMPTY_FILE,
    FILES_FULL,
    MAX_UPLOAD_BYTES,
    TOO_BIG,
    FileRefused,
    byte_count,
    create_task_file,
    delete_file,
    get_file,
    list_files,
)
from app.operations import Context, agent_tools, call
from app.storage import FakeStorage


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"file-write-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def other() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr("app.operations.open_storage", lambda: fake)
    return fake


def _insert(connection, account_id, kind, size):
    connection.execute(
        """
        INSERT INTO file (account_id, kind, name, content_type, byte_size, status)
        VALUES (%s, %s, 'big.bin', 'application/pdf', %s, 'ready')
        """,
        (account_id, kind, size),
    )


def test_a_task_file_is_stored_under_the_account_and_listed_as_ready(connection, account) -> None:
    account_id, _ = account
    storage = FakeStorage()

    row = create_task_file(connection, storage, account_id, "plan.md", "text/markdown", b"# Plan\n")

    assert row[1] == account_id and row[2] == "task" and row[3] == "plan.md"
    assert row[4] == "text/markdown" and row[5] == 7 and row[6] == "ready"
    assert storage.objects[f"accounts/{account_id}/files/{row[0]}/plan.md"] == b"# Plan\n"
    assert list_files(connection, account_id) == [row]
    assert byte_count(connection, account_id) == 7


def test_a_task_file_can_be_deleted_like_any_file(connection, account) -> None:
    account_id, _ = account
    storage = FakeStorage()
    row = create_task_file(connection, storage, account_id, "a.txt", "text/plain", b"hi")

    assert delete_file(connection, storage, account_id, row[0]) is True
    assert storage.objects == {}
    assert byte_count(connection, account_id) == 0


def test_the_name_is_cleaned_like_an_upload(connection, account) -> None:
    account_id, _ = account
    storage = FakeStorage()

    row = create_task_file(
        connection, storage, account_id, "../../etc/pass\x00wd.txt", "text/plain", b"x"
    )

    assert row[3] == "passwd.txt"


def test_only_the_four_task_types_are_taken_and_they_must_match_the_name(
    connection, account
) -> None:
    account_id, _ = account
    storage = FakeStorage()
    kinds = {
        "a.md": "text/markdown",
        "a.txt": "text/plain",
        "a.csv": "text/csv",
        "a.PDF": "application/pdf",
    }
    for name, content_type in kinds.items():
        assert create_task_file(connection, storage, account_id, name, content_type, b"x")
    for name, content_type in (
        ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.png", "image/png"),
        ("a.exe", "application/octet-stream"),
        ("noextension", "text/plain"),
        ("", "text/plain"),
        ("a.md", "text/plain"),
    ):
        with pytest.raises(FileRefused, match=BAD_FILE_TYPE):
            create_task_file(connection, storage, account_id, name, content_type, b"x")
    assert len(storage.objects) == 4


def test_empty_and_oversize_files_are_refused_and_50_mb_exactly_is_taken(
    connection, account
) -> None:
    account_id, _ = account
    storage = FakeStorage()
    with pytest.raises(FileRefused, match=EMPTY_FILE):
        create_task_file(connection, storage, account_id, "a.txt", "text/plain", b"")
    with pytest.raises(FileRefused, match=TOO_BIG):
        create_task_file(
            connection, storage, account_id, "a.txt", "text/plain", b"x" * (MAX_UPLOAD_BYTES + 1)
        )
    assert storage.objects == {}
    big = b"x" * MAX_UPLOAD_BYTES
    row = create_task_file(connection, storage, account_id, "a.txt", "text/plain", big)
    assert row[5] == MAX_UPLOAD_BYTES


def test_the_account_limit_counts_what_is_stored_and_a_refusal_stores_nothing(
    connection, account
) -> None:
    account_id, _ = account
    storage = FakeStorage()
    _insert(connection, account_id, "upload", ACCOUNT_LIMIT_BYTES - 10)

    with pytest.raises(FileRefused, match=FILES_FULL):
        create_task_file(connection, storage, account_id, "a.txt", "text/plain", b"x" * 11)
    assert storage.objects == {}
    assert create_task_file(connection, storage, account_id, "a.txt", "text/plain", b"x" * 10)
    assert byte_count(connection, account_id) == ACCOUNT_LIMIT_BYTES


def test_a_file_written_for_one_account_is_invisible_to_another(connection, account, other) -> None:
    account_id, _ = account
    other_id, _ = other
    storage = FakeStorage()
    row = create_task_file(connection, storage, account_id, "mine.txt", "text/plain", b"secret")

    assert list_files(connection, other_id) == []
    assert get_file(connection, other_id, row[0]) is None
    assert delete_file(connection, storage, other_id, row[0]) is False
    assert byte_count(connection, other_id) == 0
    assert byte_count(connection, account_id) == 6
    assert len(storage.objects) == 1


def test_one_accounts_full_files_do_not_block_another(connection, account, other) -> None:
    account_id, _ = account
    other_id, _ = other
    storage = FakeStorage()
    _insert(connection, account_id, "task", ACCOUNT_LIMIT_BYTES)

    with pytest.raises(FileRefused, match=FILES_FULL):
        create_task_file(connection, storage, account_id, "a.txt", "text/plain", b"x")
    assert create_task_file(connection, storage, other_id, "a.txt", "text/plain", b"x")


def _write(connection, account_id, **arguments):
    return call(connection, account_id, Context(None), "write_file", arguments)


def test_write_file_stores_the_text_with_the_right_extension(connection, account, storage) -> None:
    account_id, _ = account

    result = _write(connection, account_id, name="notes", content="# Hi", format="md")

    assert result["ok"] is True and result["name"] == "notes.md"
    file_id = uuid.UUID(result["file_id"])
    row = get_file(connection, account_id, file_id)
    assert row is not None and row[2] == "task" and row[4] == "text/markdown"
    assert storage.objects[f"accounts/{account_id}/files/{file_id}/notes.md"] == b"# Hi"


def test_write_file_keeps_an_extension_that_is_already_there(connection, account, storage) -> None:
    account_id, _ = account

    kept = _write(connection, account_id, name="data.CSV", content="a,b", format="csv")
    other = _write(connection, account_id, name="x.md", content="t", format="txt")

    assert kept["name"] == "data.CSV"
    assert other["name"] == "x.md.txt"


def test_write_file_makes_a_pdf(connection, account, storage) -> None:
    account_id, _ = account

    text = "Quarterly numbers"
    result = _write(connection, account_id, name="report", content=text, format="pdf")

    assert result["name"] == "report.pdf"
    data = storage.objects[f"accounts/{account_id}/files/{result['file_id']}/report.pdf"]
    assert data.startswith(b"%PDF") and b"Quarterly numbers" in data
    row = get_file(connection, account_id, uuid.UUID(result["file_id"]))
    assert row is not None and row[4] == "application/pdf" and row[5] == len(data)


def test_write_file_refusals_come_back_as_sentences(connection, account, storage) -> None:
    account_id, _ = account
    _insert(connection, account_id, "task", ACCOUNT_LIMIT_BYTES)

    full = _write(connection, account_id, name="a", content="x", format="txt")
    assert full == {"ok": False, "code": "limit", "error": FILES_FULL}

    empty = _write(connection, account_id, name="a", content="", format="txt")
    assert empty["ok"] is False and empty["error"] == EMPTY_FILE

    huge = "x" * (MAX_UPLOAD_BYTES + 1)
    big = _write(connection, account_id, name="b", content=huge, format="txt")
    assert big["ok"] is False and big["error"] == TOO_BIG


def test_write_file_refuses_a_format_that_is_not_offered_and_a_bad_name(
    connection, account, storage
) -> None:
    account_id, _ = account

    assert _write(connection, account_id, name="a", content="x", format="exe")["code"] == "invalid"
    assert _write(connection, account_id, name="a", content="x", format="xlsx")["code"] == "invalid"
    assert _write(connection, account_id, name="  ", content="x", format="txt")["code"] == "invalid"
    assert _write(connection, account_id, name="a", content="x\x00", format="txt")["code"] == (
        "invalid"
    )
    assert storage.objects == {}


def test_write_file_is_an_agent_tool_in_chat_and_in_runs_and_takes_no_account_id() -> None:
    for in_run in (False, True):
        specs = {spec.name: spec for spec in agent_tools(in_run=in_run)}
        assert "write_file" in specs
    spec = {spec.name: spec for spec in agent_tools()}["write_file"]
    assert "account_id" not in json.dumps(spec.schema)
    assert set(spec.schema["properties"]) == {"name", "content", "format"}
    assert "only" in spec.description.lower() and "thread" in spec.description.lower()


def test_write_file_works_in_a_scheduled_run(connection, account, storage) -> None:
    account_id, _ = account

    result = call(
        connection,
        account_id,
        Context(None),
        "write_file",
        {"name": "run", "content": "done", "format": "txt"},
        in_run=True,
    )

    assert result["ok"] is True


def test_a_file_written_by_the_tool_for_one_account_is_not_seen_by_another(
    connection, account, other, storage
) -> None:
    account_id, _ = account
    other_id, _ = other
    _write(connection, account_id, name="mine", content="secret", format="txt")

    assert list_files(connection, other_id) == []
    assert byte_count(connection, other_id) == 0
    assert byte_count(connection, account_id) == 6
