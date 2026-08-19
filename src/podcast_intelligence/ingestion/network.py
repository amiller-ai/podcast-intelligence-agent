"""Shared public-network destination validation for guarded ingestion."""

import socket
from collections.abc import Callable, Sequence
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import urlsplit

type HostResolver = Callable[[str, int], Sequence[str]]


class PublicHttpDestinationError(ValueError):
    """Raised when a URL or resolved address is not a safe public destination."""


def validate_public_http_destination(
    url: str,
    *,
    resolver: HostResolver,
    resource_name: str,
) -> None:
    """Require an HTTP(S) URL whose complete DNS result is publicly routable."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise PublicHttpDestinationError(f"{resource_name} URL is invalid") from error

    if parsed.scheme.lower() not in {"http", "https"}:
        raise PublicHttpDestinationError(f"{resource_name} URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise PublicHttpDestinationError(
            f"{resource_name} URL must not contain embedded credentials"
        )
    if parsed.hostname is None:
        raise PublicHttpDestinationError(f"{resource_name} URL must include a host")

    effective_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    addresses = _destination_addresses(parsed.hostname, effective_port, resolver=resolver)
    if not addresses:
        raise PublicHttpDestinationError(
            f"{resource_name} destination did not resolve to an IP address"
        )
    if any(not _is_safe_public_address(address) for address in addresses):
        raise PublicHttpDestinationError(
            f"{resource_name} destination must resolve only to public IP addresses"
        )


def resolve_host(host: str, port: int) -> tuple[str, ...]:
    """Resolve a host to unique stream-socket addresses."""

    results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(result[4][0]) for result in results))


def _destination_addresses(host: str, port: int, *, resolver: HostResolver) -> Sequence[str]:
    try:
        return (str(ip_address(host.split("%", maxsplit=1)[0])),)
    except ValueError:
        try:
            return resolver(host, port)
        except OSError as error:
            raise PublicHttpDestinationError("destination host could not be resolved") from error


def _is_safe_public_address(address: str) -> bool:
    try:
        parsed: IPv4Address | IPv6Address = ip_address(address.split("%", maxsplit=1)[0])
    except ValueError:
        return False

    return bool(
        parsed.is_global
        and not parsed.is_loopback
        and not parsed.is_link_local
        and not parsed.is_multicast
        and not parsed.is_private
        and not parsed.is_reserved
        and not parsed.is_unspecified
    )
