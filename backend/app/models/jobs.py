from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.entities import utcnow


class SlurmJob(Base):
    __tablename__ = "slurm_jobs"
    __table_args__ = (
        UniqueConstraint(
            "official_account_id",
            "cluster_name",
            "job_id",
            "submit_time",
            name="uq_slurm_job_instance",
        ),
        CheckConstraint("attribution_source IN ('unassigned', 'confirmed_external', 'platform')"),
        CheckConstraint("observation_status IN ('observed', 'awaiting_history')"),
        Index("ix_slurm_jobs_user_submit", "user_id", "submit_time"),
        Index("ix_slurm_jobs_account_state", "official_account_id", "latest_state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    official_account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    attribution_source: Mapped[str] = mapped_column(String(32), default="unassigned")
    cluster_name: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str] = mapped_column(String(128))
    job_name: Mapped[str | None] = mapped_column(String(256))
    partition: Mapped[str | None] = mapped_column(String(128))
    submit_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime)
    end_time: Mapped[datetime | None] = mapped_column(DateTime)
    expected_start_time: Mapped[datetime | None] = mapped_column(DateTime)
    expected_end_time: Mapped[datetime | None] = mapped_column(DateTime)
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer)
    suspended_seconds: Mapped[int | None] = mapped_column(Integer)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer)
    requested_resources: Mapped[dict | None] = mapped_column(JSON)
    allocated_resources: Mapped[dict | None] = mapped_column(JSON)
    latest_state: Mapped[str] = mapped_column(String(128))
    state_reason: Mapped[str | None] = mapped_column(String(512))
    exit_code: Mapped[str | None] = mapped_column(String(32))
    restarts: Mapped[int | None] = mapped_column(Integer)
    complex_history: Mapped[bool] = mapped_column(Boolean, default=True)
    observation_status: Mapped[str] = mapped_column(String(32), default="observed")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    data_source: Mapped[str] = mapped_column(String(32))


class SlurmJobRun(Base):
    __tablename__ = "slurm_job_runs"
    __table_args__ = (
        UniqueConstraint("slurm_job_id", "scheduler_record_key", name="uq_slurm_job_run"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slurm_job_id: Mapped[int] = mapped_column(ForeignKey("slurm_jobs.id"), index=True)
    scheduler_record_key: Mapped[str] = mapped_column(String(256))
    start_time: Mapped[datetime | None] = mapped_column(DateTime)
    end_time: Mapped[datetime | None] = mapped_column(DateTime)
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer)
    suspended_seconds: Mapped[int | None] = mapped_column(Integer)
    allocated_resources: Mapped[dict | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(128))
    exit_code: Mapped[str | None] = mapped_column(String(32))


class JobSyncState(Base):
    __tablename__ = "job_sync_state"

    official_account_id: Mapped[int] = mapped_column(
        ForeignKey("official_accounts.id"), primary_key=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_full_history_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="idle")
    error: Mapped[str | None] = mapped_column(String(256))
    history_supported: Mapped[bool | None] = mapped_column(Boolean)
    timezone: Mapped[str | None] = mapped_column(String(32))
    versions: Mapped[dict | None] = mapped_column(JSON)
    skipped_records: Mapped[int] = mapped_column(Integer, default=0)


class JobAttribution(Base):
    __tablename__ = "job_attributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    slurm_job_id: Mapped[int] = mapped_column(ForeignKey("slurm_jobs.id"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
