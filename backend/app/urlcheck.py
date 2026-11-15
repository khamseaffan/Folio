import ipaddress
import socket
from urllib.parse import urlsplit

MAX_URL_CHARS = 2048
ALLOWED_PORTS = (80, 443)
_DEFAULT_PORT = {"http": 80, "https": 443}
# Forms that carry an IPv4 address inside an IPv6 one, which the standard module does not unwrap.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


class BadAddress(Exception):
    """The message is one sentence that is safe to show to the model."""


def is_public_ip(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public_ip(str(address.ipv4_mapped))
        if address in _NAT64:
            return is_public_ip(str(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)))
        if address in _SIX_TO_FOUR:
            return is_public_ip(str(ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)))
    return address.is_global and not (
        address.is_multicast
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        or address.is_reserved
        or address.is_unspecified
    )


def normalize_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise BadAddress("The address is empty.")
    if len(url) > MAX_URL_CHARS:
        raise BadAddress("The address is too long.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise BadAddress("The address has spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise BadAddress("The address is not a valid web address.") from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORT:
        raise BadAddress("Only http and https addresses can be opened.")
    if not host:
        raise BadAddress("The address has no host.")
    if parts.username is not None or parts.password is not None:
        raise BadAddress("An address with a user name or password cannot be opened.")
    if port is not None and port not in ALLOWED_PORTS:
        raise BadAddress("Only ports 80 and 443 can be opened.")
    shown = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORT[scheme]:
        shown = f"{shown}:{port}"
    path = parts.path
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{shown}{path}{query}"


def resolve_public(host: str, resolver=socket.getaddrinfo) -> list[str]:
    try:
        found = resolver(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise BadAddress("The name could not be resolved.") from None
    addresses: list[str] = []
    for item in found:
        ip = str(item[4][0]).split("%", 1)[0]
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise BadAddress("The name has no address.")
    if not all(is_public_ip(ip) for ip in addresses):
        raise BadAddress("The address is not a public destination.")
    return addresses
