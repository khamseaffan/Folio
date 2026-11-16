import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.browser import MAX_LINKS, MAX_PAGE_CHARS, PageError
from app.http_page import HttpPageOpener
from app.proxy import SafeProxy
from app.robots import Robots, fetch_through_proxy

PAGE = "<html><head><title>{title}</title>{head}</head><body>{body}</body></html>"
LONG_TEXT = "The quick brown fox jumps over the lazy dog. " * 3


class Handler(BaseHTTPRequestHandler):
    log: list[str] = []
    headers_seen: list[dict[str, str]] = []

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
        self.headers_seen.append({k.lower(): v for k, v in self.headers.items()})
        if path == "/plain":
            body = f"<p>{LONG_TEXT}</p>"
            head = "<style>.hidden{color:red}</style>"
            body += "<script>var secret='SCRIPT-TEXT';</script><noscript>NOSCRIPT-TEXT</noscript>"
            self.send(200, PAGE.format(title="Plain  Title", head=head, body=body))
        elif path == "/redirect":
            self.send(302, "", extra={"Location": "/plain"})
        elif path == "/loop":
            self.send(302, "", extra={"Location": "/loop"})
        elif path == "/to-private":
            self.send(302, "", extra={"Location": "http://private.test/plain"})
        elif path == "/missing":
            self.send(404, PAGE.format(title="Gone", head="", body=LONG_TEXT))
        elif path == "/image":
            self.send(200, b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
        elif path == "/empty":
            self.send(200, PAGE.format(title="Empty", head="", body=""))
        elif path == "/plaintext":
            self.send(200, LONG_TEXT, "text/plain")
        elif path == "/huge":
            body = "<p>" + "word " * 40_000 + "</p>"
            self.send(200, PAGE.format(title="Huge", head="", body=body))
        elif path == "/enormous":
            self.send(200, "<p>" + "x" * 5_000_000 + "</p>")
        elif path == "/links":
            links = "".join(f'<a href="/l{i}">Link {i}</a> ' for i in range(50))
            links += '<a href="/l0">Again</a><a href="mailto:a@b.c">Mail</a>'
            self.send(200, PAGE.format(title="Links", head="", body=f"<p>{LONG_TEXT}</p>{links}"))
        elif path == "/few":
            links = '<a href="mailto:a@b.c">Mail</a>'
            links += '<a href="http://other.test/x">Other <b>site</b></a>'
            links +='<a href="https://c.example/y">Mail  me</a><a href="javascript:void(0)">J</a>'
            self.send(200, PAGE.format(title="Few", head="", body=f"<p>{LONG_TEXT}</p>{links}"))
        elif path == "/robots.txt":
            self.send(200, "User-agent: *\nDisallow: /blocked\n", "text/plain")
        elif path == "/blocked":
            self.send(200, PAGE.format(title="Blocked", head="", body=LONG_TEXT))
        else:
            self.send(404, "no")


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True


@pytest.fixture(scope="module")
def world():
    Handler.log.clear()
    server = QuietServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin_port = server.server_address[1]
    names = {
        "allowed.test": "127.0.0.1",
        "other.test": "127.0.0.1",
        "private.test": "10.0.0.5",
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
    opener = HttpPageOpener(proxy=proxy, robots=Robots(fetch_through_proxy(proxy.port)))
    yield opener, proxy
    opener.close()
    proxy.stop()
    server.shutdown()


def url(path):
    return f"http://allowed.test{path}"


def test_a_plain_page_gives_its_title_and_readable_text_only(world):
    opener, _ = world
    page = opener.open(url("/plain"))
    assert page.title == "Plain Title"
    assert "quick brown fox" in page.text
    assert "SCRIPT-TEXT" not in page.text and "NOSCRIPT-TEXT" not in page.text
    assert "hidden" not in page.text
    assert page.url == url("/plain")


def test_plain_text_is_read(world):
    opener, _ = world
    assert "quick brown fox" in opener.open(url("/plaintext")).text


def test_a_redirect_is_followed_and_the_final_address_is_reported(world):
    opener, _ = world
    page = opener.open(url("/redirect"))
    assert page.url == url("/plain")
    assert page.title == "Plain Title"


def test_a_redirect_loop_gives_up(world):
    opener, _ = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/loop"))
    assert "redirect" in str(caught.value).lower()


def test_a_redirect_to_a_private_address_is_refused(world):
    opener, proxy = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/to-private"))
    assert "not a public destination" in str(caught.value)
    assert "private.test" in proxy.refused


@pytest.mark.parametrize("address", ["http://private.test/plain", "http://169.254.169.254/latest"])
def test_a_private_or_metadata_address_is_refused_and_never_reached(world, address):
    opener, _ = world
    before = len(Handler.log)
    with pytest.raises(PageError) as caught:
        opener.open(address)
    assert "not a public destination" in str(caught.value)
    assert len(Handler.log) == before


@pytest.mark.parametrize(
    "address",
    ["ftp://allowed.test/x", "http://user:pw@allowed.test/x", "http://allowed.test:8080/x", ""],
)
def test_a_bad_address_is_refused_before_any_request(world, address):
    opener, _ = world
    before = len(Handler.log)
    with pytest.raises(PageError):
        opener.open(address)
    assert len(Handler.log) == before


def test_a_robots_disallow_means_the_page_is_never_requested(world):
    opener, _ = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/blocked"))
    assert "robots.txt" in str(caught.value)
    assert "/blocked" not in Handler.log


def test_a_status_error_is_a_plain_sentence(world):
    opener, _ = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/missing"))
    assert "404" in str(caught.value)


def test_something_that_is_not_a_page_is_refused(world):
    opener, _ = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/image"))
    assert "image/png" in str(caught.value)


def test_a_page_with_no_text_is_refused(world):
    opener, _ = world
    with pytest.raises(PageError) as caught:
        opener.open(url("/empty"))
    assert "no readable text" in str(caught.value)


def test_a_long_page_is_cut_to_the_limit(world):
    opener, _ = world
    assert len(opener.open(url("/huge")).text) == MAX_PAGE_CHARS


def test_an_enormous_body_is_cut_and_still_read(world):
    opener, _ = world
    assert len(opener.open(url("/enormous")).text) == MAX_PAGE_CHARS


def test_links_are_resolved_deduplicated_and_limited(world):
    opener, _ = world
    page = opener.open(url("/links"))
    assert len(page.links) == MAX_LINKS
    assert page.links[0].url == url("/l0") and page.links[0].text == "Link 0"
    assert len({link.url for link in page.links}) == len(page.links)
    assert all(link.url.startswith("http") for link in page.links)


def test_a_link_to_another_host_keeps_its_text(world):
    opener, _ = world
    page = opener.open(url("/few"))
    assert [(link.text, link.url) for link in page.links] == [
        ("Other site", "http://other.test/x"),
        ("Mail me", "https://c.example/y"),
    ]


def test_the_request_carries_no_cookie_or_account_header(world):
    opener, _ = world
    Handler.headers_seen.clear()
    opener.open(url("/plain"))
    seen = Handler.headers_seen[-1]
    assert seen["user-agent"].startswith("FolioResearch")
    assert not {"cookie", "authorization", "referer", "x-account", "x-email"} & set(seen)
