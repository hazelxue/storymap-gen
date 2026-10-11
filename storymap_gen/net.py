"""A small, defensive HTTP layer for fetching public data.

Every download goes through here. It protects against: requests to private or loopback addresses (SSRF), redirects to
somewhere else, responses of unbounded size, hung connections, and credentials embedded in a URL.

Residual risk: the host name is resolved and checked before the request, but a hostile DNS server could answer differently
for the real connection (DNS rebinding). Only fetch from sources you trust; the MCP server only uses fixed, built-in hosts.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

USER_AGENT = "storymap-gen/0.3 (+https://github.com/hazelxue/storymap-gen)"
DEFAULT_TIMEOUT = (10, 90)  # connect, read


class NetworkError(RuntimeError):
    """A download failed, was refused, or returned something unusable."""


def max_download_bytes() -> int:
    return int(float(os.environ.get("STORYMAP_MAX_DOWNLOAD_MB", "100")) * 1024 * 1024)


def assert_public_https_url(url: str, allowed_hosts: Iterable[str] | None = None) -> str:
    """Return ``url`` if it is a plain https URL to a public host, else raise ``NetworkError``."""
    if len(url) > 2000:
        raise NetworkError("the URL is too long")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise NetworkError("only https URLs are allowed")
    if parts.username or parts.password:
        raise NetworkError("URLs with embedded credentials are not allowed")
    if parts.port not in (None, 443):
        raise NetworkError("only the default https port is allowed")
    if not host:
        raise NetworkError("the URL has no host")
    if allowed_hosts is not None and not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise NetworkError(f"host '{host}' is not on the allowed list for this source")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise NetworkError(f"could not resolve '{host}'") from None
    for info in infos:
        if not _is_public(ipaddress.ip_address(info[4][0])):
            raise NetworkError(f"'{host}' resolves to a non-public address; refusing to connect")
    return url


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for ordinary routable internet addresses (not private, loopback, link-local, multicast, reserved, or unspecified)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # ::ffff:10.0.0.1 is the private address 10.0.0.1 in disguise
    return bool(ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                      or ip.is_unspecified or ip.is_private))


def request_bytes(method: str, url: str, *, params: Mapping[str, Any] | None = None, data: Mapping[str, Any] | None = None,
                  headers: Mapping[str, str] | None = None, timeout: tuple[float, float] = DEFAULT_TIMEOUT,
                  max_bytes: int | None = None, allowed_hosts: Iterable[str] | None = None) -> bytes:
    """Make one request and return the body, enforcing the protections described in the module docstring."""
    assert_public_https_url(url, allowed_hosts)
    cap = max_bytes or max_download_bytes()
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        with requests.request(method, url, params=params, data=data, headers=h, timeout=timeout, stream=True,
                              allow_redirects=False) as resp:
            if 300 <= resp.status_code < 400:
                raise NetworkError("the server redirected the request; redirects are not followed")
            if resp.status_code != 200:
                raise NetworkError(f"the server answered HTTP {resp.status_code}")
            declared = resp.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > cap:
                raise NetworkError(f"the response is larger than {cap // (1024 * 1024)} MB")
            chunks, size = [], 0
            for chunk in resp.iter_content(chunk_size=1 << 16):
                size += len(chunk)
                if size > cap:
                    raise NetworkError(f"the response is larger than {cap // (1024 * 1024)} MB")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.exceptions.RequestException as e:
        raise NetworkError(f"network error ({type(e).__name__})") from None
