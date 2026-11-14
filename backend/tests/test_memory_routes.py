import uuid

import pytest
from app.ability import create_ability
from app.db import connect
from app.knowledge_file import put_file
from app.main import app
from app.session import apply as apply_session
from app.sign_in_proof import VerifiedSignIn
from fastapi.testclient import TestClient


def _email(label: str) -> str:
    return f"{label}.{uuid.uuid4()}@Example.com"


def _remove(email: str) -> None:
    with connect() as connection:
        # On a new database nothing has created the tables yet.
        apply_session(connection)
        connection.execute("DELETE FROM account WHERE email = %s", (email.lower(),))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email.lower(),))


def _account_id(email: str) -> uuid.UUID:
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s", (email.lower(),)
        ).fetchone()
    assert row is not None
    return row[0]


def _sign_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(app)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


_PATHS = ("/knowledge", f"/knowledge/{uuid.uuid4()}", "/abilities")


def test_knowledge_and_abilities_need_a_session() -> None:
    client = TestClient(app)
    for path in _PATHS:
        response = client.get(path)
        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}


def test_routes_list_and_read_only_the_callers_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    mine, theirs = _email("MemoryRoutes"), _email("MemoryOther")
    _remove(mine)
    _remove(theirs)
    try:
        client = _sign_in(monkeypatch, mine)
        stranger = _sign_in(monkeypatch, theirs)
        with connect() as connection:
            account_id = _account_id(mine)
            profile = put_file(connection, account_id, "profile.md", "Answer briefly.")
            put_file(connection, account_id, "a.md", "hello")
            create_ability(connection, account_id, "Tone", "Be brief.")
            other_file = put_file(connection, _account_id(theirs), "private.md", "secret")
        listed = client.get("/knowledge")
        assert listed.status_code == 200
        assert [item["name"] for item in listed.json()] == ["a.md", "profile.md"]
        assert set(listed.json()[0]) == {"id", "name", "byte_size", "updated_at"}
        opened = client.get(f"/knowledge/{profile[0]}")
        assert opened.status_code == 200
        assert opened.json()["body"] == "Answer briefly."
        assert set(opened.json()) == {"id", "name", "body", "byte_size", "updated_at"}
        for missing in (str(other_file[0]), str(uuid.uuid4()), "not-an-id"):
            refused = client.get(f"/knowledge/{missing}")
            assert refused.status_code == 404
            assert refused.json() == {"error": "Not found"}
        abilities = client.get("/abilities")
        assert abilities.status_code == 200
        assert [(item["name"], item["body"]) for item in abilities.json()] == [
            ("Tone", "Be brief.")
        ]
        assert set(abilities.json()[0]) == {"id", "name", "body", "updated_at"}
        assert stranger.get("/abilities").json() == []
        assert [item["name"] for item in stranger.get("/knowledge").json()] == ["private.md"]
    finally:
        _remove(mine)
        _remove(theirs)


def test_there_is_no_route_that_writes_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("MemoryWrite")
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        for path in _PATHS:
            for method in ("post", "put", "patch", "delete"):
                response = getattr(client, method)(path)
                assert response.status_code in (404, 405)
    finally:
        _remove(email)
