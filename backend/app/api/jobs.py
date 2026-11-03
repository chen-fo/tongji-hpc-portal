"""Job views and explicit administrator attribution. No remote commands in HTTP handlers."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, or_, select

from app.models.entities import OfficialAccount, User, utcnow
from app.models.jobs import JobAttribution, JobSyncState, SlurmJob
from app.schemas.accounts import StrictModel
from app.security.dependencies import DB, AdminUser, CurrentUser
from app.ssh.slurm import ACTIVE, PENDING, TERMINAL

router = APIRouter(prefix="/api", tags=["jobs"])


class Assignment(StrictModel):
    user_id: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason", mode="before")
    @classmethod
    def trim_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


def iso_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def state_filter(values):
    # Slurm can append '+' for truncated states and 'by UID' for cancellation.
    return or_(
        *(
            or_(
                SlurmJob.latest_state == name,
                SlurmJob.latest_state.like(name + "+%"),
                SlurmJob.latest_state.like(name + " %"),
            )
            for name in values
        )
    )


def serialize(job, alias, username):
    return {
        "id": job.id,
        "official_account_id": job.official_account_id,
        "account_alias": alias,
        "user_id": job.user_id,
        "username": username,
        "attribution_source": job.attribution_source,
        "cluster_name": job.cluster_name,
        "job_id": job.job_id,
        "job_name": job.job_name,
        "partition": job.partition,
        "state": job.latest_state,
        "state_reason": job.state_reason,
        "submit_time": iso_utc(job.submit_time),
        "start_time": iso_utc(job.start_time),
        "end_time": iso_utc(job.end_time),
        "expected_start_time": iso_utc(job.expected_start_time),
        "expected_end_time": iso_utc(job.expected_end_time),
        "elapsed_seconds": job.elapsed_seconds,
        "suspended_seconds": job.suspended_seconds,
        "time_limit_seconds": job.time_limit_seconds,
        "requested_resources": job.requested_resources,
        "allocated_resources": job.allocated_resources,
        "exit_code": job.exit_code,
        "restarts": job.restarts,
        "complex_history": job.complex_history,
        "observation_status": job.observation_status,
        "last_synced_at": iso_utc(job.last_synced_at),
        "data_source": job.data_source,
    }


def sync_states(request, user, db):
    query = select(OfficialAccount, JobSyncState).outerjoin(
        JobSyncState, JobSyncState.official_account_id == OfficialAccount.id
    )
    if user.role != "admin":
        query = query.where(OfficialAccount.id == user.official_account_id)
    now = utcnow()
    states = []
    for account, sync in db.execute(query.order_by(OfficialAccount.id)):
        stale = (
            not account.enabled
            or not sync
            or sync.status != "ok"
            or not sync.last_success_at
            or now - sync.last_success_at
            > timedelta(seconds=request.app.state.settings.slurm_sync_interval_seconds * 2 + 5)
        )
        states.append(
            {
                "official_account_id": account.id,
                "account_alias": account.alias,
                "last_attempt_at": iso_utc(sync.last_attempt_at) if sync else None,
                "last_success_at": iso_utc(sync.last_success_at) if sync else None,
                "status": sync.status if sync else "idle",
                "stale": bool(stale),
                "error": sync.error if sync else None,
                "history_supported": sync.history_supported if sync else None,
                "versions": sync.versions if sync else None,
                "timezone": sync.timezone if sync else None,
                "skipped_records": sync.skipped_records if sync else 0,
            }
        )
    return states


def list_jobs(request, user, db, status, official_account_id, username, start, end, limit, offset):
    if start and end and utc_naive(start) > utc_naive(end):
        raise HTTPException(400, "开始时间不能晚于结束时间")
    query = (
        select(SlurmJob, OfficialAccount.alias, User.username)
        .join(OfficialAccount, OfficialAccount.id == SlurmJob.official_account_id)
        .outerjoin(User, User.id == SlurmJob.user_id)
    )
    if user.role != "admin":
        query = query.where(SlurmJob.user_id == user.id)
    if official_account_id is not None:
        query = query.where(SlurmJob.official_account_id == official_account_id)
    if username and user.role == "admin":
        query = query.where(User.username == username)
    if status:
        states = {"running": ACTIVE, "pending": PENDING, "history": TERMINAL}[status]
        query = query.where(SlurmJob.observation_status == "observed", state_filter(states))
    if start:
        query = query.where(SlurmJob.submit_time >= utc_naive(start))
    if end:
        query = query.where(SlurmJob.submit_time <= utc_naive(end))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(SlurmJob.submit_time.desc(), SlurmJob.id.desc()).offset(offset).limit(limit)
    )
    return {
        "items": [serialize(job, alias, owner) for job, alias, owner in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "sync": sync_states(request, user, db),
    }


@router.get("/jobs")
def jobs(
    request: Request,
    user: CurrentUser,
    db: DB,
    status: Literal["running", "pending", "history"] | None = None,
    official_account_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return list_jobs(
        request, user, db, status, official_account_id, None, start, end, limit, offset
    )


@router.get("/admin/jobs")
def admin_jobs(
    request: Request,
    admin: AdminUser,
    db: DB,
    status: Literal["running", "pending", "history"] | None = None,
    official_account_id: int | None = None,
    username: str | None = Query(default=None, max_length=32),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return list_jobs(
        request, admin, db, status, official_account_id, username, start, end, limit, offset
    )


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, user: CurrentUser, db: DB):
    query = (
        select(SlurmJob, OfficialAccount.alias, User.username)
        .join(OfficialAccount, OfficialAccount.id == SlurmJob.official_account_id)
        .outerjoin(User, User.id == SlurmJob.user_id)
        .where(SlurmJob.id == job_id)
    )
    if user.role != "admin":
        query = query.where(SlurmJob.user_id == user.id)
    row = db.execute(query).first()
    if not row:
        raise HTTPException(404, "任务不存在")
    return serialize(*row)


@router.post("/admin/jobs/{job_id}/assign")
def assign_job(job_id: int, data: Assignment, admin: AdminUser, db: DB):
    actor_id = admin.id
    db.rollback()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    job = db.get(SlurmJob, job_id)
    member = db.get(User, data.user_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job.user_id is not None or job.attribution_source != "unassigned":
        raise HTTPException(409, "任务已有归属，不能直接覆盖")
    if not member or member.role != "user" or member.official_account_id != job.official_account_id:
        raise HTTPException(400, "只能归属给同一官方账号下的组内用户")
    job.user_id, job.attribution_source = member.id, "confirmed_external"
    db.add(
        JobAttribution(
            slurm_job_id=job.id, user_id=member.id, actor_id=actor_id, reason=data.reason
        )
    )
    db.commit()
    account = db.get(OfficialAccount, job.official_account_id)
    return serialize(job, account.alias, member.username)
