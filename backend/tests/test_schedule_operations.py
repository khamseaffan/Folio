import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.operations import (
    BAD_INPUT,
    BAD_TASK,
    BAD_TIME,
    NO_SCHEDULE,
    NOT_FOUND,
    UNKNOWN,
    Context,
    Turn,
    agent_tools,
    call,
)
from app.schedule import get_schedule, list_schedules
from app.thread import create_thread


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"schedops-{uuid.uuid4().hex}@example.com"
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


def _soon(hours: float = 2) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _write(connection, account_id, thread_id, **fields):
    arguments = {"task": "Three things to focus on", "when_text": "daily 09:00, UTC"}
    arguments["next_run_at"] = _soon()
    arguments.update(fields)
    return call(
        connection, account_id, Context(thread_id, Turn(opened=set())), "write_schedule", arguments
    )


def test_create_in_the_turn_thread_and_list(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    made = _write(connection, account_id, thread[0])

    assert made["ok"] is True
    stored = get_schedule(connection, account_id, uuid.UUID(made["schedule"]["id"]))
    assert stored is not None and stored[2] == thread[0]
    assert stored[3] == "Three things to focus on"
    listed = call(connection, account_id, Context(thread[0]), "list_schedules", {})
    assert listed["ok"] is True
    assert listed["schedules"] == [
        {
            "id": made["schedule"]["id"],
            "task": "Three things to focus on",
            "when_text": "daily 09:00, UTC",
            "next_run_at": stored[5].isoformat(),
            "thread_id": str(thread[0]),
        }
    ]


def test_text_is_stripped(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    made = _write(connection, account_id, thread[0], task="  plan  ", when_text=" daily ")

    assert made["schedule"]["task"] == "plan"
    assert made["schedule"]["when_text"] == "daily"


def test_update_by_id_replaces_the_schedule(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = _write(connection, account_id, thread[0])
    later = _soon(30)

    changed = _write(
        connection,
        account_id,
        None,
        schedule_id=made["schedule"]["id"],
        task="New task",
        when_text="weekly",
        next_run_at=later,
    )

    assert changed["ok"] is True
    assert changed["schedule"]["id"] == made["schedule"]["id"]
    rows = list_schedules(connection, account_id)
    assert [(row[3], row[4]) for row in rows] == [("New task", "weekly")]
    assert rows[0][5] == datetime.fromisoformat(later)


def test_delete(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = _write(connection, account_id, thread[0])

    deleted = call(
        connection,
        account_id,
        Context(thread[0]),
        "delete_schedule",
        {"schedule_id": made["schedule"]["id"]},
    )

    assert deleted == {"ok": True, "schedule_id": made["schedule"]["id"]}
    assert list_schedules(connection, account_id) == []
    again = call(
        connection,
        account_id,
        Context(thread[0]),
        "delete_schedule",
        {"schedule_id": made["schedule"]["id"]},
    )
    assert again == {"ok": False, "code": "not_found", "error": NO_SCHEDULE}


def test_another_account_cannot_change_or_delete(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    thread = create_thread(connection, account_id)
    other_thread = create_thread(connection, other_id)
    made = _write(connection, account_id, thread[0])
    before = get_schedule(connection, account_id, uuid.UUID(made["schedule"]["id"]))

    changed = _write(
        connection, other_id, other_thread[0], schedule_id=made["schedule"]["id"], task="stolen"
    )
    deleted = call(
        connection,
        other_id,
        Context(other_thread[0]),
        "delete_schedule",
        {"schedule_id": made["schedule"]["id"]},
    )

    assert changed == {"ok": False, "code": "not_found", "error": NO_SCHEDULE}
    assert deleted == {"ok": False, "code": "not_found", "error": NO_SCHEDULE}
    assert get_schedule(connection, account_id, uuid.UUID(made["schedule"]["id"])) == before
    listed = call(connection, other_id, Context(other_thread[0]), "list_schedules", {})
    assert listed == {"ok": True, "schedules": []}


def test_a_new_schedule_needs_a_thread_of_the_account(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    foreign = create_thread(connection, other_id)

    without = _write(connection, account_id, None)
    wrong = _write(connection, account_id, foreign[0])

    assert without == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert wrong == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert list_schedules(connection, account_id) == []
    assert list_schedules(connection, other_id) == []


@pytest.mark.parametrize(
    "bad",
    [
        "2099-01-01T09:00:00",
        "2099-01-01",
        "2020-01-01T09:00:00+00:00",
        "tomorrow at 9",
        "",
    ],
)
def test_a_bad_time_is_refused_and_nothing_is_stored(connection, account, bad):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    refused = _write(connection, account_id, thread[0], next_run_at=bad)

    assert refused == {"ok": False, "code": "invalid", "error": BAD_TIME}
    assert list_schedules(connection, account_id) == []


def test_a_time_in_the_past_is_refused_on_update_and_the_row_is_kept(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = _write(connection, account_id, thread[0])
    before = get_schedule(connection, account_id, uuid.UUID(made["schedule"]["id"]))

    refused = _write(
        connection,
        account_id,
        thread[0],
        schedule_id=made["schedule"]["id"],
        next_run_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )

    assert refused == {"ok": False, "code": "invalid", "error": BAD_TIME}
    assert get_schedule(connection, account_id, uuid.UUID(made["schedule"]["id"])) == before


@pytest.mark.parametrize(
    "fields",
    [
        {"task": ""},
        {"task": "   "},
        {"when_text": ""},
        {"task": "x" * 2001},
        {"when_text": "x" * 2001},
        {"task": "a\x00b"},
        {"when_text": "\ud800"},
    ],
)
def test_bad_text_is_refused_and_nothing_is_stored(connection, account, fields):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    refused = _write(connection, account_id, thread[0], **fields)

    assert refused == {"ok": False, "code": "invalid", "error": BAD_TASK}
    assert list_schedules(connection, account_id) == []


def test_text_at_the_limit_is_stored(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    made = _write(connection, account_id, thread[0], task="x" * 2000, when_text="y" * 2000)

    assert made["ok"] is True


def test_unknown_fields_and_a_given_account_id_are_refused(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    refused = call(
        connection,
        account_id,
        Context(thread[0]),
        "write_schedule",
        {"task": "t", "when_text": "w", "next_run_at": _soon(), "account_id": str(account_id)},
    )

    assert refused == {"ok": False, "code": "invalid", "error": BAD_INPUT}
    assert list_schedules(connection, account_id) == []


def test_tools_are_described_and_hide_the_account_id():
    specs = {spec.name: spec for spec in agent_tools()}

    for name in ("list_schedules", "write_schedule", "delete_schedule"):
        assert name in specs
        assert "account_id" not in json.dumps(specs[name].schema)
    description = specs["write_schedule"].description
    assert "time zone" in description
    assert "ISO 8601" in description
    assert "thread of this conversation" in description


def test_a_run_gets_only_the_list_tool(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    names = {spec.name for spec in agent_tools(in_run=True)}
    refused = call(
        connection,
        account_id,
        Context(thread[0]),
        "write_schedule",
        {"task": "t", "when_text": "w", "next_run_at": _soon()},
        in_run=True,
    )
    also = call(
        connection,
        account_id,
        Context(thread[0]),
        "delete_schedule",
        {"schedule_id": str(uuid.uuid4())},
        in_run=True,
    )
    listed = call(connection, account_id, Context(thread[0]), "list_schedules", {}, in_run=True)

    assert "list_schedules" in names
    assert "write_schedule" not in names and "delete_schedule" not in names
    assert "write_knowledge" in names
    assert refused == {"ok": False, "code": "unknown", "error": UNKNOWN}
    assert also == {"ok": False, "code": "unknown", "error": UNKNOWN}
    assert listed["ok"] is True
    assert list_schedules(connection, account_id) == []


def test_deleting_the_thread_removes_its_schedules(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    _write(connection, account_id, thread[0])
    assert len(list_schedules(connection, account_id)) == 1

    deleted = call(
        connection, account_id, Context(thread[0]), "delete_thread", {"thread_id": str(thread[0])}
    )

    assert deleted["ok"] is True
    assert list_schedules(connection, account_id) == []
