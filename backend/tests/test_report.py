import re
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.account import delete_account, ensure_account
from app.db import connect
from app.knowledge_file import delete_file, put_file
from app.report import apply as apply_report
from app.report import (
    create_report,
    delete_report,
    download_filename,
    get_report,
    list_reports,
    rename_report,
    render_download,
)
from app.report_source import NewSource, list_sources
from app.report_source import apply as apply_report_source
from app.run import create_run
from app.thread import create_thread, delete_thread


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"report-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        # A report restricts the delete of its run, so the account delete removes it first.
        connection.execute("DELETE FROM report WHERE account_id = %s", (account_id,))
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    with connect() as connection:
        apply_report_source(connection)
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


def _run(connection, account_id):
    thread = create_thread(connection, account_id)
    run = create_run(connection, account_id, thread[0], None, "research", "a question")
    assert run is not None
    return thread[0], run[0]


PAGE = NewSource("page", "A page", "https://example.com/a", "the first fact")
OTHER = NewSource("page", "Another", "https://example.com/b", "the second fact")


def _report(connection, account_id, name="Heat pumps", sources=(PAGE, OTHER)):
    thread_id, run_id = _run(connection, account_id)
    report = create_report(
        connection, account_id, run_id, thread_id, name, "Which is better?", "The body.",
        "It stopped.", sources,
    )  # fmt: skip
    assert report is not None
    return thread_id, run_id, report


def test_apply_is_repeatable(connection):
    apply_report(connection)
    apply_report(connection, force=True)
    apply_report_source(connection, force=True)


def test_create_then_get_list_rename_delete(connection, account):
    account_id, _ = account
    _, _, report = _report(connection, account_id)

    assert get_report(connection, account_id, report[0]) == report
    assert [row[0] for row in list_reports(connection, account_id)] == [report[0]]
    assert report[4] == "Heat pumps" and report[8] == "ready"
    assert report[9] == "text/markdown"

    renamed = rename_report(connection, account_id, report[0], "New name")
    assert renamed is not None and renamed[4] == "New name"

    assert delete_report(connection, account_id, report[0]) is True
    assert get_report(connection, account_id, report[0]) is None
    assert delete_report(connection, account_id, report[0]) is False
    assert list_reports(connection, account_id) == []


def test_list_is_newest_first(connection, account):
    account_id, _ = account
    _, _, first = _report(connection, account_id, "First")
    _, _, second = _report(connection, account_id, "Second")
    connection.execute(
        "UPDATE report SET created_at = now() - interval '1 hour' WHERE id = %s", (first[0],)
    )

    assert [row[0] for row in list_reports(connection, account_id)] == [second[0], first[0]]


def test_sources_are_numbered_from_one_and_byte_size_matches_the_download(connection, account):
    account_id, _ = account
    _, _, report = _report(connection, account_id)

    sources = list_sources(connection, account_id, report[0])
    assert [row[0] for row in sources] == [1, 2]
    assert [row[2] for row in sources] == ["A page", "Another"]
    assert sources[0] == (1, "page", "A page", "https://example.com/a", "the first fact", None)
    assert report[10] == len(render_download(report, sources).encode())


def test_a_second_report_for_the_same_run_is_not_stored(connection, account):
    account_id, _ = account
    thread_id, run_id, report = _report(connection, account_id)

    again = create_report(connection, account_id, run_id, thread_id, "Other", "q", "b", "n", [PAGE])

    assert again is None
    assert [row[0] for row in list_reports(connection, account_id)] == [report[0]]
    assert len(list_sources(connection, account_id, report[0])) == 2
    count = connection.execute(
        "SELECT count(*) FROM report_source WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert count == (2,)


def test_another_account_sees_nothing(connection, account):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        _, _, report = _report(connection, account_id)

        assert get_report(connection, other_id, report[0]) is None
        assert list_reports(connection, other_id) == []
        assert rename_report(connection, other_id, report[0], "Mine") is None
        assert delete_report(connection, other_id, report[0]) is False
        assert list_sources(connection, other_id, report[0]) == []
        kept = get_report(connection, account_id, report[0])
        assert kept is not None and kept[4] == "Heat pumps"
    finally:
        _remove(other_id, other_email)


def test_a_run_of_another_account_cannot_get_a_report(connection, account):
    account_id, _ = account
    other_id, other_email = _new_account()
    try:
        _, run_id = _run(connection, account_id)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            create_report(connection, other_id, run_id, None, "n", "q", "b", "s", [PAGE])
    finally:
        _remove(other_id, other_email)


def test_deleting_the_thread_keeps_the_report(connection, account):
    account_id, _ = account
    thread_id, _, report = _report(connection, account_id)

    assert delete_thread(connection, account_id, thread_id) is True

    kept = get_report(connection, account_id, report[0])
    assert kept is not None and kept[2] is None


def test_deleting_a_knowledge_file_keeps_the_source(connection, account):
    account_id, _ = account
    stored = put_file(connection, account_id, "notes.md", "text")
    file_source = NewSource("file", "notes.md", None, "background", stored[0])
    _, _, report = _report(connection, account_id, sources=[file_source])
    assert list_sources(connection, account_id, report[0])[0][5] == stored[0]

    assert delete_file(connection, account_id, "notes.md") is True

    kept = list_sources(connection, account_id, report[0])
    assert kept == [(1, "file", "notes.md", None, "background", None)]


def test_a_run_with_a_report_cannot_be_deleted(connection, account):
    account_id, _ = account
    _, run_id, report = _report(connection, account_id)

    with pytest.raises(psycopg.errors.RestrictViolation):
        connection.execute("DELETE FROM run WHERE id = %s", (run_id,))

    assert get_report(connection, account_id, report[0]) is not None


def test_deleting_an_account_with_a_report_removes_everything(connection):
    account_id, email = _new_account()
    try:
        thread_id, run_id, report = _report(connection, account_id)

        assert delete_account(account_id) is True

        for table, column in (
            ("report", "id"),
            ("report_source", "report_id"),
            ("run", "id"),
            ("thread", "id"),
        ):
            value = {"report": report[0], "report_source": report[0], "run": run_id}.get(
                table, thread_id
            )
            count = connection.execute(
                f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,)
            ).fetchone()
            assert count == (0,), table
    finally:
        _remove(account_id, email)


def test_deleting_an_account_without_a_report_still_works(connection):
    account_id, email = _new_account()
    try:
        _run(connection, account_id)
        assert delete_account(account_id) is True
        assert delete_account(account_id) is False
    finally:
        _remove(account_id, email)


def test_render_download_holds_the_question_body_note_and_sources(connection, account):
    account_id, _ = account
    file_source = NewSource("file", "notes.md", None, "background")
    _, _, report = _report(connection, account_id, sources=[PAGE, file_source])
    text = render_download(report, list_sources(connection, account_id, report[0]))

    assert text.startswith("# Heat pumps")
    assert "Which is better?" in text and "The body." in text
    assert "## How this research stopped" in text and "It stopped." in text
    assert "## Sources" in text
    assert "A page" in text and "https://example.com/a" in text and "the first fact" in text
    assert "notes.md" in text and "background" in text
    assert "()" not in text and "<>" not in text and "[]" not in text


@pytest.mark.parametrize(
    "name",
    [
        "Heat pumps vs. boilers!",
        "../../etc/passwd",
        "   ",
        "Ünïcode name",
        "x" * 200,
        'line one\nand "quoted"',
    ],
)
def test_download_filename_is_safe(name):
    filename = download_filename(name)

    assert "/" not in filename and ".." not in filename
    assert '"' not in filename and "\n" not in filename
    assert re.fullmatch(r"[a-z0-9-]*\.md", filename)
    assert len(filename) <= 64


def test_download_filename_values():
    assert download_filename("Heat pumps vs. boilers!") == "heat-pumps-vs-boilers.md"
    assert download_filename("   ") == "report.md"
    assert download_filename("???") == "report.md"
    assert download_filename("x" * 200) == "x" * 60 + ".md"


def test_the_database_refuses_a_bad_status_and_a_bad_kind(connection, account):
    account_id, _ = account
    thread_id, run_id = _run(connection, account_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO report (account_id, thread_id, run_id, name, question, body, "
            "stop_note, status, byte_size) VALUES (%s, %s, %s, 'n', 'q', 'b', 's', 'draft', 1)",
            (account_id, thread_id, run_id),
        )

    _, _, report = _report(connection, account_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        connection.execute(
            "INSERT INTO report_source (account_id, report_id, position, kind, title, used_for) "
            "VALUES (%s, %s, 9, 'video', 't', 'u')",
            (account_id, report[0]),
        )
