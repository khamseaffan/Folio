import json
import logging
import threading
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app import research
from app.account import ensure_account
from app.browser import FakePageOpener, Link, OpenedPage, PageError
from app.db import connect
from app.knowledge_file import put_file
from app.message import list_messages
from app.model import FakeModel, Reply, ToolCall
from app.report import list_reports
from app.report_source import list_sources
from app.research import (
    FAILED_LINE,
    MIN_RETRY_SECONDS,
    NO_SOURCE_LINE,
    READY_LINE,
    execute_research,
)
from app.run import cancel_run, create_run, get_run, start_attempt
from app.scheduled import MAX_ATTEMPTS, execute
from app.search import FakeSearch, SearchResult
from app.thread import create_thread, delete_thread
from app.turn import _CAPPED, _STOPPED

LOGGER = "folio.worker"
QUESTION = "secret-question-wording-lantern about heat pumps"
RAW = "RAWSECRET-page-text-quill"
URL_A = "https://a.example/heat"
URL_B = "https://b.example/gas"
WAIT = 30


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"research-run-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        # A report restricts the delete of its run, so it goes first.
        connection.execute("DELETE FROM report WHERE account_id = %s", (account_id,))
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


class _Opener(FakePageOpener):
    def __init__(self, pages) -> None:
        super().__init__(pages)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Proxy:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.port = 0

    def start(self) -> int:
        self.started = True
        self.port = 4242
        return self.port

    def stop(self) -> None:
        self.stopped = True


class _World:
    def __init__(self) -> None:
        self.search = FakeSearch(
            [SearchResult("A", URL_A, "snippet a"), SearchResult("B", URL_B, "snippet b")]
        )
        self.opener = _Opener(
            {
                URL_A: OpenedPage(URL_A, "Title A", RAW, (Link("b", URL_B),)),
                URL_B: OpenedPage(URL_B, "Title B", "Text of B. " * 5, ()),
            }
        )
        self.proxies: list[_Proxy] = []

    def make_proxy(self) -> _Proxy:
        self.proxies.append(_Proxy())
        return self.proxies[-1]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    built = _World()
    monkeypatch.setattr("app.research.open_search", lambda: built.search)
    monkeypatch.setattr("app.research.SafeProxy", built.make_proxy)
    monkeypatch.setattr("app.research.ChromePageOpener", lambda proxy, robots: built.opener)
    return built


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("app.research._clock", lambda: fake.now)
    return fake


def _call(name, arguments, call_id="c1"):
    return Reply("", 100, 20, 120, (ToolCall(call_id, name, arguments),))


def _say(text):
    return Reply(text, 50, 10, 60)


def _search():
    return _call("search_web", {"query": "heat pumps"})


def _open(url):
    return _call("open_page", {"url": url})


def _submit(sources=(), note="Enough was read."):
    return _call(
        "submit_report",
        {"name": "Heat pumps", "body": "The body.", "stop_note": note, "sources": list(sources)},
    )


def _happy():
    return (_search(), _open(URL_A), _say("Digest A"), _submit())


class _Hook(FakeModel):
    def __init__(self, hooks=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hooks = hooks or {}
        self._count = 0

    def complete(self, prompt, blocks, tools=()):
        self._count += 1
        hook = self._hooks.get(self._count)
        if hook is not None:
            hook()
        return super().complete(prompt, blocks, tools)


class _Raising(FakeModel):
    def __init__(self, clock: _Clock | None = None, seconds: float = 0.0) -> None:
        super().__init__()
        self._clock = clock
        self._seconds = seconds
        self.calls = 0

    def complete(self, prompt, blocks, tools=()):
        self.calls += 1
        if self._clock is not None:
            self._clock.now += self._seconds
        raise RuntimeError(f"provider said {QUESTION} {RAW}")


def _make(connection, account_id, task=QUESTION):
    thread = create_thread(connection, account_id)
    run = create_run(connection, account_id, thread[0], None, "research", task)
    assert run is not None
    return thread[0], run[0]


def _lines(connection, account_id, thread_id, kind="result"):
    rows = list_messages(connection, account_id, thread_id) or []
    return [row for row in rows if row[5] == kind]


def _usage(connection, email):
    return connection.execute(
        "SELECT operation, run_id, message_id, usage_missing, provider FROM usage "
        "WHERE email = %s ORDER BY at",
        (email,),
    ).fetchall()


def _state(connection, run_id):
    row = get_run(connection, run_id)
    assert row is not None
    return row


def _reports(connection, account_id):
    return list_reports(connection, account_id)


def test_a_run_saves_one_report_and_records_usage_as_it_goes(connection, account, world) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)
    reached, release = threading.Event(), threading.Event()

    def hold() -> None:
        reached.set()
        assert release.wait(WAIT)

    model = _Hook(
        hooks={6: hold},
        script=(
            _search(),
            _open(URL_A),
            _say("Digest A"),
            _open(URL_B),
            _say("Digest B"),
            _submit(sources=[{"number": 2, "used_for": "Running costs"}]),
        ),
    )
    out: list[str] = []
    worker = threading.Thread(target=lambda: out.append(execute(run_id, model)))
    worker.start()
    try:
        assert reached.wait(WAIT)
        # Five model calls and one search are already stored while the sixth call is still running.
        assert len(_usage(connection, email)) == 6
        assert _state(connection, run_id)[5] == "working"
    finally:
        release.set()
        worker.join(timeout=WAIT)
    assert not worker.is_alive()

    assert out == ["ready"]
    rows = _usage(connection, email)
    assert len(rows) == 7
    assert all(row[0] == "research" and row[1] == run_id and row[2] is None for row in rows)
    assert sum(1 for row in rows if row[4] == "tavily") == 1
    reports = _reports(connection, account_id)
    assert len(reports) == 1 and reports[0][3] == run_id and reports[0][2] == thread_id
    assert reports[0][4] == "Heat pumps" and reports[0][5] == QUESTION
    sources = list_sources(connection, account_id, reports[0][0])
    assert [(s[0], s[1], s[2], s[3], s[4]) for s in sources] == [
        (1, "page", "Title A", URL_A, "Read during the research."),
        (2, "page", "Title B", URL_B, "Running costs"),
    ]
    lines = _lines(connection, account_id, thread_id)
    assert [(row[3], row[4], row[6]) for row in lines] == [
        (run_id, "assistant", READY_LINE.format(name="Heat pumps"))
    ]
    state = _state(connection, run_id)
    assert state[5] == "ready" and state[9] is not None and state[8] == 1
    assert world.opener.closed and world.proxies[0].started and world.proxies[0].stopped

    before = len(_usage(connection, email))
    assert execute(run_id, FakeModel()) == "duplicate"
    assert len(_usage(connection, email)) == before
    assert len(_reports(connection, account_id)) == 1
    assert len(_lines(connection, account_id, thread_id)) == 1


def test_the_page_text_is_never_stored_or_logged(connection, account, world, caplog) -> None:
    account_id, _ = account
    _make(connection, account_id)
    model = FakeModel(script=_happy())

    with caplog.at_level(logging.DEBUG):
        execute(_state_run(connection, account_id), model)

    stored = connection.execute(
        "SELECT body, stop_note FROM report WHERE account_id = %s", (account_id,)
    ).fetchall()
    assert all(RAW not in text for row in stored for text in row)
    assert RAW not in caplog.text and QUESTION not in caplog.text


def _state_run(connection, account_id):
    row = connection.execute(
        "SELECT id FROM run WHERE account_id = %s ORDER BY created_at DESC", (account_id,)
    ).fetchone()
    return row[0]


def test_a_second_delivery_while_the_first_works_does_nothing(
    connection, account, world
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)
    reached, release = threading.Event(), threading.Event()

    def hold() -> None:
        reached.set()
        assert release.wait(WAIT)

    out: list[str] = []
    model = _Hook(hooks={1: hold}, script=_happy())
    worker = threading.Thread(target=lambda: out.append(execute(run_id, model)))
    worker.start()
    try:
        assert reached.wait(WAIT)
        second = FakeModel(script=_happy())
        assert execute(run_id, second) == "duplicate"
        assert second.seen == []
    finally:
        release.set()
        worker.join(timeout=WAIT)

    assert out == ["ready"]
    assert len(_reports(connection, account_id)) == 1


def test_cancel_during_a_step_ends_the_run_with_no_report_and_no_line(
    connection, account, world
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)

    def cancel() -> None:
        assert cancel_run(connection, account_id, run_id)

    model = _Hook(hooks={2: cancel}, script=_happy())

    assert execute(run_id, model) == "cancelled"

    assert _reports(connection, account_id) == []
    assert _lines(connection, account_id, thread_id) == []
    assert _state(connection, run_id)[5] == "cancelled"
    assert len(model.seen) == 2
    # The call that was running finished, and both calls and the search are on record.
    assert len(_usage(connection, email)) == 3
    assert world.opener.opened == []


def test_a_deleted_thread_ends_the_run_with_no_report_and_no_line(
    connection, account, world
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)

    def remove_thread() -> None:
        assert delete_thread(connection, account_id, thread_id)

    model = _Hook(hooks={2: remove_thread}, script=_happy())

    assert execute(run_id, model) == "cancelled"

    assert _reports(connection, account_id) == []
    assert _state(connection, run_id)[5] == "cancelled"
    count = connection.execute(
        "SELECT count(*) FROM message WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert count is not None and count[0] == 0
    assert len(model.seen) == 2


def test_a_stopped_account_ends_the_run_with_the_stopped_sentence_once(
    connection, account, world
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)

    def stop() -> None:
        connection.execute("UPDATE email_limit SET stopped = true WHERE email = %s", (email,))

    model = _Hook(hooks={2: stop}, script=_happy())

    assert execute(run_id, model) == "cancelled"

    assert _reports(connection, account_id) == []
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [_STOPPED]
    assert _state(connection, run_id)[5] == "cancelled"
    assert len(model.seen) == 2
    assert execute(run_id, FakeModel()) == "duplicate"
    assert len(_lines(connection, account_id, thread_id)) == 1


def test_a_capped_account_gets_one_last_call_and_a_report(connection, account, world) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)

    def cap() -> None:
        connection.execute("UPDATE email_limit SET token_cap = 250 WHERE email = %s", (email,))

    model = _Hook(hooks={3: cap}, script=_happy())

    assert execute(run_id, model) == "ready"

    assert [tool.name for tool in model.seen[-1][1]] == ["submit_report"]
    reports = _reports(connection, account_id)
    assert len(reports) == 1
    assert reports[0][7].endswith("Stopped because the usage limit was reached.")
    assert execute(run_id, FakeModel()) == "duplicate"
    assert len(_reports(connection, account_id)) == 1
    assert len(_lines(connection, account_id, thread_id)) == 1


@pytest.mark.parametrize(("state", "line"), [("stopped", _STOPPED), ("capped", _CAPPED)])
def test_a_run_for_a_stopped_or_capped_account_never_starts(
    connection, account, world, state, line
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)
    if state == "stopped":
        connection.execute("UPDATE email_limit SET stopped = true WHERE email = %s", (email,))
    else:
        connection.execute("UPDATE email_limit SET token_cap = 0 WHERE email = %s", (email,))
    model = FakeModel(script=_happy())

    assert execute(run_id, model) == "cancelled"

    assert model.seen == []
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [line]
    assert _reports(connection, account_id) == []
    assert world.proxies == []


def test_a_failed_attempt_adds_its_time_and_asks_for_a_retry(
    connection, account, world, clock, caplog
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)
    model = _Raising(clock, 300.0)

    with caplog.at_level(logging.DEBUG):
        assert execute(run_id, model) == "retry"

    state = _state(connection, run_id)
    assert state[5] == "working" and state[7] == 300_000 and state[8] == 1
    assert _lines(connection, account_id, thread_id) == []
    assert world.opener.closed and world.proxies[0].stopped
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(str(run_id) in text and "RuntimeError" in text for text in errors)
    assert QUESTION not in caplog.text and RAW not in caplog.text
    rows = _usage(connection, email)
    assert len(rows) == 1 and rows[0][3] is True and rows[0][1] == run_id


def test_a_failure_with_too_little_time_left_is_not_retried(
    connection, account, world, clock
) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    connection.execute("UPDATE run SET work_ms = %s WHERE id = %s", (20 * 60 * 1000, run_id))
    model = _Raising(clock, 9 * 60.0)

    assert execute(run_id, model) == "failed"

    state = _state(connection, run_id)
    assert state[5] == "failed" and state[7] == 29 * 60 * 1000
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [FAILED_LINE]
    assert execute(run_id, FakeModel()) == "duplicate"


def test_a_retry_with_too_little_time_left_is_not_started(connection, account, world) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    left = MIN_RETRY_SECONDS - 1
    connection.execute(
        "UPDATE run SET work_ms = %s, attempt_count = 1 WHERE id = %s",
        ((30 * 60 - left) * 1000, run_id),
    )
    model = FakeModel(script=_happy())

    assert execute(run_id, model) == "failed"

    assert model.seen == []
    assert _state(connection, run_id)[5] == "failed"
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [FAILED_LINE]
    assert world.proxies == []


def test_the_fourth_failed_attempt_is_dead(connection, account, world, clock) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    connection.execute(
        "UPDATE run SET attempt_count = %s WHERE id = %s", (MAX_ATTEMPTS - 1, run_id)
    )

    assert execute(run_id, _Raising(clock, 1.0)) == "dead"

    assert _state(connection, run_id)[5] == "dead"
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [FAILED_LINE]
    assert execute(run_id, FakeModel()) == "duplicate"


def test_a_run_that_opened_nothing_fails_once_with_a_line_and_no_report(
    connection, account, world
) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    world.opener = _Opener({URL_A: PageError("That page could not be opened.")})
    model = FakeModel(script=(_search(), _open(URL_A), _say("nothing"), _say("nothing")))

    assert execute(run_id, model) == "failed"

    assert _reports(connection, account_id) == []
    assert [row[6] for row in _lines(connection, account_id, thread_id)] == [NO_SOURCE_LINE]
    assert _state(connection, run_id)[5] == "failed"
    assert execute(run_id, FakeModel()) == "duplicate"


def test_a_run_marked_dead_while_it_works_drops_its_late_report(
    connection, account, world, monkeypatch
) -> None:
    account_id, email = account
    thread_id, run_id = _make(connection, account_id)
    real = research.add_work_ms

    def after_the_last_check(conn, rid, ms) -> None:
        # The loop is over and the report is ready to save when the tick gives up on the run.
        connection.execute("UPDATE run SET status = 'dead' WHERE id = %s", (rid,))
        real(conn, rid, ms)

    monkeypatch.setattr("app.research.add_work_ms", after_the_last_check)
    model = FakeModel(script=_happy())

    assert execute(run_id, model) == "dead"

    assert len(model.seen) == 4
    assert _reports(connection, account_id) == []
    assert _lines(connection, account_id, thread_id) == []
    assert len(_usage(connection, email)) == 5
    assert _state(connection, run_id)[5] == "dead"


def test_a_run_marked_dead_early_stops_calling_the_model(connection, account, world) -> None:
    account_id, _ = account
    _, run_id = _make(connection, account_id)

    def mark_dead() -> None:
        connection.execute("UPDATE run SET status = 'dead' WHERE id = %s", (run_id,))

    model = _Hook(hooks={2: mark_dead}, script=_happy())

    assert execute(run_id, model) == "dead"

    assert len(model.seen) == 2
    assert _reports(connection, account_id) == []


def test_the_loop_can_read_knowledge_files_and_no_other_tool(
    connection, account, world
) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    notes = put_file(connection, account_id, "notes.md", "My own notes about the house.")
    model = FakeModel(
        script=(
            _call("write_knowledge", {"name": "evil.md", "body": "x"}, "w1"),
            _call("read_knowledge", {"name": "notes.md"}, "r1"),
            _call("start_research", {"question": "again"}, "s1"),
            _submit(sources=[{"number": 1, "used_for": "Own notes"}]),
        )
    )

    assert execute(run_id, model) == "ready"

    first = [json.loads(b.text) for b in model.seen[1][0] if b.role == "tool"]
    assert first[0]["ok"] is False and first[0]["code"] == "unknown"
    third = [json.loads(b.text) for b in model.seen[3][0] if b.role == "tool"]
    assert third[1]["ok"] is True and third[1]["source"] == 1
    assert third[2]["ok"] is False and third[2]["code"] == "unknown"
    stored = connection.execute(
        "SELECT name FROM knowledge_file WHERE account_id = %s", (account_id,)
    ).fetchall()
    assert [row[0] for row in stored] == ["notes.md"]
    reports = _reports(connection, account_id)
    sources = list_sources(connection, account_id, reports[0][0])
    assert [(s[1], s[2], s[3], s[5]) for s in sources] == [("file", "notes.md", None, notes[0])]
    assert connection.execute(
        "SELECT count(*) FROM run WHERE account_id = %s", (account_id,)
    ).fetchone() == (1,)


def test_memory_answers_only_the_three_read_tools(connection, account) -> None:
    account_id, _ = account
    thread_id, _ = _make(connection, account_id)
    memory = research._memory_for(account_id, thread_id)

    for name in ("write_knowledge", "start_research", "create_thread", "delete_report", "other"):
        result = json.loads(memory(ToolCall("c", name, {"name": "x.md", "body": "y"})))
        assert result["ok"] is False and result["code"] == "unknown"
    assert json.loads(memory(ToolCall("c", "list_knowledge", {})))["ok"] is True
    stored = connection.execute(
        "SELECT count(*) FROM knowledge_file WHERE account_id = %s", (account_id,)
    ).fetchone()
    assert stored == (0,)


def test_the_prompt_holds_the_question_and_the_rules_and_no_chat_tools(
    connection, account, world
) -> None:
    account_id, _ = account
    _, run_id = _make(connection, account_id)
    prompts: list[str] = []

    class _Capture(FakeModel):
        def complete(self, prompt, blocks, tools=()):
            prompts.append(prompt)
            return super().complete(prompt, blocks, tools)

    model = _Capture(script=_happy())

    execute(run_id, model)

    assert QUESTION in model.seen[0][0][0].text
    assert "start_research" not in prompts[0]
    names = {tool.name for tool in model.seen[0][1]}
    assert "write_knowledge" not in names and "create_thread" not in names


def test_execute_research_with_given_search_and_opener_starts_no_browser(
    connection, account, world
) -> None:
    account_id, _ = account
    thread_id, run_id = _make(connection, account_id)
    run = start_attempt(connection, run_id)
    assert run is not None
    opener = _Opener(world.opener._pages)

    outcome = execute_research(run, FakeModel(script=_happy()), world.search, opener)

    assert outcome == "ready"
    assert world.proxies == []
    assert opener.opened == [URL_A]


def test_the_proxy_and_the_browser_are_closed_when_the_loop_fails(
    connection, account, world, clock
) -> None:
    account_id, _ = account
    _, run_id = _make(connection, account_id)

    assert execute(run_id, _Raising(clock, 1.0)) == "retry"

    assert world.opener.closed
    assert world.proxies[0].started and world.proxies[0].stopped
