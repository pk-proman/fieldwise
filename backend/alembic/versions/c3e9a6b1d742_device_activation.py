"""device activation: app_devices, activation_pins

Revision ID: c3e9a6b1d742
Revises: b7d2f4a8c915
Create Date: 2026-11-13 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3e9a6b1d742'
down_revision: Union[str, Sequence[str], None] = 'b7d2f4a8c915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('app_devices',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('secret_hash', sa.String(64), nullable=False), sa.Column('scope', sa.String(10), nullable=False, server_default='field'),
        sa.Column('status', sa.String(10), nullable=False, server_default='active'), sa.Column('label', sa.String(200), nullable=False, server_default=''),
        sa.Column('model', sa.String(80), nullable=False, server_default=''), sa.Column('app_version', sa.String(20), nullable=False, server_default=''),
        sa.Column('phone', sa.String(30), nullable=False, server_default=''), sa.Column('pending_phone', sa.String(30), nullable=False, server_default=''),
        sa.Column('activated_at', sa.DateTime(), nullable=False), sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True), sa.Column('revoked_reason', sa.String(60), nullable=False, server_default=''))
    op.create_index('ix_app_devices_tenant_id', 'app_devices', ['tenant_id']); op.create_index('ix_app_devices_user_id', 'app_devices', ['user_id'])
    op.create_index('ix_app_devices_deleted_at', 'app_devices', ['deleted_at'])
    op.create_table('activation_pins',
        sa.Column('id', sa.Integer(), primary_key=True), sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False), sa.Column('phone', sa.String(15), nullable=False),
        sa.Column('pin_hash', sa.String(64), nullable=False), sa.Column('scope', sa.String(10), nullable=False, server_default='field'),
        sa.Column('expires_at', sa.DateTime(), nullable=False), sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'), sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('revoked', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index('ix_activation_pins_tenant_id', 'activation_pins', ['tenant_id']); op.create_index('ix_activation_pins_user_id', 'activation_pins', ['user_id'])
    op.create_index('ix_activation_pins_phone', 'activation_pins', ['phone']); op.create_index('ix_activation_pins_deleted_at', 'activation_pins', ['deleted_at'])


def downgrade() -> None:
    op.drop_table('activation_pins')
    op.drop_table('app_devices')
