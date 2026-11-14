import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, ValidationError

from app import live, thread
from app.model import ToolSpec

NOT_FOUND = "Thread not found."
BAD_NAME = "A thread name is 1 to 80 characters."
BAD_INPUT = "That input is not valid."
UNKNOWN = "Unknown operation."

_NAME_LIMIT = 80

Result = dict[str, Any]


@dataclass(frozen=True)
class Context:
    thread_id: uuid.UUID | None


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


def _refused(code: str, error: str) -> Result:
    return {"ok": False, "code": code, "error": error}


def _item(row: thread.ThreadRow) -> dict[str, str]:
    return {"id": str(row[0]), "name": row[2], "created_at": row[4].isoformat()}


def _name(raw: str) -> str | None:
    name = raw.strip()
    if len(name) < 1 or len(name) > _NAME_LIMIT:
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
