"""Read-only readiness checks for a local ion_CSP CSP workstation.

The checker validates the selected local profile and work directory, reads an
MLP preflight receipt, and checks executable visibility. It never launches an
MLP interpreter, VASP, or MPI. The only external command it may query is
nvidia-smi, and only when the profile assigns gen_opt to a GPU.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from ion_CSP.compute_profile import ProfileError, ProfilePlan, StagePlan, load_profile
from ion_CSP.preflight import check_core


SCHEMA_VERSION = 1
MLP_BACKENDS = {"deepmd", "dpa4", "dpa4_ion_ft", "mattersim"}
DEEPMD_TORCH_PAIR = {"deepmd-kit": "3.2.0", "torch": "2.11.0"}
NVIDIA_SMI_TIMEOUT_SECONDS = 5
REQUIRED_CSP_STAGES = ("gen_opt", "vasp_processing")
DEFAULT_LOCAL_GENERATION_LIMIT = 100
MAX_LOCAL_GENERATION_LIMIT = 1000
MAX_MODEL_BYTES = 512 * 1024 * 1024
MODEL_HASH_CHUNK_BYTES = 1024 * 1024


class _UniqueYamlLoader(yaml.SafeLoader):
    """Reject duplicate keys in customer-owned read-only YAML inputs."""


def _construct_unique_mapping(loader: _UniqueYamlLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise yaml.YAMLError("duplicate mapping key")
        except TypeError as error:
            raise yaml.YAMLError("mapping keys must be scalar") from error
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    message: str,
    *,
    required: bool = True,
    details: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": status,
            "required": required,
            "message": message,
            "details": details or {},
        }
    )


def _resolved_workdir(value: str | Path) -> Path | None:
    path = Path(value)
    if not path.is_absolute():
        return None
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


def _read_workdir_config(workdir: Path | None) -> dict[str, Any] | None:
    if workdir is None:
        return None
    try:
        parsed = yaml.load(
            (workdir / "config.yaml").read_text(encoding="utf-8"),
            Loader=_UniqueYamlLoader,
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _profile_environment_path(stage: StagePlan | None) -> str:
    base_path = os.environ.get("PATH", os.defpath)
    if stage is None:
        return base_path
    envs = stage.resources.get("envs", {})
    configured_path = envs.get("PATH") if isinstance(envs, dict) else None
    if not isinstance(configured_path, str) or not configured_path:
        return base_path
    return configured_path.replace("$PATH", base_path)


def _resolve_executable(command: Any, *, path: str) -> Path | None:
    if not isinstance(command, str) or not command.strip() or command != command.strip():
        return None
    candidate = Path(command)
    if candidate.is_absolute():
        found = candidate
    else:
        resolved = shutil.which(command, path=path)
        found = Path(resolved) if resolved else None
    return _invocation_path(found, require_executable=True) if found is not None else None


def _invocation_path(path: Path | None, *, require_executable: bool = False) -> Path | None:
    """Resolve directory aliases while preserving the executable's final symlink."""
    if path is None or not path.name:
        return None
    try:
        parent = path.parent.resolve(strict=True)
        identity = parent / path.name
        if not identity.is_file():
            return None
        if require_executable and not os.access(identity, os.X_OK):
            return None
        return identity
    except (OSError, RuntimeError):
        return None


def _effective_model_path(config: dict[str, Any] | None, backend: str | None) -> Path | None:
    """Mirror the model file gen_opt stages, without opening it as a model."""
    package_model = Path(__file__).resolve().parent / "model" / "model.pt"
    if backend == "deepmd":
        candidate = package_model
    else:
        generation = _stage_mapping(config, "gen_opt")
        configured = generation.get("mlp_model", "model.pt") if generation else None
        if not isinstance(configured, str) or not configured.strip():
            return None
        candidate = Path(configured)
        if not candidate.is_file():
            candidate = package_model.parent / candidate.name
    try:
        resolved = candidate.resolve(strict=True)
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError):
        return None


def _model_sha256(path: Path) -> str | None:
    """Hash model bytes with a strict size cap; never deserialize or load them."""
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size < 1 or size > MAX_MODEL_BYTES:
            return None
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as model_file:
            while chunk := model_file.read(MODEL_HASH_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    return None
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _core_check_status(core_report: Any, predicate: Callable[[str], bool]) -> str:
    selected = [
        item
        for item in getattr(core_report, "checks", [])
        if getattr(item, "required", True) and predicate(str(getattr(item, "check_id", "")))
    ]
    if not selected:
        return "FAIL"
    return "PASS" if all(getattr(item, "status", None) == "pass" for item in selected) else "FAIL"


def _stage_mapping(config: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if config is None:
        return None
    value = config.get(name)
    return value if isinstance(value, dict) else None


def _workload_budget(
    config: dict[str, Any] | None, *, generation_limit: int
) -> tuple[str, int | None, dict[str, int]]:
    if (
        type(generation_limit) is not int
        or generation_limit < 1
        or generation_limit > MAX_LOCAL_GENERATION_LIMIT
    ):
        return "FAIL", None, {}
    generation = _stage_mapping(config, "gen_opt")
    density = _stage_mapping(config, "read_mlp_density")
    vasp = _stage_mapping(config, "vasp_processing")
    if generation is None or density is None or vasp is None:
        return "NOT RUN", None, {}
    fields = {
        "num_per_group": generation.get("num_per_group"),
        "space_groups_limit": generation.get("space_groups_limit"),
        "n_screen": density.get("n_screen"),
        "gen_opt_nodes": generation.get("nodes"),
        "vasp_processing_nodes": vasp.get("nodes"),
    }
    if any(value is None for value in fields.values()):
        return "NOT RUN", None, {}
    if any(type(value) is not int or value < 1 for value in fields.values()):
        return "FAIL", None, {}
    if fields["space_groups_limit"] > 230:
        return "FAIL", None, {}
    scale = fields["num_per_group"] * fields["space_groups_limit"]
    return ("PASS" if scale <= generation_limit else "FAIL", scale, fields)


def _local_cpu_budget(
    config: dict[str, Any] | None,
    gen_stage: StagePlan | None,
    vasp_stage: StagePlan | None,
    *,
    cpu_count: int | None,
) -> tuple[str, dict[str, int]]:
    generation = _stage_mapping(config, "gen_opt")
    vasp = _stage_mapping(config, "vasp_processing")
    if generation is None or vasp is None or gen_stage is None or vasp_stage is None:
        return "NOT RUN", {}
    if type(cpu_count) is not int or cpu_count < 1:
        return "NOT RUN", {}

    workers = generation.get("mlp_workers")
    gen_nodes = generation.get("nodes")
    vasp_nodes = vasp.get("nodes")
    gen_cpu = gen_stage.resources.get("cpu_per_node")
    vasp_cpu = vasp_stage.resources.get("cpu_per_node")
    gen_resource_nodes = gen_stage.resources.get("number_node")
    vasp_resource_nodes = vasp_stage.resources.get("number_node")
    values = (
        workers,
        gen_nodes,
        vasp_nodes,
        gen_cpu,
        vasp_cpu,
        gen_resource_nodes,
        vasp_resource_nodes,
    )
    if any(type(value) is not int or value < 1 for value in values):
        return "FAIL", {}
    if workers > gen_cpu:
        return "FAIL", {}
    if gen_stage.resource_class == "gpu" and workers != 1:
        return "FAIL", {}

    gen_allocation = gen_nodes * gen_resource_nodes * gen_cpu
    vasp_allocation = vasp_nodes * vasp_resource_nodes * vasp_cpu
    details = {
        "mlp_workers": workers,
        "gen_opt_cpu_allocation": gen_allocation,
        "vasp_processing_cpu_allocation": vasp_allocation,
        "available_logical_cpus": cpu_count,
    }
    if gen_allocation > cpu_count or vasp_allocation > cpu_count:
        return "FAIL", details
    return "PASS", details


def _config_path_matches(value: Any, expected: Path) -> bool:
    if not isinstance(value, str):
        return False
    path = Path(value)
    if not path.is_absolute():
        return False
    try:
        return path.resolve(strict=True) == expected
    except (OSError, RuntimeError):
        return False


def _config_resources_match(value: Any, stage: StagePlan) -> bool:
    if not isinstance(value, str):
        return False
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() not in {".yaml", ".yml", ".json"}:
        return False
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            return False
        if resolved == stage.resources_path:
            return True
        parsed = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_UniqueYamlLoader)
        return (
            isinstance(parsed, dict)
            and json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            == json.dumps(stage.resources, sort_keys=True, separators=(",", ":"))
        )
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError):
        return False


def _valid_pairing(receipt: dict[str, Any], backend: str) -> bool:
    if backend not in {"deepmd", "dpa4", "dpa4_ion_ft"}:
        return True
    facts = receipt.get("facts")
    pairing = facts.get("pairing") if isinstance(facts, dict) else None
    actual = pairing.get("actual") if isinstance(pairing, dict) else None
    return (
        isinstance(actual, dict)
        and actual.get("deepmd-kit") == DEEPMD_TORCH_PAIR["deepmd-kit"]
        and actual.get("torch") == DEEPMD_TORCH_PAIR["torch"]
    )


def _receipt_checks(receipt: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    raw_checks = receipt.get("checks")
    if not isinstance(raw_checks, list):
        return None
    output: dict[str, dict[str, Any]] = {}
    for item in raw_checks:
        if not isinstance(item, dict) or not isinstance(item.get("check_id"), str):
            return None
        check_id = item["check_id"]
        if check_id in output:
            return None
        output[check_id] = item
    return output


def _check_mlp_receipt(
    receipt_path: str | Path | None,
    *,
    backend: str | None,
    configured_executable: Path | None,
    effective_model_path: Path | None,
    require_gpu: bool,
) -> tuple[str, str]:
    if receipt_path is None:
        return "NOT RUN", "No MLP preflight receipt was supplied."
    path = Path(receipt_path)
    if not path.is_absolute():
        return "FAIL", "The MLP preflight receipt path must be absolute."
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "FAIL", "The MLP preflight receipt could not be read as JSON."
    if not isinstance(receipt, dict):
        return "FAIL", "The MLP preflight receipt has an unsupported format."
    if receipt.get("target") != "mlp" or receipt.get("ok") is not True:
        return "FAIL", "The MLP preflight receipt did not pass for the MLP target."
    facts = receipt.get("facts")
    receipt_backend = facts.get("backend") if isinstance(facts, dict) else None
    if backend not in MLP_BACKENDS or receipt_backend != backend:
        return "FAIL", "The MLP receipt backend does not match the work directory."

    host = receipt.get("host")
    if not isinstance(host, dict):
        return "FAIL", "The MLP receipt is missing interpreter metadata."
    python_version = host.get("python_version")
    if not isinstance(python_version, str) or not python_version.startswith("3.12."):
        return "FAIL", "The MLP receipt does not report the documented Python 3.12 runtime."
    receipt_executable = host.get("python_executable")
    if (
        not isinstance(receipt_executable, str)
        or not Path(receipt_executable).is_absolute()
        or configured_executable is None
    ):
        return "FAIL", "The MLP receipt cannot be matched to gen_opt.mlp_python."
    receipt_interpreter = _invocation_path(Path(receipt_executable))
    same_executable = receipt_interpreter == configured_executable
    if not same_executable:
        return "FAIL", "The MLP receipt interpreter does not match gen_opt.mlp_python."

    checks = _receipt_checks(receipt)
    if checks is None:
        return "FAIL", "The MLP receipt checks are missing or ambiguous."
    required_ids = [
        "python.mlp_profile",
        "python.distribution.ase",
        "mlp.model",
    ]
    if backend in {"deepmd", "dpa4", "dpa4_ion_ft"}:
        required_ids.extend(
            (
                "python.import.deepmd",
                "python.distribution.deepmd-kit",
                "python.distribution.torch",
            )
        )
    else:
        required_ids.extend(
            (
                "python.import.mattersim",
                "python.distribution.mattersim",
                "python.distribution.torch",
            )
        )
    if require_gpu:
        required_ids.extend(("mlp.cuda", "mlp.nvidia_smi"))
        cuda = facts.get("cuda") if isinstance(facts, dict) else None
        if not isinstance(cuda, dict) or cuda.get("required") is not True or cuda.get("available") is not True:
            return "FAIL", "The GPU MLP receipt does not confirm CUDA visibility."
    if any(checks.get(check_id, {}).get("status") != "pass" for check_id in required_ids):
        return "FAIL", "One or more required MLP runtime or model checks did not pass."
    if not _valid_pairing(receipt, backend):
        return "FAIL", "The MLP receipt does not match the documented DeepMD/Torch version pair."
    if effective_model_path is None:
        return "NOT RUN", "The model staged by gen_opt could not be resolved."
    model_check = checks.get("mlp.model")
    model_details = model_check.get("details") if isinstance(model_check, dict) else None
    receipt_model_value = model_details.get("path") if isinstance(model_details, dict) else None
    if not isinstance(receipt_model_value, str) or not Path(receipt_model_value).is_absolute():
        return "NOT RUN", "The MLP receipt does not identify a readable model file."
    try:
        staged_model = effective_model_path.resolve(strict=True)
        receipt_model = Path(receipt_model_value).resolve(strict=True)
    except (OSError, RuntimeError):
        return "NOT RUN", "The staged or receipted model file is unavailable."
    staged_hash = _model_sha256(staged_model)
    if staged_hash is None:
        return "NOT RUN", "The staged model is empty, unreadable, or exceeds the 512 MiB hash limit."
    receipt_hash = (
        staged_hash
        if receipt_model == staged_model
        else _model_sha256(receipt_model)
    )
    if receipt_hash is None:
        return "NOT RUN", "The receipted model is unreadable or exceeds the 512 MiB hash limit."
    if receipt_hash != staged_hash:
        return "FAIL", "The receipted model bytes do not match the model staged by gen_opt."
    return "PASS", "The MLP receipt matches the configured interpreter and required runtime metadata."


def _query_gpu_driver(path: str) -> tuple[bool, str]:
    executable = shutil.which("nvidia-smi", path=path)
    if not executable:
        return False, "nvidia-smi is not visible in the selected gen_opt environment."
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=driver_version",
                "--format=csv,noheader",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "The bounded nvidia-smi driver query failed."
    if result.returncode != 0 or not result.stdout.strip():
        return False, "nvidia-smi did not confirm a visible GPU driver."
    return True, "nvidia-smi confirmed a visible GPU driver."


def _gpu_mask_compatibility(stage: StagePlan | None) -> tuple[str, str, dict[str, Any]]:
    if stage is None:
        return "NOT RUN", "The gen_opt GPU mask check requires a valid profile stage.", {}
    if stage.resource_class != "gpu":
        return "NOT RUN", "CUDA_VISIBLE_DEVICES masks are not used by CPU-only MLP.", {}
    try:
        from ion_CSP.gen_opt import _resolve_allowed_gpu_uuids

        allowed = _resolve_allowed_gpu_uuids(stage.resources.get("envs", {}))
    except Exception:
        return (
            "FAIL",
            "Controller and gen_opt CUDA_VISIBLE_DEVICES masks are invalid or incompatible.",
            {},
        )
    return (
        "PASS",
        "Controller and gen_opt CUDA_VISIBLE_DEVICES masks are compatible GPU UUID masks.",
        {"restricted": allowed is not None, "compatible_uuid_count": len(allowed or ())},
    )


def check_local_readiness(
    profile_path: str | Path,
    workdir_path: str | Path,
    mlp_receipt_path: str | Path | None = None,
    *,
    max_generation_attempts: int = DEFAULT_LOCAL_GENERATION_LIMIT,
) -> dict[str, Any]:
    """Return a credential-safe readiness report without launching science tools."""

    checks: list[dict[str, Any]] = []
    workdir = _resolved_workdir(workdir_path)
    config = _read_workdir_config(workdir)

    try:
        core_report = check_core(workflow="csp", work_dir=workdir)
        core_python_ok = _core_check_status(
            core_report,
            lambda check_id: check_id.startswith("python.") or check_id == "external.phonopy",
        ) == "PASS"
        workdir_config_ok = _core_check_status(
            core_report,
            lambda check_id: check_id == "work_dir" or check_id.startswith("config."),
        ) == "PASS"
    except Exception:
        core_python_ok = False
        workdir_config_ok = False

    _add_check(
        checks,
        "core_python",
        "PASS" if core_python_ok else "FAIL",
        (
            "Core Python 3.11 and required CSP dependencies are available."
            if core_python_ok
            else "Core Python 3.11 or a required CSP dependency is missing or mismatched."
        ),
    )
    _add_check(
        checks,
        "workdir_config",
        "PASS" if workdir_config_ok and workdir is not None else "FAIL",
        (
            "The work directory contains a valid CSP config.yaml and referenced inputs."
            if workdir_config_ok and workdir is not None
            else "The work directory or its CSP config.yaml is missing or invalid."
        ),
    )

    plan: ProfilePlan | None = None
    try:
        plan = load_profile(profile_path, gpu_probe=lambda _name: "deferred-nvidia-smi-query")
        profile_valid = plan.mode == "local" and all(
            any(stage.name == required for stage in plan.stages)
            for required in REQUIRED_CSP_STAGES
        )
    except (ProfileError, OSError, RuntimeError, ValueError):
        profile_valid = False
    if profile_valid:
        _add_check(
            checks,
            "local_profile",
            "PASS",
            "The local profile validates and includes gen_opt plus CPU VASP processing.",
        )
    else:
        _add_check(
            checks,
            "local_profile",
            "FAIL",
            "A valid local profile with gen_opt and vasp_processing stages is required.",
        )

    stages = {stage.name: stage for stage in plan.stages} if plan is not None else {}
    gen_stage = stages.get("gen_opt")
    vasp_stage = stages.get("vasp_processing")
    gen_config = _stage_mapping(config, "gen_opt")
    vasp_config = _stage_mapping(config, "vasp_processing")
    workload = "UNDETERMINED"
    if gen_stage is not None and vasp_stage is not None:
        workload = (
            "GPU_MLP_PLUS_CPU_VASP"
            if gen_stage.resource_class == "gpu"
            else "CPU_MLP_PLUS_CPU_VASP"
        )
    paths_match = bool(
        gen_stage is not None
        and vasp_stage is not None
        and gen_config is not None
        and vasp_config is not None
        and _config_path_matches(gen_config.get("machine"), gen_stage.machine_path)
        and _config_resources_match(gen_config.get("resources"), gen_stage)
        and _config_path_matches(vasp_config.get("machine"), vasp_stage.machine_path)
        and _config_resources_match(vasp_config.get("resources"), vasp_stage)
    )
    _add_check(
        checks,
        "profile_workdir_match",
        "PASS" if paths_match else "FAIL",
        (
            "The work directory machine/resources paths match the validated profile."
            if paths_match
            else "The work directory machine/resources paths must match the local profile."
        ),
    )

    cpu_status, cpu_details = _local_cpu_budget(
        config,
        gen_stage,
        vasp_stage,
        cpu_count=os.cpu_count(),
    )
    if cpu_status == "PASS":
        _add_check(
            checks,
            "local_cpu_budget",
            "PASS",
            "MLP workers and per-stage CPU allocations fit the local workstation.",
            details=cpu_details,
        )
    elif cpu_status == "FAIL":
        _add_check(
            checks,
            "local_cpu_budget",
            "FAIL",
            "Use positive mlp_workers within cpu_per_node, one worker for GPU MLP, and per-stage CPU allocations within os.cpu_count().",
            details=cpu_details,
        )
    else:
        _add_check(
            checks,
            "local_cpu_budget",
            "NOT RUN",
            "The selected local profile or workstation CPU count is unavailable.",
        )

    budget_status, generation_scale, budget_fields = _workload_budget(
        config, generation_limit=max_generation_attempts
    )
    if budget_status == "PASS":
        _add_check(
            checks,
            "local_workload_budget",
            "PASS",
            "Explicit CSP workload limits are valid; this check does not launch the workload.",
            details={
                "attempted_generation_scale": generation_scale,
                "generation_limit": max_generation_attempts,
                "n_screen": budget_fields["n_screen"],
                "gen_opt_nodes": budget_fields["gen_opt_nodes"],
                "vasp_processing_nodes": budget_fields["vasp_processing_nodes"],
            },
        )
    elif budget_status == "FAIL":
        _add_check(
            checks,
            "local_workload_budget",
            "FAIL",
            "CSP workload scale must fit the bounded local limit (at most 1,000) and all fields must be valid.",
            details={
                "attempted_generation_scale": generation_scale,
                "generation_limit": max_generation_attempts,
            },
        )
    else:
        _add_check(
            checks,
            "local_workload_budget",
            "NOT RUN",
            "Explicit CSP workload limits are required; defaults are not assumed.",
        )

    backend_value = gen_config.get("mlp_backend", "deepmd") if gen_config else None
    backend = backend_value if isinstance(backend_value, str) and backend_value in MLP_BACKENDS else None
    effective_model_path = _effective_model_path(config, backend)
    configured_python = gen_config.get("mlp_python", "python") if gen_config else None
    gen_path = _profile_environment_path(gen_stage)
    mlp_executable = _resolve_executable(configured_python, path=gen_path)
    _add_check(
        checks,
        "mlp_python_visible",
        "PASS" if mlp_executable is not None else "FAIL",
        (
            "gen_opt.mlp_python is visible in the selected gen_opt environment."
            if mlp_executable is not None
            else "gen_opt.mlp_python is not visible in the selected gen_opt environment."
        ),
    )

    gpu_required = gen_stage is not None and gen_stage.resource_class == "gpu"
    mask_status, mask_message, mask_details = _gpu_mask_compatibility(gen_stage)
    _add_check(
        checks,
        "gpu_mask_compatibility",
        mask_status,
        mask_message,
        required=gpu_required,
        details=mask_details,
    )
    if gen_stage is None:
        gpu_guard_status = "NOT RUN"
        gpu_guard_message = "The GPU allocation guard requires a valid gen_opt profile stage."
    elif gpu_required:
        one_gpu_node = (
            gen_stage.resources.get("gpu_per_node") == 1
            and gen_config is not None
            and type(gen_config.get("nodes")) is int
            and gen_config.get("nodes") == 1
        )
        gpu_guard_status = "PASS" if one_gpu_node else "FAIL"
        gpu_guard_message = (
            "GPU MLP uses one workflow node and exactly one GPU per profile node."
            if one_gpu_node
            else "GPU MLP requires gen_opt.nodes == 1 and resources.gpu_per_node == 1."
        )
    else:
        gpu_guard_status = "PASS"
        gpu_guard_message = "The single-GPU allocation guard does not apply to CPU-only MLP."
    _add_check(checks, "gpu_allocation_guard", gpu_guard_status, gpu_guard_message)

    if gpu_required and mask_status == "PASS":
        gpu_ok, gpu_message = _query_gpu_driver(gen_path)
        _add_check(
            checks,
            "gpu_driver",
            "PASS" if gpu_ok else "FAIL",
            gpu_message,
            details={"timeout_seconds": NVIDIA_SMI_TIMEOUT_SECONDS},
        )
    elif gpu_required:
        _add_check(
            checks,
            "gpu_driver",
            "NOT RUN",
            "The GPU driver query was skipped because the CUDA_VISIBLE_DEVICES masks are invalid.",
        )
    elif gen_stage is not None:
        _add_check(
            checks,
            "gpu_driver",
            "PASS",
            "No GPU driver query is required for CPU-only MLP.",
            required=False,
        )
    else:
        _add_check(checks, "gpu_driver", "NOT RUN", "The gen_opt resource class is unavailable.")

    if vasp_stage is not None:
        vasp_path = _profile_environment_path(vasp_stage)
        vasp_visible = shutil.which("vasp_std", path=vasp_path) is not None
        mpi_visible = shutil.which("mpirun", path=vasp_path) is not None
        _add_check(
            checks,
            "vasp_executable",
            "PASS" if vasp_visible else "FAIL",
            (
                "vasp_std is visible in the selected VASP environment."
                if vasp_visible
                else "vasp_std is not visible in the selected VASP environment."
            ),
        )
        _add_check(
            checks,
            "mpi_executable",
            "PASS" if mpi_visible else "FAIL",
            (
                "mpirun is visible in the selected VASP environment."
                if mpi_visible
                else "mpirun is not visible in the selected VASP environment."
            ),
        )
    else:
        _add_check(checks, "vasp_executable", "NOT RUN", "The vasp_processing profile stage is unavailable.")
        _add_check(checks, "mpi_executable", "NOT RUN", "The vasp_processing profile stage is unavailable.")

    receipt_status, receipt_message = _check_mlp_receipt(
        mlp_receipt_path,
        backend=backend,
        configured_executable=mlp_executable,
        effective_model_path=effective_model_path,
        require_gpu=gpu_required,
    )
    _add_check(checks, "mlp_receipt", receipt_status, receipt_message)
    _add_check(
        checks,
        "vasp_abi_and_science",
        "NOT RUN",
        "Executable visibility does not verify MPI ABI compatibility or scientific correctness.",
        required=False,
    )

    required_statuses = [item["status"] for item in checks if item["required"]]
    if any(status == "FAIL" for status in required_statuses):
        overall = "FAIL"
    elif any(status == "NOT RUN" for status in required_statuses):
        overall = "NOT RUN"
    else:
        overall = "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "local_workstation",
        "status": overall,
        "ready_for_bounded_smoke": overall == "PASS",
        "workload": workload,
        "requested_generation_scale": generation_scale,
        "checks": checks,
        "limitations": [
            "The checker never launches mlp_python, a model, VASP, or MPI.",
            "Executable visibility does not prove MPI ABI compatibility or scientific correctness.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only local ion_CSP CSP workstation readiness check."
    )
    parser.add_argument("--profile", required=True, type=Path, help="absolute local compute profile")
    parser.add_argument("--workdir", required=True, type=Path, help="absolute CSP work directory")
    parser.add_argument(
        "--mlp-receipt",
        type=Path,
        help="JSON receipt created by ion-csp-preflight --target mlp in the configured MLP environment",
    )
    parser.add_argument(
        "--max-generation-attempts",
        type=int,
        default=DEFAULT_LOCAL_GENERATION_LIMIT,
        help="maximum num_per_group * space_groups_limit (default: 100; hard maximum: 1000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = check_local_readiness(
        args.profile,
        args.workdir,
        args.mlp_receipt,
        max_generation_attempts=args.max_generation_attempts,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "NOT RUN": 2}[result["status"]]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
