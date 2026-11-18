import uuid
from collections.abc import Iterator

import pytest
from app.account import ensure_account
from app.db import connect
from app.model import FakeModel, ModelError, Reply
from app.thread import create_thread, get_thread, rename_thread
from app.turn import DEFAULT_THREAD_NAME, name_thread


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"naming-{uuid.uuid4().hex}@example.com"
    return ensure_account(f"local:{email}", email), email


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    account_id, email = _new_account()
    yield account_id, email
    with connect() as connection:
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


class Spy(FakeModel):
    def __init__(self, *replies, error=None):
        super().__init__(script=tuple(replies))
        self.prompts = []
        self.error = error

    def complete(self, prompt, blocks, tools=()):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return super().complete(prompt, blocks, tools)


def _name(account_id, thread_id) -> str:
    with connect() as connection:
        row = get_thread(connection, account_id, thread_id)
    assert row is not None
    return row[2]


def _usage(email):
    with connect() as connection:
        return connection.execute(
            "SELECT operation, input_tokens, output_tokens, total_tokens, usage_missing "
            "FROM usage WHERE email = %s",
            (email,),
        ).fetchall()


def _thread(account_id):
    with connect() as connection:
        return create_thread(connection, account_id)[0]


def test_a_new_thread_is_named_from_its_first_exchange(account):
    account_id, email = account
    thread_id = _thread(account_id)
    model = Spy(Reply("Cloud Run cold starts", 12, 4, 16))

    name_thread(account_id, thread_id, email, model, "Why is it slow at first?", "Because...")

    assert _name(account_id, thread_id) == "Cloud Run cold starts"
    assert _usage(email) == [("chat", 12, 4, 16, False)]


def test_the_model_sees_the_exchange_and_is_asked_for_a_short_title(account):
    account_id, email = account
    thread_id = _thread(account_id)
    model = Spy(Reply("Short title", 1, 1, 2))

    name_thread(account_id, thread_id, email, model, "my question", "the answer")

    assert "title" in model.prompts[0].lower()
    text = model.seen[0][0][0].text
    assert "my question" in text and "the answer" in text
    assert model.seen[0][1] == ()


def test_a_name_set_by_the_person_or_the_agent_is_never_replaced(account):
    account_id, email = account
    thread_id = _thread(account_id)
    with connect() as connection:
        rename_thread(connection, account_id, thread_id, "Trip to Kyoto")
    model = Spy(Reply("Something else", 1, 1, 2))

    name_thread(account_id, thread_id, email, model, "hello", "hi")

    assert _name(account_id, thread_id) == "Trip to Kyoto"
    assert model.seen == []
    assert _usage(email) == []


def test_a_name_set_while_the_title_was_being_written_wins(account):
    account_id, email = account
    thread_id = _thread(account_id)

    class Renaming(Spy):
        def complete(self, prompt, blocks, tools=()):
            with connect() as connection:
                rename_thread(connection, account_id, thread_id, "Chosen meanwhile")
            return super().complete(prompt, blocks, tools)

    renaming = Renaming(Reply("Generated", 1, 1, 2))
    name_thread(account_id, thread_id, email, renaming, "a", "b")

    assert _name(account_id, thread_id) == "Chosen meanwhile"
    assert len(_usage(email)) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Heat pumps vs boilers"', "Heat pumps vs boilers"),
        ("  Heat pumps vs boilers.  ", "Heat pumps vs boilers"),
        ("Heat\npumps  and\tboilers", "Heat pumps and boilers"),
        ("x" * 200, "x" * 60),
    ],
)
def test_the_title_is_tidied_and_capped(account, raw, expected):
    account_id, email = account
    thread_id = _thread(account_id)

    name_thread(account_id, thread_id, email, Spy(Reply(raw, 1, 1, 2)), "a", "b")

    assert _name(account_id, thread_id) == expected


def test_an_empty_title_leaves_the_default_name_but_the_call_is_still_recorded(account):
    account_id, email = account
    thread_id = _thread(account_id)

    name_thread(account_id, thread_id, email, Spy(Reply("  ", 3, 1, 4)), "a", "b")

    assert _name(account_id, thread_id) == DEFAULT_THREAD_NAME
    assert _usage(email) == [("chat", 3, 1, 4, False)]


def test_a_failing_model_never_raises_and_leaves_the_name(account):
    account_id, email = account
    thread_id = _thread(account_id)

    name_thread(account_id, thread_id, email, Spy(error=ModelError("down")), "a", "b")

    assert _name(account_id, thread_id) == DEFAULT_THREAD_NAME


def test_missing_usage_is_stored_as_missing(account):
    account_id, email = account
    thread_id = _thread(account_id)

    name_thread(account_id, thread_id, email, Spy(Reply("A title", None, None, None)), "a", "b")

    assert _usage(email) == [("chat", None, None, None, True)]


def test_only_the_threads_own_account_is_touched(account):
    account_id, email = account
    other_id, other_email = _new_account()
    try:
        thread_id = _thread(account_id)
        name_thread(other_id, thread_id, other_email, Spy(Reply("Hijack", 1, 1, 2)), "a", "b")
        assert _name(account_id, thread_id) == DEFAULT_THREAD_NAME
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM account WHERE id = %s", (other_id,))
            connection.execute("DELETE FROM usage WHERE email = %s", (other_email,))
            connection.execute("DELETE FROM email_limit WHERE email = %s", (other_email,))
