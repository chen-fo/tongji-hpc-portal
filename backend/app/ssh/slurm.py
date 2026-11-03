"""Read-only Slurm queries and bounded parsers. Never submit or modify a job."""

import asyncio
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.ssh.manager import SSHUnavailable, validate_target
from app.ssh.real import connection_options

JOB_ID = re.compile(r"^[0-9]+(?:_[0-9]+)?(?:\+[0-9]+)?$")
CLUSTER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
TRES_KEY = re.compile(r"^[A-Za-z0-9_./:-]{1,128}$")
UNKNOWN = {"", "unknown", "n/a", "none", "(null)", "invalid", "unlimited", "infinite"}
PENDING = {"PENDING", "CONFIGURING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD"}
ACTIVE = {"RUNNING", "COMPLETING", "SUSPENDED", "STOPPED", "RESIZING", "STAGE_OUT"}
TERMINAL = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
}
PREFIX = "LC_ALL=C TZ=UTC0 SLURM_TIME_FORMAT=standard"


class SlurmCommandError(SSHUnavailable):
    """Expose a fixed diagnosis, never arbitrary remote stderr or command output."""

    def __init__(self, status: int | None, stderr: str):
        super().__init__("Slurm 查询失败，请检查命令和账号权限")
        self.exit_status = status if isinstance(status, int) and 0 <= status <= 255 else None
        message = stderr.lower()
        if any(s in message for s in ("unrecognized option", "invalid option", "unknown option")):
            self.category = "unsupported_option"
        elif any(s in message for s in ("invalid field", "invalid job format", "unknown field")):
            self.category = "unsupported_field"
        elif any(
            s in message for s in ("permission denied", "access denied", "insufficient privileges")
        ):
            self.category = "permission_denied"
        elif "accounting storage" in message and any(
            s in message for s in ("disabled", "not enabled", "not configured")
        ):
            self.category = "accounting_disabled"
        elif any(s in message for s in ("slurmdbd", "connection refused", "failed to connect")):
            self.category = "accounting_service_error"
        else:
            self.category = "unknown"


def state_name(value: str) -> str:
    return value.strip().upper().split()[0].rstrip("+") if value.strip() else "UNKNOWN"


def value_or_none(value: str, limit: int = 4096) -> str | None:
    value = value.strip()
    if value.lower() in UNKNOWN:
        return None
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError("Invalid Slurm field")
    return value


def timestamp(value: str) -> datetime | None:
    value = value_or_none(value, 64)
    if not value or value.startswith("1970-01-01T00:00:00"):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo:
        return result.astimezone(timezone.utc).replace(tzinfo=None)
    # Every remote Slurm query is explicitly run with TZ=UTC0.
    return result


def seconds(value: str) -> int | None:
    value = value_or_none(value, 32)
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    match = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d{2})(?::(\d{2}))?", value)
    if not match:
        return None
    days, first, second, third = match.groups()
    if int(second) >= 60 or (third is not None and int(third) >= 60):
        return None
    if third is None:
        return int(days or 0) * 86400 + int(first) * 60 + int(second)
    return int(days or 0) * 86400 + int(first) * 3600 + int(second) * 60 + int(third)


def tres(value: str) -> dict[str, str] | None:
    value = value_or_none(value, 4096)
    if not value:
        return None
    result = {}
    for part in value.split(","):
        if "=" not in part:
            raise ValueError("Malformed TRES")
        key, item = part.split("=", 1)
        if not TRES_KEY.fullmatch(key) or not item or len(item) > 128 or key in result:
            raise ValueError("Malformed TRES")
        result[key] = item
    return result


@dataclass
class Observation:
    cluster_name: str
    job_id: str
    submit_time: datetime
    latest_state: str
    data_source: str
    job_name: str | None = None
    partition: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    expected_start_time: datetime | None = None
    expected_end_time: datetime | None = None
    elapsed_seconds: int | None = None
    suspended_seconds: int | None = None
    time_limit_seconds: int | None = None
    requested_resources: dict | None = None
    allocated_resources: dict | None = None
    state_reason: str | None = None
    exit_code: str | None = None
    restarts: int | None = None
    scheduler_record_key: str | None = None

    @property
    def key(self):
        return self.cluster_name, self.job_id, self.submit_time


@dataclass
class Snapshot:
    queue: list[Observation] = field(default_factory=list)
    history: list[Observation] = field(default_factory=list)
    history_supported: bool = True
    versions: dict = field(default_factory=dict)
    timezone: str = "UTC"
    skipped_records: int = 0
    error: str | None = None
    cluster_name: str = "mock"
    remote_timezone_offset: str | None = None
    available_fields: list[str] = field(default_factory=list)
    history_diagnostic: dict | None = None


def queue_records(text: str, cluster: str, limit: int) -> tuple[list[Observation], int]:
    lines = text.splitlines()
    if len(lines) > limit:
        raise SSHUnavailable("队列记录超过安全上限，请缩小查询范围")
    result, skipped = [], 0
    for line in lines:
        try:
            fields = [item.strip() for item in line.split("|")]
            if len(fields) != 11:
                raise ValueError("Invalid queue row")
            jid, name, partition, state, submit, start, end, elapsed, limit_time, cpus, reason = (
                fields
            )
            submitted, status = timestamp(submit), state_name(state)
            if not JOB_ID.fullmatch(jid) or not submitted or status == "UNKNOWN":
                raise ValueError("Invalid queue identity")
            active = status in ACTIVE
            expected = status in PENDING
            cpu = int(cpus) if cpus.isdigit() and int(cpus) > 0 else None
            result.append(
                Observation(
                    cluster_name=cluster,
                    job_id=jid,
                    submit_time=submitted,
                    latest_state=value_or_none(state, 128) or "UNKNOWN",
                    data_source="squeue",
                    job_name=value_or_none(name, 256),
                    partition=value_or_none(partition, 128),
                    start_time=timestamp(start) if active else None,
                    expected_start_time=timestamp(start) if expected else None,
                    expected_end_time=timestamp(end),
                    elapsed_seconds=seconds(elapsed) if active else None,
                    time_limit_seconds=seconds(limit_time),
                    requested_resources={"cpu": str(cpu)} if expected and cpu else None,
                    allocated_resources={"cpu": str(cpu)} if active and cpu else None,
                    state_reason=value_or_none(reason, 512) if expected else None,
                )
            )
        except (ValueError, OverflowError):
            skipped += 1
    return result, skipped


def history_records(
    text: str, fields: list[str], cluster: str, limit: int
) -> tuple[list[Observation], int]:
    lines = text.splitlines()
    if len(lines) > limit:
        raise SSHUnavailable("历史记录超过安全上限，请缩小查询范围")
    result, skipped = [], 0
    for line in lines:
        try:
            parts = line.split("|")
            if len(parts) != len(fields):
                raise ValueError("Invalid history row")
            data = {key: value.strip() for key, value in zip(fields, parts)}
            jid = data["JobID"]
            # Job steps are not separate allocations and must not be billed twice.
            if "." in jid:
                continue
            submitted = timestamp(data["Submit"])
            status = state_name(data["State"])
            observed_cluster = value_or_none(data.get("Cluster", ""), 128) or cluster
            if (
                not JOB_ID.fullmatch(jid)
                or not submitted
                or status == "UNKNOWN"
                or observed_cluster != cluster
            ):
                raise ValueError("Invalid history identity")
            active, pending = status in ACTIVE, status in PENDING
            started, ended = timestamp(data.get("Start", "")), timestamp(data.get("End", ""))
            raw_restart = data.get("Restarts", "")
            restart_count = int(raw_restart) if raw_restart.isdigit() else None
            record = value_or_none(data.get("DBIndex", ""), 128)
            record_key = (
                "db:" + record
                if record and record.isdigit()
                else ("start:" + started.isoformat() if started and not pending else None)
            )
            result.append(
                Observation(
                    cluster_name=cluster,
                    job_id=jid,
                    submit_time=submitted,
                    latest_state=value_or_none(data["State"], 128) or "UNKNOWN",
                    data_source="sacct",
                    job_name=value_or_none(data.get("JobName", ""), 256),
                    partition=value_or_none(data.get("Partition", ""), 128),
                    start_time=started if not pending else None,
                    end_time=ended if status in TERMINAL else None,
                    expected_start_time=started if pending else None,
                    expected_end_time=ended if active or pending else None,
                    elapsed_seconds=seconds(data.get("ElapsedRaw", "")) if not pending else None,
                    suspended_seconds=seconds(data.get("Suspended", "")),
                    requested_resources=tres(data.get("ReqTRES", "")),
                    allocated_resources=tres(data.get("AllocTRES", "")),
                    exit_code=value_or_none(data.get("ExitCode", ""), 32),
                    restarts=restart_count,
                    scheduler_record_key=record_key,
                )
            )
        except (KeyError, ValueError, OverflowError):
            skipped += 1
    return result, skipped


async def bounded_run(conn, command: str, timeout: float, maximum: int = 2 * 1024 * 1024) -> str:
    async with asyncio.timeout(timeout):
        process = await conn.create_process(command, encoding="utf-8", errors="replace")

        async def read(stream, cap):
            chunks, total = [], 0
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return "".join(chunks)
                total += len(chunk)
                if total > cap:
                    raise SSHUnavailable("Slurm 查询结果超过安全上限")
                chunks.append(chunk)

        tasks = [
            asyncio.create_task(read(process.stdout, maximum)),
            asyncio.create_task(read(process.stderr, 16384)),
        ]
        try:
            stdout, stderr = await asyncio.gather(*tasks)
            await process.wait_closed()
            if process.exit_status != 0:
                raise SlurmCommandError(process.exit_status, stderr)
            return stdout
        finally:
            for task in tasks:
                task.cancel()
            # AsyncSSH close() initiates closure synchronously; it does not wait
            # for the peer. Never let a stuck peer or reader block another account.
            try:
                process.close()
            except Exception:
                pass
            closed = asyncio.create_task(process.wait_closed())
            try:
                await asyncio.wait(tasks, timeout=0.5)
                done, _ = await asyncio.wait({closed}, timeout=0.5)
                if closed in done:
                    # Retrieve exceptions without masking the original failure.
                    try:
                        closed.result()
                    except Exception:
                        pass
            finally:
                closed.cancel()


class RealSlurmCollector:
    def __init__(self, settings, ssh):
        self.settings, self.ssh = settings, ssh

    async def snapshot(self, account, since: datetime) -> Snapshot:
        options = await asyncio.to_thread(connection_options, self.settings, account.alias)
        if (options.host, options.port, options.username) != (
            account.ssh_host,
            account.ssh_port,
            account.ssh_username,
        ):
            raise SSHUnavailable("SSH profile 与已绑定的官方账号不一致")
        timeout = self.settings.ssh_timeout_seconds
        username = shlex.quote(account.ssh_username)
        async with self.ssh.connect(account) as conn:

            async def run(command):
                return await bounded_run(conn, command, timeout)

            probe = await run(
                "printf 'remote_offset='; date +%z; for c in squeue sacct scontrol; do if command -v \"$c\" >/dev/null 2>&1; then printf '%s=' \"$c\"; \"$c\" --version 2>/dev/null | head -c 120; printf '\\n'; else printf '%s=missing\\n' \"$c\"; fi; done"
            )
            capabilities = dict(line.split("=", 1) for line in probe.splitlines() if "=" in line)
            if (
                capabilities.get("squeue", "missing") == "missing"
                or capabilities.get("scontrol", "missing") == "missing"
            ):
                raise SSHUnavailable("服务器未提供 squeue 或 scontrol，无法安全确认集群")
            cluster = (
                await run(
                    'LC_ALL=C scontrol show config | awk \'$1 == "ClusterName" && $2 == "=" {print $3; exit}\''
                )
            ).strip()
            if not CLUSTER.fullmatch(cluster):
                raise SSHUnavailable("无法可靠识别 Slurm 集群名称")
            fmt = "%i|%j|%P|%T|%V|%S|%e|%M|%l|%C|%R"
            output = await run(
                f"{PREFIX} squeue --local --array --noheader -u {username} -o {shlex.quote(fmt)}"
            )
            queue, skipped = queue_records(output, cluster, self.settings.slurm_max_records)
            remote_offset = capabilities.get("remote_offset", "")
            snapshot = Snapshot(
                queue=queue,
                versions={
                    k: capabilities.get(k, "unknown")[:120] for k in ("squeue", "sacct", "scontrol")
                },
                timezone="UTC",
                skipped_records=skipped,
                cluster_name=cluster,
                remote_timezone_offset=remote_offset
                if re.fullmatch(r"[+-]\d{4}", remote_offset)
                else None,
            )
            if capabilities.get("sacct", "missing") == "missing":
                snapshot.history_supported, snapshot.error = False, "sacct 不可用，历史任务待确认"
                snapshot.history_diagnostic = {"stage": "capability", "category": "missing"}
                return snapshot
            stage = "fields"
            try:
                help_fields = (await run(f"{PREFIX} sacct --helpformat")).split()
                available = {item.lower() for item in help_fields}
                all_fields = [
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
                fields = [item for item in all_fields if item.lower() in available]
                snapshot.available_fields = fields
                if not {"JobID", "State", "Submit", "Start", "End", "Cluster"}.issubset(fields):
                    snapshot.history_supported = False
                    snapshot.error = "sacct 缺少必要字段，历史任务待确认"
                    snapshot.history_diagnostic = {
                        "stage": stage,
                        "category": "required_fields_missing",
                    }
                    return snapshot
                widths = {
                    "JobID": 128,
                    "JobName": 256,
                    "Partition": 128,
                    "State": 128,
                    "ReqTRES": 1024,
                    "AllocTRES": 1024,
                    "Cluster": 128,
                }
                field_option = ",".join(
                    f"{item}%{widths[item]}" if item in widths else item for item in fields
                )
                start = shlex.quote(since.strftime("%Y-%m-%dT%H:%M:%S"))
                stage = "history_query"
                output = await run(
                    f"{PREFIX} sacct --local --duplicates --allocations --noheader --parsable2 -u {username} -S {start} --format={shlex.quote(field_option)}"
                )
                snapshot.history, count = history_records(
                    output, fields, cluster, self.settings.slurm_max_records
                )
                snapshot.skipped_records += count
            except SlurmCommandError as exc:
                snapshot.history_supported, snapshot.error = False, "sacct 查询失败，历史任务待确认"
                snapshot.history_diagnostic = {
                    "stage": stage,
                    "category": exc.category,
                    "exit_status": exc.exit_status,
                }
            except TimeoutError:
                snapshot.history_supported, snapshot.error = False, "sacct 查询失败，历史任务待确认"
                snapshot.history_diagnostic = {"stage": stage, "category": "timeout"}
            except SSHUnavailable:
                snapshot.history_supported, snapshot.error = False, "sacct 查询失败，历史任务待确认"
                snapshot.history_diagnostic = {"stage": stage, "category": "unavailable"}
            return snapshot


class MockSlurmCollector:
    """Explicit fixtures only; never run commands or create fake production jobs."""

    def __init__(self, settings):
        self.settings = settings
        self.snapshots: dict[int, Snapshot] = {}
        self.calls: list[int] = []

    async def snapshot(self, account, since: datetime) -> Snapshot:
        validate_target(self.settings, account)
        if account.alias in self.settings.mock_fail_aliases:
            raise SSHUnavailable("Mock SSH 模拟连接失败")
        self.calls.append(account.id)
        return self.snapshots.get(
            account.id, Snapshot(versions={"squeue": "Mock", "sacct": "Mock", "scontrol": "Mock"})
        )


async def probe_slurm(collector, account, since: datetime) -> dict:
    """Probe read-only query capabilities without disclosing task content."""
    snapshot = await collector.snapshot(account, since)
    return {
        "squeue_query_ok": True,
        "scontrol_query_ok": True,
        "sacct_query_ok": snapshot.history_supported,
        "cluster_name": snapshot.cluster_name,
        "versions": snapshot.versions,
        "query_timezone": snapshot.timezone,
        "remote_timezone_offset": snapshot.remote_timezone_offset,
        "available_sacct_fields": snapshot.available_fields,
        "queue_count": len(snapshot.queue),
        "history_count": len(snapshot.history),
        "skipped_records": snapshot.skipped_records,
        "error": snapshot.error,
        "history_diagnostic": snapshot.history_diagnostic,
    }
