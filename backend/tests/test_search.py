import json
import logging

import pytest
from app.search import FakeSearch, SearchError, SearchResult, TavilySearch, open_search

KEY = "tvly-secret-key-1234"


def _answer(status: int, body: object):
    text = body if isinstance(body, str) else json.dumps(body)

    def post(url, headers, body_bytes, timeout):
        return status, text

    return post


def _items(*urls: str) -> dict:
    return {
        "results": [
            {"title": f"T{i}", "url": url, "content": f"C{i}", "score": 0.5}
            for i, url in enumerate(urls)
        ]
    }


def test_normal_answer_maps_to_results() -> None:
    seen: dict = {}

    def post(url, headers, body_bytes, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body_bytes), timeout=timeout)
        return 200, json.dumps(_items("https://a.example/x", "http://b.example/y"))

    found = TavilySearch(KEY, post).search("rust async", 5)
    assert found == [
        SearchResult("T0", "https://a.example/x", "C0"),
        SearchResult("T1", "http://b.example/y", "C1"),
    ]
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["headers"]["Authorization"] == f"Bearer {KEY}"
    assert seen["body"] == {
        "query": "rust async",
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
    }
    assert seen["timeout"] == 15


def test_non_web_addresses_and_duplicates_are_dropped_and_limit_applies() -> None:
    body = _items(
        "javascript:alert(1)",
        "https://a.example/1",
        "ftp://a.example/2",
        "https://a.example/1",
        "https://a.example/3",
        "https://a.example/4",
    )
    found = TavilySearch(KEY, _answer(200, body)).search("q", 2)
    assert [r.url for r in found] == ["https://a.example/1", "https://a.example/3"]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_error_gives_the_status_sentence(status: int) -> None:
    with pytest.raises(SearchError) as caught:
        TavilySearch(KEY, _answer(status, {"detail": KEY})).search("q")
    assert str(caught.value) == f"Search failed with status {status}."


def test_timeout_and_network_error_say_unreachable() -> None:
    for error in (TimeoutError(f"slow {KEY}"), OSError(f"down {KEY}")):

        def post(url, headers, body_bytes, timeout, error=error):
            raise error

        with pytest.raises(SearchError) as caught:
            TavilySearch(KEY, post).search("q")
        assert str(caught.value) == "Search could not be reached."
        assert KEY not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", json.dumps({"results": "no"}), json.dumps({"results": [1]})],
)
def test_unreadable_body_gives_the_unreadable_sentence(body: str) -> None:
    with pytest.raises(SearchError) as caught:
        TavilySearch(KEY, _answer(200, body)).search("q")
    assert str(caught.value) == "Search returned something unreadable."


def test_key_never_reaches_a_log_record(caplog: pytest.LogCaptureFixture) -> None:
    def post(url, headers, body_bytes, timeout):
        raise OSError(f"connection failed, sent {headers['Authorization']}")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SearchError):
            TavilySearch(KEY, post).search("q")
        with pytest.raises(SearchError):
            TavilySearch(KEY, _answer(401, {"detail": KEY})).search("q")
    assert caplog.records
    for record in caplog.records:
        assert KEY not in record.getMessage()
        assert KEY not in repr(record.args)
        assert KEY not in (record.exc_text or "")


def test_open_search_without_key_says_not_configured(monkeypatch) -> None:
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        else:
            monkeypatch.setenv("TAVILY_API_KEY", value)
        with pytest.raises(SearchError) as caught:
            open_search().search("q")
        assert str(caught.value) == "Web search is not configured."


def test_open_search_with_key_returns_tavily(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", f" {KEY} ")
    assert isinstance(open_search(), TavilySearch)


def test_fake_search_records_queries_and_can_fail() -> None:
    one = SearchResult("t", "https://a.example", "s")
    fake = FakeSearch([one])
    assert fake.search("a") == [one]
    assert fake.queries == ["a"]
    broken = FakeSearch(error=SearchError("down"))
    with pytest.raises(SearchError):
        broken.search("b")
    assert broken.queries == ["b"]
