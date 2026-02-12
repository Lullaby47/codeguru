"""add last_active column to users table

Revision ID: 20260212180000
Revises: 20260205180000
Create Date: 2026-02-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260212180000'
down_revision: Union[str, Sequence[str], None] = '20260205180000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add last_active column to users for online status tracking."""
    op.add_column('users', sa.Column('last_active', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Remove last_active column from users."""
    op.drop_column('users', 'last_active')
