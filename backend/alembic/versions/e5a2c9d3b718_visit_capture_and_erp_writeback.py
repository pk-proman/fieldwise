"""visit customer capture, ERPNext write-back status for leave and expenses

Revision ID: e5a2c9d3b718
Revises: d4f1b8c27a96
Create Date: 2026-11-17 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e5a2c9d3b718'
down_revision: Union[str, Sequence[str], None] = 'd4f1b8c27a96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('visits', sa.Column('lead_id', sa.Integer(), nullable=True))
    op.add_column('visits', sa.Column('prospect', sa.JSON(), nullable=True))
    op.add_column('visits', sa.Column('start_dist_m', sa.Float(), nullable=True))
    op.add_column('visits', sa.Column('end_lat', sa.Float(), nullable=True))
    op.add_column('visits', sa.Column('end_lng', sa.Float(), nullable=True))
    op.add_column('visits', sa.Column('outcome', sa.String(length=300), nullable=False, server_default=''))
    op.create_index('ix_visits_lead_id', 'visits', ['lead_id'])
    op.add_column('leave_requests', sa.Column('erp_ref', sa.String(length=100), nullable=False, server_default=''))
    op.add_column('leave_requests', sa.Column('erp_error', sa.String(length=300), nullable=False, server_default=''))
    op.add_column('expense_claims', sa.Column('erp_error', sa.String(length=300), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('expense_claims', 'erp_error')
    op.drop_column('leave_requests', 'erp_error')
    op.drop_column('leave_requests', 'erp_ref')
    op.drop_index('ix_visits_lead_id', table_name='visits')
    for c in ('outcome', 'end_lng', 'end_lat', 'start_dist_m', 'prospect', 'lead_id'):
        op.drop_column('visits', c)
