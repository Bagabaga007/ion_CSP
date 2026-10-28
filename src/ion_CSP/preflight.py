"""Deployment preflight checks for the three ion_CSP execution endpoints.

The scientific workflow is intentionally split across a controller, an MLP
worker, and licensed/external quantum-chemistry workers.  This module provides
one JSON-producing checker that is installed with the package and can be run
on each endpoint independently.  It never submits a job or executes a licensed
scientific program.  Ordinary checks are visibility checks; an explicit
``--probe-poscar`` on an allocated compute node also exercises the MLP model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    import yaml
except ImportError:  # Keep the checker JSON-capable on an unprovisioned core.
    yaml = None


SCHEMA_VERSION = 1
MAX_MODEL_BYTES = 512 * 1024 * 1024
MODEL_HASH_CHUNK_BYTES = 1024 * 1024
NVIDIA_SMI_TIMEOUT_SECONDS = 5
MAX_NVIDIA_SMI_OUTPUT_BYTES = 32 * 1024
MAX_NVIDIA_SMI_GPUS = 64
MLP_RUNTIME_PROBE_CHECK_ID = "mlp.runtime_probe"
COMPUTE_ALLOCATION_ENV_VARS = (
    "SLURM_JOB_ID",
    "SLURM_STEP_ID",
    "PBS_JOBID",
    "LSB_JOBID",
    "JOB_ID",
)
DEEPMD_TORCH_PAIR = {
    "python": "3.12",
    "deepmd-kit": "3.2.0",
    "torch": "2.11.0",
    "triton": "3.6.0",
}

# The official CUDA wheels append a PEP 440 local version such as
# ``2.11.0+cu126`` to the public Torch release. Keep the public version
# exact: a local suffix is the only tolerated variation, so ``2.12.0`` and
# pre/post/dev releases remain rejected.
_SUPPORTED_TORCH_VERSION = re.compile(
    r"^2\.11\.0(?:\+[0-9A-Za-z]+(?:[-_.][0-9A-Za-z]+)*)?$",
    re.IGNORECASE,
)


def _is_supported_torch_version(version: Any) -> bool:
    """Accept Torch 2.11.0 and its PEP 440 local CUDA build suffixes."""

    return isinstance(version, str) and _SUPPORTED_TORCH_VERSION.fullmatch(version) is not None

CORE_IMPORTS = (
    "ase",
    "dpdispatcher",
    "networkx",
    "numpy",
    "pandas",
    "phonopy",
    "pyxtal",
    "rdkit",
    "scipy",
    "yaml",
)


@dataclass
class Check:
    """One machine-readable preflight assertion."""

    check_id: str
    status: str
    required: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    """Collection of checks for one endpoint."""

    target: str
    workflow: str | None = None
    checks: list[Check] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def add(
        self,
        check_id: str,
        passed: bool,
        message: str,
        *,
        required: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.checks.append(
            Check(
                check_id=check_id,
                status="pass" if passed else ("fail" if required else "warn"),
                required=required,
                message=message,
                details=details or {},
            )
        )

    @property
    def ok(self) -> bool:
        return not any(item.status == "fail" for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": self.target,
            "workflow": self.workflow,
            "ok": self.ok,
            "host": {
                "hostname": platform.node(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python_executable": sys.executable,
                "python_version": platform.python_version(),
            },
            "facts": self.facts,
            "checks": [asdict(item) for item in self.checks],
        }


def _check_path(
    report: Report,
    check_id: str,
    path: Path,
    *,
    kind: str = "file",
    writable: bool = False,
    required: bool = True,
) -> None:
    path = path.expanduser()
    exists = path.is_dir() if kind == "directory" else path.is_file()
    if exists and writable:
        exists = os.access(path, os.W_OK | os.X_OK)
    qualifier = "writable " if writable else ""
    report.add(
        check_id,
        exists,
        f"{qualifier}{kind} {'available' if exists else 'missing'}: {path}",
        required=required,
        details={"path": str(path.resolve(strict=False)), "kind": kind},
    )


def _check_command(
    report: Report,
    check_id: str,
    names: Iterable[str],
    *,
    required: bool = True,
) -> str | None:
    candidates = tuple(names)
    resolved = next((shutil.which(name) for name in candidates if shutil.which(name)), None)
    report.add(
        check_id,
        resolved is not None,
        (
            f"executable visible: {resolved}"
            if resolved
            else f"none of {', '.join(candidates)} is visible on PATH"
        ),
        required=required,
        details={"candidates": list(candidates), "resolved": resolved},
    )
    return resolved


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _check_distribution(
    report: Report,
    distribution: str,
    expected: str | None = None,
    *,
    required: bool = True,
    matcher: Callable[[str], bool] | None = None,
) -> str | None:
    installed = _distribution_version(distribution)
    if installed is None:
        passed = False
    elif matcher is not None:
        passed = matcher(installed)
    else:
        passed = expected is None or installed == expected
    expectation = f"=={expected}" if expected else ""
    if matcher is not None and expected:
        expectation = f" (public version {expected}; PEP 440 local suffix allowed)"
    report.add(
        f"python.distribution.{distribution}",
        passed,
        (
            f"{distribution}{expectation} available ({installed})"
            if passed
            else f"expected {distribution}{expectation}; found {installed or 'not installed'}"
        ),
        required=required,
        details={"expected": expected, "installed": installed},
    )
    return installed


def _check_import(report: Report, module: str, *, required: bool = True) -> None:
    try:
        available = importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        available = False
    report.add(
        f"python.import.{module}",
        available,
        f"Python module {module} {'is' if available else 'is not'} importable",
        required=required,
    )


def _model_sha256(path: Path) -> tuple[str | None, int | None, str | None]:
    """Hash a model without deserializing it or reading unbounded input."""

    try:
        if not path.is_file():
            return None, None, "model file is missing"
        size = path.stat().st_size
        if size < 1:
            return None, size, "model file is empty"
        if size > MAX_MODEL_BYTES:
            return None, size, f"model exceeds {MAX_MODEL_BYTES} byte hash limit"
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as model_file:
            while chunk := model_file.read(MODEL_HASH_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    return None, size, f"model exceeds {MAX_MODEL_BYTES} byte hash limit"
                digest.update(chunk)
        return digest.hexdigest(), size, None
    except OSError as error:
        return None, None, str(error)


def _check_model(report: Report, model: Path) -> dict[str, Any]:
    """Check and fingerprint the model used by an MLP endpoint."""

    path = model.expanduser()
    digest, size, error = _model_sha256(path)
    details: dict[str, Any] = {
        "path": str(path.resolve(strict=False)),
        "sha256": digest,
        "size_bytes": size,
    }
    if error:
        details["error"] = error
    report.add(
        "mlp.model",
        digest is not None,
        (
            f"model available and SHA-256 recorded: {path}"
            if digest is not None
            else f"model unavailable or could not be hashed: {path} ({error})"
        ),
        details=details,
    )
    return details


def _has_compute_allocation() -> bool:
    """Return whether a scheduler allocation marker is present.

    A runtime probe is deliberately opt-in and is rejected by default outside
    an allocation.  This prevents a normal login-node preflight from silently
    becoming a model execution.
    """

    return any(os.environ.get(name) for name in COMPUTE_ALLOCATION_ENV_VARS)


def _compute_allocation_details() -> dict[str, str | None]:
    """Return the non-secret scheduler identity used for an explicit probe."""

    for name in COMPUTE_ALLOCATION_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return {"scheduler_env": name, "job_id": value}
    return {"scheduler_env": None, "job_id": None}


_PROBE_STAGE_ERROR_ATTR = "_ion_csp_probe_failed_stage"
_PROBE_COMPLETED_STAGES_ATTR = "_ion_csp_probe_completed_stages"
_PROBE_TRITON_ERROR_ATTR = "_ion_csp_probe_triton_error"


class _ProbeStageError(Exception):
    """Fallback wrapper when a runtime exception cannot hold diagnostics."""

    def __init__(
        self,
        original_error: Exception,
        failed_stage: str,
        completed_stages: Iterable[str],
        *,
        triton_error: bool = False,
    ) -> None:
        self.original_error = original_error
        self.failed_stage = failed_stage
        self.completed_stages = list(completed_stages)
        self.triton_error = triton_error
        super().__init__(str(original_error))


def _is_triton_error(error: BaseException) -> bool:
    if bool(
        getattr(error, _PROBE_TRITON_ERROR_ATTR, False)
        or getattr(error, "triton_error", False)
    ):
        return True
    if isinstance(error, _ProbeStageError):
        return _is_triton_error(error.original_error)
    text = f"{type(error).__name__}: {error}".lower()
    return "triton" in text or "torchinductor" in text


def _annotate_probe_error(
    error: Exception,
    failed_stage: str,
    completed_stages: Iterable[str],
    *,
    triton_error: bool = False,
) -> bool:
    """Attach JSON-safe runtime-probe context without changing the exception.

    The caller deliberately re-raises the original exception after calling
    this helper. Keeping the exception object intact preserves its type and
    message for the receipt (and keeps the normal traceback pointed at the
    actual failing operation).
    """

    try:
        setattr(error, _PROBE_STAGE_ERROR_ATTR, failed_stage)
        setattr(error, _PROBE_COMPLETED_STAGES_ATTR, list(completed_stages))
        if triton_error:
            setattr(error, _PROBE_TRITON_ERROR_ATTR, True)
    except Exception:
        # Built-in exceptions normally have a writable ``__dict__``. The
        # diagnostic path must never mask the scientific/runtime exception if
        # a third-party exception type does not permit custom attributes.
        return False
    return True


def _record_probe_error(
    error: Exception,
    failed_stage: str,
    completed_stages: Iterable[str],
    *,
    triton_error: bool = False,
) -> None:
    """Record stage context, falling back to a wrapper for immutable errors."""

    if _annotate_probe_error(
        error,
        failed_stage,
        completed_stages,
        triton_error=triton_error,
    ):
        return
    raise _ProbeStageError(
        error,
        failed_stage,
        completed_stages,
        triton_error=triton_error,
    ) from error


def _probe_error_context(error: Exception) -> tuple[str | None, list[str]]:
    """Read stage metadata attached by :func:`_run_runtime_probe`."""

    if isinstance(error, _ProbeStageError):
        return error.failed_stage, list(error.completed_stages)
    failed_stage = getattr(error, _PROBE_STAGE_ERROR_ATTR, None)
    completed_stages = getattr(error, _PROBE_COMPLETED_STAGES_ATTR, [])
    if not isinstance(failed_stage, str):
        failed_stage = None
    if not isinstance(completed_stages, list) or not all(
        isinstance(stage, str) for stage in completed_stages
    ):
        completed_stages = []
    return failed_stage, completed_stages


def _probe_original_error(error: Exception) -> Exception:
    """Return the original runtime error when a stage wrapper was needed."""

    if isinstance(error, _ProbeStageError):
        return error.original_error
    return error


def _build_probe_calculator(backend: str, model: Path, device: str | None = None):
    if backend in {"deepmd", "dpa4", "dpa4_ion_ft"}:
        from deepmd.calculator import DP

        return DP(str(model))
    if backend == "mattersim":
        from mattersim.forcefield import MatterSimCalculator

        kwargs: dict[str, Any] = {"load_path": str(model)}
        if device:
            kwargs["device"] = device
        return MatterSimCalculator(**kwargs)
    raise ValueError(f"unsupported MLP backend for runtime probe: {backend}")


def _summarize_array(value: Any) -> dict[str, Any]:
    """Return a small, JSON-safe summary of a force/stress result."""

    import numpy as np

    array = np.asarray(value)
    return {
        "shape": list(array.shape),
        "finite": bool(np.isfinite(array).all()),
    }


def _query_nvidia_smi_gpus(executable: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return bounded GPU identity/driver facts without retaining raw command output."""

    command = [
        executable,
        "--query-gpu=index,uuid,name,driver_version",
        "--format=csv,noheader",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"nvidia-smi GPU query failed: {type(error).__name__}"

    output = result.stdout or ""
    if len(output.encode("utf-8", errors="replace")) > MAX_NVIDIA_SMI_OUTPUT_BYTES:
        return None, "nvidia-smi GPU query exceeded the receipt output limit"
    if result.returncode != 0:
        return None, f"nvidia-smi GPU query exited with status {result.returncode}"

    try:
        rows = list(csv.reader(output.splitlines(), skipinitialspace=True))
    except csv.Error:
        return None, "nvidia-smi GPU query returned malformed CSV"
    if not rows or len(rows) > MAX_NVIDIA_SMI_GPUS:
        return None, "nvidia-smi GPU query returned no GPUs or exceeded the device limit"

    gpus: list[dict[str, Any]] = []
    seen_uuids: set[str] = set()
    for row in rows:
        if len(row) != 4:
            return None, "nvidia-smi GPU query returned an unexpected field count"
        index_text, uuid, name, driver_version = (field.strip() for field in row)
        try:
            index = int(index_text)
        except ValueError:
            return None, "nvidia-smi GPU query returned an invalid device index"
        if (
            index < 0
            or not uuid
            or not name
            or not driver_version
            or uuid in seen_uuids
        ):
            return None, "nvidia-smi GPU query returned incomplete or duplicate device facts"
        seen_uuids.add(uuid)
        gpus.append(
            {
                "index": index,
                "uuid": uuid,
                "name": name,
                "driver_version": driver_version,
            }
        )
    return {"gpus": gpus}, None


def _run_runtime_probe(
    *,
    backend: str,
    model: Path,
    poscar: Path,
    device: str | None = None,
    require_gpu: bool = False,
) -> dict[str, Any]:
    """Exercise the calculator path used by MLP optimization.

    This is intentionally separate from :func:`check_mlp`; ordinary preflight
    remains a no-science visibility check.  Callers must explicitly provide a
    POSCAR, and the public checker additionally requires a scheduler allocation
    marker unless the caller explicitly overrides that policy.
    """

    completed_stages: list[str] = []

    if backend in {"deepmd", "dpa4", "dpa4_ion_ft"} and require_gpu:
        try:
            import triton  # noqa: F401
        except Exception as error:
            _record_probe_error(
                error,
                "calculator_creation",
                completed_stages,
                triton_error=True,
            )
            raise

    try:
        from ase.io import read

        atoms = read(str(poscar))
    except Exception as error:
        _record_probe_error(error, "poscar_read", completed_stages)
        raise
    completed_stages.append("poscar_read")

    try:
        atoms.calc = _build_probe_calculator(backend, model, device=device)
    except Exception as error:
        _record_probe_error(error, "calculator_creation", completed_stages)
        raise
    completed_stages.append("calculator_creation")

    try:
        forces = atoms.get_forces()
        force_summary = _summarize_array(forces)
        if not force_summary["finite"]:
            raise RuntimeError("runtime probe produced non-finite forces")
    except Exception as error:
        _record_probe_error(error, "forces", completed_stages)
        raise
    completed_stages.append("forces")

    try:
        stress = atoms.get_stress()
        stress_summary = _summarize_array(stress)
        if not stress_summary["finite"]:
            raise RuntimeError("runtime probe produced non-finite stress")
    except Exception as error:
        _record_probe_error(error, "stress", completed_stages)
        raise
    completed_stages.append("stress")

    try:
        from ase.filters import UnitCellFilter

        filtered_atoms = UnitCellFilter(atoms)
    except Exception as error:
        _record_probe_error(error, "unit_cell_filter_creation", completed_stages)
        raise
    completed_stages.append("unit_cell_filter_creation")

    try:
        from ase.optimize import LBFGS

        # A single bounded optimizer step exercises UnitCellFilter's stress
        # route without writing or retaining scientific output files.
        optimizer = LBFGS(filtered_atoms, logfile=None)
        optimizer.run(fmax=0.03, steps=1)
        completed_steps = getattr(optimizer, "nsteps", None)
        if type(completed_steps) is not int or completed_steps < 1:
            raise RuntimeError("runtime probe optimizer did not complete at least one step")
    except Exception as error:
        _record_probe_error(error, "optimizer_step", completed_stages)
        raise
    completed_stages.append("optimizer_step")
    return {
        "status": "pass",
        "backend": backend,
        "model": str(model.resolve(strict=False)),
        "poscar": str(poscar.resolve(strict=False)),
        "forces": force_summary,
        "stress": stress_summary,
        "unit_cell_filter": "pass",
        "optimizer_steps_completed": completed_steps,
        "completed_stages": completed_stages,
        "failed_stage": None,
    }


def _load_mapping(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            if yaml is None:
                return None, "PyYAML is not installed"
            value = yaml.safe_load(text)
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)
    except Exception as error:
        if yaml is None or not isinstance(error, yaml.YAMLError):
            raise
        return None, str(error)
    if not isinstance(value, dict):
        return None, "top-level value must be a mapping"
    return value, None


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _nonempty_sequence(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _config_path(value: Any, work_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    # The workflow currently forwards machine/resource paths unchanged.  A
    # relative value is therefore interpreted from the launch directory, not
    # from work_dir.  The release contract requires absolute paths to avoid
    # that ambiguity.
    if not path.is_absolute():
        return None
    return path


def _check_machine_or_resources(
    report: Report,
    value: Any,
    work_dir: Path,
    check_id: str,
    kind: str,
) -> None:
    path = _config_path(value, work_dir)
    absolute = path is not None
    report.add(
        f"{check_id}.absolute",
        absolute,
        (
            f"{kind} path is absolute: {path}"
            if absolute
            else f"{kind} path must be a non-empty absolute .yaml/.json path"
        ),
    )
    if path is None:
        return
    supported = path.suffix.lower() in {".yaml", ".json"}
    report.add(
        f"{check_id}.suffix",
        supported,
        f"{kind} path uses {'a supported' if supported else 'an unsupported'} suffix",
        details={"path": str(path)},
    )
    if not supported:
        return
    mapping, error = _load_mapping(path)
    report.add(
        f"{check_id}.parse",
        mapping is not None,
        f"{kind} file {'parsed' if mapping is not None else 'did not parse'}",
        details={"path": str(path), "error": error},
    )
    if mapping is None:
        return
    if kind == "machine":
        context_type = mapping.get("context_type")
        batch_type = mapping.get("batch_type")
        report.add(
            f"{check_id}.context_type",
            context_type in {"LocalContext", "SSHContext"},
            f"machine context_type={context_type!r}",
        )
        direct_remote = context_type == "SSHContext" and str(batch_type).lower() == "shell"
        report.add(
            f"{check_id}.scheduler_safety",
            not direct_remote,
            (
                "remote jobs use a scheduler or run locally"
                if not direct_remote
                else "SSHContext + Shell executes directly on the remote host; verify it is not a login node"
            ),
            required=False,
        )
    else:
        for key in ("number_node", "cpu_per_node", "group_size"):
            report.add(
                f"{check_id}.{key}",
                _positive_integer(mapping.get(key)),
                f"resources.{key} must be a positive integer",
                details={"value": mapping.get(key)},
            )


def validate_workflow_config(
    workflow: str,
    work_dir: Path,
    report: Report | None = None,
) -> Report:
    """Validate the public ``<work_dir>/config.yaml`` contract.

    This deliberately validates the configuration before importing the heavy
    workflow modules.  It protects the release wrapper from launching a long
    workflow with a malformed or ambiguous path contract.
    """

    workflow = workflow.lower()
    if workflow not in {"ee", "csp"}:
        raise ValueError("workflow must be 'ee' or 'csp'")
    report = report or Report(target="config", workflow=workflow)
    work_dir = work_dir.expanduser().resolve(strict=False)
    _check_path(report, "work_dir", work_dir, kind="directory", writable=True)
    config_path = work_dir / "config.yaml"
    _check_path(report, "config.file", config_path)
    if not config_path.is_file():
        return report
    config, error = _load_mapping(config_path)
    report.add(
        "config.yaml_parse",
        config is not None,
        "config.yaml parsed as a mapping" if config is not None else "config.yaml is invalid",
        details={"error": error},
    )
    if config is None:
        return report

    required_sections = (
        ("convert_SMILES", "empirical_estimate")
        if workflow == "ee"
        else ("gen_opt", "read_mlp_density", "vasp_processing")
    )
    for section in required_sections:
        report.add(
            f"config.section.{section}",
            isinstance(config.get(section), dict),
            f"required mapping section {section!r}",
        )
    if not all(isinstance(config.get(section), dict) for section in required_sections):
        return report

    if workflow == "ee":
        conversion = config["convert_SMILES"]
        empirical = config["empirical_estimate"]
        csv_name = conversion.get("csv_file")
        csv_ok = isinstance(csv_name, str) and bool(csv_name.strip())
        report.add("config.ee.csv_file", csv_ok, "convert_SMILES.csv_file is non-empty")
        if csv_ok:
            csv_path = work_dir / csv_name
            _check_path(report, "config.ee.csv_exists", csv_path)
        folders = empirical.get("folders")
        ratios = empirical.get("ratios")
        ion_numbers = empirical.get("ion_numbers")
        sequences_ok = all(
            _nonempty_sequence(item) for item in (folders, ratios, ion_numbers)
        )
        report.add(
            "config.ee.parallel_lists",
            sequences_ok and len(folders) == len(ratios) == len(ion_numbers),
            "folders, ratios and ion_numbers must be non-empty lists of equal length",
        )
        report.add(
            "config.ee.sort_by",
            empirical.get("sort_by", "density") in {"density", "nitrogen", "NC_ratio"},
            "empirical_estimate.sort_by is supported",
        )
        report.add(
            "config.ee.nodes",
            _positive_integer(conversion.get("nodes")),
            "convert_SMILES.nodes must be a positive integer",
        )
        _check_machine_or_resources(
            report, conversion.get("machine"), work_dir, "config.ee.machine", "machine"
        )
        _check_machine_or_resources(
            report,
            conversion.get("resources"),
            work_dir,
            "config.ee.resources",
            "resources",
        )
    else:
        generation = config["gen_opt"]
        vasp = config["vasp_processing"]
        species = generation.get("species")
        ion_numbers = generation.get("ion_numbers")
        lists_ok = _nonempty_sequence(species) and _nonempty_sequence(ion_numbers)
        report.add(
            "config.csp.species_ion_numbers",
            lists_ok and len(species) == len(ion_numbers),
            "gen_opt.species and gen_opt.ion_numbers must be non-empty lists of equal length",
        )
        if isinstance(species, list):
            for index, value in enumerate(species):
                species_path = work_dir / str(value)
                _check_path(report, f"config.csp.species.{index}", species_path)
        backend = generation.get("mlp_backend", "deepmd")
        report.add(
            "config.csp.mlp_backend",
            backend in {"deepmd", "dpa4", "dpa4_ion_ft", "mattersim"},
            f"gen_opt.mlp_backend={backend!r}",
        )
        for section_name, section in (("gen_opt", generation), ("vasp_processing", vasp)):
            report.add(
                f"config.csp.{section_name}.nodes",
                _positive_integer(section.get("nodes")),
                f"{section_name}.nodes must be a positive integer",
            )
            _check_machine_or_resources(
                report,
                section.get("machine"),
                work_dir,
                f"config.csp.{section_name}.machine",
                "machine",
            )
            _check_machine_or_resources(
                report,
                section.get("resources"),
                work_dir,
                f"config.csp.{section_name}.resources",
                "resources",
            )
    return report


def check_core(
    *,
    workflow: str | None = None,
    project_root: Path | None = None,
    work_dir: Path | None = None,
    required_dirs: Iterable[Path] = (),
    writable_dirs: Iterable[Path] = (),
) -> Report:
    """Check the controller endpoint used to start EE/CSP."""

    report = Report(target="core", workflow=workflow)
    python_ok = sys.version_info[:2] == (3, 11)
    report.add(
        "python.release_profile",
        python_ok,
        "core release profile requires Python 3.11.x",
        details={"actual": platform.python_version(), "expected": "3.11.x"},
    )
    for module in CORE_IMPORTS:
        _check_import(report, module)
    _check_import(report, "ion_CSP")

    if project_root is not None:
        root = project_root.expanduser().resolve(strict=False)
        _check_path(report, "project.root", root, kind="directory")
        for relative in (
            "pyproject.toml",
            "uv.lock",
            "environment.yml",
            "delivery/locks/core.lock.json",
            "delivery/config-contract.schema.json",
            "delivery/release-manifest.json",
        ):
            _check_path(report, f"project.{relative}", root / relative)
        _check_path(report, "project.package", root / "src" / "ion_CSP", kind="directory")

    for index, path in enumerate(required_dirs):
        _check_path(report, f"required_dir.{index}", path, kind="directory")
    for index, path in enumerate(writable_dirs):
        _check_path(report, f"writable_dir.{index}", path, kind="directory", writable=True)

    if workflow == "ee":
        _check_command(report, "external.multiwfn", ("Multiwfn_noGUI", "Multiwfn"))
    elif workflow == "csp":
        _check_command(report, "external.phonopy", ("phonopy",))
    elif workflow == "all":
        _check_command(report, "external.multiwfn", ("Multiwfn_noGUI", "Multiwfn"))
        _check_command(report, "external.phonopy", ("phonopy",))

    if work_dir is not None and workflow in {"ee", "csp"}:
        validate_workflow_config(workflow, work_dir, report=report)
    return report


def check_mlp(
    *,
    backend: str = "deepmd",
    model: Path | None = None,
    require_gpu: bool = False,
    probe_poscar: Path | None = None,
    device: str | None = None,
    require_compute_node: bool = True,
    required_dirs: Iterable[Path] = (),
    writable_dirs: Iterable[Path] = (),
) -> Report:
    """Check the Python/MLP endpoint on the compute worker itself.

    The default path is deliberately non-scientific: it checks imports,
    package versions, model bytes, and CUDA visibility only. Supplying
    ``probe_poscar`` explicitly opts into a bounded calculator/forces/stress/
    ``UnitCellFilter`` probe. Unless ``require_compute_node`` is disabled,
    that probe is accepted only inside a scheduler allocation.
    """

    backend = backend.lower()
    report = Report(target="mlp", workflow="csp")
    report.facts["backend"] = backend
    report.add(
        "python.mlp_profile",
        sys.version_info[:2] == (3, 12),
        "MLP release profile requires Python 3.12.x",
        details={"actual": platform.python_version(), "expected": "3.12.x"},
    )
    package_versions: dict[str, str | None] = {
        "ase": _check_distribution(report, "ase", "3.23.0")
    }

    if backend in {"deepmd", "dpa4", "dpa4_ion_ft"}:
        _check_import(report, "deepmd")
        _check_import(report, "deepmd.calculator")
        deepmd_version = _check_distribution(
            report, "deepmd-kit", DEEPMD_TORCH_PAIR["deepmd-kit"]
        )
        torch_version = _check_distribution(
            report,
            "torch",
            DEEPMD_TORCH_PAIR["torch"],
            matcher=_is_supported_torch_version,
        )
        triton_version = _check_distribution(
            report, "triton", DEEPMD_TORCH_PAIR["triton"]
        )
        package_versions.update(
            {
                "deepmd-kit": deepmd_version,
                "torch": torch_version,
                "triton": triton_version,
            }
        )
        if require_gpu:
            _check_import(report, "triton")
        report.facts["pairing"] = {
            "expected": DEEPMD_TORCH_PAIR,
            "actual": {
                "deepmd-kit": deepmd_version,
                "torch": torch_version,
                "triton": triton_version,
            },
        }
    elif backend == "mattersim":
        _check_import(report, "mattersim")
        mattersim_version = _check_distribution(report, "mattersim")
        torch_version = _check_distribution(report, "torch")
        package_versions.update(
            {"mattersim": mattersim_version, "torch": torch_version}
        )
        report.facts["pairing"] = {
            "policy": "MatterSim is bring-your-own; capture its exact worker receipt before release",
            "actual": {"torch": torch_version},
        }
    else:
        report.add("mlp.backend", False, f"unsupported MLP backend: {backend}")
    report.facts["packages"] = package_versions

    cuda_available = False
    cuda_runtime = None
    try:
        import torch  # type: ignore

        availability = torch.cuda.is_available()
        cuda_available = availability is True
        runtime = getattr(torch.version, "cuda", None)
        cuda_runtime = runtime if isinstance(runtime, str) else None
    except (ImportError, AttributeError, RuntimeError):
        pass
    report.facts["cuda"] = {
        "required": require_gpu,
        "available": cuda_available,
        "torch_runtime": cuda_runtime,
    }
    report.add(
        "mlp.cuda",
        cuda_available or not require_gpu,
        (
            f"CUDA available through Torch runtime {cuda_runtime}"
            if cuda_available
            else "CUDA is not visible through Torch"
        ),
        required=require_gpu,
    )
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        report.add(
            "mlp.nvidia_smi",
            False,
            "nvidia-smi is not visible on PATH",
            required=require_gpu,
            details={"resolved": None},
        )
        report.facts["cuda"]["nvidia_smi"] = {"resolved": None, "gpus": []}
    elif require_gpu:
        gpu_facts, gpu_error = _query_nvidia_smi_gpus(nvidia_smi)
        report.add(
            "mlp.nvidia_smi",
            gpu_facts is not None,
            (
                "nvidia-smi recorded GPU UUID, name, and driver facts"
                if gpu_facts is not None
                else gpu_error or "nvidia-smi GPU query failed"
            ),
            required=True,
            details={"resolved": nvidia_smi, **(gpu_facts or {})},
        )
        report.facts["cuda"]["nvidia_smi"] = {
            "resolved": nvidia_smi,
            **(gpu_facts or {"gpus": []}),
        }
    else:
        report.add(
            "mlp.nvidia_smi",
            True,
            f"nvidia-smi is visible: {nvidia_smi}",
            required=False,
            details={"resolved": nvidia_smi},
        )
        report.facts["cuda"]["nvidia_smi"] = {"resolved": nvidia_smi, "gpus": []}

    if model is not None:
        report.facts["model"] = _check_model(report, model)
    else:
        report.facts["model"] = None

    probe: dict[str, Any] = {
        "requested": probe_poscar is not None,
        "status": "not_requested",
    }
    if probe_poscar is not None:
        probe_path = probe_poscar.expanduser()
        probe.update(
            {
                "status": "not_run",
                "poscar": str(probe_path.resolve(strict=False)),
                "allocation": _compute_allocation_details(),
                "noncompute_override": not require_compute_node,
            }
        )
        if model is None:
            probe.update(
                {
                    "status": "fail",
                    "error": "runtime probe requires an explicit model path",
                }
            )
            report.add(MLP_RUNTIME_PROBE_CHECK_ID, False, probe["error"], details=probe)
        elif not probe_path.is_file():
            probe.update(
                {
                    "status": "fail",
                    "error": f"probe POSCAR is missing: {probe_path}",
                }
            )
            report.add(MLP_RUNTIME_PROBE_CHECK_ID, False, probe["error"], details=probe)
        elif require_compute_node and not _has_compute_allocation():
            probe.update(
                {
                    "status": "fail",
                    "error": (
                        "runtime probe requires a scheduler compute allocation; "
                        "refusing to execute on an unallocated/login node"
                    ),
                }
            )
            report.add(MLP_RUNTIME_PROBE_CHECK_ID, False, probe["error"], details=probe)
        else:
            try:
                probe_result = _run_runtime_probe(
                    backend=backend,
                    model=model.expanduser(),
                    poscar=probe_path,
                    device=device,
                    require_gpu=require_gpu,
                )
            except Exception as error:
                traceback_text = traceback.format_exc()
                failed_stage, completed_stages = _probe_error_context(error)
                original_error = _probe_original_error(error)
                probe.update(
                    {
                        "status": "fail",
                        "error_type": type(original_error).__name__,
                        "error": str(original_error),
                        "failed_stage": failed_stage,
                        "completed_stages": completed_stages,
                        "traceback": traceback_text,
                        # Retain the compact field for consumers that used the
                        # original receipt format; the full traceback above
                        # is now the authoritative diagnostic.
                        "traceback_tail": traceback_text.splitlines()[-1:],
                    }
                )
                message = str(original_error) or type(original_error).__name__
                if (
                    backend in {"deepmd", "dpa4", "dpa4_ion_ft"}
                    and require_gpu
                    and _is_triton_error(error)
                ):
                    detail = str(original_error) or type(original_error).__name__
                    message = (
                        "DeepMD GPU runtime probe failed because Triton is missing or "
                        f"unusable: {detail}"
                    )
                report.add(
                    MLP_RUNTIME_PROBE_CHECK_ID,
                    False,
                    message,
                    details=probe,
                )
            else:
                probe.update(probe_result)
                probe.setdefault("completed_stages", [])
                probe.setdefault("failed_stage", None)
                model_facts = report.facts.get("model") or {}
                probe["model_sha256"] = model_facts.get("sha256")
                report.add(
                    MLP_RUNTIME_PROBE_CHECK_ID,
                    True,
                    "MLP runtime probe passed forces, stress, and one UnitCellFilter step",
                    details=probe,
                )
    report.facts["probe"] = probe

    for index, path in enumerate(required_dirs):
        _check_path(report, f"required_dir.{index}", path, kind="directory")
    for index, path in enumerate(writable_dirs):
        _check_path(report, f"writable_dir.{index}", path, kind="directory", writable=True)
    return report


def check_external(
    *,
    profiles: Iterable[str],
    required_dirs: Iterable[Path] = (),
    writable_dirs: Iterable[Path] = (),
) -> Report:
    """Check Gaussian/VASP endpoint visibility without executing the software."""

    selected = tuple(dict.fromkeys(profiles))
    report = Report(target="external", workflow="+".join(selected))
    known = {"gaussian", "vasp"}
    for profile in selected:
        report.add(
            f"external.profile.{profile}",
            profile in known,
            f"external profile {profile!r}",
        )
    if "gaussian" in selected:
        _check_command(report, "external.gaussian.g16", ("g16",))
        _check_command(report, "external.gaussian.formchk", ("formchk",))
    if "vasp" in selected:
        _check_command(report, "external.vasp.vasp_std", ("vasp_std",))
        _check_command(report, "external.vasp.mpi", ("mpirun", "mpiexec"))
    for index, path in enumerate(required_dirs):
        _check_path(report, f"required_dir.{index}", path, kind="directory")
    for index, path in enumerate(writable_dirs):
        _check_path(report, f"writable_dir.{index}", path, kind="directory", writable=True)
    return report


def write_report(report: Report, destination: str | None, *, pretty: bool = True) -> None:
    """Write a report to stdout and optionally to a durable receipt file."""

    payload = json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )
    print(payload)
    if destination and destination != "-":
        path = Path(destination).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check one ion_CSP deployment endpoint and emit a JSON receipt."
    )
    parser.add_argument("--target", required=True, choices=("core", "mlp", "external"))
    parser.add_argument("--workflow", choices=("ee", "csp", "all"))
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--required-dir", action="append", default=[], type=Path)
    parser.add_argument("--writable-dir", action="append", default=[], type=Path)
    parser.add_argument("--backend", default="deepmd")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--require-gpu", action="store_true")
    parser.add_argument(
        "--probe-poscar",
        type=Path,
        help=(
            "explicitly run a bounded MLP forces/stress/UnitCellFilter probe "
            "for this POSCAR; omitted by default"
        ),
    )
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--allow-noncompute-probe",
        action="store_true",
        help=(
            "allow the explicit runtime probe outside a scheduler allocation; "
            "use only for an intentional local workstation check"
        ),
    )
    parser.add_argument(
        "--external-profile",
        action="append",
        choices=("gaussian", "vasp"),
        default=[],
    )
    parser.add_argument("--json-output", default="-")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.target == "core":
        report = check_core(
            workflow=args.workflow,
            project_root=args.project_root,
            work_dir=args.work_dir,
            required_dirs=args.required_dir,
            writable_dirs=args.writable_dir,
        )
    elif args.target == "mlp":
        report = check_mlp(
            backend=args.backend,
            model=args.model,
            require_gpu=args.require_gpu,
            probe_poscar=args.probe_poscar,
            device=args.device,
            require_compute_node=not args.allow_noncompute_probe,
            required_dirs=args.required_dir,
            writable_dirs=args.writable_dir,
        )
    else:
        profiles = args.external_profile or ["gaussian", "vasp"]
        report = check_external(
            profiles=profiles,
            required_dirs=args.required_dir,
            writable_dirs=args.writable_dir,
        )
    write_report(report, args.json_output, pretty=not args.compact)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the script wrapper
    raise SystemExit(main())
