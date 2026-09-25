"""Deployment preflight checks for the three ion_CSP execution endpoints.

The scientific workflow is intentionally split across a controller, an MLP
worker, and licensed/external quantum-chemistry workers.  This module provides
one JSON-producing checker that is installed with the package and can be run
on each endpoint independently.  It never submits a job or executes a licensed
scientific program; executable checks are visibility checks only.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # Keep the checker JSON-capable on an unprovisioned core.
    yaml = None


SCHEMA_VERSION = 1
DEEPMD_TORCH_PAIR = {
    "python": "3.12",
    "deepmd-kit": "3.2.0",
    "torch": "2.11.0",
}

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
) -> str | None:
    installed = _distribution_version(distribution)
    passed = installed is not None and (expected is None or installed == expected)
    expectation = f"=={expected}" if expected else ""
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
    required_dirs: Iterable[Path] = (),
    writable_dirs: Iterable[Path] = (),
) -> Report:
    """Check the Python/MLP endpoint on the compute worker itself."""

    report = Report(target="mlp", workflow="csp")
    report.facts["backend"] = backend
    report.add(
        "python.mlp_profile",
        sys.version_info[:2] == (3, 12),
        "MLP release profile requires Python 3.12.x",
        details={"actual": platform.python_version(), "expected": "3.12.x"},
    )
    _check_distribution(report, "ase", "3.23.0")

    if backend in {"deepmd", "dpa4", "dpa4_ion_ft"}:
        _check_import(report, "deepmd")
        deepmd_version = _check_distribution(report, "deepmd-kit", DEEPMD_TORCH_PAIR["deepmd-kit"])
        torch_version = _check_distribution(report, "torch", DEEPMD_TORCH_PAIR["torch"])
        report.facts["pairing"] = {
            "expected": DEEPMD_TORCH_PAIR,
            "actual": {"deepmd-kit": deepmd_version, "torch": torch_version},
        }
    elif backend == "mattersim":
        _check_import(report, "mattersim")
        _check_distribution(report, "mattersim")
        torch_version = _check_distribution(report, "torch")
        report.facts["pairing"] = {
            "policy": "MatterSim is bring-your-own; capture its exact worker receipt before release",
            "actual": {"torch": torch_version},
        }
    else:
        report.add("mlp.backend", False, f"unsupported MLP backend: {backend}")

    cuda_available = False
    cuda_runtime = None
    try:
        import torch  # type: ignore

        cuda_available = bool(torch.cuda.is_available())
        cuda_runtime = getattr(torch.version, "cuda", None)
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
    _check_command(report, "mlp.nvidia_smi", ("nvidia-smi",), required=require_gpu)

    if model is not None:
        _check_path(report, "mlp.model", model)
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
