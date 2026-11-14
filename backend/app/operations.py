import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, ValidationError

from app import ability, knowledge_file, live, thread
from app.model import ToolSpec

NOT_FOUND = "Thread not found."
BAD_NAME = "A thread name is 1 to 80 characters."
BAD_INPUT = "That input is not valid."
UNKNOWN = "Unknown operation."
NO_FILE = "File not found."
NO_ABILITY = "Ability not found."
BAD_FILE_NAME = "A file name is 1 to 80 characters."
BAD_TEXT = "That text cannot be stored."
TOO_MANY = "This turn has already opened five files."
PROFILE_TOO_LONG = (
    "profile.md can hold at most 20,000 characters. Shorten it, or move facts into another file."
)
READ_LIMIT = 5
PROFILE_LIMIT = 20_000

_NAME_LIMIT = 80

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


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    input: type[BaseModel]
    run: Callable[[psycopg.Connection, uuid.UUID, Context, Any], Result]
    agent: bool


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
    )
}


def call(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    context: Context,
    name: str,
    arguments: dict[str, object],
) -> Result:
    operation = OPERATIONS.get(name)
    if operation is None:
        return _refused("unknown", UNKNOWN)
    try:
        given = operation.input.model_validate(arguments)
    except ValidationError:
        return _refused("invalid", BAD_INPUT)
    return operation.run(connection, account_id, context, given)


def agent_tools() -> list[ToolSpec]:
    return [
        ToolSpec(operation.name, operation.description, operation.input.model_json_schema())
        for operation in OPERATIONS.values()
        if operation.agent
    ]
