import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.knowledge_file import (
    apply,
    delete_all,
    delete_file,
    get_file,
    get_file_by_id,
    list_files,
    put_file,
    search_files,
)


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"kf-{uuid.uuid4().hex}@sakana.ai"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
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


def test_put_creates_then_replaces_and_tracks_the_size(connection, account) -> None:
    account_id, _ = account
    first = put_file(connection, account_id, "notes.md", "héllo")
    assert first[4] == 6
    second = put_file(connection, account_id, "notes.md", "x")
    assert second[0] == first[0]
    assert second[3] == "x"
    assert second[4] == 1
    assert second[6] >= first[6]
    assert second[5] == first[5]
    assert [row[2] for row in list_files(connection, account_id)] == ["notes.md"]


def test_list_is_ordered_by_name_and_get_finds_by_name_and_id(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "b.md", "two")
    created = put_file(connection, account_id, "a.md", "one")
    assert [row[2] for row in list_files(connection, account_id)] == ["a.md", "b.md"]
    assert get_file(connection, account_id, "a.md") == created
    assert get_file_by_id(connection, account_id, created[0]) == created
    assert get_file(connection, account_id, "missing.md") is None
    assert get_file_by_id(connection, account_id, uuid.uuid4()) is None


def test_names_are_unique_per_account_not_across_accounts(connection, account, other) -> None:
    account_id, _ = account
    first = put_file(connection, account_id, "same.md", "mine")
    second = put_file(connection, other[0], "same.md", "theirs")
    assert first[0] != second[0]
    assert get_file(connection, account_id, "same.md")[3] == "mine"
    assert get_file(connection, other[0], "same.md")[3] == "theirs"
    with pytest.raises(psycopg.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO knowledge_file (account_id, name, body, byte_size) "
            "VALUES (%s, 'same.md', 'x', 1)",
            (account_id,),
        )


def test_another_account_cannot_get_delete_or_search_the_file(connection, account, other) -> None:
    account_id, _ = account
    created = put_file(connection, account_id, "secret.md", "The passport is blue.")
    assert get_file(connection, other[0], "secret.md") is None
    assert get_file_by_id(connection, other[0], created[0]) is None
    assert delete_file(connection, other[0], "secret.md") is False
    assert search_files(connection, other[0], "passport") == []
    assert list_files(connection, other[0]) == []
    assert get_file(connection, account_id, "secret.md") == created


def test_search_ranks_the_better_match_first_and_returns_a_snippet(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "once.md", "I have a passport and many other documents.")
    put_file(
        connection, account_id, "thrice.md", "Passport renewal. The passport is blue. Passport."
    )
    put_file(connection, account_id, "other.md", "Nothing relevant here.")
    found = search_files(connection, account_id, "passport")
    assert [name for name, _ in found] == ["thrice.md", "once.md"]
    assert all("assport" in snippet for _, snippet in found)


def test_search_returns_at_most_five(connection, account) -> None:
    account_id, _ = account
    for number in range(7):
        put_file(connection, account_id, f"f{number}.md", "The passport expires soon.")
    assert len(search_files(connection, account_id, "passport")) == 5


def test_search_survives_odd_queries(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "plain text")
    for query in ('"unclosed', "a & | !", "!!!", "", "   ", "or", "-"):
        assert isinstance(search_files(connection, account_id, query), list)


def test_delete_file_reports_whether_a_row_went(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "x")
    assert delete_file(connection, account_id, "a.md") is True
    assert delete_file(connection, account_id, "a.md") is False


def test_delete_all_removes_only_this_accounts_files(connection, account, other) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "x")
    put_file(connection, account_id, "b.md", "y")
    put_file(connection, other[0], "a.md", "z")
    assert delete_all(connection, account_id) == 2
    assert list_files(connection, account_id) == []
    assert len(list_files(connection, other[0])) == 1


def test_deleting_the_account_removes_its_files(connection) -> None:
    account_id, email = _new_account()
    try:
        put_file(connection, account_id, "a.md", "x")
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        left = connection.execute(
            "SELECT count(*) FROM knowledge_file WHERE account_id = %s", (account_id,)
        ).fetchone()
        assert left == (0,)
    finally:
        _remove(account_id, email)


def test_apply_is_repeatable(connection) -> None:
    apply(connection)
    apply(connection, force=True)
