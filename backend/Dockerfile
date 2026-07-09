FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pg_restore powers Settings → Restore from Backup. It must match the
# Postgres 16 server (Debian's own client 15 can't read pg16 archives),
# so pull it from the PostgreSQL apt repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $VERSION_CODENAME-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# System deps kept minimal; asyncpg and bcrypt ship wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Media dir must exist and be owned by the runtime user *before* the named
# volume mounts over it — Docker seeds a fresh volume's ownership from the
# image directory, so this makes uploads writable as non-root.
RUN mkdir -p /app/media

# Non-root runtime user. The container STARTS as root so entrypoint.sh can
# repair media-volume ownership (a fresh volume may be seeded root-owned by
# another container), then it immediately drops to appuser via setpriv.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app

EXPOSE 8000

# entrypoint fixes media ownership, drops root, runs migrations, starts
# the server. Compose sets the same command explicitly; this default keeps
# a bare `docker run` on this image equally safe (never root at runtime).
CMD ["./entrypoint.sh"]
