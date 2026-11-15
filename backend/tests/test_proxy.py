import socket
import threading

import pytest
from app.proxy import SafeProxy
from app.urlcheck import is_public_ip

ORIGIN_BODY = b"hello from the origin"


class Origin:
    """A tiny HTTP server on loopback that records each request it gets."""

    def __init__(self):
        self.requests = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        conn.settimeout(5)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            self.requests.append(data)
            head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n"
            conn.sendall(head % len(ORIGIN_BODY) + ORIGIN_BODY)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def origin():
    server = Origin()
    yield server
    server.close()


def resolver_for(answers, calls=None):
    def resolver(host, port=None, *args, **kwargs):
        if calls is not None:
            calls.append(host)
        if host not in answers:
            raise socket.gaierror("no such name")
        value = answers[host]
        if callable(value):
            value = value()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in value]

    return resolver


def make_proxy(origin, answers, connected=None, resolver=None, is_public=None):
    # Ports 80 and 443 cannot be bound in a test, so the connection goes to the origin's port.
    # The address is still the one the proxy checked.
    def connect(ip, port, timeout):
        if connected is not None:
            connected.append((ip, port))
        return socket.create_connection((ip, origin.port), timeout=timeout)

    proxy = SafeProxy(
        resolver=resolver or resolver_for(answers),
        is_public=is_public or (lambda ip: ip == "127.0.0.1"),
        connect=connect,
    )
    proxy.start()
    return proxy


def talk(port, payload):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(payload)
        out = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                break
            if not chunk:
                break
            out += chunk
        return out


def status_of(response):
    return int(response.split(b" ", 2)[1])


def test_a_plain_get_reaches_the_origin(origin):
    proxy = make_proxy(origin, {"allowed.test": ["127.0.0.1"]})
    try:
        reply = talk(
            proxy.port,
            b"GET http://allowed.test/page?x=1 HTTP/1.1\r\nHost: allowed.test\r\n"
            b"Proxy-Connection: keep-alive\r\n\r\n",
        )
    finally:
        proxy.stop()
    assert status_of(reply) == 200
    assert reply.endswith(ORIGIN_BODY)
    sent = origin.requests[0].decode()
    assert sent.startswith("GET /page?x=1 HTTP/1.1\r\n")
    assert "Proxy-Connection" not in sent


def test_connect_reaches_the_origin_and_passes_bytes_both_ways(origin):
    proxy = make_proxy(origin, {"allowed.test": ["127.0.0.1"]})
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
            sock.sendall(b"CONNECT allowed.test:443 HTTP/1.1\r\nHost: allowed.test:443\r\n\r\n")
            first = sock.recv(4096)
            assert status_of(first) == 200
            sock.sendall(b"GET /inside HTTP/1.1\r\nHost: allowed.test\r\n\r\n")
            out = b""
            while ORIGIN_BODY not in out:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                out += chunk
    finally:
        proxy.stop()
    assert ORIGIN_BODY in out
    assert origin.requests[0].startswith(b"GET /inside")


@pytest.mark.parametrize(
    "host,answer",
    [
        ("localhost", ["127.0.0.1"]),
        ("metadata.test", ["169.254.169.254"]),
        ("internal.test", ["10.0.0.5"]),
        ("v6.test", ["::1"]),
        ("mapped.test", ["::ffff:127.0.0.1"]),
        ("mixed.test", ["8.8.8.8", "10.0.0.5"]),
        ("2130706433", ["127.0.0.1"]),
        ("0x7f.1", ["127.0.0.1"]),
        ("0177.0.0.1", ["127.0.0.1"]),
        ("[::1]", ["::1"]),
    ],
)
def test_a_private_destination_is_refused_by_the_default_check(origin, host, answer):
    connected = []
    proxy = make_proxy(
        origin, {host.strip("[]"): answer}, connected=connected, is_public=is_public_ip
    )
    try:
        via_connect = talk(
            proxy.port, f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
        )
        via_get = talk(proxy.port, f"GET http://{host}/ HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
    finally:
        proxy.stop()
    assert status_of(via_connect) == 403
    assert status_of(via_get) == 403
    assert connected == []
    assert len(proxy.refused) == 2


def test_the_real_resolver_and_check_refuse_localhost_and_the_metadata_address():
    proxy = SafeProxy()
    proxy.start()
    try:
        reply = talk(proxy.port, b"CONNECT localhost:443 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        literal = talk(proxy.port, b"GET http://169.254.169.254/ HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        proxy.stop()
    assert status_of(reply) == 403
    assert status_of(literal) == 403
    assert proxy.refused == ["localhost", "169.254.169.254"]


@pytest.mark.parametrize("port", [22, 8080, 25, 0, 65535])
def test_a_port_other_than_80_and_443_is_refused(origin, port):
    connected = []
    proxy = make_proxy(origin, {"allowed.test": ["127.0.0.1"]}, connected=connected)
    try:
        via_connect = talk(
            proxy.port, f"CONNECT allowed.test:{port} HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )
        via_get = talk(
            proxy.port, f"GET http://allowed.test:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )
    finally:
        proxy.stop()
    assert status_of(via_connect) == 403
    assert status_of(via_get) == 403
    assert connected == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS", "TRACE", "PATCH"])
def test_other_methods_are_not_allowed(origin, method):
    connected = []
    proxy = make_proxy(origin, {"allowed.test": ["127.0.0.1"]}, connected=connected)
    try:
        reply = talk(
            proxy.port,
            f"{method} http://allowed.test/ HTTP/1.1\r\nHost: x\r\n\r\n".encode(),
        )
    finally:
        proxy.stop()
    assert status_of(reply) == 405
    assert connected == []


def test_a_name_that_answers_public_then_private_is_connected_at_the_first_answer(origin):
    answers = iter([["127.0.0.1"], ["10.0.0.5"], ["10.0.0.5"]])
    calls = []
    connected = []
    proxy = make_proxy(
        origin,
        {},
        connected=connected,
        resolver=resolver_for({"rebind.test": lambda: next(answers)}, calls),
    )
    try:
        reply = talk(proxy.port, b"GET http://rebind.test/ HTTP/1.1\r\nHost: rebind.test\r\n\r\n")
    finally:
        proxy.stop()
    assert status_of(reply) == 200
    assert reply.endswith(ORIGIN_BODY)
    assert calls == ["rebind.test"]
    assert connected == [("127.0.0.1", 80)]


def test_every_answer_must_be_public_even_when_the_first_is(origin):
    connected = []
    proxy = make_proxy(origin, {"two.test": ["127.0.0.1", "10.0.0.5"]}, connected=connected)
    try:
        reply = talk(proxy.port, b"GET http://two.test/ HTTP/1.1\r\nHost: two.test\r\n\r\n")
    finally:
        proxy.stop()
    assert status_of(reply) == 403
    assert connected == []
    assert proxy.refused == ["two.test"]


def test_the_proxy_listens_on_loopback_only(origin):
    proxy = make_proxy(origin, {})
    try:
        host, port = proxy.address
        assert host == "127.0.0.1"
        assert port == proxy.port
        assert port > 0
    finally:
        proxy.stop()


def test_a_malformed_request_is_answered_400(origin):
    proxy = make_proxy(origin, {})
    try:
        reply = talk(proxy.port, b"NONSENSE\r\n\r\n")
        relative = talk(proxy.port, b"GET /relative HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        proxy.stop()
    assert status_of(reply) == 400
    assert status_of(relative) == 400


def test_refused_lists_the_hosts_it_refused(origin):
    proxy = make_proxy(origin, {"allowed.test": ["127.0.0.1"], "bad.test": ["10.0.0.1"]})
    try:
        talk(proxy.port, b"GET http://bad.test/a HTTP/1.1\r\nHost: x\r\n\r\n")
        talk(proxy.port, b"GET http://allowed.test/a HTTP/1.1\r\nHost: x\r\n\r\n")
        talk(proxy.port, b"CONNECT bad.test:443 HTTP/1.1\r\nHost: x\r\n\r\n")
    finally:
        proxy.stop()
    assert proxy.refused == ["bad.test", "bad.test"]


def test_a_stopped_proxy_no_longer_accepts():
    proxy = SafeProxy()
    port = proxy.start()
    proxy.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
