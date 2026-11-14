import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime

import psycopg

from app import operations
from app.db import connect
from app.knowledge_file import PROFILE, get_file
from app.loop import stream_turn
from app.message import insert_message
from app.model import Block, Model, Reply, ToolCall, open_model
from app.queue import enqueue
from app.run import (
    RunRow,
    create_run,
    finish_run,
    start_attempt,
    stuck_runs,
)
from app.schedule import ScheduleRow, due_schedules, set_next_run
from app.thread import get_thread
from app.turn import _account, _limit, _system_prompt, _who
from app.usage import record_usage, tokens_for_email

FAILED_LINE = "A scheduled run failed."
NO_NEXT_LINE = "This schedule has no next time now. Tell Folio when it should run again."
MAX_ATTEMPTS = 4

_OPERATION = "schedule"
_log = logging.getLogger("folio.worker")

_NEXT_TIME = (
    "You work out when a schedule runs next. You are given the person's words for when it "
    "should run, which include their time zone, and the time now. Answer with the next date "
    "and time after now, in ISO 8601 with a UTC offset, and nothing else. If it should not "
    "run again, answer with the single word none."
)
_RUN_LINE = (
    "You are running a scheduled task while the person is away. Do the task and write the "
    "result for them. Do not ask a question."
)


def _blocked(connection: psycopg.Connection, account_id: uuid.UUID) -> tuple[str, bool] | None:
    # The email, and whether the account may start no work now. None when the account is gone.
    try:
        email, _ = _account(connection, account_id)
        cap, stopped = _limit(connection, email)
    except RuntimeError:
        return None
    return email, stopped or tokens_for_email(connection, email) >= cap


def next_time(
    model: Model, when_text: str, now: datetime
) -> tuple[datetime | None, Reply | None, bool]:
    stamp = now.isoformat(timespec="minutes")
    blocks = [Block(role="person", text=f"The person's words: {when_text}\nThe time now: {stamp}")]
    try:
        reply = model.complete(_NEXT_TIME, blocks)
    except Exception:
        return None, None, True
    answer = reply.text.strip().strip(".\"'` ").lower()
    if answer == "none":
        return None, reply, False
    try:
        parsed = datetime.fromisoformat(reply.text.strip())
    except ValueError:
        return None, reply, True
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed <= now:
        return None, reply, True
    return parsed, reply, False


def _write_line(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID | None,
    line: str,
) -> None:
    if thread_id is not None:
        # None back means the thread is gone, which is nothing to tell.
        insert_message(connection, account_id, thread_id, "assistant", "failure", line)


def _record(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    email: str,
    model: Model,
    replies: list[Reply],
    run_id: uuid.UUID | None,
    message_id: uuid.UUID | None,
) -> None:
    # The account can be deleted meanwhile. The row is kept for the email without it.
    present = connection.execute("SELECT id FROM account WHERE id = %s", (account_id,)).fetchone()
    stored_account = account_id if present is not None else None
    for reply in replies:
        record_usage(
            connection,
            stored_account,
            email,
            _OPERATION,
            *_who(model),
            reply.input_tokens,
            reply.output_tokens,
            reply.total_tokens,
            message_id,
            run_id,
            reply.estimated,
        )


def _fire(model: Model, schedule: ScheduleRow, counts: dict[str, int]) -> None:
    schedule_id, account_id, thread_id, task, when_text, due_at = schedule[:6]
    if due_at is None:
        return
    with connect() as connection:
        state = _blocked(connection, account_id)
    if state is None:
        return
    email, blocked = state
    if blocked:
        counts["held"] += 1
        return
    # The model call is outside every transaction, so a slow answer holds no lock.
    following, reply, failed = next_time(model, when_text, datetime.now(UTC))
    new_run: RunRow | None = None
    with connect() as connection:
        _record(
            connection,
            account_id,
            email,
            model,
            [reply or Reply("", None, None, None)],
            None,
            None,
        )
        if not set_next_run(connection, account_id, schedule_id, due_at, following):
            # Another tick took this occurrence.
            return
        new_run = create_run(connection, account_id, thread_id, schedule_id, "schedule", task)
        if failed:
            _write_line(connection, account_id, thread_id, NO_NEXT_LINE)
    if failed:
        _log.error("schedule %s has no next time, the answer could not be used", schedule_id)
    if new_run is None:
        counts["skipped"] += 1
        return
    counts["started"] += 1
    enqueue(new_run[0])


def _free_stuck(counts: dict[str, int]) -> None:
    with connect() as connection:
        stuck = stuck_runs(connection)
    for run in stuck:
        run_id, account_id, thread_id = run[0], run[1], run[2]
        with connect() as connection:
            finish_run(connection, run_id, "dead", run[7], False)
            _write_line(connection, account_id, thread_id, FAILED_LINE)
        counts["stuck"] += 1
        _log.error("run %s was working for too long and is marked dead", run_id)


def tick(model: Model) -> dict[str, int]:
    counts = {"due": 0, "started": 0, "skipped": 0, "held": 0, "stuck": 0}
    with connect() as connection:
        due = due_schedules(connection)
    counts["due"] = len(due)
    for schedule in due:
        try:
            _fire(model, schedule, counts)
        except Exception as error:
            _log.error("schedule %s could not be started: %s", schedule[0], type(error).__name__)
    try:
        _free_stuck(counts)
    except Exception as error:
        _log.error("stuck runs could not be freed: %s", type(error).__name__)
    return counts


def _answer(
    model: Model,
    run: RunRow,
    email: str,
    display_name: str | None,
    summary: str | None,
    profile: str | None,
) -> tuple[str, list[Reply], bool]:
    account_id, thread_id = run[1], run[2]
    prompt = _system_prompt(display_name, summary, profile) + "\n" + _RUN_LINE
    blocks = [Block(role="person", text=run[6])]
    turn = operations.Turn(opened=set())
    parts: list[str] = []
    replies: list[Reply] = []

    def call_tool(call: ToolCall) -> str:
        if call.unreadable:
            result = {"ok": False, "code": "invalid", "error": operations.BAD_INPUT}
        else:
            with connect() as connection:
                result = operations.call(
                    connection,
                    account_id,
                    operations.Context(thread_id, turn),
                    call.name,
                    call.arguments,
                    in_run=True,
                )
        return json.dumps(result)

    failed = False
    try:
        for event in stream_turn(
            model,
            prompt,
            blocks,
            threading.Event(),
            operations.agent_tools(in_run=True),
            call_tool,
        ):
            if not isinstance(event, dict):
                continue
            text = event.get("text")
            if isinstance(text, str):
                parts.append(text)
            usage = event.get("usage")
            if isinstance(usage, Reply):
                replies.append(usage)
    except Exception as error:
        failed = True
        _log.error("run %s attempt %s failed: %s", run[0], run[8], type(error).__name__)
    return "".join(parts), replies, failed


def execute(run_id: uuid.UUID, model: Model | None = None) -> str:
    with connect() as connection:
        run = start_attempt(connection, run_id)
        if run is None:
            return "duplicate"
        account_id, thread_id = run[1], run[2]
        state = _blocked(connection, account_id)
        if state is None:
            return "cancelled"
        email, blocked = state
        thread = get_thread(connection, account_id, thread_id) if thread_id is not None else None
        if blocked or thread is None:
            # A stopped or capped account, or a thread that is gone, is not worth another try.
            finish_run(connection, run_id, "cancelled", 0, False)
            return "cancelled"
        display_name = _account(connection, account_id)[1]
        profile_file = get_file(connection, account_id, PROFILE)
        profile = profile_file[3] if profile_file is not None else None
        summary = thread[3]
    # The start is committed above, so no transaction is open while the agent loop runs.
    chosen = model if model is not None else open_model()
    started = time.monotonic()
    text, replies, failed = _answer(chosen, run, email, display_name, summary, profile)
    work_ms = int((time.monotonic() - started) * 1000)
    if not failed:
        with connect() as connection:
            message = None
            if thread_id is not None:
                message = insert_message(
                    connection, account_id, thread_id, "assistant", "result", text, run_id
                )
            _record(
                connection,
                account_id,
                email,
                chosen,
                replies,
                run_id,
                message[0] if message is not None else None,
            )
            finish_run(connection, run_id, "ready", work_ms, True)
        return "ready"
    with connect() as connection:
        _record(connection, account_id, email, chosen, replies, run_id, None)
        if run[8] < MAX_ATTEMPTS:
            return "retry"
        finish_run(connection, run_id, "dead", work_ms, False)
        _write_line(connection, account_id, thread_id, FAILED_LINE)
    _log.error("run %s is dead after %s attempts", run_id, run[8])
    return "dead"
