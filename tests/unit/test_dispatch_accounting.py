import json
import stat

import pytest

from ion_CSP.dispatch_accounting import (
    AccountingError,
    AccountingIdentity,
    AccountingJournal,
    JournalCorruptionError,
    JournalError,
    RequestedResources,
    SacctParseError,
    SubmissionOutcomeUnknownError,
    PostSubmitJournalError,
    UnresolvedAttemptError,
    format_sacct_evidence,
    parse_sacct_allocations,
    submit_with_accounting,
)


class SSHContext:
    pass


class LocalContext:
    pass


class FakeSlurm:
    batch_type = "Slurm"

    def __init__(self):
        self.context = SSHContext()


class FakeLocalShell:
    batch_type = "Shell"

    def __init__(self):
        self.context = LocalContext()


class FakeRemoteShell:
    batch_type = "Shell"

    def __init__(self):
        self.context = SSHContext()


class FakeJob:
    def __repr__(self):
        return "FakeJob(password='do-not-record-this')"


class FakeSubmitter:
    scheduler_name = "slurm"

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def submit_once(self, job):
        self.calls.append(job)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeShellSubmitter:
    scheduler_name = "shell"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def submit_once(self, job):
        self.calls.append(job)
        return self.result


def _identity(**overrides):
    values = {
        "owner_id": "owner-7",
        "run_id": "run-20261019-a",
        "role": "gpu-worker",
        "stage": "relaxation-1",
    }
    values.update(overrides)
    return AccountingIdentity(**values)


def _resources(**overrides):
    values = {
        "cpu_per_node": 16,
        "gpu_per_node": 2,
        "nodes": 1,
        "partition": "gpu-short",
    }
    values.update(overrides)
    return RequestedResources(**values)


def _submit(
    journal,
    submitter,
    *,
    identity=None,
    job_key="dp-job-hash-001",
    retry_of_job_id=None,
):
    return submit_with_accounting(
        journal,
        FakeSlurm(),
        submitter,
        FakeJob(),
        identity=identity or _identity(),
        job_key=job_key,
        resources=_resources(),
        task_ids=("pop-0", "pop-1"),
        retry_of_job_id=retry_of_job_id,
    )


def test_journal_is_private_and_intent_precedes_submit(tmp_path):
    path = tmp_path / "private" / "slurm.jsonl"
    job = FakeJob()
    journal = AccountingJournal(path)

    class InspectingSubmitter(FakeSubmitter):
        def submit_once(self, submitted_job):
            assert submitted_job is job
            assert len(path.read_text(encoding="utf-8").splitlines()) == 1
            intent = json.loads(path.read_text(encoding="utf-8"))
            assert intent["event"] == "submit_intent"
            assert intent["identity"] == _identity().as_dict()
            return "821;cluster-a"

    result = submit_with_accounting(
        journal,
        FakeSlurm(),
        InspectingSubmitter(),
        job,
        identity=_identity(),
        job_key="dp-job-hash-001",
        resources=_resources(),
        task_ids=("pop-0", "pop-1"),
    )

    assert result == "821;cluster-a"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["event"] for row in rows] == ["submit_intent", "submit_result"]
    assert rows[1]["job_id"] == "821;cluster-a"
    assert rows[1]["task_mapping"] == [
        {"task_index": 0, "task_id": "pop-0"},
        {"task_index": 1, "task_id": "pop-1"},
    ]
    assert rows[0]["requested"] == {
        "cpu_per_node": 16,
        "gpu_per_node": 2,
        "nodes": 1,
        "partition": "gpu-short",
    }
    assert "do-not-record-this" not in path.read_text(encoding="utf-8")
    assert "password" not in path.read_text(encoding="utf-8")
    journal.close()


def test_retry_records_each_returned_job_and_mapping(tmp_path):
    journal = AccountingJournal(tmp_path / "accounting.jsonl")
    submitter = FakeSubmitter("821", "822")

    first = _submit(journal, submitter)
    second = _submit(journal, submitter, retry_of_job_id=first)

    assert (first, second) == ("821", "822")
    records = [
        json.loads(line)
        for line in journal.path.read_text(encoding="utf-8").splitlines()
    ]
    intents = [record for record in records if record["event"] == "submit_intent"]
    results = [record for record in records if record["event"] == "submit_result"]
    assert [row["attempt_number"] for row in intents] == [1, 2]
    assert intents[1]["retry_of_job_id"] == "821"
    assert [row["job_id"] for row in results] == ["821", "822"]
    assert all(row["task_mapping"][1]["task_id"] == "pop-1" for row in results)


def test_same_stage_jobs_have_independent_retry_chains(tmp_path):
    journal = AccountingJournal(tmp_path / "accounting.jsonl")
    submitter = FakeSubmitter("821", "822", "823", "824")
    shared_identity = _identity()

    first_a = _submit(
        journal, submitter, identity=shared_identity, job_key="job-hash-a"
    )
    first_b = _submit(
        journal, submitter, identity=shared_identity, job_key="job-hash-b"
    )
    retry_a = _submit(
        journal,
        submitter,
        identity=shared_identity,
        job_key="job-hash-a",
        retry_of_job_id=first_a,
    )
    retry_b = _submit(
        journal,
        submitter,
        identity=shared_identity,
        job_key="job-hash-b",
        retry_of_job_id=first_b,
    )

    assert (first_a, first_b, retry_a, retry_b) == ("821", "822", "823", "824")
    records = [
        json.loads(line)
        for line in journal.path.read_text(encoding="utf-8").splitlines()
    ]
    intents = [record for record in records if record["event"] == "submit_intent"]
    assert [(row["job_key"], row["attempt_number"]) for row in intents] == [
        ("job-hash-a", 1),
        ("job-hash-b", 1),
        ("job-hash-a", 2),
        ("job-hash-b", 2),
    ]
    assert [row["retry_of_job_id"] for row in intents] == [None, None, "821", "822"]


def test_retry_must_reference_latest_job_and_keep_same_mapping(tmp_path):
    journal = AccountingJournal(tmp_path / "accounting.jsonl")
    submitter = FakeSubmitter("821", "822")
    _submit(journal, submitter)

    with pytest.raises(ValueError, match="latest durably recorded"):
        _submit(journal, submitter)
    assert len(submitter.calls) == 1

    with pytest.raises(ValueError, match="latest durably recorded"):
        _submit(journal, submitter, retry_of_job_id="999")
    assert len(submitter.calls) == 1


def test_post_submit_journal_failure_never_reinvokes_submitter(tmp_path):
    class FailOnResultJournal(AccountingJournal):
        def __init__(self, path):
            super().__init__(path)
            self.append_count = 0

        def _append_unlocked(self, record):
            self.append_count += 1
            if self.append_count == 2:
                self._poisoned = True
                raise JournalError("simulated post-submit disk failure")
            return super()._append_unlocked(record)

    path = tmp_path / "accounting.jsonl"
    journal = FailOnResultJournal(path)
    submitter = FakeSubmitter("821", "822")

    with pytest.raises(PostSubmitJournalError) as exc_info:
        _submit(journal, submitter)
    assert exc_info.value.job_id == "821"
    assert len(submitter.calls) == 1
    with pytest.raises(JournalCorruptionError):
        _submit(journal, submitter)
    assert len(submitter.calls) == 1

    reopened = AccountingJournal(path)
    with pytest.raises(UnresolvedAttemptError):
        _submit(reopened, submitter, retry_of_job_id="821")
    assert len(submitter.calls) == 1


def test_submit_exception_is_redacted_and_blocks_retry(tmp_path):
    journal = AccountingJournal(tmp_path / "accounting.jsonl")
    submitter = FakeSubmitter(RuntimeError("password=hunter2; host=private.example"), "822")

    with pytest.raises(SubmissionOutcomeUnknownError):
        _submit(journal, submitter)
    journal_text = journal.path.read_text(encoding="utf-8")
    assert "hunter2" not in journal_text
    assert "private.example" not in journal_text

    with pytest.raises(UnresolvedAttemptError):
        _submit(journal, submitter)
    assert len(submitter.calls) == 1


def test_localcontext_shell_is_never_recorded_as_slurm_usage(tmp_path):
    path = tmp_path / "accounting.jsonl"
    journal = AccountingJournal(path)
    submitter = FakeShellSubmitter(4321)

    result = submit_with_accounting(
        journal,
        FakeLocalShell(),
        submitter,
        FakeJob(),
        identity=_identity(),
        job_key="local-job-1",
        resources=_resources(),
        task_ids=("pop-0",),
    )

    assert result == 4321
    assert len(submitter.calls) == 1
    assert path.read_bytes() == b""


def test_remote_shell_is_rejected_without_calling_submitter(tmp_path):
    path = tmp_path / "accounting.jsonl"
    journal = AccountingJournal(path)
    submitter = FakeShellSubmitter(4321)

    with pytest.raises(AccountingError, match="only remote Slurm"):
        submit_with_accounting(
            journal,
            FakeRemoteShell(),
            submitter,
            FakeJob(),
            identity=_identity(),
            job_key="remote-shell-job",
            resources=_resources(),
            task_ids=("pop-0",),
        )

    assert submitter.calls == []
    assert path.read_bytes() == b""


@pytest.mark.parametrize("failure_point", ["write", "fsync"])
def test_intent_write_or_fsync_failure_poisons_journal_before_submit(
    tmp_path, monkeypatch, failure_point
):
    journal = AccountingJournal(tmp_path / "accounting.jsonl")
    submitter = FakeSubmitter("821")

    def fail(*args, **kwargs):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(
        "ion_CSP.dispatch_accounting.os." + failure_point,
        fail,
    )
    with pytest.raises(JournalError):
        _submit(journal, submitter)
    assert submitter.calls == []

    monkeypatch.undo()
    with pytest.raises(JournalCorruptionError):
        _submit(journal, submitter)
    assert submitter.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner_id": "bad owner"},
        {"run_id": "../../run"},
        {"role": "host@example"},
        {"stage": ""},
    ],
)
def test_accounting_tags_are_strict(kwargs):
    values = {
        "owner_id": "owner-7",
        "run_id": "run-a",
        "role": "gpu-worker",
        "stage": "relaxation",
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        AccountingIdentity(**values)


def test_requested_resource_values_are_strict_and_sanitized():
    with pytest.raises(ValueError, match="cpu_per_node"):
        _resources(cpu_per_node=True)
    with pytest.raises(ValueError, match="partition"):
        _resources(partition="queue secret=token")
    with pytest.raises(ValueError, match="nodes"):
        _resources(nodes=0)


def _sacct_row(
    job_id,
    *,
    state="COMPLETED",
    elapsed="3600",
    cpus="8",
    nodes="1",
    tres="cpu=8,mem=32G,node=1,gres/gpu=2,gres/gpu:a100=2",
    partition="gpu-short",
):
    return "|".join(
        [
            job_id,
            "ioncsp-run-gpu-worker",
            state,
            elapsed,
            cpus,
            nodes,
            tres,
            partition,
            "2026-10-19T01:00:00",
            "2026-10-19T01:01:00",
            "2026-10-19T02:01:00",
        ]
    )


def test_sacct_parser_counts_allocation_once_and_formats_evidence():
    output = "\n".join(
        [
            _sacct_row("821;cluster-a".split(";", 1)[0]),
            "821.batch|step|COMPLETED|3600|8|1||gpu-short|||",
            "821.extern|step|COMPLETED|3600|8|1||gpu-short|||",
            "822_[1-2]|array|PENDING|0|0|0||gpu-short|||",
            _sacct_row(
                "823",
                state="FAILED+",
                elapsed="120",
                cpus="4",
                nodes="1",
                tres="cpu=4,node=1,gres/gpu=1,gres/gpu:v100=1",
            ),
        ]
    )

    allocations = parse_sacct_allocations(output, job_ids=("821;cluster-a", "823"))

    assert len(allocations) == 2
    assert allocations[0].job_id == "821;cluster-a"
    assert allocations[0].allocated_gpus == 2
    assert allocations[1].state == "FAILED"
    assert allocations[1].allocated_gpus == 1
    evidence = json.loads(format_sacct_evidence(allocations))
    assert evidence["evidence_type"] == "slurm_allocation_only"
    assert evidence["allocation_totals"] == {
        "allocated_cpu_seconds": 29_280,
        "allocated_gpu_seconds": 7_320,
    }
    assert not any("invoice" in key.lower() or "cost" in key.lower() for key in evidence)


@pytest.mark.parametrize(
    "output",
    [
        "821|too|few|columns",
        _sacct_row("821", elapsed="not-seconds"),
        _sacct_row("821", cpus="8", tres="cpu=4,node=1"),
        _sacct_row("not-a-job", tres="cpu=8,node=1"),
        _sacct_row("821", partition="private queue"),
        _sacct_row("821", tres="cpu=8,node=1,gres/gpu=1,gres/gpu=2"),
        _sacct_row("821", tres="cpu=8,node=1,gres/gpu=4,gres/gpu:a100=2"),
    ],
)
def test_sacct_parser_rejects_malformed_or_inconsistent_rows(output):
    with pytest.raises(SacctParseError):
        parse_sacct_allocations(output)


def test_sacct_parser_rejects_duplicate_allocation_ids():
    output = "\n".join([_sacct_row("821"), _sacct_row("821")])
    with pytest.raises(SacctParseError, match="duplicate allocation"):
        parse_sacct_allocations(output)


def test_sacct_parser_skips_unallocated_pending_jobs():
    pending = _sacct_row(
        "821",
        state="PENDING",
        elapsed="Unknown",
        cpus="0",
        nodes="0",
        tres="",
    )
    assert parse_sacct_allocations(pending) == []

    pending_with_zero_elapsed = _sacct_row(
        "822", state="PENDING", elapsed="0", cpus="0", nodes="0", tres=""
    )
    assert parse_sacct_allocations(pending_with_zero_elapsed) == []
    mixed = "\n".join([_sacct_row("821"), pending_with_zero_elapsed])
    assert [item.job_id for item in parse_sacct_allocations(mixed)] == ["821"]
