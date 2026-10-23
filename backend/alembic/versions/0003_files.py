"""Phase 3: file-operation metadata, never file contents."""

import sqlalchemy as sa

from alembic import op

revision = "0003_files"
down_revision = "0002_terminal"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "file_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("remote_path", sa.String(4096), nullable=False),
        sa.Column("destination", sa.String(4096)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_file_audit_official_account_id", "file_audit", ["official_account_id"])
    op.create_index("ix_file_audit_timestamp", "file_audit", ["timestamp"])
    op.create_index("ix_file_audit_user_time", "file_audit", ["user_id", "timestamp"])


def downgrade():
    op.drop_table("file_audit")
