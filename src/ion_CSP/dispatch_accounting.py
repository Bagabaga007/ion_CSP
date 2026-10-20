"""Fail-closed Slurm allocation accounting primitives.

This module deliberately does not inspect or serialize dpdispatcher machine
configuration. A caller supplies explicit ownership tags, a sanitized
resource request, task identifiers, and a one-shot submitter. The submitter
must perform at most one external scheduler submission per ``submit_once``
call. The installed dpdispatcher Slurm backend does not currently meet that
contract because its ``do_submit`` method retries internally.

Slurm ``sacct`` data produced here is allocation evidence only. It is not a
provider invoice and this module never estimates a currency amount.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence


_SCHEMA_VERSION = 1
_TAG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_JOB_KEY_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_TASK_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_SLURM_JOB_ID_RE = re.compile(
    r"\A[0-9]+(?:_[0-9]+)?(?:;[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?\Z",
    re.ASCII,
)
_SACCT_ALLOCATION_ID_RE = re.compile(r"\A[0-9]+(?:_[0-9]+)?\Z", re.ASCII)
_PARTITION_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_GPU_TRES_RE = re.compile(r"\Agres/gpu(?::[A-Za-z0-9_.-]+)?\Z", re.ASCII)
_SACCT_FIELDS = 11


class AccountingError(RuntimeError):
    """Base class for safe accounting errors."""


class JournalError(AccountingError):
    """The private append-only journal could not be safely used."""


class JournalCorruptionError(JournalError):
    """The journal is malformed; accounting stops instead of guessing."""


class UnresolvedAttemptError(AccountingError):
    """A prior submission may have reached Slurm and must be reconciled."""


class SubmissionOutcomeUnknownError(AccountingError):
    """A submit call returned no trustworthy scheduler job ID."""

    def __init__(self, attempt_id: str):
        self.attempt_id = attempt_id
        super().__init__(
            "submission outcome is unknown; reconcile scheduler state before retrying "
            f"(attempt {attempt_id})"
        )


class PostSubmitJournalError(JournalError):
    """Slurm returned a job ID but its result record could not be persisted."""

    def __init__(self, attempt_id: str, job_id: str):
        self.attempt_id = attempt_id
        self.job_id = job_id
        super().__init__(
            "Slurm returned a job ID, but the result could not be persisted; "
            f"do not resubmit until reconciled (attempt {attempt_id}, job {job_id})"
        )


class SacctParseError(ValueError):
    """A sacct row is malformed or inconsistent with its allocation fields."""


@dataclass(frozen=True)
class AccountingIdentity:
    owner_id: str
    run_id: str
    role: str
    stage: str

    def __post_init__(self) -> None:
        for name in ("owner_id", "run_id", "role", "stage"):
            _validate_tag(name, getattr(self, name))

    def as_dict(self) -> dict[str, str]:
        return {
            "owner_id": self.owner_id,
            "run_id": self.run_id,
            "role": self.role,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class RequestedResources:
    """The only resource fields permitted in the private journal."""

    cpu_per_node: int
    gpu_per_node: int
    nodes: int
    partition: str | None

    def __post_init__(self) -> None:
        _validate_int("cpu_per_node", self.cpu_per_node, minimum=1)
        _validate_int("gpu_per_node", self.gpu_per_node, minimum=0)
        _validate_int("nodes", self.nodes, minimum=1)
        if self.partition == "":
            object.__setattr__(self, "partition", None)
        elif self.partition is not None:
            if not isinstance(self.partition, str) or not _PARTITION_RE.fullmatch(
                self.partition
            ):
                raise ValueError("partition must be a safe Slurm partition name")

    @classmethod
    def from_dpdispatcher_resources(cls, resources: Any) -> "RequestedResources":
        """Copy only public CPU/GPU/node/partition values from a Resources object."""

        return cls(
            cpu_per_node=getattr(resources, "cpu_per_node"),
            gpu_per_node=getattr(resources, "gpu_per_node"),
            nodes=getattr(resources, "number_node"),
            partition=getattr(resources, "queue_name"),
        )

    def as_dict(self) -> dict[str, int | str | None]:
        return {
            "cpu_per_node": self.cpu_per_node,
            "gpu_per_node": self.gpu_per_node,
            "nodes": self.nodes,
            "partition": self.partition,
        }


class OneShotSubmitter(Protocol):
    """Adapter whose method makes at most one external submission attempt."""

    scheduler_name: str

    def submit_once(self, job: Any, /) -> str:
        """Submit once and return the exact scheduler ID from that call."""


@dataclass(frozen=True)
class SacctAllocation:
    job_id: str
    state: str
    elapsed_seconds: int
    allocated_cpus: int
    allocated_nodes: int
    allocated_gpus: int | None
    partition: str | None

    def __post_init__(self) -> None:
        _validate_job_id(self.job_id)
        if not isinstance(self.state, str) or not re.fullmatch(
            r"[A-Z_]+", self.state, re.ASCII
        ):
            raise ValueError("state must be a normalized Slurm state token")
        _validate_int("elapsed_seconds", self.elapsed_seconds, minimum=0)
        _validate_int("allocated_cpus", self.allocated_cpus, minimum=1)
        _validate_int("allocated_nodes", self.allocated_nodes, minimum=1)
        if self.allocated_gpus is not None:
            _validate_int("allocated_gpus", self.allocated_gpus, minimum=0)
        if self.partition is not None and (
            not isinstance(self.partition, str)
            or not _PARTITION_RE.fullmatch(self.partition)
        ):
            raise ValueError("partition must be a safe Slurm partition name")


def _validate_tag(name: str, value: str) -> None:
    if not isinstance(value, str) or not _TAG_RE.fullmatch(value):
        raise ValueError(
            f"{name} must be 1-64 ASCII letters, digits, dot, underscore, or hyphen"
        )


def _validate_int(name: str, value: int, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _validate_task_ids(task_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(task_ids, (str, bytes)):
        raise ValueError("task_ids must be a sequence of safe task identifiers")
    values = tuple(task_ids)
    if not values:
        raise ValueError("at least one task identifier is required")
    for task_id in values:
        if not isinstance(task_id, str) or not _TASK_ID_RE.fullmatch(task_id):
            raise ValueError("task identifiers must be safe 1-128 character tokens")
    if len(set(values)) != len(values):
        raise ValueError("task identifiers must be unique within a scheduler job")
    return values


def _validate_job_id(job_id: Any) -> str:
    if not isinstance(job_id, str) or not _SLURM_JOB_ID_RE.fullmatch(job_id):
        raise ValueError("submitter did not return a valid Slurm job ID")
    return job_id


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AccountingJournal:
    """A private, append-only JSONL journal with fail-closed attempt tracking.

    The journal is opened with ``O_APPEND`` and ``O_NOFOLLOW`` where available,
    chmodded to exactly 0600, and fsynced after every record. A malformed or
    truncated line prevents later submissions through this journal.
    """

    def __init__(self, path: os.PathLike[str] | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_CREAT | os.O_RDWR | os.O_APPEND
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._fd = os.open(self.path, flags, 0o600)
            os.fchmod(self._fd, 0o600)
            if not stat.S_ISREG(os.fstat(self._fd).st_mode):
                raise JournalError("accounting journal must be a regular file")
        except OSError:
            raise JournalError("could not open private accounting journal") from None
        self._lock = threading.RLock()
        self._closed = False
        self._poisoned = False

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                os.close(self._fd)
                self._closed = True

    def __enter__(self) -> "AccountingJournal":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise JournalError("accounting journal is closed")
        if self._poisoned:
            raise JournalCorruptionError("accounting journal had a write or fsync failure")

    def _read_unlocked(self) -> list[dict[str, Any]]:
        self._ensure_open()
        size = os.fstat(self._fd).st_size
        if size == 0:
            return []
        raw = os.pread(self._fd, size, 0)
        if len(raw) != size or not raw.endswith(b"\n"):
            raise JournalCorruptionError("accounting journal has an incomplete line")
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise JournalCorruptionError(
                    f"accounting journal line {line_number} is invalid JSON"
                ) from None
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != _SCHEMA_VERSION
                or record.get("event") not in {"submit_intent", "submit_result", "submit_unknown"}
            ):
                raise JournalCorruptionError(
                    f"accounting journal line {line_number} has an unsupported record"
                )
            records.append(record)
        return records

    def _append_unlocked(self, record: dict[str, Any]) -> None:
        self._ensure_open()
        payload = (
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode("ascii")
        # Keep the journal unusable if an asynchronous exception interrupts
        # either operation. Clear the poison only after the bytes are durable.
        self._poisoned = True
        try:
            written = os.write(self._fd, payload)
            if written != len(payload):
                raise JournalError("accounting journal write was incomplete")
            os.fsync(self._fd)
        except OSError:
            raise JournalError("accounting journal write or fsync failed") from None
        except JournalError:
            raise
        self._poisoned = False

    def _locked(self):
        """Acquire both an in-process and interprocess lock on the journal."""

        return _JournalLock(self)


class _JournalLock:
    def __init__(self, journal: AccountingJournal):
        self.journal = journal

    def __enter__(self) -> None:
        self.journal._lock.acquire()
        try:
            self.journal._ensure_open()
            fcntl.flock(self.journal._fd, fcntl.LOCK_EX)
        except BaseException:
            self.journal._lock.release()
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            fcntl.flock(self.journal._fd, fcntl.LOCK_UN)
        finally:
            self.journal._lock.release()


def _related_attempts(
    records: Sequence[dict[str, Any]], identity: AccountingIdentity, job_key: str
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    intents: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    identity_dict = identity.as_dict()
    for record in records:
        if record.get("identity") != identity_dict or record.get("job_key") != job_key:
            continue
        attempt_id = record.get("attempt_id")
        if not isinstance(attempt_id, str):
            raise JournalCorruptionError("journal event is missing an attempt ID")
        if record["event"] == "submit_intent":
            if attempt_id in intents:
                raise JournalCorruptionError("journal contains a duplicate attempt ID")
            intents[attempt_id] = record
        else:
            if attempt_id in outcomes or attempt_id not in intents:
                raise JournalCorruptionError("journal outcome has no unique intent")
            outcomes[attempt_id] = record
    return [(intent, outcomes.get(attempt_id)) for attempt_id, intent in intents.items()]


def _reserve_attempt(
    journal: AccountingJournal,
    identity: AccountingIdentity,
    job_key: str,
    resources: RequestedResources,
    task_ids: tuple[str, ...],
    retry_of_job_id: str | None,
) -> tuple[str, int]:
    if retry_of_job_id is not None:
        retry_of_job_id = _validate_job_id(retry_of_job_id)
    with journal._locked():
        records = journal._read_unlocked()
        attempts = _related_attempts(records, identity, job_key)
        if attempts and attempts[-1][1] is None:
            raise UnresolvedAttemptError(
                "a prior submission has no durable outcome; reconcile it before retrying"
            )
        previous = attempts[-1] if attempts else None
        if previous is None:
            if retry_of_job_id is not None:
                raise ValueError("retry_of_job_id is invalid before the first submission")
            attempt_number = 1
        else:
            previous_intent, previous_outcome = previous
            assert previous_outcome is not None
            if previous_outcome.get("status") != "submitted":
                raise UnresolvedAttemptError(
                    "the prior submission outcome is unknown; reconcile it before retrying"
                )
            previous_job_id = previous_outcome.get("job_id")
            if retry_of_job_id != previous_job_id:
                raise ValueError(
                    "retry_of_job_id must name the latest durably recorded scheduler job"
                )
            if previous_intent.get("requested") != resources.as_dict():
                raise ValueError("resource request cannot change within a retry chain")
            if previous_intent.get("task_ids") != list(task_ids):
                raise ValueError("task mapping cannot change within a retry chain")
            attempt_number = len(attempts) + 1

        attempt_id = str(uuid.uuid4())
        intent = {
            "schema_version": _SCHEMA_VERSION,
            "event": "submit_intent",
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "occurred_at": _timestamp(),
            "scheduler": "slurm",
            "identity": identity.as_dict(),
            "job_key": job_key,
            "requested": resources.as_dict(),
            "task_ids": list(task_ids),
            "retry_of_job_id": retry_of_job_id,
        }
        journal._append_unlocked(intent)
        return attempt_id, attempt_number


def _record_unknown(
    journal: AccountingJournal,
    attempt_id: str,
    identity: AccountingIdentity,
    job_key: str,
) -> None:
    with journal._locked():
        journal._append_unlocked(
            {
                "schema_version": _SCHEMA_VERSION,
                "event": "submit_unknown",
                "attempt_id": attempt_id,
                "occurred_at": _timestamp(),
                "scheduler": "slurm",
                "identity": identity.as_dict(),
                "job_key": job_key,
                "status": "unknown",
            }
        )


def _record_result(
    journal: AccountingJournal,
    attempt_id: str,
    attempt_number: int,
    identity: AccountingIdentity,
    job_key: str,
    task_ids: tuple[str, ...],
    job_id: str,
) -> None:
    with journal._locked():
        journal._append_unlocked(
            {
                "schema_version": _SCHEMA_VERSION,
                "event": "submit_result",
                "attempt_id": attempt_id,
                "attempt_number": attempt_number,
                "occurred_at": _timestamp(),
                "scheduler": "slurm",
                "identity": identity.as_dict(),
                "job_key": job_key,
                "status": "submitted",
                "job_id": job_id,
                "task_mapping": [
                    {"task_index": index, "task_id": task_id}
                    for index, task_id in enumerate(task_ids)
                ],
            }
        )


def _dispatch_target(machine: Any) -> str:
    batch_type = getattr(machine, "batch_type", type(machine).__name__)
    context = getattr(machine, "context", None)
    context_type = type(context).__name__ if context is not None else ""
    if not isinstance(batch_type, str):
        return "unsupported"
    normalized_batch = batch_type.lower()
    normalized_context = context_type.lower()
    if normalized_batch == "shell" and normalized_context == "localcontext":
        return "local_shell"
    if normalized_batch == "slurm" and normalized_context not in {"", "localcontext"}:
        return "remote_slurm"
    return "unsupported"


def submit_with_accounting(
    journal: AccountingJournal,
    machine: Any,
    submitter: OneShotSubmitter,
    job: Any,
    *,
    identity: AccountingIdentity,
    job_key: str,
    resources: RequestedResources,
    task_ids: Sequence[str],
    retry_of_job_id: str | None = None,
) -> Any:
    """Submit through a one-shot adapter and journal remote Slurm allocations.

    Only a LocalContext+Shell call is delegated without scheduler accounting.
    All other unsupported or unknown machine/context combinations fail
    closed. For remote Slurm, the supplied adapter must be a genuine one-shot
    call; wrapping the current
    ``dpdispatcher.machines.slurm.Slurm.do_submit`` is unsafe because that
    method retries internally.

    A later retry is accepted only when the caller names the latest durable
    job ID in ``retry_of_job_id``. The caller is responsible for establishing
    that this prior scheduler job reached a terminal state before retrying.
    Any exception or invalid return after the write-ahead intent leaves an
    unresolved attempt and blocks future calls for the same identity and
    logical job key. Distinct allocations in the same stage must use distinct
    ``job_key`` values (for example, a dpdispatcher job hash).
    """

    method = getattr(submitter, "submit_once", None)
    if not callable(method):
        raise TypeError("submitter must implement the explicit submit_once(job) API")

    target = _dispatch_target(machine)
    if target == "local_shell":
        # Local Shell PIDs are not Slurm IDs and must never enter the journal.
        return method(job)
    if target != "remote_slurm":
        raise AccountingError(
            "accounting supports only remote Slurm or LocalContext+Shell dispatch"
        )

    scheduler_name = getattr(submitter, "scheduler_name", None)
    if scheduler_name != "slurm":
        raise TypeError("remote Slurm accounting requires a Slurm one-shot adapter")

    if not isinstance(job_key, str) or not _JOB_KEY_RE.fullmatch(job_key):
        raise ValueError("job_key must be a safe 1-128 character allocation identifier")
    safe_task_ids = _validate_task_ids(task_ids)
    attempt_id, attempt_number = _reserve_attempt(
        journal,
        identity,
        job_key,
        resources,
        safe_task_ids,
        retry_of_job_id,
    )
    try:
        raw_job_id = method(job)
    except Exception:
        try:
            _record_unknown(journal, attempt_id, identity, job_key)
        except Exception:
            # The write-ahead intent remains, so the next invocation fails
            # closed even when this supplemental marker cannot be appended.
            pass
        raise SubmissionOutcomeUnknownError(attempt_id) from None

    try:
        job_id = _validate_job_id(raw_job_id)
    except ValueError:
        try:
            _record_unknown(journal, attempt_id, identity, job_key)
        except Exception:
            pass
        raise SubmissionOutcomeUnknownError(attempt_id) from None

    try:
        _record_result(
            journal,
            attempt_id,
            attempt_number,
            identity,
            job_key,
            safe_task_ids,
            job_id,
        )
    except Exception:
        raise PostSubmitJournalError(attempt_id, job_id) from None
    return job_id


def _parse_nonnegative_int(value: str, field: str, line_number: int) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise SacctParseError(f"sacct line {line_number}: invalid {field}")
    return int(value)


def _gpu_count_from_tres(
    value: str, line_number: int
) -> tuple[int | None, int | None, int | None]:
    if value == "":
        return None, None, None
    recognized: dict[str, int] = {}
    for item in value.split(","):
        if not item or "=" not in item:
            raise SacctParseError(f"sacct line {line_number}: malformed AllocTRES")
        key, raw_count = item.split("=", 1)
        is_gpu = _GPU_TRES_RE.fullmatch(key) is not None
        if key not in {"cpu", "node"} and not is_gpu:
            continue
        if key in recognized:
            raise SacctParseError(f"sacct line {line_number}: duplicate AllocTRES key")
        recognized[key] = _parse_nonnegative_int(raw_count, "AllocTRES count", line_number)
    typed_gpu_counts = [
        count for key, count in recognized.items() if key.startswith("gres/gpu:")
    ]
    if typed_gpu_counts:
        gpu_count = sum(typed_gpu_counts)
        total_gpu_count = recognized.get("gres/gpu")
        if total_gpu_count is not None and total_gpu_count != gpu_count:
            raise SacctParseError(
                f"sacct line {line_number}: AllocTRES GPU total/type mismatch"
            )
    else:
        gpu_count = recognized.get("gres/gpu")
    return recognized.get("cpu"), recognized.get("node"), gpu_count


def _sacct_expected_ids(job_ids: Iterable[str] | None) -> dict[str, str] | None:
    if job_ids is None:
        return None
    expected: dict[str, str] = {}
    values = list(job_ids)
    for value in values:
        job_id = _validate_job_id(value)
        raw_id = job_id.split(";", 1)[0]
        if raw_id in expected:
            raise ValueError("expected Slurm job IDs must have unique raw IDs")
        expected[raw_id] = job_id
    return expected


def parse_sacct_allocations(
    text: str, *, job_ids: Iterable[str] | None = None
) -> list[SacctAllocation]:
    """Parse ``sacct --parsable2 --noheader`` allocation rows only.

    Expected column order is ``JobIDRaw,JobName,State,ElapsedRaw,AllocCPUS,
    NNodes,AllocTRES,Partition,Submit,Start,End``. Step rows such as
    ``123.batch``/``123.extern`` and array umbrella rows are excluded so their
    values cannot be counted in addition to the allocation record.
    """

    if not isinstance(text, str):
        raise TypeError("sacct output must be text")
    expected = _sacct_expected_ids(job_ids)
    allocations: list[SacctAllocation] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) != _SACCT_FIELDS:
            raise SacctParseError(
                f"sacct line {line_number}: expected {_SACCT_FIELDS} fields"
            )
        raw_job_id = fields[0]
        # Slurm steps have no independent allocation charge; exclude before
        # reading their fields, which may legitimately be blank/different.
        if "." in raw_job_id or "_[" in raw_job_id:
            continue
        if not _SACCT_ALLOCATION_ID_RE.fullmatch(raw_job_id):
            if expected is not None and raw_job_id not in expected:
                continue
            raise SacctParseError(f"sacct line {line_number}: invalid allocation job ID")
        if expected is not None and raw_job_id not in expected:
            continue
        if raw_job_id in seen:
            raise SacctParseError(f"sacct line {line_number}: duplicate allocation job ID")
        seen.add(raw_job_id)

        state = fields[2].split(None, 1)[0].removesuffix("+") if fields[2] else ""
        if not re.fullmatch(r"[A-Z_]+", state, re.ASCII):
            raise SacctParseError(f"sacct line {line_number}: invalid allocation state")
        partition = fields[7]
        if partition and not _PARTITION_RE.fullmatch(partition):
            raise SacctParseError(f"sacct line {line_number}: invalid partition")

        elapsed_raw, cpus_raw, nodes_raw = fields[3], fields[4], fields[5]
        if elapsed_raw in {"", "0", "Unknown"} and cpus_raw in {"", "0", "Unknown"} and nodes_raw in {
            "",
            "0",
            "Unknown",
        }:
            # Pending jobs do not yet have an allocation record to account.
            continue
        elapsed = _parse_nonnegative_int(elapsed_raw, "ElapsedRaw", line_number)
        cpus = _parse_nonnegative_int(cpus_raw, "AllocCPUS", line_number)
        nodes = _parse_nonnegative_int(nodes_raw, "NNodes", line_number)
        if cpus == 0 or nodes == 0:
            raise SacctParseError(
                f"sacct line {line_number}: partial allocation has zero CPUs or nodes"
            )
        tres_cpu, tres_nodes, gpus = _gpu_count_from_tres(fields[6], line_number)
        if tres_cpu is not None and tres_cpu != cpus:
            raise SacctParseError(f"sacct line {line_number}: AllocTRES CPU mismatch")
        if tres_nodes is not None and tres_nodes != nodes:
            raise SacctParseError(f"sacct line {line_number}: AllocTRES node mismatch")

        canonical_id = expected[raw_job_id] if expected is not None else raw_job_id
        allocations.append(
            SacctAllocation(
                job_id=canonical_id,
                state=state,
                elapsed_seconds=elapsed,
                allocated_cpus=cpus,
                allocated_nodes=nodes,
                allocated_gpus=gpus,
                partition=partition or None,
            )
        )
    return allocations


def format_sacct_evidence(allocations: Sequence[SacctAllocation]) -> str:
    """Return stable JSON allocation evidence, without invoice or cost fields."""

    job_ids = [allocation.job_id for allocation in allocations]
    if len(set(job_ids)) != len(job_ids):
        raise ValueError("allocation evidence must not contain duplicate job IDs")
    cpu_seconds = sum(
        allocation.elapsed_seconds * allocation.allocated_cpus
        for allocation in allocations
    )
    gpu_seconds: int | None
    if all(allocation.allocated_gpus is not None for allocation in allocations):
        gpu_seconds = sum(
            allocation.elapsed_seconds * allocation.allocated_gpus  # type: ignore[operator]
            for allocation in allocations
        )
    else:
        gpu_seconds = None
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "evidence_type": "slurm_allocation_only",
        "source": "sacct",
        "allocation_records": [asdict(allocation) for allocation in allocations],
        "allocation_totals": {
            "allocated_cpu_seconds": cpu_seconds,
            "allocated_gpu_seconds": gpu_seconds,
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
