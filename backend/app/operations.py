import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, ValidationError

from app import ability, knowledge_file, live, report, schedule, thread
from app import queue as run_queue
from app import run as run_table
from app.model import ToolSpec

NOT_FOUND = "Thread not found."
BAD_NAME = "A thread name is 1 to 80 characters."
BAD_INPUT = "That input is not valid."
UNKNOWN = "Unknown operation."
NO_FILE = "File not found."
NO_ABILITY = "Ability not found."
BAD_FILE_NAME = "A file name is 1 to 80 characters."
BAD_TEXT = "That text cannot be stored."
NO_SCHEDULE = "Schedule not found."
BAD_TIME = "Give the next time as a date and time with a time zone, in the future."
BAD_TASK = "A schedule needs a task and the words for when."
NO_REPORT = "Report not found."
NO_RUN = "Run not found."
BAD_REPORT_NAME = "A report name is 1 to 80 characters."
BAD_QUESTION = "A research question is 1 to 2,000 characters."
NOT_AVAILABLE = "Research is not available right now."
RESEARCH_BUSY = "A research run is already working in this thread."
TOO_MANY = "This turn has already opened five files."
PROFILE_TOO_LONG = (
    "profile.md can hold at most 20,000 characters. Shorten it, or move facts into another file."
)
READ_LIMIT = 5
PROFILE_LIMIT = 20_000

_NAME_LIMIT = 80
_SCHEDULE_TEXT_LIMIT = 2_000
_QUESTION_LIMIT = 2_000
_log = logging.getLogger("folio.operations")

Result = dict[str, Any]


@dataclass
class Turn:
    # Names of the knowledge files read in this chat turn, without profile.md.
    opened: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Context:
    thread_id: uuid.UUID | None
    # None for a route call, which has no turn to limit.
    turn: Turn | None = None
    # True when a scheduled run, and not a person in the chat, is calling.
    in_run: bool = False


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    input: type[BaseModel]
    run: Callable[[psycopg.Connection, uuid.UUID, Context, Any], Result]
    agent: bool
    # Left out of a scheduled run, so a task cannot make more schedules.
    chat_only: bool = False


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _NoInput(_Input):
    pass


class _CreateThread(_Input):
    name: str | None = None


class _RenameThread(_Input):
    thread_id: uuid.UUID | None = None
    name: str


class _DeleteThread(_Input):
    thread_id: uuid.UUID | None = None


class _ReadKnowledge(_Input):
    name: str


class _SearchKnowledge(_Input):
    query: str


class _WriteKnowledge(_Input):
    name: str
    body: str


class _AppendKnowledge(_Input):
    name: str
    text: str


class _EditKnowledge(_Input):
    name: str
    find: str
    replace: str


class _WriteAbility(_Input):
    ability_id: uuid.UUID | None = None
    name: str
    body: str


class _DeleteAbility(_Input):
    ability_id: uuid.UUID


class _WriteSchedule(_Input):
    schedule_id: uuid.UUID | None = None
    task: str
    when_text: str
    next_run_at: str


class _DeleteSchedule(_Input):
    schedule_id: uuid.UUID


class _StartResearch(_Input):
    question: str


class _CancelResearch(_Input):
    run_id: uuid.UUID


class _RenameReport(_Input):
    report_id: uuid.UUID
    name: str


class _DeleteReport(_Input):
    report_id: uuid.UUID


def _refused(code: str, error: str) -> Result:
    return {"ok": False, "code": code, "error": error}


def _item(row: thread.ThreadRow) -> dict[str, str]:
    return {"id": str(row[0]), "name": row[2], "created_at": row[4].isoformat()}


def _clean(raw: str) -> bool:
    # Postgres text cannot hold a null character, and a lone surrogate cannot be encoded.
    if "\x00" in raw:
        return False
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _too_long(name: str, body: str) -> bool:
    return name == knowledge_file.PROFILE and len(body) > PROFILE_LIMIT


def _name(raw: str) -> str | None:
    name = raw.strip()
    if len(name) < 1 or len(name) > _NAME_LIMIT or not _clean(name):
        return None
    return name


def _list_threads(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = thread.list_threads(connection, account_id)
    return {"ok": True, "threads": [_item(row) for row in rows]}


def _create_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _CreateThread,
) -> Result:
    name: str | None = None
    if given.name is not None:
        name = _name(given.name)
        if name is None:
            return _refused("invalid", BAD_NAME)
    return {"ok": True, "thread": _item(thread.create_thread(connection, account_id, name))}


def _rename_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _RenameThread,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_NAME)
    thread_id = given.thread_id or context.thread_id
    if thread_id is None:
        return _refused("not_found", NOT_FOUND)
    renamed = thread.rename_thread(connection, account_id, thread_id, name)
    if renamed is None:
        return _refused("not_found", NOT_FOUND)
    return {"ok": True, "thread": _item(renamed)}


def _delete_thread(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _DeleteThread,
) -> Result:
    thread_id = given.thread_id or context.thread_id
    if thread_id is None:
        return _refused("not_found", NOT_FOUND)
    if thread.get_thread(connection, account_id, thread_id) is None:
        return _refused("not_found", NOT_FOUND)
    live.stop_reply(account_id, thread_id)
    if not thread.delete_thread(connection, account_id, thread_id):
        return _refused("not_found", NOT_FOUND)
    return {"ok": True, "thread_id": str(thread_id)}


def _file_result(row: knowledge_file.KnowledgeRow) -> Result:
    return {"ok": True, "name": row[2], "byte_size": row[4]}


def _list_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = knowledge_file.list_files(connection, account_id)
    return {
        "ok": True,
        "files": [
            {"name": row[2], "byte_size": row[4], "updated_at": row[6].isoformat()} for row in rows
        ],
    }


def _search_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _SearchKnowledge,
) -> Result:
    if not _clean(given.query):
        return _refused("invalid", BAD_TEXT)
    found = knowledge_file.search_files(connection, account_id, given.query, READ_LIMIT)
    return {
        "ok": True,
        "matches": [{"name": name, "snippet": snippet} for name, snippet in found],
    }


def _read_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _ReadKnowledge,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    row = knowledge_file.get_file(connection, account_id, name)
    if row is None:
        return _refused("not_found", NO_FILE)
    turn = context.turn
    if turn is not None and name != knowledge_file.PROFILE and name not in turn.opened:
        if len(turn.opened) >= READ_LIMIT:
            return _refused("limit", TOO_MANY)
        turn.opened.add(name)
    return {"ok": True, "name": row[2], "body": row[3]}


def _write_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _WriteKnowledge,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    if not _clean(given.body):
        return _refused("invalid", BAD_TEXT)
    if _too_long(name, given.body):
        return _refused("limit", PROFILE_TOO_LONG)
    return _file_result(knowledge_file.put_file(connection, account_id, name, given.body))


def _append_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _AppendKnowledge,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    if not _clean(given.text):
        return _refused("invalid", BAD_TEXT)
    existing = knowledge_file.get_file(connection, account_id, name, lock=True)
    body = given.text
    if existing is not None and existing[3] != "":
        joiner = "" if existing[3].endswith("\n") else "\n"
        body = existing[3] + joiner + given.text
    if _too_long(name, body):
        return _refused("limit", PROFILE_TOO_LONG)
    return _file_result(knowledge_file.put_file(connection, account_id, name, body))


def _edit_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _EditKnowledge,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    if not (_clean(given.find) and _clean(given.replace)):
        return _refused("invalid", BAD_TEXT)
    row = knowledge_file.get_file(connection, account_id, name, lock=True)
    if row is None:
        return _refused("not_found", NO_FILE)
    count = row[3].count(given.find) if given.find != "" else 0
    if count != 1:
        return _refused(
            "invalid",
            f"The text to find appears {count} times in the file. It must appear exactly once.",
        )
    body = row[3].replace(given.find, given.replace)
    if _too_long(name, body):
        return _refused("limit", PROFILE_TOO_LONG)
    return _file_result(knowledge_file.put_file(connection, account_id, name, body))


def _delete_knowledge(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _ReadKnowledge,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    if not knowledge_file.delete_file(connection, account_id, name):
        return _refused("not_found", NO_FILE)
    return {"ok": True, "name": name}


def _delete_profile(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    return {"ok": True, "deleted": knowledge_file.delete_all(connection, account_id)}


def _list_abilities(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = ability.list_abilities(connection, account_id)
    return {
        "ok": True,
        "abilities": [{"id": str(row[0]), "name": row[2], "body": row[3]} for row in rows],
    }


def _write_ability(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _WriteAbility,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_FILE_NAME)
    if not _clean(given.body):
        return _refused("invalid", BAD_TEXT)
    if given.ability_id is None:
        row = ability.create_ability(connection, account_id, name, given.body)
    else:
        updated = ability.update_ability(connection, account_id, given.ability_id, name, given.body)
        if updated is None:
            return _refused("not_found", NO_ABILITY)
        row = updated
    return {"ok": True, "ability": {"id": str(row[0]), "name": row[2]}}


def _delete_ability(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _DeleteAbility,
) -> Result:
    if not ability.delete_ability(connection, account_id, given.ability_id):
        return _refused("not_found", NO_ABILITY)
    return {"ok": True, "ability_id": str(given.ability_id)}


def _schedule_text(raw: str) -> str | None:
    text = raw.strip()
    if len(text) < 1 or len(text) > _SCHEDULE_TEXT_LIMIT or not _clean(text):
        return None
    return text


def _future_time(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    if parsed <= datetime.now(UTC):
        return None
    return parsed


def _schedule_item(row: schedule.ScheduleRow) -> dict[str, str | None]:
    return {
        "id": str(row[0]),
        "task": row[3],
        "when_text": row[4],
        "next_run_at": row[5].isoformat() if row[5] is not None else None,
    }


def _list_schedules(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = schedule.list_schedules(connection, account_id)
    return {
        "ok": True,
        "schedules": [{**_schedule_item(row), "thread_id": str(row[2])} for row in rows],
    }


def _write_schedule(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _WriteSchedule,
) -> Result:
    task = _schedule_text(given.task)
    when_text = _schedule_text(given.when_text)
    if task is None or when_text is None:
        return _refused("invalid", BAD_TASK)
    next_run_at = _future_time(given.next_run_at)
    if next_run_at is None:
        return _refused("invalid", BAD_TIME)
    if given.schedule_id is None:
        if context.thread_id is None:
            return _refused("not_found", NOT_FOUND)
        row = schedule.create_schedule(
            connection, account_id, context.thread_id, task, when_text, next_run_at
        )
        if row is None:
            return _refused("not_found", NOT_FOUND)
    else:
        row = schedule.update_schedule(
            connection, account_id, given.schedule_id, task, when_text, next_run_at
        )
        if row is None:
            return _refused("not_found", NO_SCHEDULE)
    return {"ok": True, "schedule": _schedule_item(row)}


def _delete_schedule(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _DeleteSchedule,
) -> Result:
    if not schedule.delete_schedule(connection, account_id, given.schedule_id):
        return _refused("not_found", NO_SCHEDULE)
    return {"ok": True, "schedule_id": str(given.schedule_id)}


def _start_research(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _StartResearch,
) -> Result:
    # Imported here because turn imports this module.
    from app import turn

    question = given.question.strip()
    if len(question) < 1 or len(question) > _QUESTION_LIMIT or not _clean(question):
        return _refused("invalid", BAD_QUESTION)
    if context.thread_id is None:
        return _refused("not_found", NOT_FOUND)
    if thread.get_thread(connection, account_id, context.thread_id) is None:
        return _refused("not_found", NOT_FOUND)
    # A schedule that fires often must not pile research runs up while the cap is far away.
    # A person in the chat is not held by this.
    if context.in_run and run_table.research_in_flight(connection, account_id, context.thread_id):
        return _refused("busy", RESEARCH_BUSY)
    email, _ = turn._account(connection, account_id)
    cap, stopped = turn._limit(connection, email)
    if stopped:
        return _refused("limit", turn._STOPPED)
    if turn.tokens_for_email(connection, email) >= cap:
        return _refused("limit", turn._CAPPED)
    row = run_table.create_run(
        connection, account_id, context.thread_id, None, "research", question
    )
    if row is None:
        return _refused("not_found", NOT_FOUND)
    # The worker may pick the run up the moment it is on the queue, from its own connection.
    connection.commit()
    try:
        run_queue.enqueue(row[0], "research")
    except run_queue.QueueNotConfigured:
        _log.error("research run %s could not be put on a queue: not configured", row[0])
        run_table.finish_working(connection, row[0], "failed", 0, "queued")
        connection.commit()
        return _refused("unavailable", NOT_AVAILABLE)
    except Exception as error:
        # The tick puts a queued run back on its queue after two minutes.
        _log.error("research run %s was not put on the queue: %s", row[0], type(error).__name__)
    return {"ok": True, "run": {"id": str(row[0]), "question": question, "status": "queued"}}


def _cancel_research(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _CancelResearch,
) -> Result:
    if not run_table.cancel_run(connection, account_id, given.run_id):
        return _refused("not_found", NO_RUN)
    return {"ok": True, "run_id": str(given.run_id)}


def _list_research_runs(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = run_table.list_in_flight(connection, account_id, "research")
    return {
        "ok": True,
        "runs": [
            {
                "id": str(row[0]),
                "question": row[6],
                "status": row[5],
                "created_at": row[10].isoformat(),
            }
            for row in rows
        ],
    }


def _list_reports(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _NoInput,
) -> Result:
    rows = report.list_reports(connection, account_id)
    return {
        "ok": True,
        "reports": [
            {
                "id": str(row[0]),
                "name": row[4],
                "question": row[5],
                "created_at": row[11].isoformat(),
            }
            for row in rows
        ],
    }


def _rename_report(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _RenameReport,
) -> Result:
    name = _name(given.name)
    if name is None:
        return _refused("invalid", BAD_REPORT_NAME)
    renamed = report.rename_report(connection, account_id, given.report_id, name)
    if renamed is None:
        return _refused("not_found", NO_REPORT)
    return {"ok": True, "report": {"id": str(renamed[0]), "name": renamed[4]}}


def _delete_report(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    given: _DeleteReport,
) -> Result:
    if not report.delete_report(connection, account_id, given.report_id):
        return _refused("not_found", NO_REPORT)
    return {"ok": True, "report_id": str(given.report_id)}


OPERATIONS: dict[str, Operation] = {
    operation.name: operation
    for operation in (
        Operation(
            "list_threads",
            "List the person's threads with their ids and names.",
            _NoInput,
            _list_threads,
            True,
        ),
        Operation(
            "create_thread",
            "Create a new thread, with a name of 1 to 80 characters if one is given.",
            _CreateThread,
            _create_thread,
            True,
        ),
        Operation(
            "rename_thread",
            "Rename a thread. Without a thread id it renames the thread of this conversation.",
            _RenameThread,
            _rename_thread,
            True,
        ),
        Operation(
            "delete_thread",
            "Delete a thread with its messages. It cannot be restored. Without a thread id it "
            "deletes the thread of this conversation.",
            _DeleteThread,
            _delete_thread,
            True,
        ),
        Operation(
            "list_knowledge",
            "List the person's knowledge files with their sizes and update times.",
            _NoInput,
            _list_knowledge,
            True,
        ),
        Operation(
            "search_knowledge",
            "Search the person's knowledge files. A file matches when it contains any of the words "
            "or its name does. Returns up to five files, best match first, each with a short "
            "piece of the matching text. If nothing is found, try other words before saying "
            "something is not stored.",
            _SearchKnowledge,
            _search_knowledge,
            True,
        ),
        Operation(
            "read_knowledge",
            "Read the whole text of one knowledge file. At most five different files can be "
            "opened in one turn.",
            _ReadKnowledge,
            _read_knowledge,
            True,
        ),
        Operation(
            "write_knowledge",
            "Create a knowledge file, or replace all of its text. Use it for a new file or to "
            "reorganise one. profile.md can hold at most 20,000 characters.",
            _WriteKnowledge,
            _write_knowledge,
            True,
        ),
        Operation(
            "append_knowledge",
            "Add text to the end of a knowledge file. Creates the file when it is missing.",
            _AppendKnowledge,
            _append_knowledge,
            True,
        ),
        Operation(
            "edit_knowledge",
            "Replace one piece of text in a knowledge file with another. The text to find must "
            "appear exactly once in the file.",
            _EditKnowledge,
            _edit_knowledge,
            True,
        ),
        Operation(
            "delete_knowledge",
            "Delete one knowledge file. It cannot be restored.",
            _ReadKnowledge,
            _delete_knowledge,
            True,
        ),
        Operation(
            "delete_profile",
            "Delete every knowledge file of the person, profile.md included. Abilities stay. It "
            "cannot be restored.",
            _NoInput,
            _delete_profile,
            True,
        ),
        Operation(
            "list_abilities",
            "List the person's abilities with their ids, names, and text.",
            _NoInput,
            _list_abilities,
            True,
        ),
        Operation(
            "write_ability",
            "Create an ability, or replace the one with the given id. An ability is a way of "
            "working the person wants you to follow.",
            _WriteAbility,
            _write_ability,
            True,
        ),
        Operation(
            "delete_ability",
            "Delete one ability by its id.",
            _DeleteAbility,
            _delete_ability,
            True,
        ),
        Operation(
            "list_schedules",
            "List the person's schedules with their ids, tasks, the words for when, the next "
            "time, and the thread each belongs to.",
            _NoInput,
            _list_schedules,
            True,
        ),
        Operation(
            "write_schedule",
            "Create a schedule, or replace the one with the given id. A schedule is work Folio "
            "does by itself at a time the person names, and it writes the result in a thread. "
            "A new schedule belongs to the thread of this conversation. Put the person's time "
            "zone into when_text, for example 'every weekday at 09:00, America/New_York'. "
            "Give next_run_at as the next time it should run, in ISO 8601 with an offset, "
            "in the future.",
            _WriteSchedule,
            _write_schedule,
            True,
            True,
        ),
        Operation(
            "delete_schedule",
            "Delete one schedule by its id. It cannot be restored.",
            _DeleteSchedule,
            _delete_schedule,
            True,
            True,
        ),
        Operation(
            "start_research",
            "Start a research run for the question, written out in full. Folio searches the "
            "web, reads pages, and writes one report with its sources, which appears in "
            "Reports when it is ready. It can take up to 30 minutes.",
            _StartResearch,
            _start_research,
            True,
            False,
        ),
        Operation(
            "cancel_research",
            "Cancel one research run that is waiting or working, by its id. No report is "
            "written for a cancelled run.",
            _CancelResearch,
            _cancel_research,
            True,
            True,
        ),
        Operation(
            "list_research_runs",
            "List the research runs that are waiting or working, with their ids and questions.",
            _NoInput,
            _list_research_runs,
            True,
            True,
        ),
        Operation(
            "list_reports",
            "List the person's reports with their ids, names, questions, and times.",
            _NoInput,
            _list_reports,
            True,
            True,
        ),
        Operation(
            "rename_report",
            "Give a report a new name of 1 to 80 characters.",
            _RenameReport,
            _rename_report,
            True,
            True,
        ),
        Operation(
            "delete_report",
            "Delete one report with its source list. It cannot be restored.",
            _DeleteReport,
            _delete_report,
            True,
            True,
        ),
    )
}


def call(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    name: str,
    arguments: dict[str, object],
    in_run: bool = False,
) -> Result:
    operation = OPERATIONS.get(name)
    if operation is None or (in_run and operation.chat_only):
        return _refused("unknown", UNKNOWN)
    if in_run:
        context = replace(context, in_run=True)
    try:
        given = operation.input.model_validate(arguments)
    except ValidationError:
        return _refused("invalid", BAD_INPUT)
    return operation.run(connection, account_id, context, given)


def agent_tools(in_run: bool = False) -> list[ToolSpec]:
    return [
        ToolSpec(operation.name, operation.description, operation.input.model_json_schema())
        for operation in OPERATIONS.values()
        if operation.agent and not (in_run and operation.chat_only)
    ]
