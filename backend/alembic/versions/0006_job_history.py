"""Persist the last complete lookback to recover late accounting records."""

import sqlalchemy as sa

from alembic import op

revision = "0006_job_history"
down_revision = "0005_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_sync_state", sa.Column("last_full_history_at", sa.DateTime()))


def downgrade():
    op.drop_column("job_sync_state", "last_full_history_at")
