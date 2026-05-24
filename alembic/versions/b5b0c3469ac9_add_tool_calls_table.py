"""add_tool_calls_table + steps.emissions column

Revision ID: b5b0c3469ac9
Revises: 1c28e167b74f
Create Date: 2026-05-23 11:14:15.145963

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5b0c3469ac9'
down_revision: Union[str, None] = '1c28e167b74f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Emissions tracking: per-request emissions estimate on steps
    op.add_column('steps', sa.Column('emissions', sa.JSON(), nullable=True))

    # Per-tool-call records for observability
    op.create_table('tool_calls',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('step_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=True),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('tool_name', sa.String(), nullable=False),
        sa.Column('tool_args', sa.JSON(), nullable=True),
        sa.Column('tool_result', sa.Text(), nullable=True),
        sa.Column('duration_ns', sa.BigInteger(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('request_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column('_created_by_id', sa.String(), nullable=True),
        sa.Column('_last_updated_by_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['step_id'], ['steps.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_calls_step_id'), 'tool_calls', ['step_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tool_calls_step_id'), table_name='tool_calls')
    op.drop_table('tool_calls')
    op.drop_column('steps', 'emissions')
