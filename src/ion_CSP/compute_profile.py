"""Offline validation and rendering for customer-owned compute profiles.

This module deliberately does not construct a dpdispatcher Machine, establish
an SSH connection, execute ``nvidia-smi``, or submit a job. It reads only the
small set of machine fields needed to check the declared execution mode and
writes private, non-overwriting resource files for a single owner/run pair.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml


SCHEMA_VERSION = 1
STAGE_NAMES = ("convert_SMILES", "gen_opt", "vasp_processing")
PROFILE_KEYS = {"schema_version", "owner_id", "run_id", "mode", "scheduler", "stages"}
STAGE_KEYS = {"resource_class", "machine", "resources"}
MACHINE_SUFFIXES = {".yaml", ".json"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
SECRET_MARKER_RE = re.compile(
    r"(?i)(?:password|passwd|secret|token|credential|authorization|bearer|private[ _-]?key)\s*[:=]"
)
SECRET_WORD_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:password|passwd|secret|token|credential|authorization|bearer|private[ _-]?key)(?:$|[^A-Za-z0-9])"
)
URL_CREDENTIAL_RE = re.compile(r"(?i)https?://[^/@\s:]+:[^/@\s]+@")
REMOTE_ROOT_RE = re.compile(r"^/[A-Za-z0-9_./:+%\-]*$")
SAFE_SHELL_VALUE_RE = re.compile(r"^(?:[A-Za-z0-9_./:+%=-]|\$PATH(?=[:/]|$))*$")
SAFE_RESOURCE_KEYS = {
    "number_node",
    "cpu_per_node",
    "gpu_per_node",
    "group_size",
    "queue_name",
    "custom_flags",
    "para_deg",
    "module_purge",
    "module_unload_list",
    "module_list",
    "source_list",
    "wait_time",
    "strategy",
    "envs",
}
SAFE_ENV_NAMES = {
    "PATH",
    "LD_LIBRARY_PATH",
    "CUDA_HOME",
    "CUDA_PATH",
    "CUDA_VISIBLE_DEVICES",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VASP_PP_PATH",
    "GAUSS_SCRDIR",
    "TMPDIR",
    "PYTHONPATH",
}
SAFE_SLURM_FLAGS = {
    "--account",
    "-A",
    "--partition",
    "-p",
    "--time",
    "-t",
    "--mem",
    "--mem-per-cpu",
    "--constraint",
    "--exclude",
    "--nodelist",
    "--output",
    "-o",
    "--error",
    "-e",
    "--qos",
    "--reservation",
    "--signal",
    "--hint",
    "--nice",
    "--begin",
    "--requeue",
    "--exclusive",
}
FLAG_ALIASES = {
    "-A": "--account",
    "-p": "--partition",
    "-t": "--time",
    "-o": "--output",
    "-e": "--error",
}
VALUELESS_SLURM_FLAGS = {"--requeue", "--exclusive"}
RESERVED_SLURM_FLAGS = {"-J", "--job-name", "--comment"}
RESOURCE_OWNED_SLURM_FLAGS = {
    "-N",
    "--nodes",
    "-n",
    "--ntasks",
    "-c",
    "--cpus-per-task",
    "--gres",
}
SAFE_FLAG_TEXT_RE = re.compile(r"^[A-Za-z0-9_#=:+,./%\-\[\] \t]+$")


class ProfileError(ValueError):
    """A safe, user-facing profile validation error."""


@dataclass(frozen=True)
class StagePlan:
    name: str
    resource_class: str
    machine_path: Path
    resources_path: Path
    machine: dict[str, Any]
    resources: dict[str, Any]


@dataclass(frozen=True)
class ProfilePlan:
    owner_id: str
    run_id: str
    mode: str
    scheduler: str
    stages: tuple[StagePlan, ...]


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys instead of hiding them."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise ProfileError("profile mappings must use scalar keys") from error
        if duplicate:
            raise ProfileError("profile contains a duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _parse_document(path: Path) -> Any:
    """Read YAML or JSON without exposing parser details or file contents."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ProfileError("a profile, machine, or resources file cannot be read") from error
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text, object_pairs_hook=_unique_json_object)
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.load(text, Loader=_UniqueKeyLoader)
    except ProfileError:
        raise
    except (ValueError, yaml.YAMLError) as error:
        # Parser exceptions can include source lines that contain credentials.
        raise ProfileError("a profile, machine, or resources file is not valid YAML/JSON") from error
    raise ProfileError("profile, machine, and resources files must use .yaml or .json")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("profile contains a duplicate mapping key")
        result[key] = value
    return result


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ProfileError("profile, machine, and resources documents must be mappings")
    return value


def _check_keys(mapping: dict[str, Any], allowed: set[str], required: set[str]) -> None:
    if set(mapping) - allowed or required - set(mapping):
        raise ProfileError("profile contains missing or unsupported fields")


def _identifier(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not IDENTIFIER_RE.fullmatch(value)
        or value in {".", ".."}
        or _contains_credential_material(value)
    ):
        raise ProfileError("owner_id and run_id must be explicit safe identifiers")
    return value


def _absolute_file(value: Any, *, role: str) -> Path:
    if not isinstance(value, str):
        raise ProfileError(f"stage {role} path must be an absolute YAML or JSON file")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() not in MACHINE_SUFFIXES:
        raise ProfileError(f"stage {role} path must be an absolute YAML or JSON file")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProfileError(f"stage {role} file is unavailable") from error
    if not resolved.is_file():
        raise ProfileError(f"stage {role} file is unavailable")
    return resolved


def _strict_int(value: Any, *, minimum: int, message: str) -> int:
    if type(value) is not int or value < minimum:
        raise ProfileError(message)
    return value


def _validate_identifier_text(value: Any, *, message: str, max_length: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or _contains_credential_material(value)
    ):
        raise ProfileError(message)
    return value


def _normalize_custom_flags(value: Any, *, mode: str, queue_name: str | None) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(flag, str) for flag in value):
        raise ProfileError("resources.custom_flags must be a list of safe single-line flags")
    if mode == "local" and value:
        raise ProfileError("local profiles cannot contain scheduler custom_flags")

    flags: list[str] = []
    seen: set[str] = set()
    partition_seen = False
    for raw_flag in value:
        flag = raw_flag.strip()
        if (
            not flag
            or "\n" in flag
            or "\r" in flag
            or not SAFE_FLAG_TEXT_RE.fullmatch(flag)
            or not flag.startswith("#SBATCH ")
        ):
            raise ProfileError("resources.custom_flags must contain safe #SBATCH directives")
        tokens = flag.split()
        if not tokens or tokens[0] != "#SBATCH":
            raise ProfileError("resources.custom_flags must contain safe #SBATCH directives")
        index = 1
        while index < len(tokens):
            token = tokens[index]
            option, separator, inline_value = token.partition("=")
            if option in RESERVED_SLURM_FLAGS:
                raise ProfileError("custom_flags cannot override generated job-name or comment tags")
            if option in RESOURCE_OWNED_SLURM_FLAGS:
                raise ProfileError("custom_flags cannot override resource allocation fields")
            if option not in SAFE_SLURM_FLAGS:
                raise ProfileError("custom_flags contains an unsupported Slurm option")
            canonical = FLAG_ALIASES.get(option, option)
            if canonical in seen:
                raise ProfileError("custom_flags contains duplicate or conflicting Slurm options")
            seen.add(canonical)
            if canonical == "--partition":
                partition_seen = True
            if canonical in VALUELESS_SLURM_FLAGS:
                if separator:
                    raise ProfileError("custom_flags contains an invalid valueless Slurm option")
                index += 1
                continue
            if separator:
                argument = inline_value
            else:
                index += 1
                if index >= len(tokens):
                    raise ProfileError("custom_flags contains a Slurm option without a value")
                argument = tokens[index]
            if (
                not argument
                or argument.startswith("-")
                or not SAFE_FLAG_TEXT_RE.fullmatch(argument)
                or _contains_credential_material(argument)
            ):
                raise ProfileError("custom_flags contains an unsafe Slurm option value")
            index += 1
        flags.append(flag)
    if queue_name and partition_seen:
        raise ProfileError("queue_name and custom_flags cannot both set a Slurm partition")
    return flags


def _clean_resources(raw: dict[str, Any], *, resource_class: str, mode: str) -> dict[str, Any]:
    if set(raw) - SAFE_RESOURCE_KEYS:
        raise ProfileError("resources file contains unsupported or unsafe fields")
    number_node = _strict_int(
        raw.get("number_node"), minimum=1, message="resources.number_node must be positive"
    )
    cpu_per_node = _strict_int(
        raw.get("cpu_per_node"), minimum=1, message="resources.cpu_per_node must be positive"
    )
    gpu_per_node = _strict_int(
        raw.get("gpu_per_node", 0), minimum=0, message="resources.gpu_per_node must be non-negative"
    )
    group_size = _strict_int(
        raw.get("group_size"), minimum=1, message="resources.group_size must be positive"
    )
    if resource_class == "gpu" and gpu_per_node < 1:
        raise ProfileError("GPU stages require resources.gpu_per_node >= 1")
    if resource_class == "cpu" and gpu_per_node != 0:
        raise ProfileError("CPU stages require resources.gpu_per_node == 0")

    output: dict[str, Any] = {
        "number_node": number_node,
        "cpu_per_node": cpu_per_node,
        "gpu_per_node": gpu_per_node,
        "group_size": group_size,
    }
    queue_name: str | None = None
    if mode == "remote_slurm" and raw.get("queue_name") is not None:
        queue_name = _validate_identifier_text(
            raw["queue_name"], message="resources.queue_name must be a safe scheduler name", max_length=128
        )
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", queue_name):
            raise ProfileError("resources.queue_name must be a safe scheduler name")
        output["queue_name"] = queue_name
    elif mode == "local" and raw.get("queue_name") is not None:
        queue_name = _validate_identifier_text(
            raw["queue_name"], message="resources.queue_name must be a safe label", max_length=128
        )
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", queue_name):
            raise ProfileError("resources.queue_name must be a safe label")

    if "para_deg" in raw:
        output["para_deg"] = _strict_int(
            raw["para_deg"], minimum=1, message="resources.para_deg must be positive"
        )
    if "wait_time" in raw:
        output["wait_time"] = _strict_int(
            raw["wait_time"], minimum=0, message="resources.wait_time must be non-negative"
        )
    if "module_purge" in raw:
        if type(raw["module_purge"]) is not bool:
            raise ProfileError("resources.module_purge must be a boolean")
        output["module_purge"] = raw["module_purge"]
    for key in ("module_list", "module_unload_list"):
        if key in raw:
            values = raw[key]
            if not isinstance(values, list) or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[A-Za-z0-9_.+/-]{1,256}", item)
                for item in values
            ):
                raise ProfileError(f"resources.{key} must contain safe module names")
            output[key] = list(values)
    if "source_list" in raw:
        values = raw["source_list"]
        if not isinstance(values, list) or any(
            not isinstance(item, str)
            or not Path(item).is_absolute()
            or not SAFE_SHELL_VALUE_RE.fullmatch(item)
            or _contains_credential_material(item)
            for item in values
        ):
            raise ProfileError("resources.source_list must contain absolute non-secret paths")
        output["source_list"] = list(values)
    if "strategy" in raw:
        strategy = raw["strategy"]
        if not isinstance(strategy, dict) or set(strategy) - {
            "if_cuda_multi_devices",
            "ratio_unfinished",
        }:
            raise ProfileError("resources.strategy contains unsupported fields")
        cleaned_strategy: dict[str, Any] = {}
        if "if_cuda_multi_devices" in strategy:
            value = strategy["if_cuda_multi_devices"]
            if type(value) is not bool:
                raise ProfileError("resources.strategy.if_cuda_multi_devices must be a boolean")
            if value and (gpu_per_node < 1 or number_node != 1):
                raise ProfileError("multi-GPU task strategy requires one GPU node")
            cleaned_strategy["if_cuda_multi_devices"] = value
        if "ratio_unfinished" in strategy:
            value = strategy["ratio_unfinished"]
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value < 1:
                raise ProfileError("resources.strategy.ratio_unfinished must be between 0 and 1")
            cleaned_strategy["ratio_unfinished"] = value
        output["strategy"] = cleaned_strategy
    if "envs" in raw:
        envs = raw["envs"]
        if not isinstance(envs, dict):
            raise ProfileError("resources.envs must be a mapping of approved non-secret variables")
        cleaned_envs: dict[str, str] = {}
        for key, value in envs.items():
            if (
                not isinstance(key, str)
                or key not in SAFE_ENV_NAMES
                or not isinstance(value, str)
                or not value
                or "\n" in value
                or "\r" in value
                or not SAFE_SHELL_VALUE_RE.fullmatch(value)
                or _contains_credential_material(value)
            ):
                raise ProfileError("resources.envs contains an unsupported or unsafe value")
            cleaned_envs[key] = value
        output["envs"] = cleaned_envs

    flags = _normalize_custom_flags(
        raw.get("custom_flags", []), mode=mode, queue_name=queue_name
    )
    if mode == "remote_slurm":
        output["custom_flags"] = flags
    return output


def _contains_credential_material(value: str) -> bool:
    return bool(
        SECRET_MARKER_RE.search(value)
        or SECRET_WORD_RE.search(value)
        or URL_CREDENTIAL_RE.search(value)
        or "-----BEGIN " in value
    )


def _read_machine(path: Path, *, mode: str, scheduler: str) -> dict[str, Any]:
    machine = _mapping(_parse_document(path))
    context_type = machine.get("context_type")
    batch_type = machine.get("batch_type")
    if mode == "remote_slurm":
        valid = context_type == "SSHContext" and isinstance(batch_type, str) and batch_type.lower() == "slurm"
    else:
        valid = context_type == "LocalContext" and isinstance(batch_type, str) and batch_type.lower() == "shell"
    if not valid:
        raise ProfileError("machine context_type and batch_type do not match the selected mode/scheduler")
    if scheduler != ("slurm" if mode == "remote_slurm" else "none"):
        raise ProfileError("scheduler does not match the selected mode")
    if mode == "remote_slurm":
        remote_root = machine.get("remote_root")
        if not isinstance(remote_root, str) or not REMOTE_ROOT_RE.fullmatch(remote_root):
            raise ProfileError(
                "remote Slurm machine remote_root must be absolute and contain only safe path characters"
            )
        remote_profile = machine.get("remote_profile")
        if not isinstance(remote_profile, dict):
            raise ProfileError("remote Slurm machine requires a remote_profile mapping")
        for key in ("hostname", "username"):
            identity = remote_profile.get(key)
            if (
                not isinstance(identity, str)
                or not identity.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in identity)
            ):
                raise ProfileError(
                    "remote Slurm machine remote_profile requires non-empty hostname and username"
                )
    # Returning only these non-secret fields prevents accidental serialization
    # of machine.remote_profile (which commonly contains a host and login data).
    return {"context_type": context_type, "batch_type": batch_type}


def load_profile(
    profile_path: str | Path,
    *,
    gpu_probe: Callable[[str], str | None] | None = None,
) -> ProfilePlan:
    """Validate a site profile and all referenced files without external effects."""
    profile_file = Path(profile_path)
    if not profile_file.is_absolute() or profile_file.suffix.lower() not in {".yaml", ".json"}:
        raise ProfileError("profile path must be an absolute .yaml or .json file")
    try:
        profile_file = profile_file.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ProfileError("profile file is unavailable") from error
    profile = _mapping(_parse_document(profile_file))
    _check_keys(profile, PROFILE_KEYS, PROFILE_KEYS)
    if type(profile["schema_version"]) is not int or profile["schema_version"] != SCHEMA_VERSION:
        raise ProfileError("unsupported compute-profile schema_version")
    owner_id = _identifier(profile["owner_id"])
    run_id = _identifier(profile["run_id"])
    mode = profile["mode"]
    scheduler = profile["scheduler"]
    if not isinstance(mode, str) or mode not in {"remote_slurm", "local"}:
        raise ProfileError("mode must be remote_slurm or local")
    expected_scheduler = "slurm" if mode == "remote_slurm" else "none"
    if scheduler != expected_scheduler:
        raise ProfileError("scheduler does not match the selected mode")
    stages_mapping = _mapping(profile["stages"])
    if not stages_mapping or set(stages_mapping) - set(STAGE_NAMES):
        raise ProfileError("stages must contain supported ion_CSP stages")

    probe = gpu_probe or shutil.which
    stages: list[StagePlan] = []
    for name in STAGE_NAMES:
        if name not in stages_mapping:
            continue
        stage_mapping = _mapping(stages_mapping[name])
        _check_keys(stage_mapping, STAGE_KEYS, STAGE_KEYS)
        resource_class = stage_mapping["resource_class"]
        if not isinstance(resource_class, str):
            raise ProfileError(f"stage {name} resource_class must be a string")
        if name in {"convert_SMILES", "vasp_processing"} and resource_class != "cpu":
            raise ProfileError(f"stage {name} must use resource_class cpu")
        if name == "gen_opt" and resource_class not in {"cpu", "gpu"}:
            raise ProfileError("stage gen_opt resource_class must be cpu or gpu")
        machine_path = _absolute_file(stage_mapping["machine"], role="machine")
        resources_path = _absolute_file(stage_mapping["resources"], role="resources")
        machine = _read_machine(machine_path, mode=mode, scheduler=scheduler)
        raw_resources = _mapping(_parse_document(resources_path))
        resources = _clean_resources(raw_resources, resource_class=resource_class, mode=mode)
        if mode == "local" and resource_class == "gpu" and probe("nvidia-smi") is None:
            raise ProfileError("local GPU mode requires nvidia-smi to be available on PATH")
        stages.append(
            StagePlan(
                name=name,
                resource_class=resource_class,
                machine_path=machine_path,
                resources_path=resources_path,
                machine=machine,
                resources=resources,
            )
        )
    return ProfilePlan(
        owner_id=owner_id,
        run_id=run_id,
        mode=mode,
        scheduler=scheduler,
        stages=tuple(stages),
    )


def _tagged_resources(plan: ProfilePlan, stage: StagePlan) -> dict[str, Any]:
    result = dict(stage.resources)
    if plan.mode == "remote_slurm":
        role = stage.resource_class
        job_name = f"icsp-{plan.owner_id}-{plan.run_id}-{role}-{stage.name}"
        comment = f"ioncsp.owner.{plan.owner_id}.run.{plan.run_id}.role.{role}.stage.{stage.name}"
        flags = list(result.get("custom_flags", []))
        flags.extend((f"#SBATCH --job-name={job_name}", f"#SBATCH --comment={comment}"))
        result["custom_flags"] = flags
    return result


def _exclusive_write(path: Path, contents: str) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(contents)
    except FileExistsError as error:
        raise ProfileError("refusing to overwrite an existing compute-profile output") from error
    except OSError as error:
        raise ProfileError("unable to write a new compute-profile output") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def render_profile(
    profile_path: str | Path,
    output_dir: str | Path,
    *,
    gpu_probe: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Render new resources and a config fragment under an exclusive run dir."""
    plan = load_profile(profile_path, gpu_probe=gpu_probe)
    output_root = Path(output_dir)
    if not output_root.is_absolute():
        raise ProfileError("output-dir must be absolute")
    run_dir = output_root / f"{plan.owner_id}__{plan.run_id}"
    resources_dir = run_dir / "resources"
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(mode=0o700, exist_ok=False)
        resources_dir.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as error:
        raise ProfileError("refusing to overwrite an existing compute-profile run directory") from error
    except OSError as error:
        raise ProfileError("unable to create a new compute-profile run directory") from error

    config_fragment: dict[str, Any] = {}
    resource_paths: dict[str, str] = {}
    for stage in plan.stages:
        filename = f"{stage.name}.resources.yaml"
        resource_path = resources_dir / filename
        resource_data = _tagged_resources(plan, stage)
        _exclusive_write(
            resource_path,
            yaml.safe_dump(resource_data, sort_keys=False, allow_unicode=True),
        )
        config_fragment[stage.name] = {
            "machine": str(stage.machine_path),
            "resources": str(resource_path.resolve()),
        }
        resource_paths[stage.name] = str(resource_path.resolve())

    fragment_path = run_dir / "config.compute-profile.yaml"
    _exclusive_write(
        fragment_path,
        yaml.safe_dump(config_fragment, sort_keys=False, allow_unicode=True),
    )
    receipt_path = run_dir / "receipt.json"
    receipt = {
        "identifiers": {"owner_id": plan.owner_id, "run_id": plan.run_id},
        "outputs": {
            "config_fragment": str(fragment_path.resolve()),
            "stages": [
                {
                    "stage": stage.name,
                    "role": stage.resource_class,
                    "resources": resource_paths[stage.name],
                }
                for stage in plan.stages
            ],
        },
    }
    _exclusive_write(receipt_path, json.dumps(receipt, indent=2) + "\n")
    return receipt


def _validation_receipt(plan: ProfilePlan) -> dict[str, Any]:
    return {
        "ok": True,
        "identifiers": {"owner_id": plan.owner_id, "run_id": plan.run_id},
        "stages": [
            {"stage": stage.name, "role": stage.resource_class}
            for stage in plan.stages
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ion-csp-profile",
        description="Validate or render an offline ion_CSP machine/resources profile.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="validate profile and referenced files")
    validate_parser.add_argument("profile", help="absolute site-profile YAML/JSON path")
    render_parser = subparsers.add_parser("render", help="write non-overwriting per-run resources")
    render_parser.add_argument("profile", help="absolute site-profile YAML/JSON path")
    render_parser.add_argument(
        "--output-dir", required=True, help="absolute parent directory for owner/run output"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = _validation_receipt(load_profile(args.profile))
        else:
            result = render_profile(args.profile, args.output_dir)
    except ProfileError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
