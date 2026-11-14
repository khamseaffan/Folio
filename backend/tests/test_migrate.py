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


def test_a_write_does_not_wait_on_another_open_write():
    import uuid

    from app.account import ensure_account
    from app.message import insert_message
    from app.thread import create_thread
    from app.usage import record_usage

    run()
    email = f"setup-{uuid.uuid4().hex}@example.com"
    account_id = ensure_account(f"local:{email}", email)
    try:
        with connect() as setup:
            thread = create_thread(setup, account_id)
        with connect() as first, connect() as second:
            # The first writer holds its row locks until it commits.
            assert insert_message(first, account_id, thread[0], "person", "chat", "hi") is not None
            second.execute("SET lock_timeout = '500ms'")
            row = record_usage(second, account_id, email, "chat", "openai", "m", 1, 1, 2)
            assert row is not None
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
            connection.execute("DELETE FROM usage WHERE email = %s", (email,))
            connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


def test_setup_is_skipped_when_the_table_exists():
    from app import account, email_limit, message, session, thread, usage

    run()

    class _Spy:
        def __init__(self, connection) -> None:
            self._connection = connection
            self.statements: list[str] = []

        def execute(self, query, params=None):
            self.statements.append(str(query))
            return self._connection.execute(query, params)

    with connect() as connection:
        spy = _Spy(connection)
        for module in (email_limit, account, session, thread, message, usage):
            module.apply(spy)
    assert not [text for text in spy.statements if "CREATE" in text.upper()]
