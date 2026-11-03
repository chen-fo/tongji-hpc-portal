from datetime import datetime, timedelta

import pytest
from conftest import ORIGIN, PASSWORD, migrate
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.models.billing import BillingEntry, BudgetChangeAudit
from app.models.entities import OfficialAccount, User, utcnow
from app.models.jobs import SlurmJob
from app.security.passwords import hash_password


@pytest.fixture
def billing_case(settings):
    migrate(settings.database_url)
    app = create_app(settings)
    with app.state.sessions() as db:
        official = OfficialAccount(
            alias="official_a",
            ssh_host="mock-a",
            ssh_port=22,
            ssh_username="researcher",
            ssh_key_reference="mock-key",
            enabled=True,
        )
        db.add(official)
        db.flush()
        password = hash_password(PASSWORD)
        admin = User(username="admin", password_hash=password, role="admin", status="active")
        alice = User(
            username="alice",
            password_hash=password,
            role="user",
            status="active",
            official_account_id=official.id,
            remote_home="/mock/user/alice",
        )
        bob = User(
            username="bob",
            password_hash=password,
            role="user",
            status="active",
            official_account_id=official.id,
            remote_home="/mock/user/bob",
        )
        db.add_all([admin, alice, bob])
        db.flush()
        start = datetime(2026, 2, 1, 9)

        def job(user, job_id):
            return SlurmJob(
                official_account_id=official.id,
                user_id=user.id if user else None,
                attribution_source="confirmed_external" if user else "unassigned",
                cluster_name="cluster_a",
                job_id=job_id,
                job_name="analysis",
                partition="intel",
                submit_time=start - timedelta(minutes=5),
                start_time=start,
                end_time=start + timedelta(hours=2),
                elapsed_seconds=7200,
                requested_resources={"cpu": "32", "mem": "64G"},
                allocated_resources={"cpu": "32", "mem": "64G"},
                latest_state="COMPLETED",
                restarts=0,
                suspended_seconds=0,
                complex_history=False,
                observation_status="observed",
                last_synced_at=utcnow(),
                data_source="sacct",
            )

        alice_job, bob_job, unassigned = job(alice, "101"), job(bob, "102"), job(None, "103")
        db.add_all([alice_job, bob_job, unassigned])
        db.commit()
        ids = {
            "alice": alice.id,
            "bob": bob.id,
            "alice_job": alice_job.id,
            "bob_job": bob_job.id,
            "unassigned": unassigned.id,
        }
    clients = {}
    for name in ("admin", "alice", "bob"):
        client = TestClient(app, headers={"Origin": ORIGIN})
        response = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        clients[name] = client
    yield app, clients, ids
    for client in clients.values():
        client.close()
    app.state.engine.dispose()


def valid_rate(**overrides):
    value = {
        "cluster_name": "cluster_a",
        "partition": "intel",
        "cpu_price_per_core_hour": "0.04",
        "physical_cores_per_slurm_cpu": "1",
        "gpu_rates": [],
        "charge_cpu_with_gpu": None,
        "source_url": "https://dev.tongji.edu.cn/hpc-doc/#/pages/platformDes/charge",
        "verified_at": "2026-01-01",
        "effective_from": "2026-01-01T00:00:00Z",
        "confirmed": True,
    }
    return {**value, **overrides}


def configure_rate(admin, **overrides):
    response = admin.post("/api/admin/billing/rates", json=valid_rate(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_budgets_are_observation_only_versioned_audited_and_user_scoped(billing_case):
    app, clients, ids = billing_case
    admin, alice, bob = clients["admin"], clients["alice"], clients["bob"]
    budget_path = f"/api/admin/users/{ids['alice']}/budget"
    payload = {
        "limit_minor": 10000,
        "warning_threshold": 80,
        "expected_version": 0,
        "reason": "课题预算",
    }
    assert alice.patch(budget_path, json=payload).status_code == 403
    assert (
        admin.patch(budget_path, json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    assert (
        admin.patch(
            budget_path, json=payload, headers={"Origin": "https://untrusted.test"}
        ).status_code
        == 403
    )
    result = admin.patch(budget_path, json=payload)
    assert result.status_code == 200
    assert result.json()["budget"]["control_mode"] == "observe"
    assert result.json()["reserved_minor"] is None and result.json()["available_minor"] is None
    assert admin.patch(budget_path, json=payload).status_code == 409
    payload.update(limit_minor=12000, expected_version=1, reason="追加额度")
    assert admin.patch(budget_path, json=payload).json()["budget"]["version"] == 2
    assert alice.get("/api/billing/summary").json()["budget"]["limit_minor"] == 12000
    assert bob.get("/api/billing/summary").json()["budget"] is None
    assert alice.get("/api/admin/budgets").status_code == 403
    with app.state.sessions() as db:
        events = db.scalars(select(BudgetChangeAudit).order_by(BudgetChangeAudit.id)).all()
        assert [item.reason for item in events] == ["课题预算", "追加额度"]
        assert events[0].previous_values is None and events[1].previous_values is not None
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE budget_change_audit SET reason='rewritten'"))
            db.commit()
        db.rollback()


def test_budget_change_and_audit_roll_back_together_on_failure(billing_case):
    app, clients, ids = billing_case
    with app.state.sessions() as db:
        db.execute(
            text(
                "CREATE TRIGGER fail_budget_audit BEFORE INSERT ON budget_change_audit "
                "BEGIN SELECT RAISE(ABORT, 'test audit failure'); END"
            )
        )
        db.commit()
    response = clients["admin"].patch(
        f"/api/admin/users/{ids['alice']}/budget",
        json={
            "limit_minor": 10000,
            "warning_threshold": 80,
            "expected_version": 0,
            "reason": "测试事务回滚",
        },
    )
    assert response.status_code == 409
    assert clients["alice"].get("/api/billing/summary").json()["budget"] is None
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BudgetChangeAudit)) == 0


def test_immutable_rate_decimal_calculation_and_idempotent_job_ledger(billing_case):
    app, clients, ids = billing_case
    admin, alice, bob = clients["admin"], clients["alice"], clients["bob"]
    path = f"/api/billing/jobs/{ids['alice_job']}"
    assert alice.get(path).json()["status"] == "unpriced"
    assert alice.get(f"/api/billing/jobs/{ids['bob_job']}").status_code == 404
    assert bob.get(path).status_code == 404
    assert alice.get(f"/api/billing/jobs/{ids['unassigned']}").status_code == 404
    assert admin.post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code == 409
    rate = configure_rate(admin)
    result = alice.get(path).json()
    assert result["status"] == "estimated" and result["amount_minor"] == 256
    assert result["rate_snapshot_id"] == rate["id"]
    assert alice.post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code == 403
    url = f"/api/admin/billing/jobs/{ids['alice_job']}/settle"
    result = admin.post(url).json()
    assert result["status"] == "recorded" and result["amount_minor"] == 256
    assert result["reconciliation_status"] == "pending"
    assert admin.post(url).json() == result
    second = configure_rate(
        admin, cpu_price_per_core_hour="0.07", effective_from="2026-01-15T00:00:00Z"
    )
    assert second["version"] == 2 and second["id"] != rate["id"]
    assert alice.get(path).json()["amount_minor"] == 256
    assert alice.get(path).json()["rate_snapshot_id"] == rate["id"]
    assert alice.get("/api/billing/summary").json()["recorded_minor"] == 256
    assert bob.get("/api/billing/entries").json()["items"] == []
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BillingEntry)) == 1
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE billing_rate_snapshots SET cpu_price_per_core_hour='99'"))
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM billing_entries"))
            db.commit()
        db.rollback()


def test_gpu_rules_unknown_resources_and_exact_cent_rounding(billing_case):
    app, clients, ids = billing_case
    admin, alice = clients["admin"], clients["alice"]
    configure_rate(
        admin,
        cpu_price_per_core_hour="0.04",
        gpu_rates=[{"tres_key": "gres/gpu:l40", "price_per_hour": "2"}],
        charge_cpu_with_gpu=True,
    )
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.elapsed_seconds = 5400
        job.requested_resources = {"cpu": "32", "gres/gpu": "2"}
        job.allocated_resources = {"cpu": "32", "mem": "64G", "gres/gpu:l40": "2", "gres/gpu": "2"}
        db.commit()
    path = f"/api/billing/jobs/{ids['alice_job']}"
    assert alice.get(path).json()["amount_minor"] == 792  # (2 GPU * 2 + 32 CPU * .04) * 1.5h
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.allocated_resources = {"cpu": "32", "gres/gpu": "2"}
        db.commit()
    assert alice.get(path).json()["status"] == "unpriced"
    assert admin.post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code == 409
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.allocated_resources = {"cpu": "1"}
        job.requested_resources = {"cpu": "1"}
        job.elapsed_seconds = 3600
        db.commit()
    configure_rate(admin, cpu_price_per_core_hour="0.005", effective_from="2026-01-15T00:00:00Z")
    assert alice.get(path).json()["amount_minor"] == 1  # 0.5 fen rounds half up once.


def test_running_estimate_requires_matched_queue_and_complete_accounting_record(billing_case):
    app, clients, ids = billing_case
    admin = clients["admin"]
    configure_rate(
        admin,
        gpu_rates=[{"tres_key": "gres/gpu:l40", "price_per_hour": "2"}],
        charge_cpu_with_gpu=True,
    )
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.latest_state = "RUNNING"
        job.end_time = None
        job.elapsed_seconds = 3600
        job.last_synced_at = job.start_time + timedelta(hours=1)
        job.data_source = "squeue+sacct"
        job.requested_resources = {"cpu": "32", "gres/gpu": "2"}
        job.allocated_resources = {"cpu": "32", "gres/gpu:l40": "2"}
        db.commit()
    path = f"/api/billing/jobs/{ids['alice_job']}"
    result = clients["alice"].get(path).json()
    assert result["status"] == "estimated" and result["amount_minor"] == 528
    assert result["as_of"] is not None
    assert admin.post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code == 409
    with app.state.sessions() as db:
        db.get(SlurmJob, ids["alice_job"]).data_source = "squeue"
        db.commit()
    assert clients["alice"].get(path).json()["status"] == "unpriced"


@pytest.mark.parametrize(
    "case",
    [
        "unknown_restart",
        "requeued",
        "array",
        "missing_time",
        "zero_time",
        "missing_resources",
        "missing_end",
        "awaiting_history",
        "unknown_suspension",
        "suspended",
        "queue_only",
    ],
)
def test_uncertain_or_complex_jobs_never_get_a_fabricated_cost(billing_case, case):
    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        if case == "unknown_restart":
            job.restarts = None
        elif case == "requeued":
            job.complex_history = True
        elif case == "array":
            job.job_id = "101_1"
        elif case == "missing_time":
            job.elapsed_seconds = None
        elif case == "zero_time":
            job.elapsed_seconds = 0
        elif case == "missing_resources":
            job.allocated_resources = None
        elif case == "missing_end":
            job.end_time = None
        elif case == "awaiting_history":
            job.observation_status = "awaiting_history"
        elif case == "unknown_suspension":
            job.suspended_seconds = None
        elif case == "suspended":
            job.suspended_seconds = 60
        elif case == "queue_only":
            job.data_source = "squeue"
        db.commit()
    result = clients["alice"].get(f"/api/billing/jobs/{ids['alice_job']}").json()
    assert result["status"] == "unpriced" and result["amount_minor"] is None
    assert (
        clients["admin"].post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code
        == 409
    )
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BillingEntry)) == 0


def test_job_crossing_rate_change_requires_manual_calculation(billing_case):
    _, clients, ids = billing_case
    admin = clients["admin"]
    configure_rate(admin)
    configure_rate(admin, cpu_price_per_core_hour="0.05", effective_from="2026-02-01T10:00:00Z")
    path = f"/api/billing/jobs/{ids['alice_job']}"
    result = clients["alice"].get(path).json()
    assert result["status"] == "unpriced" and result["amount_minor"] is None
    assert "费率变更" in result["reason"]
    assert admin.post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code == 409


def test_requested_gpu_missing_from_allocation_is_never_billed_as_cpu_only(billing_case):
    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.requested_resources = {"cpu": "32", "gres/gpu": "2"}
        job.allocated_resources = {"cpu": "32", "mem": "64G"}
        db.commit()
    result = clients["alice"].get(f"/api/billing/jobs/{ids['alice_job']}").json()
    assert result["status"] == "unpriced" and result["amount_minor"] is None
    assert (
        clients["admin"].post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code
        == 409
    )


@pytest.mark.parametrize(
    "requested,allocated,expected",
    [
        ({"gres/gpu": "2"}, {"gres/gpu": "0", "gres/gpu:l40": "2"}, "unpriced"),
        ({"gres/gpu": "2"}, {"gres/gpu": "2", "gres/gpu:l40": "0"}, "unpriced"),
        ({"gres/gpu": "2"}, {"gres/gpu:l40": "1"}, "unpriced"),
        ({"gres/gpu:l40": "2"}, {"gres/gpu:a800": "2"}, "unpriced"),
        ({"gres/gpu:l40": "2"}, {"gres/gpu": "2"}, "unpriced"),
        ({"gres/gpu": "0", "gres/gpu:l40": "2"}, {"gres/gpu:l40": "2"}, "unpriced"),
        ({"gres/gpu": "2", "gres/gpu:l40": "0"}, {"gres/gpu:l40": "2"}, "unpriced"),
        (None, {"gres/gpu:l40": "2"}, "unpriced"),
        ({"gres/gpu": "2"}, {"gres/gpu:l40": "2"}, "estimated"),
        ({"gres/gpu:l40": "2"}, {"gres/gpu:l40": "2"}, "estimated"),
    ],
)
def test_gpu_request_and_allocation_must_be_consistent(
    billing_case, requested, allocated, expected
):
    app, clients, ids = billing_case
    configure_rate(
        clients["admin"],
        gpu_rates=[{"tres_key": "gres/gpu:l40", "price_per_hour": "2"}],
        charge_cpu_with_gpu=True,
    )
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job.requested_resources = requested
        job.allocated_resources = {"cpu": "32", **allocated}
        db.commit()
    result = clients["alice"].get(f"/api/billing/jobs/{ids['alice_job']}").json()
    assert result["status"] == expected
    if expected == "unpriced":
        assert result["amount_minor"] is None
        assert (
            clients["admin"].post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code
            == 409
        )


def test_manual_and_official_adjustments_are_idempotent_scoped_and_require_sources(billing_case):
    app, clients, ids = billing_case
    admin, alice, bob = clients["admin"], clients["alice"], clients["bob"]
    payload = {
        "user_id": ids["alice"],
        "amount_minor": 250,
        "entry_type": "manual_adjustment",
        "reason": "人工核对",
        "idempotency_key": "adjustment-123",
    }
    assert alice.post("/api/admin/billing/adjustments", json=payload).status_code == 403
    response = admin.post("/api/admin/billing/adjustments", json=payload)
    assert response.status_code == 201 and response.json()["amount_minor"] == 250
    assert (
        admin.post("/api/admin/billing/adjustments", json=payload).json()["id"]
        == response.json()["id"]
    )
    assert (
        admin.post(
            "/api/admin/billing/adjustments", json={**payload, "amount_minor": 251}
        ).status_code
        == 409
    )
    assert (
        admin.post(
            "/api/admin/billing/adjustments",
            json={
                **payload,
                "entry_type": "official_adjustment",
                "idempotency_key": "statement-123",
            },
        ).status_code
        == 422
    )
    negative = {
        **payload,
        "amount_minor": -50,
        "entry_type": "official_adjustment",
        "statement_reference": "2026-02 对账",
        "idempotency_key": "statement-123",
    }
    assert admin.post("/api/admin/billing/adjustments", json=negative).status_code == 201
    assert alice.get("/api/billing/summary").json()["recorded_minor"] == 200
    assert len(alice.get("/api/billing/entries").json()["items"]) == 2
    assert bob.get("/api/billing/entries").json()["items"] == []
    first = alice.get("/api/billing/entries?limit=1&offset=0").json()
    second = alice.get("/api/billing/entries?limit=1&offset=1").json()
    assert first["total"] == second["total"] == 2
    assert first["limit"] == second["limit"] == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert alice.get("/api/admin/billing/entries").status_code == 403
    assert admin.get(f"/api/admin/billing/entries?user_id={ids['bob']}").json()["total"] == 0
    assert (
        admin.get(f"/api/admin/billing/entries?user_id={ids['alice']}&limit=1").json()["total"] == 2
    )
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BillingEntry)) == 2


def test_rate_requires_explicit_valid_rules_and_verified_source(billing_case):
    _, clients, _ = billing_case
    admin = clients["admin"]
    for changes in (
        {"confirmed": False},
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://name:secret@example.org/rates"},
        {"physical_cores_per_slurm_cpu": None},
        {"cpu_price_per_core_hour": 0.04},
        {"effective_from": "2026-01-01T00:00:00"},
    ):
        assert admin.post("/api/admin/billing/rates", json=valid_rate(**changes)).status_code == 422
    assert clients["alice"].post("/api/admin/billing/rates", json=valid_rate()).status_code == 403
    assert clients["alice"].get("/api/admin/billing/rates").status_code == 403


def test_missing_requested_tres_is_not_cpu_only(billing_case):
    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    with app.state.sessions() as db:
        db.get(SlurmJob, ids["alice_job"]).requested_resources = None
        db.commit()
    path = f"/api/billing/jobs/{ids['alice_job']}"
    assert clients["alice"].get(path).json()["status"] == "unpriced"
    assert (
        clients["admin"].post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code
        == 409
    )


@pytest.mark.parametrize("offset_days", [0, 1])
def test_duplicate_identity_blocks_settlement_and_warns_after_settlement(billing_case, offset_days):
    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    path = f"/api/billing/jobs/{ids['alice_job']}"
    settle = f"/api/admin/billing/jobs/{ids['alice_job']}/settle"
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        job_id, account_id, submit = job.job_id, job.official_account_id, job.submit_time
        duplicate = SlurmJob(
            official_account_id=account_id,
            user_id=None,
            attribution_source="unassigned",
            cluster_name=job.cluster_name,
            job_id=job_id,
            submit_time=submit + timedelta(days=1),
            latest_state="PENDING",
            data_source="squeue",
            complex_history=True,
            observation_status="observed",
            last_synced_at=utcnow(),
        )
        db.add(duplicate)
        db.commit()
        duplicate_id = duplicate.id
    assert clients["alice"].get(path).json()["status"] == "unpriced"
    assert clients["admin"].post(settle).status_code == 409
    with app.state.sessions() as db:
        db.delete(db.get(SlurmJob, duplicate_id))
        db.commit()
    recorded = clients["admin"].post(settle).json()
    assert recorded["status"] == "recorded" and recorded["evidence"]["job"]["requested_resources"]
    recorded_as_of = recorded["as_of"]
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        other = OfficialAccount(
            alias="disabled_alias",
            ssh_host="another-host",
            ssh_port=22,
            ssh_username="researcher",
            ssh_key_reference="mock",
            enabled=False,
        )
        db.add(other)
        db.flush()
        db.add(
            SlurmJob(
                official_account_id=other.id,
                user_id=None,
                attribution_source="unassigned",
                cluster_name=job.cluster_name,
                job_id=job_id,
                submit_time=submit + timedelta(days=offset_days),
                latest_state="PENDING",
                data_source="squeue",
                complex_history=True,
                observation_status="observed",
                last_synced_at=utcnow(),
            )
        )
        job.allocated_resources = {"cpu": "64"}
        job.last_synced_at = utcnow() + timedelta(days=1)
        db.commit()
    result = clients["alice"].get(path).json()
    assert result["amount_minor"] == recorded["amount_minor"] and result["as_of"] == recorded_as_of
    assert "疑似重复" in result["reason"] and "已变化" in result["reason"]
    assert clients["admin"].post(settle).json() == result
    with app.state.sessions() as db:
        with pytest.raises(IntegrityError):
            db.execute(text("UPDATE billing_evidence SET evidence_json='{}'"))
            db.commit()
        db.rollback()


@pytest.mark.parametrize("offset_days", [0, 1])
def test_duplicate_different_disabled_account_before_settlement(billing_case, offset_days):
    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        other = OfficialAccount(
            alias="other",
            ssh_host="other-host",
            ssh_port=22,
            ssh_username="researcher",
            ssh_key_reference="mock",
            enabled=False,
        )
        db.add(other)
        db.flush()
        db.add(
            SlurmJob(
                official_account_id=other.id,
                user_id=None,
                attribution_source="unassigned",
                cluster_name=job.cluster_name,
                job_id=job.job_id,
                submit_time=job.submit_time + timedelta(days=offset_days),
                latest_state="PENDING",
                data_source="squeue",
                complex_history=True,
                observation_status="observed",
                last_synced_at=utcnow(),
            )
        )
        db.commit()
    result = clients["alice"].get(f"/api/billing/jobs/{ids['alice_job']}").json()
    assert result["status"] == "unpriced" and "疑似重复" in result["reason"]
    assert (
        clients["admin"].post(f"/api/admin/billing/jobs/{ids['alice_job']}/settle").status_code
        == 409
    )


def test_legacy_charge_without_evidence_is_explicitly_unknown(billing_case):
    app, clients, ids = billing_case
    with app.state.sessions() as db:
        job = db.get(SlurmJob, ids["alice_job"])
        db.add(
            BillingEntry(
                official_account_id=job.official_account_id,
                user_id=job.user_id,
                slurm_job_id=job.id,
                entry_type="job_cost",
                amount_minor=123,
                currency="CNY",
                source="scheduler_calculation",
                reconciliation_status="pending",
                idempotency_key=f"job:{job.id}",
                actor_id=job.user_id,
                reason="历史记录",
            )
        )
        db.commit()
    result = clients["alice"].get(f"/api/billing/jobs/{ids['alice_job']}").json()
    assert result["amount_minor"] == 123 and result["as_of"] is None
    assert result["evidence"] is None and "缺少入账证据" in result["reason"]


def test_concurrent_settlement_is_idempotent(billing_case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.services.billing import settle_job

    app, clients, ids = billing_case
    configure_rate(clients["admin"])
    with app.state.sessions() as db:
        actor_id = db.scalar(select(User.id).where(User.role == "admin"))
    barrier = Barrier(2)

    def settle():
        with app.state.sessions() as db:
            barrier.wait(timeout=5)
            return settle_job(db, actor_id, ids["alice_job"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: settle(), range(2)))
    assert first == second and first["status"] == "recorded"
    with app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(BillingEntry)) == 1
        assert db.scalar(text("SELECT count(*) FROM billing_evidence")) == 1
