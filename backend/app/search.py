import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.model import _without_key

_URL = "https://api.tavily.com/search"
_TIMEOUT_SECONDS = 15
_KEY_ENV = "TAVILY_API_KEY"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(Exception):
    """The message is safe to show to the model and never holds the key."""


class Search(Protocol):
    def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...


def _post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, ""


class TavilySearch:
    def __init__(self, api_key: str, post: Callable[..., Any] | None = None) -> None:
        self._key = api_key
        self._post = post if post is not None else _post

    def _clean(self, text: str) -> str:
        return _without_key(text.replace(self._key, "redacted"))

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        body = json.dumps(
            {
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
            }
        ).encode()
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        try:
            status, text = self._post(_URL, headers, body, _TIMEOUT_SECONDS)
        except Exception as error:
            logger.warning(
                "search request failed: %s: %s", type(error).__name__, self._clean(str(error))
            )
            raise SearchError("Search could not be reached.") from None
        if status < 200 or status >= 300:
            logger.warning("search answered status %s", status)
            raise SearchError(f"Search failed with status {status}.")
        try:
            data = json.loads(text)
            items = data["results"]
            if not isinstance(items, list):
                raise TypeError("results is not a list")
            parsed = [(str(i["title"]), str(i["url"]), str(i["content"])) for i in items]
        except (ValueError, KeyError, TypeError):
            logger.warning("search returned an unreadable body")
            raise SearchError("Search returned something unreadable.") from None
        found: list[SearchResult] = []
        seen: set[str] = set()
        for title, url, snippet in parsed:
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            found.append(SearchResult(title, url, snippet))
        return found[:limit]


class FakeSearch:
    def __init__(
        self, results: Sequence[SearchResult] = (), error: Exception | None = None
    ) -> None:
        self._results = list(results)
        self._error = error
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._results[:limit]


class _NotConfigured:
    def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        raise SearchError("Web search is not configured.")


def open_search() -> Search:
    key = (os.environ.get(_KEY_ENV) or "").strip()
    if key == "":
        return _NotConfigured()
    return TavilySearch(key)
