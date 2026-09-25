"""Create and verify an auditable ion_CSP release manifest."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SOURCE_ROOTS = (
    "src/ion_CSP",
    "scripts",
    "delivery",
)
ROOT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "environment.yml",
    "environment-mlp.yml",
    "MANIFEST.in",
    "README.md",
    "docs/delivery.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _source_paths(root: Path, manifest_path: Path) -> list[Path]:
    paths: set[Path] = set()
    for relative in ROOT_FILES:
        candidate = root / relative
        if candidate.is_file():
            paths.add(candidate)
    for relative in SOURCE_ROOTS:
        directory = root / relative
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*"):
            if not candidate.is_file() or candidate == manifest_path:
                continue
            if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".pyo", ".orig"}:
                continue
            paths.add(candidate)
    return sorted(paths, key=lambda item: _relative(item, root))


def _source_inventory(root: Path, manifest_path: Path) -> tuple[list[dict[str, Any]], str]:
    inventory: list[dict[str, Any]] = []
    aggregate = hashlib.sha256()
    for path in _source_paths(root, manifest_path):
        relative = _relative(path, root)
        digest = sha256_file(path)
        size = path.stat().st_size
        inventory.append({"path": relative, "sha256": digest, "size_bytes": size})
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\0")
    return inventory, aggregate.hexdigest()


def _parse_python_sources(root: Path) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    files = sorted((root / "src" / "ion_CSP").rglob("*.py"))
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError) as error:
            failures.append({"path": _relative(path, root), "error": str(error)})
    return {
        "parser": "python.ast.parse",
        "status": "pass" if not failures else "fail",
        "file_count": len(files),
        "failures": failures,
    }


def _run_git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _git_revision(root: Path, content_sha256: str) -> dict[str, Any]:
    head = _run_git(root, "rev-parse", "HEAD")
    # The sidecar manifest is committed after the source snapshot. Exclude it
    # from the source revision's dirty check, as it is excluded from the hash.
    porcelain = _run_git(
        root,
        "status",
        "--short",
        "--untracked-files=all",
        "--",
        *SOURCE_ROOTS,
        *ROOT_FILES,
        ":(exclude)delivery/release-manifest.json",
        ":(exclude)src/ion_CSP/**/*.orig",
    )
    dirty_entries = [] if not porcelain else porcelain.splitlines()
    return {
        "git_head": head,
        "dirty": bool(dirty_entries),
        "dirty_entries": dirty_entries,
        "content_sha256": content_sha256,
    }


def _project_metadata(root: Path) -> tuple[str, str]:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    return str(project["name"]), str(project["version"])


def _core_profile(root: Path) -> dict[str, Any]:
    uv_path = root / "uv.lock"
    uv_data = tomllib.loads(uv_path.read_text(encoding="utf-8"))
    packages = uv_data.get("package", [])
    names = sorted(str(item.get("name")) for item in packages if item.get("name"))
    core_index = root / "delivery" / "locks" / "core.lock.json"
    return {
        "role": "controller and workflow parser",
        "python": "3.11.x",
        "authoritative_lock": _relative(uv_path, root),
        "lock_sha256": sha256_file(uv_path),
        "lock_index": _relative(core_index, root),
        "lock_index_sha256": sha256_file(core_index),
        "resolved_package_count": len(packages),
        "resolved_packages": names,
        "gpu_stack_excluded": not ({"torch", "deepmd-kit"} & set(names)),
        "conda_bootstrap": "environment.yml",
    }


def _mlp_profile(root: Path) -> dict[str, Any]:
    lock_path = root / "delivery" / "locks" / "mlp-deepmd-torch.lock.toml"
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    requirements = root / "delivery" / "locks" / "mlp-requirements.txt"
    model = root / "src" / "ion_CSP" / "model" / "model.pt"
    profile = {
        "role": "MLP structure optimization worker",
        "authoritative_lock": _relative(lock_path, root),
        "lock_sha256": sha256_file(lock_path),
        "requirements": _relative(requirements, root),
        "requirements_sha256": sha256_file(requirements),
        "pairing": lock.get("pairing", {}),
        "cuda_boundary": lock.get("cuda", {}),
        "receipt_required": True,
    }
    if model.is_file():
        profile["bundled_model"] = {
            "path": _relative(model, root),
            "sha256": sha256_file(model),
            "size_bytes": model.stat().st_size,
        }
    return profile


def _external_profile(root: Path) -> dict[str, Any]:
    path = root / "delivery" / "external-software.json"
    inventory = json.loads(path.read_text(encoding="utf-8"))
    return {
        "role": "licensed/external scientific software workers",
        "inventory": _relative(path, root),
        "inventory_sha256": sha256_file(path),
        "software": inventory.get("software", []),
        "receipt_required": True,
    }


def _artifact_inventory(root: Path, artifact_dir: Path) -> list[dict[str, Any]]:
    candidates = sorted(
        path
        for path in artifact_dir.rglob("*")
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    artifacts = []
    for path in candidates:
        role = "wheel" if path.suffix == ".whl" else "sdist"
        artifacts.append(
            {
                "path": _relative(path, root),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "role": role,
                "required": True,
            }
        )
    return artifacts


def create_manifest(
    root: Path,
    artifact_dir: Path,
    output: Path,
    *,
    require_artifacts: bool = True,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    output = output.expanduser().resolve()
    inventory, content_sha256 = _source_inventory(root, output)
    artifacts = _artifact_inventory(root, artifact_dir.expanduser().resolve())
    roles = {item["role"] for item in artifacts}
    if require_artifacts and roles != {"wheel", "sdist"}:
        raise ValueError("artifact directory must contain one wheel and one sdist")
    project, version = _project_metadata(root)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "version": version,
        "source_revision": _git_revision(root, content_sha256),
        "profiles": {
            "core": _core_profile(root),
            "mlp": _mlp_profile(root),
            "external": _external_profile(root),
        },
        "artifacts": artifacts,
        "source_validation": {
            "inventory": inventory,
            "python_parse": _parse_python_sources(root),
        },
        "config_contract": {
            "path": "delivery/config-contract.schema.json",
            "sha256": sha256_file(root / "delivery" / "config-contract.schema.json"),
        },
        "endpoint_receipts": {
            "required": ["core", "mlp", "external"],
            "command": "python scripts/release_preflight.py --target <core|mlp|external> --json-output <receipt.json>",
            "captured": [],
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": {
            "tool": "ion_CSP.release_manifest",
            "schema_version": SCHEMA_VERSION,
            "python": sys.version.split()[0],
        },
    }
    if manifest["source_validation"]["python_parse"]["status"] != "pass":
        raise ValueError("Python source parsing failed; refusing to create release manifest")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _verify_entry(root: Path, entry: dict[str, Any], errors: list[str]) -> None:
    path = Path(str(entry["path"]))
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        errors.append(f"missing file: {entry['path']}")
        return
    actual = sha256_file(path)
    if actual != entry["sha256"]:
        errors.append(f"sha256 mismatch: {entry['path']}")
    if path.stat().st_size != entry["size_bytes"]:
        errors.append(f"size mismatch: {entry['path']}")


def verify_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest_path = manifest_path.expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for key in (
        "schema_version",
        "project",
        "version",
        "source_revision",
        "profiles",
        "artifacts",
        "generated_by",
    ):
        if key not in manifest:
            errors.append(f"missing manifest field: {key}")
    for entry in manifest.get("artifacts", []):
        _verify_entry(root, entry, errors)
    for entry in manifest.get("source_validation", {}).get("inventory", []):
        _verify_entry(root, entry, errors)
    inventory = manifest.get("source_validation", {}).get("inventory", [])
    recorded_paths = {entry["path"] for entry in inventory}
    current_paths = {
        _relative(path, root) for path in _source_paths(root, manifest_path)
    }
    if current_paths != recorded_paths:
        errors.append(
            "source path inventory mismatch: "
            f"added={sorted(current_paths - recorded_paths)}; "
            f"removed={sorted(recorded_paths - current_paths)}"
        )
    aggregate = hashlib.sha256()
    for entry in inventory:
        aggregate.update(str(entry["path"]).encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(str(entry["sha256"]).encode("ascii"))
        aggregate.update(b"\0")
    expected = manifest.get("source_revision", {}).get("content_sha256")
    if aggregate.hexdigest() != expected:
        errors.append("source content aggregate mismatch")
    parse_result = _parse_python_sources(root)
    if parse_result["status"] != "pass":
        errors.append("current Python source parsing failed")
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest": str(manifest_path),
        "ok": not errors,
        "errors": errors,
        "checked_artifacts": len(manifest.get("artifacts", [])),
        "checked_sources": len(inventory),
    }


def _write_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create or verify an ion_CSP release manifest")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--project-root", type=Path, default=Path.cwd())
    create.add_argument("--artifact-dir", type=Path, required=True)
    create.add_argument(
        "--output",
        type=Path,
        default=Path("delivery/release-manifest.json"),
    )
    create.add_argument("--allow-no-artifacts", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--project-root", type=Path, default=Path.cwd())
    verify.add_argument(
        "--manifest",
        type=Path,
        default=Path("delivery/release-manifest.json"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "create":
        try:
            manifest = create_manifest(
                args.project_root,
                args.artifact_dir,
                args.output,
                require_artifacts=not args.allow_no_artifacts,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            _write_json({"schema_version": SCHEMA_VERSION, "ok": False, "error": str(error)})
            return 1
        _write_json(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "output": str(args.output),
                "artifacts": manifest["artifacts"],
            }
        )
        return 0
    try:
        result = verify_manifest(args.project_root, args.manifest)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        result = {"schema_version": SCHEMA_VERSION, "ok": False, "errors": [str(error)]}
    _write_json(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
