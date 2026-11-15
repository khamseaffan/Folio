import socket

import pytest
from app.urlcheck import BadAddress, is_public_ip, normalize_url, resolve_public


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "100.64.0.1",
        "100.127.255.254",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd12:3456::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "224.0.0.1",
        "ff02::1",
        "240.0.0.1",
        "255.255.255.255",
        "64:ff9b::7f00:1",
        "2002:7f00:1::",
        "not an address",
        "",
    ],
)
def test_private_and_odd_addresses_are_not_public(ip):
    assert is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_public(ip):
    assert is_public_ip(ip) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://x",
        "data:text/html,x",
        "http://user:pw@host/",
        "http://user@host/",
        "http://host:8080/",
        "http://host:22/",
        "javascript:alert(1)",
        "chrome://settings",
        "http:///nohost",
        "",
        "   ",
        "https://example.com/" + "a" * 3000,
        "https://exam ple.com/",
        "https://example.com/\nx",
    ],
)
def test_bad_addresses_are_refused(url):
    with pytest.raises(BadAddress):
        normalize_url(url)


def test_an_address_is_normalized():
    assert normalize_url("HTTPS://Example.COM:443/a?b=1#frag") == "https://example.com/a?b=1"
    assert normalize_url("http://example.com:80") == "http://example.com"
    assert normalize_url("http://example.com:443/x") == "http://example.com:443/x"
    assert normalize_url("https://example.com/p") == "https://example.com/p"


def stub(answers):
    def resolver(host, port=None, *args, **kwargs):
        if host not in answers:
            raise socket.gaierror("no such name")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in answers[host]]

    return resolver


def test_a_name_with_a_public_answer_passes():
    assert resolve_public("a.test", stub({"a.test": ["8.8.8.8"]})) == ["8.8.8.8"]


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["8.8.8.8", "10.0.0.5"],
        ["10.0.0.5", "8.8.8.8"],
        [],
    ],
)
def test_a_name_with_any_private_answer_or_none_is_refused(answers):
    with pytest.raises(BadAddress):
        resolve_public("a.test", stub({"a.test": answers}))


def test_a_name_that_does_not_resolve_is_refused():
    with pytest.raises(BadAddress):
        resolve_public("missing.test", stub({}))


@pytest.mark.parametrize("spelling", ["2130706433", "0x7f.1", "0177.0.0.1", "127.1"])
def test_other_spellings_of_loopback_are_refused_by_the_resolved_answer(spelling):
    # ipaddress cannot parse these, so the resolved answer is what is checked.
    with pytest.raises(BadAddress):
        resolve_public(spelling, stub({spelling: ["127.0.0.1"]}))


def test_the_real_resolver_turns_odd_spellings_into_a_refusal():
    # Octal is left out: the system resolver reads 0177.0.0.1 as decimal on some platforms.
    # Chrome writes the canonical address into the request. The stub test covers the octal answer.
    for spelling in ["2130706433", "0x7f.1", "localhost"]:
        with pytest.raises(BadAddress):
            resolve_public(spelling)


def test_a_scope_id_on_an_ipv6_answer_is_ignored():
    def resolver(host, port=None, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2))]

    with pytest.raises(BadAddress):
        resolve_public("a.test", resolver)
