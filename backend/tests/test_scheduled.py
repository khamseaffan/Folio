import logging
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.knowledge_file import put_file
from app.message import list_messages
from app.model import Block, FakeModel, Reply, ToolCall
from app.research import FAILED_LINE as RESEARCH_FAILED_LINE
from app.run import create_run, get_run, start_attempt
from app.schedule import create_schedule, get_schedule, list_schedules
from app.scheduled import FAILED_LINE, MAX_ATTEMPTS, NO_NEXT_LINE, execute, next_time, tick
from app.thread import create_thread, delete_thread

LOGGER = "folio.worker"
TASK = "secret-task-wording-lantern"
RESULT = "secret-result-wording-quill"


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"sched-run-{uuid.uuid4().hex}@example.com"
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
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    collected: list[uuid.UUID] = []
    monkeypatch.setattr("app.scheduled.enqueue", collected.append)
    return collected


def _later(hours: float = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def _model(text: str | None = None) -> FakeModel:
    return FakeModel(
        reply=text if text is not None else _later(),
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
    )


def _due_schedule(connection, account_id, task=TASK, when="daily at 9, UTC"):
    thread = create_thread(connection, account_id)
    made = create_schedule(
        connection, account_id, thread[0], task, when, datetime.now(UTC) + timedelta(hours=1)
    )
    assert made is not None
    connection.execute(
        "UPDATE schedule SET next_run_at = now() - interval '1 minute' WHERE id = %s", (made[0],)
    )
    return thread[0], made[0]


def _runs(connection, account_id):
    return connection.execute(
        "SELECT id, status, schedule_id FROM run WHERE account_id = %s ORDER BY created_at",
        (account_id,),
    ).fetchall()


def _usage(connection, email):
    return connection.execute(
        "SELECT operation, run_id, message_id, usage_missing FROM usage WHERE email = %s",
        (email,),
    ).fetchall()


def _lines(connection, account_id, thread_id, kind):
    rows = list_messages(connection, account_id, thread_id) or []
    return [row[6] for row in rows if row[5] == kind]


def _make_run(connection, account_id, thread_id, schedule_id=None, task=TASK):
    run = create_run(connection, account_id, thread_id, schedule_id, "schedule", task)
    assert run is not None
    return run


def _tool(name, arguments, call_id="c1"):
    return Reply("", 5, 1, 6, (ToolCall(call_id, name, arguments),))


class _BrokenStream:
    def __iter__(self):
        raise RuntimeError("boom")

    def reply(self):
        return Reply("", None, None, None)


class _BrokenModel(FakeModel):
    def stream(self, prompt, blocks, tools=()):
        return _BrokenStream()

    def complete(self, prompt, blocks, tools=()):
        raise RuntimeError("boom")


class _PromptModel(FakeModel):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.prompts: list[str] = []

    def stream(self, prompt, blocks, tools=()):
        self.prompts.append(prompt)
        return super().stream(prompt, blocks, tools)


def test_tzdata_is_installed() -> None:
    assert ZoneInfo("America/New_York").key == "America/New_York"


def test_a_due_schedule_gets_one_run_and_the_next_time(connection, account, enqueued):
    account_id, email = account
    _, due_id = _due_schedule(connection, account_id)
    thread = create_thread(connection, account_id)
    idle = create_schedule(
        connection, account_id, thread[0], "later", "w", datetime.now(UTC) + timedelta(hours=5)
    )
    assert idle is not None
    next_at = _later(48)

    counts = tick(_model(next_at))

    assert counts["started"] == 1
    runs = _runs(connection, account_id)
    assert [(row[1], row[2]) for row in runs] == [("queued", due_id)]
    assert enqueued == [runs[0][0]]
    moved = get_schedule(connection, account_id, due_id)
    assert moved is not None and moved[5] == datetime.fromisoformat(next_at)
    untouched = get_schedule(connection, account_id, idle[0])
    assert untouched is not None and untouched[5] == idle[5]
    assert _usage(connection, email) == [("schedule", None, None, False)]
    stored = get_run(connection, runs[0][0])
    assert stored is not None and stored[4] == "schedule" and stored[6] == TASK


def test_two_ticks_in_a_row_make_one_run(connection, account, enqueued):
    account_id, _ = account
    _due_schedule(connection, account_id)

    first = tick(_model())
    second = tick(_model())

    assert first["started"] == 1
    assert second["started"] == 0
    assert len(_runs(connection, account_id)) == 1
    assert len(enqueued) == 1


def test_two_ticks_at_once_make_one_run(connection, account, enqueued):
    account_id, _ = account
    _due_schedule(connection, account_id)
    barrier = threading.Barrier(2, timeout=20)

    class _Meeting(FakeModel):
        def complete(self, prompt, blocks, tools=()):
            # Both ticks have read the due list before either one writes.
            barrier.wait()
            return super().complete(prompt, blocks, tools)

    results: list[dict[str, int]] = []
    errors: list[BaseException] = []

    def run_tick() -> None:
        try:
            results.append(tick(_Meeting(reply=_later(48), total_tokens=1)))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=run_tick) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert sum(result["started"] for result in results) == 1
    assert len(_runs(connection, account_id)) == 1
    assert len(enqueued) == 1


def test_a_run_in_flight_skips_the_occurrence(connection, account, enqueued):
    account_id, _ = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    first = _make_run(connection, account_id, thread_id, schedule_id)
    next_at = _later(48)

    counts = tick(_model(next_at))

    assert counts["skipped"] == 1 and counts["started"] == 0
    assert [row[0] for row in _runs(connection, account_id)] == [first[0]]
    assert enqueued == []
    moved = get_schedule(connection, account_id, schedule_id)
    assert moved is not None and moved[5] == datetime.fromisoformat(next_at)


@pytest.mark.parametrize("state", ["stopped", "capped"])
def test_a_stopped_or_capped_account_is_held(connection, account, enqueued, state):
    account_id, email = account
    _, schedule_id = _due_schedule(connection, account_id)
    before = get_schedule(connection, account_id, schedule_id)
    if state == "stopped":
        connection.execute("UPDATE email_limit SET stopped = true WHERE email = %s", (email,))
    else:
        connection.execute("UPDATE email_limit SET token_cap = 0 WHERE email = %s", (email,))
    model = _model()

    counts = tick(model)

    assert counts["held"] == 1
    assert model.seen == []
    assert _usage(connection, email) == []
    assert _runs(connection, account_id) == []
    assert enqueued == []
    after = get_schedule(connection, account_id, schedule_id)
    assert after is not None and before is not None and after[5] == before[5]


@pytest.mark.parametrize(
    "answer",
    ["tomorrow morning", "2020-01-01T09:00:00+00:00", "2099-01-01T09:00:00", "error"],
)
def test_a_next_time_that_cannot_be_used_is_told_once(
    connection, account, enqueued, caplog, answer
):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    model = _BrokenModel() if answer == "error" else _model(answer)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        tick(model)

    moved = get_schedule(connection, account_id, schedule_id)
    assert moved is not None and moved[5] is None
    assert _lines(connection, account_id, thread_id, "result") == [NO_NEXT_LINE]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(schedule_id) in errors[0].getMessage()
    assert TASK not in caplog.text
    # The call was sent, so it still has a usage row.
    assert [row[0] for row in _usage(connection, email)] == ["schedule"]
    assert len(_runs(connection, account_id)) == 1
    assert len(enqueued) == 1
    # The schedule is no longer due, so a second tick adds no second line.
    tick(_model())
    assert _lines(connection, account_id, thread_id, "result") == [NO_NEXT_LINE]


@pytest.mark.parametrize("answer", ["none", "None.", " NONE "])
def test_none_ends_a_schedule_without_a_line(connection, account, enqueued, caplog, answer):
    account_id, _ = account
    thread_id, schedule_id = _due_schedule(connection, account_id)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        counts = tick(_model(answer))

    moved = get_schedule(connection, account_id, schedule_id)
    assert moved is not None and moved[5] is None
    assert _lines(connection, account_id, thread_id, "result") == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert counts["started"] == 1
    assert len(enqueued) == 1
    assert len(list_schedules(connection, account_id)) == 1


def test_next_time_reads_the_answer():
    now = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)

    good = next_time(_model("2026-11-15T09:00:00+00:00"), "daily", now)
    none = next_time(_model("none"), "once", now)
    text = next_time(_model("soon"), "daily", now)
    past = next_time(_model("2026-11-13T09:00:00+00:00"), "daily", now)
    naive = next_time(_model("2026-11-15T09:00:00"), "daily", now)
    broken = next_time(_BrokenModel(), "daily", now)

    assert good[0] == datetime(2026, 11, 15, 9, 0, tzinfo=UTC) and good[2] is False
    assert good[1] is not None
    assert none[0] is None and none[2] is False and none[1] is not None
    assert text[0] is None and text[2] is True and text[1] is not None
    assert past[0] is None and past[2] is True
    assert naive[0] is None and naive[2] is True
    assert broken == (None, None, True)


def test_execute_writes_one_result_and_ignores_a_second_delivery(connection, account):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    model = _PromptModel(reply=RESULT, input_tokens=4, output_tokens=3, total_tokens=7)

    outcome = execute(run[0], model)

    assert outcome == "ready"
    stored = get_run(connection, run[0])
    assert stored is not None
    assert stored[5] == "ready" and stored[9] is not None and stored[8] == 1
    messages = [m for m in list_messages(connection, account_id, thread_id) or []]
    assert [(m[3], m[4], m[5], m[6]) for m in messages] == [
        (run[0], "assistant", "result", RESULT)
    ]
    assert _usage(connection, email) == [("schedule", run[0], messages[0][0], False)]
    assert "while the person is away" in model.prompts[0]
    assert "The time now is " in model.prompts[0]
    assert model.seen[0][0] == [Block(role="person", text=TASK)]

    again = execute(run[0], _model("second"))

    assert again == "duplicate"
    assert len(list_messages(connection, account_id, thread_id) or []) == 1
    assert len(_usage(connection, email)) == 1


def test_a_run_uses_memory_and_cannot_make_schedules(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    put_file(connection, account_id, "notes.md", "the-notes-body-heron")
    when = _later()
    model = FakeModel(
        script=(
            _tool("read_knowledge", {"name": "notes.md"}, "c1"),
            _tool(
                "write_schedule",
                {"task": "again", "when_text": "w", "next_run_at": when},
                "c2",
            ),
            Reply("Done.", 1, 1, 2),
        )
    )

    outcome = execute(run[0], model)

    assert outcome == "ready"
    tool_names = {spec.name for spec in model.seen[0][1]}
    assert "read_knowledge" in tool_names and "list_schedules" in tool_names
    assert "write_schedule" not in tool_names and "delete_schedule" not in tool_names
    read = [b for b in model.seen[1][0] if b.role == "tool"]
    assert "the-notes-body-heron" in read[0].text
    refused = [b for b in model.seen[2][0] if b.role == "tool"]
    assert '"unknown"' in refused[-1].text
    assert [row[0] for row in list_schedules(connection, account_id)] == [schedule_id]


def test_a_scheduled_task_can_start_research_and_a_second_start_is_refused(
    connection, account, monkeypatch
):
    queued = []

    def enqueue(run_id, kind="schedule", client=None):
        queued.append((run_id, kind))

    monkeypatch.setattr("app.queue.enqueue", enqueue)
    account_id, _ = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id, "Research Sakana AI")
    question = "What does Sakana AI sell, and what problem does it tackle?"
    model = FakeModel(
        script=(
            _tool("start_research", {"question": question}, "c1"),
            _tool("start_research", {"question": "again"}, "c2"),
            Reply("Research has started. The report will appear in Reports.", 1, 1, 2),
        )
    )

    outcome = execute(run[0], model)

    assert outcome == "ready"
    assert "start_research" in {spec.name for spec in model.seen[0][1]}
    assert len(queued) == 1 and queued[0][1] == "research"
    started = connection.execute(
        "SELECT kind, status, task, thread_id FROM run WHERE id = %s", (queued[0][0],)
    ).fetchone()
    assert started == ("research", "queued", question, thread_id)
    second = [b for b in model.seen[2][0] if b.role == "tool"][-1]
    assert '"busy"' in second.text


def test_a_failing_model_is_tried_four_times_then_dead(connection, account, caplog):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    model = _BrokenModel()
    outcomes = []
    counts = []

    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(MAX_ATTEMPTS):
            outcomes.append(execute(run[0], model))
            stored = get_run(connection, run[0])
            assert stored is not None
            counts.append(stored[8])

    assert outcomes == ["retry", "retry", "retry", "dead"]
    assert counts == [1, 2, 3, 4]
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "dead" and stored[9] is None
    assert _lines(connection, account_id, thread_id, "result") == [FAILED_LINE]
    assert _lines(connection, account_id, thread_id, "failure") == []
    line = [m for m in list_messages(connection, account_id, thread_id) or []]
    assert [(m[3], m[4], m[5]) for m in line] == [(run[0], "assistant", "result")]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    dead = [r for r in errors if "dead" in r.getMessage()]
    assert len(dead) == 1 and str(run[0]) in dead[0].getMessage()
    assert all(
        str(run[0]) in r.getMessage() for r in caplog.records if r.name == LOGGER
    )
    assert TASK not in caplog.text
    assert [(row[0], row[1], row[3]) for row in _usage(connection, email)] == [
        ("schedule", run[0], True)
    ] * 4
    assert execute(run[0], model) == "duplicate"


def test_a_stopped_or_capped_account_cancels_a_run(connection, account):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    connection.execute("UPDATE email_limit SET token_cap = 0 WHERE email = %s", (email,))
    model = _model("never")

    assert execute(run[0], model) == "cancelled"

    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "cancelled"
    assert model.seen == []
    assert list_messages(connection, account_id, thread_id) == []
    assert execute(run[0], model) == "duplicate"


def test_a_thread_deleted_before_the_run_starts(connection, account):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    delete_thread(connection, account_id, thread_id)
    model = _model("never")

    outcome = execute(run[0], model)

    assert outcome != "retry"
    assert model.seen == []
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] in ("cancelled", "ready")
    assert _usage(connection, email) == []


def test_a_thread_deleted_during_the_run(connection, account, caplog):
    account_id, email = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)

    class _Deleting(FakeModel):
        def stream(self, prompt, blocks, tools=()):
            with connect() as other:
                other.execute("DELETE FROM thread WHERE id = %s", (thread_id,))
            return super().stream(prompt, blocks, tools)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        outcome = execute(
            run[0], _Deleting(reply=RESULT, input_tokens=1, output_tokens=1, total_tokens=2)
        )

    assert outcome == "ready"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "ready" and stored[9] is not None
    count = connection.execute(
        "SELECT count(*) FROM message WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert count == (0,)
    assert _usage(connection, email) == [("schedule", run[0], None, False)]
    assert execute(run[0], _model("again")) == "duplicate"


def test_no_database_transaction_is_open_while_the_model_runs(connection, account, enqueued):
    account_id, _ = account
    thread_id, schedule_id = _due_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    seen: list[int] = []

    def count_idle_in_transaction() -> int:
        row = connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND state = 'idle in transaction' "
            "AND pid <> pg_backend_pid()"
        ).fetchone()
        assert row is not None
        return int(row[0])

    class _Watching(FakeModel):
        def stream(self, prompt, blocks, tools=()):
            seen.append(count_idle_in_transaction())
            return super().stream(prompt, blocks, tools)

        def complete(self, prompt, blocks, tools=()):
            seen.append(count_idle_in_transaction())
            return super().complete(prompt, blocks, tools)

    tick(_Watching(reply=_later(48), total_tokens=1))
    execute(run[0], _Watching(reply=RESULT, total_tokens=1))

    assert seen == [0, 0]


def test_a_stuck_run_is_marked_dead_and_frees_the_schedule(
    connection, account, enqueued, caplog
):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = create_schedule(
        connection, account_id, thread[0], TASK, "daily", datetime.now(UTC) + timedelta(hours=1)
    )
    assert made is not None
    run = _make_run(connection, account_id, thread[0], made[0])
    assert start_attempt(connection, run[0]) is not None
    connection.execute(
        "UPDATE run SET started_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
    )

    with caplog.at_level(logging.INFO, logger=LOGGER):
        counts = tick(_model())

    assert counts["stuck"] == 1
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "dead"
    assert _lines(connection, account_id, thread[0], "result") == [FAILED_LINE]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(run[0]) in errors[0].getMessage()
    connection.execute(
        "UPDATE schedule SET next_run_at = now() - interval '1 minute' WHERE id = %s", (made[0],)
    )

    again = tick(_model())

    assert again["started"] == 1
    assert len(enqueued) == 1
    assert len(_runs(connection, account_id)) == 2


def test_a_late_result_after_the_run_was_marked_dead_is_dropped(connection, account, caplog):
    account_id, email = account
    thread = create_thread(connection, account_id)
    made = create_schedule(
        connection, account_id, thread[0], TASK, "daily", datetime.now(UTC) + timedelta(hours=1)
    )
    assert made is not None
    run = _make_run(connection, account_id, thread[0], made[0])
    started = threading.Event()
    release = threading.Event()

    class _Held(FakeModel):
        def stream(self, prompt, blocks, tools=()):
            started.set()
            release.wait(timeout=30)
            return super().stream(prompt, blocks, tools)

    outcome: list[str] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            outcome.append(
                execute(
                    run[0],
                    _Held(reply=RESULT, input_tokens=1, output_tokens=1, total_tokens=2),
                )
            )
        except BaseException as error:
            errors.append(error)

    worker_thread = threading.Thread(target=work)
    worker_thread.start()
    try:
        assert started.wait(timeout=30)
        connection.execute(
            "UPDATE run SET started_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
        )
        with caplog.at_level(logging.INFO, logger=LOGGER):
            assert tick(_model())["stuck"] == 1
    finally:
        release.set()
        worker_thread.join(timeout=30)

    assert not worker_thread.is_alive()
    assert errors == []
    assert outcome == ["dead"]
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "dead" and stored[9] is None
    messages = list_messages(connection, account_id, thread[0]) or []
    assert [m[6] for m in messages] == [FAILED_LINE]
    assert RESULT not in [m[6] for m in messages]
    assert _usage(connection, email) == [("schedule", run[0], None, False)]


def test_a_tick_does_not_overwrite_a_run_that_just_finished(connection, account):
    account_id, _ = account
    thread = create_thread(connection, account_id)
    made = create_schedule(
        connection, account_id, thread[0], TASK, "daily", datetime.now(UTC) + timedelta(hours=1)
    )
    assert made is not None
    run = _make_run(connection, account_id, thread[0], made[0])
    assert execute(run[0], _model(RESULT)) == "ready"
    connection.execute(
        "UPDATE run SET started_at = now() - interval '91 minutes' WHERE id = %s", (run[0],)
    )

    assert tick(_model())["stuck"] == 0

    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "ready"
    assert _lines(connection, account_id, thread[0], "result") == [RESULT]


def _age(connection, run_id, minutes):
    connection.execute(
        "UPDATE run SET created_at = now() - make_interval(mins => %s) WHERE id = %s",
        (minutes, run_id),
    )


def _age_attempt(connection, run_id, minutes):
    connection.execute(
        "UPDATE run SET started_at = now() - make_interval(mins => %s) WHERE id = %s",
        (minutes, run_id),
    )


def _quiet_schedule(connection, account_id):
    thread = create_thread(connection, account_id)
    made = create_schedule(
        connection, account_id, thread[0], TASK, "daily", datetime.now(UTC) + timedelta(hours=1)
    )
    assert made is not None
    return thread[0], made[0]


def test_a_run_left_queued_is_enqueued_again_and_then_runs(connection, account, enqueued):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    _age(connection, run[0], 3)

    counts = tick(_model())

    assert counts["requeued"] == 1
    assert enqueued == [run[0]]
    assert execute(run[0], _model(RESULT)) == "ready"
    assert tick(_model())["requeued"] == 0


def test_a_fresh_queued_run_is_left_alone(connection, account, enqueued):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    _make_run(connection, account_id, thread_id, schedule_id)

    counts = tick(_model())

    assert counts["requeued"] == 0
    assert enqueued == []


def test_a_run_queued_for_too_long_is_dead_and_frees_the_schedule(
    connection, account, enqueued, caplog
):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    _age(connection, run[0], 31)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        counts = tick(_model())

    assert counts["stuck"] == 1 and counts["requeued"] == 0
    assert enqueued == []
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "dead"
    assert _lines(connection, account_id, thread_id, "result") == [FAILED_LINE]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(run[0]) in errors[0].getMessage()
    connection.execute(
        "UPDATE schedule SET next_run_at = now() - interval '1 minute' WHERE id = %s",
        (schedule_id,),
    )

    assert tick(_model())["started"] == 1
    assert len(enqueued) == 1


def test_an_enqueue_that_raises_does_not_stop_the_tick(
    connection, account, monkeypatch, caplog
):
    account_id, _ = account
    first_thread, first = _due_schedule(connection, account_id)
    second_thread, second = _due_schedule(connection, account_id)
    connection.execute(
        "UPDATE schedule SET next_run_at = now() - interval '2 minutes' WHERE id = %s", (first,)
    )
    seen: list[uuid.UUID] = []

    def flaky(run_id: uuid.UUID) -> None:
        seen.append(run_id)
        if len(seen) == 1:
            raise RuntimeError("queue is down")

    monkeypatch.setattr("app.scheduled.enqueue", flaky)

    with caplog.at_level(logging.INFO, logger=LOGGER):
        counts = tick(_model())

    assert counts["started"] == 2
    assert len(seen) == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and str(seen[0]) in errors[0].getMessage()
    assert len(_runs(connection, account_id)) == 2
    assert TASK not in caplog.text


def test_a_working_run_31_minutes_old_is_not_stuck(connection, account, enqueued):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    assert start_attempt(connection, run[0]) is not None
    _age_attempt(connection, run[0], 31)

    counts = tick(_model())

    assert counts["stuck"] == 0
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "working"
    assert _lines(connection, account_id, thread_id, "result") == []


def test_a_run_made_hours_ago_but_started_just_now_is_not_stuck(connection, account, enqueued):
    # It waited in the queue for a long time. The wait is not work.
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    assert start_attempt(connection, run[0]) is not None
    _age(connection, run[0], 5 * 60)

    counts = tick(_model())

    assert counts["stuck"] == 0
    stored = get_run(connection, run[0])
    assert stored is not None and stored[5] == "working"


def test_two_deliveries_at_once_run_the_model_once(connection, account):
    account_id, email = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    class _Held(FakeModel):
        def stream(self, prompt, blocks, tools=()):
            calls.append(1)
            started.set()
            release.wait(timeout=30)
            return super().stream(prompt, blocks, tools)

    outcomes: dict[str, str] = {}

    def deliver(name: str, model: FakeModel) -> None:
        outcomes[name] = execute(run[0], model)

    first = threading.Thread(
        target=deliver,
        args=("first", _Held(reply=RESULT, input_tokens=1, output_tokens=1, total_tokens=2)),
    )
    second_model = _Held(reply=RESULT, input_tokens=1, output_tokens=1, total_tokens=2)
    second = threading.Thread(target=deliver, args=("second", second_model))
    first.start()
    try:
        assert started.wait(timeout=30)
        second.start()
        second.join(timeout=30)
        assert not second.is_alive()
        assert outcomes == {"second": "duplicate"}
    finally:
        release.set()
        first.join(timeout=30)

    assert not first.is_alive()
    assert outcomes["first"] == "ready"
    assert len(calls) == 1
    stored = get_run(connection, run[0])
    assert stored is not None and stored[8] == 1 and stored[5] == "ready"
    messages = list_messages(connection, account_id, thread_id) or []
    assert [m[6] for m in messages] == [RESULT]
    assert len(_usage(connection, email)) == 1
    assert execute(run[0], _model("third")) == "duplicate"


def test_a_retry_after_a_failed_attempt_still_works(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)

    assert execute(run[0], _BrokenModel()) == "retry"
    assert execute(run[0], _model(RESULT)) == "ready"

    stored = get_run(connection, run[0])
    assert stored is not None and stored[8] == 2 and stored[5] == "ready"


def test_a_run_another_copy_delivered_reports_its_real_status(connection, account):
    account_id, _ = account
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    run = _make_run(connection, account_id, thread_id, schedule_id)

    class _Other(FakeModel):
        def stream(self, prompt, blocks, tools=()):
            with connect() as other:
                other.execute(
                    "UPDATE run SET status = 'ready', delivered_at = now() WHERE id = %s",
                    (run[0],),
                )
            return super().stream(prompt, blocks, tools)

    assert execute(run[0], _Other(reply=RESULT, total_tokens=2)) == "ready"
    assert list_messages(connection, account_id, thread_id) == []


def _research_run(connection, account_id):
    thread = create_thread(connection, account_id)
    run = create_run(connection, account_id, thread[0], None, "research", TASK)
    assert run is not None
    return thread[0], run[0]


def test_a_stranded_research_run_goes_back_on_the_research_queue(connection, account, monkeypatch):
    account_id, _ = account
    _, research_run = _research_run(connection, account_id)
    thread_id, schedule_id = _quiet_schedule(connection, account_id)
    schedule_run = _make_run(connection, account_id, thread_id, schedule_id)
    _age(connection, research_run, 3)
    _age(connection, schedule_run[0], 3)
    calls: list[tuple] = []
    monkeypatch.setattr("app.scheduled.enqueue", lambda *args: calls.append(args))

    counts = tick(_model())

    assert counts["requeued"] == 2
    assert sorted(calls, key=len) == [(schedule_run[0],), (research_run, "research")]


def test_a_stuck_research_run_is_given_up_with_the_research_line(connection, account, enqueued):
    account_id, _ = account
    thread_id, run_id = _research_run(connection, account_id)
    assert start_attempt(connection, run_id) is not None
    _age_attempt(connection, run_id, 91)

    counts = tick(_model())

    assert counts["stuck"] == 1
    stored = get_run(connection, run_id)
    assert stored is not None and stored[5] == "dead"
    assert _lines(connection, account_id, thread_id, "result") == [RESEARCH_FAILED_LINE]


def test_a_research_run_queued_for_too_long_is_given_up_with_the_research_line(
    connection, account, enqueued
):
    account_id, _ = account
    thread_id, run_id = _research_run(connection, account_id)
    _age(connection, run_id, 31)

    counts = tick(_model())

    assert counts["stuck"] == 1 and enqueued == []
    assert _lines(connection, account_id, thread_id, "result") == [RESEARCH_FAILED_LINE]
