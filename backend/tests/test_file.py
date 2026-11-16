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
    NOT_UPLOADED,
    TOO_BIG,
    FileRefused,
    begin_upload,
    byte_count,
    complete_upload,
    delete_account_bytes,
    delete_file,
    discard_stale,
    get_file,
    list_files,
)
from app.storage import FakeStorage

MB = 1024 * 1024


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"file-{uuid.uuid4().hex}@example.com"
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
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


def _upload(connection, storage, account_id, name="notes.pdf", data=b"x" * 2000):
    begun = begin_upload(connection, storage, account_id, name, len(data))
    storage.put(f"pending/{begun.file_id}", data)
    return begun, complete_upload(connection, storage, account_id, begun.file_id)


def _insert(connection, account_id, kind, size, status="ready"):
    return connection.execute(
        """
        INSERT INTO file (account_id, kind, name, content_type, byte_size, status)
        VALUES (%s, %s, 'big.bin', 'application/pdf', %s, %s) RETURNING id
        """,
        (account_id, kind, size, status),
    ).fetchone()[0]


def test_an_upload_is_pending_until_its_bytes_arrive_and_counts_from_the_start(
    connection, account, storage
):
    account_id, _ = account
    begun = begin_upload(connection, storage, account_id, "notes.pdf", 2000)

    assert begun.url == storage.upload_url(f"pending/{begun.file_id}", "application/pdf")
    assert begun.content_type == "application/pdf"
    assert byte_count(connection, account_id) == 2000
    assert list_files(connection, account_id) == []
    assert get_file(connection, account_id, begun.file_id) is None


def test_completing_moves_the_bytes_to_the_accounts_folder_and_lists_the_file(
    connection, account, storage
):
    account_id, _ = account
    begun, row = _upload(connection, storage, account_id)

    assert row is not None and row[2] == "upload" and row[3] == "notes.pdf" and row[6] == "ready"
    path = f"accounts/{account_id}/files/{begun.file_id}/notes.pdf"
    assert storage.has(path) and not storage.has(f"pending/{begun.file_id}")
    assert byte_count(connection, account_id) == 2000
    assert [item[0] for item in list_files(connection, account_id)] == [begun.file_id]
    assert get_file(connection, account_id, begun.file_id) == row


def test_a_file_that_never_arrived_stays_pending_and_is_refused(connection, account, storage):
    account_id, _ = account
    begun = begin_upload(connection, storage, account_id, "notes.pdf", 2000)

    with pytest.raises(FileRefused, match=NOT_UPLOADED):
        complete_upload(connection, storage, account_id, begun.file_id)
    assert byte_count(connection, account_id) == 2000


def test_bytes_of_another_size_are_thrown_away_and_stop_counting(connection, account, storage):
    account_id, _ = account
    begun = begin_upload(connection, storage, account_id, "notes.pdf", 2000)
    storage.put(f"pending/{begun.file_id}", b"y" * 1500)

    with pytest.raises(FileRefused, match=NOT_UPLOADED):
        complete_upload(connection, storage, account_id, begun.file_id)
    assert not storage.has(f"pending/{begun.file_id}")
    assert byte_count(connection, account_id) == 0


def test_completing_twice_gives_the_same_file(connection, account, storage):
    account_id, _ = account
    begun, row = _upload(connection, storage, account_id)

    assert complete_upload(connection, storage, account_id, begun.file_id) == row


def test_only_the_listed_types_are_taken_and_the_type_comes_from_the_name(
    connection, account, storage
):
    account_id, _ = account
    taken = ("a.pdf", "a.TXT", "a.md", "a.csv", "a.xlsx", "a.jpg", "a.jpeg", "a.png", "a.webp")
    for name in (*taken, "a.heic"):
        assert begin_upload(connection, storage, account_id, name, 10).file_id
    for name in ("a.exe", "a.zip", "noextension", "a.pdf.exe"):
        with pytest.raises(FileRefused, match=BAD_FILE_TYPE):
            begin_upload(connection, storage, account_id, name, 10)
    photo = begin_upload(connection, storage, account_id, "photo.JPG", 10)
    assert photo.content_type == "image/jpeg"


def test_empty_and_oversize_files_are_refused_and_50_mb_exactly_is_taken(
    connection, account, storage
):
    account_id, _ = account
    with pytest.raises(FileRefused, match=EMPTY_FILE):
        begin_upload(connection, storage, account_id, "a.pdf", 0)
    with pytest.raises(FileRefused, match=TOO_BIG):
        begin_upload(connection, storage, account_id, "a.pdf", MAX_UPLOAD_BYTES + 1)
    assert begin_upload(connection, storage, account_id, "a.pdf", MAX_UPLOAD_BYTES).file_id


def test_a_name_is_cut_to_its_last_part_and_cleaned(connection, account, storage):
    account_id, _ = account
    begun = begin_upload(connection, storage, account_id, "../../etc/pass\x00wd.txt", 10)
    storage.put(f"pending/{begun.file_id}", b"x" * 10)

    row = complete_upload(connection, storage, account_id, begun.file_id)

    assert row[3] == "passwd.txt"
    assert storage.has(f"accounts/{account_id}/files/{begun.file_id}/passwd.txt")


def test_the_account_limit_counts_uploads_task_files_and_pending_but_not_drive_copies(
    connection, account, storage
):
    account_id, _ = account
    _insert(connection, account_id, "drive_copy", 3 * 1024 * MB)
    _insert(connection, account_id, "task", ACCOUNT_LIMIT_BYTES - 40 * MB)
    assert byte_count(connection, account_id) == ACCOUNT_LIMIT_BYTES - 40 * MB

    with pytest.raises(FileRefused, match=FILES_FULL):
        begin_upload(connection, storage, account_id, "a.pdf", 41 * MB)
    pending = begin_upload(connection, storage, account_id, "b.pdf", 40 * MB)
    assert byte_count(connection, account_id) == ACCOUNT_LIMIT_BYTES
    with pytest.raises(FileRefused, match=FILES_FULL):
        begin_upload(connection, storage, account_id, "c.pdf", 1)
    assert pending.file_id


def test_deleting_a_file_removes_its_bytes_and_lowers_the_count(connection, account, storage):
    account_id, _ = account
    begun, _row = _upload(connection, storage, account_id)

    assert delete_file(connection, storage, account_id, begun.file_id) is True

    assert byte_count(connection, account_id) == 0
    assert list_files(connection, account_id) == []
    assert not storage.has(f"accounts/{account_id}/files/{begun.file_id}/notes.pdf")
    assert delete_file(connection, storage, account_id, begun.file_id) is False


def test_another_account_cannot_see_complete_or_delete_a_file(connection, account, storage):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        begun, _row = _upload(connection, storage, account_id)
        pending = begin_upload(connection, storage, account_id, "p.pdf", 10)
        storage.put(f"pending/{pending.file_id}", b"x" * 10)

        assert list_files(connection, other_id) == []
        assert get_file(connection, other_id, begun.file_id) is None
        assert complete_upload(connection, storage, other_id, pending.file_id) is None
        assert delete_file(connection, storage, other_id, begun.file_id) is False
        assert byte_count(connection, other_id) == 0
        assert len(list_files(connection, account_id)) == 1
    finally:
        _remove(other_id, other_email)


def test_an_unfinished_upload_is_discarded_after_24_hours_and_the_count_drops(
    connection, account, storage
):
    account_id, _ = account
    old = begin_upload(connection, storage, account_id, "old.pdf", 100)
    fresh = begin_upload(connection, storage, account_id, "new.pdf", 200)
    kept, _row = _upload(connection, storage, account_id)
    storage.put(f"pending/{old.file_id}", b"x" * 100)
    connection.execute(
        "UPDATE file SET created_at = now() - interval '25 hours' WHERE id = %s", (old.file_id,)
    )

    assert discard_stale(connection, storage) == 1

    assert not storage.has(f"pending/{old.file_id}")
    assert byte_count(connection, account_id) == 2200
    still = connection.execute("SELECT count(*) FROM file WHERE id = %s", (fresh.file_id,))
    assert still.fetchone() == (1,)
    assert get_file(connection, account_id, kept.file_id) is not None


def test_deleting_the_account_removes_every_row_and_its_whole_folder_but_no_other(
    connection, account, storage
):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        begun, _row = _upload(connection, storage, account_id)
        theirs, _their_row = _upload(connection, storage, other_id, "other.pdf")
        mine = f"accounts/{account_id}/files/{begun.file_id}/notes.pdf"
        other = f"accounts/{other_id}/files/{theirs.file_id}/other.pdf"
        assert storage.has(mine) and storage.has(other)

        delete_account_bytes(storage, account_id)
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))

        assert not storage.has(mine) and storage.has(other)
        count = connection.execute(
            "SELECT count(*) FROM file WHERE account_id = %s", (account_id,)
        ).fetchone()
        assert count == (0,)
    finally:
        _remove(other_id, other_email)
