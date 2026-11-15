import uuid
from collections.abc import Iterator

import pytest
from app.db import connect
from app.main import app
from app.report import create_report, download_filename, render_download
from app.report_source import NewSource, list_sources
from app.run import create_run
from app.sign_in_proof import VerifiedSignIn
from app.thread import create_thread
from fastapi.testclient import TestClient


def _email(label: str) -> str:
    return f"{label}.{uuid.uuid4()}@Example.com"


def _remove(email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        connection.execute(
            "DELETE FROM report WHERE account_id IN (SELECT id FROM account WHERE email = %s)",
            (stored,),
        )
        connection.execute("DELETE FROM account WHERE email = %s", (stored,))
        connection.execute("DELETE FROM usage WHERE email = %s", (stored,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (stored,))


def _sign_in(monkeypatch: pytest.MonkeyPatch, email: str) -> TestClient:
    uid = str(uuid.uuid4())

    def verify(id_token: str) -> VerifiedSignIn:
        return VerifiedSignIn(firebase_uid=uid, email=email)

    monkeypatch.setattr("app.main.verify_allowed_sign_in", verify)
    client = TestClient(app)
    assert client.post("/sign-in", json={"id_token": "token"}).status_code == 200
    return client


def _account_id(email: str) -> uuid.UUID:
    with connect() as connection:
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s", (email.lower(),)
        ).fetchone()
    assert row is not None
    return row[0]


def _make_report(email: str, name: str = "Heat pumps"):
    account_id = _account_id(email)
    with connect() as connection:
        thread = create_thread(connection, account_id)
        run = create_run(connection, account_id, thread[0], None, "research", "q")
        assert run is not None
        report = create_report(
            connection,
            account_id,
            run[0],
            thread[0],
            name,
            "Which is better?",
            "The body.",
            "It stopped.",
            [
                NewSource("page", "A page", "https://example.com/a", "a fact"),
                NewSource("file", "notes.md", None, "background"),
            ],
        )
    assert report is not None
    return report


@pytest.fixture
def email() -> Iterator[str]:
    value = _email("report-routes")
    yield value
    _remove(value)


@pytest.fixture
def other() -> Iterator[str]:
    value = _email("report-routes-other")
    yield value
    _remove(value)


def test_the_routes_need_a_session():
    client = TestClient(app)
    some = uuid.uuid4()

    for path in ("/reports", f"/reports/{some}", f"/reports/{some}/download"):
        assert client.get(path).status_code == 401


def test_the_list_holds_only_the_callers_reports_newest_first(monkeypatch, email, other):
    client = _sign_in(monkeypatch, email)
    first = _make_report(email, "First")
    second = _make_report(email, "Second")
    with connect() as connection:
        connection.execute(
            "UPDATE report SET created_at = now() - interval '1 hour' WHERE id = %s", (first[0],)
        )
    other_client = _sign_in(monkeypatch, other)
    theirs = _make_report(other, "Theirs")

    listed = client.get("/reports")

    assert listed.status_code == 200
    body = listed.json()
    assert [item["id"] for item in body] == [str(second[0]), str(first[0])]
    assert set(body[0]) == {"id", "name", "question", "created_at"}
    assert body[0]["name"] == "Second" and body[0]["question"] == "Which is better?"
    assert [item["id"] for item in other_client.get("/reports").json()] == [str(theirs[0])]


def test_one_report_with_its_sources(monkeypatch, email):
    client = _sign_in(monkeypatch, email)
    report = _make_report(email)

    response = client.get(f"/reports/{report[0]}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(report[0]),
        "name": "Heat pumps",
        "question": "Which is better?",
        "body": "The body.",
        "stop_note": "It stopped.",
        "created_at": report[11].isoformat(),
        "sources": [
            {
                "position": 1,
                "kind": "page",
                "title": "A page",
                "url": "https://example.com/a",
                "used_for": "a fact",
            },
            {
                "position": 2,
                "kind": "file",
                "title": "notes.md",
                "url": None,
                "used_for": "background",
            },
        ],
    }


def test_another_accounts_report_and_a_bad_id_are_404(monkeypatch, email, other):
    _sign_in(monkeypatch, email)
    report = _make_report(email)
    client = _sign_in(monkeypatch, other)

    for path in (
        f"/reports/{report[0]}",
        f"/reports/{report[0]}/download",
        f"/reports/{uuid.uuid4()}",
        "/reports/not-an-id",
        "/reports/not-an-id/download",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json() == {"error": "Not found"}


def test_the_download_is_the_rendered_markdown_with_a_safe_filename(monkeypatch, email):
    client = _sign_in(monkeypatch, email)
    report = _make_report(email, '../x "quoted"\nname')

    response = client.get(f"/reports/{report[0]}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    with connect() as connection:
        sources = list_sources(connection, _account_id(email), report[0])
    assert response.text == render_download(report, sources)
    assert len(response.content) == report[10]
    filename = download_filename(report[4])
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert ".." not in filename and "/" not in filename and '"' not in filename


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_route_writes_a_report(monkeypatch, email, method):
    client = _sign_in(monkeypatch, email)
    report = _make_report(email)

    for path in ("/reports", f"/reports/{report[0]}", f"/reports/{report[0]}/download"):
        response = getattr(client, method)(path)
        assert response.status_code in (404, 405), (method, path)

    kept = client.get(f"/reports/{report[0]}")
    assert kept.status_code == 200 and kept.json()["name"] == "Heat pumps"
