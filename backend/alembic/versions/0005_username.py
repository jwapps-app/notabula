"""Username-only login: users.email → users.username.

Existing accounts keep working — their email's local part becomes the
username (john@example.com → john). If two accounts would collide on the
same local part, those keep their full email address as the username
(still unique) and can be tidied by the admin.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "email", new_column_name="username")
    op.execute("ALTER INDEX ix_users_email RENAME TO ix_users_username")
    op.execute(
        """
        UPDATE users u SET username = lower(split_part(username, '@', 1))
        WHERE position('@' in username) > 0
          AND NOT EXISTS (
            SELECT 1 FROM users o
            WHERE o.id <> u.id
              AND lower(split_part(o.username, '@', 1)) = lower(split_part(u.username, '@', 1))
          )
        """
    )


def downgrade() -> None:
    op.execute("ALTER INDEX ix_users_username RENAME TO ix_users_email")
    op.alter_column("users", "username", new_column_name="email")
