from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from app.proxy import SafeProxy
from app.robots import USER_AGENT, Robots, fetch_through_proxy
from app.urlcheck import BadAddress, normalize_url

MAX_PAGE_CHARS = 40_000
MAX_LINKS = 30
MAX_LINK_TEXT = 100
PAGE_TIMEOUT_SECONDS = 30
QUIET_NETWORK_SECONDS = 5
MIN_READABLE_CHARS = 20
READABLE_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(frozen=True)
class Link:
    text: str
    url: str


@dataclass(frozen=True)
class OpenedPage:
    url: str
    title: str
    text: str
    links: tuple[Link, ...]


class PageError(Exception):
    """The message is one sentence that is safe to show to the model."""


class PageOpener(Protocol):
    def open(self, url: str) -> OpenedPage: ...

    def close(self) -> None: ...


class FakePageOpener:
    def __init__(self, pages: Mapping[str, OpenedPage | PageError]) -> None:
        self._pages = pages
        self.opened: list[str] = []

    def open(self, url: str) -> OpenedPage:
        self.opened.append(url)
        page = self._pages.get(url)
        if page is None:
            raise PageError("That page could not be reached.")
        if isinstance(page, PageError):
            raise page
        return page

    def close(self) -> None:
        pass


def _collapse(text: str) -> str:
    return " ".join(text.split())


class ChromePageOpener:
    def __init__(self, proxy: SafeProxy | None = None, robots: Robots | None = None) -> None:
        self._own_proxy = proxy is None
        self._proxy = proxy or SafeProxy()
        self._robots = robots
        self._playwright = None
        self._browser = None

    def _start(self) -> None:
        # Imported here so the rest of the app, and every test that does not need Chrome, loads
        # without Playwright installed.
        from playwright.sync_api import sync_playwright

        if not self._proxy.port:
            self._proxy.start()
        if self._robots is None:
            self._robots = Robots(fetch_through_proxy(self._proxy.port))
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            # Chrome skips a proxy for loopback names unless told not to, which would be a way
            # around it. The sandbox cannot start inside Cloud Run, so the proxy is the boundary.
            proxy={"server": f"http://127.0.0.1:{self._proxy.port}", "bypass": "<-loopback>"},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # WebRTC and QUIC use UDP, which a proxy does not carry, so a page could use them
                # to reach a destination the proxy refuses.
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--disable-quic",
            ],
        )

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        if self._own_proxy:
            self._proxy.stop()

    def open(self, url: str) -> OpenedPage:
        try:
            address = normalize_url(url)
        except BadAddress as error:
            raise PageError(f"{error} It was not opened.") from None
        if self._browser is None:
            self._start()
        assert self._robots is not None and self._browser is not None
        host = (urlsplit(address).hostname or "").strip("[]")
        refused_before = len(self._proxy.refused)
        if not self._robots.allowed(address):
            if host in self._proxy.refused[refused_before:]:
                raise PageError("That address is not a public destination, so it was not opened.")
            raise PageError("The site's robots.txt does not allow reading this page.")
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            accept_downloads=False,
            permissions=[],
            service_workers="block",
        )
        try:
            return self._read(context, address, host, refused_before)
        finally:
            context.close()

    def _read(self, context, address: str, host: str, refused_before: int) -> OpenedPage:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        def route(request_route) -> None:
            request = request_route.request
            if request.resource_type in ("image", "media", "font") or not request.url.startswith(
                ("http://", "https://")
            ):
                request_route.abort()
            else:
                request_route.continue_()

        def refused() -> bool:
            return host in self._proxy.refused[refused_before:]

        context.route("**/*", route)
        page = context.new_page()
        try:
            response = page.goto(
                address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_SECONDS * 1000
            )
            try:
                page.wait_for_load_state("networkidle", timeout=QUIET_NETWORK_SECONDS * 1000)
            except PlaywrightTimeout:
                pass
            if refused():
                raise PageError("That address is not a public destination, so it was not opened.")
            if response is None:
                raise PageError("The page gave no answer.")
            if response.status >= 400:
                raise PageError(f"The page answered with status {response.status}.")
            kind = response.headers.get("content-type", "text/html").split(";")[0].strip().lower()
            if kind not in READABLE_TYPES:
                raise PageError(f"The address is {kind}, not a web page that can be read.")
            text = _collapse(page.evaluate("document.body ? document.body.innerText : ''"))
            if len(text) < MIN_READABLE_CHARS:
                raise PageError("The page has no readable text.")
            raw_links = page.evaluate(
                "Array.from(document.querySelectorAll('a[href]')).slice(0, 1000)"
                ".map(a => [a.innerText || a.textContent || '', a.href])"
            )
            return OpenedPage(
                url=page.url,
                title=_collapse(page.title()),
                text=text[:MAX_PAGE_CHARS],
                links=self._links(raw_links),
            )
        except PageError:
            raise
        except PlaywrightTimeout:
            raise PageError("The page took too long to load.") from None
        except PlaywrightError as error:
            if refused():
                raise PageError(
                    "That address is not a public destination, so it was not opened."
                ) from None
            if "Download is starting" in str(error):
                raise PageError("The address is a file download, not a web page.") from None
            raise PageError("The page could not be reached.") from None

    @staticmethod
    def _links(raw_links) -> tuple[Link, ...]:
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
