import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.account import ensure_account
from app.db import connect
from app.message import insert_message, list_messages
from app.model import CONTEXT_TOKENS, Block, ModelError, Reply
from app.session import apply as apply_session
from app.thread import create_thread
from app.turn import TurnResult, run_turn
from app.usage import apply as apply_usage
from app.usage import record_usage

ALLOWED_EMAIL = "khamseaffan@gmail.com"
STOPPED = "A system admin has stopped your account. Contact khamseaffan@gmail.com."
CAPPED = "You have reached the allowed usage limit."
FAILED = "That didn't go through."
HARM = "A task that would cause physical harm stops and asks."
CAUSE = "distinctive-cause-zebra"
PROVIDER_DETAIL = "provider-detail-quoll"


class FakeModel:
    def __init__(
        self,
        reply: Reply | None = None,
        error: BaseException | None = None,
        token_count: int = 1,
        overflow_text: str | None = None,
        later_mark: str | None = None,
        later_text: str | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.completed: list[list[Block]] = []
        self.counted: list[list[Block]] = []
        self._reply = reply if reply is not None else Reply("ok", 1, 1, 2)
        self._error = error
        self._token_count = token_count
        self._overflow_text = overflow_text
        self._later_mark = later_mark
        self._later_text = later_text

    def complete(self, prompt: str, blocks: list[Block]) -> Reply:
        self.prompts.append(prompt)
        self.completed.append(list(blocks))
        if self._error is not None:
            raise ModelError(PROVIDER_DETAIL) from self._error
        return self._reply

    def count_tokens(self, prompt: str, blocks: list[Block]) -> int:
        self.counted.append(list(blocks))
        texts = [block.text for block in blocks]
        if self._overflow_text is not None and self._overflow_text in texts and len(blocks) > 1:
            return CONTEXT_TOKENS + 1
        if (
            self._later_mark is not None
            and self._later_text is not None
            and self._later_mark in prompt
            and self._later_text in texts
            and len(blocks) > 1
        ):
            return CONTEXT_TOKENS + 1
        return self._token_count


def _account_existed(email: str) -> bool:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if present is None or present[0] is None:
            return False
        row = connection.execute(
            "SELECT id FROM account WHERE email = %s",
            (stored,),
        ).fetchone()
    return row is not None


def _usage_ids(email: str) -> set[uuid.UUID]:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return set()
        rows = connection.execute(
            "SELECT id FROM usage WHERE email = %s",
            (stored,),
        ).fetchall()
    return {row[0] for row in rows}


def _delete_usage(account_id: uuid.UUID, email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return
        connection.execute(
            """
            DELETE FROM usage
            WHERE email = %s
               OR account_id = %s
            """,
            (stored, account_id),
        )


def _delete_new_usage(email: str, kept: set[uuid.UUID]) -> None:
    stored = email.lower()
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.usage')").fetchone()
        if present is None or present[0] is None:
            return
        rows = connection.execute(
            "SELECT id FROM usage WHERE email = %s",
            (stored,),
        ).fetchall()
        extra = [row[0] for row in rows if row[0] not in kept]
        if not extra:
            return
        connection.execute("DELETE FROM usage WHERE id = ANY(%s)", (extra,))


def _delete_session(session_id: uuid.UUID) -> None:
    with connect() as connection:
        present = connection.execute("SELECT to_regclass('public.session')").fetchone()
        if present is None or present[0] is None:
            return
        connection.execute("DELETE FROM session WHERE id = %s", (session_id,))


def _delete_account(account_id: uuid.UUID, email: str) -> None:
    stored = email.lower()
    with connect() as connection:
        account = connection.execute("SELECT to_regclass('public.account')").fetchone()
        if account is not None and account[0] is not None:
            connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        limit = connection.execute("SELECT to_regclass('public.email_limit')").fetchone()
        if limit is not None and limit[0] is not None:
            connection.execute(
                "DELETE FROM email_limit WHERE email = %s",
                (stored,),
            )


def _insert_session(connection, account_id: uuid.UUID) -> uuid.UUID:
    created = connection.execute(
        """
        INSERT INTO session (account_id)
        VALUES (%s)
        RETURNING id
        """,
        (account_id,),
    ).fetchone()
    if created is None:
        raise RuntimeError("session was not stored")
    return created[0]


def _stamp(connection, message_id: uuid.UUID, when: datetime) -> None:
    connection.execute(
        "UPDATE message SET created_at = %s WHERE id = %s",
        (when, message_id),
    )


@pytest.fixture
def accounts():
    owner_email = f"turn-{uuid.uuid4()}@example.com"
    allowed_existed = _account_existed(ALLOWED_EMAIL)
    allowed_usage = _usage_ids(ALLOWED_EMAIL)
    owner_id = ensure_account(str(uuid.uuid4()), owner_email)
    allowed_id = ensure_account(str(uuid.uuid4()), ALLOWED_EMAIL)
    with connect() as connection:
        apply_session(connection)
        apply_usage(connection)
        owner_session = _insert_session(connection, owner_id)
        allowed_session = _insert_session(connection, allowed_id)
        connection.execute(
            "UPDATE account SET display_name = %s WHERE id = %s",
            ("Ada", owner_id),
        )
        thread = create_thread(connection, owner_id)
    try:
        yield {
            "owner_id": owner_id,
            "owner_email": owner_email,
            "owner_session": owner_session,
            "allowed_id": allowed_id,
            "allowed_session": allowed_session,
            "thread_id": thread[0],
        }
    finally:
        _delete_usage(owner_id, owner_email)
        _delete_account(owner_id, owner_email)
        _delete_new_usage(ALLOWED_EMAIL, allowed_usage)
        if allowed_existed:
            _delete_session(allowed_session)
        else:
            _delete_usage(allowed_id, ALLOWED_EMAIL)
            _delete_account(allowed_id, ALLOWED_EMAIL)


def test_owned_thread_stores_the_reply_and_one_usage_row(accounts) -> None:
    owner_id = accounts["owner_id"]
    email = accounts["owner_email"]
    thread_id = accounts["thread_id"]
    reply = Reply(text="fake-reply", input_tokens=17, output_tokens=19, total_tokens=36)
    model = FakeModel(reply=reply)
    with connect() as connection:
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        usage = connection.execute(
            """
            SELECT operation, provider, model, input_tokens, output_tokens,
                   total_tokens, message_id, run_id
            FROM usage
            WHERE email = %s
            """,
            (email,),
        ).fetchall()
    assert isinstance(result, TurnResult)
    assert result.kind == "chat"
    assert result.body == "fake-reply"
    assert listed is not None
    assert len(listed) == 2
    by_role = {row[4]: row for row in listed}
    person = by_role["person"]
    assistant = by_role["assistant"]
    assert person[3] is None
    assert person[5] == "chat"
    assert person[6] == "hello"
    assert assistant[3] is None
    assert assistant[5] == "chat"
    assert assistant[6] == "fake-reply"
    assert usage == [("chat", "google", "gemini-4-argon", 17, 19, 36, assistant[0], None)]
    assert HARM in model.prompts[0]
    assert "Ada" in model.prompts[0]


def test_last_active_at_moves_on_success_and_on_failure(accounts) -> None:
    owner_id = accounts["owner_id"]
    session_id = accounts["owner_session"]
    thread_id = accounts["thread_id"]
    stale = datetime(2000, 1, 1, tzinfo=UTC)
    with connect() as connection:
        connection.execute(
            """
            UPDATE session
            SET last_active_at = %s
            WHERE id = %s
              AND account_id = %s
            """,
            (stale, session_id, owner_id),
        )
        run_turn(
            connection,
            session_id,
            owner_id,
            thread_id,
            "hello",
            FakeModel(reply=Reply("ok", 1, 1, 2)),
        )
        succeeded = connection.execute(
            "SELECT last_active_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE session
            SET last_active_at = %s
            WHERE id = %s
              AND account_id = %s
            """,
            (stale, session_id, owner_id),
        )
        run_turn(
            connection,
            session_id,
            owner_id,
            thread_id,
            "again",
            FakeModel(error=RuntimeError(CAUSE)),
        )
        failed = connection.execute(
            "SELECT last_active_at FROM session WHERE id = %s",
            (session_id,),
        ).fetchone()
    assert succeeded is not None
    assert failed is not None
    assert succeeded[0] > stale
    assert failed[0] > stale


def test_stopped_email_returns_the_line_and_stores_nothing(accounts) -> None:
    owner_id = accounts["owner_id"]
    email = accounts["owner_email"]
    thread_id = accounts["thread_id"]
    model = FakeModel()
    with connect() as connection:
        connection.execute(
            "UPDATE email_limit SET stopped = true WHERE email = %s",
            (email,),
        )
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        usage = connection.execute(
            "SELECT count(*) FROM usage WHERE email = %s",
            (email,),
        ).fetchone()
    assert result == STOPPED
    assert listed == []
    assert usage == (0,)
    assert model.completed == []
    assert model.counted == []


def test_usage_at_the_cap_returns_the_line_without_figures(accounts) -> None:
    owner_id = accounts["owner_id"]
    email = accounts["owner_email"]
    thread_id = accounts["thread_id"]
    model = FakeModel(reply=Reply("should-not-run", 9, 9, 18))
    with connect() as connection:
        connection.execute(
            "UPDATE email_limit SET token_cap = %s WHERE email = %s",
            (100, email),
        )
        record_usage(
            connection,
            owner_id,
            email,
            "research",
            "google",
            "gemini-4-argon",
            100,
            0,
            100,
        )
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        usage = connection.execute(
            "SELECT operation FROM usage WHERE email = %s",
            (email,),
        ).fetchall()
    assert result == CAPPED
    assert not any(character.isdigit() for character in result)
    assert listed == []
    assert usage == [("research",)]
    assert model.completed == []
    assert model.counted == []


def test_usage_one_token_under_the_cap_still_stores_the_turn(accounts) -> None:
    owner_id = accounts["owner_id"]
    email = accounts["owner_email"]
    thread_id = accounts["thread_id"]
    model = FakeModel(reply=Reply("stored-reply", 5, 6, 11))
    with connect() as connection:
        connection.execute(
            "UPDATE email_limit SET token_cap = %s WHERE email = %s",
            (100, email),
        )
        record_usage(
            connection,
            owner_id,
            email,
            "research",
            "google",
            "gemini-4-argon",
            99,
            0,
            99,
        )
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        usage = connection.execute(
            """
            SELECT operation, input_tokens, output_tokens, total_tokens, message_id, run_id
            FROM usage
            WHERE email = %s
              AND operation = 'chat'
            """,
            (email,),
        ).fetchone()
    assert isinstance(result, TurnResult)
    assert result.body == "stored-reply"
    assert listed is not None
    assert len(listed) == 2
    assistant = next(row for row in listed if row[4] == "assistant")
    assert usage is not None
    assert usage[0] == "chat"
    assert usage[1] == 5
    assert usage[2] == 6
    assert usage[3] == 11
    assert usage[4] == assistant[0]
    assert usage[5] is None


def test_other_account_thread_is_not_found_for_the_admin_email(accounts) -> None:
    owner_id = accounts["owner_id"]
    thread_id = accounts["thread_id"]
    allowed_id = accounts["allowed_id"]
    model = FakeModel()
    before = _usage_ids(ALLOWED_EMAIL)
    with connect() as connection:
        caller = connection.execute(
            "SELECT email FROM account WHERE id = %s",
            (allowed_id,),
        ).fetchone()
        result = run_turn(
            connection,
            accounts["allowed_session"],
            allowed_id,
            thread_id,
            "steal",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        owner_usage = connection.execute(
            "SELECT count(*) FROM usage WHERE account_id = %s",
            (owner_id,),
        ).fetchone()
    assert caller == (ALLOWED_EMAIL,)
    assert result is None
    assert listed == []
    assert owner_usage == (0,)
    assert _usage_ids(ALLOWED_EMAIL) == before
    assert model.completed == []
    assert model.counted == []


def test_thread_summary_is_in_the_prompt_and_not_a_message(accounts) -> None:
    owner_id = accounts["owner_id"]
    thread_id = accounts["thread_id"]
    summary = "summary-token-river-quiet"
    model = FakeModel(reply=Reply("fake-reply", 1, 1, 2))
    with connect() as connection:
        connection.execute(
            "UPDATE thread SET summary = %s WHERE id = %s",
            (summary, thread_id),
        )
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        stored = connection.execute(
            "SELECT summary FROM thread WHERE id = %s",
            (thread_id,),
        ).fetchone()
    assert isinstance(result, TurnResult)
    assert summary in model.prompts[0]
    assert HARM in model.prompts[0]
    assert "Ada" in model.prompts[0]
    assert listed is not None
    assert summary not in [row[6] for row in listed]
    assert [block.text for block in model.completed[0]] == ["hello"]
    assert stored == (summary,)


def test_short_thread_sends_every_chat_message(accounts) -> None:
    owner_id = accounts["owner_id"]
    thread_id = accounts["thread_id"]
    model = FakeModel(token_count=CONTEXT_TOKENS + 1, reply=Reply("next", 1, 1, 2))
    base = datetime(2020, 1, 1, tzinfo=UTC)
    with connect() as connection:
        earlier = insert_message(connection, owner_id, thread_id, "person", "chat", "earlier")
        old_reply = insert_message(
            connection, owner_id, thread_id, "assistant", "chat", "reply-old"
        )
        failure = insert_message(
            connection, owner_id, thread_id, "assistant", "failure", "did-not-send"
        )
        assert earlier is not None
        assert old_reply is not None
        assert failure is not None
        _stamp(connection, earlier[0], base)
        _stamp(connection, old_reply[0], base + timedelta(seconds=1))
        _stamp(connection, failure[0], base + timedelta(seconds=2))
        run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "now",
            model,
        )
    assert model.completed
    assert [block.text for block in model.completed[0]] == ["earlier", "reply-old", "now"]


def test_full_window_keeps_the_latest_ten_and_omits_an_older_message(accounts) -> None:
    owner_id = accounts["owner_id"]
    thread_id = accounts["thread_id"]
    summary = "folded-summary-not-rewritten"
    model = FakeModel(
        reply=Reply("done", 1, 1, 2),
        overflow_text="p0",
    )
    base = datetime(2020, 1, 1, tzinfo=UTC)
    with connect() as connection:
        connection.execute(
            "UPDATE thread SET summary = %s WHERE id = %s",
            (summary, thread_id),
        )
        for index in range(11):
            person = insert_message(
                connection, owner_id, thread_id, "person", "chat", f"p{index}"
            )
            assert person is not None
            _stamp(connection, person[0], base + timedelta(seconds=index))
        for index in range(10):
            assistant = insert_message(
                connection, owner_id, thread_id, "assistant", "chat", f"a{index}"
            )
            assert assistant is not None
            _stamp(connection, assistant[0], base + timedelta(seconds=100 + index))
        skipped = insert_message(
            connection, owner_id, thread_id, "assistant", "failure", "kind-failure-skip"
        )
        assert skipped is not None
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "p-new",
            model,
        )
        stored = connection.execute(
            "SELECT summary FROM thread WHERE id = %s",
            (thread_id,),
        ).fetchone()
        usage = connection.execute(
            """
            SELECT operation, input_tokens, output_tokens, total_tokens, message_id, run_id
            FROM usage
            WHERE email = %s
            """,
            (accounts["owner_email"],),
        ).fetchall()
        listed = list_messages(connection, owner_id, thread_id)
    assert isinstance(result, TurnResult)
    assert len(model.completed) == 2
    fold = "Summarize the earlier messages so the next reply can continue."
    assert model.prompts[0] == f"{fold}\n{summary}"
    assert "Ada" not in model.prompts[0]
    assert HARM not in model.prompts[0]
    assert [block.text for block in model.completed[0]] == ["p0"]
    assert HARM in model.prompts[1]
    assert "Ada" in model.prompts[1]
    assert "done" in model.prompts[1]
    assert summary not in model.prompts[1]
    sent = [block.text for block in model.completed[1]]
    persons = [text for text in sent if text.startswith("p")]
    assistants = [text for text in sent if text.startswith("a")]
    assert persons == [f"p{index}" for index in range(1, 11)] + ["p-new"]
    assert assistants == [f"a{index}" for index in range(10)]
    assert "p0" not in sent
    assert "kind-failure-skip" not in sent
    assert [message[6] for message in result.omitted] == ["p0"]
    assert stored == ("done",)
    assert any("p0" in [block.text for block in blocks] for blocks in model.counted)
    assert summary not in sent
    assert listed is not None
    reply_id = next(row[0] for row in listed if row[6] == "done")
    assert len(usage) == 2
    assert all(row[0] == "chat" and row[5] is None for row in usage)
    assert {row[4] for row in usage} == {None, reply_id}
    assert all(row[1] == 1 and row[2] == 1 and row[3] == 2 for row in usage)


def test_a_second_omission_is_summarized_before_the_reply(accounts) -> None:
    owner_id = accounts["owner_id"]
    thread_id = accounts["thread_id"]
    prior = "prior-fold-mantle"
    model = FakeModel(
        reply=Reply("done", 1, 1, 2),
        overflow_text="p0",
        later_mark="done",
        later_text="p1",
    )
    base = datetime(2020, 1, 1, tzinfo=UTC)
    with connect() as connection:
        connection.execute(
            "UPDATE thread SET summary = %s WHERE id = %s",
            (prior, thread_id),
        )
        for index in range(11):
            person = insert_message(
                connection, owner_id, thread_id, "person", "chat", f"p{index}"
            )
            assert person is not None
            _stamp(connection, person[0], base + timedelta(seconds=index))
        for index in range(10):
            assistant = insert_message(
                connection, owner_id, thread_id, "assistant", "chat", f"a{index}"
            )
            assert assistant is not None
            _stamp(connection, assistant[0], base + timedelta(seconds=100 + index))
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "p-new",
            model,
        )
        stored = connection.execute(
            "SELECT summary FROM thread WHERE id = %s",
            (thread_id,),
        ).fetchone()
        usage = connection.execute(
            """
            SELECT operation, message_id, run_id
            FROM usage
            WHERE email = %s
            """,
            (accounts["owner_email"],),
        ).fetchall()
        listed = list_messages(connection, owner_id, thread_id)
    fold = "Summarize the earlier messages so the next reply can continue."
    assert isinstance(result, TurnResult)
    assert len(model.completed) == 3
    assert model.prompts[0] == f"{fold}\n{prior}"
    assert HARM not in model.prompts[0]
    assert [block.text for block in model.completed[0]] == ["p0"]
    assert model.prompts[1] == f"{fold}\ndone"
    assert [block.text for block in model.completed[1]] == ["p1"]
    assert HARM in model.prompts[2]
    assert "done" in model.prompts[2]
    assert fold not in model.prompts[2]
    sent = [block.text for block in model.completed[2]]
    assert "p0" not in sent
    assert "p1" not in sent
    assert [f"p{index}" for index in range(2, 11)] + ["p-new"] == [
        text for text in sent if text.startswith("p")
    ]
    assert [f"a{index}" for index in range(10)] == [text for text in sent if text.startswith("a")]
    assert [message[6] for message in result.omitted] == ["p0", "p1"]
    assert stored == ("done",)
    assert listed is not None
    reply_id = next(row[0] for row in listed if row[6] == "done")
    assert len(usage) == 3
    assert all(row[0] == "chat" and row[2] is None for row in usage)
    assert sum(row[1] is None for row in usage) == 2
    assert sum(row[1] == reply_id for row in usage) == 1


def test_model_error_keeps_the_person_message_and_stores_null_usage(accounts) -> None:
    owner_id = accounts["owner_id"]
    email = accounts["owner_email"]
    thread_id = accounts["thread_id"]
    model = FakeModel(error=RuntimeError(CAUSE))
    with connect() as connection:
        result = run_turn(
            connection,
            accounts["owner_session"],
            owner_id,
            thread_id,
            "hello",
            model,
        )
        listed = list_messages(connection, owner_id, thread_id)
        usage = connection.execute(
            """
            SELECT operation, provider, model, input_tokens, output_tokens,
                   total_tokens, message_id, run_id
            FROM usage
            WHERE email = %s
            """,
            (email,),
        ).fetchall()
    assert isinstance(result, TurnResult)
    assert result.kind == "failure"
    assert result.body == FAILED
    assert listed is not None
    assert len(listed) == 2
    bodies = [row[6] for row in listed]
    assert "hello" in bodies
    assert bodies.count(FAILED) == 1
    assert all(CAUSE not in body for body in bodies)
    assert all(PROVIDER_DETAIL not in body for body in bodies)
    person = next(row for row in listed if row[4] == "person")
    failure = next(row for row in listed if row[4] == "assistant")
    assert person[5] == "chat"
    assert person[6] == "hello"
    assert failure[5] == "failure"
    assert failure[6] == FAILED
    assert usage == [("chat", "google", "gemini-4-argon", None, None, None, None, None)]
