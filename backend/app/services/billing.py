"""Observation-only internal billing. Never calls Slurm or creates jobs."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.billing import (
    BillingEntry,
    BillingEvidence,
    BillingRateSnapshot,
    BudgetChangeAudit,
    UserBudget,
)
from app.models.entities import OfficialAccount, User, utcnow
from app.models.jobs import SlurmJob

NOTICE = "仅供内部观察；不含待核算作业，不限制终端提交，学校账单为最终依据"
FINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
}


def _begin_write(db):
    # Dependencies have read current user/session; release the read transaction
    # before serializing a financial state change in a short SQLite transaction.
    db.rollback()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "数据冲突，请刷新后重试") from None


def _validate_total(db, user_id, addition):
    current = db.scalar(
        select(func.coalesce(func.sum(BillingEntry.amount_minor), 0)).where(
            BillingEntry.user_id == user_id
        )
    )
    # Bound both the entry and aggregate below JavaScript's safe integer range.
    if abs(current + addition) > 10**15:
        raise HTTPException(409, "用户累计金额超出系统支持范围")


def budget_view(budget):
    if budget is None:
        return None
    return {
        "limit_minor": budget.limit_minor,
        "warning_threshold": budget.warning_threshold,
        "version": budget.version,
        "control_mode": budget.control_mode,
        "updated_at": budget.updated_at,
    }


def summary(db, user):
    budget = db.scalar(select(UserBudget).where(UserBudget.user_id == user.id))
    spent = db.scalar(
        select(func.coalesce(func.sum(BillingEntry.amount_minor), 0)).where(
            BillingEntry.user_id == user.id
        )
    )
    return {
        "user_id": user.id,
        "username": user.username,
        "currency": "CNY",
        "budget": budget_view(budget),
        "recorded_minor": spent,
        # A reservation/submit service does not exist yet. Zero or a computed
        # remaining balance would imply an enforceable number, so return null.
        "reserved_minor": None,
        "available_minor": None,
        "running_estimate_minor": None,
        "notice": NOTICE,
    }


def summaries(db):
    users = db.scalars(select(User).where(User.role == "user").order_by(User.id)).all()
    return {"items": [summary(db, user) for user in users]}


def update_budget(db, user_id, actor_id, data):
    _begin_write(db)
    user = db.get(User, user_id)
    if not user or user.role != "user":
        raise HTTPException(404, "组内用户不存在")
    budget = db.scalar(select(UserBudget).where(UserBudget.user_id == user_id))
    current_version = budget.version if budget else 0
    if current_version != data.expected_version:
        raise HTTPException(409, "预算已更新，请刷新后重试")
    previous = budget_view(budget)
    if budget is None:
        budget = UserBudget(user_id=user_id, currency="CNY", control_mode="observe", version=1)
        db.add(budget)
    else:
        budget.version += 1
    budget.limit_minor = data.limit_minor
    budget.warning_threshold = data.warning_threshold
    budget.updated_by = actor_id
    budget.updated_at = utcnow()
    db.flush()
    current = budget_view(budget)
    db.add(
        BudgetChangeAudit(
            budget_id=budget.id,
            actor_id=actor_id,
            previous_values=json.dumps(previous, default=str, ensure_ascii=False)
            if previous
            else None,
            new_values=json.dumps(current, default=str, ensure_ascii=False),
            reason=data.reason,
        )
    )
    _commit(db)
    return summary(db, user)


def rate_view(rate):
    return {
        "id": rate.id,
        "version": rate.version,
        "currency": rate.currency,
        "cluster_name": rate.cluster_name,
        "partition": rate.partition,
        "cpu_price_per_core_hour": rate.cpu_price_per_core_hour,
        "physical_cores_per_slurm_cpu": rate.physical_cores_per_slurm_cpu,
        "gpu_rates": json.loads(rate.gpu_rates),
        "charge_cpu_with_gpu": rate.charge_cpu_with_gpu,
        "source_url": rate.source_url,
        "verified_at": rate.verified_at,
        "effective_from": rate.effective_from.isoformat() + "Z",
        "created_by": rate.created_by,
        "created_at": rate.created_at,
    }


def create_rate(db, actor_id, data):
    _begin_write(db)
    query = select(func.max(BillingRateSnapshot.version)).where(
        BillingRateSnapshot.cluster_name == data.cluster_name,
        BillingRateSnapshot.partition == data.partition,
    )
    version = (db.scalar(query) or 0) + 1
    rate = BillingRateSnapshot(
        version=version,
        currency="CNY",
        cluster_name=data.cluster_name,
        partition=data.partition,
        cpu_price_per_core_hour=data.cpu_price_per_core_hour,
        physical_cores_per_slurm_cpu=data.physical_cores_per_slurm_cpu,
        gpu_rates=json.dumps([item.model_dump() for item in data.gpu_rates], sort_keys=True),
        charge_cpu_with_gpu=data.charge_cpu_with_gpu,
        source_url=data.source_url,
        verified_at=data.verified_at,
        effective_from=data.effective_from,
        created_by=actor_id,
    )
    db.add(rate)
    _commit(db)
    return rate_view(rate)


def entry_view(row):
    return {
        "id": row.id,
        "official_account_id": row.official_account_id,
        "user_id": row.user_id,
        "slurm_job_id": row.slurm_job_id,
        "rate_snapshot_id": row.rate_snapshot_id,
        "entry_type": row.entry_type,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "source": row.source,
        "reconciliation_status": row.reconciliation_status,
        "reason": row.reason,
        "statement_reference": row.statement_reference,
        "created_at": row.created_at,
        "evidence": json.loads(row.evidence.evidence_json) if row.evidence else None,
    }


def add_adjustment(db, actor_id, data):
    _begin_write(db)
    key = "adjust:" + data.idempotency_key
    existing = db.scalar(select(BillingEntry).where(BillingEntry.idempotency_key == key))
    if existing:
        if any(
            getattr(existing, name) != getattr(data, name)
            for name in ("user_id", "entry_type", "amount_minor", "reason", "statement_reference")
        ):
            raise HTTPException(409, "此幂等键已用于另一笔调整")
        return entry_view(existing)
    user = db.get(User, data.user_id)
    if not user or user.role != "user" or not user.official_account_id:
        raise HTTPException(404, "组内用户不存在或未绑定官方账号")
    budget = db.scalar(select(UserBudget).where(UserBudget.user_id == user.id))
    _validate_total(db, user.id, data.amount_minor)
    row = BillingEntry(
        official_account_id=user.official_account_id,
        user_id=user.id,
        budget_id=budget.id if budget else None,
        entry_type=data.entry_type,
        amount_minor=data.amount_minor,
        currency="CNY",
        source="official_statement" if data.entry_type == "official_adjustment" else "manual",
        reconciliation_status="pending",
        statement_reference=data.statement_reference,
        idempotency_key=key,
        actor_id=actor_id,
        reason=data.reason,
    )
    db.add(row)
    _commit(db)
    return entry_view(row)


def _unknown(job, reason, snapshot=None):
    return {
        "job_id": job.id,
        "status": "unpriced",
        "amount_minor": None,
        "rate_snapshot_id": snapshot.id if snapshot else None,
        "reason": reason,
        "as_of": job.last_synced_at,
        "reconciliation_status": None,
    }


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError
    text = str(value)
    if not re.fullmatch(r"[0-9]{1,7}", text):
        raise ValueError
    value = int(text)
    if value > 1_000_000:
        raise ValueError
    return value


def _gpu_resources(resources):
    """Return total and model counts, rejecting contradictory or malformed TRES."""
    if not isinstance(resources, dict):
        raise ValueError("缺少 GPU 资源信息")
    models = {}
    has_models = False
    for key, value in resources.items():
        if not isinstance(key, str):
            raise ValueError("GPU 资源信息格式不受支持")
        if key.startswith("gres/gpu:"):
            has_models = True
            if not key.removeprefix("gres/gpu:"):
                raise ValueError("GPU 型号无效")
            count = _quantity(value)
            if count:
                models[key] = count
        elif key.startswith("gres/gpu") and key != "gres/gpu":
            raise ValueError("GPU 资源信息格式不受支持")
    model_total = sum(models.values())
    if "gres/gpu" in resources:
        total = _quantity(resources["gres/gpu"])
        if has_models and total != model_total:
            raise ValueError("GPU 总数与型号数量不一致")
    else:
        total = model_total
    return total, models


def _verify_requested_gpu(requested, allocated):
    allocated_total, allocated_models = _gpu_resources(allocated)
    if not isinstance(requested, dict) or not requested:
        raise ValueError("缺少 GPU 申请信息")
    requested_total, requested_models = _gpu_resources(requested)
    if requested_total != allocated_total:
        raise ValueError("申请和分配的 GPU 总数不一致")
    if requested_models and requested_models != allocated_models:
        raise ValueError("申请和分配的 GPU 型号不一致")


def _calculate(snapshot, resources, seconds):
    """Apply only the explicit, administrator-confirmed complete resource rule."""
    if not isinstance(resources, dict) or not resources:
        raise ValueError("缺少已分配资源信息")
    # Unknown generic resources could have additional pricing not represented.
    if any(key.startswith("gres/") and not key.startswith("gres/gpu") for key in resources):
        raise ValueError("包含未核实计价规则的其他资源")
    total_gpu, gpu = _gpu_resources(resources)
    if total_gpu and not gpu:
        raise ValueError("GPU 型号或数量不完整")
    prices = {
        item["tres_key"]: Decimal(item["price_per_hour"]) for item in json.loads(snapshot.gpu_rates)
    }
    if any(key not in prices for key in gpu):
        raise ValueError("GPU 型号尚未配置已核实费率")
    if gpu and snapshot.charge_cpu_with_gpu is None:
        raise ValueError("尚未核实 GPU 作业是否另计 CPU 费用")
    include_cpu = not gpu or snapshot.charge_cpu_with_gpu
    with localcontext() as ctx:
        ctx.prec = 60
        rate_total = sum((Decimal(count) * prices[key] for key, count in gpu.items()), Decimal(0))
        if include_cpu:
            if (
                snapshot.cpu_price_per_core_hour is None
                or snapshot.physical_cores_per_slurm_cpu is None
            ):
                raise ValueError("CPU 单价或物理核换算规则尚未核实")
            if "cpu" not in resources:
                raise ValueError("缺少 CPU 分配数量")
            count = _quantity(resources["cpu"])
            if count == 0:
                raise ValueError("CPU 分配数量无效")
            rate_total += (
                Decimal(count)
                * Decimal(snapshot.physical_cores_per_slurm_cpu)
                * Decimal(snapshot.cpu_price_per_core_hour)
            )
        fen = (rate_total * Decimal(seconds) * Decimal(100) / Decimal(3600)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        if fen > 10**15:
            raise ValueError("计算金额超出系统范围")
        return int(fen)


def _job_evidence(job):
    fields = (
        "id",
        "official_account_id",
        "user_id",
        "attribution_source",
        "cluster_name",
        "job_id",
        "partition",
        "submit_time",
        "start_time",
        "end_time",
        "elapsed_seconds",
        "suspended_seconds",
        "requested_resources",
        "allocated_resources",
        "latest_state",
        "restarts",
        "complex_history",
        "observation_status",
        "last_synced_at",
        "data_source",
    )
    return json.loads(json.dumps({name: getattr(job, name) for name in fields}, default=str))


def _duplicate_reason(db, job):
    # Include unassigned and disabled accounts; both can still have duplicate records.
    if db.scalar(
        select(SlurmJob.id)
        .where(
            SlurmJob.id != job.id,
            SlurmJob.official_account_id == job.official_account_id,
            SlurmJob.cluster_name == job.cluster_name,
            SlurmJob.job_id == job.job_id,
            SlurmJob.submit_time != job.submit_time,
        )
        .limit(1)
    ):
        return "同一账号存在相同作业号的其他提交记录，须人工核对"
    account = db.get(OfficialAccount, job.official_account_id)
    if not account:
        return "官方账号不存在，须人工核对"
    if db.scalar(
        select(SlurmJob.id)
        .join(OfficialAccount, SlurmJob.official_account_id == OfficialAccount.id)
        .where(
            SlurmJob.official_account_id != job.official_account_id,
            OfficialAccount.ssh_username == account.ssh_username,
            SlurmJob.cluster_name == job.cluster_name,
            SlurmJob.job_id == job.job_id,
        )
        .limit(1)
    ):
        return "不同官方账号存在疑似重复作业，须人工核对"
    return None


def estimate_job(db, job):
    booked = db.scalar(select(BillingEntry).where(BillingEntry.slurm_job_id == job.id))
    if booked:
        evidence = json.loads(booked.evidence.evidence_json) if booked.evidence else None
        duplicate = _duplicate_reason(db, job)
        current_job = _job_evidence(job)
        recorded_job = evidence.get("job", {}) if evidence else {}
        # A later successful poll alone does not change the charged facts.
        changed = evidence and any(
            recorded_job.get(key) != value
            for key, value in current_job.items()
            if key != "last_synced_at"
        )
        warnings = []
        if evidence is None:
            warnings.append("历史流水缺少入账证据，须人工核对")
        if duplicate:
            warnings.append(duplicate)
        if changed:
            warnings.append("调度器数据已变化，须人工核对")
        return {
            "job_id": job.id,
            "status": "recorded",
            "amount_minor": booked.amount_minor,
            "rate_snapshot_id": booked.rate_snapshot_id,
            "reason": "；".join(warnings) if warnings else None,
            "as_of": evidence.get("job", {}).get("last_synced_at") if evidence else None,
            "reconciliation_status": booked.reconciliation_status,
            "evidence": evidence,
        }
    duplicate = _duplicate_reason(db, job)
    if duplicate:
        return _unknown(job, duplicate)
    if not job.user_id or job.attribution_source not in {"confirmed_external", "platform"}:
        return _unknown(job, "任务尚未确认归属")
    owner = db.get(User, job.user_id)
    if not owner or owner.role != "user" or owner.official_account_id != job.official_account_id:
        return _unknown(job, "任务归属与官方账号映射不一致")
    if job.observation_status != "observed":
        return _unknown(job, "任务历史数据待同步")
    if not re.fullmatch(r"[0-9]+", job.job_id) or job.complex_history or job.restarts != 0:
        return _unknown(job, "复杂、重排队或执行历史不完整的任务须人工核算")
    if job.data_source not in {"sacct", "squeue+sacct"}:
        return _unknown(job, "缺少完整的调度器资源记账记录")
    if job.suspended_seconds != 0:
        return _unknown(job, "挂起时间未知或不为零，须人工核算")
    if (
        not job.start_time
        or job.elapsed_seconds is None
        or not 0 < job.elapsed_seconds <= 315_576_000
    ):
        return _unknown(job, "尚无可核实的开始时间或运行秒数")
    normalized_state = job.latest_state.split()[0].rstrip("+")
    if normalized_state not in FINAL_STATES | {"RUNNING"}:
        return _unknown(job, "当前任务状态不能进行核算")
    if job.data_source == "squeue+sacct" and normalized_state != "RUNNING":
        return _unknown(job, "缺少可核实的历史任务记录")
    if normalized_state in FINAL_STATES and not job.end_time:
        return _unknown(job, "尚无可核实的结束时间")
    try:
        _verify_requested_gpu(job.requested_resources, job.allocated_resources)
    except ValueError:
        return _unknown(job, "申请和分配的 GPU 信息不完整或不一致")
    if job.end_time and (
        job.end_time < job.start_time
        or job.elapsed_seconds > int((job.end_time - job.start_time).total_seconds()) + 1
    ):
        return _unknown(job, "运行时间与起止时间不一致")
    if not job.partition:
        return _unknown(job, "缺少分区")
    rate = db.scalar(
        select(BillingRateSnapshot)
        .where(
            BillingRateSnapshot.cluster_name == job.cluster_name,
            BillingRateSnapshot.partition == job.partition,
            BillingRateSnapshot.effective_from <= job.start_time,
        )
        .order_by(BillingRateSnapshot.effective_from.desc(), BillingRateSnapshot.id.desc())
        .limit(1)
    )
    if not rate:
        return _unknown(job, "该集群和分区没有适用的已核实费率")
    observed_until = job.end_time or job.last_synced_at
    if not observed_until or observed_until < job.start_time:
        return _unknown(job, "无法确认任务的观察时间范围", rate)
    crosses_rate_change = db.scalar(
        select(BillingRateSnapshot.id)
        .where(
            BillingRateSnapshot.cluster_name == job.cluster_name,
            BillingRateSnapshot.partition == job.partition,
            BillingRateSnapshot.effective_from > job.start_time,
            BillingRateSnapshot.effective_from < observed_until,
        )
        .limit(1)
    )
    if crosses_rate_change:
        return _unknown(job, "任务跨越费率变更时间，须人工分段核算", rate)
    try:
        amount = _calculate(rate, job.allocated_resources, job.elapsed_seconds)
    except (ValueError, InvalidOperation, TypeError, KeyError):
        return _unknown(job, "资源或计费规则不完整，需管理员核实", rate)
    return {
        "job_id": job.id,
        "status": "estimated",
        "amount_minor": amount,
        "rate_snapshot_id": rate.id,
        "reason": "内部估算，待核对学校账单",
        "as_of": job.last_synced_at,
        "reconciliation_status": None,
    }


def get_job(db, current_user, job_id):
    job = db.get(SlurmJob, job_id)
    if not job or (current_user.role != "admin" and job.user_id != current_user.id):
        raise HTTPException(404, "任务不存在")
    return estimate_job(db, job)


def settle_job(db, actor_id, job_id):
    _begin_write(db)
    job = db.get(SlurmJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    existing = db.scalar(select(BillingEntry).where(BillingEntry.slurm_job_id == job_id))
    if existing:
        return estimate_job(db, job)
    normalized_state = job.latest_state.split()[0].rstrip("+")
    if normalized_state not in FINAL_STATES or not job.end_time:
        raise HTTPException(409, "任务尚未有已确认的最终状态和结束时间")
    if job.data_source != "sacct":
        raise HTTPException(409, "尚无调度器历史记账记录")
    result = estimate_job(db, job)
    if result["status"] != "estimated":
        raise HTTPException(409, result["reason"] or "任务尚不可核算")
    budget = db.scalar(select(UserBudget).where(UserBudget.user_id == job.user_id))
    _validate_total(db, job.user_id, result["amount_minor"])
    row = BillingEntry(
        official_account_id=job.official_account_id,
        user_id=job.user_id,
        budget_id=budget.id if budget else None,
        slurm_job_id=job.id,
        rate_snapshot_id=result["rate_snapshot_id"],
        entry_type="job_cost",
        amount_minor=result["amount_minor"],
        currency="CNY",
        source="scheduler_calculation",
        reconciliation_status="pending",
        idempotency_key=f"job:{job.id}",
        actor_id=actor_id,
        reason="管理员根据已核实的任务和费率执行内部核算",
    )
    db.add(row)
    db.flush()
    rate = db.get(BillingRateSnapshot, result["rate_snapshot_id"])
    evidence = {
        "job": _job_evidence(job),
        "rate": json.loads(json.dumps(rate_view(rate), default=str)),
        "rounding": "Decimal 精确计算，所有资源金额相加后按分四舍五入一次",
        "amount_minor": result["amount_minor"],
    }
    db.add(
        BillingEvidence(
            billing_entry_id=row.id, evidence_json=json.dumps(evidence, ensure_ascii=False)
        )
    )
    _commit(db)
    return estimate_job(db, job)
