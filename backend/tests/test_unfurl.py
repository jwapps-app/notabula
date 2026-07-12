"""Link-unfurl endpoint, caching, SSRF guards, and capture enrichment."""

import app.routers.links as links_router
import app.services.unfurl as unfurl_mod
from app.services.unfurl import _parse, is_safe_url


def test_parse_prefers_opengraph_then_title():
    html = """
    <html><head>
      <title>Fallback Title</title>
      <meta property="og:title" content="Wordle Solver &amp; Helper">
      <meta name="description" content="Solve any Wordle puzzle.">
      <meta property="og:image" content="/img/card.png">
      <meta property="og:site_name" content="Example">
    </head><body>hi</body></html>
    """
    data = _parse(html, "https://example.com/wordle")
    assert data["title"] == "Wordle Solver & Helper"
    assert data["description"] == "Solve any Wordle puzzle."
    assert data["image_url"] == "https://example.com/img/card.png"  # made absolute
    assert data["site_name"] == "Example"


def test_parse_falls_back_to_title_tag():
    data = _parse("<title>  Just   a  Title </title>", "https://x.com")
    assert data["title"] == "Just a Title"
    assert data["description"] is None


def test_ssrf_blocks_private_and_non_http():
    assert is_safe_url("ftp://example.com") is False
    assert is_safe_url("http://localhost/") is False
    assert is_safe_url("http://127.0.0.1/") is False
    assert is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert is_safe_url("http://10.0.0.5/") is False
    assert is_safe_url("https://notarealhostxyz.invalid/") is False


async def test_preview_endpoint_caches(auth, monkeypatch):
    client, alice, _ = auth
    calls = {"n": 0}

    async def fake_fetch(url):
        calls["n"] += 1
        return {
            "title": "Cached Page",
            "description": "desc",
            "image_url": "https://ex.com/i.png",
            "site_name": "Ex",
        }

    monkeypatch.setattr(links_router, "fetch_preview", fake_fetch)

    r1 = await client.get(
        "/api/v1/links/preview?url=https://example.com/a", headers=alice
    )
    assert r1.status_code == 200
    assert r1.json()["title"] == "Cached Page"
    assert r1.json()["ok"] is True

    r2 = await client.get(
        "/api/v1/links/preview?url=https://example.com/a", headers=alice
    )
    assert r2.status_code == 200
    assert calls["n"] == 1  # second call served from cache


async def test_preview_endpoint_requires_auth(auth):
    client, _, _ = auth
    r = await client.get("/api/v1/links/preview?url=https://example.com")
    assert r.status_code == 401


async def test_preview_rejects_bad_scheme(auth):
    client, alice, _ = auth
    r = await client.get(
        "/api/v1/links/preview?url=javascript:alert(1)", headers=alice
    )
    assert r.status_code == 400


async def test_capture_unfurls_a_bare_link(auth, monkeypatch):
    client, alice, _ = auth

    async def fake_fetch(url):
        return {
            "title": "Example Site",
            "description": None,
            "image_url": None,
            "site_name": None,
        }

    monkeypatch.setattr(unfurl_mod, "fetch_preview", fake_fetch)

    cap = (await client.post("/api/v1/auth/capture-token", headers=alice)).json()["token"]
    resp = await client.post(
        f"/api/v1/notes/capture?token={cap}",
        content="https://example.com/some-article",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 201, resp.text
    note = resp.json()
    assert note["title"] == "Example Site"
    assert "https://example.com/some-article" in note["body_text"]
