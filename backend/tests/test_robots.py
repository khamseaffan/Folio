import pytest
from app.robots import Robots

FILE = """User-agent: *
Disallow: /private
Disallow: /b

User-agent: FolioResearch
Disallow: /a
"""


def fetcher(status, text="", raises=None):
    calls = []

    def fetch(url):
        calls.append(url)
        if raises:
            raise raises
        return status, text

    fetch.calls = calls
    return fetch


def test_a_disallowed_path_is_refused_and_another_is_allowed():
    robots = Robots(fetcher(200, "User-agent: *\nDisallow: /private\n"))
    assert robots.allowed("https://a.test/private/x") is False
    assert robots.allowed("https://a.test/public") is True


def test_the_folio_token_wins_over_the_star_group():
    robots = Robots(fetcher(200, FILE))
    assert robots.allowed("https://a.test/a") is False
    assert robots.allowed("https://a.test/b") is True
    assert robots.allowed("https://a.test/private") is True


def test_no_file_allows_everything():
    robots = Robots(fetcher(404))
    assert robots.allowed("https://a.test/anything") is True


@pytest.mark.parametrize("status", [500, 503, 429])
def test_a_server_error_or_a_rate_limit_disallows_the_whole_host(status):
    robots = Robots(fetcher(status))
    assert robots.allowed("https://a.test/anything") is False


def test_a_failed_fetch_disallows_the_whole_host():
    robots = Robots(fetcher(0, raises=OSError("down")))
    assert robots.allowed("https://a.test/anything") is False


def test_one_fetch_per_host():
    fetch = fetcher(200, "User-agent: *\nDisallow: /x\n")
    robots = Robots(fetch)
    robots.allowed("https://a.test/1")
    robots.allowed("https://a.test/2")
    robots.allowed("https://b.test/1")
    robots.allowed("http://a.test/1")
    assert fetch.calls == [
        "https://a.test/robots.txt",
        "https://b.test/robots.txt",
        "http://a.test/robots.txt",
    ]


def test_a_failed_fetch_is_not_retried():
    fetch = fetcher(0, raises=OSError("down"))
    robots = Robots(fetch)
    robots.allowed("https://a.test/1")
    robots.allowed("https://a.test/2")
    assert len(fetch.calls) == 1
