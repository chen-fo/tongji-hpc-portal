"""Phase 4A: observed Slurm jobs, runs, sync state, and attribution audit."""

import sqlalchemy as sa

from alembic import op

revision = "0004_jobs"
down_revision = "0003_files"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "slurm_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("attribution_source", sa.String(32), nullable=False),
        sa.Column("cluster_name", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("job_name", sa.String(256)),
        sa.Column("partition", sa.String(128)),
        sa.Column("submit_time", sa.DateTime(), nullable=False),
        sa.Column("start_time", sa.DateTime()),
        sa.Column("end_time", sa.DateTime()),
        sa.Column("expected_start_time", sa.DateTime()),
        sa.Column("expected_end_time", sa.DateTime()),
        sa.Column("elapsed_seconds", sa.Integer()),
        sa.Column("suspended_seconds", sa.Integer()),
        sa.Column("time_limit_seconds", sa.Integer()),
        sa.Column("requested_resources", sa.JSON()),
        sa.Column("allocated_resources", sa.JSON()),
        sa.Column("latest_state", sa.String(128), nullable=False),
        sa.Column("state_reason", sa.String(512)),
        sa.Column("exit_code", sa.String(32)),
        sa.Column("restarts", sa.Integer()),
        sa.Column("complex_history", sa.Boolean(), nullable=False),
        sa.Column("observation_status", sa.String(32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("data_source", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "official_account_id",
            "cluster_name",
            "job_id",
            "submit_time",
            name="uq_slurm_job_instance",
        ),
        sa.CheckConstraint(
            "attribution_source IN ('unassigned', 'confirmed_external', 'platform')"
        ),
        sa.CheckConstraint("observation_status IN ('observed', 'awaiting_history')"),
    )
    op.create_index("ix_slurm_jobs_official_account_id", "slurm_jobs", ["official_account_id"])
    op.create_index("ix_slurm_jobs_user_id", "slurm_jobs", ["user_id"])
    op.create_index("ix_slurm_jobs_submit_time", "slurm_jobs", ["submit_time"])
    op.create_index("ix_slurm_jobs_user_submit", "slurm_jobs", ["user_id", "submit_time"])
    op.create_index(
        "ix_slurm_jobs_account_state", "slurm_jobs", ["official_account_id", "latest_state"]
    )
    op.create_table(
        "slurm_job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slurm_job_id", sa.Integer(), sa.ForeignKey("slurm_jobs.id"), nullable=False),
        sa.Column("scheduler_record_key", sa.String(256), nullable=False),
        sa.Column("start_time", sa.DateTime()),
        sa.Column("end_time", sa.DateTime()),
        sa.Column("elapsed_seconds", sa.Integer()),
        sa.Column("suspended_seconds", sa.Integer()),
        sa.Column("allocated_resources", sa.JSON()),
        sa.Column("state", sa.String(128), nullable=False),
        sa.Column("exit_code", sa.String(32)),
        sa.UniqueConstraint("slurm_job_id", "scheduler_record_key", name="uq_slurm_job_run"),
    )
    op.create_index("ix_slurm_job_runs_slurm_job_id", "slurm_job_runs", ["slurm_job_id"])
    op.create_table(
        "job_sync_state",
        sa.Column(
            "official_account_id",
            sa.Integer(),
            sa.ForeignKey("official_accounts.id"),
            primary_key=True,
        ),
        sa.Column("last_attempt_at", sa.DateTime()),
        sa.Column("last_success_at", sa.DateTime()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error", sa.String(256)),
        sa.Column("history_supported", sa.Boolean()),
        sa.Column("timezone", sa.String(32)),
        sa.Column("versions", sa.JSON()),
        sa.Column("skipped_records", sa.Integer(), nullable=False),
    )
    op.create_table(
        "job_attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "slurm_job_id",
            sa.Integer(),
            sa.ForeignKey("slurm_jobs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("job_attributions")
    op.drop_table("job_sync_state")
    op.drop_table("slurm_job_runs")
    op.drop_table("slurm_jobs")
