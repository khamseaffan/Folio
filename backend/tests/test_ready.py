from app.db import connect
from app.main import app
from fastapi.testclient import TestClient


def test_ready_reports_ok():
    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_postgres_server_version_starts_with_18():
    with connect() as connection:
        row = connection.execute("SHOW server_version").fetchone()

    assert row is not None
    version = row[0]
    assert isinstance(version, str)
    assert version.startswith("18")


def test_ready_reports_down_when_database_port_is_closed(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://folio:folio@127.0.0.1:5433/folio",
    )
    client = TestClient(app)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "down"}
