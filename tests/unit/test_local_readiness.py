from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ion_CSP import local_readiness
from ion_CSP.compute_profile import render_profile

GPU_UUID_1 = "GPU-11111111-1111-1111-1111-111111111111"
GPU_UUID_2 = "GPU-22222222-2222-2222-2222-222222222222"


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path.resolve()


def _profile_and_workdir(
    root: Path,
    *,
    gpu: bool,
    nodes: int = 1,
    profile_gpus: int | None = None,
    mlp_workers: int = 1,
    gpu_mask: str | None = None,
) -> tuple[Path, Path, Path]:
    workdir = root / "workdir"
    workdir.mkdir(parents=True)
    mlp_bin = root / "selected" / "mlp" / "bin"
    vasp_bin = root / "selected" / "vasp" / "bin"
    for executable in (
        mlp_bin / "python-mlp",
        mlp_bin / "nvidia-smi",
        vasp_bin / "vasp_std",
        vasp_bin / "mpirun",
        vasp_bin / "mpiexec",
    ):
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("mock executable; never launched\n", encoding="utf-8")
        executable.chmod(0o755)
    machine = _write_yaml(
        root / "machine.yaml",
        {"context_type": "LocalContext", "batch_type": "Shell", "local_root": "./"},
    )
    mlp_envs = {"PATH": f"{mlp_bin}:$PATH"}
    if gpu_mask is not None:
        mlp_envs["CUDA_VISIBLE_DEVICES"] = gpu_mask
    mlp_resources = _write_yaml(
        root / "mlp-resources.yaml",
        {
            "number_node": 1,
            "cpu_per_node": 8,
            "gpu_per_node": profile_gpus if profile_gpus is not None else int(gpu),
            "group_size": 1,
            "queue_name": "local",
            "envs": mlp_envs,
        },
    )
    vasp_resources = _write_yaml(
        root / "vasp-resources.yaml",
        {
            "number_node": 1,
            "cpu_per_node": 8,
            "gpu_per_node": 0,
            "group_size": 1,
            "queue_name": "local",
            "envs": {"PATH": f"{vasp_bin}:$PATH"},
        },
    )
    profile = _write_yaml(
        root / "profile.yaml",
        {
            "schema_version": 1,
            "owner_id": "customer",
            "run_id": "local-01",
            "mode": "local",
            "scheduler": "none",
            "stages": {
                "gen_opt": {
                    "resource_class": "gpu" if gpu else "cpu",
                    "machine": str(machine),
                    "resources": str(mlp_resources),
                },
                "vasp_processing": {
                    "resource_class": "cpu",
                    "machine": str(machine),
                    "resources": str(vasp_resources),
                },
            },
        },
    )
    (workdir / "ion.gjf").write_text("%chk=ion.chk\n", encoding="utf-8")
    _write_yaml(
        workdir / "config.yaml",
        {
            "gen_opt": {
                "species": ["ion.gjf"],
                "ion_numbers": [1],
                "nodes": nodes,
                "num_per_group": 2,
                "space_groups_limit": 3,
                "mlp_workers": mlp_workers,
                "mlp_backend": "deepmd",
                "mlp_python": "python-mlp",
                "mlp_model": "model.pt",
                "machine": str(machine),
                "resources": str(mlp_resources),
            },
            "read_mlp_density": {"n_screen": 1},
            "vasp_processing": {
                "nodes": 1,
                "machine": str(machine),
                "resources": str(vasp_resources),
            },
        },
    )
    return profile, workdir.resolve(), root / "mlp-receipt.json"


def _mlp_receipt(
    path: Path,
    *,
    python: str | None = None,
    gpu: bool = False,
    backend: str = "deepmd",
    model_path: Path | None = None,
) -> Path:
    if python is None:
        python = str(path.parent / "selected" / "mlp" / "bin" / "python-mlp")
    if model_path is None:
        model_path = Path(local_readiness.__file__).resolve().parent / "model" / "model.pt"
    check_ids = [
        "python.mlp_profile",
        "python.distribution.ase",
        "python.import.deepmd",
        "python.distribution.deepmd-kit",
        "python.distribution.torch",
        "mlp.model",
    ]
    if gpu:
        check_ids.extend(("mlp.cuda", "mlp.nvidia_smi"))
    receipt = {
        "schema_version": 1,
        "target": "mlp",
        "workflow": "csp",
        "ok": True,
        "host": {"python_executable": python, "python_version": "3.12.4"},
        "facts": {
            "backend": backend,
            "pairing": {
                "expected": {"python": "3.12", "deepmd-kit": "3.2.0", "torch": "2.11.0"},
                "actual": {"deepmd-kit": "3.2.0", "torch": "2.11.0"},
            },
            "cuda": {"required": gpu, "available": gpu},
        },
        "checks": [
            {
                "check_id": check_id,
                "status": "pass",
                **(
                    {"details": {"path": str(model_path.resolve())}}
                    if check_id == "mlp.model"
                    else {}
                ),
            }
            for check_id in check_ids
        ],
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path.resolve()


def _mock_core(monkeypatch):
    expected_ids = [
        "python.release_profile",
        "python.import.ion_CSP",
        "python.import.pyxtal",
        "external.phonopy",
        "work_dir",
        "config.file",
        "config.yaml_parse",
        "config.section.gen_opt",
        "config.section.read_mlp_density",
        "config.section.vasp_processing",
        "config.csp.mlp_backend",
        "config.csp.gen_opt.nodes",
        "config.csp.vasp_processing.nodes",
    ]
    report = SimpleNamespace(
        checks=[
            SimpleNamespace(check_id=check_id, required=True, status="pass")
            for check_id in expected_ids
        ]
    )
    monkeypatch.setattr(local_readiness, "check_core", lambda **_kwargs: report)
    monkeypatch.setattr(local_readiness.os, "cpu_count", lambda: 64)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)


def _mock_executables(monkeypatch, *, missing: set[str] | None = None):
    missing = missing or set()

    def which(command: str, path: str | None = None):
        if command in missing:
            return None
        if path is None:
            return None
        for directory in path.split(os.pathsep):
            candidate = Path(directory) / command
            if candidate.is_file():
                return str(candidate)
        return None

    monkeypatch.setattr(local_readiness.shutil, "which", which)


def _status(result: dict, check_id: str) -> str:
    return next(item["status"] for item in result["checks"] if item["check_id"] == check_id)


def test_cpu_mlp_and_cpu_vasp_pass_without_network_or_scientific_execution(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not execute a command"),
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("readiness must not connect to a network"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "PASS"
    assert result["ready_for_bounded_smoke"] is True
    assert result["workload"] == "CPU_MLP_PLUS_CPU_VASP"
    assert result["requested_generation_scale"] == 6
    assert _status(result, "local_workload_budget") == "PASS"
    assert _status(result, "gpu_driver") == "PASS"
    assert _status(result, "vasp_executable") == "PASS"
    assert _status(result, "mpi_executable") == "PASS"


def test_gpu_mlp_queries_driver_with_bounded_timeout_only(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert command == [
            str(workdir.parent / "selected" / "mlp" / "bin" / "nvidia-smi"),
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ]
        return SimpleNamespace(returncode=0, stdout="575.57.08\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("readiness must not connect to a network"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=True)
    _mlp_receipt(receipt, gpu=True)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "PASS"
    assert result["workload"] == "GPU_MLP_PLUS_CPU_VASP"
    assert result["requested_generation_scale"] == 6
    assert _status(result, "gpu_driver") == "PASS"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == local_readiness.NVIDIA_SMI_TIMEOUT_SECONDS
    assert calls[0][1]["timeout"] <= 5


@pytest.mark.parametrize(
    ("controller_mask", "profile_mask"),
    [
        ("0", None),
        (None, "MIG-GPU-deadbeef/1/0"),
        (GPU_UUID_1, GPU_UUID_2),
    ],
    ids=["numeric-controller-mask", "mig-resource-mask", "conflicting-uuid-masks"],
)
def test_gpu_mask_incompatibility_fails_before_driver_query(
    tmp_path, monkeypatch, controller_mask: str | None, profile_mask: str | None
):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    if controller_mask is not None:
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", controller_mask)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("invalid GPU masks must skip the driver query"),
    )
    profile, workdir, receipt = _profile_and_workdir(
        tmp_path, gpu=True, gpu_mask=profile_mask
    )
    _mlp_receipt(receipt, gpu=True)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "gpu_mask_compatibility") == "FAIL"
    assert _status(result, "gpu_driver") == "NOT RUN"


def test_compatible_gpu_uuid_masks_pass_without_selecting_a_device(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", f"{GPU_UUID_1},{GPU_UUID_2}")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert command[-1] == "--format=csv,noheader"
        assert "nvidia-smi" in command[0]
        return SimpleNamespace(returncode=0, stdout="575.57.08\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    profile, workdir, receipt = _profile_and_workdir(
        tmp_path, gpu=True, gpu_mask=GPU_UUID_2
    )
    _mlp_receipt(receipt, gpu=True)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "PASS"
    assert _status(result, "gpu_mask_compatibility") == "PASS"
    assert len(commands) == 1


def test_cpu_only_profile_ignores_invalid_gpu_masks(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not query the GPU driver"),
    )
    profile, workdir, receipt = _profile_and_workdir(
        tmp_path, gpu=False, gpu_mask="MIG-GPU-deadbeef/1/0"
    )
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "PASS"
    assert _status(result, "gpu_mask_compatibility") == "NOT RUN"


def test_rendered_profile_fragment_can_be_merged_into_workdir_config(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not execute a command"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    rendered = render_profile(profile, (tmp_path / "rendered").resolve())
    fragment = yaml.safe_load(
        Path(rendered["outputs"]["config_fragment"]).read_text(encoding="utf-8")
    )
    config_path = workdir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    for stage in ("gen_opt", "vasp_processing"):
        config[stage]["machine"] = fragment[stage]["machine"]
        config[stage]["resources"] = fragment[stage]["resources"]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "PASS"
    assert _status(result, "profile_workdir_match") == "PASS"


def test_missing_vasp_binary_fails_closed_without_running_mpi_or_vasp(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch, missing={"vasp_std"})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not execute binaries"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "vasp_executable") == "FAIL"
    assert _status(result, "mpi_executable") == "PASS"


def test_mpiexec_only_does_not_satisfy_the_bundled_vasp_launcher(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch, missing={"mpirun"})
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not execute binaries"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "mpi_executable") == "FAIL"


def test_mismatched_mlp_runtime_pair_fails(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["facts"]["pairing"]["actual"]["torch"] = "2.13.0"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "mlp_receipt") == "FAIL"
    assert "2.13.0" not in json.dumps(result)


def test_mlp_receipt_interpreter_keeps_distinct_venv_symlink_identity(tmp_path):
    base_python = tmp_path / "base" / "python"
    base_python.parent.mkdir()
    base_python.write_text("shared interpreter binary\n", encoding="utf-8")
    base_python.chmod(0o755)
    env_paths = []
    for name in ("venv-a", "venv-b"):
        bin_dir = tmp_path / name / "bin"
        bin_dir.mkdir(parents=True)
        link = bin_dir / "python"
        link.symlink_to(base_python)
        env_paths.append(link)

    env_a = local_readiness._resolve_executable(str(env_paths[0]), path="")
    env_b = local_readiness._resolve_executable(str(env_paths[1]), path="")
    assert env_a is not None and env_b is not None
    assert env_a != env_b
    assert env_a.samefile(env_b)

    receipt_path = tmp_path / "mlp.json"
    receipt_path.write_text(
        json.dumps(
            {
                "target": "mlp",
                "ok": True,
                "facts": {"backend": "deepmd"},
                "host": {
                    "python_version": "3.12.4",
                    "python_executable": str(env_paths[1]),
                },
            }
        ),
        encoding="utf-8",
    )
    status, _message = local_readiness._check_mlp_receipt(
        receipt_path,
        backend="deepmd",
        configured_executable=env_a,
        effective_model_path=None,
        require_gpu=False,
    )
    assert status == "FAIL"


@pytest.mark.parametrize("same_bytes", [False, True])
def test_mlp_receipt_model_must_match_effective_dpa4_model(tmp_path, monkeypatch, same_bytes):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("CPU-only readiness must not execute a command"),
    )
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    staged_model = tmp_path / "model-a.pt"
    receipt_model = tmp_path / "model-b.pt"
    staged_model.write_bytes(b"model A")
    receipt_model.write_bytes(b"model A" if same_bytes else b"model B")
    config_path = workdir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"]["mlp_backend"] = "dpa4"
    config["gen_opt"]["mlp_model"] = str(staged_model)
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    _mlp_receipt(receipt, gpu=False, backend="dpa4", model_path=receipt_model)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert _status(result, "mlp_receipt") == ("PASS" if same_bytes else "FAIL")
    assert result["status"] == ("PASS" if same_bytes else "FAIL")


def test_gpu_driver_query_failure_fails_without_vasp_or_mpi_execution(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="private driver failure detail")

    monkeypatch.setattr(subprocess, "run", run)
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=True)
    _mlp_receipt(receipt, gpu=True)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "gpu_driver") == "FAIL"
    assert len(commands) == 1
    assert "private driver failure detail" not in json.dumps(result)


@pytest.mark.parametrize(
    ("nodes", "profile_gpus", "mlp_workers", "expected_check"),
    [
        (2, 1, 1, "gpu_allocation_guard"),
        (1, 2, 1, "gpu_allocation_guard"),
        (1, 1, 2, "local_cpu_budget"),
    ],
)
def test_gpu_profile_rejects_multiple_workflow_nodes_or_multiple_gpus(
    tmp_path, monkeypatch, nodes: int, profile_gpus: int, mlp_workers: int, expected_check: str
):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="575.57.08\n", stderr=""),
    )
    profile, workdir, receipt = _profile_and_workdir(
        tmp_path,
        gpu=True,
        nodes=nodes,
        profile_gpus=profile_gpus,
        mlp_workers=mlp_workers,
    )
    _mlp_receipt(receipt, gpu=True)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, expected_check) == "FAIL"


def test_missing_mlp_receipt_is_not_run_and_never_ready(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, _receipt = _profile_and_workdir(tmp_path, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir)

    assert result["status"] == "NOT RUN"
    assert result["ready_for_bounded_smoke"] is False
    assert _status(result, "mlp_receipt") == "NOT RUN"


def test_missing_explicit_generation_budget_is_not_run_without_assuming_defaults(
    tmp_path, monkeypatch
):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)
    config_path = workdir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"].pop("num_per_group")
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "NOT RUN"
    assert result["ready_for_bounded_smoke"] is False
    assert result["requested_generation_scale"] is None
    assert _status(result, "local_workload_budget") == "NOT RUN"


def test_default_like_generation_scale_exceeds_smoke_cap_and_reports_scale(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)
    config_path = workdir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"]["num_per_group"] = 500
    config["gen_opt"]["space_groups_limit"] = 230
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert result["requested_generation_scale"] == 115_000
    assert _status(result, "local_workload_budget") == "FAIL"


def test_explicit_generation_cap_override_is_bounded_to_one_thousand(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)
    config_path = workdir / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"]["num_per_group"] = 20
    config["gen_opt"]["space_groups_limit"] = 10
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = local_readiness.check_local_readiness(
        profile, workdir, receipt, max_generation_attempts=200
    )
    too_large = local_readiness.check_local_readiness(
        profile, workdir, receipt, max_generation_attempts=1001
    )

    assert result["status"] == "PASS"
    assert result["requested_generation_scale"] == 200
    assert too_large["status"] == "FAIL"
    assert _status(too_large, "local_workload_budget") == "FAIL"


def test_stage_cpu_allocation_above_visible_cpu_count_fails(tmp_path, monkeypatch):
    _mock_core(monkeypatch)
    _mock_executables(monkeypatch)
    monkeypatch.setattr(local_readiness.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("no GPU query expected"))
    profile, workdir, receipt = _profile_and_workdir(tmp_path, gpu=False)
    _mlp_receipt(receipt, gpu=False)

    result = local_readiness.check_local_readiness(profile, workdir, receipt)

    assert result["status"] == "FAIL"
    assert _status(result, "local_cpu_budget") == "FAIL"


def test_model_hash_refuses_files_over_the_size_cap(tmp_path):
    oversized = tmp_path / "oversized-model.pt"
    with oversized.open("wb") as model_file:
        model_file.truncate(local_readiness.MAX_MODEL_BYTES + 1)

    assert local_readiness._model_sha256(oversized) is None
