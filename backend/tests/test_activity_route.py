import uuid
from collections.abc import Iterator

import pytest
from app.account import ensure_account
from app.db import connect
from app.main import app as public_app
from app.run import create_run, finish_run
from app.run_activity import record_activity
from app.sign_in_proof import VerifiedSignIn
from app.thread import create_thread
from fastapi.testclient import TestClient


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"activity-route-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM session WHERE account_id = %s", (account_id,))
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


def _signed_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(public_app)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def test_the_route_needs_a_session() -> None:
    assert TestClient(public_app).get(f"/threads/{uuid.uuid4()}/activity").status_code == 401


def test_a_thread_with_a_run_going_lists_what_it_has_done(account, monkeypatch) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        thread = create_thread(connection, account_id, "Heat pumps")
        run = create_run(connection, account_id, thread[0], None, "research", "question")
        assert run is not None
        record_activity(connection, account_id, run[0], "search", "heat pumps")
        record_activity(connection, account_id, run[0], "read", "Title A", "https://a.example/x")

    response = client.get(f"/threads/{thread[0]}/activity")

    assert response.status_code == 200
    body = response.json()
    assert body["working"] is True
    assert body["kind"] == "research"
    assert body["since"] == run[10].isoformat()
    assert [(item["kind"], item["text"], item["url"]) for item in body["items"]] == [
        ("search", "heat pumps", None),
        ("read", "Title A", "https://a.example/x"),
    ]
    assert all(isinstance(item["at"], str) for item in body["items"])
    assert set(body["items"][0]) == {"at", "kind", "text", "url"}


def test_a_thread_with_nothing_running_lists_nothing(account, monkeypatch) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        thread = create_thread(connection, account_id, "Quiet")

    response = client.get(f"/threads/{thread[0]}/activity")

    assert response.status_code == 200
    assert response.json() == {"working": False, "kind": None, "since": None, "items": []}


def test_a_finished_run_no_longer_shows_its_lines(account, monkeypatch) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    with connect() as connection:
        thread = create_thread(connection, account_id, "Done")
        run = create_run(connection, account_id, thread[0], None, "research", "question")
        assert run is not None
        record_activity(connection, account_id, run[0], "search", "old words")
        finish_run(connection, run[0], "ready", 1, True)

    response = client.get(f"/threads/{thread[0]}/activity")

    assert response.json() == {"working": False, "kind": None, "since": None, "items": []}


def test_another_accounts_thread_or_a_bad_id_is_not_found(account, monkeypatch) -> None:
    account_id, email = account
    other_id, other_email = _new_account()
    try:
        client = _signed_in(monkeypatch, email)
        with connect() as connection:
            theirs = create_thread(connection, other_id, "Theirs")
            run = create_run(connection, other_id, theirs[0], None, "research", "q")
            assert run is not None
            record_activity(connection, other_id, run[0], "search", "private words")

        response = client.get(f"/threads/{theirs[0]}/activity")

        assert response.status_code == 404
        assert "private words" not in response.text
        assert client.get("/threads/not-an-id/activity").status_code == 404
        assert client.get(f"/threads/{uuid.uuid4()}/activity").status_code == 404
    finally:
        _remove(other_id, other_email)
