"""add_security_events_table

Revision ID: c7a1f3d2e890
Revises: b5b0c3469ac9
Create Date: 2026-05-23 19:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1f3d2e890'
down_revision: Union[str, None] = 'b5b0c3469ac9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('security_events',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('organization_id', sa.String(), nullable=False),
        sa.Column('step_id', sa.String(), nullable=True),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('event_data', sa.JSON(), nullable=True),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column('_created_by_id', sa.String(), nullable=True),
        sa.Column('_last_updated_by_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
        sa.ForeignKeyConstraint(['step_id'], ['steps.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_security_events_agent_id'), 'security_events', ['agent_id'], unique=False)
    op.create_index(op.f('ix_security_events_event_type'), 'security_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_security_events_created_at'), 'security_events', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_security_events_created_at'), table_name='security_events')
    op.drop_index(op.f('ix_security_events_event_type'), table_name='security_events')
    op.drop_index(op.f('ix_security_events_agent_id'), table_name='security_events')
    op.drop_table('security_events')
