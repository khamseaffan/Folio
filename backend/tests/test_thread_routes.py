import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.loop import ROUNDS_REACHED
from app.main import app
from app.message import insert_message
from app.model import CONTEXT_TOKENS, Block, Reply, ToolCall
from app.model import FakeModel as ScriptedModel
from app.session import apply as apply_session
from app.session import end_session
from app.sign_in_proof import VerifiedSignIn
from fastapi.testclient import TestClient

ALLOWED = "khamseaffan@gmail.com"
STOPPED = "A system admin has stopped your account. Contact khamseaffan@gmail.com."
CAPPED = "You have reached the allowed usage limit."
FAILED = "That didn't go through."
CAUSE = "distinctive-cause-zebra"
SUMMARY = "hidden-summary-quill"


class _Script:
    def __init__(
        self,
        chunks: tuple[str, ...],
        holds: tuple[threading.Event | None, ...],
        error: BaseException | None,
        error_at: int | None,
        final: Reply,
        arrived: threading.Event,
    ) -> None:
        self._chunks = chunks
        self._holds = holds
        self._error = error
        self._error_at = error_at
        self._final = final
        self._arrived = arrived
        self._parts: list[str] = []
        self._done = False

    def __iter__(self):
        if self._error is not None and not self._chunks:
            raise self._error
        for index, chunk in enumerate(self._chunks):
            hold = self._holds[index] if index < len(self._holds) else None
            if hold is not None:
                self._arrived.set()
                hold.wait()
            if self._error is not None and self._error_at == index:
                raise self._error
            self._parts.append(chunk)
            yield chunk
        self._done = True

    def reply(self) -> Reply:
        if not self._done:
            return Reply(
                text="".join(self._parts),
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            )
        return self._final


class FakeModel:
    def __init__(
        self,
        reply: Reply | None = None,
        error: BaseException | None = None,
        chunks: tuple[str, ...] | None = None,
        holds: tuple[threading.Event | None, ...] = (),
        error_at: int | None = None,
        arrived: threading.Event | None = None,
        summary_reply: Reply | None = None,
        complete_error: BaseException | None = None,
        token_count: int = 1,
        overflow_text: str | None = None,
    ) -> None:
        self.completed = 0
        self.calls = 0
        self.order: list[str] = []
        self._reply = reply if reply is not None else Reply("fake-reply", 424242, 434343, 858585)
        self._error = error
        self._chunks = chunks
        self._holds = holds
        self._error_at = error_at
        self._arrived = arrived if arrived is not None else threading.Event()
        self._summary_reply = summary_reply
        self._complete_error = complete_error
        self._token_count = token_count
        self._overflow_text = overflow_text
        self.seen: list[tuple[str, list[tuple[str, str]]]] = []
        self.folded: list[tuple[str, list[tuple[str, str]]]] = []

    def complete(self, prompt: str, blocks: list[Block], tools=()) -> Reply:
        self.completed += 1
        self.order.append("complete")
        self.folded.append((prompt, [(block.role, block.text) for block in blocks]))
        if self._complete_error is not None:
            raise self._complete_error
        if self._summary_reply is not None:
            return self._summary_reply
        if self._error is not None:
            raise self._error
        return self._reply

    def stream(self, prompt: str, blocks: list[Block], tools=()) -> _Script:
        self.calls += 1
        self.order.append("stream")
        self.seen.append((prompt, [(block.role, block.text) for block in blocks]))
        if self._chunks is None:
            chunks = () if self._error is not None else (self._reply.text,)
        else:
            chunks = self._chunks
        return _Script(
            chunks,
            self._holds,
            self._error,
            self._error_at,
            self._reply,
            self._arrived,
        )

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        if (
            self._overflow_text is not None
            and any(block.text == self._overflow_text for block in blocks)
            and len(blocks) > 1
        ):
            return CONTEXT_TOKENS + 1
        return self._token_count


def _email(label: str) -> str:
    return f"{label}.{uuid.uuid4()}@Example.com"


def _remove(email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        usage = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        session = connection.execute("SELECT to_regclass('public.session')").fetchone()
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
        if usage is not None and usage[0] is not None:
            connection.execute("DELETE FROM usage WHERE email = %s", (stored,))
        if account is not None and account[0] is not None:
            if session is not None and session[0] is not None:
                connection.execute(
                    """
                    DELETE FROM session
                    WHERE account_id IN (
                        SELECT id FROM account WHERE email = %s
                    )
                    """,
                    (stored,),
                )
            connection.execute("DELETE FROM account WHERE email = %s", (stored,))
        if limit is not None and limit[0] is not None:
            connection.execute("DELETE FROM email_limit WHERE email = %s", (stored,))


def _allow(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        assert id_token == "token"
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)


def _sign_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    _allow(monkeypatch, email)
    client = TestClient(app)
    signed_in = client.post("/sign-in", json={"id_token": "token"})
    assert signed_in.status_code == 200
    return client


def _use_model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr("app.main.open_model", lambda: model)


def _account_exists(email: str) -> bool:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if present is None or present[0] is None:
            return False
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s",
            (stored,),
        ).fetchone()
    return row is not None


def _usage_ids(email: str) -> set[uuid.UUID]:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return set()
        rows = connection.execute(
            "SELECT id FROM usage WHERE email = %s",
            (stored,),
        ).fetchall()
    return {row[0] for row in rows}


def _thread_ids(email: str) -> set[uuid.UUID]:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.thread')").fetchone()
        if present is None or present[0] is None:
            return set()
        rows = connection.execute(
            """
            SELECT thread.id
            FROM thread
            JOIN account ON account.id = thread.account_id
            WHERE account.email = %s
            """,
            (stored,),
        ).fetchall()
    return {row[0] for row in rows}


def _message_count(thread_id: str) -> int:
    with connect() as connection:
        row = connection.execute(
            "SELECT count(*) FROM message WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _events(text: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for block in text.split("\n\n"):
        line = block.strip()
        if line == "":
            continue
        assert line.startswith("data: ")
        payload = json.loads(line.removeprefix("data: "))
        assert isinstance(payload, dict)
        found.append(payload)
    return found


def _next_event(lines) -> dict[str, object]:
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        assert isinstance(payload, dict)
        return payload
    raise AssertionError("stream ended")


def _wait_until(ready) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _stored_messages(thread_id: str) -> list[tuple[str, str, str]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT role, kind, body
            FROM message
            WHERE thread_id = %s
            ORDER BY created_at ASC
            """,
            (thread_id,),
        ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def _usage_rows(email: str) -> list[tuple]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT operation, provider, model, input_tokens, output_tokens,
                   total_tokens, message_id
            FROM usage
            WHERE email = %s
            """,
            (email.lower(),),
        ).fetchall()
    return list(rows)


def _usage_count(email: str) -> int:
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return 0
        row = connection.execute(
            "SELECT count(*) FROM usage WHERE email = %s",
            (email.lower(),),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _allowed_client() -> tuple[TestClient, uuid.UUID]:
    account_id = ensure_account(str(uuid.uuid4()), ALLOWED)
    with connect() as connection:
        apply_session(connection)
        created = connection.execute(
            """
            INSERT INTO session (account_id)
            VALUES (%s)
            RETURNING id
            """,
            (account_id,),
        ).fetchone()
    if created is None:
        raise RuntimeError("session was not stored")
    client = TestClient(app)
    client.cookies.set("session", str(created[0]))
    return client, created[0]


def _cleanup_allowed(
    existed: bool,
    usage_before: set[uuid.UUID],
    threads_before: set[uuid.UUID],
    session_id: uuid.UUID | None,
) -> None:
    if not existed:
        _remove(ALLOWED)
        return
    with connect() as connection:
        thread = connection.execute("SELECT to_regclass('public.thread')").fetchone()
        if thread is not None and thread[0] is not None:
            current = connection.execute(
                """
                SELECT thread.id
                FROM thread
                JOIN account ON account.id = thread.account_id
                WHERE account.email = %s
                """,
                (ALLOWED,),
            ).fetchall()
            extra_threads = [row[0] for row in current if row[0] not in threads_before]
            if extra_threads:
                connection.execute("DELETE FROM thread WHERE id = ANY(%s)", (extra_threads,))
        if session_id is not None:
            session = connection.execute("SELECT to_regclass('public.session')").fetchone()
            if session is not None and session[0] is not None:
                connection.execute("DELETE FROM session WHERE id = %s", (session_id,))
        usage = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if usage is None or usage[0] is None:
            return
        rows = connection.execute(
            "SELECT id FROM usage WHERE email = %s",
            (ALLOWED,),
        ).fetchall()
        extra_usage = [row[0] for row in rows if row[0] not in usage_before]
        if extra_usage:
            connection.execute("DELETE FROM usage WHERE id = ANY(%s)", (extra_usage,))


def _assert_item(body: object) -> None:
    assert isinstance(body, dict)
    assert set(body) == {"id", "name", "created_at"}


def _assert_thread(body: object) -> dict[str, object]:
    assert isinstance(body, dict)
    assert set(body) == {"id", "name", "created_at", "messages"}
    messages = body["messages"]
    assert isinstance(messages, list)
    for message in messages:
        assert isinstance(message, dict)
        assert set(message) == {"id", "role", "kind", "body", "created_at"}
    return body


def _assert_not_found(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    thread_id: str,
) -> None:
    model = FakeModel()
    _use_model(monkeypatch, model)
    loaded = client.get(f"/threads/{thread_id}")
    posted = client.post(
        f"/threads/{thread_id}/messages",
        json={"text": "cross-account-text"},
    )
    stopped = client.post(f"/threads/{thread_id}/stop")
    retried = client.post(f"/threads/{thread_id}/retry")
    assert loaded.status_code == 404
    assert loaded.json() == {"error": "Not found"}
    assert posted.status_code == 404
    assert posted.json() == {"error": "Not found"}
    assert stopped.status_code == 404
    assert stopped.json() == {"error": "Not found"}
    assert retried.status_code == 404
    assert retried.json() == {"error": "Not found"}
    assert model.completed == 0
    assert model.calls == 0


def test_no_session_uses_the_sign_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid() -> None:
        raise AssertionError("model should not be opened")

    monkeypatch.setattr("app.main.open_model", forbid)
    client = TestClient(app)
    thread_id = str(uuid.uuid4())
    missing = [
        client.get("/threads"),
        client.post("/threads"),
        client.get(f"/threads/{thread_id}"),
        client.post(f"/threads/{thread_id}/messages", json={"text": "hello"}),
        client.post(f"/threads/{thread_id}/stop"),
        client.post(f"/threads/{thread_id}/retry"),
    ]
    client.cookies.set("session", "not-a-session")
    invalid = client.get("/threads")
    client.cookies.set("session", str(uuid.uuid4()))
    unknown = client.get("/threads")
    for response in [*missing, invalid, unknown]:
        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}


def test_create_and_list_only_the_callers_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    caller_email = _email("Caller")
    other_email = _email("Other")
    _remove(caller_email)
    _remove(other_email)
    try:
        caller = _sign_in(monkeypatch, caller_email)
        other = _sign_in(monkeypatch, other_email)
        first = caller.post("/threads")
        second = caller.post("/threads")
        theirs = other.post("/threads")
        assert first.status_code == 200
        assert second.status_code == 200
        assert theirs.status_code == 200
        first_id = first.json()["id"]
        second_id = second.json()["id"]
        their_id = theirs.json()["id"]
        with connect() as connection:
            connection.execute(
                """
                UPDATE thread
                SET created_at = timestamptz '2020-01-02',
                    summary = %s
                WHERE id = %s
                """,
                (SUMMARY, second_id),
            )
            connection.execute(
                """
                UPDATE thread
                SET created_at = timestamptz '2020-01-01'
                WHERE id = %s
                """,
                (first_id,),
            )
        listed = caller.get("/threads")
        assert listed.status_code == 200
        rows = listed.json()
        assert [item["id"] for item in rows] == [first_id, second_id]
        for item in rows:
            _assert_item(item)
            assert item["name"] == "New thread"
        assert their_id not in {item["id"] for item in rows}
        assert SUMMARY not in listed.text
        assert "summary" not in listed.text
        other_rows = other.get("/threads").json()
        assert [item["id"] for item in other_rows] == [their_id]
    finally:
        _remove(caller_email)
        _remove(other_email)


def test_a_new_thread_is_named_new_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Named")
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        created = client.post("/threads")
        assert created.status_code == 200
        body = created.json()
        _assert_item(body)
        assert body["name"] == "New thread"
        uuid.UUID(body["id"])
        assert isinstance(body["created_at"], str)
        assert "summary" not in created.text
    finally:
        _remove(email)


def test_post_message_streams_chunks_in_order_and_stores_one_usage_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Chat")
    stored = email.lower()
    _remove(email)
    model = FakeModel(
        reply=Reply("fakereply", 424242, 434343, 858585),
        chunks=("fake", "reply"),
    )
    try:
        client = _sign_in(monkeypatch, email)
        created = client.post("/threads")
        assert created.status_code == 200
        thread_id = created.json()["id"]
        with connect() as connection:
            connection.execute(
                "UPDATE thread SET summary = %s WHERE id = %s",
                (SUMMARY, thread_id),
            )
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        loaded = client.get(f"/threads/{thread_id}")
        assert posted.status_code == 200
        assert "text/event-stream" in posted.headers["content-type"]
        assert loaded.status_code == 200
        events = _events(posted.text)
        assert events == [
            {"text": "fake"},
            {"text": "reply"},
            {"done": True},
        ]
        for event in events:
            if "text" in event:
                assert set(event) == {"text"}
        assert SUMMARY not in posted.text
        assert "summary" not in posted.text
        assert "424242" not in posted.text
        assert "434343" not in posted.text
        assert "858585" not in posted.text
        assert "gpt-6.1-sol" not in posted.text
        assert "expected_cost" not in posted.text
        body = _assert_thread(loaded.json())
        messages = body["messages"]
        assert isinstance(messages, list)
        created_at = [item["created_at"] for item in messages]
        assert created_at == sorted(created_at)
        by_role = {item["role"]: item for item in messages}
        person = by_role["person"]
        assistant = by_role["assistant"]
        assert isinstance(person, dict)
        assert isinstance(assistant, dict)
        assert person["body"] == "hello"
        assert str(person["created_at"]) <= str(assistant["created_at"])
        assert person["kind"] == "chat"
        assert assistant["body"] == "fakereply"
        assert assistant["kind"] == "chat"
        assert SUMMARY not in loaded.text
        usage = _usage_rows(stored)
        assert usage == [
            (
                "chat",
                "openai",
                "gpt-6.1-sol",
                424242,
                434343,
                858585,
                uuid.UUID(str(assistant["id"])),
            )
        ]
        assert model.calls == 1
        assert model.completed == 0
    finally:
        _remove(email)


def test_another_accounts_thread_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_email = _email("Owner")
    other_email = _email("Other")
    _remove(owner_email)
    _remove(other_email)
    allowed_existed = _account_exists(ALLOWED)
    allowed_usage = _usage_ids(ALLOWED)
    allowed_threads = _thread_ids(ALLOWED)
    allowed_session: uuid.UUID | None = None
    try:
        owner = _sign_in(monkeypatch, owner_email)
        created = owner.post("/threads")
        assert created.status_code == 200
        thread_id = created.json()["id"]
        other = _sign_in(monkeypatch, other_email)
        _assert_not_found(monkeypatch, other, thread_id)
        admin, allowed_session = _allowed_client()
        _assert_not_found(monkeypatch, admin, thread_id)
        missing = owner.get(f"/threads/{uuid.uuid4()}")
        assert missing.status_code == 404
        assert missing.json() == {"error": "Not found"}
        assert owner_email.lower() not in missing.text
        assert _message_count(thread_id) == 0
    finally:
        _remove(owner_email)
        _remove(other_email)
        _cleanup_allowed(allowed_existed, allowed_usage, allowed_threads, allowed_session)


def test_stopped_email_returns_the_sentence_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Stopped")
    stored = email.lower()
    _remove(email)
    model = FakeModel()
    try:
        client = _sign_in(monkeypatch, email)
        created = client.post("/threads")
        assert created.status_code == 200
        thread_id = created.json()["id"]
        with connect() as connection:
            connection.execute(
                "UPDATE email_limit SET stopped = true WHERE email = %s",
                (stored,),
            )
        _use_model(monkeypatch, model)
        text = "stopped-person-text"
        response = client.post(f"/threads/{thread_id}/messages", json={"text": text})
        assert response.status_code == 403
        assert response.json() == {"error": STOPPED}
        assert text not in response.text
        assert _message_count(thread_id) == 0
        assert _usage_count(stored) == 0
        assert model.completed == 0
        assert model.calls == 0
    finally:
        _remove(email)


def test_token_cap_returns_the_sentence_without_digits_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Capped")
    stored = email.lower()
    _remove(email)
    model = FakeModel(reply=Reply("should-not-run", 9, 9, 18))
    try:
        client = _sign_in(monkeypatch, email)
        created = client.post("/threads")
        assert created.status_code == 200
        thread_id = created.json()["id"]
        with connect() as connection:
            connection.execute(
                "UPDATE email_limit SET token_cap = %s WHERE email = %s",
                (0, stored),
            )
        _use_model(monkeypatch, model)
        text = "capped-person-text"
        response = client.post(f"/threads/{thread_id}/messages", json={"text": text})
        assert response.status_code == 403
        assert response.json() == {"error": CAPPED}
        assert not any(character.isdigit() for character in response.text)
        assert text not in response.text
        assert _message_count(thread_id) == 0
        assert _usage_count(stored) == 0
        assert model.completed == 0
        assert model.calls == 0
    finally:
        _remove(email)


def test_model_error_keeps_the_person_message_and_hides_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Failed")
    _remove(email)
    model = FakeModel(error=RuntimeError(CAUSE))
    try:
        client = _sign_in(monkeypatch, email)
        created = client.post("/threads")
        assert created.status_code == 200
        thread_id = created.json()["id"]
        _use_model(monkeypatch, model)
        response = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        loaded = client.get(f"/threads/{thread_id}")
        assert response.status_code == 200
        assert _events(response.text) == [{"failed": True}]
        assert CAUSE not in response.text
        assert "summary" not in response.text
        assert "gpt-6.1-sol" not in response.text
        assert "expected_cost" not in response.text
        body = _assert_thread(loaded.json())
        messages = body["messages"]
        assert isinstance(messages, list)
        by_role = {item["role"]: item for item in messages}
        person = by_role["person"]
        assistant = by_role["assistant"]
        assert isinstance(person, dict)
        assert isinstance(assistant, dict)
        assert person["body"] == "hello"
        assert person["kind"] == "chat"
        assert assistant["kind"] == "failure"
        assert assistant["body"] == FAILED
        assert CAUSE not in loaded.text
        assert _usage_rows(email) == [("chat", "openai", "gpt-6.1-sol", None, None, None, None)]
        assert model.calls == 1
        assert model.completed == 0
    finally:
        _remove(email)


def _open_thread(monkeypatch: pytest.MonkeyPatch, email: str) -> tuple[TestClient, str]:
    client = _sign_in(monkeypatch, email)
    created = client.post("/threads")
    assert created.status_code == 200
    return client, created.json()["id"]


def _last_active(session_id: uuid.UUID) -> datetime:
    with connect() as connection:
        row = connection.execute(
            "SELECT last_active_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert row is not None
    return row[0]


def _assistant_id(thread_id: str) -> uuid.UUID:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM message
            WHERE thread_id = %s AND role = 'assistant' AND kind = 'chat'
            """,
            (thread_id,),
        ).fetchone()
    assert row is not None
    return row[0]


def test_stop_after_the_first_chunk_stores_that_text_and_one_usage_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Stop")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("keptlater", 424242, 434343, 858585),
        chunks=("kept", "later"),
        holds=(None, release),
        arrived=arrived,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        session_raw = client.cookies.get("session")
        assert session_raw is not None
        session_id = uuid.UUID(session_raw)
        _use_model(monkeypatch, model)
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            assert response.status_code == 200
            lines = response.iter_lines()
            first = _next_event(lines)
            assert first == {"text": "kept"}
            assert set(first) == {"text"}
            assert arrived.wait(2)
            after_person = _last_active(session_id)
            assert _stored_messages(thread_id) == [("person", "chat", "hello")]
            stopped = client.post(f"/threads/{thread_id}/stop")
            assert stopped.status_code == 204
            release.set()
            done = _next_event(lines)
            assert done == {"done": True}
            assert "424242" not in json.dumps(first)
            assert "424242" not in json.dumps(done)
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "kept"),
        ]
        assert _last_active(session_id) > after_person
        rows = _usage_rows(email)
        assert len(rows) == 1
        assert rows[0][0:6] == ("chat", "openai", "gpt-6.1-sol", None, None, None)
        assert rows[0][6] == _assistant_id(thread_id)
        assert FAILED not in "".join(body for _, _, body in _stored_messages(thread_id))
    finally:
        release.set()
        _remove(email)


def test_stop_before_any_word_stores_usage_and_no_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Early")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("later", 11, 12, 23),
        chunks=("later",),
        holds=(release,),
        arrived=arrived,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            assert response.status_code == 200
            assert arrived.wait(2)
            assert _stored_messages(thread_id) == [("person", "chat", "hello")]
            stopped = client.post(f"/threads/{thread_id}/stop")
            assert stopped.status_code == 204
            release.set()
            assert _next_event(response.iter_lines()) == {"done": True}
        assert _stored_messages(thread_id) == [("person", "chat", "hello")]
        assert _usage_rows(email) == [("chat", "openai", "gpt-6.1-sol", None, None, None, None)]
    finally:
        release.set()
        _remove(email)


def test_closing_the_stream_does_not_stop_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Close")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("keptlater", 424242, 434343, 858585),
        chunks=("kept", "later"),
        holds=(None, release),
        arrived=arrived,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        stream_cm = client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        )
        response = stream_cm.__enter__()
        try:
            assert response.status_code == 200
            first = _next_event(response.iter_lines())
            assert first == {"text": "kept"}
            assert arrived.wait(2)
        finally:

            def _close() -> None:
                stream_cm.__exit__(None, None, None)

            closer = threading.Thread(target=_close)
            closer.start()
            closer.join(3)
            if closer.is_alive():
                release.set()
                closer.join(3)
                raise AssertionError("closing the stream waited on the reply")
        release.set()
        _wait_until(lambda: _usage_count(email) == 1)
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "keptlater"),
        ]
        rows = _usage_rows(email)
        assert len(rows) == 1
        assert rows[0][0:6] == ("chat", "openai", "gpt-6.1-sol", 424242, 434343, 858585)
        stored = "".join(body for _, _, body in _stored_messages(thread_id))
        assert FAILED not in stored
    finally:
        release.set()
        _remove(email)


def test_ending_the_session_does_not_stop_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Ended")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("keptlater", 7, 8, 15),
        chunks=("kept", "later"),
        holds=(None, release),
        arrived=arrived,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        session_raw = client.cookies.get("session")
        assert session_raw is not None
        _use_model(monkeypatch, model)
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            lines = response.iter_lines()
            assert _next_event(lines) == {"text": "kept"}
            assert arrived.wait(2)
            end_session(uuid.UUID(session_raw))
            signed_out = client.post("/sign-out")
            assert signed_out.status_code == 204
            release.set()
            assert _next_event(lines) == {"text": "later"}
            assert _next_event(lines) == {"done": True}
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "keptlater"),
        ]
        assert _usage_rows(email) == [
            ("chat", "openai", "gpt-6.1-sol", 7, 8, 15, _assistant_id(thread_id))
        ]
    finally:
        release.set()
        _remove(email)


def test_another_account_cannot_stop_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_email = _email("Owner")
    other_email = _email("Other")
    _remove(owner_email)
    _remove(other_email)
    allowed_existed = _account_exists(ALLOWED)
    allowed_usage = _usage_ids(ALLOWED)
    allowed_threads = _thread_ids(ALLOWED)
    allowed_session: uuid.UUID | None = None
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("keptlater", 3, 4, 7),
        chunks=("kept", "later"),
        holds=(None, release),
        arrived=arrived,
    )
    try:
        owner, thread_id = _open_thread(monkeypatch, owner_email)
        _use_model(monkeypatch, model)
        other = _sign_in(monkeypatch, other_email)
        with owner.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            lines = response.iter_lines()
            assert _next_event(lines) == {"text": "kept"}
            assert arrived.wait(2)
            refused = other.post(f"/threads/{thread_id}/stop")
            assert refused.status_code == 404
            assert refused.json() == {"error": "Not found"}
            admin, allowed_session = _allowed_client()
            admin_stop = admin.post(f"/threads/{thread_id}/stop")
            assert admin_stop.status_code == 404
            assert admin_stop.json() == {"error": "Not found"}
            release.set()
            later = _next_event(lines)
            done = _next_event(lines)
            assert later == {"text": "later"}
            assert done == {"done": True}
            assert "3" not in json.dumps([later, done])
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "keptlater"),
        ]
        assert len(_usage_rows(owner_email)) == 1
    finally:
        release.set()
        _remove(owner_email)
        _remove(other_email)
        _cleanup_allowed(allowed_existed, allowed_usage, allowed_threads, allowed_session)


def test_mid_stream_model_error_keeps_the_arrived_text_and_hides_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Partial")
    _remove(email)
    model = FakeModel(
        chunks=("kept", "later"),
        error=RuntimeError(CAUSE),
        error_at=1,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        response = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert response.status_code == 200
        assert _events(response.text) == [{"text": "kept"}, {"failed": True}]
        assert CAUSE not in response.text
        assert "gpt-6.1-sol" not in response.text
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "kept"),
            ("assistant", "failure", FAILED),
        ]
        assert _usage_rows(email) == [
            ("chat", "openai", "gpt-6.1-sol", None, None, None, _assistant_id(thread_id))
        ]
    finally:
        _remove(email)


HARM = "A task that would cause physical harm stops and asks."
NO_FAILURE = "There is no failed reply."


def _message_rows(thread_id: str) -> list[tuple[uuid.UUID, str, str, str]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, role, kind, body
            FROM message
            WHERE thread_id = %s
            ORDER BY created_at ASC, id ASC
            """,
            (thread_id,),
        ).fetchall()
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def _cause_stored(thread_id: str, email: str, cause: str) -> bool:
    with connect() as connection:
        messages = connection.execute(
            """
            SELECT body FROM message WHERE thread_id = %s
            UNION ALL
            SELECT name FROM thread WHERE id = %s
            UNION ALL
            SELECT coalesce(summary, '') FROM thread WHERE id = %s
            """,
            (thread_id, thread_id, thread_id),
        ).fetchall()
        usage = connection.execute(
            "SELECT * FROM usage WHERE email = %s",
            (email.lower(),),
        ).fetchall()
    if any(cause in str(row[0]) for row in messages):
        return True
    return any(cause in str(row) for row in usage)


def test_retry_replaces_the_partial_and_keeps_one_person_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Retry")
    _remove(email)
    failed = FakeModel(
        chunks=("kept", "later"),
        error=RuntimeError(CAUSE),
        error_at=1,
    )
    retry = FakeModel(
        reply=Reply("newreply", 424242, 434343, 858585),
        chunks=("new", "reply"),
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        named = client.post("/display-name", json={"display_name": "Ada"})
        assert named.status_code == 200
        with connect() as connection:
            connection.execute(
                "UPDATE thread SET summary = %s WHERE id = %s",
                (SUMMARY, thread_id),
            )
        session_raw = client.cookies.get("session")
        assert session_raw is not None
        session_id = uuid.UUID(session_raw)
        _use_model(monkeypatch, failed)
        first = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _message_rows(thread_id)
        assert [(role, kind, body) for _, role, kind, body in before] == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "kept"),
            ("assistant", "failure", FAILED),
        ]
        person_id = before[0][0]
        partial_id = before[1][0]
        active = _last_active(session_id)
        _use_model(monkeypatch, retry)
        posted = client.post(f"/threads/{thread_id}/retry")
        assert posted.status_code == 200
        events = _events(posted.text)
        assert events == [{"text": "new"}, {"text": "reply"}, {"done": True}]
        for event in events:
            if "text" in event:
                assert set(event) == {"text"}
        assert CAUSE not in posted.text
        assert SUMMARY not in posted.text
        assert "summary" not in posted.text
        assert "424242" not in posted.text
        assert "434343" not in posted.text
        assert "858585" not in posted.text
        assert "gpt-6.1-sol" not in posted.text
        after = _message_rows(thread_id)
        assert after == [
            (person_id, "person", "chat", "hello"),
            (partial_id, "assistant", "chat", "newreply"),
        ]
        assert _last_active(session_id) > active
        assert set(_usage_rows(email)) == {
            ("chat", "openai", "gpt-6.1-sol", None, None, None, partial_id),
            ("chat", "openai", "gpt-6.1-sol", 424242, 434343, 858585, partial_id),
        }
        assert len(_usage_rows(email)) == 2
        assert retry.calls == 1
        assert retry.completed == 0
        assert retry.seen == [
            (
                f"The person's name is Ada.\n{HARM}\n{SUMMARY}",
                [("person", "hello")],
            )
        ]
    finally:
        _remove(email)


def test_retry_sends_earlier_chat_and_leaves_out_the_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Context")
    _remove(email)
    first_model = FakeModel(reply=Reply("full", 1, 1, 2), chunks=("full",))
    failed = FakeModel(
        chunks=("kept", "later"),
        error=RuntimeError(CAUSE),
        error_at=1,
    )
    retry = FakeModel(reply=Reply("done", 5, 6, 11), chunks=("done",))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, first_model)
        earlier = client.post(f"/threads/{thread_id}/messages", json={"text": "earlier"})
        assert earlier.status_code == 200
        _use_model(monkeypatch, failed)
        again = client.post(f"/threads/{thread_id}/messages", json={"text": "again"})
        assert again.status_code == 200
        before = _message_rows(thread_id)
        assert [row[1] for row in before].count("person") == 2
        partial_id = next(row[0] for row in before if row[3] == "kept")
        _use_model(monkeypatch, retry)
        posted = client.post(f"/threads/{thread_id}/retry")
        assert posted.status_code == 200
        after = _message_rows(thread_id)
        assert [row[1] for row in after].count("person") == 2
        assert [row[0] for row in after if row[1] == "person"] == [
            row[0] for row in before if row[1] == "person"
        ]
        assert (partial_id, "assistant", "chat", "done") in after
        assert all(row[2] != "failure" for row in after)
        assert retry.seen == [
            (
                HARM,
                [
                    ("person", "earlier"),
                    ("assistant", "full"),
                    ("person", "again"),
                ],
            )
        ]
        assert "kept" not in [text for _, blocks in retry.seen for _, text in blocks]
    finally:
        _remove(email)


def test_retry_failure_keeps_one_of_each_row_and_hides_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Again")
    _remove(email)
    failed = FakeModel(
        chunks=("kept", "later"),
        error=RuntimeError(CAUSE),
        error_at=1,
    )
    retry = FakeModel(
        chunks=("fresh", "hidden"),
        error=RuntimeError(CAUSE),
        error_at=1,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        session_raw = client.cookies.get("session")
        assert session_raw is not None
        session_id = uuid.UUID(session_raw)
        _use_model(monkeypatch, failed)
        first = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _message_rows(thread_id)
        person_id, partial_id, failure_id = (row[0] for row in before)
        active = _last_active(session_id)
        _use_model(monkeypatch, retry)
        posted = client.post(f"/threads/{thread_id}/retry")
        assert posted.status_code == 200
        assert _events(posted.text) == [{"text": "fresh"}, {"failed": True}]
        assert CAUSE not in posted.text
        assert "424242" not in posted.text
        assert "gpt-6.1-sol" not in posted.text
        assert "summary" not in posted.text
        after = _message_rows(thread_id)
        assert after == [
            (person_id, "person", "chat", "hello"),
            (partial_id, "assistant", "chat", "fresh"),
            (failure_id, "assistant", "failure", FAILED),
        ]
        assert _last_active(session_id) == active
        rows = _usage_rows(email)
        assert len(rows) == 2
        assert all(
            row == ("chat", "openai", "gpt-6.1-sol", None, None, None, partial_id)
            for row in rows
        )
        assert not _cause_stored(thread_id, email, CAUSE)
        assert retry.calls == 1
        assert retry.seen == [(HARM, [("person", "hello")])]
    finally:
        _remove(email)


def test_retry_inserts_a_reply_when_the_failure_has_no_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Insert")
    _remove(email)
    failed = FakeModel(error=RuntimeError(CAUSE))
    retry = FakeModel(reply=Reply("arrived", 8, 9, 17), chunks=("arrived",))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        session_raw = client.cookies.get("session")
        assert session_raw is not None
        session_id = uuid.UUID(session_raw)
        _use_model(monkeypatch, failed)
        first = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _message_rows(thread_id)
        assert [(role, kind, body) for _, role, kind, body in before] == [
            ("person", "chat", "hello"),
            ("assistant", "failure", FAILED),
        ]
        person_id = before[0][0]
        active = _last_active(session_id)
        _use_model(monkeypatch, retry)
        posted = client.post(f"/threads/{thread_id}/retry")
        assert posted.status_code == 200
        assert _events(posted.text) == [{"text": "arrived"}, {"done": True}]
        assert CAUSE not in posted.text
        assert "8" not in posted.text
        after = _message_rows(thread_id)
        assert len(after) == 2
        assert after[0] == (person_id, "person", "chat", "hello")
        assert after[1][1:] == ("assistant", "chat", "arrived")
        reply_id = after[1][0]
        assert _last_active(session_id) > active
        assert set(_usage_rows(email)) == {
            ("chat", "openai", "gpt-6.1-sol", None, None, None, None),
            ("chat", "openai", "gpt-6.1-sol", 8, 9, 17, reply_id),
        }
        assert len(_usage_rows(email)) == 2
    finally:
        _remove(email)


def test_retry_stopped_email_adds_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("RetryStopped")
    stored = email.lower()
    _remove(email)
    failed = FakeModel(error=RuntimeError(CAUSE))
    retry = FakeModel(reply=Reply("should-not-run", 9, 9, 18))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, failed)
        first = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _stored_messages(thread_id)
        usage = _usage_count(stored)
        with connect() as connection:
            connection.execute(
                "UPDATE email_limit SET stopped = true WHERE email = %s",
                (stored,),
            )
        _use_model(monkeypatch, retry)
        response = client.post(f"/threads/{thread_id}/retry")
        assert response.status_code == 403
        assert response.json() == {"error": STOPPED}
        assert _stored_messages(thread_id) == before
        assert _usage_count(stored) == usage
        assert retry.calls == 0
        assert retry.completed == 0
    finally:
        _remove(email)


def test_retry_at_the_cap_adds_no_row_and_no_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("RetryCapped")
    stored = email.lower()
    _remove(email)
    failed = FakeModel(error=RuntimeError(CAUSE))
    retry = FakeModel(reply=Reply("should-not-run", 9, 9, 18))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, failed)
        first = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _stored_messages(thread_id)
        usage = _usage_count(stored)
        with connect() as connection:
            connection.execute(
                "UPDATE email_limit SET token_cap = %s WHERE email = %s",
                (0, stored),
            )
        _use_model(monkeypatch, retry)
        response = client.post(f"/threads/{thread_id}/retry")
        assert response.status_code == 403
        assert response.json() == {"error": CAPPED}
        assert not any(character.isdigit() for character in response.text)
        assert _stored_messages(thread_id) == before
        assert _usage_count(stored) == usage
        assert retry.calls == 0
        assert retry.completed == 0
    finally:
        _remove(email)


def test_another_account_cannot_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_email = _email("Owner")
    other_email = _email("Other")
    _remove(owner_email)
    _remove(other_email)
    allowed_existed = _account_exists(ALLOWED)
    allowed_usage = _usage_ids(ALLOWED)
    allowed_threads = _thread_ids(ALLOWED)
    allowed_session: uuid.UUID | None = None
    failed = FakeModel(error=RuntimeError(CAUSE))
    retry = FakeModel(reply=Reply("should-not-run", 3, 4, 7))
    try:
        owner, thread_id = _open_thread(monkeypatch, owner_email)
        _use_model(monkeypatch, failed)
        first = owner.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert first.status_code == 200
        before = _message_count(thread_id)
        usage = _usage_count(owner_email)
        other = _sign_in(monkeypatch, other_email)
        _use_model(monkeypatch, retry)
        refused = other.post(f"/threads/{thread_id}/retry")
        assert refused.status_code == 404
        assert refused.json() == {"error": "Not found"}
        admin, allowed_session = _allowed_client()
        admin_retry = admin.post(f"/threads/{thread_id}/retry")
        assert admin_retry.status_code == 404
        assert admin_retry.json() == {"error": "Not found"}
        assert _message_count(thread_id) == before
        assert _usage_count(owner_email) == usage
        assert retry.calls == 0
        assert retry.completed == 0
    finally:
        _remove(owner_email)
        _remove(other_email)
        _cleanup_allowed(allowed_existed, allowed_usage, allowed_threads, allowed_session)


def test_retry_without_a_failure_line_does_not_call_or_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Plain")
    _remove(email)
    model = FakeModel(reply=Reply("fakereply", 1, 1, 2), chunks=("fakereply",))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        empty = client.post(f"/threads/{thread_id}/retry")
        assert empty.status_code == 400
        assert empty.json() == {"error": NO_FAILURE}
        assert model.calls == 0
        assert model.completed == 0
        assert _message_count(thread_id) == 0
        sent = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert sent.status_code == 200
        stored = _stored_messages(thread_id)
        calls = model.calls
        again = client.post(f"/threads/{thread_id}/retry")
        assert again.status_code == 400
        assert again.json() == {"error": NO_FAILURE}
        assert model.calls == calls
        assert model.completed == 0
        assert _stored_messages(thread_id) == stored
        assert _message_count(thread_id) == 2
    finally:
        _remove(email)


def _account_id(email: str) -> uuid.UUID:
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s",
            (email.lower(),),
        ).fetchone()
    assert row is not None
    return row[0]


def _thread_summary(thread_id: str) -> str | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT summary FROM thread WHERE id = %s",
            (thread_id,),
        ).fetchone()
    assert row is not None
    return row[0]


def _usage_full(email: str) -> list[tuple]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT operation, provider, model, input_tokens, output_tokens,
                   total_tokens, message_id, run_id
            FROM usage
            WHERE email = %s
            ORDER BY at ASC, id ASC
            """,
            (email.lower(),),
        ).fetchall()
    return list(rows)


def _stamp_message(connection, message_id: uuid.UUID, when: datetime) -> None:
    connection.execute(
        "UPDATE message SET created_at = %s WHERE id = %s",
        (when, message_id),
    )


def _seed_long(email: str, thread_id: str, *, failure: bool) -> None:
    account_id = _account_id(email)
    thread = uuid.UUID(thread_id)
    base = datetime(2020, 1, 1, tzinfo=UTC)
    with connect() as connection:
        for index in range(11):
            person = insert_message(connection, account_id, thread, "person", "chat", f"p{index}")
            assert person is not None
            _stamp_message(connection, person[0], base + timedelta(seconds=index))
        for index in range(10):
            assistant = insert_message(
                connection, account_id, thread, "assistant", "chat", f"a{index}"
            )
            assert assistant is not None
            _stamp_message(connection, assistant[0], base + timedelta(seconds=100 + index))
        if not failure:
            return
        again = insert_message(connection, account_id, thread, "person", "chat", "again")
        assert again is not None
        _stamp_message(connection, again[0], base + timedelta(seconds=200))
        failed = insert_message(connection, account_id, thread, "assistant", "failure", FAILED)
        assert failed is not None
        _stamp_message(connection, failed[0], base + timedelta(seconds=201))


_FOLD = "Summarize the earlier messages so the next reply can continue."
_FOLDED = "earlier-folded-quill"
_SUMMARY_CAUSE = "summary-cause-ibex"


def _reply_blocks(last: str, *, oldest: int) -> list[tuple[str, str]]:
    blocks = [("person", f"p{index}") for index in range(oldest, 11)]
    blocks.extend(("assistant", f"a{index}") for index in range(10))
    blocks.append(("person", last))
    return blocks


def test_short_thread_sends_every_chat_message_and_skips_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Short")
    _remove(email)
    model = FakeModel(
        reply=Reply("visible-answer", 5, 6, 11),
        chunks=("visible-answer",),
        token_count=1,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        account_id = _account_id(email)
        thread = uuid.UUID(thread_id)
        base = datetime(2020, 1, 1, tzinfo=UTC)
        with connect() as connection:
            earlier = insert_message(connection, account_id, thread, "person", "chat", "earlier")
            old = insert_message(connection, account_id, thread, "assistant", "chat", "old-reply")
            failure = insert_message(connection, account_id, thread, "assistant", "failure", FAILED)
            assert earlier is not None
            assert old is not None
            assert failure is not None
            _stamp_message(connection, earlier[0], base)
            _stamp_message(connection, old[0], base + timedelta(seconds=1))
            _stamp_message(connection, failure[0], base + timedelta(seconds=2))
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "now"})
        loaded = client.get(f"/threads/{thread_id}")
        assert posted.status_code == 200
        assert model.order == ["stream"]
        assert model.completed == 0
        assert model.seen == [
            (HARM, [("person", "earlier"), ("assistant", "old-reply"), ("person", "now")])
        ]
        assert _thread_summary(thread_id) is None
        body = _assert_thread(loaded.json())
        messages = body["messages"]
        assert isinstance(messages, list)
        assistant = next(item for item in messages if item["body"] == "visible-answer")
        assert isinstance(assistant, dict)
        assert _usage_full(email) == [
            (
                "chat",
                "openai",
                "gpt-6.1-sol",
                5,
                6,
                11,
                uuid.UUID(str(assistant["id"])),
                None,
            )
        ]
    finally:
        _remove(email)


def test_a_long_thread_writes_the_summary_before_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Long")
    _remove(email)
    model = FakeModel(
        reply=Reply("visible-answer", 5, 6, 11),
        chunks=("visible-answer",),
        summary_reply=Reply(_FOLDED, 7, 8, 15),
        overflow_text="p0",
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _seed_long(email, thread_id, failure=False)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "p-new"})
        loaded = client.get(f"/threads/{thread_id}")
        assert posted.status_code == 200
        assert model.order == ["complete", "stream"]
        assert model.folded == [(_FOLD, [("person", "p0")])]
        assert model.seen == [(f"{HARM}\n{_FOLDED}", _reply_blocks("p-new", oldest=1))]
        assert _thread_summary(thread_id) == _FOLDED
        assert _FOLDED not in posted.text
        assert _FOLDED not in loaded.text
        _assert_thread(loaded.json())
        reply_id = next(row[0] for row in _message_rows(thread_id) if row[3] == "visible-answer")
        rows = _usage_full(email)
        assert len(rows) == 2
        summary_row = next(row for row in rows if row[6] is None)
        reply_row = next(row for row in rows if row[6] is not None)
        assert summary_row == ("chat", "openai", "gpt-6.1-sol", 7, 8, 15, None, None)
        assert reply_row == ("chat", "openai", "gpt-6.1-sol", 5, 6, 11, reply_id, None)
    finally:
        _remove(email)


def test_a_failed_summary_keeps_the_older_messages_and_one_failure_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Missed")
    _remove(email)
    model = FakeModel(
        error=RuntimeError(CAUSE),
        complete_error=RuntimeError(_SUMMARY_CAUSE),
        overflow_text="p0",
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _seed_long(email, thread_id, failure=False)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "p-new"})
        loaded = client.get(f"/threads/{thread_id}")
        assert posted.status_code == 200
        assert _events(posted.text) == [{"failed": True}]
        assert model.order == ["complete", "stream"]
        assert model.seen == [(HARM, _reply_blocks("p-new", oldest=0))]
        assert _SUMMARY_CAUSE not in posted.text
        assert CAUSE not in posted.text
        assert _SUMMARY_CAUSE not in loaded.text
        assert _thread_summary(thread_id) is None
        stored = _stored_messages(thread_id)
        assert [row for row in stored if row[1] == "failure"] == [("assistant", "failure", FAILED)]
        assert _usage_full(email) == [
            ("chat", "openai", "gpt-6.1-sol", None, None, None, None, None)
        ]
    finally:
        _remove(email)


def test_retry_on_a_long_thread_writes_the_summary_before_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("RetryLong")
    _remove(email)
    model = FakeModel(
        reply=Reply("visible-answer", 5, 6, 11),
        chunks=("visible-answer",),
        summary_reply=Reply(_FOLDED, 7, 8, 15),
        overflow_text="p0",
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _seed_long(email, thread_id, failure=True)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/retry")
        loaded = client.get(f"/threads/{thread_id}")
        assert posted.status_code == 200
        assert model.order == ["complete", "stream"]
        assert model.folded == [(_FOLD, [("person", "p0")])]
        assert model.seen == [(f"{HARM}\n{_FOLDED}", _reply_blocks("again", oldest=1))]
        assert _thread_summary(thread_id) == _FOLDED
        assert _FOLDED not in posted.text
        assert _FOLDED not in loaded.text
        _assert_thread(loaded.json())
        reply_id = next(row[0] for row in _message_rows(thread_id) if row[3] == "visible-answer")
        rows = _usage_full(email)
        assert len(rows) == 2
        summary_row = next(row for row in rows if row[6] is None)
        reply_row = next(row for row in rows if row[6] is not None)
        assert summary_row == ("chat", "openai", "gpt-6.1-sol", 7, 8, 15, None, None)
        assert reply_row == ("chat", "openai", "gpt-6.1-sol", 5, 6, 11, reply_id, None)
        assert all(row[1] != "failure" for row in _stored_messages(thread_id))
    finally:
        _remove(email)


def _tool(name: str, arguments: dict[str, object], call_id: str = "c1") -> Reply:
    return Reply("", 5, 1, 6, (ToolCall(call_id, name, arguments),))


def _thread_name(client: TestClient, thread_id: str) -> str:
    loaded = client.get(f"/threads/{thread_id}")
    assert loaded.status_code == 200
    return str(loaded.json()["name"])


def test_agent_renames_the_thread_it_is_in(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Rename")
    _remove(email)
    model = ScriptedModel(
        script=(_tool("rename_thread", {"name": "Taxes"}), Reply("Renamed.", 7, 2, 9))
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "call it Taxes"})
        assert _events(posted.text) == [
            {"tool": "rename_thread"},
            {"text": "Renamed."},
            {"done": True},
        ]
        assert _thread_name(client, thread_id) == "Taxes"
        assert _stored_messages(thread_id) == [
            ("person", "chat", "call it Taxes"),
            ("assistant", "chat", "Renamed."),
        ]
        assistant = _assistant_id(thread_id)
        usage = sorted(_usage_rows(email), key=lambda row: row[5])
        assert [row[3:] for row in usage] == [(5, 1, 6, assistant), (7, 2, 9, assistant)]
        assert {spec.name for spec in model.seen[0][1]} >= {"rename_thread", "delete_thread"}
        second = model.seen[1][0]
        assert second[-2].role == "assistant"
        assert second[-2].tool_calls[0].name == "rename_thread"
        assert second[-1].role == "tool"
        assert second[-1].call_id == "c1"
        assert json.loads(second[-1].text)["ok"] is True
    finally:
        _remove(email)


def test_unknown_tool_and_bad_arguments_continue_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("BadTool")
    _remove(email)
    model = ScriptedModel(
        script=(
            _tool("drop_tables", {}, "c1"),
            _tool("rename_thread", {"name": ""}, "c2"),
            Reply("Sorry.", 1, 1, 2),
        )
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert _events(posted.text)[-1] == {"done": True}
        assert json.loads(model.seen[1][0][-1].text)["code"] == "unknown"
        assert json.loads(model.seen[2][0][-1].text)["code"] == "invalid"
        assert _thread_name(client, thread_id) == "New thread"
        assert _stored_messages(thread_id)[-1] == ("assistant", "chat", "Sorry.")
    finally:
        _remove(email)


def test_turn_stops_after_eight_tool_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Rounds")
    _remove(email)
    model = ScriptedModel(
        reply="never",
        script=tuple(_tool("list_threads", {}, f"c{index}") for index in range(9)),
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "loop"})
        events = _events(posted.text)
        assert events.count({"tool": "list_threads"}) == 8
        assert events[-2:] == [{"text": ROUNDS_REACHED}, {"done": True}]
        assert ROUNDS_REACHED == "I stopped after 8 tool steps."
        assert _stored_messages(thread_id)[-1] == ("assistant", "chat", ROUNDS_REACHED)
        assert len(model.seen) == 9
        assert _usage_count(email) == 9
    finally:
        _remove(email)


def test_agent_deletes_its_own_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("SelfDelete")
    _remove(email)
    model = ScriptedModel(script=(_tool("delete_thread", {}), Reply("Deleted.", 1, 1, 2)))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "delete this"})
        assert _events(posted.text)[-1] == {"done": True}
        assert client.get(f"/threads/{thread_id}").status_code == 404
        assert _message_count(thread_id) == 0
        usage = _usage_rows(email)
        assert len(usage) >= 1
        assert all(row[6] is None for row in usage)
        assert (5, 1, 6) in [row[3:6] for row in usage]
    finally:
        _remove(email)


class _HeldToolModel:
    def __init__(self, hold: threading.Event, arrived: threading.Event) -> None:
        self._hold = hold
        self._arrived = arrived
        self.calls = 0

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        return 1

    def complete(self, prompt: str, blocks: list[Block], tools=()) -> Reply:
        return Reply("", None, None, None)

    def stream(self, prompt: str, blocks: list[Block], tools=()):
        self.calls += 1
        outer = self

        class _Held:
            def __iter__(self):
                outer._arrived.set()
                outer._hold.wait()
                return iter(())

            def reply(self) -> Reply:
                return _tool("rename_thread", {"name": "Too late"})

        return _Held()


def test_no_tool_runs_after_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("StopTool")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = _HeldToolModel(release, arrived)
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "rename"},
        ) as response:
            assert arrived.wait(2)
            assert client.post(f"/threads/{thread_id}/stop").status_code == 204
            release.set()
            assert _next_event(response.iter_lines()) == {"done": True}
        assert _thread_name(client, thread_id) == "New thread"
        assert model.calls == 1
    finally:
        release.set()
        _remove(email)


def test_database_failure_inside_a_tool_fails_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("ToolDown")
    _remove(email)
    model = ScriptedModel(script=(_tool("list_threads", {}), Reply("unreached", 1, 1, 2)))

    def broken(*args: object, **kwargs: object) -> dict[str, object]:
        raise psycopg.OperationalError(CAUSE)

    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        monkeypatch.setattr("app.operations.call", broken)
        started = time.monotonic()
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert time.monotonic() - started < 5
        assert _events(posted.text)[-1] == {"failed": True}
        assert CAUSE not in posted.text
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "failure", FAILED),
        ]
        assert [row[3:] for row in _usage_rows(email)] == [(5, 1, 6, None)]
        assert len(model.seen) == 1
    finally:
        _remove(email)


BAD_NAME = "A thread name is 1 to 80 characters."


def test_patch_renames_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Patch")
    _remove(email)
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        renamed = client.patch(f"/threads/{thread_id}", json={"name": "  Tax  "})
        assert renamed.status_code == 200
        _assert_item(renamed.json())
        assert renamed.json()["name"] == "Tax"
        assert renamed.json()["id"] == thread_id
        assert [item["name"] for item in client.get("/threads").json()] == ["Tax"]
    finally:
        _remove(email)


def test_patch_refuses_bad_names(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("PatchBad")
    _remove(email)
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        for body in ({"name": ""}, {"name": "   "}, {"name": "x" * 81}, {"name": 7}, {}, []):
            refused = client.patch(f"/threads/{thread_id}", json=body)
            assert refused.status_code == 400
            assert refused.json() == {"error": BAD_NAME}
        unreadable = client.patch(f"/threads/{thread_id}", content=b"not json")
        assert unreadable.status_code == 400
        assert _thread_name(client, thread_id) == "New thread"
        assert client.patch(f"/threads/{thread_id}", json={"name": "x" * 80}).status_code == 200
    finally:
        _remove(email)


def test_patch_and_delete_another_accounts_thread_are_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _email("Owner")
    stranger = _email("Stranger")
    _remove(owner)
    _remove(stranger)
    try:
        owner_client, thread_id = _open_thread(monkeypatch, owner)
        other = _sign_in(monkeypatch, stranger)
        renamed = other.patch(f"/threads/{thread_id}", json={"name": "Mine"})
        deleted = other.delete(f"/threads/{thread_id}")
        assert renamed.status_code == 404
        assert renamed.json() == {"error": "Not found"}
        assert deleted.status_code == 404
        assert deleted.json() == {"error": "Not found"}
        assert other.patch("/threads/not-a-uuid", json={"name": "Mine"}).status_code == 404
        assert other.delete("/threads/not-a-uuid").status_code == 404
        assert _thread_name(owner_client, thread_id) == "New thread"
    finally:
        _remove(owner)
        _remove(stranger)


def test_delete_returns_204_and_the_thread_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Delete")
    _remove(email)
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        kept = client.post("/threads").json()["id"]
        deleted = client.delete(f"/threads/{thread_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert client.get(f"/threads/{thread_id}").status_code == 404
        assert [item["id"] for item in client.get("/threads").json()] == [kept]
        assert client.delete(f"/threads/{thread_id}").status_code == 404
    finally:
        _remove(email)


def test_post_thread_with_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Named")
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        named = client.post("/threads", json={"name": " Trip "})
        assert named.status_code == 200
        assert named.json()["name"] == "Trip"
        refused = client.post("/threads", json={"name": "x" * 81})
        assert refused.status_code == 400
        assert refused.json() == {"error": BAD_NAME}
        assert len(client.get("/threads").json()) == 1
    finally:
        _remove(email)


def test_rename_and_delete_need_a_session() -> None:
    client = TestClient(app)
    thread_id = str(uuid.uuid4())
    renamed = client.patch(f"/threads/{thread_id}", json={"name": "Tax"})
    deleted = client.delete(f"/threads/{thread_id}")
    assert renamed.status_code == 401
    assert deleted.status_code == 401


def test_delete_while_the_reply_is_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("DeleteLive")
    _remove(email)
    release = threading.Event()
    arrived = threading.Event()
    model = FakeModel(
        reply=Reply("first second", 11, 12, 23),
        chunks=("first", " second"),
        holds=(None, release),
        arrived=arrived,
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            lines = response.iter_lines()
            assert _next_event(lines) == {"text": "first"}
            assert arrived.wait(2)
            assert client.delete(f"/threads/{thread_id}").status_code == 204
            release.set()
            assert _next_event(lines) == {"done": True}
        assert _message_count(thread_id) == 0
        assert client.get(f"/threads/{thread_id}").status_code == 404
        assert _usage_rows(email) == [("chat", "openai", "gpt-6.1-sol", None, None, None, None)]
    finally:
        release.set()
        _remove(email)


def test_the_browser_may_send_patch_and_delete() -> None:
    client = TestClient(app)
    for method in ("PATCH", "DELETE"):
        allowed = client.options(
            "/threads/x",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": method},
        )
        assert allowed.status_code == 200
        assert method in allowed.headers["access-control-allow-methods"]


def test_a_failed_save_still_writes_the_usage_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.turn as turn

    email = _email("SaveDown")
    _remove(email)
    model = ScriptedModel(script=(Reply("hello there", 5, 1, 6),))
    real = turn.insert_message

    def broken(connection, account_id, thread_id, role, kind, body, run_id=None):
        if role == "assistant":
            raise psycopg.OperationalError(CAUSE)
        return real(connection, account_id, thread_id, role, kind, body, run_id)

    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        monkeypatch.setattr("app.turn.insert_message", broken)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert _events(posted.text)[-1] == {"failed": True}
        assert CAUSE not in posted.text
        assert [row[3:] for row in _usage_rows(email)] == [(5, 1, 6, None)]
    finally:
        _remove(email)


def test_text_from_two_model_calls_is_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("TwoTexts")
    _remove(email)
    model = ScriptedModel(
        script=(
            Reply(
                "I will rename it.",
                5,
                1,
                6,
                (ToolCall("c1", "rename_thread", {"name": "Tax"}),),
            ),
            Reply("Done.", 7, 2, 9),
        )
    )
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        posted = client.post(f"/threads/{thread_id}/messages", json={"text": "rename"})
        text = "".join(str(event["text"]) for event in _events(posted.text) if "text" in event)
        assert text == "I will rename it.\n\nDone."
        assert _stored_messages(thread_id)[-1] == (
            "assistant",
            "chat",
            "I will rename it.\n\nDone.",
        )
    finally:
        _remove(email)


def test_an_unreadable_tool_call_is_answered_and_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = _email("Unreadable")
    _remove(email)
    broken = ToolCall("c1", "rename_thread", {}, unreadable=True)
    model = ScriptedModel(script=(Reply("", 5, 1, 6, (broken,)), Reply("Sorry.", 1, 1, 2)))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with caplog.at_level("WARNING", logger="folio"):
            posted = client.post(f"/threads/{thread_id}/messages", json={"text": "rename"})
        assert _events(posted.text)[-1] == {"done": True}
        result = json.loads(model.seen[1][0][-1].text)
        assert result == {"ok": False, "code": "invalid", "error": "That input is not valid."}
        assert _thread_name(client, thread_id) == "New thread"
        assert any("rename_thread" in record.getMessage() for record in caplog.records)
    finally:
        _remove(email)


def test_a_refused_tool_call_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = _email("RefusedTool")
    _remove(email)
    model = ScriptedModel(script=(_tool("drop_tables", {}), Reply("Sorry.", 1, 1, 2)))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with caplog.at_level("WARNING", logger="folio"):
            client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert any(
            "drop_tables" in record.getMessage() and "unknown" in record.getMessage()
            for record in caplog.records
        )
    finally:
        _remove(email)


def test_a_failed_turn_is_logged_without_the_person_seeing_the_cause(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    email = _email("LoudFail")
    _remove(email)
    model = FakeModel(error=RuntimeError(CAUSE))
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        _use_model(monkeypatch, model)
        with caplog.at_level("ERROR", logger="folio"):
            posted = client.post(f"/threads/{thread_id}/messages", json={"text": "hello"})
        assert _events(posted.text)[-1] == {"failed": True}
        assert CAUSE not in posted.text
        errors = [record for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1
        assert errors[0].exc_info is not None
        assert thread_id in errors[0].getMessage()
    finally:
        _remove(email)


FOREIGN = "This request did not come from Folio."


def test_a_change_from_another_site_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Origin")
    _remove(email)
    evil = {"Origin": "https://evil.example"}
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        for response in (
            client.post("/threads", headers=evil),
            client.patch(f"/threads/{thread_id}", json={"name": "Owned"}, headers=evil),
            client.delete(f"/threads/{thread_id}", headers=evil),
            client.post("/display-name", json={"display_name": "Owned"}, headers=evil),
            client.delete("/account", headers=evil),
            client.post("/sign-out", headers=evil),
            client.post("/sign-in", json={"id_token": "token"}, headers=evil),
        ):
            assert response.status_code == 403
            assert response.json() == {"error": FOREIGN}
        assert _thread_name(client, thread_id) == "New thread"
        assert len(client.get("/threads").json()) == 1
        assert client.get("/session").status_code == 200
    finally:
        _remove(email)


def test_a_change_from_the_site_itself_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("OwnOrigin")
    _remove(email)
    try:
        client, thread_id = _open_thread(monkeypatch, email)
        monkeypatch.setenv("FRONTEND_ORIGIN", "https://folio.example")
        for origin in ("http://localhost:3000", "https://folio.example", "http://testserver"):
            renamed = client.patch(
                f"/threads/{thread_id}",
                json={"name": "Mine"},
                headers={"Origin": origin},
            )
            assert renamed.status_code == 200
        # A proxy in front says which host the browser asked for.
        forwarded = client.patch(
            f"/threads/{thread_id}",
            json={"name": "Mine"},
            headers={"Origin": "https://preview.example", "X-Forwarded-Host": "preview.example"},
        )
        assert forwarded.status_code == 200
        assert client.patch(f"/threads/{thread_id}", json={"name": "Mine"}).status_code == 200
    finally:
        _remove(email)


def test_reading_is_not_refused_by_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("ReadOrigin")
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        read = client.get("/threads", headers={"Origin": "https://evil.example"})
        assert read.status_code == 200
        assert "access-control-allow-origin" not in read.headers
    finally:
        _remove(email)
