"""Internal, auditable billing data; no school payment credentials or passwords."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.entities import utcnow


class BillingRateSnapshot(Base):
    __tablename__ = "billing_rate_snapshots"
    __table_args__ = (
        UniqueConstraint("cluster_name", "partition", "version"),
        UniqueConstraint("cluster_name", "partition", "effective_from"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    cluster_name: Mapped[str] = mapped_column(String(128))
    partition: Mapped[str] = mapped_column(String(128))
    # Exact decimal strings; SQLite NUMERIC/float must not round the rate.
    cpu_price_per_core_hour: Mapped[str | None] = mapped_column(String(40))
    physical_cores_per_slurm_cpu: Mapped[str | None] = mapped_column(String(40))
    gpu_rates: Mapped[str] = mapped_column(Text)
    charge_cpu_with_gpu: Mapped[bool | None] = mapped_column()
    source_url: Mapped[str] = mapped_column(String(2048))
    verified_at: Mapped[date] = mapped_column(Date)
    effective_from: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class UserBudget(Base):
    __tablename__ = "user_budgets"
    __table_args__ = (
        CheckConstraint("limit_minor >= 0"),
        CheckConstraint("warning_threshold BETWEEN 1 AND 100"),
        CheckConstraint("control_mode = 'observe'"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    limit_minor: Mapped[int] = mapped_column(BigInteger)
    warning_threshold: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    control_mode: Mapped[str] = mapped_column(String(20), default="observe")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BudgetChangeAudit(Base):
    __tablename__ = "budget_change_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("user_budgets.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    previous_values: Mapped[str | None] = mapped_column(Text)
    new_values: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(512))


class BillingEntry(Base):
    __tablename__ = "billing_entries"
    __table_args__ = (
        CheckConstraint("entry_type IN ('job_cost', 'official_adjustment', 'manual_adjustment')"),
        CheckConstraint("reconciliation_status IN ('pending', 'reconciled')"),
        CheckConstraint("amount_minor BETWEEN -1000000000000000 AND 1000000000000000"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    official_account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    budget_id: Mapped[int | None] = mapped_column(ForeignKey("user_budgets.id"))
    slurm_job_id: Mapped[int | None] = mapped_column(ForeignKey("slurm_jobs.id"), unique=True)
    rate_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("billing_rate_snapshots.id"))
    entry_type: Mapped[str] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    source: Mapped[str] = mapped_column(String(32))
    reconciliation_status: Mapped[str] = mapped_column(String(16), default="pending")
    statement_reference: Mapped[str | None] = mapped_column(String(256))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class BillingEvidence(Base):
    """Immutable evidence captured at the time of a job charge."""

    __tablename__ = "billing_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    billing_entry_id: Mapped[int] = mapped_column(ForeignKey("billing_entries.id"), unique=True)
    evidence_json: Mapped[str] = mapped_column(Text)


BillingEntry.evidence = relationship(BillingEvidence, uselist=False)
