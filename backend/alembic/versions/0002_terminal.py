"""Phase 2: terminal lifecycle and best-effort Bash command audit."""

import sqlalchemy as sa

from alembic import op

revision = "0002_terminal"
down_revision = "0001_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "terminal_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime()),
        sa.Column("connection_status", sa.String(32), nullable=False),
    )
    for column in ("user_id", "official_account_id", "started_at"):
        op.create_index(f"ix_terminal_sessions_{column}", "terminal_sessions", [column])
    op.create_table(
        "command_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "terminal_session_id",
            sa.String(32),
            sa.ForeignKey("terminal_sessions.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            nullable=False,
        ),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("cwd", sa.String(4096), nullable=False),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("audit_source", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_command_audit_terminal_session_id", "command_audit", ["terminal_session_id"]
    )
    op.create_index("ix_command_audit_timestamp", "command_audit", ["timestamp"])
    op.create_index("ix_command_audit_user_time", "command_audit", ["user_id", "timestamp"])
    op.create_index(
        "ix_command_audit_account_time", "command_audit", ["official_account_id", "timestamp"]
    )


def downgrade():
    op.drop_table("command_audit")
    op.drop_table("terminal_sessions")
