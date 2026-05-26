"""Merge remaining fork heads

Revision ID: f3a4b5c6d7e8
Revises: 69253882cec8, f2a3b4c5d6e7
Create Date: 2026-05-26 00:58:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = ('69253882cec8', 'f2a3b4c5d6e7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
