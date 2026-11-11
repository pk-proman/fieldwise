"""device health reports from the phone

Revision ID: a1c7e2d9b413
Revises: 6deb0ad4519f
Create Date: 2026-11-11 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1c7e2d9b413'
down_revision: Union[str, Sequence[str], None] = '6deb0ad4519f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('device_health',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('reported_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_device_health_deleted_at'), 'device_health', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_device_health_tenant_id'), 'device_health', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_device_health_user_id'), 'device_health', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_device_health_user_id'), table_name='device_health')
    op.drop_index(op.f('ix_device_health_tenant_id'), table_name='device_health')
    op.drop_index(op.f('ix_device_health_deleted_at'), table_name='device_health')
    op.drop_table('device_health')
