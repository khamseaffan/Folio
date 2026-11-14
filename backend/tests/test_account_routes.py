import threading
import uuid

import pytest
from app.db import connect
from app.main import app
from app.message import insert_message
from app.model import FakeModel
from app.operations import OPERATIONS
from app.sign_in_proof import SignInError, VerifiedSignIn
from app.thread import create_thread
from app.usage import apply as apply_usage
from app.usage import record_usage
from fastapi.testclient import TestClient

REFUSED = "The account could not be deleted. Try again."


def _email(label: str) -> str:
    return f"{label}.{uuid.uuid4()}@Example.com"


def _remove(email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        # On a new database nothing has created the tables yet.
        apply_usage(connection)
        connection.execute("DELETE FROM usage WHERE email = %s", (stored,))
        connection.execute("DELETE FROM account WHERE email = %s", (stored,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (stored,))


@pytest.fixture
def email():
    value = _email("account-routes")
    try:
        yield value
    finally:
        _remove(value)


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


def _account_id(email: str) -> uuid.UUID | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s", (email.lower(),)
        ).fetchone()
    return None if row is None else row[0]


def _add_usage(email: str, counts: tuple[int | None, int | None, int | None]) -> None:
    account_id = _account_id(email)
    assert account_id is not None
    with connect() as connection:
        record_usage(connection, account_id, email, "chat", "openai", "m", *counts)


def _count(table: str, column: str, value: object) -> int:
    with connect() as connection:
        row = connection.execute(
            f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)
        ).fetchone()
    assert row is not None
    return row[0]


def test_account_routes_require_sign_in() -> None:
    assert TestClient(app).get("/sessions").status_code == 401
    assert TestClient(app).get("/usage").status_code == 401
    assert TestClient(app).delete("/account").status_code == 401
    assert TestClient(app).post(f"/sessions/{uuid.uuid4()}/end").status_code == 401


def test_sessions_lists_open_ones_and_marks_the_current(monkeypatch, email) -> None:
    first = _sign_in(monkeypatch, email)
    second = _sign_in(monkeypatch, email)
    listed = second.get("/sessions")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 2
    assert [item["current"] for item in body].count(True) == 1
    current = next(item for item in body if item["current"])
    assert current["id"] == second.cookies["session"]
    assert set(current) == {"id", "created_at", "last_active_at", "current"}
    assert first.get("/sessions").json()[0]["id"] == body[0]["id"]


def test_ending_another_session_keeps_this_one(monkeypatch, email) -> None:
    first = _sign_in(monkeypatch, email)
    second = _sign_in(monkeypatch, email)
    ended = second.post(f"/sessions/{first.cookies['session']}/end")
    assert ended.status_code == 204
    assert first.get("/session").status_code == 401
    assert second.get("/session").status_code == 200
    assert [item["current"] for item in second.get("/sessions").json()] == [True]


def test_ending_the_current_session_clears_the_cookie(monkeypatch, email) -> None:
    client = _sign_in(monkeypatch, email)
    ended = client.post(f"/sessions/{client.cookies['session']}/end")
    assert ended.status_code == 204
    assert "Max-Age=0" in ended.headers["set-cookie"]
    assert client.get("/session").status_code == 401


def test_ending_another_accounts_session_is_404(monkeypatch, email) -> None:
    other_email = _email("account-routes-other")
    try:
        mine = _sign_in(monkeypatch, email)
        theirs = _sign_in(monkeypatch, other_email)
        other_id = theirs.cookies["session"]
        assert mine.post(f"/sessions/{other_id}/end").status_code == 404
        assert theirs.get("/session").status_code == 200
        assert mine.post(f"/sessions/{uuid.uuid4()}/end").status_code == 404
        assert mine.post("/sessions/not-an-id/end").status_code == 404
    finally:
        _remove(other_email)


def test_ending_a_session_twice_is_404(monkeypatch, email) -> None:
    first = _sign_in(monkeypatch, email)
    second = _sign_in(monkeypatch, email)
    path = f"/sessions/{first.cookies['session']}/end"
    assert second.post(path).status_code == 204
    assert second.post(path).status_code == 404


def test_usage_sums_the_email_and_skips_missing_rows(monkeypatch, email) -> None:
    client = _sign_in(monkeypatch, email)
    _add_usage(email, (10, 5, 15))
    _add_usage(email, (1, 1, 2))
    _add_usage(email, (None, 3, 99))
    response = client.get("/usage")
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 11, "output_tokens": 6, "total_tokens": 17}


def test_usage_with_no_rows_is_zero(monkeypatch, email) -> None:
    client = _sign_in(monkeypatch, email)
    assert client.get("/usage").json() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usage_includes_rows_from_a_deleted_earlier_account(monkeypatch, email) -> None:
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)
    first = _sign_in(monkeypatch, email)
    _add_usage(email, (10, 5, 15))
    assert first.delete("/account").status_code == 204
    again = _sign_in(monkeypatch, email)
    _add_usage(email, (1, 1, 2))
    assert again.get("/usage").json() == {
        "input_tokens": 11,
        "output_tokens": 6,
        "total_tokens": 17,
    }


def test_usage_has_no_cost_field(monkeypatch, email) -> None:
    client = _sign_in(monkeypatch, email)
    _add_usage(email, (10, 5, 15))
    assert set(client.get("/usage").json()) == {
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }


def test_delete_account_removes_rows_and_keeps_usage_and_limit(monkeypatch, email) -> None:
    deleted: list[str] = []
    monkeypatch.setattr("app.main.delete_firebase_user", deleted.append)
    client = _sign_in(monkeypatch, email)
    account_id = _account_id(email)
    assert account_id is not None
    with connect() as connection:
        thread = create_thread(connection, account_id, "Keep nothing")
        insert_message(connection, account_id, thread[0], "person", "chat", "hello")
        connection.execute(
            "UPDATE email_limit SET token_cap = 42, stopped = true WHERE email = %s",
            (email.lower(),),
        )
        uid = connection.execute(
            "SELECT firebase_uid FROM account WHERE id = %s", (account_id,)
        ).fetchone()
    assert uid is not None
    _add_usage(email, (10, 5, 15))

    response = client.delete("/account")

    assert response.status_code == 204
    assert deleted == [uid[0]]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert _account_id(email) is None
    assert _count("session", "account_id", account_id) == 0
    assert _count("thread", "account_id", account_id) == 0
    assert _count("message", "account_id", account_id) == 0
    with connect() as connection:
        usage = connection.execute(
            "SELECT account_id FROM usage WHERE email = %s", (email.lower(),)
        ).fetchall()
        limit = connection.execute(
            "SELECT token_cap, stopped FROM email_limit WHERE email = %s",
            (email.lower(),),
        ).fetchone()
    assert usage == [(None,)]
    assert limit == (42, True)


def test_delete_account_with_a_second_session_open(monkeypatch, email) -> None:
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)
    first = _sign_in(monkeypatch, email)
    second = _sign_in(monkeypatch, email)
    assert first.delete("/account").status_code == 204
    assert second.get("/session").status_code == 401


def test_delete_account_stops_live_replies(monkeypatch, email) -> None:
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)
    stopped: list[uuid.UUID] = []
    monkeypatch.setattr("app.main.stop_account", stopped.append)
    client = _sign_in(monkeypatch, email)
    account_id = _account_id(email)
    assert client.delete("/account").status_code == 204
    assert stopped == [account_id]


def test_delete_account_mid_reply_keeps_the_usage_row(monkeypatch, email) -> None:
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)
    release = threading.Event()
    model = FakeModel(chunks=("first", " second"), pauses=(release,))
    monkeypatch.setattr("app.main.open_model", lambda: model)
    client = _sign_in(monkeypatch, email)
    thread_id = client.post("/threads").json()["id"]
    try:
        with client.stream(
            "POST",
            f"/threads/{thread_id}/messages",
            json={"text": "hello"},
        ) as response:
            lines = response.iter_lines()
            assert next(line for line in lines if line.startswith("data: ")) == (
                'data: {"text": "first"}'
            )
            assert client.delete("/account").status_code == 204
            release.set()
            for _ in lines:
                pass
    finally:
        release.set()
    with connect() as connection:
        rows = connection.execute(
            "SELECT account_id, usage_missing FROM usage WHERE email = %s",
            (email.lower(),),
        ).fetchall()
    assert rows == [(None, True)]


def test_firebase_refusal_keeps_the_account(monkeypatch, email) -> None:
    def refuse(uid: str) -> None:
        raise SignInError("provider text")

    monkeypatch.setattr("app.main.delete_firebase_user", refuse)
    client = _sign_in(monkeypatch, email)
    account_id = _account_id(email)
    assert account_id is not None
    with connect() as connection:
        create_thread(connection, account_id, "Still here")
    response = client.delete("/account")
    assert response.status_code == 502
    assert response.json() == {"error": REFUSED}
    assert _account_id(email) == account_id
    assert _count("thread", "account_id", account_id) == 1
    assert client.get("/session").status_code == 200


def test_sign_in_again_after_deletion_creates_a_new_account_with_the_old_cap(
    monkeypatch, email
) -> None:
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)
    first = _sign_in(monkeypatch, email)
    old_id = _account_id(email)
    with connect() as connection:
        connection.execute(
            "UPDATE email_limit SET token_cap = 77 WHERE email = %s", (email.lower(),)
        )
    assert first.delete("/account").status_code == 204
    _sign_in(monkeypatch, email)
    new_id = _account_id(email)
    assert new_id is not None and new_id != old_id
    with connect() as connection:
        cap = connection.execute(
            "SELECT token_cap FROM email_limit WHERE email = %s", (email.lower(),)
        ).fetchone()
    assert cap == (77,)


def test_account_without_a_firebase_uid_skips_firebase(monkeypatch, email) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.main.delete_firebase_user", calls.append)
    client = _sign_in(monkeypatch, email)
    with connect() as connection:
        connection.execute(
            "UPDATE account SET firebase_uid = NULL WHERE email = %s", (email.lower(),)
        )
    assert client.delete("/account").status_code == 204
    assert calls == []
    assert _account_id(email) is None


def test_account_actions_are_not_agent_tools() -> None:
    assert not {"delete_account", "end_session", "set_display_name"} & set(OPERATIONS)
