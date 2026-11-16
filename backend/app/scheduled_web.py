"""Search and page reading for a scheduled run, with a small budget. It keeps the rules of deep
research: only an address that was shown can be opened, page text goes only to a call with no
tools, and the title and address of a page come from the page. The worker has no Chrome, so a
page is fetched with a plain request through the same proxy that refuses private destinations."""

import json
import logging
import time
from collections.abc import Callable

from app import operations
from app.browser import OpenedPage, PageError, PageOpener
from app.http_page import HttpPageOpener
from app.model import Block, Model, Reply, ToolCall, ToolSpec
from app.search import Search, SearchError, open_search
from app.urlcheck import BadAddress, normalize_url
from app.web import (
    NOT_SHOWN,
    OPEN_SPEC,
    SEARCH_RESULTS,
    SEARCH_SPEC,
    clean,
    digest_page,
    error,
    page_links,
    page_title,
    shown_results,
)

MAX_SEARCHES = 6
MAX_PAGES = 8
READ_SECONDS = 5 * 60

_log = logging.getLogger("folio.worker")
_clock = time.monotonic
_LIMITS = {
    "time": f"The reading time of {READ_SECONDS // 60} minutes for this run is reached.",
    "searches": f"The limit of {MAX_SEARCHES} searches for this run is reached.",
    "pages": f"The limit of {MAX_PAGES} pages for this run is reached.",
}


class ScheduledWeb:
    def __init__(
        self,
        task: str,
        model: Model,
        replies: list[Reply],
        record_search: Callable[[], None],
        search: Search | None = None,
        opener: PageOpener | None = None,
    ) -> None:
        self.tools: tuple[ToolSpec, ...] = (SEARCH_SPEC, OPEN_SPEC)
        self._task = task
        self._model = model
        self._replies = replies
        self._record_search = record_search
        self._search = search
        self._opener = opener
        self._own_opener = opener is None
        self._started = _clock()
        self._allowed: set[str] = set()
        self._opened: dict[str, str] = {}
        self._searches = 0
        self._pages = 0

    def handles(self, name: str) -> bool:
        return name in ("search_web", "open_page")

    def close(self) -> None:
        # Only an opener made here is closed. One that was handed in belongs to the caller.
        if self._own_opener and self._opener is not None:
            try:
                self._opener.close()
            except Exception as problem:
                _log.error("scheduled web did not close: %s", type(problem).__name__)
            self._opener = None

    def call(self, call: ToolCall) -> str:
        if call.unreadable:
            return json.dumps({"ok": False, "code": "invalid", "error": operations.BAD_INPUT})
        if call.name == "search_web":
            return self._do_search(call.arguments)
        return self._do_open(call.arguments)

    def _over_time(self) -> bool:
        return _clock() - self._started >= READ_SECONDS

    def _do_search(self, arguments: dict[str, object]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or query.strip() == "" or "\x00" in query:
            return error("Give the words to search for.")
        if self._over_time():
            return error(_LIMITS["time"])
        if self._searches >= MAX_SEARCHES:
            return error(_LIMITS["searches"])
        self._searches += 1
        try:
            if self._search is None:
                self._search = open_search()
            found = self._search.search(query.strip(), SEARCH_RESULTS)
        except SearchError as problem:
            return error(str(problem))
        finally:
            self._record_search()
        return json.dumps({"ok": True, "results": shown_results(found, self._allowed)})

    def _do_open(self, arguments: dict[str, object]) -> str:
        raw = arguments.get("url")
        address: str | None = None
        if isinstance(raw, str):
            try:
                address = normalize_url(raw)
            except BadAddress:
                address = None
        if address is None or address not in self._allowed:
            return error(NOT_SHOWN)
        if address in self._opened:
            return self._opened[address]
        if self._over_time():
            return error(_LIMITS["time"])
        if self._pages >= MAX_PAGES:
            return error(_LIMITS["pages"])
        # An attempt counts even when the page cannot be read, so a run cannot keep trying.
        self._pages += 1
        try:
            if self._opener is None:
                self._opener = HttpPageOpener()
            page = self._opener.open(address)
        except PageError as problem:
            return error(str(problem))
        return self._read(address, page)

    def _read(self, address: str, page: OpenedPage) -> str:
        try:
            digest = digest_page(self._complete, self._task, page)
        except Exception as problem:
            _log.error("a page digest failed: %s", type(problem).__name__)
            return error("The page could not be read.")
        result = json.dumps(
            {
                "ok": True,
                "source": self._pages,
                "title": page_title(page),
                "url": clean(page.url),
                "digest": digest,
                "links": page_links(page, self._allowed),
            }
        )
        self._opened[address] = result
        try:
            self._opened[normalize_url(page.url)] = result
        except BadAddress:
            pass
        return result

    def _complete(self, prompt: str, blocks: list[Block], tools: tuple[ToolSpec, ...]) -> Reply:
        try:
            reply = self._model.complete(prompt, blocks, tools)
        except Exception:
            # The call was sent, so it still gets a usage row, with its counts missing.
            self._replies.append(Reply("", None, None, None))
            raise
        self._replies.append(reply)
        return reply
