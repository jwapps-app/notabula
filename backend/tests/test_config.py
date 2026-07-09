"""Settings: discrete DB_* parts assemble a correctly URL-encoded DSN."""

from app.config import Settings


def test_db_password_special_chars_urlencoded():
    s = Settings(DB_PASSWORD="p@ss:w/rd%25!", DB_HOST="db")
    assert (
        str(s.database_url)
        == "postgresql+asyncpg://notesapp:p%40ss%3Aw%2Frd%2525%21@db:5432/notesapp"
    )


def test_database_url_used_when_no_parts():
    s = Settings(DATABASE_URL="postgresql+asyncpg://u:p@example:5432/x")
    assert str(s.database_url) == "postgresql+asyncpg://u:p@example:5432/x"


def test_parts_override_database_url():
    s = Settings(
        DATABASE_URL="postgresql+asyncpg://ignored:ignored@ignored:5432/ignored",
        DB_PASSWORD="secret",
        DB_HOST="db",
        DB_USER="app",
        DB_NAME="notes",
    )
    assert str(s.database_url) == "postgresql+asyncpg://app:secret@db:5432/notes"
