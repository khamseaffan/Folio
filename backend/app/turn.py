import uuid
from dataclasses import dataclass

import psycopg

from app.message import MessageRow, insert_message, list_messages
from app.model import CONTEXT_TOKENS, MODEL_ID, Block, Model
from app.session import set_last_active_at
from app.thread import get_thread
from app.usage import record_usage, tokens_for_email

_PROVIDER = "google"
_STOPPED = "A system admin has stopped your account. Contact khamseaffan@gmail.com."
_CAPPED = "You have reached the allowed usage limit."
_FAILED = "That didn't go through."
_HARM = "A task that would cause physical harm stops and asks."
_KEPT = 10


@dataclass(frozen=True)
class TurnResult:
    body: str
    kind: str
    omitted: tuple[MessageRow, ...]


def _system_prompt(display_name: str | None, summary: str | None) -> str:
    parts: list[str] = []
    if display_name is not None:
        parts.append(f"The person's name is {display_name}.")
    parts.append(_HARM)
    if summary is not None:
        parts.append(summary)
    return "\n".join(parts)


def _as_blocks(messages: list[MessageRow]) -> list[Block]:
    blocks: list[Block] = []
    for message in messages:
        role = message[4]
        if role == "person":
            blocks.append(Block(role="person", text=message[6]))
        elif role == "assistant":
            blocks.append(Block(role="assistant", text=message[6]))
    return blocks


def select_blocks(
    prompt: str,
    messages: list[MessageRow],
    model: Model,
) -> tuple[list[Block], list[MessageRow]]:
    chat = [message for message in messages if message[5] == "chat"]
    ordered = sorted(chat, key=lambda message: (message[7], str(message[0])))
    persons = [message for message in ordered if message[4] == "person"]
    assistants = [message for message in ordered if message[4] == "assistant"]
    required_ids = {message[0] for message in persons[-_KEPT:]}
    required_ids.update(message[0] for message in assistants[-_KEPT:])
    older = [message for message in ordered if message[0] not in required_ids]
    chosen = set(required_ids)
    omitted: list[MessageRow] = []
    newest_first = list(reversed(older))
    for index, message in enumerate(newest_first):
        trial_ids = chosen | {message[0]}
        trial = [item for item in ordered if item[0] in trial_ids]
        if model.count_tokens(prompt, _as_blocks(trial)) <= CONTEXT_TOKENS:
            chosen.add(message[0])
        else:
            rejected = {item[0] for item in newest_first[index:]}
            omitted = [item for item in ordered if item[0] in rejected]
            break
    selected = [message for message in ordered if message[0] in chosen]
    return _as_blocks(selected), omitted


def _account(connection: psycopg.Connection, account_id: uuid.UUID) -> tuple[str, str | None]:
    row = connection.execute(
        """
        SELECT email, display_name
        FROM account
        WHERE id = %s
        """,
        (account_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("account is missing")
    return row[0], row[1]


def _limit(connection: psycopg.Connection, email: str) -> tuple[int, bool]:
    row = connection.execute(
        """
        SELECT token_cap, stopped
        FROM email_limit
        WHERE email = %s
        """,
        (email,),
    ).fetchone()
    if row is None:
        raise RuntimeError("email limit is missing")
    return int(row[0]), bool(row[1])


def _fail(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    email: str,
    omitted: list[MessageRow],
) -> TurnResult:
    stored = insert_message(
        connection,
        account_id,
        thread_id,
        "assistant",
        "failure",
        _FAILED,
    )
    if stored is None:
        raise RuntimeError("failure message was not stored")
    record_usage(
        connection,
        account_id,
        email,
        "chat",
        _PROVIDER,
        MODEL_ID,
        None,
        None,
        None,
        None,
        None,
    )
    return TurnResult(body=_FAILED, kind="failure", omitted=tuple(omitted))


def run_turn(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    model: Model,
) -> TurnResult | str | None:
    thread = get_thread(connection, account_id, thread_id)
    if thread is None:
        return None
    email, display_name = _account(connection, account_id)
    token_cap, stopped = _limit(connection, email)
    if stopped:
        return _STOPPED
    if tokens_for_email(connection, email) >= token_cap:
        return _CAPPED
    person = insert_message(connection, account_id, thread_id, "person", "chat", text)
    if person is None:
        return None
    set_last_active_at(connection, session_id, account_id)
    prompt = _system_prompt(display_name, thread[3])
    messages = list_messages(connection, account_id, thread_id)
    if messages is None:
        raise RuntimeError("thread is missing")
    blocks, omitted = select_blocks(prompt, messages, model)
    try:
        reply = model.complete(prompt, blocks)
    except Exception:
        return _fail(connection, account_id, thread_id, email, omitted)
    assistant = insert_message(
        connection,
        account_id,
        thread_id,
        "assistant",
        "chat",
        reply.text,
    )
    if assistant is None:
        raise RuntimeError("assistant message was not stored")
    set_last_active_at(connection, session_id, account_id)
    record_usage(
        connection,
        account_id,
        email,
        "chat",
        _PROVIDER,
        MODEL_ID,
        reply.input_tokens,
        reply.output_tokens,
        reply.total_tokens,
        assistant[0],
        None,
    )
    return TurnResult(body=reply.text, kind="chat", omitted=tuple(omitted))
