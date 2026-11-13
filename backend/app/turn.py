import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import psycopg

from app.db import connect
from app.message import MessageRow, insert_message, list_messages
from app.model import CONTEXT_TOKENS, MODEL_ID, Block, Model, Reply
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


@dataclass
class _Prepared:
    session_id: uuid.UUID
    account_id: uuid.UUID
    thread_id: uuid.UUID
    email: str
    prompt: str
    blocks: list[Block]
    omitted: list[MessageRow]


class LiveReply:
    def __init__(self) -> None:
        self.stop = threading.Event()
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._listening = True
        self._lock = threading.Lock()

    def publish(self, payload: dict[str, object]) -> None:
        self._put("data: " + json.dumps(payload) + "\n\n")

    def finish(self) -> None:
        self._put(None)

    def _put(self, item: str | None) -> None:
        with self._lock:
            if not self._listening:
                return
            loop = self._loop
            queue = self._queue
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            return

    def _detach(self) -> None:
        # Closing the response is not a stop. end_session does not call this.
        with self._lock:
            self._listening = False

    async def events(self) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(self._queue.get(), 0.25)
                except TimeoutError:
                    continue
                if item is None:
                    return
                yield item
        finally:
            self._detach()


_live_lock = threading.Lock()
_live: dict[tuple[uuid.UUID, uuid.UUID], list[LiveReply]] = {}


def _register(account_id: uuid.UUID, thread_id: uuid.UUID, live: LiveReply) -> None:
    with _live_lock:
        _live.setdefault((account_id, thread_id), []).append(live)


def _unregister(account_id: uuid.UUID, thread_id: uuid.UUID, live: LiveReply) -> None:
    with _live_lock:
        rows = _live.get((account_id, thread_id))
        if rows is None:
            return
        if live in rows:
            rows.remove(live)
        if not rows:
            del _live[(account_id, thread_id)]


def stop_reply(account_id: uuid.UUID, thread_id: uuid.UUID) -> None:
    with _live_lock:
        rows = list(_live.get((account_id, thread_id), ()))
    for live in rows:
        live.stop.set()


def _prepare(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    model: Model,
) -> _Prepared | str | None:
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
    return _Prepared(
        session_id=session_id,
        account_id=account_id,
        thread_id=thread_id,
        email=email,
        prompt=prompt,
        blocks=blocks,
        omitted=omitted,
    )


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
    prepared = _prepare(connection, session_id, account_id, thread_id, text, model)
    if not isinstance(prepared, _Prepared):
        return prepared
    try:
        reply = model.complete(prepared.prompt, prepared.blocks)
    except Exception:
        return _fail(
            connection,
            prepared.account_id,
            prepared.thread_id,
            prepared.email,
            prepared.omitted,
        )
    assistant = insert_message(
        connection,
        prepared.account_id,
        prepared.thread_id,
        "assistant",
        "chat",
        reply.text,
    )
    if assistant is None:
        raise RuntimeError("assistant message was not stored")
    set_last_active_at(connection, prepared.session_id, prepared.account_id)
    record_usage(
        connection,
        prepared.account_id,
        prepared.email,
        "chat",
        _PROVIDER,
        MODEL_ID,
        reply.input_tokens,
        reply.output_tokens,
        reply.total_tokens,
        assistant[0],
        None,
    )
    return TurnResult(body=reply.text, kind="chat", omitted=tuple(prepared.omitted))


def _reply_or_none(stream: object) -> Reply | None:
    read = getattr(stream, "reply", None)
    if not callable(read):
        return None
    failed = False
    try:
        value = read()
    except Exception:
        failed = True
    if failed or not isinstance(value, Reply):
        return None
    return value


def _counts(reply: Reply | None) -> tuple[int | None, int | None, int | None]:
    if reply is None:
        return None, None, None
    return reply.input_tokens, reply.output_tokens, reply.total_tokens


def _save(prepared: _Prepared, text: str, reply: Reply | None, failed: bool, stopped: bool) -> None:
    input_tokens, output_tokens, total_tokens = _counts(reply)
    with connect() as connection:
        if failed and text == "":
            _fail(connection, prepared.account_id, prepared.thread_id, prepared.email, [])
            return
        message_id: uuid.UUID | None = None
        assistant: MessageRow | None = None
        if text != "" or not stopped:
            assistant = insert_message(
                connection,
                prepared.account_id,
                prepared.thread_id,
                "assistant",
                "chat",
                text,
            )
            if assistant is not None:
                message_id = assistant[0]
                set_last_active_at(connection, prepared.session_id, prepared.account_id)
        if failed:
            failure = insert_message(
                connection,
                prepared.account_id,
                prepared.thread_id,
                "assistant",
                "failure",
                _FAILED,
            )
            if failure is None:
                raise RuntimeError("failure message was not stored")
            if assistant is not None:
                # now() is one timestamp for the transaction, so the line would tie the partial.
                connection.execute(
                    """
                    UPDATE message
                    SET created_at = %s + interval '1 microsecond'
                    WHERE id = %s
                      AND account_id = %s
                    """,
                    (assistant[7], failure[0], prepared.account_id),
                )
        record_usage(
            connection,
            prepared.account_id,
            prepared.email,
            "chat",
            _PROVIDER,
            MODEL_ID,
            input_tokens,
            output_tokens,
            total_tokens,
            message_id,
            None,
        )


def _write_reply(live: LiveReply, model: Model, prepared: _Prepared) -> None:
    parts: list[str] = []
    failed = False
    stream: object | None = None
    try:
        stream = model.stream(prepared.prompt, prepared.blocks)
    except Exception:
        failed = True
    if stream is not None and not failed:
        try:
            for chunk in stream:
                if live.stop.is_set():
                    break
                if not isinstance(chunk, str) or chunk == "":
                    continue
                parts.append(chunk)
                live.publish({"text": chunk})
        except Exception:
            failed = True
    reply = _reply_or_none(stream) if stream is not None else None
    stopped = live.stop.is_set()
    try:
        _save(prepared, "".join(parts), reply, failed, stopped)
    except Exception:
        failed = True
    if failed:
        live.publish({"failed": True})
    else:
        live.publish({"done": True})
    live.finish()
    _unregister(prepared.account_id, prepared.thread_id, live)


def start_reply(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    model: Model,
) -> LiveReply | str | None:
    prepared = _prepare(connection, session_id, account_id, thread_id, text, model)
    if not isinstance(prepared, _Prepared):
        return prepared
    # The reply thread can emit a chunk before this request transaction closes.
    connection.commit()
    live = LiveReply()
    _register(account_id, thread_id, live)
    try:
        threading.Thread(
            target=_write_reply,
            args=(live, model, prepared),
            name="folio-reply",
        ).start()
    except Exception:
        _unregister(account_id, thread_id, live)
        raise
    return live
