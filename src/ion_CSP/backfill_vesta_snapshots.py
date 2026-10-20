"""Resumable VESTA snapshot backfill for existing combo-root POSCAR files."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import psutil

from ion_CSP.vesta_snapshots import (
    _atomic_json,
    _cached_manifest,
    _digest,
    render_vesta_views,
    write_snapshot_status,
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def discover_root_poscars(results_root: Path) -> list[Path]:
    """Find only combo root POSCARs, never inner VASP stage inputs."""
    root = Path(results_root).resolve(strict=True)
    result = []
    for series in root.iterdir():
        if not series.is_dir():
            continue
        for group in series.iterdir():
            if not group.is_dir() or not group.name.startswith("2_"):
                continue
            for combo in group.iterdir():
                if not combo.is_dir() or not combo.name.startswith("combo_"):
                    continue
                number = combo.name.removeprefix("combo_")
                if number.isdecimal() and (combo / "POSCAR").is_file():
                    result.append(combo / "POSCAR")
    return sorted(result)


def active_combo_roots(results_root: Path) -> set[Path]:
    """Identify combo directories with a running main_CSP process."""
    root = Path(results_root).resolve()
    active = set()
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = process.info["cmdline"] or []
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        if "ion_CSP.run.main_CSP" not in command:
            continue
        for argument in command:
            candidate = Path(argument)
            if candidate.name.startswith("combo_") and candidate.name.removeprefix("combo_").isdecimal():
                resolved = candidate.resolve()
                if resolved.is_relative_to(root):
                    active.add(resolved)
    return active


def _render_one(poscar: Path, executable: str | None) -> dict:
    combo = poscar.parent
    output_dir = combo / "crystal_snapshots"
    status_path = output_dir / "final_snapshot_status.json"
    record = {"combo": str(combo), "poscar": str(poscar), "updated_at": _now()}
    try:
        manifest = render_vesta_views(poscar, output_dir, executable=executable)
        write_snapshot_status(status_path, poscar, status="success", manifest=manifest)
        record.update({"status": "success", "manifest": str(manifest), "error": None})
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            write_snapshot_status(status_path, poscar, status="failed", error=error)
        except Exception as status_exc:
            error += f"; status write failed: {type(status_exc).__name__}: {status_exc}"
        record.update({"status": "failed", "manifest": None, "error": error})
    return record


def run_backfill(
    results_root: Path,
    *,
    workers: int = 4,
    limit: int | None = None,
    executable: str | None = None,
) -> dict:
    if not 1 <= workers <= 4:
        raise ValueError("workers must be between 1 and 4")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    root = Path(results_root).resolve(strict=True)
    all_poscars = discover_root_poscars(root)
    selected = all_poscars[:limit] if limit is not None else all_poscars
    active = active_combo_roots(root)
    ready = [poscar for poscar in selected if poscar.parent.resolve() not in active]
    deferred = [poscar for poscar in selected if poscar.parent.resolve() in active]
    summary_path = root / "vesta_snapshot_backfill.json"
    summary = {
        "state": "RUNNING",
        "started_at": _now(),
        "updated_at": _now(),
        "results_root": str(root),
        "source_hashes_before": {str(poscar): _digest(poscar) for poscar in selected},
        "discovered_count": len(all_poscars),
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "deferred_combos": [str(poscar.parent) for poscar in deferred],
        "workers": workers,
        "success_count": 0,
        "failed_count": 0,
        "items": {},
    }
    _atomic_json(summary_path, summary)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_render_one, poscar, executable): poscar for poscar in ready}
        for future in as_completed(futures):
            record = future.result()
            summary["items"][record["combo"]] = record
            summary["success_count"] += record["status"] == "success"
            summary["failed_count"] += record["status"] == "failed"
            summary["updated_at"] = _now()
            _atomic_json(summary_path, summary)
            done = summary["success_count"] + summary["failed_count"]
            if done % 10 == 0 or record["status"] == "failed" or done == len(ready):
                print(
                    f"VESTA snapshots: {done}/{len(ready)} ready, "
                    f"{len(deferred)} deferred active; "
                    f"success={summary['success_count']}, failed={summary['failed_count']}",
                    flush=True,
                )
                if record["status"] == "failed":
                    print(f"  {record['combo']}: {record['error']}", flush=True)
    summary["state"] = (
        "DEFERRED_ACTIVE" if deferred else
        "COMPLETE_WITH_FAILURES" if summary["failed_count"] else
        "PARTIAL_LIMIT" if limit is not None and len(selected) < len(all_poscars) else
        "COMPLETE"
    )
    summary["finished_at"] = _now()
    summary["updated_at"] = summary["finished_at"]
    _atomic_json(summary_path, summary)
    return summary


def watch_deferred(results_root: Path, *, poll_seconds: int = 60) -> dict:
    """Render deferred roots after their CSP processes exit, then re-audit hashes."""
    if poll_seconds < 10:
        raise ValueError("poll_seconds must be at least 10")
    root = Path(results_root).resolve(strict=True)
    summary_path = root / "vesta_snapshot_backfill.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("state") not in {"DEFERRED_ACTIVE", "WATCHING_ACTIVE"}:
        raise RuntimeError(f"No deferred active work to watch: {summary.get('state')}")
    summary["state"] = "WATCHING_ACTIVE"
    summary["watcher_pid"] = os.getpid()
    summary["updated_at"] = _now()
    _atomic_json(summary_path, summary)
    while True:
        active = active_combo_roots(root)
        pending = {
            Path(combo) for combo in summary.get("deferred_combos", [])
        }
        pending.update(active)
        for poscar in discover_root_poscars(root):
            combo = poscar.parent
            record = summary["items"].get(str(combo))
            status_file = combo / "crystal_snapshots" / "final_snapshot_status.json"
            if record is None or not status_file.is_file():
                pending.add(combo)
                continue
            try:
                status = json.loads(status_file.read_text(encoding="utf-8"))
                source_hash = status.get("source_sha256")
                if status.get("status") == "failed" and source_hash == _digest(poscar):
                    # A deterministic failure is reported once. A changed
                    # POSCAR is a new input and remains eligible for retry.
                    continue
                if (
                    status.get("status") != "success"
                    or source_hash != _digest(poscar)
                ):
                    pending.add(combo)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pending.add(combo)
        for combo in tuple(pending):
            if combo not in active and not (combo / "POSCAR").is_file():
                summary.setdefault("skipped_no_root_poscar", []).append(str(combo))
                pending.discard(combo)
        ready = sorted(combo for combo in pending if combo not in active and (combo / "POSCAR").is_file())
        for combo in ready:
            old = summary["items"].get(str(combo))
            if old is not None:
                summary[f"{old['status']}_count"] -= 1
            record = _render_one(combo / "POSCAR", None)
            summary["items"][str(combo)] = record
            summary[f"{record['status']}_count"] += 1
            pending.discard(combo)
            print(f"Deferred VESTA snapshot {record['status']}: {combo}", flush=True)
        summary["deferred_combos"] = [str(combo) for combo in sorted(pending)]
        summary["deferred_count"] = len(pending)
        summary["discovered_count"] = len(discover_root_poscars(root))
        summary["updated_at"] = _now()
        if not pending:
            audit = audit_backfill(root)
            summary["state"] = (
                "COMPLETE_WITH_FAILURES"
                if summary["failed_count"] or audit["issue_count"] else "COMPLETE"
            )
            summary["finished_at"] = _now()
            _atomic_json(summary_path, summary)
            return summary
        _atomic_json(summary_path, summary)
        time.sleep(poll_seconds)


def launch_watcher(results_root: Path, *, poll_seconds: int = 60) -> int:
    root = Path(results_root).resolve(strict=True)
    summary_path = root / "vesta_snapshot_backfill.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    existing = int(summary.get("watcher_pid") or 0)
    if existing and psutil.pid_exists(existing):
        raise RuntimeError(f"VESTA deferred watcher is already running: {existing}")
    pending = {Path(combo) for combo in summary.get("deferred_combos", [])}
    pending.update(active_combo_roots(root))
    if not pending:
        raise RuntimeError("No deferred or active CSP combos remain")
    summary["state"] = "WATCHING_ACTIVE"
    summary["deferred_combos"] = [str(combo) for combo in sorted(pending)]
    summary["deferred_count"] = len(pending)
    summary["updated_at"] = _now()
    _atomic_json(summary_path, summary)
    log = (root / "vesta_snapshot_backfill_watcher.log").open("a", encoding="utf-8")
    command = [
        sys.executable, "-B", "-m", "ion_CSP.backfill_vesta_snapshots",
        str(root), "--watch-deferred", "--poll-seconds", str(poll_seconds),
    ]
    process = subprocess.Popen(
        command, cwd=Path(__file__).resolve().parents[2],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    log.close()
    time.sleep(1)
    if process.poll() is not None:
        raise RuntimeError("VESTA deferred watcher exited during launch; inspect watcher log")
    return process.pid


def audit_backfill(results_root: Path) -> dict:
    """Verify current POSCAR identity, three PNGs and direct-child layout."""
    from PIL import Image

    root = Path(results_root).resolve(strict=True)
    issues = []
    poscars = discover_root_poscars(root)
    summary_path = root / "vesta_snapshot_backfill.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    source_hashes_before = summary.get("source_hashes_before", {})
    changed_sources = []
    for poscar in poscars:
        combo = poscar.parent
        snapshots = combo / "crystal_snapshots"
        try:
            status = json.loads(
                (snapshots / "final_snapshot_status.json").read_text(encoding="utf-8")
            )
            if status.get("status") != "success":
                raise ValueError(f"snapshot status is {status.get('status')}")
            source_hash = _digest(poscar)
            old_hash = source_hashes_before.get(str(poscar))
            if old_hash is not None and old_hash != source_hash:
                changed_sources.append(str(poscar))
            if status.get("source_sha256") != source_hash:
                raise ValueError("snapshot status POSCAR hash differs from current input")
            manifest = Path(status["manifest"]).resolve(strict=True)
            if manifest.parent.parent != snapshots.resolve():
                raise ValueError("snapshot directory is not directly under crystal_snapshots")
            if not re.fullmatch(rf"{source_hash[:12]}-\d{{8}}T\d{{12}}", manifest.parent.name):
                raise ValueError("snapshot directory name does not match hash-timestamp")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("source_sha256") != source_hash or set(data.get("views", {})) != {"a", "b", "c"}:
                raise ValueError("manifest source hash or view set is invalid")
            if _digest(manifest.parent / "POSCAR") != source_hash:
                raise ValueError("frozen VESTA POSCAR differs from combo root")
            for axis, entry in data["views"].items():
                image_path = manifest.parent / entry["file"]
                if entry["file"] != f"{axis}.png" or _digest(image_path) != entry["sha256"]:
                    raise ValueError(f"{axis}-axis PNG hash or name is invalid")
                with Image.open(image_path) as image:
                    if min(image.size) < 200:
                        raise ValueError(f"{axis}-axis PNG is too small")
                    image.verify()
            if (snapshots / "final").exists():
                raise ValueError("obsolete final/ snapshot layer exists")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            issues.append({"combo": str(combo), "reason": f"{type(exc).__name__}: {exc}"})
    report = {
        "checked_at": _now(),
        "results_root": str(root),
        "discovered_count": len(poscars),
        "verified_count": len(poscars) - len(issues),
        "issue_count": len(issues),
        "source_changed_since_backfill_start_count": len(changed_sources),
        "source_changed_since_backfill_start": changed_sources,
        "issues": issues,
    }
    _atomic_json(root / "vesta_snapshot_audit.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_root", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--vesta-executable")
    parser.add_argument("--watch-deferred", action="store_true")
    parser.add_argument("--launch-watcher", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--audit", action="store_true")
    arguments = parser.parse_args()
    if arguments.audit:
        report = audit_backfill(arguments.results_root)
        print(json.dumps({
            "discovered": report["discovered_count"],
            "verified": report["verified_count"],
            "issues": report["issue_count"],
        }, ensure_ascii=False))
        return
    if arguments.watch_deferred:
        result = watch_deferred(arguments.results_root, poll_seconds=arguments.poll_seconds)
        print(json.dumps({"state": result["state"], "success": result["success_count"], "failed": result["failed_count"]}, ensure_ascii=False))
        return
    if arguments.launch_watcher:
        print(json.dumps({"watcher_pid": launch_watcher(arguments.results_root, poll_seconds=arguments.poll_seconds)}, ensure_ascii=False))
        return
    result = run_backfill(
        arguments.results_root,
        workers=arguments.workers,
        limit=arguments.limit,
        executable=arguments.vesta_executable,
    )
    print(json.dumps({
        "state": result["state"],
        "discovered": result["discovered_count"],
        "selected": result["selected_count"],
        "success": result["success_count"],
        "failed": result["failed_count"],
        "deferred_active": result["deferred_count"],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
