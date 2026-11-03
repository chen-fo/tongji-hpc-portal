from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.models.billing import BillingEntry, BillingRateSnapshot
from app.schemas.billing import AdjustmentCreate, BudgetUpdate, RateCreate
from app.security.dependencies import DB, AdminUser, CurrentUser
from app.services import billing

router = APIRouter(tags=["billing"])


def entries_page(db, user_id, limit, offset):
    query = select(BillingEntry)
    if user_id is not None:
        query = query.where(BillingEntry.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(BillingEntry.id.desc()).offset(offset).limit(limit))
    return {
        "items": [billing.entry_view(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/billing/summary")
def my_summary(user: CurrentUser, db: DB):
    return billing.summary(db, user)


@router.get("/api/billing/entries")
def my_entries(
    user: CurrentUser,
    db: DB,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return entries_page(db, user.id, limit, offset)


@router.get("/api/billing/jobs/{job_id}")
def job_cost(job_id: int, user: CurrentUser, db: DB):
    return billing.get_job(db, user, job_id)


@router.get("/api/admin/billing/summary")
def admin_summary(admin: AdminUser, db: DB):
    return billing.summaries(db)


@router.get("/api/admin/billing/entries")
def admin_entries(
    admin: AdminUser,
    db: DB,
    user_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return entries_page(db, user_id, limit, offset)


@router.get("/api/admin/budgets")
def budgets(admin: AdminUser, db: DB):
    return billing.summaries(db)


@router.patch("/api/admin/users/{user_id}/budget")
def budget_update(user_id: int, data: BudgetUpdate, admin: AdminUser, db: DB):
    actor_id = admin.id
    return billing.update_budget(db, user_id, actor_id, data)


@router.get("/api/admin/billing/rates")
def rates(admin: AdminUser, db: DB):
    rows = db.scalars(select(BillingRateSnapshot).order_by(BillingRateSnapshot.id.desc()))
    return {"items": [billing.rate_view(row) for row in rows]}


@router.post("/api/admin/billing/rates", status_code=201)
def rate_create(data: RateCreate, admin: AdminUser, db: DB):
    actor_id = admin.id
    return billing.create_rate(db, actor_id, data)


@router.post("/api/admin/billing/adjustments", status_code=201)
def adjustment_create(data: AdjustmentCreate, admin: AdminUser, db: DB):
    actor_id = admin.id
    return billing.add_adjustment(db, actor_id, data)


@router.post("/api/admin/billing/jobs/{job_id}/settle")
def settle_job(job_id: int, admin: AdminUser, db: DB):
    actor_id = admin.id
    return billing.settle_job(db, actor_id, job_id)
