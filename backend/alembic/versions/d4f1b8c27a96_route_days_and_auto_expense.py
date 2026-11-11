"""route_days table, expense_claims.source

Revision ID: d4f1b8c27a96
Revises: c3e9a6b1d742
Create Date: 2026-11-13 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd4f1b8c27a96'
down_revision: Union[str, Sequence[str], None] = 'c3e9a6b1d742'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('route_days',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False), sa.Column('day', sa.Date(), nullable=False),
        sa.Column('distance_km', sa.Float(), nullable=False, server_default='0'), sa.Column('est_km', sa.Float(), nullable=False, server_default='0'),
        sa.Column('moving_min', sa.Integer(), nullable=False, server_default='0'), sa.Column('stop_min', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gap_min', sa.Integer(), nullable=False, server_default='0'), sa.Column('points', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('quality', sa.Integer(), nullable=False, server_default='0'), sa.Column('far_km', sa.Float(), nullable=False, server_default='0'),
        sa.Column('first_at', sa.DateTime(), nullable=True), sa.Column('last_at', sa.DateTime(), nullable=True),
        sa.Column('stops', sa.JSON(), nullable=True), sa.Column('polyline', sa.JSON(), nullable=True), sa.Column('expense', sa.JSON(), nullable=True),
        sa.Column('built_at', sa.DateTime(), nullable=False))
    op.create_index('ix_route_days_tenant_id', 'route_days', ['tenant_id']); op.create_index('ix_route_days_user_id', 'route_days', ['user_id'])
    op.create_index('ix_route_days_day', 'route_days', ['day']); op.create_index('ix_route_days_deleted_at', 'route_days', ['deleted_at'])
    op.create_index('ux_routeday_user_day', 'route_days', ['tenant_id', 'user_id', 'day'], unique=True)
    op.add_column('users', sa.Column('vehicle', sa.String(length=20), nullable=False, server_default=''))
    op.add_column('expense_claims', sa.Column('source', sa.String(length=10), nullable=False, server_default='manual'))


def downgrade() -> None:
    op.drop_column('expense_claims', 'source')
    op.drop_column('users', 'vehicle')
    op.drop_table('route_days')
