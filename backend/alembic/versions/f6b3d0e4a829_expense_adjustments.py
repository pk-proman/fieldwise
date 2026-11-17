"""expense claims remember what the rules calculated and why it was changed

Revision ID: f6b3d0e4a829
Revises: e5a2c9d3b718
Create Date: 2026-11-17 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6b3d0e4a829'
down_revision: Union[str, Sequence[str], None] = 'e5a2c9d3b718'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('expense_claims', sa.Column('calc_amount', sa.Float(), nullable=True))
    op.add_column('expense_claims', sa.Column('adjust_note', sa.String(length=300), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('expense_claims', 'adjust_note')
    op.drop_column('expense_claims', 'calc_amount')
