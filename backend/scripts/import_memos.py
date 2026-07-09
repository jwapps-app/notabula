#!/usr/bin/env python3
"""Migrate memos from a Memos server (v0.27 API) into this notes app.

Fetches every NORMAL-state memo, converts its markdown into ProseMirror
JSON (headings, bullet/ordered lists, task lists, code blocks, bold/
italic/inline code), re-uploads image attachments, and bulk-imports into
a folder with ORIGINAL creation timestamps preserved. Inline #tags come
along for free — both apps use the same convention.

Usage:
  python scripts/import_memos.py \
    --memos-url https://memos.example.com --memos-token memos_pat_… \
    --app-url http://localhost:8200 --username admin --password … \
    [--folder Memos] [--dry-run]
"""

import argparse
import re
import sys

import httpx


def _show_errors(response: "httpx.Response") -> None:
    """Event hook: print the server's explanation before any raise."""
    if response.status_code >= 400:
        response.read()
        print(f"SERVER ERROR {response.status_code}: {response.text[:500]}")

# --- Markdown → ProseMirror -------------------------------------------------

_INLINE_RE = re.compile(
    r"(\*\*(?P<bold>.+?)\*\*)|(\*(?P<italic>[^*]+?)\*)|(`(?P<code>[^`]+?)`)"
)


def _inline(text: str) -> list[dict]:
    """Split a line into text nodes with bold/italic/code marks."""
    nodes: list[dict] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            nodes.append({"type": "text", "text": text[pos : m.start()]})
        if m.group("bold") is not None:
            nodes.append({"type": "text", "text": m.group("bold"), "marks": [{"type": "bold"}]})
        elif m.group("italic") is not None:
            nodes.append({"type": "text", "text": m.group("italic"), "marks": [{"type": "italic"}]})
        elif m.group("code") is not None:
            nodes.append({"type": "text", "text": m.group("code"), "marks": [{"type": "code"}]})
        pos = m.end()
    if pos < len(text):
        nodes.append({"type": "text", "text": text[pos:]})
    return [n for n in nodes if n.get("text")]


def _paragraph(text: str) -> dict:
    content = _inline(text)
    return {"type": "paragraph", **({"content": content} if content else {})}


def markdown_to_doc(markdown: str) -> tuple[dict, str]:
    """Best-effort conversion. Returns (ProseMirror doc, plain body_text)."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    text_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            code_text = "\n".join(code)
            blocks.append(
                {"type": "codeBlock", "content": [{"type": "text", "text": code_text}]}
                if code_text
                else {"type": "codeBlock"}
            )
            text_lines.extend(code)
            continue

        heading = re.match(r"^(#{1,6}) +(.*)$", stripped)
        task = re.match(r"^[-*] \[([ xX])\] ?(.*)$", stripped)
        bullet = re.match(r"^[-*] +(.*)$", stripped)
        ordered = re.match(r"^\d+[.)] +(.*)$", stripped)

        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.append(
                {
                    "type": "heading",
                    "attrs": {"level": level},
                    "content": _inline(heading.group(2)),
                }
            )
            text_lines.append(heading.group(2))
        elif task:
            items = []
            while i < len(lines):
                m = re.match(r"^[-*] \[([ xX])\] ?(.*)$", lines[i].strip())
                if not m:
                    break
                items.append(
                    {
                        "type": "taskItem",
                        "attrs": {"checked": m.group(1).lower() == "x"},
                        "content": [_paragraph(m.group(2))],
                    }
                )
                text_lines.append(m.group(2))
                i += 1
            blocks.append({"type": "taskList", "content": items})
            continue
        elif bullet:
            items = []
            while i < len(lines):
                s = lines[i].strip()
                m = re.match(r"^[-*] +(.*)$", s)
                if not m or re.match(r"^[-*] \[([ xX])\]", s):
                    break
                items.append({"type": "listItem", "content": [_paragraph(m.group(1))]})
                text_lines.append(m.group(1))
                i += 1
            blocks.append({"type": "bulletList", "content": items})
            continue
        elif ordered:
            items = []
            while i < len(lines):
                m = re.match(r"^\d+[.)] +(.*)$", lines[i].strip())
                if not m:
                    break
                items.append({"type": "listItem", "content": [_paragraph(m.group(1))]})
                text_lines.append(m.group(1))
                i += 1
            blocks.append({"type": "orderedList", "content": items})
            continue
        elif stripped:
            blocks.append(_paragraph(stripped))
            text_lines.append(stripped)
        i += 1

    if not blocks:
        blocks = [{"type": "paragraph"}]
    return {"type": "doc", "content": blocks}, "\n".join(text_lines)


def derive_title(body_text: str) -> str:
    for line in body_text.split("\n"):
        if line.strip():
            return line.strip()[:400]
    return ""


# --- Migration ---------------------------------------------------------------


def attach_images(memos, memos_http, app_http, folder_name) -> None:
    """Second pass: upload each memo's images and append them to the
    matching already-imported note (matched by derived title within the
    import folder). Skips notes that already contain images, so it's safe
    to re-run."""
    folders = app_http.get("/api/v1/folders").raise_for_status().json()
    folder = next((f for f in folders if f["name"] == folder_name), None)
    if folder is None:
        sys.exit(f"folder {folder_name!r} not found — run the note import first")
    listing = (
        app_http.get("/api/v1/notes", params={"folder_id": folder["id"]})
        .raise_for_status()
        .json()
    )
    by_title = {n["title"]: n["id"] for n in listing}

    attached = 0
    for memo in memos:
        images = [
            a for a in memo.get("attachments", [])
            if a.get("type", "").startswith("image/")
        ]
        if not images:
            continue
        _doc, body_text = markdown_to_doc(memo["content"])
        title = derive_title(body_text)
        note_id = by_title.get(title)
        if note_id is None:
            print(f"  no imported note matches {title!r} — skipped")
            continue
        full = app_http.get(f"/api/v1/notes/{note_id}").raise_for_status().json()
        if '"image"' in str(full["body"]):
            print(f"  {title!r} already has images — skipped")
            continue
        body = full["body"] or {"type": "doc", "content": []}
        for att in images:
            blob = memos_http.get(
                f"/file/{att['name']}/{att['filename']}"
            ).raise_for_status()
            up = app_http.post(
                "/api/v1/attachments",
                files={"file": (att["filename"], blob.content, att["type"])},
            ).raise_for_status()
            body["content"].append(
                {"type": "image", "attrs": {"src": up.json()["url"]}}
            )
            attached += 1
        app_http.patch(
            f"/api/v1/notes/{note_id}",
            json={
                "base_version": full["version"],
                "body": body,
                "body_text": full["body_text"],
            },
        ).raise_for_status()
        print(f"  attached {len(images)} image(s) to {title!r}")
    print(f"done — {attached} image(s) attached")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--memos-url", required=True)
    ap.add_argument("--memos-token", required=True)
    ap.add_argument("--app-url", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--totp", default=None, help="TOTP code if 2FA is enabled")
    ap.add_argument("--folder", default="Memos")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--skip-images",
        action="store_true",
        help="import notes only; leave attachments behind",
    )
    ap.add_argument(
        "--images-only",
        action="store_true",
        help="attach memo images to ALREADY-imported notes (matched by title)",
    )
    args = ap.parse_args()

    memos_http = httpx.Client(
        event_hooks={"response": [_show_errors]},
        base_url=args.memos_url.rstrip("/"),
        headers={"Authorization": f"Bearer {args.memos_token}"},
        timeout=60,
    )

    # 1. Fetch every memo (paginated).
    memos: list[dict] = []
    page_token = ""
    while True:
        params = {"pageSize": 200, "state": "NORMAL"}
        if page_token:
            params["pageToken"] = page_token
        data = memos_http.get("/api/v1/memos", params=params).raise_for_status().json()
        memos.extend(data.get("memos", []))
        page_token = data.get("nextPageToken", "")
        if not page_token:
            break
    memos.sort(key=lambda m: m["createTime"])  # oldest first
    print(f"fetched {len(memos)} memos")

    # 2. Log into the notes app.
    app_http = httpx.Client(base_url=args.app_url.rstrip("/"), timeout=120, event_hooks={"response": [_show_errors]})
    login = {"username": args.username, "password": args.password}
    if args.totp:
        login["totp_code"] = args.totp
    resp = app_http.post("/api/v1/auth/login", json=login)
    if resp.status_code != 200:
        sys.exit(f"login failed: {resp.status_code} {resp.text}")
    app_http.headers["Authorization"] = f"Bearer {resp.json()['session_token']}"

    if args.images_only:
        attach_images(memos, memos_http, app_http, args.folder)
        return

    # 3. Convert (and upload attachments unless dry-running).
    notes = []
    attachment_count = 0
    for memo in memos:
        doc, body_text = markdown_to_doc(memo["content"])

        for att in memo.get("attachments", []):
            if args.skip_images:
                continue
            if not att.get("type", "").startswith("image/"):
                print(f"  skipping non-image attachment {att.get('filename')}")
                continue
            attachment_count += 1
            if args.dry_run:
                continue
            blob = memos_http.get(
                f"/file/{att['name']}/{att['filename']}"
            ).raise_for_status()
            up = app_http.post(
                "/api/v1/attachments",
                files={"file": (att["filename"], blob.content, att["type"])},
            ).raise_for_status()
            doc["content"].append(
                {"type": "image", "attrs": {"src": up.json()["url"]}}
            )

        notes.append(
            {
                "title": derive_title(body_text),
                "body": doc,
                "body_text": body_text,
                "created_at": memo["createTime"],
                "updated_at": memo.get("updateTime", memo["createTime"]),
                "pinned": memo.get("pinned", False),
            }
        )

    if args.dry_run:
        print(f"DRY RUN — would import {len(notes)} notes, {attachment_count} images")
        for n in notes[:5]:
            print(f"  [{n['created_at'][:10]}] {n['title'][:60]!r}")
        return

    # 4. Import in one batch with original timestamps.
    resp = app_http.post(
        "/api/v1/admin/import",
        json={"folder_name": args.folder, "notes": notes},
    )
    resp.raise_for_status()
    print(
        f"imported {resp.json()['imported']} notes "
        f"({attachment_count} images) into folder '{args.folder}'"
    )


if __name__ == "__main__":
    main()
