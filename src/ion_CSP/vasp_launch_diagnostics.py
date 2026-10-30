"""Opt-in, metadata-only VASP file-size-limit diagnostics.

The ``exec`` command records each MPI rank's RLIMIT_FSIZE as empty filenames
inside a fixed, stage-local directory, then replaces itself with ``vasp_std``.
The ``record`` command writes a bounded stage marker after ``mpirun`` returns.
It inspects only stat metadata for a fixed allowlist of top-level VASP outputs;
it never opens their contents. A matching output size is only a candidate for
an affected file. It cannot prove which file descriptor received SIGXFSZ,
especially if VASP removed or replaced the file before the scan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import secrets
import signal
import stat
from typing import Any


MARKER_NAME = "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
RANK_DIRECTORY = ".ion_csp_vasp_limit_ranks"
MAX_MARKER_BYTES = 16 * 1024
MAX_RANK_ENTRIES = 128
MAX_RANK_SCAN = 4096
MAX_STAGE_SCAN = 4096
STAGES = ("rough", "fine", "final")

# Only stable, public VASP output basenames are eligible for path/size fields.
OUTPUT_BASENAMES = (
    "OUTCAR",
    "vasprun.xml",
    "WAVECAR",
    "CHGCAR",
    "CHG",
    "EIGENVAL",
    "DOSCAR",
    "PROCAR",
    "REPORT",
    "OSZICAR",
    "XDATCAR",
    "IBZKPT",
    "PCDAT",
    "vasp.log",
    "CONTCAR",
)
KNOWN_INPUT_BASENAMES = frozenset({"POSCAR", "INCAR", "POTCAR", "KPOINTS"})
_RANK_NAME_RE = re.compile(
    r"^r-([0-9a-f]{32})-(u|[0-9]{1,8})-(inf|unknown|[0-9]{1,32})-"
    r"(inf|unknown|[0-9]{1,32})-([0-9a-f]{32})\.rec$"
)
_SESSION_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _dir_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)


def _nofollow_flags() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _open_rank_directory(stage_fd: int, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(RANK_DIRECTORY, 0o700, dir_fd=stage_fd)
        except FileExistsError:
            pass
    return os.open(
        RANK_DIRECTORY,
        _dir_flags() | _nofollow_flags(),
        dir_fd=stage_fd,
    )


def _limit_token(value: int) -> str:
    if value == resource.RLIM_INFINITY:
        return "inf"
    try:
        numeric_value = int(value)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    if numeric_value < 0 or len(str(numeric_value)) > 32:
        return "unknown"
    return str(numeric_value)


def _rank_token() -> str:
    for key in ("OMPI_COMM_WORLD_RANK", "PMI_RANK", "PMIX_RANK", "SLURM_PROCID"):
        value = os.environ.get(key, "")
        if re.fullmatch(r"[0-9]{1,8}", value):
            return value
    return "u"


def _record_rank_limit(token: str, limits: tuple[int, int]) -> None:
    """Store this process's exact pre-exec limits without writing file bytes.

    RLIMIT_FSIZE also applies to the diagnostics helper. The per-rank record is
    therefore encoded in a strictly generated empty filename; creating an
    empty file does not write bytes that could trigger SIGXFSZ before VASP.
    """
    soft_limit, hard_limit = limits
    rank = _rank_token()
    record_name = (
        f"r-{token}-{rank}-{_limit_token(soft_limit)}-{_limit_token(hard_limit)}-"
        f"{secrets.token_hex(16)}.rec"
    )
    stage_fd = os.open(".", _dir_flags())
    try:
        rank_fd = _open_rank_directory(stage_fd, create=True)
        try:
            file_fd = os.open(
                record_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | _nofollow_flags()
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=rank_fd,
            )
            os.close(file_fd)
        finally:
            os.close(rank_fd)
    finally:
        os.close(stage_fd)


def exec_vasp() -> int:
    """Record rank limits and replace this process with the fixed VASP command."""
    try:
        limits = resource.getrlimit(resource.RLIMIT_FSIZE)
        token = os.environ.get("ION_CSP_VASP_DIAGNOSTIC_TOKEN", "")
        if _SESSION_TOKEN_RE.fullmatch(token):
            _record_rank_limit(token, limits)
    except Exception:
        # Diagnostics must never prevent the requested scientific executable.
        pass

    # Python ignores SIGPIPE/SIGXFSZ at startup. Ignored dispositions survive
    # exec(), unlike caught handlers, and would change the native VASP failure
    # behavior under RLIMIT_FSIZE. Match subprocess restore_signals semantics.
    try:
        for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
            native_signal = getattr(signal, name, None)
            if native_signal is not None:
                signal.signal(native_signal, signal.SIG_DFL)
    except (OSError, ValueError, RuntimeError):
        try:
            os.write(2, b"ion_CSP diagnostics: unable to restore native signal behavior\n")
        except OSError:
            pass
        return 126

    try:
        os.execvp("vasp_std", ["vasp_std"])
    except OSError:
        try:
            os.write(2, b"ion_CSP diagnostics: unable to execute vasp_std\n")
        except OSError:
            pass
        return 127
    return 127  # os.execvp does not return on success.


def _decode_limit(value: str) -> int | None:
    if value in {"inf", "unknown"}:
        return None
    return int(value)


def _rank_entry_from_name(name: str, token: str) -> dict[str, Any] | None:
    match = _RANK_NAME_RE.fullmatch(name)
    if match is None:
        return None
    record_token, rank_token, soft_token, hard_token, _nonce = match.groups()
    if record_token != token:
        return None
    return {
        "rank": int(rank_token) if rank_token != "u" else None,
        "soft_bytes": _decode_limit(soft_token),
        "hard_bytes": _decode_limit(hard_token),
    }


def _collect_rank_limits(
    stage_fd: int, token: str
) -> tuple[list[dict[str, Any]], int, bool]:
    if not _SESSION_TOKEN_RE.fullmatch(token):
        return [], 0, False
    try:
        rank_fd = _open_rank_directory(stage_fd, create=False)
    except OSError:
        return [], 0, False

    try:
        rank_dir_stat = os.fstat(rank_fd)
    except OSError:
        os.close(rank_fd)
        return [], 0, False
    if (
        not stat.S_ISDIR(rank_dir_stat.st_mode)
        or rank_dir_stat.st_uid != os.geteuid()
        or rank_dir_stat.st_mode & 0o077
    ):
        os.close(rank_fd)
        return [], 0, False

    all_records: list[dict[str, Any]] = []
    scan_truncated = False
    scanned = 0
    try:
        with os.scandir(rank_fd) as entries:
            for entry in entries:
                if scanned >= MAX_RANK_SCAN:
                    scan_truncated = True
                    break
                scanned += 1
                parsed = _rank_entry_from_name(entry.name, token)
                if parsed is None:
                    continue
                try:
                    item_stat = os.stat(
                        entry.name,
                        dir_fd=rank_fd,
                        follow_symlinks=False,
                    )
                except OSError:
                    continue
                if (
                    not stat.S_ISREG(item_stat.st_mode)
                    or item_stat.st_size != 0
                    or item_stat.st_uid != os.geteuid()
                    or item_stat.st_nlink != 1
                    or item_stat.st_mode & 0o077
                ):
                    continue
                all_records.append(parsed)
                try:
                    os.unlink(entry.name, dir_fd=rank_fd)
                except OSError:
                    pass
    except OSError:
        scan_truncated = True
    finally:
        os.close(rank_fd)

    all_records.sort(
        key=lambda item: (
            item["rank"] is None,
            item["rank"] if item["rank"] is not None else 0,
            item["soft_bytes"] is None,
            item["soft_bytes"] if item["soft_bytes"] is not None else 0,
            item["hard_bytes"] is None,
            item["hard_bytes"] if item["hard_bytes"] is not None else 0,
        )
    )
    omitted_count = max(0, len(all_records) - MAX_RANK_ENTRIES)
    selected = all_records[:MAX_RANK_ENTRIES]
    try:
        os.rmdir(RANK_DIRECTORY, dir_fd=stage_fd)
    except OSError:
        pass
    return selected, omitted_count, scan_truncated


def _scan_known_outputs(stage_fd: int, soft_limits: set[int]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for basename in OUTPUT_BASENAMES:
        try:
            item_stat = os.stat(basename, dir_fd=stage_fd, follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISREG(item_stat.st_mode):
            continue
        size_bytes = int(item_stat.st_size)
        files.append(
            {
                "path": basename,
                "size_bytes": size_bytes,
                "at_limit": size_bytes > 0 and size_bytes in soft_limits,
            }
        )
    files.sort(key=lambda item: (not item["at_limit"], item["path"]))
    return files


def _count_unknown_files(stage_fd: int) -> tuple[int, bool]:
    known_names = (
        frozenset(OUTPUT_BASENAMES)
        | KNOWN_INPUT_BASENAMES
        | {MARKER_NAME, RANK_DIRECTORY}
    )
    unknown_count = 0
    scan_truncated = False
    scanned = 0
    try:
        with os.scandir(stage_fd) as entries:
            for entry in entries:
                if scanned >= MAX_STAGE_SCAN:
                    scan_truncated = True
                    break
                scanned += 1
                if entry.name in known_names:
                    continue
                try:
                    item_stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISREG(item_stat.st_mode) or stat.S_ISLNK(item_stat.st_mode):
                    unknown_count += 1
    except OSError:
        scan_truncated = True
    return unknown_count, scan_truncated


def _exit_class(exit_code: int) -> str:
    if exit_code == 0:
        return "success"
    # MPI launchers may translate rank signals into ordinary exit codes, so
    # shell-style 128+signal values remain deliberately ambiguous here.
    if exit_code >= 128:
        return "signal_or_launcher_failure"
    return "nonzero_exit"


def _write_all(file_fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(file_fd, view)
        if written <= 0:
            raise OSError("short diagnostic marker write")
        view = view[written:]


def _write_marker(stage_fd: int, payload: dict[str, Any]) -> None:
    old_limits = resource.getrlimit(resource.RLIMIT_FSIZE)
    old_soft, hard_limit = old_limits
    hard_bytes = None if hard_limit == resource.RLIM_INFINITY else int(hard_limit)
    write_budget = (
        MAX_MARKER_BYTES
        if hard_bytes is None
        else min(MAX_MARKER_BYTES, hard_bytes)
    )
    limits_changed = False
    if old_soft != resource.RLIM_INFINITY and int(old_soft) < write_budget:
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (write_budget, hard_limit))
            limits_changed = True
        except (OSError, ValueError):
            write_budget = min(write_budget, int(old_soft))
    try:
        if write_budget <= 0:
            raise ValueError("file-size limit is too small for a diagnostic marker")
        _write_marker_with_budget(stage_fd, payload, write_budget)
    finally:
        if limits_changed:
            try:
                resource.setrlimit(resource.RLIMIT_FSIZE, old_limits)
            except (OSError, ValueError):
                pass


def _write_marker_with_budget(
    stage_fd: int, payload: dict[str, Any], write_budget: int
) -> None:
    marker_bytes = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    while len(marker_bytes) > write_budget and payload["rank_limits"]:
        payload["rank_limits"].pop()
        payload["rank_limits_omitted_count"] += 1
        payload["rank_limits_status"] = "partial"
        marker_bytes = (
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("ascii")
    while len(marker_bytes) > write_budget and payload["files"]:
        payload["files"].pop()
        payload["files_omitted_count"] += 1
        marker_bytes = (
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("ascii")
    if len(marker_bytes) > write_budget:
        raise ValueError("diagnostic marker exceeds safe write budget")
    if not any(item["at_limit"] for item in payload["files"]):
        payload["candidate_status"] = "offender_unconfirmed"
        marker_bytes = (
            json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("ascii")
        if len(marker_bytes) > write_budget:
            raise ValueError("diagnostic marker exceeds safe write budget")

    temporary_name = f".{MARKER_NAME}.{secrets.token_hex(16)}.tmp"
    try:
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _nofollow_flags()
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=stage_fd,
        )
        try:
            _write_all(file_fd, marker_bytes)
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.replace(
            temporary_name,
            MARKER_NAME,
            src_dir_fd=stage_fd,
            dst_dir_fd=stage_fd,
        )
        try:
            os.fsync(stage_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary_name, dir_fd=stage_fd)
        except OSError:
            pass
        raise


def record_marker(stage: str, exit_code: int, token: str) -> None:
    if stage not in STAGES:
        raise ValueError("invalid stage")
    if not 0 <= exit_code <= 255:
        raise ValueError("invalid exit code")

    stage_fd = os.open(".", _dir_flags())
    try:
        ranks, ranks_omitted, rank_scan_truncated = _collect_rank_limits(
            stage_fd, token
        )
        finite_soft_limits = {
            int(item["soft_bytes"])
            for item in ranks
            if item["soft_bytes"] is not None and int(item["soft_bytes"]) > 0
        }
        files = _scan_known_outputs(stage_fd, finite_soft_limits)
        unknown_count, file_scan_truncated = _count_unknown_files(stage_fd)
        candidate_found = any(item["at_limit"] for item in files)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "stage": stage,
            "exit_code": exit_code,
            "exit_class": _exit_class(exit_code),
            "rank_limits": ranks,
            "rank_limits_status": (
                "unavailable"
                if not ranks
                else "partial"
                if ranks_omitted or rank_scan_truncated
                else "recorded"
            ),
            "rank_limits_omitted_count": ranks_omitted,
            "rank_limits_scan_truncated": rank_scan_truncated,
            "files": files,
            "files_omitted_count": 0,
            "unknown_file_count": unknown_count,
            "file_scan_truncated": file_scan_truncated,
            "candidate_status": "candidate_at_limit" if candidate_found else "offender_unconfirmed",
            "candidate_basis": "size_match_not_fd_proof",
        }
        _write_marker(stage_fd, payload)
    finally:
        os.close(stage_fd)


def _exit_code_argument(value: str) -> int:
    try:
        exit_code = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("exit code must be an integer") from exc
    if not 0 <= exit_code <= 255:
        raise argparse.ArgumentTypeError("exit code must be between 0 and 255")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ion_CSP.vasp_launch_diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("exec", help="record this rank's limits and exec vasp_std")
    record_parser = commands.add_parser("record", help="write the stage marker")
    record_parser.add_argument("--stage", choices=STAGES, required=True)
    record_parser.add_argument("--exit-code", type=_exit_code_argument, required=True)
    record_parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)

    if args.command == "exec":
        return exec_vasp()
    try:
        if args.token and not _SESSION_TOKEN_RE.fullmatch(args.token):
            raise ValueError("invalid session token")
        record_marker(args.stage, args.exit_code, args.token)
    except Exception:
        try:
            os.write(2, b"ion_CSP diagnostics: unable to write stage marker\n")
        except OSError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
