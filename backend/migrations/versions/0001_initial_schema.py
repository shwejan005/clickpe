"""Create the current loan-engine schema for new databases.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op

from app.main import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all only creates missing tables; the following compatibility
    # revision upgrades older local databases without dropping their data.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # The baseline may contain user data, so destructive automatic rollback is disabled.
    pass
