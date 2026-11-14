import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app import live
from app.account import ensure_account
from app.db import connect
from app.message import insert_message
from app.operations import (
    BAD_INPUT,
    BAD_NAME,
    NOT_FOUND,
    OPERATIONS,
    UNKNOWN,
    Context,
    agent_tools,
    call,
)
from app.thread import create_thread, get_thread
from app.usage import apply as apply_usage
from app.usage import record_usage


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"ops-{uuid.uuid4().hex}@sakana.ai"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        # On a new database nothing has created the tables yet.
        apply_usage(connection)
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
        # Each statement commits, so cleanup on another connection never waits on this one.
        opened.autocommit = True
        yield opened


def test_rename_changes_only_that_thread(connection, account) -> None:
    account_id, _ = account
    first = create_thread(connection, account_id)
    second = create_thread(connection, account_id)
    result = call(
        connection,
        account_id,
        Context(None),
        "rename_thread",
        {"thread_id": str(first[0]), "name": "  Tax  "},
    )
    assert result["ok"] is True
    assert result["thread"]["name"] == "Tax"
    assert result["thread"]["id"] == str(first[0])
    assert get_thread(connection, account_id, first[0])[2] == "Tax"
    assert get_thread(connection, account_id, second[0])[2] == "New thread"


def test_rename_refuses_bad_names(connection, account) -> None:
    account_id, _ = account
    thread = create_thread(connection, account_id)
    for name in ("", "   ", "x" * 81):
        result = call(
            connection,
            account_id,
            Context(None),
            "rename_thread",
            {"thread_id": str(thread[0]), "name": name},
        )
        assert result == {"ok": False, "code": "invalid", "error": BAD_NAME}
    assert get_thread(connection, account_id, thread[0])[2] == "New thread"
    longest = call(
        connection,
        account_id,
        Context(None),
        "rename_thread",
        {"thread_id": str(thread[0]), "name": "x" * 80},
    )
    assert longest["ok"] is True


def test_another_accounts_thread_is_not_found(connection, account, other) -> None:
    account_id, _ = account
    theirs = create_thread(connection, other[0])
    renamed = call(
        connection,
        account_id,
        Context(None),
        "rename_thread",
        {"thread_id": str(theirs[0]), "name": "Mine"},
    )
    deleted = call(
        connection, account_id, Context(None), "delete_thread", {"thread_id": str(theirs[0])}
    )
    assert renamed == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert deleted == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert get_thread(connection, other[0], theirs[0])[2] == "New thread"


def test_missing_thread_id_uses_the_context_thread(connection, account) -> None:
    account_id, _ = account
    thread = create_thread(connection, account_id)
    result = call(connection, account_id, Context(thread[0]), "rename_thread", {"name": "Here"})
    assert result["ok"] is True
    assert get_thread(connection, account_id, thread[0])[2] == "Here"


def test_no_thread_id_and_no_context_is_not_found(connection, account) -> None:
    account_id, _ = account
    renamed = call(connection, account_id, Context(None), "rename_thread", {"name": "Here"})
    deleted = call(connection, account_id, Context(None), "delete_thread", {})
    assert renamed["code"] == "not_found"
    assert deleted["code"] == "not_found"


def test_create_takes_an_optional_name(connection, account) -> None:
    account_id, _ = account
    plain = call(connection, account_id, Context(None), "create_thread", {})
    named = call(connection, account_id, Context(None), "create_thread", {"name": " Trip "})
    refused = call(connection, account_id, Context(None), "create_thread", {"name": "x" * 81})
    assert plain["thread"]["name"] == "New thread"
    assert named["thread"]["name"] == "Trip"
    assert refused == {"ok": False, "code": "invalid", "error": BAD_NAME}


def test_list_returns_only_this_accounts_threads(connection, account, other) -> None:
    account_id, _ = account
    mine = create_thread(connection, account_id)
    create_thread(connection, other[0])
    result = call(connection, account_id, Context(None), "list_threads", {})
    assert result["ok"] is True
    assert [item["id"] for item in result["threads"]] == [str(mine[0])]
    assert set(result["threads"][0]) == {"id", "name", "created_at"}


def test_delete_removes_messages_and_keeps_usage(connection, account) -> None:
    account_id, email = account
    thread = create_thread(connection, account_id)
    message = insert_message(connection, account_id, thread[0], "assistant", "chat", "hi")
    assert message is not None
    usage = record_usage(connection, account_id, email, "chat", "openai", "m", 1, 2, 3, message[0])
    result = call(
        connection, account_id, Context(None), "delete_thread", {"thread_id": str(thread[0])}
    )
    assert result == {"ok": True, "thread_id": str(thread[0])}
    assert get_thread(connection, account_id, thread[0]) is None
    gone = connection.execute("SELECT id FROM message WHERE id = %s", (message[0],)).fetchone()
    assert gone is None
    kept = connection.execute(
        "SELECT account_id, message_id FROM usage WHERE id = %s", (usage[0],)
    ).fetchone()
    assert kept == (account_id, None)


def test_delete_stops_a_reply_streaming_in_that_thread(connection, account, monkeypatch) -> None:
    account_id, _ = account
    thread = create_thread(connection, account_id)
    stopped: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(live, "stop_reply", lambda a, t: stopped.append((a, t)))
    call(connection, account_id, Context(None), "delete_thread", {"thread_id": str(thread[0])})
    assert stopped == [(account_id, thread[0])]


def test_unknown_operation_and_bad_arguments(connection, account) -> None:
    account_id, _ = account
    unknown = call(connection, account_id, Context(None), "drop_tables", {})
    invalid = call(
        connection, account_id, Context(None), "rename_thread", {"thread_id": "nope", "name": "a"}
    )
    missing = call(connection, account_id, Context(None), "rename_thread", {})
    assert unknown == {"ok": False, "code": "unknown", "error": UNKNOWN}
    assert invalid == {"ok": False, "code": "invalid", "error": BAD_INPUT}
    assert missing == {"ok": False, "code": "invalid", "error": BAD_INPUT}


def test_agent_tools_never_expose_an_account_id() -> None:
    specs = agent_tools()
    for spec in specs:
        assert "account_id" not in json.dumps(spec.schema)
        assert spec.description != ""
    assert {spec.name for spec in specs} == {
        "list_threads",
        "create_thread",
        "rename_thread",
        "delete_thread",
    }
    assert set(OPERATIONS) == {spec.name for spec in specs}


def test_stop_account_stops_every_reply_of_that_account() -> None:
    import asyncio

    async def scenario() -> tuple[bool, bool, bool]:
        account_id, stranger = uuid.uuid4(), uuid.uuid4()
        first, second, theirs = live.LiveReply(), live.LiveReply(), live.LiveReply()
        live.register(account_id, uuid.uuid4(), first)
        live.register(account_id, uuid.uuid4(), second)
        live.register(stranger, uuid.uuid4(), theirs)
        live.stop_account(account_id)
        return first.stop.is_set(), second.stop.is_set(), theirs.stop.is_set()

    assert asyncio.run(scenario()) == (True, True, False)


def test_a_name_with_a_null_character_is_refused(connection, account) -> None:
    account_id, _ = account
    thread = create_thread(connection, account_id)
    renamed = call(
        connection,
        account_id,
        Context(None),
        "rename_thread",
        {"thread_id": str(thread[0]), "name": "Tax\x00es"},
    )
    created = call(connection, account_id, Context(None), "create_thread", {"name": "a\x00"})
    assert renamed == {"ok": False, "code": "invalid", "error": BAD_NAME}
    assert created == {"ok": False, "code": "invalid", "error": BAD_NAME}
    assert get_thread(connection, account_id, thread[0])[2] == "New thread"
