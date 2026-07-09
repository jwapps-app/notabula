# Notabula

A self-hosted, multi-user notes app modeled on iOS Notes — live WYSIWYG editing, folders, pinned notes, and checklists — installable as a PWA on any device. Your notes live on your own server, and they're never locked in: full export and import, and a one-click restore from backup.

## Features

- **WYSIWYG editor** — bold/italic/underline/highlight, headings, bullet/numbered/check lists, block quotes, inline images. Formatting renders live; raw markup is never shown.
- **Organization** — nested folders, `#hashtag` tags with autocomplete, pinning, full-text search, and smart views (Media, Links, Open Tasks, Locked, Last 7 Days).
- **Rich link previews** — any URL in any note unfurls into a title/description/image card.
- **Multi-user & sharing** — private notes by default; share notes or folders with viewer/editor roles, or mint a secret "anyone with the link" link. Edit history with word-level redline and restore.
- **Locked notes** — client-side encryption (PBKDF2 → AES-GCM); the server only ever stores ciphertext.
- **Offline-first PWA** — read everywhere offline; edits and new notes queue and sync when the connection returns.
- **Own your data** — export everything as a zip (plain text + images + lossless JSON), import it back, nightly server-side backups, and an admin one-click restore.
- **Accounts** — username + password with long-lived sessions, optional TOTP two-factor. First run bootstraps the admin; admins manage everyone else.

## Quick start

```bash
cp .env.example .env
cp backend/.env.example backend/.env
# edit both: set a real POSTGRES_PASSWORD and SECRET_KEY

(cd pwa && npm install && npm run build)
docker compose up -d --build

open http://localhost:8200
```

Register the first account — it becomes the admin. Set `ALLOW_REGISTRATION=false` in `backend/.env` once everyone you want has an account.

Deploying to a NAS / server with prebuilt images: see [DEPLOY.md](DEPLOY.md).

## Stack

- **Backend** — FastAPI (async), PostgreSQL 16 (JSONB + generated `tsvector`), Alembic, Docker
- **PWA** — React 19 + TypeScript + Vite, TipTap (ProseMirror) editor, vite-plugin-pwa (IndexedDB offline)
- **Proxy** — Nginx serving the built PWA and proxying `/api` and `/media`

Note bodies are stored as ProseMirror JSON; the first line of a note is its title, iOS-style. Deletes are soft (30-day "Recently Deleted"), and concurrent edits are version-checked so a second device can't silently clobber the first.

## Development

```bash
docker compose up -d db               # database only (host port 5433)

cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload        # http://localhost:8000

cd pwa && npm run dev                # http://localhost:5175 (proxies /api)
```

Tests: `cd backend && python -m pytest`

## License

[AGPL-3.0](LICENSE) — if you run a modified version as a network service, you must offer its source to users.
