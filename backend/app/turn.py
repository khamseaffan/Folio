import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import psycopg

from app.db import connect
from app.loop import ask, stream_turn
from app.message import (
    MessageRow,
    delete_failure,
    insert_message,
    list_messages,
    update_message_body,
)
from app.model import CONTEXT_TOKENS, MODEL_ID, Block, Model, Reply
from app.session import set_last_active_at
from app.thread import ThreadRow, get_thread, set_summary
from app.usage import record_usage, tokens_for_email

_PROVIDER = "google"


def _window(model: Model) -> int:
    context = getattr(model, "context_tokens", None)
    if isinstance(context, int):
        return context
    return CONTEXT_TOKENS


def _who(model: Model) -> tuple[str, str]:
    provider = getattr(model, "provider", None)
    model_id = getattr(model, "model_id", None)
    if not isinstance(provider, str) or provider == "":
        provider = _PROVIDER
    if not isinstance(model_id, str) or model_id == "":
        model_id = MODEL_ID
    return provider, model_id
_STOPPED = "A system admin has stopped your account. Contact khamseaffan@gmail.com."
_CAPPED = "You have reached the allowed usage limit."
_FAILED = "That didn't go through."
NO_FAILURE = "There is no failed reply."
_HARM = "A task that would cause physical harm stops and asks."
_FOLD = "Summarize the earlier messages so the next reply can continue."
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
        if model.count_tokens(prompt, _as_blocks(trial)) <= _window(model):
            chosen.add(message[0])
        else:
            rejected = {item[0] for item in newest_first[index:]}
            omitted = [item for item in ordered if item[0] in rejected]
            break
    selected = [message for message in ordered if message[0] in chosen]
    return _as_blocks(selected), omitted


def _fold_prompt(summary: str | None) -> str:
    if summary is None:
        return _FOLD
    return f"{_FOLD}\n{summary}"


def _chat_messages(messages: list[MessageRow]) -> list[MessageRow]:
    chat = [message for message in messages if message[5] == "chat"]
    return sorted(chat, key=lambda message: (message[7], str(message[0])))


def _newest_that_fit(
    prompt: str,
    messages: list[MessageRow],
    model: Model,
) -> list[MessageRow]:
    ordered = list(messages)
    if model.count_tokens(prompt, _as_blocks(ordered)) <= _window(model):
        return ordered
    chosen: set[uuid.UUID] = set()
    for message in reversed(ordered):
        trial_ids = chosen | {message[0]}
        trial = [item for item in ordered if item[0] in trial_ids]
        if model.count_tokens(prompt, _as_blocks(trial)) <= _window(model):
            chosen.add(message[0])
    return [message for message in ordered if message[0] in chosen]


def _without(
    messages: list[MessageRow],
    drop_ids: set[uuid.UUID],
) -> tuple[list[Block], list[MessageRow]]:
    ordered = _chat_messages(messages)
    omitted = [message for message in ordered if message[0] in drop_ids]
    kept = [message for message in ordered if message[0] not in drop_ids]
    return _as_blocks(kept), omitted


def _cover(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    email: str,
    display_name: str | None,
    summary: str | None,
    messages: list[MessageRow],
    model: Model,
) -> tuple[str, list[Block], list[MessageRow]]:
    covered: set[uuid.UUID] = set()
    while True:
        prompt = _system_prompt(display_name, summary)
        blocks, omitted = select_blocks(prompt, messages, model)
        if all(message[0] in covered for message in omitted):
            return prompt, blocks, omitted
        fold_prompt = _fold_prompt(summary)
        included = _newest_that_fit(fold_prompt, omitted, model)
        fresh = [message for message in included if message[0] not in covered]
        if not fresh:
            # Leave out only messages this summary already covers.
            drop = {message[0] for message in omitted if message[0] in covered}
            kept, left_out = _without(messages, drop)
            return prompt, kept, left_out
        try:
            folded = ask(model, fold_prompt, _as_blocks(included))
        except Exception:
            # The summary never reaches the person. A later reply failure keeps its line.
            ordered = _chat_messages(messages)
            return prompt, _as_blocks(ordered), []
        set_summary(connection, account_id, thread_id, folded.text)
        record_usage(
            connection,
            account_id,
            email,
            "chat",
            *_who(model),
            folded.input_tokens,
            folded.output_tokens,
            folded.total_tokens,
            None,
            None,
        )
        covered.update(message[0] for message in included)
        summary = folded.text


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
    model: Model


@dataclass
class _Retry:
    prepared: _Prepared
    failure_id: uuid.UUID
    partial_id: uuid.UUID | None


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


def _open_turn(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> tuple[ThreadRow, str, str | None] | str | None:
    thread = get_thread(connection, account_id, thread_id)
    if thread is None:
        return None
    email, display_name = _account(connection, account_id)
    token_cap, stopped = _limit(connection, email)
    if stopped:
        return _STOPPED
    if tokens_for_email(connection, email) >= token_cap:
        return _CAPPED
    return thread, email, display_name


def _prepare(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    model: Model,
) -> _Prepared | str | None:
    opened = _open_turn(connection, account_id, thread_id)
    if not isinstance(opened, tuple):
        return opened
    thread, email, display_name = opened
    person = insert_message(connection, account_id, thread_id, "person", "chat", text)
    if person is None:
        return None
    set_last_active_at(connection, session_id, account_id)
    messages = list_messages(connection, account_id, thread_id)
    if messages is None:
        raise RuntimeError("thread is missing")
    prompt, blocks, omitted = _cover(
        connection,
        account_id,
        thread_id,
        email,
        display_name,
        thread[3],
        messages,
        model,
    )
    return _Prepared(
        session_id=session_id,
        account_id=account_id,
        thread_id=thread_id,
        email=email,
        prompt=prompt,
        blocks=blocks,
        omitted=omitted,
        model=model,
    )


def _fail(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    email: str,
    omitted: list[MessageRow],
    model: Model,
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
        *_who(model),
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
        reply = ask(model, prepared.prompt, prepared.blocks)
    except Exception:
        return _fail(
            connection,
            prepared.account_id,
            prepared.thread_id,
            prepared.email,
            prepared.omitted,
            prepared.model,
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
        *_who(prepared.model),
        reply.input_tokens,
        reply.output_tokens,
        reply.total_tokens,
        assistant[0],
        None,
    )
    return TurnResult(body=reply.text, kind="chat", omitted=tuple(prepared.omitted))


def _counts(reply: Reply | None) -> tuple[int | None, int | None, int | None]:
    if reply is None:
        return None, None, None
    return reply.input_tokens, reply.output_tokens, reply.total_tokens


def _save(prepared: _Prepared, text: str, reply: Reply | None, failed: bool, stopped: bool) -> None:
    input_tokens, output_tokens, total_tokens = _counts(reply)
    with connect() as connection:
        if failed and text == "":
            _fail(
                connection,
                prepared.account_id,
                prepared.thread_id,
                prepared.email,
                [],
                prepared.model,
            )
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
            *_who(prepared.model),
            input_tokens,
            output_tokens,
            total_tokens,
            message_id,
            None,
        )


def _failed_turn(
    messages: list[MessageRow],
) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    ordered = sorted(messages, key=lambda message: (message[7], str(message[0])))
    failure_id: uuid.UUID | None = None
    for message in ordered:
        if message[5] == "failure":
            failure_id = message[0]
    if failure_id is None:
        return None
    partial_id: uuid.UUID | None = None
    saw_person = False
    for message in ordered:
        if message[0] == failure_id:
            break
        if message[4] == "person" and message[5] == "chat":
            saw_person = True
            partial_id = None
        elif saw_person and message[4] == "assistant" and message[5] == "chat":
            partial_id = message[0]
    if not saw_person:
        return None
    return failure_id, partial_id


def _prepare_retry(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    model: Model,
) -> _Retry | str | None:
    opened = _open_turn(connection, account_id, thread_id)
    if not isinstance(opened, tuple):
        return opened
    thread, email, display_name = opened
    messages = list_messages(connection, account_id, thread_id)
    if messages is None:
        raise RuntimeError("thread is missing")
    failed = _failed_turn(messages)
    if failed is None:
        return NO_FAILURE
    failure_id, partial_id = failed
    context = [
        message
        for message in messages
        if partial_id is None or message[0] != partial_id
    ]
    prompt, blocks, omitted = _cover(
        connection,
        account_id,
        thread_id,
        email,
        display_name,
        thread[3],
        context,
        model,
    )
    prepared = _Prepared(
        session_id=session_id,
        account_id=account_id,
        thread_id=thread_id,
        email=email,
        prompt=prompt,
        blocks=blocks,
        omitted=omitted,
        model=model,
    )
    return _Retry(prepared=prepared, failure_id=failure_id, partial_id=partial_id)


def _place_failure_after(
    connection: psycopg.Connection,
    account_id: uuid.UUID,
    failure_id: uuid.UUID,
    partial_at: object,
) -> None:
    # now() is one timestamp for the transaction, so the line would tie the partial.
    connection.execute(
        """
        UPDATE message
        SET created_at = %s + interval '1 microsecond'
        WHERE id = %s
          AND account_id = %s
          AND kind = 'failure'
        """,
        (partial_at, failure_id, account_id),
    )


def _save_retry(
    retry: _Retry,
    text: str,
    reply: Reply | None,
    failed: bool,
    stopped: bool,
) -> None:
    input_tokens, output_tokens, total_tokens = _counts(reply)
    prepared = retry.prepared
    with connect() as connection:
        message_id: uuid.UUID | None = None
        if text == "" and (failed or stopped):
            record_usage(
                connection,
                prepared.account_id,
                prepared.email,
                "chat",
                *_who(prepared.model),
                input_tokens,
                output_tokens,
                total_tokens,
                None,
                None,
            )
            return
        if retry.partial_id is None:
            stored = insert_message(
                connection,
                prepared.account_id,
                prepared.thread_id,
                "assistant",
                "chat",
                text,
            )
        else:
            stored = update_message_body(
                connection,
                prepared.account_id,
                prepared.thread_id,
                retry.partial_id,
                text,
            )
        if stored is None:
            raise RuntimeError("assistant message was not stored")
        message_id = stored[0]
        if failed:
            if retry.partial_id is None:
                _place_failure_after(
                    connection,
                    prepared.account_id,
                    retry.failure_id,
                    stored[7],
                )
        else:
            delete_failure(
                connection,
                prepared.account_id,
                prepared.thread_id,
                retry.failure_id,
            )
            set_last_active_at(connection, prepared.session_id, prepared.account_id)
        record_usage(
            connection,
            prepared.account_id,
            prepared.email,
            "chat",
            *_who(prepared.model),
            input_tokens,
            output_tokens,
            total_tokens,
            message_id,
            None,
        )


def _write_reply(
    live: LiveReply,
    model: Model,
    prepared: _Prepared,
    persist: Callable[[str, Reply | None, bool, bool], None],
) -> None:
    parts: list[str] = []
    failed = False
    reply: Reply | None = None
    try:
        for event in stream_turn(model, prepared.prompt, prepared.blocks, live.stop):
            if not isinstance(event, dict):
                continue
            text = event.get("text")
            if isinstance(text, str) and text != "":
                parts.append(text)
                live.publish({"text": text})
            usage = event.get("usage")
            if isinstance(usage, Reply):
                reply = usage
    except Exception:
        failed = True
    stopped = live.stop.is_set()
    try:
        persist("".join(parts), reply, failed, stopped)
    except Exception:
        failed = True
    if failed:
        live.publish({"failed": True})
    else:
        live.publish({"done": True})
    live.finish()
    _unregister(prepared.account_id, prepared.thread_id, live)


def _start(
    connection: psycopg.Connection,
    prepared: _Prepared,
    model: Model,
    persist: Callable[[str, Reply | None, bool, bool], None],
) -> LiveReply:
    # The reply thread can emit a chunk before this request transaction closes.
    connection.commit()
    live = LiveReply()
    _register(prepared.account_id, prepared.thread_id, live)
    try:
        threading.Thread(
            target=_write_reply,
            args=(live, model, prepared, persist),
            name="folio-reply",
        ).start()
    except Exception:
        _unregister(prepared.account_id, prepared.thread_id, live)
        raise
    return live


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

    def persist(text: str, reply: Reply | None, failed: bool, stopped: bool) -> None:
        _save(prepared, text, reply, failed, stopped)

    return _start(connection, prepared, model, persist)


def start_retry(
    connection: psycopg.Connection,
    session_id: uuid.UUID,
    account_id: uuid.UUID,
    thread_id: uuid.UUID,
    model: Model,
) -> LiveReply | str | None:
    prepared = _prepare_retry(connection, session_id, account_id, thread_id, model)
    if not isinstance(prepared, _Retry):
        return prepared

    def persist(text: str, reply: Reply | None, failed: bool, stopped: bool) -> None:
        _save_retry(prepared, text, reply, failed, stopped)

    return _start(connection, prepared.prepared, model, persist)
