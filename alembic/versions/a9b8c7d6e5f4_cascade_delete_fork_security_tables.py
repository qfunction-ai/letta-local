"""cascade_delete_fork_security_tables

Agent deletion failed with a ForeignKeyViolation whenever the agent had
rows in the fork-only security tables: security_events and
tool_call_policies referenced agents.id without ON DELETE CASCADE, so
any agent that ever produced an audit event (i.e. every agent) could
not be deleted. This mirrors AgentMixin's ondelete="CASCADE" used by
messages/steps/tool_calls.

Revision ID: a9b8c7d6e5f4
Revises: f3a4b5c6d7e8
Create Date: 2026-08-20 21:50:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Postgres default constraint names for the FKs being altered.
_SECURITY_EVENTS_FK = 'security_events_agent_id_fkey'
_TOOL_CALL_POLICIES_FK = 'tool_call_policies_agent_id_fkey'


def upgrade() -> None:
    # security_events.agent_id -> agents.id, add ON DELETE CASCADE
    op.drop_constraint(_SECURITY_EVENTS_FK, 'security_events', type_='foreignkey')
    op.create_foreign_key(
        _SECURITY_EVENTS_FK,
        'security_events', 'agents',
        ['agent_id'], ['id'],
        ondelete='CASCADE',
    )

    # tool_call_policies.agent_id -> agents.id, add ON DELETE CASCADE
    op.drop_constraint(_TOOL_CALL_POLICIES_FK, 'tool_call_policies', type_='foreignkey')
    op.create_foreign_key(
        _TOOL_CALL_POLICIES_FK,
        'tool_call_policies', 'agents',
        ['agent_id'], ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint(_TOOL_CALL_POLICIES_FK, 'tool_call_policies', type_='foreignkey')
    op.create_foreign_key(
        _TOOL_CALL_POLICIES_FK,
        'tool_call_policies', 'agents',
        ['agent_id'], ['id'],
    )
    op.drop_constraint(_SECURITY_EVENTS_FK, 'security_events', type_='foreignkey')
    op.create_foreign_key(
        _SECURITY_EVENTS_FK,
        'security_events', 'agents',
        ['agent_id'], ['id'],
    )
