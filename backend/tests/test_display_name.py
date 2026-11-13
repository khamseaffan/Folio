import uuid

import psycopg
import pytest
from app.db import connect
from app.main import app
from app.session import open_session
from app.sign_in_proof import VerifiedSignIn
from fastapi.testclient import TestClient


def _email(label: str) -> str:
    return f"{label}.{uuid.uuid4()}@Example.com"


def _remove(email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        session = connection.execute("SELECT to_regclass('public.session')").fetchone()
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
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
            connection.execute(
                "DELETE FROM account WHERE email = %s",
                (stored,),
            )
        if limit is not None and limit[0] is not None:
            connection.execute(
                "DELETE FROM email_limit WHERE email = %s",
                (stored,),
            )


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


def _account_state(email: str, session_id: str) -> tuple[str | None, object, object]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT account.display_name, session.last_active_at, session.ended_at
            FROM session
            JOIN account ON account.id = session.account_id
            WHERE account.email = %s
              AND session.id = %s
            """,
            (email, session_id),
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def test_one_and_forty_character_names_are_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Named")
    stored = email.lower()
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        session_id = client.cookies["session"]
        with connect() as connection:
            connection.execute(
                """
                UPDATE session
                SET last_active_at = now() - interval '1 minute'
                WHERE id = %s
                """,
                (session_id,),
            )
        before = _account_state(stored, session_id)

        short = client.post("/display-name", json={"display_name": "A"})
        short_session = client.get("/session")
        assert short.status_code == 200
        assert short.json() == {"email": stored, "display_name": "A"}
        assert short_session.status_code == 200
        assert short_session.json() == {"email": stored, "display_name": "A"}
        assert _account_state(stored, session_id)[0] == "A"

        wide = "B" * 40
        named = client.post("/display-name", json={"display_name": wide})
        named_session = client.get("/session")
        after = _account_state(stored, session_id)
        assert named.status_code == 200
        assert named.json() == {"email": stored, "display_name": wide}
        assert named_session.status_code == 200
        assert named_session.json() == {"email": stored, "display_name": wide}
        assert after[0] == wide
        assert after[1] > before[1]
        assert after[2] is None
    finally:
        _remove(email)


def test_surrounding_spaces_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Spaced")
    stored = email.lower()
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        response = client.post("/display-name", json={"display_name": "  Ada  "})
        current = client.get("/session")
        wide = "D" * 40
        padded = client.post("/display-name", json={"display_name": f"  {wide}  "})

        assert response.status_code == 200
        assert response.json() == {"email": stored, "display_name": "Ada"}
        assert current.status_code == 200
        assert current.json() == {"email": stored, "display_name": "Ada"}
        assert padded.status_code == 200
        assert padded.json() == {"email": stored, "display_name": wide}
        assert _account_state(stored, client.cookies["session"])[0] == wide
    finally:
        _remove(email)


def test_invalid_display_name_leaves_it_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Invalid.Name")
    stored = email.lower()
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        session_id = client.cookies["session"]
        before = _account_state(stored, session_id)
        refused = [
            {"display_name": ""},
            {"display_name": "   "},
            {"display_name": "C" * 41},
            {},
            {"display_name": 7},
        ]

        for body in refused:
            response = client.post("/display-name", json=body)
            assert response.status_code == 400
            assert response.json() == {"error": "Enter a name, up to 40 characters."}
            assert _account_state(stored, session_id) == before
    finally:
        _remove(email)


def test_missing_cookie_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("No.Cookie")
    stored = email.lower()
    _remove(email)
    try:
        signed_in = _sign_in(monkeypatch, email)
        session_id = signed_in.cookies["session"]
        before = _account_state(stored, session_id)

        response = TestClient(app).post("/display-name", json={"display_name": "Ada"})

        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}
        assert _account_state(stored, session_id) == before
    finally:
        _remove(email)


def test_closed_session_at_save_rolls_the_name_back(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Closed.Session")
    stored = email.lower()
    _remove(email)
    try:
        client = _sign_in(monkeypatch, email)
        session_id = client.cookies["session"]
        named = client.post("/display-name", json={"display_name": "Ada"})
        assert named.status_code == 200

        def open_then_end(raw_id: uuid.UUID) -> uuid.UUID | None:
            account_id = open_session(raw_id)
            with connect() as connection:
                connection.execute(
                    """
                    UPDATE session
                    SET ended_at = now()
                    WHERE id = %s
                    """,
                    (raw_id,),
                )
            return account_id

        monkeypatch.setattr("app.main.open_session", open_then_end)
        before = _account_state(stored, session_id)
        response = client.post("/display-name", json={"display_name": "Bea"})
        after = _account_state(stored, session_id)

        assert response.status_code == 401
        assert response.json() == {"error": "Sign in to continue."}
        assert before[0] == "Ada"
        assert after[0] == "Ada"
        assert after[1] == before[1]
    finally:
        _remove(email)


def test_database_error_reports_down(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Name.Down")
    stored = email.lower()
    _remove(email)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("folio-db-marker")

    try:
        client = _sign_in(monkeypatch, email)
        session_id = client.cookies["session"]
        before = _account_state(stored, session_id)
        monkeypatch.setattr("app.db.psycopg.connect", fail)
        response = client.post("/display-name", json={"display_name": "Ada"})

        assert response.status_code == 503
        assert response.json() == {"status": "down"}
        assert "folio-db-marker" not in response.text
        assert "psycopg" not in response.text.lower()
        monkeypatch.undo()
        assert _account_state(stored, session_id) == before
        with connect() as connection:
            row = connection.execute("SELECT 1").fetchone()
        assert row == (1,)
    finally:
        monkeypatch.undo()
        _remove(email)
