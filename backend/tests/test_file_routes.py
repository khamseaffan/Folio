import uuid
from collections.abc import Iterator
from urllib.parse import unquote

import pytest
from app.account import ensure_account
from app.db import connect
from app.file import ACCOUNT_LIMIT_BYTES, BAD_FILE_TYPE, MAX_UPLOAD_BYTES, NOT_UPLOADED, TOO_BIG
from app.main import app as public_app
from app.sign_in_proof import VerifiedSignIn
from app.storage import FakeStorage
from fastapi.testclient import TestClient


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"file-routes-{uuid.uuid4().hex}@example.com"
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
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr("app.main.open_storage", lambda: fake)
    return fake


def _signed_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(public_app, follow_redirects=False)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def _send(client, storage, name="notes.pdf", data=b"x" * 3000):
    started = client.post("/files", json={"name": name, "size": len(data)})
    assert started.status_code == 201
    body = started.json()
    storage.put(f"pending/{body['id']}", data)
    return body, client.post(f"/files/{body['id']}/complete")


def test_every_file_route_needs_a_session(storage) -> None:
    client = TestClient(public_app)
    some = uuid.uuid4()

    assert client.get("/files").status_code == 401
    assert client.post("/files", json={"name": "a.pdf", "size": 1}).status_code == 401
    assert client.post(f"/files/{some}/complete").status_code == 401
    assert client.get(f"/files/{some}/download").status_code == 401


def test_an_upload_starts_with_a_signed_address_and_the_browser_must_send_the_type(
    account, monkeypatch, storage
) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)

    response = client.post("/files", json={"name": "Report.PDF", "size": 3000})

    assert response.status_code == 201
    body = response.json()
    assert body["upload"]["method"] == "PUT"
    assert body["upload"]["headers"] == {"Content-Type": "application/pdf"}
    assert body["upload"]["url"] == storage.upload_url(f"pending/{body['id']}", "application/pdf")


def test_an_upload_that_is_not_taken_says_why(account, monkeypatch, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)

    bad = client.post("/files", json={"name": "a.exe", "size": 10})
    big = client.post("/files", json={"name": "a.pdf", "size": MAX_UPLOAD_BYTES + 1})

    assert bad.status_code == 400 and bad.json() == {"error": BAD_FILE_TYPE}
    assert big.status_code == 400 and big.json() == {"error": TOO_BIG}
    wrong = ({}, {"name": "a.pdf"}, {"name": 5, "size": 1}, {"name": "a.pdf", "size": "9"})
    for body in wrong:
        assert client.post("/files", json=body).status_code == 400
    assert client.post("/files").status_code == 400


def test_a_finished_upload_is_listed_with_the_byte_count_and_the_limit(
    account, monkeypatch, storage
) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    empty = client.get("/files").json()
    assert empty == {"files": [], "used_bytes": 0, "limit_bytes": ACCOUNT_LIMIT_BYTES}

    started, done = _send(client, storage)

    assert done.status_code == 200
    item = done.json()
    assert item["id"] == started["id"] and item["name"] == "notes.pdf"
    assert item["kind"] == "upload" and item["byte_size"] == 3000
    assert item["content_type"] == "application/pdf" and isinstance(item["created_at"], str)
    listed = client.get("/files").json()
    assert listed["files"] == [item]
    assert listed["used_bytes"] == 3000


def test_a_file_that_did_not_arrive_cannot_be_completed(account, monkeypatch, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    started = client.post("/files", json={"name": "a.pdf", "size": 100}).json()

    response = client.post(f"/files/{started['id']}/complete")

    assert response.status_code == 400 and response.json() == {"error": NOT_UPLOADED}
    assert client.get("/files").json()["files"] == []


def test_download_goes_to_a_signed_address_for_that_file(account, monkeypatch, storage) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    started, _done = _send(client, storage, "My notes.pdf")

    response = client.get(f"/files/{started['id']}/download")

    assert response.status_code == 302
    path = f"accounts/{account_id}/files/{started['id']}/My notes.pdf"
    assert unquote(response.headers["location"]) == storage.download_url(path, "My notes.pdf")


def test_another_accounts_file_is_not_found_on_every_route(account, monkeypatch, storage) -> None:
    _, email = account
    other_id, other_email = _new_account()
    try:
        client = _signed_in(monkeypatch, email)
        started, _done = _send(client, storage)
        other = _signed_in(monkeypatch, other_email)

        assert other.get("/files").json()["files"] == []
        assert other.get(f"/files/{started['id']}/download").status_code == 404
        assert other.post(f"/files/{started['id']}/complete").status_code == 404
        assert other.get("/files/not-an-id/download").status_code == 404
        assert other.post(f"/files/{uuid.uuid4()}/complete").status_code == 404
    finally:
        _remove(other_id, other_email)
