"""Phase 0/1: official accounts, users, persisted sessions and login throttles."""

import sqlalchemy as sa

from alembic import op

revision = "0001_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "official_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("alias", sa.String(64), nullable=False, unique=True),
        sa.Column("ssh_host", sa.String(253), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(64), nullable=False),
        sa.Column("ssh_key_reference", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("ssh_port BETWEEN 1 AND 65535"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column("remote_home", sa.String(1024)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'user')"),
        sa.CheckConstraint("status IN ('active', 'disabled')"),
        sa.CheckConstraint(
            "role = 'admin' OR (official_account_id IS NOT NULL AND remote_home IS NOT NULL)"
        ),
    )
    op.create_index("ix_users_official_account_id", "users", ["official_account_id"])
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("login_time", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("logout_time", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("client_ip", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])
    op.create_table(
        "login_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_login_throttles_window_start", "login_throttles", ["window_start"])


def downgrade():
    for table in ("login_throttles", "login_sessions", "users", "official_accounts"):
        op.drop_table(table)
