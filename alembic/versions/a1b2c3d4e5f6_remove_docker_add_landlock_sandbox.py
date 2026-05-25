"""Remove docker sandbox type and add landlock sandbox type

Two-phase migration:
  Phase A (this migration): Update existing sandbox_type='docker' rows to
  'local', then replace the PostgreSQL enum to remove 'docker' and add
  'landlock'.

  This migration should be run AFTER deploying the new code that removes
  SandboxType.DOCKER from the Python enum. The old 'docker' enum value
  is harmless if it remains in PostgreSQL during the deploy window —
  no code path produces it anymore.

Revision ID: a1b2c3d4e5f6
Revises: d8b2e4f1a376
Create Date: 2026-05-25 23:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'd8b2e4f1a376'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Migrate docker -> local, then replace enum to remove docker and add landlock.

    PostgreSQL native enums cannot remove values with ALTER TYPE.
    The standard approach is:
    1. Update rows that reference the value being removed
    2. Create a new enum type with the desired values
    3. Alter the column to use the new type
    4. Drop the old type
    5. Rename the new type to the old name
    """
    # Step 1: Update any existing rows with type='docker' to type='local'
    op.execute("UPDATE sandbox_config SET type = 'local' WHERE type = 'docker'")

    # Step 2: Create new enum type with 'docker' removed and 'landlock' added
    op.execute("""
        CREATE TYPE sandboxtype_new AS ENUM ('e2b', 'modal', 'local', 'landlock')
    """)

    # Step 3: Alter the column to use the new type
    op.execute("""
        ALTER TABLE sandbox_config
        ALTER COLUMN type TYPE sandboxtype_new
        USING type::text::sandboxtype_new
    """)

    # Step 4: Drop the old type
    op.execute("DROP TYPE sandboxtype")

    # Step 5: Rename the new type to the old name
    op.execute("ALTER TYPE sandboxtype_new RENAME TO sandboxtype")


def downgrade() -> None:
    """Reverse the migration: add back 'docker', remove 'landlock'.

    WARNING: This will fail if any rows have type='landlock'.
    Update those rows to 'local' first if a downgrade is needed.
    """
    # Update any landlock rows to local
    op.execute("UPDATE sandbox_config SET type = 'local' WHERE type = 'landlock'")

    # Create old enum type
    op.execute("""
        CREATE TYPE sandboxtype_old AS ENUM ('e2b', 'modal', 'local', 'docker')
    """)

    # Alter column back
    op.execute("""
        ALTER TABLE sandbox_config
        ALTER COLUMN type TYPE sandboxtype_old
        USING type::text::sandboxtype_old
    """)

    # Drop the current type
    op.execute("DROP TYPE sandboxtype")

    # Rename back
    op.execute("ALTER TYPE sandboxtype_old RENAME TO sandboxtype")
