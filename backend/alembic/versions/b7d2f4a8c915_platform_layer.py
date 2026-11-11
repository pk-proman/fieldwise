"""platform layer: tenant kind, setup state, contact, notes

Revision ID: b7d2f4a8c915
Revises: a1c7e2d9b413
Create Date: 2026-11-12 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7d2f4a8c915'
down_revision: Union[str, Sequence[str], None] = 'a1c7e2d9b413'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('kind', sa.String(length=20), nullable=False, server_default='client'))
    op.add_column('tenants', sa.Column('setup_completed_at', sa.DateTime(), nullable=True))
    op.add_column('tenants', sa.Column('setup_state', sa.JSON(), nullable=True))
    op.add_column('tenants', sa.Column('contact_email', sa.String(length=200), nullable=False, server_default=''))
    op.add_column('tenants', sa.Column('notes', sa.String(length=1000), nullable=False, server_default=''))
    # Every workspace that exists today is already in use: it counts as set up (it keeps working exactly as before).
    op.execute("UPDATE tenants SET setup_completed_at = created_at WHERE setup_completed_at IS NULL")


def downgrade() -> None:
    for c in ('notes', 'contact_email', 'setup_state', 'setup_completed_at', 'kind'):
        op.drop_column('tenants', c)
