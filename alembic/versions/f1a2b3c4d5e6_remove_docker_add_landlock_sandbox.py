"""Remove docker sandbox type and add landlock sandbox type

Idempotent migration that handles both fresh and existing DBs:
  - Fresh DB: The enum may already have the correct values (no 'docker', with 'landlock')
  - Existing DB with 'docker': Migrate docker -> local, then replace the enum
  - Existing DB without 'docker' but without 'landlock': Add 'landlock' to the enum

Revision ID: f1a2b3c4d5e6
Revises: d8b2e4f1a376
Create Date: 2026-05-25 23:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd8b2e4f1a376'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_enum_values(conn):
    """Get the current values of the sandboxtype enum."""
    result = conn.execute(sa.text(
        "SELECT enumlabel FROM pg_enum "
        "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
        "WHERE typname = 'sandboxtype' "
        "ORDER BY enumsortorder"
    ))
    return [row[0] for row in result]


def upgrade() -> None:
    conn = op.get_bind()

    # Check if the sandboxtype enum exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'sandboxtype'"
    ))
    enum_exists = result.fetchone() is not None

    if not enum_exists:
        # Fresh DB: create the enum with the correct values directly
        op.execute("CREATE TYPE sandboxtype AS ENUM ('e2b', 'modal', 'local', 'landlock')")
        return

    # Get current enum values
    current_values = _get_enum_values(conn)
    has_docker = 'docker' in current_values
    has_landlock = 'landlock' in current_values

    if has_landlock and not has_docker:
        # Already in the correct state
        return

    if has_docker:
        # Existing DB: need to migrate docker -> local and replace enum
        # Check if sandbox_configs table exists
        result = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'sandbox_configs'"
        ))
        table_exists = result.fetchone() is not None

        if table_exists:
            # Migrate docker rows to local BEFORE changing the enum
            # (can't reference 'docker' after it's removed from the enum)
            op.execute("UPDATE sandbox_configs SET type = 'local' WHERE type = 'docker'")

        # Replace the enum: create new type, alter column, drop old, rename
        op.execute("""
            CREATE TYPE sandboxtype_new AS ENUM ('e2b', 'modal', 'local', 'landlock')
        """)
        if table_exists:
            op.execute("""
                ALTER TABLE sandbox_configs
                ALTER COLUMN type TYPE sandboxtype_new
                USING type::text::sandboxtype_new
            """)
        op.execute("DROP TYPE sandboxtype")
        op.execute("ALTER TYPE sandboxtype_new RENAME TO sandboxtype")
    else:
        # No docker but no landlock either — just add landlock
        op.execute("ALTER TYPE sandboxtype ADD VALUE 'landlock'")


def downgrade() -> None:
    conn = op.get_bind()

    # Check if sandbox_configs table exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'sandbox_configs'"
    ))
    table_exists = result.fetchone() is not None

    if table_exists:
        op.execute("UPDATE sandbox_configs SET type = 'local' WHERE type = 'landlock'")

    # Get current enum values
    current_values = _get_enum_values(conn)

    if 'landlock' in current_values:
        # Replace the enum to remove landlock and add docker
        op.execute("""
            CREATE TYPE sandboxtype_old AS ENUM ('e2b', 'modal', 'local', 'docker')
        """)
        if table_exists:
            op.execute("""
                ALTER TABLE sandbox_configs
                ALTER COLUMN type TYPE sandboxtype_old
                USING type::text::sandboxtype_old
            """)
        op.execute("DROP TYPE sandboxtype")
        op.execute("ALTER TYPE sandboxtype_old RENAME TO sandboxtype")
