import uuid
from collections.abc import Iterator
from urllib.parse import parse_qs, urlsplit

import pytest
from app.account import ensure_account
from app.db import connect
from app.file import BAD_FILE_TYPE, MAX_UPLOAD_BYTES, TOO_BIG, byte_count
from app.google_drive import CANNOT_OPEN, NOT_CONNECTED, DriveFile, FakeDrive
from app.integration import GOOGLE_DRIVE, load_integration
from app.kms import FakeKms
from app.main import app as public_app
from app.sign_in_proof import VerifiedSignIn
from app.storage import FakeStorage
from fastapi.testclient import TestClient

ORIGIN = "https://folio.test"
REFRESH = "refresh-token-secret"
DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"drive-{uuid.uuid4().hex}@example.com"
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
def other() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr("app.main.open_storage", lambda: fake)
    return fake


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    monkeypatch.setattr("app.main.open_drive", lambda: fake)
    monkeypatch.setenv("FRONTEND_ORIGIN", ORIGIN)
    return fake


@pytest.fixture(autouse=True)
def kms(monkeypatch: pytest.MonkeyPatch) -> FakeKms:
    fake = FakeKms()
    monkeypatch.setattr("app.main.open_kms", lambda: fake)
    return fake


def _signed_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(public_app, follow_redirects=False)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def _connect(client: TestClient, code: str = "good-code"):
    started = client.get("/integrations/drive/connect")
    assert started.status_code == 302
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    return client.get("/integrations/drive/callback", params={"code": code, "state": state})


def _stored(account_id: uuid.UUID):
    with connect() as connection:
        return load_integration(connection, account_id, GOOGLE_DRIVE)


def test_every_route_needs_a_session(drive, storage) -> None:
    client = TestClient(public_app, follow_redirects=False)

    assert client.get("/integrations").status_code == 401
    assert client.get("/integrations/drive/connect").status_code == 401
    assert client.get("/integrations/drive/token").status_code == 401
    assert client.post("/integrations/drive/import", json={"file_id": "abcde"}).status_code == 401
    assert client.delete("/integrations/drive").status_code == 401


def test_the_callback_without_a_session_goes_to_failed(account, monkeypatch, drive) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    started = client.get("/integrations/drive/connect")
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    stranger = TestClient(public_app, follow_redirects=False)
    stranger.cookies.set("drive_state", state)

    response = stranger.get(
        "/integrations/drive/callback", params={"code": "good-code", "state": state}
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"{ORIGIN}/?drive=failed"


def test_nothing_is_connected_at_first_and_the_picker_values_are_public(
    account, monkeypatch, drive
) -> None:
    _, email = account
    monkeypatch.setenv("DRIVE_PICKER_API_KEY", "picker-key")
    monkeypatch.setenv("DRIVE_APP_ID", "1234")
    client = _signed_in(monkeypatch, email)

    response = client.get("/integrations")

    assert response.status_code == 200
    assert response.json() == {
        "drive": {
            "connected": False,
            "label": None,
            "picker_api_key": "picker-key",
            "app_id": "1234",
        }
    }


def test_connect_goes_to_google_with_a_state_cookie_that_script_cannot_read(
    account, monkeypatch, drive
) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)

    response = client.get("/integrations/drive/connect")

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.invalid/consent")
    cookie = response.headers["set-cookie"].lower()
    assert "drive_state=" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_the_consent_page_asks_for_files_the_person_picks_and_nothing_else() -> None:
    from app.google_drive import HttpDrive

    url = HttpDrive("the-id", "the-secret", f"{ORIGIN}/api/integrations/drive/callback")
    query = parse_qs(urlsplit(url.consent_url("abc")).query)

    assert query["scope"] == ["openid email https://www.googleapis.com/auth/drive.file"]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["redirect_uri"] == [f"{ORIGIN}/api/integrations/drive/callback"]
    assert query["state"] == ["abc"] and "the-secret" not in url.consent_url("abc")


def test_a_connection_stores_the_refresh_token_encrypted(account, monkeypatch, drive, kms) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)

    done = _connect(client)

    assert done.status_code == 302 and done.headers["location"] == f"{ORIGIN}/?drive=connected"
    cleared = done.headers["set-cookie"]
    assert "drive_state=" in cleared and "Max-Age=0" in cleared
    secret, label = _stored(account_id)
    assert REFRESH.encode() not in secret and label == "person@example.com"
    assert kms.decrypt(secret, account_id.bytes) == REFRESH.encode()
    shown = client.get("/integrations").json()["drive"]
    assert shown["connected"] is True and shown["label"] == "person@example.com"


def test_connecting_again_replaces_the_row(account, monkeypatch, drive) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.email = "second@example.com"

    _connect(client)

    assert _stored(account_id)[1] == "second@example.com"
    with connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM integration WHERE account_id = %s", (account_id,)
        ).fetchone()[0]
    assert count == 1


def test_a_wrong_state_or_a_refusal_stores_nothing(account, monkeypatch, drive) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    client.get("/integrations/drive/connect")
    failed = f"{ORIGIN}/?drive=failed"

    wrong = client.get(
        "/integrations/drive/callback", params={"code": "good-code", "state": "not-it"}
    )
    denied = client.get("/integrations/drive/callback", params={"error": "access_denied"})
    no_cookie = TestClient(public_app, follow_redirects=False)
    no_cookie.cookies.set("session", client.cookies["session"])
    missing = no_cookie.get(
        "/integrations/drive/callback", params={"code": "good-code", "state": "x"}
    )

    for response in (wrong, denied, missing):
        assert response.status_code == 302 and response.headers["location"] == failed
    assert _stored(account_id) is None


def test_a_code_google_refuses_goes_to_failed(account, monkeypatch, drive) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)

    response = _connect(client, code="bad-code")

    assert response.headers["location"] == f"{ORIGIN}/?drive=failed"
    assert _stored(account_id) is None


def test_the_token_and_import_need_a_connection(account, monkeypatch, drive, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)

    token = client.get("/integrations/drive/token")
    imported = client.post("/integrations/drive/import", json={"file_id": "abcdefgh"})

    assert token.status_code == 409 and token.json() == {"error": NOT_CONNECTED}
    assert imported.status_code == 409 and imported.json() == {"error": NOT_CONNECTED}


def test_the_token_is_minted_from_the_stored_refresh_token(account, monkeypatch, drive) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)

    response = client.get("/integrations/drive/token")

    assert response.status_code == 200
    assert response.json() == {"access_token": "access-token-secret", "expires_in": 3599}
    assert drive.refreshed == [REFRESH]


def test_a_permission_google_took_back_asks_to_connect_again(account, monkeypatch, drive) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.refresh_ok = False

    response = client.get("/integrations/drive/token")

    assert response.status_code == 409 and response.json() == {"error": NOT_CONNECTED}


def test_an_allowed_file_is_copied_and_kept_out_of_the_byte_count(
    account, monkeypatch, drive, storage
) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("file-one", "plan.pdf", "application/pdf", b"%PDF data")

    response = client.post("/integrations/drive/import", json={"file_id": "file-one"})

    assert response.status_code == 201
    item = response.json()
    assert item["kind"] == "drive_copy" and item["name"] == "plan.pdf"
    assert item["content_type"] == "application/pdf" and item["byte_size"] == 9
    assert storage.objects[f"accounts/{account_id}/files/{item['id']}/plan.pdf"] == b"%PDF data"
    with connect() as connection:
        assert byte_count(connection, account_id) == 0
    assert [f["id"] for f in client.get("/files").json()["files"]] == [item["id"]]


def test_google_files_are_exported(account, monkeypatch, drive, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("doc-aaaa", "Notes", DOC, b"pdf bytes")
    drive.add("sheet-aa", "Budget", SHEET, b"xlsx bytes")
    drive.add("deck-aaa", "Talk.pdf", SLIDES, b"deck bytes")

    doc = client.post("/integrations/drive/import", json={"file_id": "doc-aaaa"}).json()
    sheet = client.post("/integrations/drive/import", json={"file_id": "sheet-aa"}).json()
    deck = client.post("/integrations/drive/import", json={"file_id": "deck-aaa"}).json()

    assert (doc["name"], doc["content_type"]) == ("Notes.pdf", "application/pdf")
    assert sheet["name"] == "Budget.xlsx" and sheet["content_type"].endswith("spreadsheetml.sheet")
    assert deck["name"] == "Talk.pdf"


def test_the_same_file_can_be_imported_twice(account, monkeypatch, drive, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("file-one", "plan.pdf", "application/pdf", b"data")

    first = client.post("/integrations/drive/import", json={"file_id": "file-one"}).json()
    second = client.post("/integrations/drive/import", json={"file_id": "file-one"}).json()

    assert first["id"] != second["id"]


def test_a_file_that_is_not_allowed_is_refused(account, monkeypatch, drive, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("prog-aaa", "setup.exe", "application/octet-stream", b"MZ")
    drive.add("form-aaa", "Survey", "application/vnd.google-apps.form", b"x")
    drive.add("none-aaa", "data", "application/octet-stream", b"x")

    for file_id in ("prog-aaa", "form-aaa", "none-aaa"):
        response = client.post("/integrations/drive/import", json={"file_id": file_id})
        assert response.status_code == 400 and response.json() == {"error": BAD_FILE_TYPE}
    assert storage.objects == {}


def test_a_file_without_an_ending_is_named_by_its_type(
    account, monkeypatch, drive, storage
) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("scan-aaa", "Scan", "image/png", b"png")

    response = client.post("/integrations/drive/import", json={"file_id": "scan-aaa"})

    assert response.status_code == 201 and response.json()["name"] == "Scan.png"


def test_a_file_over_50_mb_is_refused(account, monkeypatch, drive, storage) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.files["big-aaaa"] = (
        DriveFile("big-aaaa", "big.pdf", "application/pdf", 60 * 2**20),
        b"x",
    )
    # An export has no size up front, so the stream itself is counted.
    drive.add("long-aaa", "Long", DOC, b"x" * (MAX_UPLOAD_BYTES + 1))

    declared = client.post("/integrations/drive/import", json={"file_id": "big-aaaa"})
    streamed = client.post("/integrations/drive/import", json={"file_id": "long-aaa"})

    for response in (declared, streamed):
        assert response.status_code == 400 and response.json() == {"error": TOO_BIG}
    assert storage.objects == {}


def test_a_file_google_will_not_give_is_refused_in_plain_words(
    account, monkeypatch, drive, storage
) -> None:
    _, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)

    unknown = client.post("/integrations/drive/import", json={"file_id": "gone-aaaa"})

    assert unknown.status_code == 400 and unknown.json() == {"error": CANNOT_OPEN}
    for body in (None, {}, {"file_id": 5}, {"file_id": "../../etc"}, {"file_id": "a b c d e"}):
        response = client.post("/integrations/drive/import", json=body)
        assert response.status_code == 400


def test_disconnecting_removes_the_row_and_keeps_the_copies(
    account, monkeypatch, drive, storage
) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("file-one", "plan.pdf", "application/pdf", b"data")
    item = client.post("/integrations/drive/import", json={"file_id": "file-one"}).json()

    response = client.delete("/integrations/drive")

    assert response.status_code == 204
    assert _stored(account_id) is None and drive.revoked == [REFRESH]
    assert client.get("/integrations").json()["drive"]["connected"] is False
    assert [f["id"] for f in client.get("/files").json()["files"]] == [item["id"]]
    assert storage.has(f"accounts/{account_id}/files/{item['id']}/plan.pdf")
    assert client.delete("/integrations/drive").status_code == 204


def test_disconnecting_works_when_google_will_not_revoke(account, monkeypatch, drive) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.revoke_ok = False

    response = client.delete("/integrations/drive")

    assert response.status_code == 204 and _stored(account_id) is None


def test_another_account_never_sees_or_uses_a_connection(
    account, other, monkeypatch, drive, storage
) -> None:
    account_id, email = account
    _, other_email = other
    mine = _signed_in(monkeypatch, email)
    _connect(mine)
    drive.add("file-one", "plan.pdf", "application/pdf", b"data")
    copy = mine.post("/integrations/drive/import", json={"file_id": "file-one"}).json()
    theirs = _signed_in(monkeypatch, other_email)

    assert theirs.get("/integrations").json()["drive"]["connected"] is False
    assert theirs.get("/integrations/drive/token").status_code == 409
    stolen = theirs.post("/integrations/drive/import", json={"file_id": "file-one"})
    assert stolen.status_code == 409
    assert theirs.get("/files").json()["files"] == []
    assert theirs.get(f"/files/{copy['id']}/download").status_code == 404
    assert theirs.delete("/integrations/drive").status_code == 204
    assert _stored(account_id) is not None


def test_a_secret_copied_to_another_account_does_not_open(
    account, other, monkeypatch, drive
) -> None:
    account_id, email = account
    other_id, other_email = other
    mine = _signed_in(monkeypatch, email)
    _connect(mine)
    secret, label = _stored(account_id)
    with connect() as connection:
        connection.execute(
            "INSERT INTO integration (account_id, provider, secret, label) "
            "VALUES (%s, 'google_drive', %s, %s)",
            (other_id, secret, label),
        )
    theirs = _signed_in(monkeypatch, other_email)

    assert theirs.get("/integrations/drive/token").status_code == 503


def test_no_secret_appears_in_any_response(account, monkeypatch, drive, storage, kms) -> None:
    account_id, email = account
    monkeypatch.setenv("DRIVE_CLIENT_SECRET", "client-secret-value")
    client = _signed_in(monkeypatch, email)
    drive.add("file-one", "plan.pdf", "application/pdf", b"data")
    responses = [
        client.get("/integrations/drive/connect"),
        _connect(client),
        client.get("/integrations"),
        client.post("/integrations/drive/import", json={"file_id": "file-one"}),
        client.get("/files"),
        client.delete("/integrations/drive"),
    ]
    stored = kms.encrypt(REFRESH.encode(), account_id.bytes)

    for response in responses:
        text = response.text + str(dict(response.headers))
        assert REFRESH not in text and "client-secret-value" not in text
        assert stored.decode(errors="ignore") not in text


def test_deleting_the_account_removes_the_row_and_the_copies(
    account, monkeypatch, drive, storage
) -> None:
    account_id, email = account
    client = _signed_in(monkeypatch, email)
    _connect(client)
    drive.add("file-one", "plan.pdf", "application/pdf", b"data")
    client.post("/integrations/drive/import", json={"file_id": "file-one"})
    monkeypatch.setattr("app.main.delete_firebase_user", lambda uid: None)

    assert client.delete("/account").status_code == 204

    assert _stored(account_id) is None
    assert not [path for path in storage.objects if path.startswith(f"accounts/{account_id}/")]
