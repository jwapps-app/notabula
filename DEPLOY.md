# Deploying to a NAS (Portainer)

The NAS never builds anything: GitHub Actions tests every push to `main`,
then publishes two images to GHCR —

- `ghcr.io/jwapps-app/notabula-api` (FastAPI + migrations)
- `ghcr.io/jwapps-app/notabula-web` (nginx + built PWA)

Portainer pulls them and runs the stack in `docker-compose.portainer.yml`.

## One-time setup

1. **GHCR registry access** (images are private). Portainer → Registries →
   Add registry → Custom: URL `ghcr.io`, username `<your-github-username>`, password =
   a GitHub PAT with `read:packages`. Skip if already added for another
   project.

2. **Folders on the NAS** — File Station (or ssh). Synology/Portainer does
   NOT reliably auto-create bind-mount folders, so create all of these:
   - `/volume1/docker/notabula/pgdata` (Postgres data)
   - `/volume1/Backup/notabula` (nightly dumps)

3. **Deploy the stack**: Portainer → Stacks → Add stack → name `notabula` →
   Web editor → paste `docker-compose.portainer.yml` → add the environment
   variables (below) → Deploy.

4. **HTTPS ingress**: point your Cloudflare Tunnel (or Synology reverse
   proxy) at `http://<NAS-IP>:<WEB_PORT>`. HTTPS at the edge is required for
   the PWA to install on iOS.

5. Open the site and **register the first account** — it becomes the admin
   and registration closes automatically. Add everyone else from
   Settings → Users (admins create accounts, reset passwords, and clear a
   lost 2FA setup there).

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | — | strong password |
| `SECRET_KEY` | ✅ | — | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `WEB_PORT` | | `8210` | host port nginx is published on |
| `APP_URL` | | `https://notes.example.com` | public URL (display only) |
| `DATA_DIR` | | `/volume1/docker/notabula` | Postgres bind mount parent |
| `BACKUP_DIR` | | `/volume1/Backup/notabula` | nightly backup target |
| `IMAGE_PREFIX` | | `ghcr.io/jwapps-app/notabula` | |
| `IMAGE_TAG` | | `latest` | pin a `sha-…` tag to freeze a version |

## Updating

Push to `main` → wait for the Actions run → Portainer → Stacks → notabula →
**Re-pull image and redeploy** (or `docker compose pull && up -d` via ssh).
Migrations run automatically when the api container starts.

## Backups & restore

The `backup` service writes nightly to `BACKUP_DIR`, keeping 14 days:
`db-<ts>.dump` (pg_dump custom format) and `media-<ts>.tar.gz` (attachments).

Restore:
```bash
pg_restore -h <db-host> -U notesapp -d notesapp --clean db-<ts>.dump
tar -xzf media-<ts>.tar.gz -C <media volume mountpoint>
```

## Troubleshooting

**`api unhealthy` right after a failed first deploy** — Postgres sets its
password only when initializing an EMPTY `pgdata`. If an earlier deploy
attempt half-initialized it (or you changed `POSTGRES_PASSWORD` between
attempts), the DB keeps the old password and the API can't connect.
Confirm in Portainer → Containers → `notabula-api-1` → Logs (look for
`password authentication failed`). Fix (safe before real data exists):
stop the stack, delete the *contents* of `/volume1/docker/notabula/pgdata`,
redeploy.

**Reading logs**: Portainer → Containers → pick the container → Logs. The
api container prints alembic migration output on every boot.

## If someone is locked out (lost 2FA + recovery codes)

```sql
UPDATE users SET totp_enabled = false, totp_secret = NULL
WHERE username = '<username>';
```
(run via `docker exec -it <db container> psql -U notesapp`)
