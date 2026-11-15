import uuid
from collections.abc import Iterator

import pytest
from app.account import ensure_account
from app.db import connect
from app.research import _ActivityLog
from app.run import create_run
from app.run_activity import list_activity
from app.thread import create_thread


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    email = f"research-activity-{uuid.uuid4().hex}@example.com"
    account_id = ensure_account(f"local:{email}", email)
    yield account_id, email
    with connect() as connection:
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


def _run_for(account_id):
    with connect() as connection:
        thread = create_thread(connection, account_id)
        run = create_run(connection, account_id, thread[0], None, "research", "question")
    assert run is not None
    return run[0]


def test_the_log_writes_each_line_so_it_can_be_read_while_the_run_works(account) -> None:
    account_id, _ = account
    run_id = _run_for(account_id)
    log = _ActivityLog(account_id, run_id)

    log.searched("heat pumps")
    log.read("Title A", "https://a.example/heat")
    log.read("notes.md", None)
    log.skipped("https://b.example/pdf")

    with connect() as connection:
        lines = list_activity(connection, account_id, run_id)
    assert [(line[1], line[2], line[3]) for line in lines] == [
        ("search", "heat pumps", None),
        ("read", "Title A", "https://a.example/heat"),
        ("read", "notes.md", None),
        ("skipped", "https://b.example/pdf", None),
    ]


def test_a_line_that_cannot_be_written_is_dropped_and_never_raises(account) -> None:
    account_id, _ = account
    # The run does not exist, as when the account is deleted while the run works.
    log = _ActivityLog(account_id, uuid.uuid4())

    log.searched("words")
    log.read("Title", "https://a.example/x")
    log.skipped("https://a.example/y")
