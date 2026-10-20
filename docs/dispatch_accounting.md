# Shared-account Slurm accounting helper

`ion_CSP.dispatch_accounting` provides an isolated, fail-closed accounting
boundary. It does not load a dpdispatcher machine file or copy machine
configuration into the journal. The caller supplies `owner_id`, `run_id`,
`role`, `stage`, a safe logical `job_key` for each allocation (such as the
dpdispatcher job hash), the four allowed requested-resource values, and task
IDs. Retry history is scoped to the full identity plus `job_key`, so distinct
jobs in one stage do not share a retry chain.

`AccountingJournal` creates a JSONL file with mode `0600`, opens it in append
mode, and fsyncs every event. `submit_with_accounting` writes and fsyncs an
intent before calling a `OneShotSubmitter.submit_once(job)` adapter. A
successful return is recorded with the exact Slurm job ID and the task IDs
mapped to that allocation. A subsequent attempt must name the latest durable
job ID in `retry_of_job_id`; the caller must first establish that the prior
job reached a terminal state. Exceptions, invalid IDs, or a failed result
write leave an unresolved intent and prevent another attempt for that
identity and logical job key. A write or fsync failure poisons the open
journal instance. The helper does not automatically retry an ambiguous
submission.

Only `LocalContext` plus `Shell` is delegated to the adapter without writing
accounting records. Its PIDs are never stored as Slurm job IDs or turned into
usage. Remote non-Slurm and unknown machine/context combinations fail closed
without calling the adapter.

## Current dpdispatcher integration limit

The installed dpdispatcher 1.1.0 call chain invokes `machine.do_submit(job)`
from `Job.submit_job()` in `dpdispatcher/submission.py` (the `machine` call is
near line 917). However, `dpdispatcher.machines.slurm.Slurm.do_submit` is
decorated with an internal `@retry()` that repeats on `RetrySignal`. Its
caller sees only the eventual return value. If `sbatch` accepted a job but the
response timed out, the retry can create a duplicate, and an outer wrapper
cannot write an intent before each hidden call or record IDs from calls whose
responses were lost.

For that reason, the current Slurm method must not be passed through a thin
adapter and treated as one-shot. A future integration needs a version-gated
or forked Slurm adapter exposing exactly one `sbatch --parsable` attempt, then
must place the accounting call at each `Job.submit_job()` submission boundary
before invoking that adapter. Retry policy must sit outside this boundary so
each explicit attempt has its own intent and returned ID. A timeout or other
ambiguous result must remain unresolved until scheduler state is reconciled;
it must not trigger a transparent resubmit.

## Allocation evidence

`parse_sacct_allocations` is a pure parser for
`sacct --parsable2 --noheader` output in this column order:

```text
JobIDRaw,JobName,State,ElapsedRaw,AllocCPUS,NNodes,AllocTRES,Partition,Submit,Start,End
```

It keeps only allocation rows, excludes `.batch` / `.extern` steps and array
umbrella rows, and optionally filters to the exact job IDs in the journal.
`format_sacct_evidence` emits allocation-only JSON evidence with allocated
CPU/GPU seconds where available. It contains no currency or price fields and
is not a provider invoice; invoices must be stored and reconciled separately.
