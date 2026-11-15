import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "FolioResearch/0.1"
MAX_ROBOTS_BYTES = 500_000
FETCH_TIMEOUT_SECONDS = 10


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
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )

    def fetch(url: str) -> tuple[int, str]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_ROBOTS_BYTES)
                return response.status, raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            return error.code, ""

    return fetch
