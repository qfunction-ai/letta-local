"""add_organization_id_to_tool_call_policies

Revision ID: 69253882cec8
Revises: d8b2e4f1a376
Create Date: 2026-05-24 01:44:00.000000

VULN-001 fix: the tool_call_policies table had no organization_id column,
meaning the policy API endpoints could not enforce org scoping at the data
layer. This migration adds the column, backfills from agents.organization_id,
and adds the FK constraint + index.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69253882cec8'
down_revision: Union[str, None] = 'd8b2e4f1a376'
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Add column as nullable first (existing rows have no org_id)
    op.add_column('tool_call_policies',
        sa.Column('organization_id', sa.String(), nullable=True))

    # Backfill from agents table
    op.execute("""
        UPDATE tool_call_policies tcp
        SET organization_id = a.organization_id
        FROM agents a
        WHERE tcp.agent_id = a.id
    """)

    # Make non-nullable after backfill
    op.alter_column('tool_call_policies', 'organization_id',
        nullable=False)

    # Add FK constraint
    op.create_foreign_key(
        'fk_tool_call_policies_organization_id',
        'tool_call_policies', 'organizations',
        ['organization_id'], ['id'],
    )

    # Add index for org-scoped queries
    op.create_index(
        'ix_tool_call_policies_organization_id',
        'tool_call_policies', ['organization_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_tool_call_policies_organization_id', table_name='tool_call_policies')
    op.drop_constraint('fk_tool_call_policies_organization_id', 'tool_call_policies', type_='foreignkey')
    op.drop_column('tool_call_policies', 'organization_id')
