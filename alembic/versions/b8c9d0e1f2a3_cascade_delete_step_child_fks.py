"""cascade_delete_step_child_fks

Agent deletion failed with FK violations whenever the agent had executed
tools: tool_calls.step_id and security_events.step_id referenced
steps.id with NO ACTION. SQLAlchemy flushes child DELETEs before the
parent agent row, so the steps DELETE executes while tool_calls rows and
step-linked security_events rows are still live — the agent-level
CASCADEs from a9b8c7d6e5f4 cannot help because the agent row DELETE
hasn't executed yet.

Complete inventory of FKs on steps.id at time of writing:
- messages.step_id: SET NULL (fine)
- step_metrics.step_id: CASCADE (fine)
- tool_calls.step_id: NO ACTION -> fixed here
- security_events.step_id: NO ACTION -> fixed here

Revision ID: b8c9d0e1f2a3
Revises: a9b8c7d6e5f4
Create Date: 2026-08-20 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TOOL_CALLS_FK = 'tool_calls_step_id_fkey'
_SECURITY_EVENTS_STEP_FK = 'security_events_step_id_fkey'


def upgrade() -> None:
    # tool_calls.step_id -> steps.id, add ON DELETE CASCADE
    op.drop_constraint(_TOOL_CALLS_FK, 'tool_calls', type_='foreignkey')
    op.create_foreign_key(
        _TOOL_CALLS_FK,
        'tool_calls', 'steps',
        ['step_id'], ['id'],
        ondelete='CASCADE',
    )

    # security_events.step_id -> steps.id, add ON DELETE CASCADE
    op.drop_constraint(_SECURITY_EVENTS_STEP_FK, 'security_events', type_='foreignkey')
    op.create_foreign_key(
        _SECURITY_EVENTS_STEP_FK,
        'security_events', 'steps',
        ['step_id'], ['id'],
        ondelete='CASCADE',
    )


def downgrade() -> None:
    op.drop_constraint(_SECURITY_EVENTS_STEP_FK, 'security_events', type_='foreignkey')
    op.create_foreign_key(
        _SECURITY_EVENTS_STEP_FK,
        'security_events', 'steps',
        ['step_id'], ['id'],
    )
    op.drop_constraint(_TOOL_CALLS_FK, 'tool_calls', type_='foreignkey')
    op.create_foreign_key(
        _TOOL_CALLS_FK,
        'tool_calls', 'steps',
        ['step_id'], ['id'],
    )
