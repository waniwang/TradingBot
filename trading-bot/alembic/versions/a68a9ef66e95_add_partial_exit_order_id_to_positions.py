"""add partial_exit_order_id to positions

Revision ID: a68a9ef66e95
Revises:
Create Date: 2026-02-20 14:20:38.961711

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a68a9ef66e95'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('partial_exit_order_id', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_column('partial_exit_order_id')
