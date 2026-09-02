"""Fetch a URL and extract its link-preview metadata (title/desc/image).

Security: this fetches arbitrary user-supplied URLs server-side, so it is
a classic SSRF surface. Guards: http(s) only, DNS is resolved and every
resolved IP is checked against private/loopback/link-local/reserved
ranges, redirects are followed manually so each hop is re-validated, the
response is size-capped, and everything runs under a short timeout.
"""

import asyncio
import html
import ipaddress
import re
import socket
from contextlib import aclosing
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = 6.0
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 4
_UA = "NotabulaBot/1.0 (+https://github.com/; link preview)"
_HEADERS = {"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"}


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _pick_public_ip(infos) -> str | None:
    """From getaddrinfo results, ONE public IP — or None if ANY resolved
    address is private/loopback/etc. Returning the address we actually
    connect to is what closes the DNS-rebinding window: the caller connects
    to this pinned IP, not a second, attacker-controlled re-resolution."""
    chosen: str | None = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_is_public(ip):
            return None  # any bad address disqualifies the host
        if chosen is None:
            chosen = str(ip)
    return chosen


def _resolve_public_ip(host: str) -> str | None:
    """Synchronous resolve — for is_safe_url (validation-only callers)."""
    try:
        return _pick_public_ip(socket.getaddrinfo(host, None))
    except (socket.gaierror, OSError):
        return None


async def _resolve_public_ip_async(host: str) -> str | None:
    """The fetch path resolves on the loop's executor: a blocking
    getaddrinfo can hang the whole server for the resolver timeout."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        return None
    return _pick_public_ip(infos)


def is_safe_url(url: str) -> bool:
    p = urlparse(url)
    return (
        p.scheme in ("http", "https")
        and bool(p.hostname)
        and _resolve_public_ip(p.hostname) is not None
    )


def _meta(html_text: str, prop: str) -> str | None:
    """Read a <meta property|name="prop" content="..."> value."""
    tag = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]*>',
        html_text,
        re.I,
    )
    if not tag:
        return None
    content = re.search(r'content=["\']([^"\']*)["\']', tag.group(0), re.I)
    if not content:
        return None
    value = html.unescape(content.group(1)).strip()
    return value or None


def _parse(html_text: str, base_url: str) -> dict:
    title = _meta(html_text, "og:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
        if m:
            title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) or None
    description = _meta(html_text, "og:description") or _meta(html_text, "description")
    image = _meta(html_text, "og:image") or _meta(html_text, "twitter:image")
    if image:
        image = urljoin(base_url, image)
    site_name = _meta(html_text, "og:site_name")
    return {
        "title": (title or "")[:500] or None,
        "description": (description or "")[:1000] or None,
        "image_url": (image or "")[:2048] or None,
        "site_name": (site_name or "")[:200] or None,
    }


async def _read_capped(resp: httpx.Response) -> bytes:
    """Read at most MAX_BYTES of the (decompressed) body, then stop — never
    buffer a whole response. A user-supplied URL can point at a multi-GB
    file or a small gzip that inflates to one; either would otherwise be
    read fully into memory before the old post-hoc slice."""
    chunks: list[bytes] = []
    total = 0
    # aclosing: breaking out early leaves the generator suspended; close it
    # so the underlying stream is released now, not at garbage collection.
    async with aclosing(resp.aiter_bytes()) as body:
        async for chunk in body:
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BYTES:
                break
    return b"".join(chunks)[:MAX_BYTES]


async def fetch_preview(url: str) -> dict | None:
    """Return preview metadata, or None if the URL is unsafe/unreachable
    or serves no HTML."""
    current = url
    text: str | None = None
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=False, headers=_HEADERS
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(current)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            pinned_ip = await _resolve_public_ip_async(parsed.hostname)
            if pinned_ip is None:
                return None
            # Connect to the validated IP, but keep the real Host header and
            # TLS SNI so virtual hosts and cert verification still work — and
            # so a rebind between validation and connection can't happen.
            host_ip = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
            netloc = f"{host_ip}:{parsed.port}" if parsed.port else host_ip
            ip_url = parsed._replace(netloc=netloc).geturl()
            try:
                async with client.stream(
                    "GET",
                    ip_url,
                    headers={"Host": parsed.netloc},
                    extensions={"sni_hostname": parsed.hostname},
                ) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue  # closes this response; re-validate the hop
                    if "html" not in resp.headers.get("content-type", "").lower():
                        return None
                    raw = await _read_capped(resp)
                    text = raw.decode(resp.encoding or "utf-8", errors="replace")
            except httpx.HTTPError:
                return None
            break
    if text is None:
        return None  # redirect chain never settled

    data = _parse(text, current)
    # A preview with nothing to show isn't worth caching as a hit.
    if not (data["title"] or data["description"] or data["image_url"]):
        return None
    return data
