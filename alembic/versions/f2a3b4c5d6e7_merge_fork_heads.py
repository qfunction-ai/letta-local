"""Merge fork heads into single revision chain

Revision ID: f2a3b4c5d6e7
Revises: 15b577c62f3f, 4e88e702f85e, 5d27a719b24d, 7f7933666957, f1a2b3c4d5e6
Create Date: 2026-05-26 00:35:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = ('15b577c62f3f', '4e88e702f85e', '5d27a719b24d', '7f7933666957', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
