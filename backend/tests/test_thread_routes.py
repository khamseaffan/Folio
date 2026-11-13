import json
import threading
import time
import uuid
from datetime import datetime

import pytest
from app.account import ensure_account
from app.db import connect
from app.main import app
from app.model import Block, Reply
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
    ) -> None:
        self.completed = 0
        self.calls = 0
        self._reply = reply if reply is not None else Reply("fake-reply", 424242, 434343, 858585)
        self._error = error
        self._chunks = chunks
        self._holds = holds
        self._error_at = error_at
        self._arrived = arrived if arrived is not None else threading.Event()

    def complete(self, prompt: str, blocks: list[Block]) -> Reply:
        self.completed += 1
        if self._error is not None:
            raise self._error
        return self._reply

    def stream(self, prompt: str, blocks: list[Block]) -> _Script:
        self.calls += 1
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
        return 1


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
    assert loaded.status_code == 404
    assert loaded.json() == {"error": "Not found"}
    assert posted.status_code == 404
    assert posted.json() == {"error": "Not found"}
    assert stopped.status_code == 404
    assert stopped.json() == {"error": "Not found"}
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
        assert "gemini-4-argon" not in posted.text
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
                "google",
                "gemini-4-argon",
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
        assert "gemini-4-argon" not in response.text
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
        assert _usage_rows(email) == [("chat", "google", "gemini-4-argon", None, None, None, None)]
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
        assert rows[0][0:6] == ("chat", "google", "gemini-4-argon", None, None, None)
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
        assert _usage_rows(email) == [("chat", "google", "gemini-4-argon", None, None, None, None)]
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
        assert rows[0][0:6] == ("chat", "google", "gemini-4-argon", 424242, 434343, 858585)
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
            ("chat", "google", "gemini-4-argon", 7, 8, 15, _assistant_id(thread_id))
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
        assert "gemini-4-argon" not in response.text
        assert _stored_messages(thread_id) == [
            ("person", "chat", "hello"),
            ("assistant", "chat", "kept"),
            ("assistant", "failure", FAILED),
        ]
        assert _usage_rows(email) == [
            ("chat", "google", "gemini-4-argon", None, None, None, _assistant_id(thread_id))
        ]
    finally:
        _remove(email)
