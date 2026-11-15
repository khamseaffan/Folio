import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app import turn
from app.account import ensure_account
from app.db import connect
from app.operations import (
    BAD_INPUT,
    BAD_QUESTION,
    BAD_REPORT_NAME,
    NO_REPORT,
    NO_RUN,
    NOT_AVAILABLE,
    NOT_FOUND,
    OPERATIONS,
    RESEARCH_BUSY,
    Context,
    Turn,
    agent_tools,
    call,
)
from app.queue import QueueNotConfigured
from app.report import create_report, get_report
from app.report_source import NewSource, list_sources
from app.run import create_run, get_run, start_attempt
from app.thread import create_thread, get_thread

SIX = {
    "start_research",
    "cancel_research",
    "list_research_runs",
    "list_reports",
    "rename_report",
    "delete_report",
}


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"researchops-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM report WHERE account_id = %s", (account_id,))
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
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, str]]:
    seen: list[tuple[uuid.UUID, str]] = []

    def enqueue(run_id, kind="schedule", client=None):
        seen.append((run_id, kind))

    monkeypatch.setattr("app.queue.enqueue", enqueue)
    return seen


def _start(connection, account_id, thread_id, question="How do heat pumps compare?"):
    return call(
        connection,
        account_id,
        Context(thread_id, Turn(opened=set())),
        "start_research",
        {"question": question},
    )


def _report_for(connection, account_id, name="Heat pumps"):
    thread = create_thread(connection, account_id)
    run = create_run(connection, account_id, thread[0], None, "research", "q")
    assert run is not None
    report = create_report(
        connection,
        account_id,
        run[0],
        thread[0],
        name,
        "q",
        "body",
        "note",
        [NewSource("page", "A page", "https://example.com", "a fact")],
    )
    assert report is not None
    return thread[0], run[0], report


def test_start_research_makes_a_research_run_and_enqueues_it_once(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    made = _start(connection, account_id, thread[0], "  How do heat pumps compare?  ")

    assert made["ok"] is True
    assert made["run"]["status"] == "queued"
    assert made["run"]["question"] == "How do heat pumps compare?"
    run_id = uuid.UUID(made["run"]["id"])
    row = get_run(connection, run_id)
    assert row is not None
    assert row[1] == account_id and row[2] == thread[0] and row[3] is None
    assert row[4] == "research" and row[5] == "queued" and row[6] == "How do heat pumps compare?"
    assert queued == [(run_id, "research")]


def test_the_run_is_committed_before_it_is_enqueued(account, monkeypatch):
    account_id, _ = account
    with connect() as setup:
        thread = create_thread(setup, account_id)
    visible: list[bool] = []

    def enqueue(run_id, kind="schedule", client=None):
        # The worker has its own connection and may pick the run up at once.
        with connect() as second:
            visible.append(get_run(second, run_id) is not None)

    monkeypatch.setattr("app.queue.enqueue", enqueue)
    # A connection in a transaction, as a tool call has.
    with connect() as tool_connection:
        made = _start(tool_connection, account_id, thread[0])

    assert made["ok"] is True
    assert visible == [True]


def test_a_stopped_account_is_refused_and_makes_no_run(connection, account, queued):
    account_id, email = account
    thread = create_thread(connection, account_id)
    connection.execute("UPDATE email_limit SET stopped = true WHERE email = %s", (email,))

    refused = _start(connection, account_id, thread[0])

    assert refused == {"ok": False, "code": "limit", "error": turn._STOPPED}
    assert queued == []
    count = connection.execute(
        "SELECT count(*) FROM run WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert count == (0,)


def test_a_capped_account_is_refused_and_makes_no_run(connection, account, queued):
    account_id, email = account
    thread = create_thread(connection, account_id)
    connection.execute("UPDATE email_limit SET token_cap = 0 WHERE email = %s", (email,))

    refused = _start(connection, account_id, thread[0])

    assert refused == {"ok": False, "code": "limit", "error": turn._CAPPED}
    assert queued == []
    count = connection.execute(
        "SELECT count(*) FROM run WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert count == (0,)


@pytest.mark.parametrize("question", ["", "   ", "x" * 2001, "a\x00b", "bad \ud800 text"])
def test_a_bad_question_is_refused(connection, account, queued, question):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    refused = _start(connection, account_id, thread[0], question)

    assert refused == {"ok": False, "code": "invalid", "error": BAD_QUESTION}
    assert queued == []


def test_a_question_at_the_limit_is_accepted(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    assert _start(connection, account_id, thread[0], "x" * 2000)["ok"] is True


def test_no_thread_or_another_accounts_thread_is_not_found(connection, account, other, queued):
    account_id, _ = account
    other_id, _ = other
    foreign = create_thread(connection, other_id)

    none = call(connection, account_id, Context(None), "start_research", {"question": "q"})
    wrong = _start(connection, account_id, foreign[0], "q")

    assert none == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert wrong == {"ok": False, "code": "not_found", "error": NOT_FOUND}
    assert queued == []


def test_a_queue_that_is_not_configured_fails_the_run(connection, account, monkeypatch):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    def enqueue(run_id, kind="schedule", client=None):
        raise QueueNotConfigured(kind)

    monkeypatch.setattr("app.queue.enqueue", enqueue)

    refused = _start(connection, account_id, thread[0])

    assert refused == {"ok": False, "code": "unavailable", "error": NOT_AVAILABLE}
    row = connection.execute(
        "SELECT status FROM run WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert row == ("failed",)


def test_a_raising_enqueue_still_answers_ok(connection, account, monkeypatch, caplog):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    def enqueue(run_id, kind="schedule", client=None):
        raise RuntimeError("secret detail")

    monkeypatch.setattr("app.queue.enqueue", enqueue)

    made = _start(connection, account_id, thread[0])

    assert made["ok"] is True
    row = get_run(connection, uuid.UUID(made["run"]["id"]))
    assert row is not None and row[5] == "queued"
    assert made["run"]["id"] in caplog.text and "secret detail" not in caplog.text


def test_cancel_queued_and_working_research(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    first = uuid.UUID(_start(connection, account_id, thread[0])["run"]["id"])
    second = uuid.UUID(_start(connection, account_id, thread[0])["run"]["id"])
    start_attempt(connection, second)
    context = Context(thread[0])

    one = call(connection, account_id, context, "cancel_research", {"run_id": str(first)})
    two = call(connection, account_id, context, "cancel_research", {"run_id": str(second)})

    assert one == {"ok": True, "run_id": str(first)}
    assert two == {"ok": True, "run_id": str(second)}
    for run_id in (first, second):
        row = get_run(connection, run_id)
        assert row is not None and row[5] == "cancelled"
    # A cancelled run is not started by a worker that receives it later.
    assert start_attempt(connection, first) is None


def test_cancel_refuses_a_schedule_run_a_finished_run_and_another_account(
    connection, account, other, queued
):
    account_id, _ = account
    other_id, _ = other
    thread = create_thread(connection, account_id)
    scheduled = create_run(connection, account_id, thread[0], None, "schedule", "t")
    assert scheduled is not None
    done = uuid.UUID(_start(connection, account_id, thread[0])["run"]["id"])
    connection.execute("UPDATE run SET status = 'ready' WHERE id = %s", (done,))
    mine = uuid.UUID(_start(connection, account_id, thread[0])["run"]["id"])

    for run_id in (scheduled[0], done, uuid.uuid4()):
        refused = call(
            connection, account_id, Context(None), "cancel_research", {"run_id": str(run_id)}
        )
        assert refused == {"ok": False, "code": "not_found", "error": NO_RUN}
    foreign = call(connection, other_id, Context(None), "cancel_research", {"run_id": str(mine)})
    assert foreign == {"ok": False, "code": "not_found", "error": NO_RUN}
    for run_id, status in ((scheduled[0], "queued"), (done, "ready"), (mine, "queued")):
        row = get_run(connection, run_id)
        assert row is not None and row[5] == status


def test_list_research_runs_shows_only_in_flight_research_of_the_caller(
    connection, account, other, queued
):
    account_id, _ = account
    other_id, _ = other
    thread = create_thread(connection, account_id)
    older = uuid.UUID(_start(connection, account_id, thread[0], "older")["run"]["id"])
    newer = uuid.UUID(_start(connection, account_id, thread[0], "newer")["run"]["id"])
    connection.execute(
        "UPDATE run SET created_at = now() - interval '1 hour' WHERE id = %s", (older,)
    )
    done = uuid.UUID(_start(connection, account_id, thread[0], "done")["run"]["id"])
    connection.execute("UPDATE run SET status = 'ready' WHERE id = %s", (done,))
    create_run(connection, account_id, thread[0], None, "schedule", "not research")
    foreign_thread = create_thread(connection, other_id)
    _start(connection, other_id, foreign_thread[0], "theirs")

    listed = call(connection, account_id, Context(None), "list_research_runs", {})

    assert listed["ok"] is True
    assert [item["id"] for item in listed["runs"]] == [str(newer), str(older)]
    assert listed["runs"][0]["question"] == "newer" and listed["runs"][0]["status"] == "queued"
    assert set(listed["runs"][0]) == {"id", "question", "status", "created_at"}


def test_list_reports_is_the_callers_and_newest_first(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    _, _, first = _report_for(connection, account_id, "First")
    _, _, second = _report_for(connection, account_id, "Second")
    connection.execute(
        "UPDATE report SET created_at = now() - interval '1 hour' WHERE id = %s", (first[0],)
    )
    _report_for(connection, other_id, "Theirs")

    listed = call(connection, account_id, Context(None), "list_reports", {})

    assert listed["ok"] is True
    assert [item["id"] for item in listed["reports"]] == [str(second[0]), str(first[0])]
    assert set(listed["reports"][0]) == {"id", "name", "question", "created_at"}


def test_rename_report(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    _, _, report = _report_for(connection, account_id)

    renamed = call(
        connection,
        account_id,
        Context(None),
        "rename_report",
        {"report_id": str(report[0]), "name": "  Better name  "},
    )

    assert renamed == {"ok": True, "report": {"id": str(report[0]), "name": "Better name"}}
    for name in ("", "   ", "x" * 81, "a\x00b"):
        bad = call(
            connection,
            account_id,
            Context(None),
            "rename_report",
            {"report_id": str(report[0]), "name": name},
        )
        assert bad == {"ok": False, "code": "invalid", "error": BAD_REPORT_NAME}
    foreign = call(
        connection,
        other_id,
        Context(None),
        "rename_report",
        {"report_id": str(report[0]), "name": "Mine"},
    )
    assert foreign == {"ok": False, "code": "not_found", "error": NO_REPORT}
    kept = get_report(connection, account_id, report[0])
    assert kept is not None and kept[4] == "Better name"


def test_delete_report_keeps_the_run_and_the_thread(connection, account, other):
    account_id, _ = account
    other_id, _ = other
    thread_id, run_id, report = _report_for(connection, account_id)

    foreign = call(
        connection, other_id, Context(None), "delete_report", {"report_id": str(report[0])}
    )
    assert foreign == {"ok": False, "code": "not_found", "error": NO_REPORT}

    deleted = call(
        connection, account_id, Context(None), "delete_report", {"report_id": str(report[0])}
    )

    assert deleted == {"ok": True, "report_id": str(report[0])}
    assert get_report(connection, account_id, report[0]) is None
    assert list_sources(connection, account_id, report[0]) == []
    assert get_run(connection, run_id) is not None
    assert get_thread(connection, account_id, thread_id) is not None
    again = call(
        connection, account_id, Context(None), "delete_report", {"report_id": str(report[0])}
    )
    assert again == {"ok": False, "code": "not_found", "error": NO_REPORT}


def test_bad_arguments_are_invalid(connection, account):
    account_id, _ = account

    for name, arguments in (
        ("start_research", {}),
        ("start_research", {"question": "q", "account_id": str(account_id)}),
        ("cancel_research", {"run_id": "nope"}),
        ("rename_report", {"report_id": str(uuid.uuid4())}),
        ("delete_report", {"report_id": "nope"}),
    ):
        result = call(connection, account_id, Context(None), name, arguments)
        assert result == {"ok": False, "code": "invalid", "error": BAD_INPUT}


def test_the_six_operations_are_agent_operations_and_only_start_research_is_open_to_a_run(account):
    assert SIX <= set(OPERATIONS)
    for name in SIX:
        assert OPERATIONS[name].agent is True
        assert OPERATIONS[name].chat_only is (name != "start_research")

    chat = {spec.name: spec for spec in agent_tools()}
    assert SIX <= set(chat)
    in_run = {spec.name for spec in agent_tools(in_run=True)}
    assert in_run & SIX == {"start_research"}
    for name in SIX:
        assert "account" not in json.dumps(chat[name].schema).lower()


def test_a_run_cannot_call_the_other_five(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    for name in SIX - {"start_research"}:
        result = call(
            connection, account_id, Context(thread[0]), name, {"question": "q"}, in_run=True
        )
        assert result["ok"] is False and result["code"] == "unknown"
    assert queued == []


def test_a_run_can_start_research_in_its_thread(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)

    result = call(
        connection,
        account_id,
        Context(thread[0], Turn(opened=set())),
        "start_research",
        {"question": "How do heat pumps compare?"},
        in_run=True,
    )

    assert result["ok"] is True and result["run"]["status"] == "queued"
    run_id = uuid.UUID(result["run"]["id"])
    row = get_run(connection, run_id)
    assert row is not None and row[4] == "research" and row[2] == thread[0]
    assert queued == [(run_id, "research")]


def test_a_run_cannot_start_a_second_research_while_one_is_working_in_the_thread(
    connection, account, queued
):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    context = Context(thread[0], Turn(opened=set()))
    first = call(
        connection, account_id, context, "start_research", {"question": "one"}, in_run=True
    )
    assert first["ok"] is True

    again = call(
        connection, account_id, context, "start_research", {"question": "two"}, in_run=True
    )

    assert again == {"ok": False, "code": "busy", "error": RESEARCH_BUSY}
    assert len(queued) == 1


def test_a_person_in_the_chat_is_not_held_by_that_limit(connection, account, queued):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    context = Context(thread[0], Turn(opened=set()))
    for question in ("one", "two"):
        result = call(connection, account_id, context, "start_research", {"question": question})
        assert result["ok"] is True
    assert len(queued) == 2


def test_the_limit_is_per_thread_and_ends_with_the_run(connection, account, queued):
    account_id, _ = account
    first_thread = create_thread(connection, account_id)
    second_thread = create_thread(connection, account_id)
    started = call(
        connection,
        account_id,
        Context(first_thread[0], Turn(opened=set())),
        "start_research",
        {"question": "one"},
        in_run=True,
    )
    other = call(
        connection,
        account_id,
        Context(second_thread[0], Turn(opened=set())),
        "start_research",
        {"question": "two"},
        in_run=True,
    )
    assert other["ok"] is True

    connection.execute(
        "UPDATE run SET status = 'ready' WHERE id = %s", (uuid.UUID(started["run"]["id"]),)
    )
    after = call(
        connection,
        account_id,
        Context(first_thread[0], Turn(opened=set())),
        "start_research",
        {"question": "three"},
        in_run=True,
    )
    assert after["ok"] is True


def test_the_chat_prompt_has_the_research_line_and_a_scheduled_prompt_does_not():
    chat = turn._system_prompt("Ada", None, None, chat=True)
    scheduled = turn._system_prompt("Ada", None, None)

    assert turn.RESEARCH in chat
    assert turn.RESEARCH not in scheduled
    assert chat.index(turn.MEMORY) < chat.index(turn.RESEARCH) < chat.index("The time now is ")
    assert "start_research" in turn.RESEARCH


def test_a_scheduled_run_is_told_how_to_start_research():
    from app import scheduled

    assert "start_research" in scheduled._RUN_LINE
    assert "Do not ask a question" in scheduled._RUN_LINE
