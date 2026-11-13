from app.db import connect
from app.migrate import run

TABLES = {"account", "email_limit", "session", "thread", "message", "usage"}


def _present() -> set[str]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {row[0] for row in rows}


def test_run_creates_every_table_and_is_repeatable():
    run()
    run()

    assert TABLES <= _present()
