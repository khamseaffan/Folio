import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app import operations
from app.account import ensure_account
from app.db import connect
from app.file import begin_upload, complete_upload, get_file
from app.file_reader import PART_CHARS
from app.model import Image
from app.operations import NO_STORED_FILE, Context, agent_tools, call
from app.storage import FakeStorage


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"fileops-{uuid.uuid4().hex}@example.com"
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
    monkeypatch.setattr(operations, "open_storage", lambda: fake)
    return fake


def _upload(connection, storage, account_id, name: str, data: bytes) -> uuid.UUID:
    begun = begin_upload(connection, storage, account_id, name, len(data))
    storage.put(f"pending/{begun.file_id}", data)
    complete_upload(connection, storage, account_id, begun.file_id)
    return begun.file_id


def _call(connection, account_id, name, arguments, in_run=False):
    return call(connection, account_id, Context(None), name, arguments, in_run=in_run)


def test_the_three_tools_exist_and_scheduled_runs_cannot_delete() -> None:
    chat = {spec.name for spec in agent_tools()}
    scheduled = {spec.name for spec in agent_tools(in_run=True)}
    assert {"list_files", "read_file", "delete_file"} <= chat
    assert {"list_files", "read_file"} <= scheduled
    assert "delete_file" not in scheduled


def test_file_tool_inputs_never_hold_an_account_id() -> None:
    for spec in agent_tools():
        if spec.name in {"list_files", "read_file", "delete_file"}:
            assert "account" not in json.dumps(spec.schema)
            assert len(spec.description) > 30


def test_a_scheduled_run_is_refused_delete_file(connection, account, storage) -> None:
    account_id, _ = account
    file_id = _upload(connection, storage, account_id, "a.txt", b"hi")
    refused = _call(connection, account_id, "delete_file", {"file_id": str(file_id)}, in_run=True)
    assert refused["code"] == "unknown"
    assert get_file(connection, account_id, file_id) is not None


def test_list_files_shows_id_name_kind_type_and_size(connection, account, storage) -> None:
    account_id, _ = account
    file_id = _upload(connection, storage, account_id, "notes.md", b"# hello")
    listed = _call(connection, account_id, "list_files", {}, in_run=True)
    assert listed == {
        "ok": True,
        "files": [
            {
                "id": str(file_id),
                "name": "notes.md",
                "kind": "upload",
                "type": "text/markdown",
                "size": 7,
            }
        ],
    }


def test_list_files_leaves_out_other_accounts_and_unfinished_uploads(
    connection, account, other, storage
) -> None:
    account_id, _ = account
    other_id, _ = other
    _upload(connection, storage, other_id, "theirs.txt", b"x")
    begin_upload(connection, storage, account_id, "pending.txt", 5)
    assert _call(connection, account_id, "list_files", {})["files"] == []


def test_read_file_returns_text_with_part_numbers(connection, account, storage) -> None:
    account_id, _ = account
    file_id = _upload(connection, storage, account_id, "a.txt", b"hello there")
    result = _call(connection, account_id, "read_file", {"file_id": str(file_id)}, in_run=True)
    assert result == {
        "ok": True,
        "name": "a.txt",
        "part": 1,
        "parts": 1,
        "text": "hello there",
    }


def test_read_file_reads_a_long_file_in_successive_parts(connection, account, storage) -> None:
    account_id, _ = account
    body = ("a" * PART_CHARS) + "tail"
    file_id = _upload(connection, storage, account_id, "long.txt", body.encode())
    first = _call(connection, account_id, "read_file", {"file_id": str(file_id)})
    second = _call(connection, account_id, "read_file", {"file_id": str(file_id), "part": 2})
    assert (first["part"], first["parts"], len(first["text"])) == (1, 2, PART_CHARS)
    assert (second["part"], second["text"]) == (2, "tail")
    beyond = _call(connection, account_id, "read_file", {"file_id": str(file_id), "part": 3})
    assert beyond["ok"] is False and beyond["code"] == "invalid"
    assert "2 parts" in beyond["error"]


def test_read_file_of_an_image_carries_the_image_out_of_band(
    connection, account, storage
) -> None:
    import io

    from PIL import Image as PilImage

    account_id, _ = account
    out = io.BytesIO()
    PilImage.new("RGB", (4, 4), "red").save(out, format="PNG")
    file_id = _upload(connection, storage, account_id, "pic.png", out.getvalue())
    result = _call(connection, account_id, "read_file", {"file_id": str(file_id)})
    assert result["ok"] is True
    assert "text" not in result
    assert "image" in result["note"].lower()
    assert [image.media_type for image in result["images"]] == ["image/png"]
    encoded = operations.encode(result)
    assert "images" not in json.loads(encoded.text)
    assert encoded.images == (Image("image/png", out.getvalue()),)


def test_encode_of_a_plain_result_is_json_without_images() -> None:
    encoded = operations.encode({"ok": True})
    assert (encoded.text, encoded.images) == ('{"ok": true}', ())


def test_another_accounts_file_is_not_found_for_every_tool(
    connection, account, other, storage
) -> None:
    account_id, _ = account
    other_id, _ = other
    file_id = _upload(connection, storage, other_id, "theirs.txt", b"secret")
    for name in ("read_file", "delete_file"):
        result = _call(connection, account_id, name, {"file_id": str(file_id)})
        assert result == {"ok": False, "code": "not_found", "error": NO_STORED_FILE}
    assert get_file(connection, other_id, file_id) is not None
    assert storage.has(f"accounts/{other_id}/files/{file_id}/theirs.txt")


def test_a_bad_file_id_is_invalid_input(connection, account, storage) -> None:
    account_id, _ = account
    result = _call(connection, account_id, "read_file", {"file_id": "nope"})
    assert result["code"] == "invalid"
    assert _call(connection, account_id, "read_file", {"file_id": str(uuid.uuid4()), "part": 0})[
        "code"
    ] == "invalid"


def test_a_file_that_cannot_be_opened_is_a_plain_refusal(connection, account, storage) -> None:
    account_id, _ = account
    file_id = _upload(connection, storage, account_id, "bad.pdf", b"not a pdf")
    result = _call(connection, account_id, "read_file", {"file_id": str(file_id)})
    assert result["ok"] is False and result["code"] == "unreadable"
    assert "could not be opened" in result["error"]


def test_delete_file_removes_the_row_and_the_bytes(connection, account, storage) -> None:
    account_id, _ = account
    file_id = _upload(connection, storage, account_id, "a.txt", b"hi")
    result = _call(connection, account_id, "delete_file", {"file_id": str(file_id)})
    assert result == {"ok": True, "file_id": str(file_id)}
    assert get_file(connection, account_id, file_id) is None
    assert not storage.has(f"accounts/{account_id}/files/{file_id}/a.txt")
    again = _call(connection, account_id, "delete_file", {"file_id": str(file_id)})
    assert again["code"] == "not_found"


def test_deleting_a_file_leaves_the_messages_that_mentioned_it(
    connection, account, storage
) -> None:
    from app.message import insert_message, list_messages
    from app.thread import create_thread

    account_id, _ = account
    thread = create_thread(connection, account_id, "t")
    file_id = _upload(connection, storage, account_id, "a.txt", b"hi")
    insert_message(connection, account_id, thread[0], "person", "chat", f"see file {file_id}")
    _call(connection, account_id, "delete_file", {"file_id": str(file_id)})
    kept = list_messages(connection, account_id, thread[0])
    assert [row[6] for row in kept] == [f"see file {file_id}"]
