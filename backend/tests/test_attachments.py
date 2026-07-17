"""Attachment upload: happy path, type/size validation."""

import pytest

from app.config import settings

# Smallest valid PNG (1x1 transparent).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcff9fa11e0000078400816cd42ca70000000049454e44ae426082"
)


@pytest.fixture(autouse=True)
def media_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    return tmp_path


async def test_upload_png(auth, media_tmp):
    client, headers, _ = auth
    resp = await client.post(
        "/api/v1/attachments",
        headers=headers,
        files={"file": ("photo.png", PNG, "image/png")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["url"].startswith("/media/attachments/")
    assert data["url"].endswith(".png")
    assert data["size_bytes"] == len(PNG)

    stored = media_tmp / "attachments" / data["url"].rsplit("/", 1)[1]
    assert stored.read_bytes() == PNG


async def test_upload_pdf(auth, media_tmp):
    client, headers, _ = auth
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    resp = await client.post(
        "/api/v1/attachments",
        headers=headers,
        files={"file": ("report.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["url"].endswith(".pdf")
    assert data["content_type"] == "application/pdf"
    stored = media_tmp / "attachments" / data["url"].rsplit("/", 1)[1]
    assert stored.read_bytes() == pdf


async def test_upload_rejects_non_image(auth):
    client, headers, _ = auth
    resp = await client.post(
        "/api/v1/attachments",
        headers=headers,
        files={"file": ("evil.html", b"<script>", "text/html")},
    )
    assert resp.status_code == 415


async def test_upload_rejects_oversize(auth, media_tmp, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)  # everything is too big
    client, headers, _ = auth
    resp = await client.post(
        "/api/v1/attachments",
        headers=headers,
        files={"file": ("photo.png", PNG, "image/png")},
    )
    assert resp.status_code == 413
    assert list((media_tmp / "attachments").iterdir()) == []  # cleaned up


async def test_upload_requires_auth(client):
    resp = await client.post(
        "/api/v1/attachments",
        files={"file": ("photo.png", PNG, "image/png")},
    )
    assert resp.status_code == 401
