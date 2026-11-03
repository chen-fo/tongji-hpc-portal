import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import ORIGIN, account_payload, create_member, login
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.entities import User, utcnow
from app.models.jobs import JobAttribution, JobSyncState, SlurmJob, SlurmJobRun
from app.services.jobs import JobSyncService
from app.ssh.manager import SSHUnavailable
from app.ssh.slurm import (
    Observation,
    RealSlurmCollector,
    Snapshot,
    bounded_run,
    history_records,
    probe_slurm,
    queue_records,
    seconds,
)

FIELDS = [
    "JobID",
    "JobName",
    "Partition",
    "State",
    "Submit",
    "Start",
    "End",
    "ElapsedRaw",
    "ReqTRES",
    "AllocTRES",
    "ExitCode",
    "Cluster",
    "DBIndex",
    "Restarts",
    "Suspended",
]
SUBMIT = datetime(2026, 10, 23, 0, 0)
START = datetime(2026, 10, 23, 0, 30)
END = datetime(2026, 10, 23, 1, 30)


def observed(state, *, source="squeue", jid="123", submit=SUBMIT, **kwargs):
    return Observation(
        cluster_name="cluster-a",
        job_id=jid,
        submit_time=submit,
        latest_state=state,
        data_source=source,
        job_name="example",
        partition="intel",
        **kwargs,
    )


def test_parsers_keep_real_and_estimated_times_separate_and_parse_suspension():
    queue = (
        "123|example|intel|PENDING|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T02:30:00|0:00|2:00:00|4|Priority\n"
        "124|task|gpu|RUNNING|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T02:30:00|1:00:00|2:00:00|8|gpu01\n"
        "125|bad|name|with|extra|fields\n"
    )
    rows, skipped = queue_records(queue, "cluster-a", 10)
    assert skipped == 1 and len(rows) == 2
    pending, running = rows
    assert pending.start_time is None and pending.expected_start_time == START
    assert pending.end_time is None and pending.elapsed_seconds is None
    assert pending.requested_resources == {"cpu": "4"}
    assert running.start_time == START and running.end_time is None
    assert running.expected_end_time == datetime(2026, 10, 23, 2, 30)
    assert running.elapsed_seconds == 3600 and running.allocated_resources == {"cpu": "8"}
    assert seconds("7-00:00:00") == 604800
    history = (
        "123|example|intel|COMPLETED|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T01:30:00|3600|cpu=4,mem=8G|cpu=4,gres/gpu:a800=2|0:0|cluster-a|9|0|00:00:00\n"
        "123.batch|batch|intel|COMPLETED|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T01:30:00|3600|cpu=4|cpu=4|0:0|cluster-a|10|0|00:00:00\n"
        "126|pending|intel|PENDING|2026-10-23T00:00:00|2026-10-23T00:30:00|Unknown|0|cpu=4||0:0|cluster-a|11|0|00:00:00\n"
    )
    rows, skipped = history_records(history, FIELDS, "cluster-a", 10)
    assert len(rows) == 2 and skipped == 0
    assert rows[0].end_time == END and rows[0].elapsed_seconds == 3600
    assert rows[0].allocated_resources == {"cpu": "4", "gres/gpu:a800": "2"}
    assert rows[0].scheduler_record_key == "db:9" and rows[0].suspended_seconds == 0
    assert (
        rows[1].start_time is None
        and rows[1].expected_start_time == START
        and rows[1].elapsed_seconds is None
    )
    no_suspend_fields = FIELDS[:-1]
    no_suspend = "|".join(history.splitlines()[0].split("|")[:-1])
    assert (
        history_records(no_suspend, no_suspend_fields, "cluster-a", 10)[0][0].suspended_seconds
        is None
    )
    with pytest.raises(SSHUnavailable):
        queue_records("x\ny\n", "cluster-a", 1)


def test_sync_missing_history_replay_requeue_id_reuse_and_account_failure_isolation(
    app, admin, account, settings
):
    second = admin.post(
        "/api/admin/official-accounts", json=account_payload("official_b", "mock-b", "research_b")
    ).json()
    service = JobSyncService(settings, app.state.sessions, app.state.ssh)
    a, b = account["id"], second["id"]

    async def run():
        service.collector.snapshots[a] = Snapshot(
            queue=[observed("PENDING", expected_start_time=START)]
        )
        service.collector.snapshots[b] = Snapshot(
            queue=[observed("RUNNING", jid="777", start_time=START)]
        )
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob).where(SlurmJob.official_account_id == a))
            assert job.user_id is None and job.attribution_source == "unassigned"
            assert job.start_time is None and job.expected_start_time == START
            old = utcnow() - timedelta(days=3)
            db.get(JobSyncState, a).last_success_at = old
            db.commit()
        seen_since = []
        original = service.collector.snapshot

        async def inspect(account, since):
            if account.id == a:
                seen_since.append(since)
            return await original(account, since)

        service.collector.snapshot = inspect
        settings.mock_fail_aliases = ["official_a"]
        await service.sync_once()
        with app.state.sessions() as db:
            assert db.get(JobSyncState, a).status == "failed"
            assert db.get(JobSyncState, a).last_success_at == old
            assert db.get(JobSyncState, b).status == "ok"
        settings.mock_fail_aliases = []
        running = observed(
            "RUNNING", start_time=START, elapsed_seconds=300, allocated_resources={"cpu": "4"}
        )
        service.collector.snapshots[a] = Snapshot(queue=[running])
        await service.sync_once()
        assert seen_since[-1] == old - timedelta(days=1)
        service.collector.snapshots[a] = Snapshot(history_supported=False, error="历史任务待确认")
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob).where(SlurmJob.official_account_id == a))
            assert job.latest_state == "RUNNING" and job.end_time is None
            assert job.observation_status == "awaiting_history" and job.elapsed_seconds == 300
            assert db.get(JobSyncState, a).status == "partial"
        finished = observed(
            "COMPLETED",
            source="sacct",
            start_time=START,
            end_time=END,
            elapsed_seconds=3600,
            suspended_seconds=0,
            restarts=0,
            scheduler_record_key="db:9",
            allocated_resources={"cpu": "4"},
        )
        service.collector.snapshots[a] = Snapshot(history=[finished])
        await service.sync_once()
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob).where(SlurmJob.official_account_id == a))
            assert job.latest_state == "COMPLETED" and job.end_time == END
            assert job.suspended_seconds == 0 and not job.complex_history
            assert (
                len(db.scalars(select(SlurmJobRun).where(SlurmJobRun.slurm_job_id == job.id)).all())
                == 1
            )
        requeue = observed(
            "COMPLETED",
            source="sacct",
            start_time=END,
            end_time=END + timedelta(hours=1),
            elapsed_seconds=3600,
            suspended_seconds=0,
            restarts=1,
            scheduler_record_key="db:10",
        )
        reused = observed("PENDING", submit=SUBMIT + timedelta(days=10))
        service.collector.snapshots[a] = Snapshot(queue=[reused], history=[finished, requeue])
        await service.sync_once()
        with app.state.sessions() as db:
            jobs = db.scalars(
                select(SlurmJob)
                .where(SlurmJob.official_account_id == a)
                .order_by(SlurmJob.submit_time)
            ).all()
            assert len(jobs) == 2 and jobs[0].complex_history
            assert (
                len(
                    db.scalars(
                        select(SlurmJobRun).where(SlurmJobRun.slurm_job_id == jobs[0].id)
                    ).all()
                )
                == 2
            )

    asyncio.run(run())


def test_jobs_privacy_assignment_audit_and_filters(app, admin, account):
    other = admin.post(
        "/api/admin/official-accounts", json=account_payload("official_b", "mock-b", "research_b")
    ).json()
    for name, account_id in (
        ("alice", account["id"]),
        ("bob", account["id"]),
        ("charlie", other["id"]),
    ):
        assert create_member(admin, account_id, name).status_code == 201
    with app.state.sessions() as db:
        ids = {u.username: u.id for u in db.scalars(select(User))}
        job = SlurmJob(
            official_account_id=account["id"],
            cluster_name="cluster-a",
            job_id="123",
            submit_time=SUBMIT,
            latest_state="RUNNING",
            data_source="squeue",
            last_synced_at=utcnow(),
            observation_status="observed",
            complex_history=True,
            attribution_source="unassigned",
        )
        db.add(job)
        db.commit()
        jid = job.id
    alice = TestClient(app, headers={"Origin": ORIGIN})
    bob = TestClient(app, headers={"Origin": ORIGIN})
    login(alice, "alice")
    login(bob, "bob")
    assert alice.get("/api/jobs").json()["items"] == []
    assert alice.get(f"/api/jobs/{jid}").status_code == 404
    assert (
        alice.post(
            f"/api/admin/jobs/{jid}/assign",
            json={"user_id": ids["alice"], "reason": "已核对提交记录"},
        ).status_code
        == 403
    )
    assert (
        admin.post(
            f"/api/admin/jobs/{jid}/assign", json={"user_id": ids["alice"], "reason": "  "}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            f"/api/admin/jobs/{jid}/assign",
            json={"user_id": ids["charlie"], "reason": "已核对提交记录"},
        ).status_code
        == 400
    )
    csrf_token = admin.headers.pop("X-CSRF-Token")
    assert (
        admin.post(
            f"/api/admin/jobs/{jid}/assign",
            json={"user_id": ids["alice"], "reason": "已核对提交记录"},
        ).status_code
        == 403
    )
    admin.headers["X-CSRF-Token"] = csrf_token
    response = admin.post(
        f"/api/admin/jobs/{jid}/assign",
        json={"user_id": ids["alice"], "reason": "  已核对提交记录  "},
    )
    assert response.status_code == 200 and response.json()["username"] == "alice"
    assert (
        admin.post(
            f"/api/admin/jobs/{jid}/assign", json={"user_id": ids["bob"], "reason": "不能覆盖归属"}
        ).status_code
        == 409
    )
    assert (
        alice.get(f"/api/jobs/{jid}").status_code == 200
        and bob.get(f"/api/jobs/{jid}").status_code == 404
    )
    assert (
        len(alice.get("/api/jobs").json()["items"]) == 1
        and bob.get("/api/jobs").json()["items"] == []
    )
    with app.state.sessions() as db:
        audit = db.scalar(select(JobAttribution))
        assert (audit.user_id, audit.actor_id, audit.reason) == (
            ids["alice"],
            ids["admin"],
            "已核对提交记录",
        )
        db.get(SlurmJob, jid).observation_status = "awaiting_history"
        db.commit()
    assert admin.get("/api/admin/jobs?status=running").json()["total"] == 0
    assert admin.get("/api/admin/jobs?status=history").json()["total"] == 0
    assert admin.get("/api/admin/jobs").json()["total"] == 1
    assert admin.get("/api/admin/jobs?username=alice&limit=1&offset=0").json()["total"] == 1


def test_running_estimate_requires_fresh_matching_history(app, account, settings):
    service = JobSyncService(settings, app.state.sessions, app.state.ssh)
    account_id = account["id"]
    queue = observed(
        "RUNNING", start_time=START, elapsed_seconds=600, allocated_resources={"cpu": "4"}
    )
    history = observed(
        "RUNNING",
        source="sacct",
        start_time=START,
        elapsed_seconds=590,
        suspended_seconds=0,
        restarts=0,
        scheduler_record_key="db:15",
        requested_resources={"cpu": "4", "gres/gpu": "2"},
        allocated_resources={"cpu": "4", "gres/gpu:a800": "2"},
    )

    async def run():
        service.collector.snapshots[account_id] = Snapshot(queue=[queue], history=[history])
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob))
            assert job.data_source == "squeue+sacct" and not job.complex_history
            assert job.allocated_resources == {"cpu": "4", "gres/gpu:a800": "2"}
            assert job.suspended_seconds == 0 and job.elapsed_seconds == 600
        # A later queue-only poll cannot reuse stale sacct resources.
        service.collector.snapshots[account_id] = Snapshot(queue=[queue], history_supported=False)
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob))
            assert job.data_source == "squeue" and job.complex_history
            assert job.allocated_resources == {"cpu": "4"} and job.suspended_seconds is None
        # Current scheduler records disagreeing on CPU count stay unpriced.
        history.allocated_resources = {"cpu": "8", "gres/gpu:a800": "2"}
        service.collector.snapshots[account_id] = Snapshot(queue=[queue], history=[history])
        await service.sync_once()
        with app.state.sessions() as db:
            job = db.scalar(select(SlurmJob))
            assert job.data_source == "squeue" and job.complex_history
            assert job.allocated_resources == {"cpu": "4"}

    asyncio.run(run())


def test_simultaneous_attribution_only_one_owner_and_one_audit(app, admin, account):
    for name in ("alice", "bob"):
        assert create_member(admin, account["id"], name).status_code == 201
    with app.state.sessions() as db:
        ids = {u.username: u.id for u in db.scalars(select(User))}
        job = SlurmJob(
            official_account_id=account["id"],
            cluster_name="cluster-a",
            job_id="123",
            submit_time=SUBMIT,
            latest_state="COMPLETED",
            data_source="sacct",
            last_synced_at=utcnow(),
            observation_status="observed",
            complex_history=True,
            attribution_source="unassigned",
        )
        db.add(job)
        db.commit()
        jid = job.id

    clients = [TestClient(app, headers={"Origin": ORIGIN}) for _ in range(2)]
    for client in clients:
        login(client)

    def assign(index):
        return (
            clients[index]
            .post(
                f"/api/admin/jobs/{jid}/assign",
                json={"user_id": ids[("alice", "bob")[index]], "reason": "已核对提交记录"},
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(assign, range(2))) == [200, 409]
    with app.state.sessions() as db:
        audit = db.scalars(select(JobAttribution).where(JobAttribution.slurm_job_id == jid)).all()
        assert len(audit) == 1 and db.get(SlurmJob, jid).user_id == audit[0].user_id


@pytest.mark.parametrize("slurm_version", ["22.05.10", "23.11"])
def test_readonly_capability_probe_does_not_expose_job_contents(
    settings, monkeypatch, slurm_version
):
    commands = []

    class Stream:
        def __init__(self, value):
            self.value = value

        async def read(self, _):
            value, self.value = self.value, ""
            return value

    class Process:
        def __init__(self, value, stderr="", exit_status=0):
            self.stdout, self.stderr, self.exit_status = Stream(value), Stream(stderr), exit_status

        async def wait_closed(self):
            pass

        def close(self):
            pass

    class Connection:
        async def create_process(self, command, **kwargs):
            commands.append(command)
            if "for c in squeue" in command:
                text = f"remote_offset=+0800\nsqueue=slurm {slurm_version}\nsacct=slurm {slurm_version}\nscontrol=slurm {slurm_version}\n"
            elif "scontrol show config" in command:
                text = "cluster-a\n"
            elif "squeue --local" in command:
                text = "123|example|intel|RUNNING|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T02:30:00|1:00:00|2:00:00|4|node01\n"
            elif "sacct --helpformat" in command:
                text = " ".join(
                    field for field in FIELDS if field != "Restarts" or slurm_version != "22.05.10"
                )
            elif "sacct --local" in command:
                if slurm_version == "22.05.10" and "--array" in command:
                    return Process("", "sacct: unrecognized option '--array'", 1)
                restart_field = "" if slurm_version == "22.05.10" else "0|"
                text = f"123|example|intel|COMPLETED|2026-10-23T00:00:00|2026-10-23T00:30:00|2026-10-23T01:30:00|3600|cpu=4|cpu=4|0:0|cluster-a|9|{restart_field}00:00:00\n"
            else:
                pytest.fail("Unexpected remote command")
            return Process(text)

    class SSH:
        @asynccontextmanager
        async def connect(self, account):
            yield Connection()

    account = SimpleNamespace(
        id=1, alias="intel", ssh_host="hpc.example", ssh_port=10022, ssh_username="u-test"
    )
    monkeypatch.setattr(
        "app.ssh.slurm.connection_options",
        lambda *args: SimpleNamespace(
            host=account.ssh_host, port=account.ssh_port, username=account.ssh_username
        ),
    )
    result = asyncio.run(probe_slurm(RealSlurmCollector(settings, SSH()), account, SUBMIT))
    assert result["squeue_query_ok"] and result["sacct_query_ok"]
    assert result["remote_timezone_offset"] == "+0800" and result["query_timezone"] == "UTC"
    assert result["queue_count"] == result["history_count"] == 1
    assert "Suspended" in result["available_sacct_fields"] and "example" not in str(result)
    assert ("Restarts" in result["available_sacct_fields"]) == (slurm_version != "22.05.10")
    assert all("--array" not in command for command in commands if "sacct --local" in command)
    assert all("--array" in command for command in commands if "squeue --local" in command)
    assert not any(
        any(word in command for word in ("sbatch", "scancel", "srun", "salloc"))
        for command in commands
    )
    assert all(
        "TZ=UTC0" in command
        for command in commands
        if "squeue --local" in command or "sacct --local" in command
    )


@pytest.mark.parametrize("cancel", ["none", "reading", "cleanup"])
def test_bounded_slurm_channel_cleanup_does_not_hang_or_swallow_cancellation(cancel):
    class StuckStream:
        async def read(self, _):
            await asyncio.Event().wait()

    class Process:
        stdout = StuckStream()
        stderr = StuckStream()
        exit_status = None
        closed = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            await asyncio.Event().wait()

    process = Process()

    class Connection:
        async def create_process(self, *args, **kwargs):
            return process

    async def run():
        task = asyncio.create_task(
            bounded_run(Connection(), "read-only", 30 if cancel == "reading" else 0.03)
        )
        if cancel == "reading":
            await asyncio.sleep(0.01)
            task.cancel()
        elif cancel == "cleanup":
            await asyncio.sleep(0.08)
            assert process.closed
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel != "none" else TimeoutError):
            await asyncio.wait_for(task, timeout=1.5)
        assert process.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("sacct: unrecognized option --some-private-value", "unsupported_option"),
        ("sacct: invalid field /home/private", "unsupported_field"),
        ("Access denied for secret-account", "permission_denied"),
        ("Slurm accounting storage is disabled", "accounting_disabled"),
        ("could not contact slurmdbd on privatehost", "accounting_service_error"),
        ("unknown remote failure: private-token", "unknown"),
    ],
)
def test_sacct_diagnostic_is_categorized_without_remote_output(stderr, expected):
    from app.ssh.slurm import SlurmCommandError

    class Stream:
        def __init__(self, value):
            self.value = value

        async def read(self, _):
            value, self.value = self.value, ""
            return value

    class Process:
        stdout, exit_status = Stream("private-stdout"), 2

        def __init__(self):
            self.stderr = Stream(stderr)

        async def wait_closed(self):
            pass

        def close(self):
            pass

    class Connection:
        async def create_process(self, *_args, **_kwargs):
            return Process()

    async def run():
        with pytest.raises(SlurmCommandError) as caught:
            await bounded_run(Connection(), "read-only", 1)
        error = caught.value
        assert error.category == expected
        assert error.exit_status == 2
        assert "private" not in str(error)
        assert "secret" not in str(error)

    asyncio.run(run())


def test_full_history_backfill_retry_restart_and_old_unresolved(app, account, settings):
    account_id = account["id"]
    now = utcnow()
    late_submit = now - timedelta(days=3)
    late = observed(
        "COMPLETED",
        source="sacct",
        jid="888",
        submit=late_submit,
        start_time=late_submit + timedelta(hours=1),
        end_time=late_submit + timedelta(hours=2),
        restarts=0,
        suspended_seconds=0,
        scheduler_record_key="db:888",
    )
    seen_since = []
    snapshots = [
        Snapshot(),
        Snapshot(history_supported=False),
        Snapshot(),
        Snapshot(history=[late]),
    ]

    class Collector:
        async def snapshot(self, account, since):
            seen_since.append(since)
            return snapshots.pop(0)

    async def run():
        service = JobSyncService(settings, app.state.sessions, app.state.ssh, Collector())
        await service.sync_once()
        with app.state.sessions() as db:
            sync = db.get(JobSyncState, account_id)
            assert sync.last_full_history_at is not None
            sync.last_full_history_at = now - timedelta(hours=2)
            db.commit()
            previous_full = sync.last_full_history_at
        # Failed/partial backfills do not advance the persisted watermark.
        await service.sync_once()
        with app.state.sessions() as db:
            sync = db.get(JobSyncState, account_id)
            assert sync.last_full_history_at == previous_full and sync.status == "partial"
        assert seen_since[-1] <= utcnow() - timedelta(days=30)
        # A fresh service after restart uses the same persisted full-scan watermark.
        service = JobSyncService(settings, app.state.sessions, app.state.ssh, service.collector)
        await service.sync_once()
        with app.state.sessions() as db:
            sync = db.get(JobSyncState, account_id)
            assert sync.last_full_history_at > previous_full
            # Force the next periodic full scan; the late record was not in squeue.
            sync.last_full_history_at = now - timedelta(hours=2)
            db.commit()
        await service.sync_once()
        assert seen_since[-1] <= late_submit
        with app.state.sessions() as db:
            assert db.scalar(select(SlurmJob).where(SlurmJob.job_id == "888")) is not None
            old = SlurmJob(
                official_account_id=account_id,
                cluster_name="cluster-a",
                job_id="999",
                submit_time=now - timedelta(days=60),
                latest_state="RUNNING",
                data_source="squeue",
                last_synced_at=now,
                observation_status="awaiting_history",
                complex_history=True,
                attribution_source="unassigned",
            )
            db.add(old)
            db.commit()
        snapshots.append(Snapshot())
        await service.sync_once()
        assert seen_since[-1] <= now - timedelta(days=60)
        with app.state.sessions() as db:
            old = db.scalar(select(SlurmJob).where(SlurmJob.job_id == "999"))
            assert old.observation_status == "awaiting_history" and old.end_time is None

    asyncio.run(run())
