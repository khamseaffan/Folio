import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from app import browser
from app.browser import (
    MAX_LINKS,
    MAX_PAGE_CHARS,
    ChromePageOpener,
    FakePageOpener,
    Link,
    OpenedPage,
    PageError,
)
from app.proxy import SafeProxy
from app.robots import Robots, fetch_through_proxy


def _chromium_problem() -> str | None:
    """Why the real-browser tests cannot run here, or None. They skip with this reason."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright is not installed (uv sync --group research)"
    try:
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                return "Chromium is not installed (uv run playwright install chromium)"
    except Exception as error:
        return f"Playwright cannot start: {error}"
    return None


RELEASE = threading.Event()
PAGE = "<html><head><title>{title}</title></head><body>{body}</body></html>"
LONG_TEXT = "The quick brown fox jumps over the lazy dog. " * 3


class Handler(BaseHTTPRequestHandler):
    log: list[str] = []

    def log_message(self, *args):
        pass

    def send(self, status, body, content_type="text/html; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path
        self.log.append(path)
        if path == "/plain":
            self.send(200, PAGE.format(title="Plain Title", body=f"<p>{LONG_TEXT}</p>"))
        elif path == "/script":
            body = "<div id=x></div><script>document.getElementById('x').textContent="
            body += "'Written by a script after it ran, with enough words to read.'</script>"
            self.send(200, PAGE.format(title="Scripted", body=body))
        elif path == "/redirect":
            self.send(302, "", extra={"Location": "/plain"})
        elif path == "/missing":
            self.send(404, PAGE.format(title="Gone", body=LONG_TEXT))
        elif path == "/image":
            self.send(200, b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
        elif path == "/pdf":
            self.send(200, b"%PDF-1.4 " + b"0" * 200, "application/pdf")
        elif path == "/download":
            self.send(
                200,
                PAGE.format(title="File", body=LONG_TEXT),
                extra={"Content-Disposition": 'attachment; filename="a.html"'},
            )
        elif path == "/empty":
            self.send(200, PAGE.format(title="Empty", body=""))
        elif path == "/hang":
            RELEASE.wait(30)
        elif path == "/huge":
            self.send(200, PAGE.format(title="Huge", body="<p>" + "word " * 40_000 + "</p>"))
        elif path == "/links":
            links = "".join(f'<a href="/l{i}">Link {i}</a> ' for i in range(50))
            links += '<a href="/l0">Again</a><a href="mailto:a@b.c">Mail</a>'
            self.send(200, PAGE.format(title="Links", body=f"<p>{LONG_TEXT}</p>{links}"))
        elif path == "/images":
            body = f'<p>{LONG_TEXT}</p><img src="/pic.png"><video src="/clip.mp4"></video>'
            self.send(200, PAGE.format(title="Images", body=body))
        elif path in ("/pic.png", "/clip.mp4"):
            self.send(200, b"0" * 10, "image/png")
        elif path == "/rogue":
            body = f"<p>{LONG_TEXT}</p>"
            body += '<script src="http://private.test/evil.js"></script>'
            body += '<script src="http://loop.test/evil.js"></script>'
            self.send(200, PAGE.format(title="Rogue", body=body))
        elif path == "/evil.js":
            self.send(200, "document.body.textContent='TAKEN OVER'", "application/javascript")
        elif path == "/robots.txt":
            self.send(200, "User-agent: *\nDisallow: /blocked\n", "text/plain")
        elif path == "/blocked":
            self.send(200, PAGE.format(title="Blocked", body=LONG_TEXT))
        else:
            self.send(404, "no")


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture(scope="module")
def world():
    problem = _chromium_problem()
    if problem:
        pytest.skip(problem)
    server = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin_port = server.server_address[1]

    names = {
        "allowed.test": "127.0.0.1",
        "private.test": "10.0.0.5",
        "loop.test": "127.0.0.9",
        "127.0.0.1": "127.0.0.9",
        "169.254.169.254": "169.254.169.254",
    }

    def resolver(host, port=None, *args, **kwargs):
        ip = names.get(host)
        if ip is None:
            raise socket.gaierror("no such name")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def connect(ip, port, timeout):
        return socket.create_connection((ip, origin_port), timeout=timeout)

    proxy = SafeProxy(resolver=resolver, is_public=lambda ip: ip == "127.0.0.1", connect=connect)
    proxy.start()
    opener = ChromePageOpener(proxy=proxy, robots=Robots(fetch_through_proxy(proxy.port)))
    yield opener, proxy, Handler.log
    opener.close()
    proxy.stop()
    RELEASE.set()
    server.shutdown()


def url(path):
    return f"http://allowed.test{path}"


def test_a_plain_page_gives_its_title_and_text(world):
    opener, _, _ = world
    page = opener.open(url("/plain"))
    assert page.title == "Plain Title"
    assert "quick brown fox" in page.text
    assert page.url == url("/plain")


def test_a_page_written_by_a_script_is_read_after_it_runs(world):
    opener, _, _ = world
    page = opener.open(url("/script"))
    assert "Written by a script" in page.text


def test_a_redirect_is_followed_and_the_final_address_returned(world):
    opener, _, _ = world
    page = opener.open(url("/redirect"))
    assert page.url == url("/plain")
    assert page.title == "Plain Title"


@pytest.mark.parametrize(
    "path,words",
    [
        ("/missing", "404"),
        ("/image", "image/png"),
        ("/pdf", "download"),  # Chrome saves a PDF instead of showing it
        ("/download", "download"),
        ("/empty", "readable text"),
    ],
)
def test_a_page_that_cannot_be_read_is_a_page_error_within_the_limit(world, path, words):
    opener, _, _ = world
    started = time.monotonic()
    with pytest.raises(PageError) as caught:
        opener.open(url(path))
    assert words in str(caught.value)
    assert time.monotonic() - started < 10


def test_a_page_that_never_answers_ends_within_the_time_limit(world, monkeypatch):
    opener, _, _ = world
    monkeypatch.setattr(browser, "PAGE_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    with pytest.raises(PageError) as caught:
        opener.open(url("/hang"))
    assert "too long" in str(caught.value)
    assert time.monotonic() - started < 10


def test_a_huge_page_is_cut(world):
    opener, _, _ = world
    page = opener.open(url("/huge"))
    assert len(page.text) == MAX_PAGE_CHARS


def test_links_are_the_first_thirty_distinct_web_links_in_order(world):
    opener, _, _ = world
    page = opener.open(url("/links"))
    assert len(page.links) == MAX_LINKS
    assert page.links[0] == Link("Link 0", url("/l0"))
    assert page.links[29].url == url("/l29")
    assert all(link.url.startswith("http://allowed.test/") for link in page.links)


def test_images_and_media_are_not_requested(world):
    opener, _, log = world
    log.clear()
    page = opener.open(url("/images"))
    assert "quick brown fox" in page.text
    assert "/pic.png" not in log
    assert "/clip.mp4" not in log


def test_a_script_from_a_private_address_is_not_loaded(world):
    opener, proxy, log = world
    before = len(proxy.refused)
    log.clear()
    page = opener.open(url("/rogue"))
    assert "quick brown fox" in page.text
    assert "TAKEN OVER" not in page.text
    assert "private.test" in proxy.refused[before:]
    assert "loop.test" in proxy.refused[before:]
    assert "/evil.js" not in log


@pytest.mark.parametrize(
    "target", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/"]
)
def test_a_private_address_typed_in_is_refused_by_the_proxy_not_bypassed(world, target):
    opener, proxy, log = world
    before = len(proxy.refused)
    log.clear()
    with pytest.raises(PageError) as caught:
        opener.open(target)
    assert "public" in str(caught.value)
    assert urlsplit(target).hostname in proxy.refused[before:]
    assert log == []


def test_a_robots_disallow_means_the_page_is_never_requested(world):
    opener, _, log = world
    log.clear()
    with pytest.raises(PageError) as caught:
        opener.open(url("/blocked"))
    assert "robots.txt" in str(caught.value)
    assert "/blocked" not in log


def test_a_bad_address_is_a_page_error(world):
    opener, _, _ = world
    for bad in ["file:///etc/passwd", "http://allowed.test:8080/", "javascript:alert(1)"]:
        with pytest.raises(PageError):
            opener.open(bad)


def test_the_fake_opener_returns_its_pages_and_records_the_order():
    page = OpenedPage("https://a.test/", "A", "text " * 10, ())
    fake = FakePageOpener({"https://a.test/": page, "https://b.test/": PageError("It is slow.")})
    assert fake.open("https://a.test/") is page
    with pytest.raises(PageError, match="It is slow."):
        fake.open("https://b.test/")
    with pytest.raises(PageError):
        fake.open("https://c.test/")
    assert fake.opened == ["https://a.test/", "https://b.test/", "https://c.test/"]
    fake.close()


def test_chrome_is_started_so_that_nothing_goes_around_the_proxy(monkeypatch):
    # A page can open WebRTC connections, which use UDP and ignore a proxy, unless Chrome is told
    # not to. The proxy is the only way out, so these must be set.
    import sys
    import types

    launched: dict = {}

    class _Chromium:
        def launch(self, **kwargs):
            launched.update(kwargs)
            return types.SimpleNamespace(close=lambda: None)

    class _Playwright:
        chromium = _Chromium()

        def stop(self):
            pass

    started = types.SimpleNamespace(start=lambda: _Playwright())
    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = lambda: started
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)

    proxy = SafeProxy()
    opener = ChromePageOpener(proxy=proxy, robots=Robots(lambda url: (404, "")))
    try:
        opener._start()
        assert launched["proxy"]["bypass"] == "<-loopback>"
        assert launched["proxy"]["server"] == f"http://127.0.0.1:{proxy.port}"
        assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in launched["args"]
        assert "--disable-quic" in launched["args"]
    finally:
        opener.close()
        proxy.stop()
