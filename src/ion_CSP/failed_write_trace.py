"""Offline candidate supervisor and bounded parser for failed VASP writes.

This module is deliberately not wired into a launcher.  Callers must gate it
to the reviewed local context.  Raw strace output is private scratch and must
be removed by the scope-external worker after the task scope is empty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


STRACE = "/usr/bin/strace"
RAW_TRACE_NAME = ".ion_csp_failed_write.strace"
SUMMARY_NAME = "ION_CSP_VASP_FAILED_WRITE_TRACE.json"
RANK_DIRECTORY = ".ion_csp_vasp_limit_ranks"
MAX_RAW_BYTES = 8 * 1024 * 1024  # hard parser cap; soft supervisor polling threshold
WATCH_INTERVAL_SECONDS = 0.1
MAX_SUMMARY_BYTES = 16 * 1024
MAX_TRACE_LINE_BYTES = 16 * 1024
MAX_EVENTS = 128
MAX_RANK_SCAN = 128
MAX_PATH_CHARS = 1024
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_RANK_RE = re.compile(
    r"^r-([0-9a-f]{32})-(0|[1-3])-p([1-9][0-9]{0,9})-"
    r"(inf|unknown|[0-9]{1,32})-(inf|unknown|[0-9]{1,32})-"
    r"([0-9a-f]{32})\.rec$"
)
_PREFIX_RE = re.compile(
    r"^(?:(?P<pid>[1-9][0-9]{0,9})\s+)?"
    r"(?P<time>[0-9]{1,12}(?:\.[0-9]{1,9})?)\s+(?P<body>.*)$"
)
_SYS_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*)\((?P<args>.*)\)\s+=\s+(?P<result>.*)$")
_ERRNO_RE = re.compile(r"^-1\s+(?P<errno>[A-Z0-9]+)(?:\s|$)")
_TARGET_SYSCALLS = frozenset(
    {"write", "writev", "pwrite64", "pwritev", "pwritev2", "ftruncate", "truncate", "fallocate"}
)


def _safe_path(raw: str, stage_dir: Path) -> tuple[str | None, str]:
    """Return a bounded stage-relative path; never expose outside paths."""
    raw = raw.replace("\\x00", "")
    raw = "".join(ch if ch >= " " and ch != "\x7f" else "?" for ch in raw)
    if len(raw) > MAX_PATH_CHARS:
        return None, "path_overlong"
    deleted = raw.endswith(" (deleted)")
    if deleted:
        raw = raw[: -len(" (deleted)")]
    try:
        path = Path(raw)
        root = stage_dir.resolve(strict=True)
        candidate = path if path.is_absolute() else root / path
        resolved_parent = candidate.parent.resolve(strict=False)
        resolved = resolved_parent / candidate.name
        rel = resolved.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return None, "path_outside_stage"
    value = rel.as_posix()
    if not value or value == "." or value.startswith("../"):
        return None, "path_outside_stage"
    return value + (" (deleted)" if deleted else ""), "stage_relative"


def rank_pid_mapping(stage_dir: os.PathLike[str] | str, token: str) -> tuple[dict[int, int], str]:
    """Read exact new-format, empty rank records without consuming them."""
    if not _TOKEN_RE.fullmatch(token):
        return {}, "invalid_token"
    rank_dir = Path(stage_dir) / RANK_DIRECTORY
    try:
        directory_fd = os.open(
            rank_dir, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
    except OSError:
        return {}, "rank_records_unavailable"
    try:
        dst = os.fstat(directory_fd)
        if not stat.S_ISDIR(dst.st_mode) or dst.st_uid != os.geteuid() or dst.st_mode & 0o077:
            return {}, "invalid_rank_directory"
        mapping: dict[int, int] = {}
        seen_ranks: set[int] = set()
        seen_pids: set[int] = set()
        with os.scandir(directory_fd) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_RANK_SCAN:
                    return {}, "rank_scan_overflow"
                match = _RANK_RE.fullmatch(entry.name)
                if match is None or match.group(1) != token:
                    continue
                rank, pid = int(match.group(2)), int(match.group(3))
                try:
                    item = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    return {}, "rank_record_changed"
                if (not stat.S_ISREG(item.st_mode) or item.st_size != 0 or item.st_uid != os.geteuid()
                        or item.st_nlink != 1 or item.st_mode & 0o077):
                    return {}, "invalid_rank_record"
                if rank in seen_ranks or pid in seen_pids:
                    return {}, "ambiguous_rank_mapping"
                mapping[pid] = rank
                seen_ranks.add(rank)
                seen_pids.add(pid)
        if seen_ranks != {0, 1, 2, 3}:
            return mapping, "incomplete_rank_mapping"
        return mapping, "complete"
    finally:
        os.close(directory_fd)


def _first_fd_path(args: str) -> str | None:
    # strace -y renders the first descriptor as N<path>; accept only that form.
    match = re.match(r"\s*[0-9]{1,10}<(?P<path>[^>]*)>(?P<deleted>\(deleted\))?", args)
    if not match:
        return None
    return match.group("path") + (" (deleted)" if match.group("deleted") else "")


def _argument_list(args: str) -> list[str]:
    """Split syscall arguments without splitting nested structures/strings."""
    result: list[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(args):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            result.append(args[start:index].strip())
            start = index + 1
    result.append(args[start:].strip())
    return result


def _requested_bytes(name: str, args: str) -> int | None:
    fields = _argument_list(args)
    indexes = {
        "write": 2, "pwrite64": 2,
        "ftruncate": 1, "truncate": 1, "fallocate": 3,
    }
    index = indexes.get(name)
    if index is None or index >= len(fields):
        return None
    value = fields[index]
    if not re.fullmatch(r"[0-9]{1,20}", value):
        return None
    parsed = int(value)
    return parsed if parsed <= (1 << 63) - 1 else None


def parse_trace(
    source: bytes | str | os.PathLike[str],
    *,
    stage_dir: os.PathLike[str] | str,
    pid_to_rank: dict[int, int] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """Parse synthetic or private strace data into a bounded evidence summary.

    `source` may be bytes/text for tests or a raw trace path.  Input reads are
    capped at MAX_RAW_BYTES+1; no trace text is copied to returned fields.
    """
    if isinstance(source, os.PathLike):
        try:
            source_fd = os.open(
                source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            try:
                item = os.fstat(source_fd)
                if (not stat.S_ISREG(item.st_mode) or item.st_uid != os.geteuid()
                        or item.st_nlink != 1 or item.st_mode & 0o077):
                    raise OSError("trace source is not a private regular file")
                data = os.pread(source_fd, MAX_RAW_BYTES + 1, 0)
            finally:
                os.close(source_fd)
        except OSError:
            data = b""
            truncated = True
    elif isinstance(source, str):
        data = source.encode("utf-8", "replace")[: MAX_RAW_BYTES + 1]
    else:
        data = source[: MAX_RAW_BYTES + 1]
    if len(data) > MAX_RAW_BYTES:
        data = data[:MAX_RAW_BYTES]
        truncated = True
    if data and not data.endswith(b"\n"):
        truncated = True
    mapping = pid_to_rank or {}
    # The trace can contain many routine ENOSPC/EIO failures before the
    # EFBIG/SIGXFSZ evidence of interest.  Count every recognized failure, but
    # retain only bounded EFBIG candidates and SIGXFSZ PIDs.
    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    candidate_duplicates: dict[tuple[Any, ...], int] = {}
    signal_pids: set[int] = set()
    failed_writes_observed = 0
    efbig_observed = 0
    sigxfsz_observed = 0
    efbig_omitted = 0
    signal_omitted = 0
    efbig_deduplicated = 0
    signal_deduplicated = 0
    overlong = False
    malformed = False
    relevant_overflow = False
    # splitlines() also treats several non-newline bytes as line boundaries.
    # strace emits LF records, so only complete LF-terminated records count.
    start = 0
    while True:
        end = data.find(b"\n", start)
        if end < 0:
            break
        raw_line = data[start:end]
        if raw_line.endswith(b"\r"):
            raw_line = raw_line[:-1]
        start = end + 1
        if len(raw_line) > MAX_TRACE_LINE_BYTES:
            overlong = True
            continue
        line = raw_line.decode("utf-8", "replace")
        prefix = _PREFIX_RE.match(line)
        if not prefix:
            # An unrecognized line can hide a relevant failure.  Never certify
            # an earlier FD after losing even one trace record.
            malformed = True
            continue
        pid = int(prefix.group("pid")) if prefix.group("pid") else None
        timestamp = prefix.group("time")
        body = prefix.group("body")
        if body.startswith("--- SIGXFSZ"):
            sigxfsz_observed += 1
            if pid is None:
                signal_omitted += 1
                malformed = True
            elif pid in signal_pids:
                signal_deduplicated += 1
            elif len(signal_pids) < MAX_EVENTS:
                signal_pids.add(pid)
            else:
                signal_omitted += 1
                relevant_overflow = True
            continue
        parsed = _SYS_RE.match(body)
        if not parsed or parsed.group("name") not in _TARGET_SYSCALLS:
            if "<unfinished ...>" in body:
                malformed = True
            continue
        errno_match = _ERRNO_RE.match(parsed.group("result"))
        if not errno_match:
            continue
        name, args, err = parsed.group("name"), parsed.group("args"), errno_match.group("errno")
        failed_writes_observed += 1
        if pid is None:
            malformed = True
            if err == "EFBIG":
                efbig_observed += 1
                efbig_omitted += 1
            continue
        if err != "EFBIG":
            continue
        efbig_observed += 1
        fd_path = _first_fd_path(args) if name not in {"truncate"} else None
        safe_path, path_state = _safe_path(fd_path, Path(stage_dir)) if fd_path is not None else (None, "fd_path_unavailable")
        fd_match = re.match(r"\s*([0-9]{1,10})(?:<|,)", args)
        fd = int(fd_match.group(1)) if fd_match else None
        row = {
            "kind": "failed_write", "pid": pid, "timestamp": timestamp,
            "syscall": name, "errno": err,
            "fd": fd,
            "requested_bytes": _requested_bytes(name, args),
            "requested_units": "iovecs" if name in {"writev", "pwritev", "pwritev2"} else "bytes",
            "path": safe_path, "path_state": path_state,
            "size_state": "size_unavailable",
        }
        # Keep paths outside the stage private, but distinguish them internally
        # so two different unsafe paths cannot collapse into one candidate.
        path_fingerprint = (
            hashlib.sha256(fd_path.encode("utf-8", "replace")).digest()
            if fd_path is not None and path_state != "stage_relative" else None
        )
        key = (pid, name, fd, safe_path, path_state, path_fingerprint)
        if key in candidates:
            candidate_duplicates[key] += 1
            efbig_deduplicated += 1
        elif len(candidates) < MAX_EVENTS:
            candidates[key] = row
            candidate_duplicates[key] = 0
        else:
            efbig_omitted += 1
            relevant_overflow = True

    candidate_rows: list[dict[str, Any]] = []
    candidate_keys = list(candidates)
    for event in candidates.values():
        row = dict(event)
        row["sigxfsz_seen"] = row["pid"] in signal_pids
        rank = mapping.get(row["pid"])
        if rank is not None:
            row["rank"] = rank
        candidate_rows.append(row)
    signal_rows = sorted(signal_pids)
    # A second EFBIG is relevant even when it has no safe path or matching
    # signal.  Ignoring it could falsely certify a different file descriptor.
    mapping_valid = set(mapping.values()) == {0, 1, 2, 3} and len(mapping) == 4
    confirmed = (
        len(candidate_rows) == 1
        and candidate_rows[0]["sigxfsz_seen"]
        and candidate_rows[0].get("rank") in {0, 1, 2, 3}
        and candidate_rows[0]["path"] is not None
        and candidate_rows[0]["path_state"] == "stage_relative"
    )
    events_observed = efbig_observed + sigxfsz_observed
    events_retained = len(candidate_rows) + len(signal_rows)
    events_omitted = efbig_omitted + signal_omitted
    events_deduplicated = efbig_deduplicated + signal_deduplicated
    parse_ambiguous = bool(
        truncated or overlong or malformed or relevant_overflow or events_omitted
        or len(candidate_rows) > 1
    )
    if truncated or overlong or malformed or relevant_overflow or events_omitted:
        status = "offender_unconfirmed"
    elif mapping_valid and confirmed:
        status = "failed_fd_path_confirmed"
    else:
        status = "offender_unconfirmed"
    summary: dict[str, Any] = {
        "schema": "ion-csp-failed-write-trace-v1",
        "status": status,
        "trace_truncated": bool(truncated),
        "trace_overlong_line": bool(overlong),
        "trace_parse_ambiguous": parse_ambiguous,
        "trace_events_overflow": bool(relevant_overflow),
        "trace_events_observed": events_observed,
        "trace_events_retained": events_retained,
        "trace_events_omitted": bool(events_omitted),
        "trace_events_omitted_count": events_omitted,
        "trace_events_deduplicated": events_deduplicated,
        "trace_failed_writes_observed": failed_writes_observed,
        "trace_efbig_observed": efbig_observed,
        "trace_sigxfsz_observed": sigxfsz_observed,
        "rank_mapping": "complete" if mapping_valid else "incomplete_or_invalid",
        "events": candidate_rows,
        "signal_pids": signal_rows,
        "event_count": events_retained,
    }
    encoded = json.dumps(summary, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    # Keep the most useful bounded candidate rows while maintaining exact
    # observed/retained/omitted accounting if long sanitized paths fill the
    # public summary budget.
    while len(encoded) > MAX_SUMMARY_BYTES and summary["events"]:
        removed_key = candidate_keys[len(summary["events"]) - 1]
        removed = summary["events"].pop()
        duplicates = candidate_duplicates[removed_key]
        efbig_deduplicated -= duplicates
        efbig_omitted += 1 + duplicates
        events_retained -= 1
        events_omitted += 1 + duplicates
        events_deduplicated -= duplicates
        summary["trace_events_retained"] = events_retained
        summary["trace_events_omitted"] = bool(events_omitted)
        summary["trace_events_omitted_count"] = events_omitted
        summary["trace_events_deduplicated"] = events_deduplicated
        summary["event_count"] = events_retained
        summary["trace_parse_ambiguous"] = True
        summary["trace_events_overflow"] = True
        summary["status"] = "offender_unconfirmed"
        summary["summary_truncated"] = True
        encoded = json.dumps(summary, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(encoded) > MAX_SUMMARY_BYTES:
        # The fixed metadata and capped PID list should fit.  If a future field
        # unexpectedly breaks that invariant, fail closed with a compact row.
        summary = {
            "schema": "ion-csp-failed-write-trace-v1",
            "status": "offender_unconfirmed",
            "trace_truncated": bool(truncated),
            "trace_overlong_line": bool(overlong),
            "trace_parse_ambiguous": True,
            "trace_events_overflow": True,
            "trace_events_observed": events_observed,
            "trace_events_retained": 0,
            "trace_events_omitted": bool(events_observed),
            "trace_events_omitted_count": events_observed,
            "trace_events_deduplicated": 0,
            "trace_failed_writes_observed": failed_writes_observed,
            "trace_efbig_observed": efbig_observed,
            "trace_sigxfsz_observed": sigxfsz_observed,
            "rank_mapping": "complete" if mapping_valid else "incomplete_or_invalid",
            "events": [],
            "signal_pids": [],
            "event_count": 0,
            "summary_truncated": True,
        }
        encoded = json.dumps(summary, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return summary


def encode_summary(summary: dict[str, Any]) -> bytes:
    """Serialize only the bounded public summary (never raw strace text)."""
    payload = json.dumps(summary, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(payload) > MAX_SUMMARY_BYTES:
        raise ValueError("summary exceeds 16 KiB")
    return payload


def _write_summary_marker(stage: Path, summary: dict[str, Any]) -> None:
    """Create the fixed summary marker without following or replacing files."""
    marker_fd = os.open(
        stage / SUMMARY_NAME,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        payload = encode_summary(summary)
        view = memoryview(payload)
        while view:
            written = os.write(marker_fd, view)
            if written <= 0:
                raise OSError("short write while creating failed-write summary")
            view = view[written:]
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)


def _terminate_group(proc: subprocess.Popen[Any]) -> None:
    """Bounded TERM/KILL for the private tracer session, including children."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass
    # The leader may have exited while Hydra/ranks remain in the group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=2.0)


def _tracer_has_raw_fd(pid: int, expected: os.stat_result) -> bool:
    """Witness the tracer writing the exclusive inode, not a replaced path."""
    try:
        with os.scandir(f"/proc/{pid}/fd") as entries:
            for index, entry in enumerate(entries):
                if index >= 256:
                    return False
                try:
                    item = os.stat(entry.path)
                except OSError:
                    continue
                if (item.st_dev, item.st_ino) == (expected.st_dev, expected.st_ino):
                    return True
    except OSError:
        return False
    return False


def _raw_identity_ok(raw_path: Path, expected: os.stat_result) -> tuple[bool, int]:
    try:
        item = raw_path.lstat()
    except OSError:
        return False, 0
    return (
        stat.S_ISREG(item.st_mode) and item.st_uid == os.geteuid()
        and item.st_nlink == 1 and not item.st_mode & 0o077
        and (item.st_dev, item.st_ino) == (expected.st_dev, expected.st_ino),
        item.st_size,
    )


def supervise(
    argv: Sequence[str],
    *,
    stage_dir: os.PathLike[str] | str,
    token: str,
    watch_interval: float = WATCH_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Run fixed strace around argv and return bounded metadata.

    This is an offline candidate only.  The caller remains responsible for
    task-scope limits, preserving mpirun status, and scope-external raw cleanup.
    """
    if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
        raise ValueError("argv must be a non-empty sequence of NUL-free strings")
    if not math.isfinite(watch_interval) or not 0.01 <= watch_interval <= 1.0:
        raise ValueError("invalid watch interval")
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid diagnostic token")
    stage = Path(stage_dir)
    stage_stat = stage.lstat()
    if (not stat.S_ISDIR(stage_stat.st_mode) or stage_stat.st_uid != os.geteuid()
            or stage_stat.st_mode & 0o077):
        raise OSError("stage directory is not a private task-owned directory")
    raw_path = stage / RAW_TRACE_NAME
    raw_fd = os.open(raw_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    initial = os.fstat(raw_fd)
    if not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.geteuid() or initial.st_nlink != 1:
        os.close(raw_fd)
        raise OSError("raw trace file failed inode checks")
    command = [STRACE, "--kill-on-exit", "-I", "1", "--seccomp-bpf", "-f", "-y", "-ttt", "-s", "0",
               "-o", str(raw_path), "-e", "trace=write,writev,pwrite64,pwritev,pwritev2,ftruncate,truncate,fallocate",
               "-e", "signal=SIGXFSZ", "-e", "status=failed,unfinished", "--", *argv]
    overflow = False
    path_changed = False
    tracer_fd_witnessed = False
    proc: subprocess.Popen[Any] | None = None
    try:
        # The fixed exec-only shell keeps every argument separate (no eval or
        # interpolation). On this host, direct Python -> strace launch loses
        # `-y` FD targets; the extra exec preserves the native trace output.
        proc = subprocess.Popen(
            ["/bin/sh", "-c", 'exec "$@"', "ion-csp-strace", *command],
            cwd=stage, start_new_session=True, close_fds=True,
        )
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        def _cancel(_signum: int, _frame: Any) -> None:
            raise InterruptedError("failed-write tracer cancelled")
        signal.signal(signal.SIGTERM, _cancel)
        try:
            while proc.poll() is None:
                tracer_fd_witnessed |= _tracer_has_raw_fd(proc.pid, initial)
                valid, size = _raw_identity_ok(raw_path, initial)
                if not valid:
                    path_changed = True
                    break
                if size > MAX_RAW_BYTES:
                    overflow = True
                    break
                time.sleep(watch_interval)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
        if overflow or path_changed:
            _terminate_group(proc)
        returncode = proc.wait(timeout=2.0)
        valid, size = _raw_identity_ok(raw_path, initial)
        if not valid:
            path_changed = True
        if size > MAX_RAW_BYTES:
            overflow = True
        rank_map, rank_state = rank_pid_mapping(stage, token)
        if path_changed:
            parsed = parse_trace(b"", stage_dir=stage, pid_to_rank=rank_map, truncated=True)
        else:
            data = os.pread(raw_fd, MAX_RAW_BYTES + 1, 0)
            parsed = parse_trace(data, stage_dir=stage, pid_to_rank=rank_map,
                                 truncated=overflow)
        if not tracer_fd_witnessed:
            parsed["status"] = "offender_unconfirmed"
        parsed["trace_identity_unwitnessed"] = not tracer_fd_witnessed
        parsed["rank_mapping"] = "complete" if rank_state == "complete" else rank_state
        parsed["trace_overflow"] = overflow
        parsed["trace_path_changed"] = path_changed
        parsed["trace_fd_witnessed"] = tracer_fd_witnessed
        parsed["raw_trace_cleanup_required"] = True
        parsed["strace_returncode"] = returncode
        encode_summary(parsed)
        _write_summary_marker(stage, parsed)
        return parsed
    except BaseException:
        if proc is not None:
            _terminate_group(proc)
        raise
    finally:
        os.close(raw_fd)


def main(argv: Sequence[str] | None = None) -> int:
    """Fixed local-only launcher; no caller-supplied scientific command."""
    parser = argparse.ArgumentParser(description="Internal local VASP failed-write diagnostic")
    parser.add_argument("run", choices=("run",))
    parser.add_argument("--token", required=True)
    parser.add_argument("--ranks", type=int, required=True)
    args = parser.parse_args(argv)
    expected_hash = os.environ.get("ION_CSP_STRACE_SHA256", "")
    if (args.ranks != 4 or not _TOKEN_RE.fullmatch(args.token)
            or os.environ.get("ION_CSP_CAPTURE_VASP_DIAGNOSTICS") != "1"
            or os.environ.get("ION_CSP_CAPTURE_FAILED_WRITE_TRACE") != "1"
            or os.environ.get("ION_CSP_VASP_MAX_ATTEMPTS") != "1"
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
        print("ion_CSP: failed-write diagnostic launch gate denied", file=os.sys.stderr)
        return 125
    try:
        with open(STRACE, "rb") as binary:
            actual_hash = hashlib.file_digest(binary, "sha256").hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("strace binary hash mismatch")
        command = ["mpirun", "-n", "4", "python", "-m",
                   "ion_CSP.vasp_launch_diagnostics", "exec"]
        summary = supervise(command, stage_dir=".", token=args.token)
    except (OSError, ValueError, subprocess.SubprocessError, InterruptedError):
        # Never print paths, raw trace lines, or exception text to vasp.log.
        print("ion_CSP: failed-write diagnostic unavailable", file=os.sys.stderr)
        return 125
    result = summary["strace_returncode"]
    return 128 - result if result < 0 else result


if __name__ == "__main__":
    raise SystemExit(main())
