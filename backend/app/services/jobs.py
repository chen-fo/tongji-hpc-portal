"""Background, read-only Slurm synchronization; failures never interrupt terminals."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, tuple_

from app.models.entities import OfficialAccount, utcnow
from app.models.jobs import JobSyncState, SlurmJob, SlurmJobRun
from app.ssh.manager import SSHUnavailable
from app.ssh.slurm import (
    ACTIVE,
    PENDING,
    TERMINAL,
    MockSlurmCollector,
    RealSlurmCollector,
    state_name,
)

logger = logging.getLogger("portal.jobs")


class JobSyncService:
    def __init__(self, settings, sessions, ssh, collector=None):
        self.settings, self.sessions, self.ssh = settings, sessions, ssh
        self.collector = collector or (
            MockSlurmCollector(settings)
            if settings.ssh_mode == "mock"
            else RealSlurmCollector(settings, ssh)
        )
        self.task = None
        self.lock = asyncio.Lock()

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop())
            return True
        return False

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self):
        while True:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Slurm background synchronization failed; existing services remain active"
                )
            await asyncio.sleep(self.settings.slurm_sync_interval_seconds)

    def _accounts(self):
        with self.sessions() as db:
            return list(
                db.scalars(select(OfficialAccount).where(OfficialAccount.enabled.is_(True))).all()
            )

    @staticmethod
    def _account_unchanged(current, original):
        return bool(
            current
            and current.enabled
            and (current.alias, current.ssh_host, current.ssh_port, current.ssh_username)
            == (original.alias, original.ssh_host, original.ssh_port, original.ssh_username)
        )

    def _attempt(self, account, now):
        with self.sessions() as db:
            if not self._account_unchanged(db.get(OfficialAccount, account.id), account):
                return None
            sync = db.get(JobSyncState, account.id)
            if sync is None:
                sync = JobSyncState(
                    official_account_id=account.id, status="idle", skipped_records=0
                )
                db.add(sync)
            full_since = now - timedelta(days=self.settings.slurm_initial_lookback_days)
            full_due = (
                sync.last_full_history_at is None
                or sync.last_full_history_at > now
                or now - sync.last_full_history_at
                >= timedelta(seconds=self.settings.slurm_full_history_interval_seconds)
            )
            since = (
                (sync.last_success_at - timedelta(days=1)) if sync.last_success_at else full_since
            )
            # A clock change must not skip the initial look-back period.
            if since > now:
                since = full_since
                full_due = True
            if full_due:
                since = min(since, full_since)
            earliest_unresolved = db.scalar(
                select(func.min(SlurmJob.submit_time)).where(
                    SlurmJob.official_account_id == account.id,
                    SlurmJob.observation_status == "awaiting_history",
                )
            )
            if earliest_unresolved is not None:
                since = min(since, earliest_unresolved)
            sync.status, sync.last_attempt_at, sync.error = "syncing", now, None
            db.commit()
            return since, full_due

    def _failed(self, account, message):
        with self.sessions() as db:
            if not self._account_unchanged(db.get(OfficialAccount, account.id), account):
                return
            sync = db.get(JobSyncState, account.id)
            if sync:
                sync.status, sync.error = "failed", message
                db.commit()

    @staticmethod
    def _apply_history(job, item, now):
        job.latest_state, job.data_source, job.last_synced_at = item.latest_state, "sacct", now
        job.observation_status = "observed"
        for name in (
            "job_name",
            "partition",
            "start_time",
            "end_time",
            "expected_start_time",
            "expected_end_time",
            "elapsed_seconds",
            "suspended_seconds",
            "time_limit_seconds",
            "requested_resources",
            "allocated_resources",
            "state_reason",
            "exit_code",
            "restarts",
        ):
            setattr(job, name, getattr(item, name))

    @staticmethod
    def _apply_queue(job, item, history, now):
        state = state_name(item.latest_state)
        job.latest_state, job.data_source, job.last_synced_at = item.latest_state, "squeue", now
        job.observation_status, job.end_time, job.exit_code = "observed", None, None
        for name in (
            "job_name",
            "partition",
            "start_time",
            "expected_start_time",
            "expected_end_time",
            "elapsed_seconds",
            "time_limit_seconds",
            "state_reason",
        ):
            setattr(job, name, getattr(item, name))
        if state in PENDING:
            job.requested_resources, job.allocated_resources = item.requested_resources, None
        else:
            # A fresh matching sacct record supplies TRES; squeue cannot prove GPU allocation.
            matched = bool(
                history
                and item.start_time is not None
                and item.start_time == history.start_time
                and state_name(history.latest_state) == state
                and history.allocated_resources
                and item.allocated_resources
                and history.allocated_resources.get("cpu") == item.allocated_resources.get("cpu")
                and history.allocated_resources.get("cpu") is not None
            )
            if matched:
                job.requested_resources = history.requested_resources
                job.allocated_resources = history.allocated_resources
                job.suspended_seconds, job.restarts = history.suspended_seconds, history.restarts
                job.data_source = "squeue+sacct"
            else:
                job.requested_resources, job.allocated_resources = None, item.allocated_resources
                job.suspended_seconds, job.restarts = None, None
        job.complex_history = (
            True  # Running or pending records are never eligible for final billing.
        )

    def _save(self, account, snapshot, now, full_due=False):
        with self.sessions() as db:
            if not self._account_unchanged(db.get(OfficialAccount, account.id), account):
                return
            sync = db.get(JobSyncState, account.id)
            if not sync:
                return
            observations = snapshot.history + snapshot.queue
            keys = list({item.key for item in observations})
            existing = {}
            for i in range(0, len(keys), 100):
                rows = db.scalars(
                    select(SlurmJob).where(
                        SlurmJob.official_account_id == account.id,
                        tuple_(SlurmJob.cluster_name, SlurmJob.job_id, SlurmJob.submit_time).in_(
                            keys[i : i + 100]
                        ),
                    )
                )
                existing.update(
                    {(job.cluster_name, job.job_id, job.submit_time): job for job in rows}
                )
            seen, active_queue = set(), set()
            history = sorted(
                snapshot.history,
                key=lambda row: (
                    row.submit_time,
                    row.job_id,
                    row.start_time or datetime.min,
                    row.end_time or datetime.min,
                ),
            )
            latest_history = {item.key: item for item in history}
            for item in history + snapshot.queue:
                key = item.key
                seen.add(key)
                if item.data_source == "squeue":
                    active_queue.add(key)
                job = existing.get(key)
                if job is None:
                    job = SlurmJob(
                        official_account_id=account.id,
                        cluster_name=item.cluster_name,
                        job_id=item.job_id,
                        submit_time=item.submit_time,
                        attribution_source="unassigned",
                        latest_state=item.latest_state,
                        observation_status="observed",
                        complex_history=True,
                        last_synced_at=now,
                        data_source=item.data_source,
                    )
                    db.add(job)
                    db.flush()
                    existing[key] = job
                if item.data_source == "sacct":
                    self._apply_history(job, item, now)
                    if item.scheduler_record_key:
                        run = db.scalar(
                            select(SlurmJobRun).where(
                                SlurmJobRun.slurm_job_id == job.id,
                                SlurmJobRun.scheduler_record_key == item.scheduler_record_key,
                            )
                        )
                        if not run:
                            run = SlurmJobRun(
                                slurm_job_id=job.id,
                                scheduler_record_key=item.scheduler_record_key,
                                state=item.latest_state,
                            )
                            db.add(run)
                        for name in (
                            "start_time",
                            "end_time",
                            "elapsed_seconds",
                            "suspended_seconds",
                            "allocated_resources",
                            "exit_code",
                        ):
                            setattr(run, name, getattr(item, name))
                        run.state = item.latest_state
                else:
                    self._apply_queue(job, item, latest_history.get(key), now)
            db.flush()
            for key in active_queue:
                job = existing[key]
                if job.data_source == "squeue+sacct" and state_name(job.latest_state) == "RUNNING":
                    run_count = len(
                        db.scalars(
                            select(SlurmJobRun.id).where(SlurmJobRun.slurm_job_id == job.id)
                        ).all()
                    )
                    job.complex_history = (
                        job.restarts != 0
                        or job.suspended_seconds != 0
                        or run_count != 1
                        or "_" in job.job_id
                        or "+" in job.job_id
                    )
            for key in seen - active_queue:
                job = existing[key]
                if state_name(job.latest_state) in TERMINAL:
                    run_count = len(
                        db.scalars(
                            select(SlurmJobRun.id).where(SlurmJobRun.slurm_job_id == job.id)
                        ).all()
                    )
                    job.complex_history = (
                        job.restarts != 0
                        or run_count != 1
                        or "_" in job.job_id
                        or "+" in job.job_id
                    )
                elif state_name(job.latest_state) in ACTIVE | PENDING:
                    job.observation_status, job.complex_history = "awaiting_history", True
            if snapshot.skipped_records == 0:
                # Disappearance is not a terminal state. Keep the previous observation.
                old = db.scalars(
                    select(SlurmJob).where(
                        SlurmJob.official_account_id == account.id,
                        SlurmJob.end_time.is_(None),
                        SlurmJob.observation_status == "observed",
                    )
                )
                for job in old:
                    key = (job.cluster_name, job.job_id, job.submit_time)
                    if key not in seen and state_name(job.latest_state) in ACTIVE | PENDING:
                        job.observation_status, job.complex_history = "awaiting_history", True
            sync.history_supported = snapshot.history_supported
            sync.versions, sync.timezone, sync.skipped_records = (
                snapshot.versions,
                snapshot.timezone,
                snapshot.skipped_records,
            )
            if snapshot.history_supported and snapshot.skipped_records == 0:
                sync.status, sync.error, sync.last_success_at = "ok", None, now
                if full_due:
                    sync.last_full_history_at = now
            else:
                sync.status = "partial"
                sync.error = snapshot.error or "部分任务记录无法识别，已保留历史数据"
            db.commit()

    async def sync_once(self):
        async with self.lock:
            accounts = await asyncio.to_thread(self._accounts)
            for account in accounts:
                now = utcnow()
                attempt = await asyncio.to_thread(self._attempt, account, now)
                if attempt is None:
                    continue
                since, full_due = attempt
                try:
                    snapshot = await self.collector.snapshot(account, since)
                    await asyncio.to_thread(self._save, account, snapshot, utcnow(), full_due)
                except asyncio.CancelledError:
                    raise
                except (SSHUnavailable, TimeoutError):
                    await asyncio.to_thread(
                        self._failed, account, "Slurm 同步失败，请检查 SSH、命令和查询权限"
                    )
                except Exception:
                    logger.warning("Slurm synchronization failed; remote exception details omitted")
                    await asyncio.to_thread(
                        self._failed, account, "Slurm 同步失败，请检查 SSH、命令和查询权限"
                    )
