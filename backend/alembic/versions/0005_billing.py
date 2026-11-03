"""Phase 4: internal observation-only billing and immutable ledger."""

import sqlalchemy as sa

from alembic import op

revision = "0005_billing"
down_revision = "0004_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "billing_rate_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("cluster_name", sa.String(128), nullable=False),
        sa.Column("partition", sa.String(128), nullable=False),
        sa.Column("cpu_price_per_core_hour", sa.String(40)),
        sa.Column("physical_cores_per_slurm_cpu", sa.String(40)),
        sa.Column("gpu_rates", sa.Text(), nullable=False),
        sa.Column("charge_cpu_with_gpu", sa.Boolean()),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("verified_at", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cluster_name", "partition", "version"),
        sa.UniqueConstraint("cluster_name", "partition", "effective_from"),
    )
    op.create_table(
        "user_budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("limit_minor", sa.BigInteger(), nullable=False),
        sa.Column("warning_threshold", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("control_mode", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("limit_minor >= 0"),
        sa.CheckConstraint("warning_threshold BETWEEN 1 AND 100"),
        sa.CheckConstraint("control_mode = 'observe'"),
    )
    op.create_table(
        "budget_change_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("budget_id", sa.Integer(), sa.ForeignKey("user_budgets.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("previous_values", sa.Text()),
        sa.Column("new_values", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
    )
    op.create_index("ix_budget_change_audit_budget_id", "budget_change_audit", ["budget_id"])
    op.create_table(
        "billing_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("budget_id", sa.Integer(), sa.ForeignKey("user_budgets.id")),
        sa.Column("slurm_job_id", sa.Integer(), sa.ForeignKey("slurm_jobs.id"), unique=True),
        sa.Column("rate_snapshot_id", sa.Integer(), sa.ForeignKey("billing_rate_snapshots.id")),
        sa.Column("entry_type", sa.String(32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reconciliation_status", sa.String(16), nullable=False),
        sa.Column("statement_reference", sa.String(256)),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('job_cost', 'official_adjustment', 'manual_adjustment')"
        ),
        sa.CheckConstraint("reconciliation_status IN ('pending', 'reconciled')"),
        sa.CheckConstraint("amount_minor BETWEEN -1000000000000000 AND 1000000000000000"),
    )
    op.create_index(
        "ix_billing_entries_official_account_id", "billing_entries", ["official_account_id"]
    )
    op.create_index("ix_billing_entries_user_id", "billing_entries", ["user_id"])
    op.create_index("ix_billing_entries_created_at", "billing_entries", ["created_at"])
    for table in ("billing_rate_snapshots", "billing_entries", "budget_change_audit"):
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable billing record'); END"
            )


def downgrade():
    for table in (
        "billing_entries",
        "budget_change_audit",
        "user_budgets",
        "billing_rate_snapshots",
    ):
        op.drop_table(table)
