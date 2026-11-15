import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app import worker
from app.account import ensure_account
from app.db import connect
from app.main import app as public_app
from app.model import FakeModel
from app.run import create_run
from app.schedule import create_schedule
from app.sign_in_proof import VerifiedSignIn
from app.thread import create_thread
from fastapi.testclient import TestClient


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"worker-{uuid.uuid4().hex}@example.com"
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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.worker.open_model", lambda: FakeModel(reply="none"))
    return TestClient(worker.app)


def test_health(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_tick_returns_the_counts(client, monkeypatch: pytest.MonkeyPatch) -> None:
    counts = {"due": 2, "started": 1, "skipped": 1, "held": 0, "stuck": 0}
    monkeypatch.setattr("app.scheduled.tick", lambda model: counts)

    response = client.post("/tick")

    assert response.status_code == 200
    assert response.json() == counts


@pytest.mark.parametrize(
    ("outcome", "status"),
    [("ready", 200), ("duplicate", 200), ("cancelled", 200), ("dead", 200), ("retry", 503)],
)
def test_run_status_codes(client, account, monkeypatch, outcome, status) -> None:
    account_id, _ = account
    with connect() as connection:
        thread = create_thread(connection, account_id)
        run = create_run(connection, account_id, thread[0], None, "schedule", "t")
    assert run is not None
    monkeypatch.setattr("app.scheduled.execute", lambda run_id, model=None: outcome)

    response = client.post(f"/runs/{run[0]}")

    assert response.status_code == status


def test_an_unknown_run_is_404(client) -> None:
    assert client.post(f"/runs/{uuid.uuid4()}").status_code == 404
    assert client.post("/runs/not-an-id").status_code == 404


def test_the_worker_has_no_session_or_chat_routes() -> None:
    paths = {route.path for route in worker.app.routes}

    assert "/sign-in" not in paths and "/threads" not in paths and "/schedules" not in paths


def test_the_public_app_has_no_worker_routes() -> None:
    paths = {getattr(route, "path", "") for route in public_app.routes}

    assert "/tick" not in paths
    assert "/runs/{run_id}" not in paths
    assert not any(path.startswith("/runs") for path in paths)
    assert TestClient(public_app).post("/tick").status_code in (404, 405)
    assert TestClient(public_app).post(f"/runs/{uuid.uuid4()}").status_code in (404, 405)


def _signed_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(public_app)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def test_schedules_and_the_working_mark_need_a_session() -> None:
    client = TestClient(public_app)

    assert client.get("/schedules").status_code == 401
    assert client.get("/threads").status_code == 401


def test_schedules_and_working_show_only_the_callers_rows(
    account, monkeypatch: pytest.MonkeyPatch
) -> None:
    account_id, email = account
    other_id, other_email = _new_account()
    try:
        client = _signed_in(monkeypatch, email)
        soon = datetime.now(UTC) + timedelta(hours=1)
        with connect() as connection:
            mine = create_thread(connection, account_id, "Mine")
            quiet = create_thread(connection, account_id, "Quiet")
            theirs = create_thread(connection, other_id, "Theirs")
            first = create_schedule(connection, account_id, mine[0], "my task", "daily", soon)
            second = create_schedule(connection, account_id, quiet[0], "no time", "once", None)
            create_schedule(connection, other_id, theirs[0], "their task", "daily", soon)
            assert first is not None and second is not None
            create_run(connection, account_id, mine[0], first[0], "schedule", "my task")
            create_run(connection, other_id, theirs[0], None, "schedule", "their task")

        listed = client.get("/schedules")
        threads = client.get("/threads")

        assert listed.status_code == 200
        assert listed.json() == [
            {
                "id": str(first[0]),
                "task": "my task",
                "when_text": "daily",
                "next_run_at": first[5].isoformat(),
                "thread_id": str(mine[0]),
                "thread_name": "Mine",
            },
            {
                "id": str(second[0]),
                "task": "no time",
                "when_text": "once",
                "next_run_at": None,
                "thread_id": str(quiet[0]),
                "thread_name": "Quiet",
            },
        ]
        assert threads.status_code == 200
        marks = {item["name"]: item["working"] for item in threads.json()}
        assert marks == {"Mine": True, "Quiet": False}
    finally:
        _remove(other_id, other_email)
