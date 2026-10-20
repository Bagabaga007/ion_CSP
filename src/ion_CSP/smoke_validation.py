"""Read-only evidence checks for one completed local CSP smoke.

This module never launches a scientific program. It reads the workdir outputs
and can optionally create a new CIF or JSON evidence file when explicitly asked.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
from ase.io import read as ase_read
from ase.io import write as ase_write


WORKFLOW_STAGES = (
    "1_generation",
    "1_optimization",
    "2_read_mlp_density",
    "3_vasp_optimization",
    "3_vasp_relaxation",
)

MAX_TIMEOUT_SECONDS = 120.0
MAX_CONFIG_BYTES = 1_000_000
MAX_STATUS_BYTES = 1_000_000
MAX_CSV_BYTES = 10_000_000
MAX_GJF_BYTES = 2_000_000
MAX_POSCAR_BYTES = 20_000_000
MAX_STAGE_STATUS_BYTES = 32_000
MAX_FINAL_OUTCAR_BYTES = 128_000_000
MAX_DIRECTORY_ENTRIES = 10_000
MAX_CANDIDATE_COUNT = 100
MAX_SMOKE_SITES = 200
_INDEXED_NAME = re.compile(r"^(?:POSCAR|CONTCAR|OUTCAR)_(\d+)$")
_SCREENED_CANDIDATE_ID = re.compile(r"^-?\d+(?:\.\d+)?_\d+$")
_OUTCAR_FATAL_MARKERS = (
    b"zbrent: fatal error",
    b"i refuse to continue with this sick job",
    b"very bad news",
    b"segmentation fault",
)
_OUTCAR_NORMAL_END = b"general timing and accounting informations for this job"
_OUTCAR_IONIC_CONVERGENCE = (
    b"reached required accuracy - stopping structural energy minimisation"
)


class _ValidationIssue(Exception):
    def __init__(
        self,
        message: str,
        *,
        category: str = "invalid_output",
        status: str = "FAIL",
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status = status


class _ReadBudget:
    def __init__(self, seconds: float) -> None:
        self.deadline = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self.deadline:
            raise _ValidationIssue(
                "Verifier read-time budget expired.",
                category="verifier_timeout",
                status="NOT RUN",
            )

    def read_bytes(self, path: Path, max_bytes: int) -> bytes:
        self.check()
        try:
            with path.open("rb") as stream:
                data = bytearray()
                while len(data) <= max_bytes:
                    self.check()
                    chunk = stream.read(min(1024 * 1024, max_bytes + 1 - len(data)))
                    if not chunk:
                        return bytes(data)
                    data.extend(chunk)
                raise _ValidationIssue(
                    f"File exceeds the {max_bytes}-byte verifier limit: {path.name}",
                    category="file_too_large",
                )
        except _ValidationIssue:
            raise
        except OSError as exc:
            raise _ValidationIssue(
                f"Could not read {path.name}: {exc}", category="missing_or_unreadable"
            ) from exc

    def read_text(self, path: Path, max_bytes: int) -> str:
        try:
            return self.read_bytes(path, max_bytes).decode("utf-8")
        except UnicodeError as exc:
            raise _ValidationIssue(
                f"File is not valid UTF-8: {path.name}", category="invalid_encoding"
            ) from exc


def _add_check(
    checks: list[dict[str, Any]],
    name: str,
    action: Callable[[], dict[str, Any] | None],
) -> dict[str, Any] | None:
    try:
        details = action() or {}
    except _ValidationIssue as exc:
        checks.append(
            {
                "check": name,
                "status": exc.status,
                "category": exc.category,
                "message": str(exc),
            }
        )
        return None
    except Exception as exc:  # Keep malformed artifacts in the report, not a traceback.
        checks.append(
            {
                "check": name,
                "status": "FAIL",
                "category": "invalid_output",
                "message": f"{type(exc).__name__}: {exc}",
            }
        )
        return None

    checks.append(
        {
            "check": name,
            "status": "PASS",
            "category": None,
            "message": "Check passed.",
            "details": details,
        }
    )
    return details


def _require_file(root: Path, relative: str, *, max_bytes: int | None = None) -> Path:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _ValidationIssue(
            f"Required output is missing: {relative}", category="missing_output"
        ) from exc
    if not resolved.is_relative_to(root):
        raise _ValidationIssue(
            f"Output path escapes the smoke workdir: {relative}",
            category="unsafe_path",
        )
    if not resolved.is_file():
        raise _ValidationIssue(
            f"Required output is not a file: {relative}", category="missing_output"
        )
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise _ValidationIssue(
            f"Could not stat {relative}: {exc}", category="missing_or_unreadable"
        ) from exc
    if max_bytes is not None and size > max_bytes:
        raise _ValidationIssue(
            f"File exceeds the {max_bytes}-byte verifier limit: {relative}",
            category="file_too_large",
        )
    if size == 0:
        raise _ValidationIssue(
            f"Required output is empty: {relative}", category="missing_output"
        )
    return resolved


def _load_mapping(path: Path, budget: _ReadBudget, max_bytes: int) -> dict[str, Any]:
    text = budget.read_text(path, max_bytes)
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            value = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise _ValidationIssue(
            f"Could not parse {path.name}: {exc}", category="invalid_metadata"
        ) from exc
    if not isinstance(value, dict):
        raise _ValidationIssue(
            f"Expected a mapping in {path.name}.", category="invalid_metadata"
        )
    return value


def _load_csv(
    path: Path, budget: _ReadBudget
) -> tuple[list[str], list[dict[str, str]]]:
    text = budget.read_text(path, MAX_CSV_BYTES)
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise _ValidationIssue(
            f"CSV has no header: {path.name}", category="invalid_csv"
        )
    rows = []
    for row in reader:
        budget.check()
        rows.append(row)
        if len(rows) > MAX_CANDIDATE_COUNT:
            raise _ValidationIssue(
                f"CSV exceeds the {MAX_CANDIDATE_COUNT}-row smoke limit: {path.name}",
                category="workload_limit_exceeded",
            )
    return list(reader.fieldnames), rows


def _counts_to_formula(counts: Counter[str]) -> str:
    if not counts:
        return ""
    order = ["C", "H"] if "C" in counts else []
    order.extend(sorted(symbol for symbol in counts if symbol not in order))
    return "".join(
        symbol + (str(counts[symbol]) if counts[symbol] != 1 else "")
        for symbol in order
    )


def _atom_counts(atoms) -> Counter[str]:
    return Counter(atoms.get_chemical_symbols())


def _validate_periodic_atoms(atoms, expected: dict[str, Any], label: str) -> None:
    volume = float(atoms.get_volume())
    cell_is_finite = all(
        math.isfinite(float(value)) for row in atoms.cell.array for value in row
    )
    if _atom_counts(atoms) != Counter(expected["counts"]):
        raise _ValidationIssue(
            f"{label} composition does not match the input formula.",
            category="composition_mismatch",
        )
    if (
        len(atoms) != expected["site_count"]
        or not math.isfinite(volume)
        or volume <= 0
        or not cell_is_finite
        or not bool(atoms.pbc.all())
    ):
        raise _ValidationIssue(
            f"{label} has an invalid site count or periodic cell.",
            category="invalid_structure",
        )


def _positive_finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _ValidationIssue(
            f"{label} is not numeric.", category="invalid_scientific_value"
        ) from exc
    if not math.isfinite(number) or number <= 0:
        raise _ValidationIssue(
            f"{label} is not finite and positive.", category="invalid_scientific_value"
        )
    return number


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _ValidationIssue(
            f"{label} is not numeric.", category="invalid_scientific_value"
        ) from exc
    if not math.isfinite(number):
        raise _ValidationIssue(
            f"{label} is not finite.", category="invalid_scientific_value"
        )
    return number


def _load_workdir_config(root: Path, budget: _ReadBudget) -> dict[str, Any]:
    path = _require_file(root, "config.yaml", max_bytes=MAX_CONFIG_BYTES)
    return _load_mapping(path, budget, MAX_CONFIG_BYTES)


def _load_external_mapping(
    path_value: Any, budget: _ReadBudget, label: str
) -> dict[str, Any]:
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise _ValidationIssue(
            f"{label} path must be absolute.", category="invalid_config"
        )
    budget.check()
    path = Path(path_value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _ValidationIssue(
            f"{label} file is unavailable.", category="missing_config"
        ) from exc
    if not resolved.is_file():
        raise _ValidationIssue(
            f"{label} path is not a file.", category="invalid_config"
        )
    if resolved.stat().st_size > MAX_CONFIG_BYTES:
        raise _ValidationIssue(
            f"{label} file exceeds the configuration size limit.",
            category="file_too_large",
        )
    return _load_mapping(resolved, budget, MAX_CONFIG_BYTES)


def _verify_local_dispatch_config(
    config: dict[str, Any], budget: _ReadBudget
) -> dict[str, Any]:
    gen = config.get("gen_opt")
    vasp = config.get("vasp_processing")
    if not isinstance(gen, dict) or not isinstance(vasp, dict):
        raise _ValidationIssue(
            "gen_opt and vasp_processing mappings are required.",
            category="invalid_config",
        )
    machine_configs = {}
    resource_configs = {}
    for stage, stage_config in (("gen_opt", gen), ("vasp_processing", vasp)):
        if not isinstance(stage_config, dict):
            raise _ValidationIssue(
                f"{stage} config is missing.", category="invalid_config"
            )
        machine_configs[stage] = _load_external_mapping(
            stage_config.get("machine"), budget, f"{stage}.machine"
        )
        resource_configs[stage] = _load_external_mapping(
            stage_config.get("resources"), budget, f"{stage}.resources"
        )
        machine = machine_configs[stage]
        if (
            machine.get("context_type") != "LocalContext"
            or str(machine.get("batch_type", "")).lower() != "shell"
        ):
            raise _ValidationIssue(
                f"{stage} must use LocalContext with Shell batch type.",
                category="not_local_execution",
            )

    gen_gpu = resource_configs["gen_opt"].get("gpu_per_node")
    vasp_gpu = resource_configs["vasp_processing"].get("gpu_per_node")
    gen_resource_nodes = resource_configs["gen_opt"].get("number_node")
    vasp_resource_nodes = resource_configs["vasp_processing"].get("number_node")
    gen_cpu = resource_configs["gen_opt"].get("cpu_per_node")
    vasp_cpu = resource_configs["vasp_processing"].get("cpu_per_node")
    if (
        type(gen_gpu) is not int
        or gen_gpu != 1
        or type(vasp_gpu) is not int
        or vasp_gpu != 0
        or type(gen_resource_nodes) is not int
        or gen_resource_nodes != 1
        or type(vasp_resource_nodes) is not int
        or vasp_resource_nodes != 1
        or type(gen_cpu) is not int
        or gen_cpu < 1
        or type(vasp_cpu) is not int
        or vasp_cpu < 1
    ):
        raise _ValidationIssue(
            "Smoke resources must use one node, one GPU for gen_opt, zero GPUs for VASP, "
            "and positive CPU counts.",
            category="invalid_resource_config",
        )
    return {
        "context_type": "LocalContext",
        "batch_type": "Shell",
        "mlp_gpu_per_node": gen_gpu,
        "mlp_cpu_per_node": gen_cpu,
        "vasp_gpu_per_node": vasp_gpu,
        "vasp_cpu_per_node": vasp_cpu,
    }


def _derive_expected_composition(
    root: Path, config: dict[str, Any], budget: _ReadBudget
) -> dict[str, Any]:
    gen_opt = config.get("gen_opt")
    if not isinstance(gen_opt, dict):
        raise _ValidationIssue(
            "config.yaml is missing gen_opt mapping.", category="invalid_config"
        )
    species = gen_opt.get("species")
    ion_numbers = gen_opt.get("ion_numbers")
    if (
        not isinstance(species, list)
        or not species
        or not isinstance(ion_numbers, list)
        or len(species) != len(ion_numbers)
    ):
        raise _ValidationIssue(
            "gen_opt.species and gen_opt.ion_numbers must be non-empty lists of equal length.",
            category="invalid_config",
        )

    expected: Counter[str] = Counter()
    total_sites = 0
    for species_file, amount in zip(species, ion_numbers):
        budget.check()
        if not isinstance(species_file, str) or Path(species_file).is_absolute():
            raise _ValidationIssue(
                "Each gen_opt species input must be a relative file in the workdir.",
                category="invalid_config",
            )
        if type(amount) is not int or amount < 1:
            raise _ValidationIssue(
                "Each gen_opt ion number must be a positive integer.",
                category="invalid_config",
            )
        input_path = _require_file(root, species_file, max_bytes=MAX_GJF_BYTES)
        try:
            molecule = ase_read(input_path, index=0)
        except Exception as exc:
            raise _ValidationIssue(
                f"Could not read species input {species_file}: {type(exc).__name__}: {exc}",
                category="invalid_species_input",
            ) from exc
        counts = _atom_counts(molecule)
        if not counts:
            raise _ValidationIssue(
                f"Species input has no atoms: {species_file}",
                category="invalid_species_input",
            )
        for symbol, count in counts.items():
            expected[symbol] += count * amount
        total_sites += len(molecule) * amount
        if total_sites > MAX_SMOKE_SITES:
            raise _ValidationIssue(
                f"Configured smoke exceeds the {MAX_SMOKE_SITES}-site verifier limit.",
                category="workload_limit_exceeded",
            )

    return {
        "counts": dict(sorted(expected.items())),
        "formula": _counts_to_formula(expected),
        "site_count": total_sites,
    }


def _verify_smoke_caps(config: dict[str, Any]) -> dict[str, Any]:
    gen_opt = config.get("gen_opt")
    density = config.get("read_mlp_density")
    vasp = config.get("vasp_processing")
    if not all(isinstance(section, dict) for section in (gen_opt, density, vasp)):
        raise _ValidationIssue(
            "config.yaml must contain gen_opt, read_mlp_density, and vasp_processing mappings.",
            category="invalid_config",
        )
    numeric_fields = (
        gen_opt.get("num_per_group"),
        gen_opt.get("space_groups_limit"),
        gen_opt.get("nodes"),
        density.get("n_screen"),
        vasp.get("nodes"),
    )
    if any(type(value) is not int or value < 1 for value in numeric_fields):
        raise _ValidationIssue(
            "Smoke limits must be explicit positive integers.",
            category="invalid_config",
        )
    generation_attempts = gen_opt["num_per_group"] * gen_opt["space_groups_limit"]
    if (
        generation_attempts > 2
        or gen_opt["nodes"] != 1
        or density["n_screen"] != 1
        or density.get("sort_by", "density") != "density"
        or density.get("molecules_screen", True) is not True
        or vasp["nodes"] != 1
        or vasp.get("molecules_prior", True) is not True
        or type(gen_opt.get("mlp_workers")) is not int
        or gen_opt.get("mlp_workers") != 1
    ):
        raise _ValidationIssue(
            "Workdir exceeds the single-candidate smoke caps (2 generation attempts, "
            "one MLP worker/node, n_screen=1, one VASP node).",
            category="workload_limit_exceeded",
        )
    return {
        "generation_attempt_cap": generation_attempts,
        "mlp_nodes": gen_opt["nodes"],
        "mlp_workers": gen_opt["mlp_workers"],
        "n_screen": density["n_screen"],
        "vasp_nodes": vasp["nodes"],
    }


def _verify_workflow_status(
    root: Path, budget: _ReadBudget, checks: list[dict[str, Any]]
) -> None:
    try:
        path = _require_file(root, "workflow_status.yaml", max_bytes=MAX_STATUS_BYTES)
        status = _load_mapping(path, budget, MAX_STATUS_BYTES)
    except _ValidationIssue as exc:
        for stage in WORKFLOW_STAGES:
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "NOT RUN",
                    "category": "missing",
                    "message": str(exc),
                }
            )
        return

    for stage in WORKFLOW_STAGES:
        raw = status.get(stage)
        if not isinstance(raw, dict):
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "NOT RUN",
                    "category": "missing",
                    "message": "Stage status is missing.",
                }
            )
            continue
        current = str(raw.get("current_status", "")).upper()
        if current == "SUCCESS":
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "PASS",
                    "category": None,
                    "message": "Workflow stage reports SUCCESS.",
                    "details": {"run_count": raw.get("run_count")},
                }
            )
        elif current == "RUNNING":
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "NOT RUN",
                    "category": "in_progress",
                    "message": "Workflow stage is still RUNNING.",
                }
            )
        elif current in {"FAILURE", "KILLED"}:
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "FAIL",
                    "category": "workflow_failure",
                    "message": f"Workflow stage ended with {current}.",
                }
            )
        else:
            checks.append(
                {
                    "check": f"workflow:{stage}",
                    "status": "NOT RUN",
                    "category": "missing",
                    "message": f"Workflow stage status is {current or 'unset'}.",
                }
            )


def _indexed_files(
    directory: Path, prefix: str, budget: _ReadBudget
) -> dict[str, Path]:
    try:
        entries = []
        for path in directory.iterdir():
            budget.check()
            entries.append(path)
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                raise _ValidationIssue(
                    f"Directory exceeds the {MAX_DIRECTORY_ENTRIES}-entry verifier limit: {directory.name}",
                    category="file_count_limit",
                )
    except _ValidationIssue:
        raise
    except OSError as exc:
        raise _ValidationIssue(
            f"Could not list {directory.name}: {exc}", category="missing_output"
        ) from exc
    result = {}
    for path in entries:
        budget.check()
        match = _INDEXED_NAME.fullmatch(path.name)
        if match and path.name.startswith(prefix) and path.is_file():
            result[match.group(1)] = path
    return result


def _verify_generated_structures(
    root: Path, expected: dict[str, Any], budget: _ReadBudget
) -> dict[str, Any]:
    generation_csv = _require_file(
        root, "1_generated/generation.csv", max_bytes=MAX_CSV_BYTES
    )
    headers, rows = _load_csv(generation_csv, budget)
    if not {"Space_group", "POSCAR_num"}.issubset(headers):
        raise _ValidationIssue(
            "generation.csv is missing required columns.", category="invalid_csv"
        )
    generated_count = 0
    for row in rows:
        try:
            generated_count += int(row["POSCAR_num"])
        except (TypeError, ValueError) as exc:
            raise _ValidationIssue(
                "generation.csv contains an invalid POSCAR count.",
                category="invalid_csv",
            ) from exc
    if generated_count < 1 or generated_count > 2:
        raise _ValidationIssue(
            "generation.csv must report one or two generated structures.",
            category="workload_limit_exceeded"
            if generated_count > 2
            else "missing_output",
        )

    directory = root / "1_generated" / "primitive_cell"
    positions = _indexed_files(directory, "POSCAR_", budget)
    if not positions:
        raise _ValidationIssue(
            "No symmetrized primitive POSCAR files were produced.",
            category="missing_output",
        )
    for index, path in positions.items():
        _require_file(root, str(path.relative_to(root)), max_bytes=MAX_POSCAR_BYTES)
        try:
            atoms = ase_read(path, format="vasp")
        except Exception as exc:
            raise _ValidationIssue(
                f"Could not parse primitive POSCAR_{index}: {type(exc).__name__}: {exc}",
                category="invalid_structure",
            ) from exc
        _validate_periodic_atoms(atoms, expected, f"Primitive POSCAR_{index}")
    return {"generated_count": generated_count, "primitive_cells": len(positions)}


def _verify_mlp_outputs(
    root: Path, expected: dict[str, Any], budget: _ReadBudget
) -> dict[str, Any]:
    directory = root / "2_mlp_optimized"
    contcars = _indexed_files(directory, "CONTCAR_", budget)
    outcars = _indexed_files(directory, "OUTCAR_", budget)
    if not contcars:
        raise _ValidationIssue(
            "No MLP CONTCAR outputs were found.", category="missing_output"
        )
    if set(contcars) != set(outcars):
        raise _ValidationIssue(
            "MLP CONTCAR and OUTCAR index sets do not match.", category="missing_output"
        )
    if len(contcars) > 2:
        raise _ValidationIssue(
            "MLP output count exceeds the two-structure smoke cap.",
            category="workload_limit_exceeded",
        )
    for index, path in contcars.items():
        _require_file(root, str(path.relative_to(root)), max_bytes=MAX_POSCAR_BYTES)
        _require_file(
            root,
            str(outcars[index].relative_to(root)),
            max_bytes=MAX_FINAL_OUTCAR_BYTES,
        )
        try:
            atoms = ase_read(path, format="vasp")
        except Exception as exc:
            raise _ValidationIssue(
                f"Could not parse MLP CONTCAR_{index}: {type(exc).__name__}: {exc}",
                category="invalid_structure",
            ) from exc
        _validate_periodic_atoms(atoms, expected, f"MLP CONTCAR_{index}")
    return {"mlp_structures": len(contcars), "indices": sorted(contcars, key=int)}


def _verify_screened_density(root: Path, budget: _ReadBudget) -> dict[str, Any]:
    path = _require_file(
        root,
        "2_mlp_optimized/max_density/mlp_density_energy.csv",
        max_bytes=MAX_CSV_BYTES,
    )
    headers, rows = _load_csv(path, budget)
    if not {"Number", "MLP_Density"}.issubset(headers):
        raise _ValidationIssue(
            "Screened MLP density CSV is missing required columns.",
            category="invalid_csv",
        )
    if len(rows) != 1:
        raise _ValidationIssue(
            "Smoke requires exactly one screened MLP density row.",
            category="workload_limit_exceeded" if len(rows) > 1 else "missing_output",
        )
    index = str(rows[0]["Number"])
    density = _positive_finite(rows[0]["MLP_Density"], "MLP_Density")
    directory = root / "3_for_vasp_opt"
    try:
        entries = []
        for path in directory.iterdir():
            budget.check()
            entries.append(path)
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                raise _ValidationIssue(
                    "Screened VASP input directory exceeds the file-count limit.",
                    category="file_count_limit",
                )
    except _ValidationIssue:
        raise
    except OSError as exc:
        raise _ValidationIssue(
            "Screened VASP input directory is missing or unreadable.",
            category="missing_output",
        ) from exc

    def find_ranked_file(prefix: str, max_bytes: int) -> Path:
        matches = [
            path
            for path in entries
            if path.is_file()
            and path.name.startswith(f"{prefix}_")
            and path.name.rsplit("_", 1)[-1] == index
        ]
        if len(matches) != 1:
            raise _ValidationIssue(
                f"Expected one screened {prefix} file for structure {index}.",
                category="missing_output" if not matches else "ambiguous_output",
            )
        return _require_file(
            root,
            str(matches[0].relative_to(root)),
            max_bytes=max_bytes,
        )

    contcar = find_ranked_file("CONTCAR", MAX_POSCAR_BYTES)
    find_ranked_file("OUTCAR", MAX_FINAL_OUTCAR_BYTES)
    return {"screened_index": index, "mlp_density": density, "source": contcar.name}


def _verify_final_summary(root: Path, budget: _ReadBudget) -> dict[str, Any]:
    path = _require_file(
        root, "final_relaxation_summary.json", max_bytes=MAX_STATUS_BYTES
    )
    summary = _load_mapping(path, budget, MAX_STATUS_BYTES)
    status = summary.get("status")
    eligible = summary.get("eligible_structures")
    if status == "NO_FINE_INPUTS":
        raise _ValidationIssue(
            "Final relaxation had no usable Fine inputs.",
            category="missing_fine_input",
        )
    if status != "SUBMISSION_COMPLETED":
        raise _ValidationIssue(
            f"Final relaxation summary status is {status!r}.",
            category="process_exit" if status == "FAILED" else "incomplete_stage",
            status="NOT RUN" if status == "PREPARED" else "FAIL",
        )
    if (
        summary.get("eligible_count") != 1
        or not isinstance(eligible, list)
        or len(eligible) != 1
        or not isinstance(eligible[0], str)
        or not _SCREENED_CANDIDATE_ID.fullmatch(eligible[0])
    ):
        raise _ValidationIssue(
            "Final relaxation summary must identify exactly one eligible structure.",
            category="invalid_metadata",
        )
    return {"candidate_id": eligible[0], "status": status}


def _parse_stage_marker(path: Path, budget: _ReadBudget) -> dict[str, str]:
    text = budget.read_text(path, MAX_STAGE_STATUS_BYTES)
    marker = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            marker[key.strip()] = value.strip()
    if not {"status", "reason", "exit_code"}.issubset(marker):
        raise _ValidationIssue(
            f"Stage marker is incomplete: {path.name}", category="invalid_metadata"
        )
    return marker


def _scan_final_outcar(path: Path, budget: _ReadBudget) -> dict[str, bool]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _ValidationIssue(
            f"Could not stat final OUTCAR: {exc}", category="missing_or_unreadable"
        ) from exc
    if size == 0:
        raise _ValidationIssue("Final OUTCAR is empty.", category="missing_output")
    if size > MAX_FINAL_OUTCAR_BYTES:
        raise _ValidationIssue(
            "Final OUTCAR exceeds the verifier size limit.", category="file_too_large"
        )

    found = {"fatal": False, "normal_end": False, "ionic_convergence": False}
    overlap = b""
    try:
        with path.open("rb") as stream:
            while True:
                budget.check()
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                data = (overlap + chunk).lower()
                found["fatal"] = found["fatal"] or any(
                    marker in data for marker in _OUTCAR_FATAL_MARKERS
                )
                found["normal_end"] = found["normal_end"] or _OUTCAR_NORMAL_END in data
                found["ionic_convergence"] = (
                    found["ionic_convergence"] or _OUTCAR_IONIC_CONVERGENCE in data
                )
                overlap = data[-160:]
    except OSError as exc:
        raise _ValidationIssue(
            f"Could not scan final OUTCAR: {exc}", category="missing_or_unreadable"
        ) from exc
    return found


def _verify_vasp_stages(
    root: Path,
    candidate_id: str,
    budget: _ReadBudget,
    checks: list[dict[str, Any]],
) -> None:
    stage_paths = {
        "rough": f"4_vasp_optimized/{candidate_id}/ION_CSP_STAGE_STATUS",
        "fine": f"4_vasp_optimized/{candidate_id}/fine/ION_CSP_STAGE_STATUS",
        "final": f"4_vasp_optimized/{candidate_id}/fine/final/ION_CSP_STAGE_STATUS",
    }
    for stage, relative in stage_paths.items():

        def check_marker(stage=stage, relative=relative):
            path = _require_file(root, relative, max_bytes=MAX_STAGE_STATUS_BYTES)
            marker = _parse_stage_marker(path, budget)
            try:
                exit_code = int(marker["exit_code"])
            except ValueError as exc:
                raise _ValidationIssue(
                    f"VASP {stage} stage has an invalid exit code.",
                    category="invalid_metadata",
                ) from exc
            if exit_code != 0:
                raise _ValidationIssue(
                    f"VASP {stage} process exited with status {exit_code}.",
                    category="process_exit",
                )
            if marker["status"].upper() != "SUCCESS":
                reason = marker["reason"]
                category = (
                    "not_converged"
                    if "not_converged" in reason
                    else "vasp_stage_failure"
                )
                raise _ValidationIssue(
                    f"VASP {stage} stage reports {marker['status']}: {reason}.",
                    category=category,
                )
            stage_dir = path.parent
            _require_file(
                root,
                str((stage_dir / "CONTCAR").relative_to(root)),
                max_bytes=MAX_POSCAR_BYTES,
            )
            outcar_path = _require_file(
                root,
                str((stage_dir / "OUTCAR").relative_to(root)),
                max_bytes=MAX_FINAL_OUTCAR_BYTES,
            )
            if stage != "final":
                intermediate = _scan_final_outcar(outcar_path, budget)
                if intermediate["fatal"]:
                    raise _ValidationIssue(
                        f"VASP {stage} OUTCAR contains a fatal marker.",
                        category="vasp_stage_failure",
                    )
            if stage == "final":
                scan = _scan_final_outcar(outcar_path, budget)
                if scan["fatal"]:
                    raise _ValidationIssue(
                        "Final OUTCAR contains a fatal VASP marker.",
                        category="vasp_stage_failure",
                    )
                if not scan["normal_end"]:
                    raise _ValidationIssue(
                        "Final OUTCAR lacks the normal-termination marker.",
                        category="incomplete_output",
                    )
                if not scan["ionic_convergence"]:
                    raise _ValidationIssue(
                        "Final OUTCAR terminated without ionic convergence.",
                        category="not_converged",
                    )
            return {"exit_code": exit_code, "reason": marker["reason"]}

        _add_check(checks, f"vasp_stage:{stage}", check_marker)


def _verify_final_csv(
    root: Path, candidate_id: str, budget: _ReadBudget
) -> dict[str, Any]:
    path = _require_file(root, "vasp_density_energy.csv", max_bytes=MAX_CSV_BYTES)
    headers, rows = _load_csv(path, budget)
    required = {"Number", "Final_Energy", "Final_Density", "Final_Ions_Check"}
    if not required.issubset(headers):
        raise _ValidationIssue(
            "Final VASP CSV is missing required Final columns.", category="invalid_csv"
        )
    if len(rows) != 1:
        raise _ValidationIssue(
            "Smoke requires exactly one valid final VASP CSV row.",
            category="missing_output" if not rows else "workload_limit_exceeded",
        )
    row = rows[0]
    candidate_number = candidate_id.rsplit("_", 1)[-1]
    if str(row["Number"]) != candidate_number:
        raise _ValidationIssue(
            "Final VASP CSV candidate does not match the relaxation summary.",
            category="candidate_mismatch",
        )
    energy = _finite(row["Final_Energy"], "Final_Energy")
    density = _positive_finite(row["Final_Density"], "Final_Density")
    if str(row["Final_Ions_Check"]).strip().lower() != "true":
        raise _ValidationIssue(
            "Final_Ions_Check is not true; the final structure is not validated.",
            category="topology_failure",
        )
    return {
        "candidate_id": candidate_id,
        "final_energy": energy,
        "final_density": density,
    }


def _verify_final_poscar(
    root: Path, expected: dict[str, Any], budget: _ReadBudget
) -> tuple[dict[str, Any], Any]:
    path = _require_file(root, "POSCAR", max_bytes=MAX_POSCAR_BYTES)
    budget.check()
    try:
        atoms = ase_read(path, format="vasp")
    except Exception as exc:
        raise _ValidationIssue(
            f"Final POSCAR could not be parsed: {type(exc).__name__}: {exc}",
            category="invalid_structure",
        ) from exc
    counts = _atom_counts(atoms)
    if counts != Counter(expected["counts"]):
        raise _ValidationIssue(
            "Final POSCAR composition does not match the configured species formula.",
            category="composition_mismatch",
        )
    try:
        volume = float(atoms.get_volume())
        cell_is_finite = all(
            math.isfinite(float(value)) for row in atoms.cell.array for value in row
        )
    except Exception as exc:
        raise _ValidationIssue(
            f"Final POSCAR has an invalid periodic cell: {exc}",
            category="invalid_structure",
        ) from exc
    if (
        len(atoms) != expected["site_count"]
        or not math.isfinite(volume)
        or volume <= 0
        or not cell_is_finite
        or not bool(atoms.pbc.all())
    ):
        raise _ValidationIssue(
            "Final POSCAR must have the expected site count and a positive periodic cell.",
            category="invalid_structure",
        )
    details = {
        "formula": _counts_to_formula(counts),
        "site_count": len(atoms),
        "volume_angstrom3": volume,
    }
    return details, atoms


def _write_cif_new(
    root: Path, atoms, expected: dict[str, Any], budget: _ReadBudget
) -> dict[str, Any]:
    destination = root / "Final.cif"
    if destination.exists():
        raise _ValidationIssue(
            "Final.cif already exists; refusing to overwrite it.",
            category="output_exists",
        )
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".Final.cif.", suffix=".tmp", dir=root, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
        budget.check()
        ase_write(temp_path, atoms, format="cif")
        if temp_path.stat().st_size > MAX_POSCAR_BYTES:
            raise _ValidationIssue(
                "Generated CIF exceeds the verifier size limit.",
                category="file_too_large",
            )
        roundtrip = ase_read(temp_path, format="cif")
        counts = _atom_counts(roundtrip)
        if (
            counts != Counter(expected["counts"])
            or len(roundtrip) != expected["site_count"]
        ):
            raise _ValidationIssue(
                "CIF roundtrip changed the final formula or site count.",
                category="cif_roundtrip_failure",
            )
        if not math.isclose(
            float(roundtrip.get_volume()),
            float(atoms.get_volume()),
            rel_tol=1e-5,
            abs_tol=1e-5,
        ):
            raise _ValidationIssue(
                "CIF roundtrip changed the final cell volume.",
                category="cif_roundtrip_failure",
            )
        budget.check()
        try:
            os.link(temp_path, destination)
        except FileExistsError as exc:
            raise _ValidationIssue(
                "Final.cif appeared during export; refusing to overwrite it.",
                category="output_exists",
            ) from exc
        return {
            "path": destination.name,
            "formula": _counts_to_formula(counts),
            "site_count": len(roundtrip),
        }
    except _ValidationIssue:
        raise
    except Exception as exc:
        raise _ValidationIssue(
            f"Could not create/roundtrip CIF: {type(exc).__name__}: {exc}",
            category="cif_roundtrip_failure",
        ) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def validate_local_csp_smoke(
    workdir: str | Path,
    *,
    timeout_seconds: float = 30.0,
    write_cif: bool = False,
) -> dict[str, Any]:
    """Read and validate one completed smoke; never launch or modify science jobs."""
    if (
        not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"timeout_seconds must be finite and in (0, {MAX_TIMEOUT_SECONDS}]."
        )
    root = Path(workdir).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workdir must be an existing directory.")
    budget = _ReadBudget(float(timeout_seconds))
    checks: list[dict[str, Any]] = []
    cif_written = False

    config = None

    def load_config() -> dict[str, Any]:
        nonlocal config
        config = _load_workdir_config(root, budget)
        return {"loaded": True}

    _add_check(checks, "config", load_config)
    expected = None
    if config is not None:
        _add_check(checks, "smoke_caps", lambda: _verify_smoke_caps(config))
        _add_check(
            checks,
            "local_dispatch_config",
            lambda: _verify_local_dispatch_config(config, budget),
        )
        expected = _add_check(
            checks,
            "input_composition",
            lambda: _derive_expected_composition(root, config, budget),
        )
    else:
        checks.append(
            {
                "check": "smoke_caps",
                "status": "NOT RUN",
                "category": "missing_config",
                "message": "Smoke config could not be loaded.",
            }
        )
        checks.append(
            {
                "check": "local_dispatch_config",
                "status": "NOT RUN",
                "category": "missing_config",
                "message": "Local dispatch configuration could not be verified without config.yaml.",
            }
        )
        checks.append(
            {
                "check": "input_composition",
                "status": "NOT RUN",
                "category": "missing_config",
                "message": "Expected formula could not be derived without config.yaml.",
            }
        )

    _verify_workflow_status(root, budget, checks)
    if expected is None:
        for name in (
            "generation",
            "mlp_outputs",
            "screened_density",
            "final_summary",
            "final_csv",
            "final_poscar",
        ):
            checks.append(
                {
                    "check": name,
                    "status": "NOT RUN",
                    "category": "missing_composition",
                    "message": "Expected composition is unavailable.",
                }
            )
        final_atoms = None
    else:
        _add_check(
            checks,
            "generation",
            lambda: _verify_generated_structures(root, expected, budget),
        )
        _add_check(
            checks,
            "mlp_outputs",
            lambda: _verify_mlp_outputs(root, expected, budget),
        )
        _add_check(
            checks,
            "screened_density",
            lambda: _verify_screened_density(root, budget),
        )
        final_summary = _add_check(
            checks,
            "final_relaxation_summary",
            lambda: _verify_final_summary(root, budget),
        )
        candidate_id = final_summary.get("candidate_id") if final_summary else None
        if candidate_id is not None:
            _verify_vasp_stages(root, candidate_id, budget, checks)
            _add_check(
                checks,
                "final_csv",
                lambda: _verify_final_csv(root, candidate_id, budget),
            )
        else:
            checks.extend(
                {
                    "check": name,
                    "status": "NOT RUN",
                    "category": "missing_candidate",
                    "message": "Final candidate could not be identified.",
                }
                for name in (
                    "vasp_stage:rough",
                    "vasp_stage:fine",
                    "vasp_stage:final",
                    "final_csv",
                )
            )
        try:
            final_poscar_details, final_atoms = _verify_final_poscar(
                root, expected, budget
            )
            checks.append(
                {
                    "check": "final_poscar",
                    "status": "PASS",
                    "category": None,
                    "message": "Final POSCAR has the expected formula and periodic cell.",
                    "details": final_poscar_details,
                }
            )
        except _ValidationIssue as exc:
            checks.append(
                {
                    "check": "final_poscar",
                    "status": exc.status,
                    "category": exc.category,
                    "message": str(exc),
                }
            )
            final_atoms = None

    if write_cif:
        if final_atoms is None or expected is None:
            checks.append(
                {
                    "check": "cif_export",
                    "status": "NOT RUN",
                    "category": "prerequisite_failed",
                    "message": "Final POSCAR validation must pass before CIF export.",
                }
            )
        elif any(
            check["status"] != "PASS"
            for check in checks
            if check["check"] != "cif_export"
        ):
            checks.append(
                {
                    "check": "cif_export",
                    "status": "NOT RUN",
                    "category": "prerequisite_failed",
                    "message": "Required smoke checks must pass before CIF export.",
                }
            )
        else:
            cif_result = _add_check(
                checks,
                "cif_export",
                lambda: _write_cif_new(root, final_atoms, expected, budget),
            )
            cif_written = cif_result is not None

    required_statuses = [check["status"] for check in checks]
    if "FAIL" in required_statuses:
        overall = "FAIL"
    elif "NOT RUN" in required_statuses:
        overall = "NOT RUN"
    else:
        overall = "PASS"
    return {
        "schema_version": 1,
        "verifier": "ion_CSP.local_smoke_validation",
        "status": overall,
        "read_only": not cif_written,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workdir": str(root),
        "timeout_seconds": float(timeout_seconds),
        "optional_cif_requested": write_cif,
        "checks": checks,
        "limitations": [
            "Validates recorded stage and artifact evidence; it does not reproduce or certify scientific calculations.",
            "CIF output is written only when explicitly requested and is never overwritten.",
        ],
    }


def write_evidence_json(report: dict[str, Any], path: str | Path) -> Path:
    """Create a new JSON evidence file only when the caller explicitly requests it."""
    output = Path(path).expanduser().resolve()
    if not output.parent.is_dir():
        raise FileNotFoundError(
            f"Evidence parent directory does not exist: {output.parent}"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(f"Refusing to overwrite evidence file: {output}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Read-only verifier for one completed local ion_CSP CSP smoke."
    )
    parser.add_argument("workdir", type=Path, help="completed CSP smoke workdir")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help=f"overall verifier read budget (maximum {MAX_TIMEOUT_SECONDS:g} seconds)",
    )
    parser.add_argument(
        "--write-cif",
        action="store_true",
        help="explicitly write a new Final.cif from a validated Final POSCAR; never overwrites",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="optional new JSON evidence path; an existing file is never overwritten",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = validate_local_csp_smoke(
            args.workdir,
            timeout_seconds=args.timeout_seconds,
            write_cif=args.write_cif,
        )
        if args.json_output is not None:
            report["read_only"] = False
            report["evidence_path"] = str(args.json_output.expanduser().resolve())
            write_evidence_json(report, args.json_output)
    except (ValueError, OSError, FileExistsError) as exc:
        report = {
            "schema_version": 1,
            "verifier": "ion_CSP.local_smoke_validation",
            "status": "FAIL",
            "read_only": not args.write_cif,
            "message": f"{type(exc).__name__}: {exc}",
            "checks": [],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "NOT RUN": 2}[report["status"]]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
