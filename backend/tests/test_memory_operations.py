import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from app.account import ensure_account
from app.db import connect
from app.knowledge_file import get_file, put_file
from app.operations import (
    BAD_FILE_NAME,
    BAD_TEXT,
    NO_ABILITY,
    NO_FILE,
    TOO_MANY,
    Context,
    Turn,
    agent_tools,
    call,
)
from app.usage import apply as apply_usage

MEMORY_TOOLS = {
    "list_knowledge",
    "search_knowledge",
    "read_knowledge",
    "write_knowledge",
    "append_knowledge",
    "edit_knowledge",
    "delete_knowledge",
    "delete_profile",
    "list_abilities",
    "write_ability",
    "delete_ability",
}


def _new_account() -> tuple[uuid.UUID, str]:
    email = f"mem-{uuid.uuid4().hex}@sakana.ai"
    return ensure_account(f"local:{email}", email), email


def _remove(account_id: uuid.UUID, email: str) -> None:
    with connect() as connection:
        apply_usage(connection)
        connection.execute("DELETE FROM account WHERE id = %s", (account_id,))
        connection.execute("DELETE FROM usage WHERE email = %s", (email,))
        connection.execute("DELETE FROM email_limit WHERE email = %s", (email,))


@pytest.fixture
def account() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def other() -> Iterator[tuple[uuid.UUID, str]]:
    created = _new_account()
    yield created
    _remove(*created)


@pytest.fixture
def connection() -> Iterator[psycopg.Connection]:
    with connect() as opened:
        opened.autocommit = True
        yield opened


def _call(connection, account_id, name, arguments, context=None):
    return call(connection, account_id, context or Context(None), name, arguments)


def test_write_then_read_then_list(connection, account) -> None:
    account_id, _ = account
    written = _call(
        connection, account_id, "write_knowledge", {"name": " travel.md ", "body": "Passport: blue"}
    )
    assert written == {"ok": True, "name": "travel.md", "byte_size": 14}
    read = _call(connection, account_id, "read_knowledge", {"name": "travel.md"})
    assert read == {"ok": True, "name": "travel.md", "body": "Passport: blue"}
    listed = _call(connection, account_id, "list_knowledge", {})
    assert listed["ok"] is True
    assert [(item["name"], item["byte_size"]) for item in listed["files"]] == [("travel.md", 14)]
    assert set(listed["files"][0]) == {"name", "byte_size", "updated_at"}
    replaced = _call(connection, account_id, "write_knowledge", {"name": "travel.md", "body": "x"})
    assert replaced["byte_size"] == 1


def test_search_returns_matches_with_a_snippet(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "travel.md", "The passport expires in May 2027.")
    found = _call(connection, account_id, "search_knowledge", {"query": "passport"})
    assert found["ok"] is True
    assert [match["name"] for match in found["matches"]] == ["travel.md"]
    assert "passport" in found["matches"][0]["snippet"].lower()
    odd = _call(connection, account_id, "search_knowledge", {"query": '"a & | !'})
    assert odd["ok"] is True


def test_append_creates_and_then_adds_on_a_new_line(connection, account) -> None:
    account_id, _ = account
    first = _call(connection, account_id, "append_knowledge", {"name": "a.md", "text": "a"})
    assert first == {"ok": True, "name": "a.md", "byte_size": 1}
    _call(connection, account_id, "append_knowledge", {"name": "a.md", "text": "b"})
    assert get_file(connection, account_id, "a.md")[3] == "a\nb"
    put_file(connection, account_id, "c.md", "a\n")
    _call(connection, account_id, "append_knowledge", {"name": "c.md", "text": "b"})
    assert get_file(connection, account_id, "c.md")[3] == "a\nb"


def test_edit_replaces_the_one_match(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "likes tea\nlives in Oslo")
    result = _call(
        connection,
        account_id,
        "edit_knowledge",
        {"name": "a.md", "find": "Oslo", "replace": "Bergen"},
    )
    assert result == {"ok": True, "name": "a.md", "byte_size": 25}
    assert get_file(connection, account_id, "a.md")[3] == "likes tea\nlives in Bergen"


def test_edit_is_refused_when_the_text_is_missing_or_repeated(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "red red blue")
    for find, count in (("red", 2), ("green", 0), ("", 0)):
        result = _call(
            connection,
            account_id,
            "edit_knowledge",
            {"name": "a.md", "find": find, "replace": "x"},
        )
        assert result["ok"] is False
        assert result["code"] == "invalid"
        assert f"appears {count} times" in result["error"]
        assert result["error"].endswith("It must appear exactly once.")
    assert get_file(connection, account_id, "a.md")[3] == "red red blue"


def test_bad_names_and_null_characters_are_refused(connection, account) -> None:
    account_id, _ = account
    for name in ("", " ", "x" * 81, "a\x00"):
        for operation, extra in (
            ("write_knowledge", {"body": "b"}),
            ("append_knowledge", {"text": "b"}),
            ("read_knowledge", {}),
            ("delete_knowledge", {}),
            ("edit_knowledge", {"find": "a", "replace": "b"}),
        ):
            result = _call(connection, account_id, operation, {"name": name, **extra})
            assert result == {"ok": False, "code": "invalid", "error": BAD_FILE_NAME}
    longest = _call(connection, account_id, "write_knowledge", {"name": "x" * 80, "body": "b"})
    assert longest["ok"] is True
    for operation, arguments in (
        ("write_knowledge", {"name": "a.md", "body": "a\x00"}),
        ("append_knowledge", {"name": "a.md", "text": "a\x00"}),
        ("search_knowledge", {"query": "a\x00"}),
        ("write_ability", {"name": "a", "body": "a\x00"}),
    ):
        result = _call(connection, account_id, operation, arguments)
        assert result == {"ok": False, "code": "invalid", "error": BAD_TEXT}
    assert get_file(connection, account_id, "a.md") is None
    bad_ability = _call(connection, account_id, "write_ability", {"name": "", "body": "b"})
    assert bad_ability == {"ok": False, "code": "invalid", "error": BAD_FILE_NAME}


def test_missing_file_is_not_found_for_read_edit_delete(connection, account) -> None:
    account_id, _ = account
    expected = {"ok": False, "code": "not_found", "error": NO_FILE}
    assert _call(connection, account_id, "read_knowledge", {"name": "no.md"}) == expected
    edit = {"name": "no.md", "find": "a", "replace": "b"}
    assert _call(connection, account_id, "edit_knowledge", edit) == expected
    assert _call(connection, account_id, "delete_knowledge", {"name": "no.md"}) == expected


def test_delete_knowledge_removes_the_file(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "a.md", "x")
    result = _call(connection, account_id, "delete_knowledge", {"name": "a.md"})
    assert result == {"ok": True, "name": "a.md"}
    assert get_file(connection, account_id, "a.md") is None


def test_another_account_sees_nothing(connection, account, other) -> None:
    account_id, _ = account
    other_id, _ = other
    put_file(connection, account_id, "secret.md", "The passport is blue.")
    missing = {"ok": False, "code": "not_found", "error": NO_FILE}
    assert _call(connection, other_id, "read_knowledge", {"name": "secret.md"}) == missing
    edit = {"name": "secret.md", "find": "blue", "replace": "red"}
    assert _call(connection, other_id, "edit_knowledge", edit) == missing
    assert _call(connection, other_id, "delete_knowledge", {"name": "secret.md"}) == missing
    assert _call(connection, other_id, "search_knowledge", {"query": "passport"})["matches"] == []
    assert _call(connection, other_id, "list_knowledge", {})["files"] == []
    assert _call(connection, other_id, "delete_profile", {}) == {"ok": True, "deleted": 0}
    written = _call(connection, other_id, "write_knowledge", {"name": "secret.md", "body": "mine"})
    assert written["ok"] is True
    assert get_file(connection, account_id, "secret.md")[3] == "The passport is blue."


def test_a_sixth_file_in_one_turn_is_refused(connection, account) -> None:
    account_id, _ = account
    for number in range(1, 7):
        put_file(connection, account_id, f"f{number}.md", f"text {number}")
    put_file(connection, account_id, "profile.md", "Answer briefly.")
    turn = Turn(opened=set())
    context = Context(None, turn)
    for number in range(1, 6):
        read = _call(connection, account_id, "read_knowledge", {"name": f"f{number}.md"}, context)
        assert read["ok"] is True
    assert _call(connection, account_id, "read_knowledge", {"name": "f1.md"}, context)["ok"]
    assert _call(connection, account_id, "read_knowledge", {"name": "profile.md"}, context)["ok"]
    sixth = _call(connection, account_id, "read_knowledge", {"name": "f6.md"}, context)
    assert sixth == {"ok": False, "code": "limit", "error": TOO_MANY}
    fresh = Context(None, Turn(opened=set()))
    assert _call(connection, account_id, "read_knowledge", {"name": "f6.md"}, fresh)["ok"]


def test_a_route_call_has_no_read_limit(connection, account) -> None:
    account_id, _ = account
    for number in range(7):
        put_file(connection, account_id, f"f{number}.md", "x")
    for number in range(7):
        read = _call(connection, account_id, "read_knowledge", {"name": f"f{number}.md"})
        assert read["ok"] is True


def test_delete_profile_removes_files_and_keeps_abilities(connection, account) -> None:
    account_id, _ = account
    put_file(connection, account_id, "profile.md", "x")
    put_file(connection, account_id, "a.md", "y")
    _call(connection, account_id, "write_ability", {"name": "Tone", "body": "Be brief."})
    assert _call(connection, account_id, "delete_profile", {}) == {"ok": True, "deleted": 2}
    assert _call(connection, account_id, "list_knowledge", {})["files"] == []
    assert len(_call(connection, account_id, "list_abilities", {})["abilities"]) == 1


def test_abilities_create_update_delete_by_id(connection, account) -> None:
    account_id, _ = account
    created = _call(connection, account_id, "write_ability", {"name": " Tone ", "body": "Brief."})
    assert created["ok"] is True
    ability_id = created["ability"]["id"]
    assert created["ability"] == {"id": ability_id, "name": "Tone"}
    updated = _call(
        connection,
        account_id,
        "write_ability",
        {"ability_id": ability_id, "name": "Tone", "body": "Very brief."},
    )
    assert updated["ability"]["id"] == ability_id
    listed = _call(connection, account_id, "list_abilities", {})
    assert listed["abilities"] == [{"id": ability_id, "name": "Tone", "body": "Very brief."}]
    deleted = _call(connection, account_id, "delete_ability", {"ability_id": ability_id})
    assert deleted == {"ok": True, "ability_id": ability_id}
    assert _call(connection, account_id, "list_abilities", {})["abilities"] == []


def test_another_accounts_ability_is_not_found(connection, account, other) -> None:
    account_id, _ = account
    other_id, _ = other
    created = _call(connection, account_id, "write_ability", {"name": "Tone", "body": "Brief."})
    ability_id = created["ability"]["id"]
    missing = {"ok": False, "code": "not_found", "error": NO_ABILITY}
    update = {"ability_id": ability_id, "name": "Tone", "body": "Long."}
    assert _call(connection, other_id, "write_ability", update) == missing
    assert _call(connection, other_id, "delete_ability", {"ability_id": ability_id}) == missing
    assert _call(connection, other_id, "list_abilities", {})["abilities"] == []
    assert _call(connection, account_id, "list_abilities", {})["abilities"][0]["body"] == "Brief."
    unknown = {"ability_id": str(uuid.uuid4())}
    assert _call(connection, account_id, "delete_ability", unknown) == missing


def test_bad_arguments_are_refused_not_raised(connection, account) -> None:
    account_id, _ = account
    for operation, arguments in (
        ("read_knowledge", {}),
        ("write_knowledge", {"name": "a.md"}),
        ("write_knowledge", {"name": "a.md", "body": "b", "account_id": str(account_id)}),
        ("delete_ability", {"ability_id": "nope"}),
        ("list_knowledge", {"extra": 1}),
    ):
        result = _call(connection, account_id, operation, arguments)
        assert result["ok"] is False
        assert result["code"] == "invalid"


def test_memory_tools_are_agent_tools_without_an_account_id() -> None:
    specs = {spec.name: spec for spec in agent_tools()}
    assert MEMORY_TOOLS <= set(specs)
    for name in MEMORY_TOOLS:
        assert "account_id" not in json.dumps(specs[name].schema)
        assert specs[name].description != ""
