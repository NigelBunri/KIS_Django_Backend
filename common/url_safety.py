"""Shared helper to guard outbound HTTP requests to user-influenced URLs
against SSRF (server-side request forgery).

Used anywhere the server makes an outbound request to a URL that a client
supplied or influenced (webhook targets, link-preview fetches, WHIP
ingest URLs, etc.) - without this, a caller can point the server at
internal-only services (admin panels, other microservices) or the cloud
metadata endpoint (169.254.169.254) and read back the response.

This is intentionally stdlib-only (urllib.parse / socket / ipaddress) so it
has no import-time dependency on Django settings or any HTTP client library,
and can be safely imported from anywhere (views, services, management
commands) without risking circular imports.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Schemes we ever want to let the server fetch on a caller's behalf.
DEFAULT_ALLOWED_SCHEMES = ("http", "https")

# Hostnames that resolve locally / to the machine itself - blocked even
# though "localhost" doesn't always show up as a loopback IP literal on
# every platform's resolver.
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the SSRF safety check."""


def _is_disallowed_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers the 169.254.0.0/16 cloud metadata range
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_safe_external_url(url: str, allowed_schemes=DEFAULT_ALLOWED_SCHEMES) -> bool:
    """Return True only if `url` is http(s) and every address its host
    resolves to is a routable, public address (not loopback/private/
    link-local/reserved/multicast). Fails closed: any parse/DNS error, or
    an empty hostname list, is treated as unsafe.
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() not in allowed_schemes:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False

    # Literal IP in the URL - validate directly without a DNS round trip.
    try:
        literal_ip = ipaddress.ip_address(hostname)
        return not _is_disallowed_ip(literal_ip)
    except ValueError:
        pass  # not a literal IP - fall through to DNS resolution below

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except Exception:
        return False

    if not addr_infos:
        return False

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        # DNS rebinding guard: every resolved address must be safe, not just
        # the first one, since the caller has no control over which address
        # the eventual HTTP client picks to connect to.
        if _is_disallowed_ip(ip_obj):
            return False

    return True


def assert_safe_external_url(url: str, allowed_schemes=DEFAULT_ALLOWED_SCHEMES) -> None:
    """Same check as is_safe_external_url(), raising UnsafeUrlError instead
    of returning a bool, for call sites that want to fail fast.
    """
    if not is_safe_external_url(url, allowed_schemes=allowed_schemes):
        raise UnsafeUrlError(f"URL is not allowed: {url!r}")
