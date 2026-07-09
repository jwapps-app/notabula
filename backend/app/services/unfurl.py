"""Fetch a URL and extract its link-preview metadata (title/desc/image).

Security: this fetches arbitrary user-supplied URLs server-side, so it is
a classic SSRF surface. Guards: http(s) only, DNS is resolved and every
resolved IP is checked against private/loopback/link-local/reserved
ranges, redirects are followed manually so each hop is re-validated, the
response is size-capped, and everything runs under a short timeout.
"""

import html
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

TIMEOUT = 6.0
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 4
_UA = "NotabulaBot/1.0 (+https://github.com/; link preview)"


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def is_safe_url(url: str) -> bool:
    p = urlparse(url)
    return (
        p.scheme in ("http", "https")
        and bool(p.hostname)
        and _host_is_public(p.hostname)
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


async def fetch_preview(url: str) -> dict | None:
    """Return preview metadata, or None if the URL is unsafe/unreachable
    or serves no HTML."""
    current = url
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.8"},
    ) as client:
        resp = None
        for _ in range(MAX_REDIRECTS + 1):
            if not is_safe_url(current):
                return None
            try:
                resp = await client.get(current)
            except httpx.HTTPError:
                return None
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            break
        if resp is None or resp.is_redirect:
            return None
        if "html" not in resp.headers.get("content-type", "").lower():
            return None
        text = resp.content[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")

    data = _parse(text, current)
    # A preview with nothing to show isn't worth caching as a hit.
    if not (data["title"] or data["description"] or data["image_url"]):
        return None
    return data
