import http.client
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from app.urlcheck import normalize_url

USER_AGENT = "FolioResearch/0.1"
MAX_ROBOTS_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 3


class Robots:
    def __init__(
        self, fetch: Callable[[str], tuple[int, str]], agent: str = "FolioResearch"
    ) -> None:
        self._fetch = fetch
        self._agent = agent
        # None means the whole host is disallowed for the life of this object.
        self._hosts: dict[str, RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._hosts:
            self._hosts[origin] = self._load(origin)
        parser = self._hosts[origin]
        return parser is not None and parser.can_fetch(self._agent, url)

    def _load(self, origin: str) -> RobotFileParser | None:
        try:
            status, text = self._fetch(f"{origin}/robots.txt")
        except Exception:
            return None
        parser = RobotFileParser()
        if status == 200:
            parser.parse(text.splitlines())
            return parser
        if 400 <= status < 500 and status != 429:
            parser.parse([])
            return parser
        return None


def fetch_through_proxy(port: int) -> Callable[[str], tuple[int, str]]:
    # Not urllib: its proxy handler skips the proxy for hosts on the system's exception list
    # (on macOS that includes 169.254.0.0/16), which would be a way around the proxy.
    def fetch(url: str) -> tuple[int, str]:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(normalize_url(url))
            https = parts.scheme == "https"
            connection = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(
                "127.0.0.1", port, timeout=FETCH_TIMEOUT_SECONDS
            )
            try:
                headers = {"User-Agent": USER_AGENT, "Connection": "close"}
                if https:
                    connection.set_tunnel(parts.hostname or "", parts.port or 443)
                    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                else:
                    target = url
                connection.request("GET", target, headers=headers)
                response = connection.getresponse()
                location = response.getheader("Location")
                if response.status in (301, 302, 303, 307, 308) and location:
                    url = urljoin(url, location)
                    continue
                raw = response.read(MAX_ROBOTS_BYTES)
                return response.status, raw.decode("utf-8", errors="replace")
            finally:
                connection.close()
        return 0, ""

    return fetch
