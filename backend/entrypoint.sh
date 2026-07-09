#!/usr/bin/env sh
# Apply DB migrations (retrying until the database is reachable), then
# launch the API server. Used as the container command in docker-compose,
# so the schema is always current and boot order never matters.
set -e

# The container starts as root for exactly one reason: a fresh media
# volume may have been seeded by whichever container mounted it first
# (e.g. the backup container's postgres image → root-owned, with alpine's
# /media junk inside). Fix ownership, then drop to appuser for good.
if [ "$(id -u)" = "0" ]; then
  rmdir /app/media/cdrom /app/media/floppy /app/media/usb 2>/dev/null || true
  chown -R appuser:appuser /app/media
  # HOME must follow the uid: libpq/asyncpg look in $HOME/.postgresql.
  export HOME=/home/appuser USER=appuser
  exec setpriv --reuid=appuser --regid=appuser --clear-groups "$0" "$@"
fi

echo "Running database migrations..."
tries=0
until alembic upgrade head; do
  tries=$((tries + 1))
  if [ "$tries" -ge 30 ]; then
    echo "Migrations still failing after $tries attempts; giving up." >&2
    exit 1
  fi
  echo "Database not ready (attempt $tries/30); retrying in 4s..."
  sleep 4
done

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
