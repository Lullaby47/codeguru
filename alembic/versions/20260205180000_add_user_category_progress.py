"""add user category progress

Revision ID: 20260205180000
Revises: 7ea100f42b8b
Create Date: 2026-02-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260205180000'
down_revision: Union[str, Sequence[str], None] = '7ea100f42b8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_category_progress table."""
    # SQLite-compatible datetime defaults
    op.create_table(
        'user_category_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('main_category', sa.String(length=255), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('xp', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'main_category', name='uq_user_category')
    )
    op.create_index(op.f('ix_user_category_progress_id'), 'user_category_progress', ['id'], unique=False)
    op.create_index(op.f('ix_user_category_progress_user_id'), 'user_category_progress', ['user_id'], unique=False)


def downgrade() -> None:
    """Drop user_category_progress table."""
    op.drop_index(op.f('ix_user_category_progress_user_id'), table_name='user_category_progress')
    op.drop_index(op.f('ix_user_category_progress_id'), table_name='user_category_progress')
    op.drop_table('user_category_progress')

