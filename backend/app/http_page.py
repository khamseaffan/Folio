"""Opens a page with a plain request, for the worker, which has no Chrome. Every request goes
through the same SafeProxy that Chrome uses, so a private or metadata destination is refused
the same way. A page that needs a script to show its text is read as the text it has."""

import http.client
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.browser import (
    MAX_LINK_TEXT,
    MAX_LINKS,
    MAX_PAGE_CHARS,
    MIN_READABLE_CHARS,
    PAGE_TIMEOUT_SECONDS,
    READABLE_TYPES,
    Link,
    OpenedPage,
    PageError,
    _collapse,
)
from app.proxy import SafeProxy
from app.robots import MAX_REDIRECTS, USER_AGENT, Robots, fetch_through_proxy
from app.urlcheck import BadAddress, normalize_url

MAX_BODY_BYTES = 2_000_000
_SCANNED_LINKS = 1000
_SKIPPED = {"script", "style", "noscript", "template", "svg", "iframe"}
_NOT_PUBLIC = "That address is not a public destination, so it was not opened."


class _Text(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base
        self._skip = 0
        self._in_title = False
        self._anchor: list[str] | None = None
        self._href = ""
        self.title: list[str] = []
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIPPED:
            self._skip += 1
        self.text.append(" ")
        if tag == "a" and self._anchor is None and len(self.links) < _SCANNED_LINKS:
            href = dict(attrs).get("href")
            if href:
                self._anchor = []
                self._href = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED and self._skip > 0:
            self._skip -= 1
        self.text.append(" ")
        if tag == "a" and self._anchor is not None:
            self.links.append((" ".join(self._anchor), urljoin(self._base, self._href)))
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
            return
        if self._skip > 0:
            return
        self.text.append(data)
        if self._anchor is not None:
            self._anchor.append(data)


def _charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip("\"'")
    return "utf-8"


def _decode(body: bytes, content_type: str) -> str:
    try:
        return body.decode(_charset(content_type), errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class HttpPageOpener:
    def __init__(self, proxy: SafeProxy | None = None, robots: Robots | None = None) -> None:
        self._own_proxy = proxy is None
        self._proxy = proxy or SafeProxy()
        self._robots = robots

    def _start(self) -> None:
        if not self._proxy.port:
            self._proxy.start()
        if self._robots is None:
            self._robots = Robots(fetch_through_proxy(self._proxy.port))

    def close(self) -> None:
        if self._own_proxy and self._proxy.port:
            self._proxy.stop()

    def open(self, url: str) -> OpenedPage:
        try:
            address = normalize_url(url)
        except BadAddress as error:
            raise PageError(f"{error} It was not opened.") from None
        self._start()
        assert self._robots is not None
        refused_before = len(self._proxy.refused)
        deadline = time.monotonic() + PAGE_TIMEOUT_SECONDS
        try:
            return self._read(address, refused_before, deadline)
        except PageError:
            raise
        except (OSError, http.client.HTTPException):
            if self._refused_since(refused_before):
                raise PageError(_NOT_PUBLIC) from None
            raise PageError("The page could not be reached.") from None

    def _refused_since(self, refused_before: int) -> bool:
        return len(self._proxy.refused) > refused_before

    def _read(self, address: str, refused_before: int, deadline: float) -> OpenedPage:
        assert self._robots is not None
        for _ in range(MAX_REDIRECTS + 1):
            if not self._robots.allowed(address):
                if self._refused_since(refused_before):
                    raise PageError(_NOT_PUBLIC)
                raise PageError("The site's robots.txt does not allow reading this page.")
            status, headers, body = self._get(address, deadline)
            if self._refused_since(refused_before):
                raise PageError(_NOT_PUBLIC)
            location = headers.get("location")
            if status in (301, 302, 303, 307, 308) and location:
                try:
                    address = normalize_url(urljoin(address, location))
                except BadAddress:
                    raise PageError("The page redirects somewhere that cannot be opened.") from None
                continue
            return self._page(address, status, headers.get("content-type", "text/html"), body)
        raise PageError("The page redirects too many times.")

    def _get(self, address: str, deadline: float) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(address)
        https = parts.scheme == "https"
        timeout = max(1.0, deadline - time.monotonic())
        connection = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(
            "127.0.0.1", self._proxy.port, timeout=timeout
        )
        try:
            # Only a user agent goes out: no cookie, no account name, no email, no id.
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,text/plain",
                "Connection": "close",
            }
            if https:
                connection.set_tunnel(parts.hostname or "", parts.port or 443)
                target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            else:
                target = address
            connection.request("GET", target, headers=headers)
            response = connection.getresponse()
            received = {name.lower(): value for name, value in response.getheaders()}
            body = b""
            while len(body) < MAX_BODY_BYTES:
                if time.monotonic() > deadline:
                    raise PageError("The page took too long to load.")
                chunk = response.read(min(65_536, MAX_BODY_BYTES - len(body)))
                if not chunk:
                    break
                body += chunk
            return response.status, received, body
        except TimeoutError:
            raise PageError("The page took too long to load.") from None
        finally:
            connection.close()

    def _page(self, address: str, status: int, content_type: str, body: bytes) -> OpenedPage:
        if status >= 400:
            raise PageError(f"The page answered with status {status}.")
        kind = content_type.split(";")[0].strip().lower()
        if kind not in READABLE_TYPES:
            raise PageError(f"The address is {kind}, not a web page that can be read.")
        source = _decode(body, content_type)
        if kind == "text/plain":
            text, title, raw_links = _collapse(source), "", []
        else:
            parser = _Text(address)
            parser.feed(source)
            parser.close()
            text = _collapse("".join(parser.text))
            title = _collapse("".join(parser.title))
            raw_links = parser.links
        if len(text) < MIN_READABLE_CHARS:
            raise PageError("The page has no readable text.")
        return OpenedPage(
            url=address, title=title, text=text[:MAX_PAGE_CHARS], links=self._links(raw_links)
        )

    @staticmethod
    def _links(raw_links: list[tuple[str, str]]) -> tuple[Link, ...]:
        links: list[Link] = []
        seen: set[str] = set()
        for text, href in raw_links:
            try:
                link = normalize_url(href)
            except BadAddress:
                continue
            if link in seen:
                continue
            seen.add(link)
            links.append(Link(_collapse(text)[:MAX_LINK_TEXT], link))
            if len(links) == MAX_LINKS:
                break
        return tuple(links)
