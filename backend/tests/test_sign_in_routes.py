import uuid

import psycopg
import pytest
from app.db import connect
from app.main import app
from app.sign_in_proof import NOT_PROVEN, SignInError, VerifiedSignIn
from fastapi.testclient import TestClient
from httpx import Response


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


def _counts() -> tuple[int, int]:
    with connect() as connection:
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
        accounts = 0
        limits = 0
        if account is not None and account[0] is not None:
            row = connection.execute("SELECT count(*) FROM account").fetchone()
            accounts = 0 if row is None else int(row[0])
        if limit is not None and limit[0] is not None:
            row = connection.execute("SELECT count(*) FROM email_limit").fetchone()
            limits = 0 if row is None else int(row[0])
    return accounts, limits


def _allow(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        assert id_token == "token"
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)


def _refuse(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    def verify(_id_token: str) -> VerifiedSignIn:
        raise SignInError(message)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)


def _cookie_header(response: Response) -> str:
    return ", ".join(response.headers.get_list("set-cookie"))


def test_session_cookie_is_secure_behind_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Proxied.SignIn")
    _allow(monkeypatch, email)
    _remove(email)
    try:
        proxied = TestClient(app).post(
            "/sign-in",
            json={"id_token": "token"},
            headers={"X-Forwarded-Proto": "https"},
        )
        plain = TestClient(app).post("/sign-in", json={"id_token": "token"})

        assert proxied.status_code == 200
        assert "Secure" in _cookie_header(proxied)
        assert "Secure" not in _cookie_header(plain)
    finally:
        _remove(email)


def test_allowed_sign_in_sets_the_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Allowed.SignIn")
    stored = email.lower()
    _allow(monkeypatch, email)
    _remove(email)
    try:
        response = TestClient(app).post("/sign-in", json={"id_token": "token"})
        secure = TestClient(app, base_url="https://testserver").post(
            "/sign-in",
            json={"id_token": "token"},
        )

        assert response.status_code == 200
        assert response.json() == {"email": stored, "display_name": None}
        assert stored == email.lower()
        assert stored != email
        header = _cookie_header(response)
        assert "HttpOnly" in header
        assert "SameSite=Lax" in header
        assert "Path=/" in header
        assert "Secure" not in header
        secure_header = _cookie_header(secure)
        assert "HttpOnly" in secure_header
        assert "SameSite=Lax" in secure_header
        assert "Secure" in secure_header
        cookie = response.cookies["session"]
        with connect() as connection:
            row = connection.execute(
                """
                SELECT session.id, session.ended_at
                FROM session
                JOIN account ON account.id = session.account_id
                WHERE session.id = %s
                """,
                (cookie,),
            ).fetchone()
        assert row is not None
        assert str(row[0]) == cookie
        assert row[1] is None
    finally:
        _remove(email)


def test_disallowed_email_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Refused")
    _refuse(monkeypatch, "This email is not an allowed account.")
    _remove(email)
    before = _counts()
    try:
        response = TestClient(app).post("/sign-in", json={"id_token": "token"})

        assert response.status_code == 403
        assert response.json() == {"error": "This email is not an allowed account."}
        assert response.headers.get_list("set-cookie") == []
        assert "session" not in response.cookies
        assert _counts() == before
    finally:
        _remove(email)


def test_failed_proof_does_not_insert_an_account(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Unproven")
    _refuse(monkeypatch, NOT_PROVEN)
    _remove(email)
    before = _counts()
    try:
        response = TestClient(app).post("/sign-in", json={"id_token": "token"})

        assert response.status_code == 401
        assert response.json() == {"error": "This sign-in could not be verified."}
        assert response.headers.get_list("set-cookie") == []
        assert _counts() == before
    finally:
        _remove(email)


def test_missing_id_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid(_id_token: str) -> VerifiedSignIn:
        raise AssertionError("verify should not run")

    monkeypatch.setattr("app.main.verify_allowed_sign_in", forbid)
    client = TestClient(app)

    missing = client.post("/sign-in", json={})
    empty = client.post("/sign-in", json={"id_token": ""})

    assert missing.status_code == 401
    assert missing.json() == {"error": "This sign-in could not be verified."}
    assert empty.status_code == 401
    assert empty.json() == {"error": "This sign-in could not be verified."}


def test_third_sign_in_ends_the_first_session(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Third.SignIn")
    stored = email.lower()
    _allow(monkeypatch, email)
    _remove(email)
    client = TestClient(app)
    try:
        first = client.post("/sign-in", json={"id_token": "token"})
        second = client.post("/sign-in", json={"id_token": "token"})
        third = client.post("/sign-in", json={"id_token": "token"})
        first_id = uuid.UUID(first.cookies["session"])
        second_id = uuid.UUID(second.cookies["session"])
        third_id = uuid.UUID(third.cookies["session"])

        with connect() as connection:
            account = connection.execute(
                "SELECT id FROM account WHERE email = %s",
                (stored,),
            ).fetchone()
            assert account is not None
            open_rows = connection.execute(
                """
                SELECT id
                FROM session
                WHERE account_id = %s
                  AND ended_at IS NULL
                """,
                (account[0],),
            ).fetchall()
            ended = connection.execute(
                "SELECT ended_at FROM session WHERE id = %s",
                (first_id,),
            ).fetchone()
        assert ended is not None
        assert ended[0] is not None
        assert {row[0] for row in open_rows} == {second_id, third_id}
        assert len(open_rows) == 2
    finally:
        _remove(email)


def test_sign_out_ends_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("SignOut")
    _allow(monkeypatch, email)
    _remove(email)
    client = TestClient(app)
    try:
        signed_in = client.post("/sign-in", json={"id_token": "token"})
        session_id = signed_in.cookies["session"]
        signed_out = client.post("/sign-out")

        assert signed_out.status_code == 204
        assert signed_out.content == b""
        with connect() as connection:
            row = connection.execute(
                "SELECT ended_at FROM session WHERE id = %s",
                (session_id,),
            ).fetchone()
        assert row is not None
        assert row[0] is not None
        current = client.get("/session")
        assert current.status_code == 401
        assert current.json() == {"error": "Sign in to continue."}

        client.cookies.set("session", session_id)
        again = client.post("/sign-out")
        assert again.status_code == 204
        assert "session=" in _cookie_header(again)
    finally:
        _remove(email)


def test_get_session_returns_the_email(monkeypatch: pytest.MonkeyPatch) -> None:
    email = _email("Session.Read")
    stored = email.lower()
    _allow(monkeypatch, email)
    _remove(email)
    client = TestClient(app)
    try:
        signed_in = client.post("/sign-in", json={"id_token": "token"})
        session_id = signed_in.cookies["session"]
        with connect() as connection:
            before = connection.execute(
                "SELECT last_active_at FROM session WHERE id = %s",
                (session_id,),
            ).fetchone()
        current = client.get("/session")
        assert current.status_code == 200
        assert current.json() == {"email": stored, "display_name": None}

        with connect() as connection:
            connection.execute(
                """
                UPDATE account
                SET display_name = %s
                WHERE email = %s
                """,
                ("Ada", stored),
            )
        named = client.get("/session")
        again = client.post("/sign-in", json={"id_token": "token"})
        with connect() as connection:
            after = connection.execute(
                "SELECT last_active_at FROM session WHERE id = %s",
                (session_id,),
            ).fetchone()
        assert named.status_code == 200
        assert named.json() == {"email": stored, "display_name": "Ada"}
        assert again.status_code == 200
        assert again.json() == {"email": stored, "display_name": "Ada"}
        assert before is not None
        assert after == before
    finally:
        _remove(email)


def test_sign_in_reports_down_when_the_database_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = _email("Database.Down")
    _allow(monkeypatch, email)
    _remove(email)
    before = _counts()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise psycopg.OperationalError("folio-db-marker")

    monkeypatch.setattr("app.db.psycopg.connect", fail)
    try:
        response = TestClient(app).post("/sign-in", json={"id_token": "token"})

        assert response.status_code == 503
        assert response.json() == {"status": "down"}
        assert "folio-db-marker" not in response.text
        assert "psycopg" not in response.text.lower()
        assert response.headers.get_list("set-cookie") == []
        assert "session" not in response.cookies
    finally:
        monkeypatch.undo()
        _remove(email)
    assert _counts() == before
    with connect() as connection:
        row = connection.execute("SELECT 1").fetchone()
    assert row == (1,)


def test_localhost_and_configured_origin_can_call_with_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    preflight = client.options(
        "/sign-in",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert "content-type" in preflight.headers["access-control-allow-headers"].lower()
    assert preflight.headers["access-control-allow-origin"] != "*"

    configured = "http://127.0.0.1:3000"
    monkeypatch.setenv("FRONTEND_ORIGIN", configured)
    allowed = client.options(
        "/session",
        headers={
            "Origin": configured,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == configured
    assert allowed.headers["access-control-allow-credentials"] == "true"

    monkeypatch.setenv("FRONTEND_ORIGIN", "*")
    blocked = client.get("/health", headers={"Origin": "http://evil.example"})
    assert blocked.status_code == 200
    assert blocked.json() == {"status": "ok"}
    assert blocked.headers.get("access-control-allow-origin") != "http://evil.example"
    assert blocked.headers.get("access-control-allow-origin") != "*"
