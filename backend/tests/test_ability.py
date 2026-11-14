import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.ability import (
    apply,
    create_ability,
    delete_ability,
    list_abilities,
    update_ability,
)
from app.account import ensure_account
from app.db import connect


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"ab-{uuid.uuid4().hex}@sakana.ai"
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


def test_create_list_update_delete(connection, account) -> None:
    account_id, _ = account
    first = create_ability(connection, account_id, "Summaries", "Give three bullets.")
    second = create_ability(connection, account_id, "Tone", "Be brief.")
    assert [row[0] for row in list_abilities(connection, account_id)] == [first[0], second[0]]
    changed = update_ability(connection, account_id, first[0], "Summaries", "Give two bullets.")
    assert changed is not None
    assert changed[0] == first[0]
    assert changed[3] == "Give two bullets."
    assert changed[5] >= first[5]
    assert delete_ability(connection, account_id, first[0]) is True
    assert delete_ability(connection, account_id, first[0]) is False
    assert [row[0] for row in list_abilities(connection, account_id)] == [second[0]]


def test_another_account_cannot_update_or_delete(connection, account, other) -> None:
    account_id, _ = account
    mine = create_ability(connection, account_id, "Tone", "Be brief.")
    assert update_ability(connection, other[0], mine[0], "Tone", "Be long.") is None
    assert delete_ability(connection, other[0], mine[0]) is False
    assert list_abilities(connection, other[0]) == []
    assert list_abilities(connection, account_id) == [mine]


def test_deleting_the_account_removes_its_abilities(connection) -> None:
    account_id, email = _new_account()
    try:
        create_ability(connection, account_id, "Tone", "Be brief.")
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        left = connection.execute(
            "SELECT count(*) FROM ability WHERE account_id = %s", (account_id,)
        ).fetchone()
        assert left == (0,)
    finally:
        _remove(account_id, email)


def test_apply_is_repeatable(connection) -> None:
    apply(connection)
    apply(connection, force=True)
