import select
import socket
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from app.urlcheck import ALLOWED_PORTS, is_public_ip

MAX_CONNECTIONS = 32
IDLE_SECONDS = 60
TOTAL_SECONDS = 120
CONNECT_SECONDS = 10
MAX_HEAD_BYTES = 65_536

_REASONS = {
    200: "Connection established",
    400: "Bad Request",
    403: "Forbidden",
    405: "Method Not Allowed",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def _connect(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout=timeout)


class _Refusal(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


class SafeProxy:
    """An HTTP proxy for the browser. It resolves each name once, requires every answer to be
    public, and connects to an address it checked, so the browser cannot be sent to a private one.
    """

    def __init__(
        self,
        resolver=socket.getaddrinfo,
        is_public: Callable[[str], bool] = is_public_ip,
        connect: Callable[[str, int, float], socket.socket] = _connect,
    ) -> None:
        self._resolver = resolver
        self._is_public = is_public
        self._connect = connect
        self.refused: list[str] = []
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self._stopping = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def address(self) -> tuple[str, int]:
        return ("127.0.0.1", self.port)

    def start(self) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        listener.settimeout(0.2)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._listener is not None:
            self._listener.close()

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                self._reply(client, 503)
                client.close()
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            deadline = time.monotonic() + TOTAL_SECONDS
            client.settimeout(IDLE_SECONDS)
            head = self._read_head(client)
            method, target, version, headers = self._parse(head)
            if method == "CONNECT":
                host, port = self._split_authority(target)
                upstream = self._open(host, port)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                self._pipe(client, upstream, deadline)
            elif method in ("GET", "HEAD"):
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    raise _Refusal(400)
                host = parts.hostname
                port = 80 if parts.port is None else parts.port
                upstream = self._open(host, port)
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                lines = [f"{method} {path} {version}", f"Host: {parts.netloc}"]
                for name, value in headers:
                    lower = name.lower()
                    if (
                        lower == "host"
                        or lower.startswith("proxy-")
                        or lower
                        in (
                            "connection",
                            "keep-alive",
                            "content-length",
                            "transfer-encoding",
                        )
                    ):
                        continue
                    lines.append(f"{name}: {value}")
                lines.append("Connection: close")
                upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
                self._pipe(client, upstream, deadline)
            else:
                raise _Refusal(405)
        except _Refusal as refusal:
            self._reply(client, refusal.status)
        except (OSError, ValueError):
            self._reply(client, 502)
        finally:
            if upstream is not None:
                upstream.close()
            client.close()
            self._slots.release()

    def _read_head(self, client: socket.socket) -> bytes:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                raise _Refusal(400)
            data += chunk
            if len(data) > MAX_HEAD_BYTES:
                raise _Refusal(400)
        return data.split(b"\r\n\r\n", 1)[0]

    def _parse(self, head: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
        lines = head.decode("latin-1").split("\r\n")
        pieces = lines[0].split(" ")
        if len(pieces) != 3 or not pieces[2].startswith("HTTP/"):
            raise _Refusal(400)
        headers = []
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers.append((name.strip(), value.strip()))
        return pieces[0].upper(), pieces[1], pieces[2], headers

    def _split_authority(self, target: str) -> tuple[str, int]:
        host, sep, port = target.rpartition(":")
        if not sep or not port.isdigit():
            raise _Refusal(400)
        return host, int(port)

    def _open(self, host: str, port: int) -> socket.socket:
        if port not in ALLOWED_PORTS:
            self._refuse(host)
        name = host.strip("[]")
        try:
            found = self._resolver(name, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            self._refuse(host)
        addresses = []
        for item in found:
            ip = str(item[4][0]).split("%", 1)[0]
            if ip not in addresses:
                addresses.append(ip)
        if not addresses or not all(self._is_public(ip) for ip in addresses):
            self._refuse(host)
        last: OSError | None = None
        for ip in addresses:
            try:
                return self._connect(ip, port, CONNECT_SECONDS)
            except OSError as error:
                last = error
        raise last or OSError("no address")

    def _refuse(self, host: str) -> None:
        with self._lock:
            self.refused.append(host)
        raise _Refusal(403)

    def _pipe(self, client: socket.socket, upstream: socket.socket, deadline: float) -> None:
        sockets = [client, upstream]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stopping.is_set():
                return
            ready, _, _ = select.select(sockets, [], [], min(IDLE_SECONDS, remaining))
            if not ready:
                return
            for source in ready:
                data = source.recv(65_536)
                if not data:
                    return
                (upstream if source is client else client).sendall(data)

    def _reply(self, client: socket.socket, status: int) -> None:
        try:
            head = f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
            client.sendall((head + "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
        except OSError:
            pass
