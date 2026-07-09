#!/usr/bin/env python3
"""Migrate a Synology Note Station export (.nsx) into this notes app.

An .nsx is a zip: config.json (index), one JSON record per note/notebook,
and attachments as file_<md5> entries. Note content is HTML; this
converts it to ProseMirror (headings, bold/italic, bullet lists,
checkbox to-dos, code, tables→text rows, links→text with URL),
re-uploads images, prepends the title as the first line, appends
Note Station's metadata tags as inline #hashtags, and imports with
ORIGINAL timestamps. Encrypted notes are skipped with a warning.

Usage:
  python scripts/import_nsx.py --nsx ~/Downloads/export.nsx \
    --app-url http://localhost:8200 --username admin --password … \
    [--totp 123456] [--folder "Note Station"] [--dry-run] \
    [--skip-images | --images-only]
"""

import argparse
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx


def _show_errors(response: "httpx.Response") -> None:
    """Event hook: print the server's explanation before any raise."""
    if response.status_code >= 400:
        response.read()
        print(f"SERVER ERROR {response.status_code}: {response.text[:500]}")

SKIP_ELEMENTS = {"script", "style", "noscript", "head", "select", "option", "button", "form"}
BLOCK_END = {"div", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class NoteStationHTML(HTMLParser):
    """HTML → ProseMirror blocks. Images become {"type": "imgref"} nodes
    carrying the Note Station ref, resolved to uploads later."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.inline: list[dict] = []
        self.text_lines: list[str] = []
        self.bold = 0
        self.italic = 0
        self.code = 0
        self.skip = 0
        self.links: list[str] = []
        self.heading: int | None = None
        self.in_list = 0
        self.block_checkbox: bool | None = None
        self.cell_break = False

    # -- inline assembly ---------------------------------------------------

    def _marks(self) -> list[dict]:
        marks = []
        if self.bold:
            marks.append({"type": "bold"})
        if self.italic:
            marks.append({"type": "italic"})
        if self.code:
            marks.append({"type": "code"})
        return marks

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        if self.cell_break and self.inline:
            self.inline.append({"type": "text", "text": " | "})
        self.cell_break = False
        node: dict = {"type": "text", "text": text}
        marks = self._marks()
        if marks:
            node["marks"] = marks
        self.inline.append(node)

    def _flush_block(self) -> None:
        text = "".join(n["text"] for n in self.inline).strip()
        if not text and self.block_checkbox is None:
            self.inline = []
            return
        content = [n for n in self.inline if n["text"].strip() or " " in n["text"]]
        self.inline = []
        para = {"type": "paragraph", **({"content": content} if content else {})}
        self.text_lines.append(text)

        if self.block_checkbox is not None:
            item = {
                "type": "taskItem",
                "attrs": {"checked": self.block_checkbox},
                "content": [para],
            }
            self.block_checkbox = None
            if self.blocks and self.blocks[-1]["type"] == "taskList":
                self.blocks[-1]["content"].append(item)
            else:
                self.blocks.append({"type": "taskList", "content": [item]})
        elif self.heading is not None and content:
            self.blocks.append(
                {"type": "heading", "attrs": {"level": min(self.heading, 3)}, "content": content}
            )
        elif self.in_list and content:
            item = {"type": "listItem", "content": [para]}
            if self.blocks and self.blocks[-1]["type"] == "bulletList":
                self.blocks[-1]["content"].append(item)
            else:
                self.blocks.append({"type": "bulletList", "content": [item]})
        else:
            self.blocks.append(para)

    # -- element handling ----------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag in SKIP_ELEMENTS:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("b", "strong"):
            self.bold += 1
        elif tag in ("i", "em"):
            self.italic += 1
        elif tag in ("code", "tt"):
            self.code += 1
        elif tag == "a":
            self.links.append(a.get("href", ""))
        elif tag in ("ul", "ol"):
            self._flush_block()
            self.in_list += 1
        elif tag.startswith("h") and tag[1:].isdigit():
            self._flush_block()
            self.heading = int(tag[1:])
        elif tag == "br":
            self._flush_block()
        elif tag in ("td", "th"):
            self.cell_break = True
        elif tag == "input" and "checkbox" in (a.get("class") or ""):
            self._flush_block()
            checked = "_02" in (a.get("src") or "") or "checked" in a
            self.block_checkbox = checked
        elif tag == "img":
            ref = a.get("ref")
            if ref:
                self._flush_block()
                self.blocks.append({"type": "imgref", "ref": ref})

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_ELEMENTS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("b", "strong"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("i", "em"):
            self.italic = max(0, self.italic - 1)
        elif tag in ("code", "tt"):
            self.code = max(0, self.code - 1)
        elif tag == "a":
            href = self.links.pop() if self.links else ""
            text = "".join(n["text"] for n in self.inline)
            if href and href.startswith("http") and href not in text:
                self.inline.append({"type": "text", "text": f" ({href})"})
        elif tag in ("ul", "ol"):
            self._flush_block()
            self.in_list = max(0, self.in_list - 1)
        elif tag in BLOCK_END:
            self._flush_block()
            if tag.startswith("h"):
                self.heading = None

    def result(self) -> tuple[list[dict], str]:
        self._flush_block()
        return self.blocks, "\n".join(self.text_lines)


def sanitize_tag(tag: str) -> str:
    return re.sub(r"[^\w-]+", "-", tag.strip()).strip("-")


def convert_note(raw: dict) -> tuple[dict, str, list[str]]:
    """Returns (doc-with-imgref-placeholders, body_text, image_refs)."""
    parser = NoteStationHTML()
    parser.feed(raw.get("content") or "")
    blocks, body_text = parser.result()

    title = (raw.get("title") or "").strip()
    first_line = body_text.split("\n", 1)[0].strip() if body_text else ""
    if title and first_line.lower() != title.lower():
        blocks.insert(0, {"type": "paragraph", "content": [{"type": "text", "text": title}]})
        body_text = f"{title}\n{body_text}" if body_text else title

    tags = [sanitize_tag(t) for t in raw.get("tag", []) if sanitize_tag(t)]
    if tags:
        line = " ".join(f"#{t}" for t in tags)
        blocks.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        body_text = f"{body_text}\n{line}"

    refs = [b["ref"] for b in blocks if b["type"] == "imgref"]
    if not blocks:
        blocks = [{"type": "paragraph"}]
    return {"type": "doc", "content": blocks}, body_text, refs


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nsx", required=True)
    ap.add_argument("--app-url", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--totp", default=None)
    ap.add_argument("--folder", default=None, help="override folder name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--images-only", action="store_true")
    args = ap.parse_args()

    zf = zipfile.ZipFile(args.nsx)
    cfg = json.loads(zf.read("config.json"))

    notebooks = {}
    for nb_id in cfg.get("notebook", []):
        data = json.loads(zf.read(nb_id))
        notebooks[nb_id] = (data.get("title") or "").strip() or "Note Station"

    notes_raw = []
    for note_id in cfg.get("note", []):
        data = json.loads(zf.read(note_id))
        if data.get("encrypt"):
            print(f"  SKIPPING encrypted note: {data.get('title')!r}")
            continue
        notes_raw.append(data)
    notes_raw.sort(key=lambda n: n.get("ctime", 0))
    print(f"read {len(notes_raw)} notes from {args.nsx}")

    app_http = httpx.Client(base_url=args.app_url.rstrip("/"), timeout=120, event_hooks={"response": [_show_errors]})
    login = {"username": args.username, "password": args.password}
    if args.totp:
        login["totp_code"] = args.totp
    resp = app_http.post("/api/v1/auth/login", json=login)
    if resp.status_code != 200:
        sys.exit(f"login failed: {resp.status_code} {resp.text}")
    app_http.headers["Authorization"] = f"Bearer {resp.json()['session_token']}"

    def upload_ref(raw_note: dict, ref: str) -> str | None:
        """Upload the attachment matching a content ref; return its URL."""
        for att in (raw_note.get("attachment") or {}).values():
            if att.get("ref") == ref:
                try:
                    blob = zf.read(f"file_{att['md5']}")
                except KeyError:
                    print(f"  attachment file missing for {att.get('name')}")
                    return None
                up = app_http.post(
                    "/api/v1/attachments",
                    files={"file": (att.get("name", "image"), blob, att.get("type", "image/jpeg"))},
                ).raise_for_status()
                return up.json()["url"]
        return None

    # --- images-only second pass -----------------------------------------
    if args.images_only:
        folder_name = args.folder or "Note Station"
        folders = app_http.get("/api/v1/folders").raise_for_status().json()
        folder = next((f for f in folders if f["name"] == folder_name), None)
        if folder is None:
            sys.exit(f"folder {folder_name!r} not found — run the note import first")
        listing = (
            app_http.get("/api/v1/notes", params={"folder_id": folder["id"]})
            .raise_for_status().json()
        )
        by_title = {n["title"]: n["id"] for n in listing}
        attached = 0
        for raw in notes_raw:
            doc, body_text, refs = convert_note(raw)
            if not refs:
                continue
            title = body_text.split("\n", 1)[0].strip()[:400]
            note_id = by_title.get(title)
            if note_id is None:
                print(f"  no imported note matches {title!r} — skipped")
                continue
            full = app_http.get(f"/api/v1/notes/{note_id}").raise_for_status().json()
            if '"image"' in str(full["body"]):
                print(f"  {title!r} already has images — skipped")
                continue
            body = full["body"] or {"type": "doc", "content": []}
            count = 0
            for ref in refs:
                url = upload_ref(raw, ref)
                if url:
                    body["content"].append({"type": "image", "attrs": {"src": url}})
                    count += 1
            if count:
                app_http.patch(
                    f"/api/v1/notes/{note_id}",
                    json={"base_version": full["version"], "body": body,
                          "body_text": full["body_text"]},
                ).raise_for_status()
                attached += count
                print(f"  attached {count} image(s) to {title!r}")
        print(f"done — {attached} image(s) attached")
        return

    # --- main import --------------------------------------------------------
    by_folder: dict[str, list[dict]] = {}
    image_total = 0
    for raw in notes_raw:
        doc, body_text, refs = convert_note(raw)
        image_total += len(refs)

        resolved = []
        for block in doc["content"]:
            if block["type"] != "imgref":
                resolved.append(block)
                continue
            if args.skip_images or args.dry_run:
                continue
            url = upload_ref(raw, block["ref"])
            if url:
                resolved.append({"type": "image", "attrs": {"src": url}})
        doc["content"] = resolved or [{"type": "paragraph"}]

        folder_name = args.folder or notebooks.get(raw.get("parent_id"), "Note Station")
        by_folder.setdefault(folder_name, []).append(
            {
                "title": body_text.split("\n", 1)[0].strip()[:400],
                "body": doc,
                "body_text": body_text,
                "created_at": iso(raw.get("ctime", 0)),
                "updated_at": iso(raw.get("mtime", raw.get("ctime", 0))),
            }
        )

    if args.dry_run:
        total = sum(len(v) for v in by_folder.items() for v in [v[1]])
        print(f"DRY RUN — would import {total} notes, {image_total} images")
        for folder_name, items in by_folder.items():
            print(f"  folder {folder_name!r}: {len(items)} notes")
            for n in items[:5]:
                print(f"    [{n['created_at'][:10]}] {n['title'][:60]!r}")
        return

    for folder_name, items in by_folder.items():
        resp = app_http.post(
            "/api/v1/admin/import",
            json={"folder_name": folder_name, "notes": items},
        )
        resp.raise_for_status()
        print(f"imported {resp.json()['imported']} notes into {folder_name!r}")
    if not args.skip_images:
        print(f"({image_total} image(s) placed inline)")


if __name__ == "__main__":
    main()
