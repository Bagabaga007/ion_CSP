from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from ion_CSP.compute_profile import ProfileError, load_profile, main, render_profile


def _write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path.resolve()


def _machine(
    path: Path,
    *,
    context: str,
    batch: str,
    credentials: bool = False,
    remote_root: str | None = "/scratch/ion-csp",
    include_remote_profile: bool = True,
    hostname: str | None = None,
    username: str | None = None,
) -> Path:
    data = {
        "context_type": context,
        "batch_type": batch,
        "local_root": "./",
    }
    if remote_root is not None:
        data["remote_root"] = remote_root
    if context == "SSHContext" and include_remote_profile:
        host_value = hostname if hostname is not None else (
            "private-host.example.invalid" if credentials else "operator-cluster.example.invalid"
        )
        username_value = username if username is not None else (
            "private-login-user" if credentials else "operator"
        )
        data["remote_profile"] = {
            "hostname": host_value,
            "username": username_value,
        }
        if credentials:
            data["remote_profile"]["password"] = "private-login-password"
    return _write_yaml(path, data)


def _resources(
    path: Path,
    *,
    gpu: int,
    queue: str | None = None,
    flags: list[str] | None = None,
    extra: dict | None = None,
) -> Path:
    data = {
        "number_node": 1,
        "cpu_per_node": 8,
        "gpu_per_node": gpu,
        "group_size": 2,
    }
    if queue is not None:
        data["queue_name"] = queue
    if flags is not None:
        data["custom_flags"] = flags
    if extra:
        data.update(extra)
    return _write_yaml(path, data)


def _profile(
    path: Path,
    *,
    mode: str,
    scheduler: str,
    stages: dict,
    owner_id: str = "team-blue",
    run_id: str = "trial-008",
) -> Path:
    return _write_yaml(
        path,
        {
            "schema_version": 1,
            "owner_id": owner_id,
            "run_id": run_id,
            "mode": mode,
            "scheduler": scheduler,
            "stages": stages,
        },
    )


def _stage(resource_class: str, machine: Path, resources: Path) -> dict:
    return {
        "resource_class": resource_class,
        "machine": str(machine),
        "resources": str(resources),
    }


def test_remote_slurm_renders_cpu_gpu_resources_and_absolute_config_paths(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not execute a command"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not execute a command"))
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("must not connect"))
    machine_cpu = _machine(
        tmp_path / "operator" / "cpu-machine.yaml",
        context="SSHContext",
        batch="Slurm",
        credentials=True,
    )
    machine_gpu = _machine(
        tmp_path / "operator" / "gpu-machine.yaml",
        context="SSHContext",
        batch="Slurm",
        credentials=True,
    )
    cpu_flags = ["#SBATCH --time=02:00:00", "#SBATCH --account=project-a"]
    cpu_resources = _resources(
        tmp_path / "operator" / "cpu.yaml", gpu=0, queue="compute", flags=cpu_flags
    )
    gpu_resources = _resources(
        tmp_path / "operator" / "gpu.yaml",
        gpu=1,
        queue="gpu-partition",
        flags=["#SBATCH --constraint=a100"],
    )
    profile = _profile(
        tmp_path / "site-profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={
            "convert_SMILES": _stage("cpu", machine_cpu, cpu_resources),
            "gen_opt": _stage("gpu", machine_gpu, gpu_resources),
            "vasp_processing": _stage("cpu", machine_cpu, cpu_resources),
        },
    )

    result = render_profile(profile, (tmp_path / "rendered").resolve())
    fragment_path = Path(result["outputs"]["config_fragment"])
    fragment = yaml.safe_load(fragment_path.read_text(encoding="utf-8"))
    assert set(fragment) == {"convert_SMILES", "gen_opt", "vasp_processing"}
    assert all(Path(item["machine"]).is_absolute() for item in fragment.values())
    assert all(Path(item["resources"]).is_absolute() for item in fragment.values())

    rendered_cpu = yaml.safe_load(
        Path(fragment["convert_SMILES"]["resources"]).read_text(encoding="utf-8")
    )
    rendered_gpu = yaml.safe_load(
        Path(fragment["gen_opt"]["resources"]).read_text(encoding="utf-8")
    )
    assert rendered_cpu["custom_flags"][:2] == cpu_flags
    assert rendered_cpu["custom_flags"][2].startswith("#SBATCH --job-name=icsp-team-blue-trial-008-cpu-convert_SMILES")
    assert "role.cpu.stage.convert_SMILES" in rendered_cpu["custom_flags"][3]
    assert rendered_cpu["queue_name"] == "compute"
    assert rendered_gpu["queue_name"] == "gpu-partition"
    assert not any("--account" in flag for flag in rendered_gpu["custom_flags"])
    assert "--partition=gpu-partition" not in " ".join(rendered_gpu["custom_flags"])

    receipt_text = Path(result["outputs"]["config_fragment"]).parent.joinpath("receipt.json").read_text()
    assert "private-host.example.invalid" not in receipt_text
    assert "private-login-user" not in receipt_text
    assert "private-login-password" not in receipt_text
    for output_file in Path(result["outputs"]["config_fragment"]).parent.rglob("*"):
        if output_file.is_file():
            contents = output_file.read_text(encoding="utf-8")
            assert "private-host.example.invalid" not in contents
            assert "private-login-user" not in contents
            assert "private-login-password" not in contents


@pytest.mark.parametrize(
    "flags",
    [
        ["#SBATCH --job-name=custom"],
        ["#SBATCH -J custom"],
        ["#SBATCH --comment=custom"],
        ["#SBATCH --time=01:00:00", "#SBATCH -t 02:00:00"],
    ],
)
def test_conflicting_or_duplicate_custom_flags_are_rejected(tmp_path: Path, flags: list[str]):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(tmp_path / "resources.yaml", gpu=0, flags=flags)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    with pytest.raises(ProfileError):
        load_profile(profile)


def test_duplicate_partition_with_queue_name_is_rejected(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(
        tmp_path / "resources.yaml", gpu=0, queue="compute", flags=["#SBATCH -p gpu"]
    )
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    with pytest.raises(ProfileError, match="queue_name"):
        load_profile(profile)


def test_stage_resource_class_must_match_gpu_allocation(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"gen_opt": _stage("gpu", machine, resources)},
    )
    with pytest.raises(ProfileError, match="GPU stages"):
        load_profile(profile)


def test_scheduler_must_match_declared_mode(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="none",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    with pytest.raises(ProfileError, match="scheduler"):
        load_profile(profile)


@pytest.mark.parametrize(
    "remote_root",
    [None, "scratch/relative", "/scratch/$(touch /tmp/ion-csp-profile-sentinel)"],
)
def test_remote_slurm_requires_absolute_shell_safe_remote_root(
    tmp_path: Path, capsys, remote_root: str | None
):
    machine = _machine(
        tmp_path / "machine.yaml",
        context="SSHContext",
        batch="Slurm",
        credentials=True,
        remote_root=remote_root,
    )
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "private-host.example.invalid" not in output
    assert "private-login-user" not in output
    assert "private-login-password" not in output
    assert "ion-csp-profile-sentinel" not in output
    assert not (tmp_path / "ion-csp-profile-sentinel").exists()


def test_remote_slurm_requires_remote_profile_without_echoing_credentials(
    tmp_path: Path, capsys
):
    machine = _machine(
        tmp_path / "machine.yaml",
        context="SSHContext",
        batch="Slurm",
        credentials=True,
        include_remote_profile=False,
    )
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "private-host.example.invalid" not in output
    assert "private-login-user" not in output
    assert "private-login-password" not in output


@pytest.mark.parametrize("field", ["hostname", "username"])
def test_remote_profile_requires_nonempty_host_and_username_without_echo(
    tmp_path: Path, capsys, field: str
):
    machine_options = {field: ""}
    if field == "hostname":
        machine_options["username"] = "private-login-user"
    else:
        machine_options["hostname"] = "private-host.example.invalid"
    machine = _machine(
        tmp_path / "machine.yaml",
        context="SSHContext",
        batch="Slurm",
        credentials=True,
        **machine_options,
    )
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "private-host.example.invalid" not in output
    assert "private-login-user" not in output
    assert "private-login-password" not in output


@pytest.mark.parametrize(
    "resource_field,value",
    [
        ("source_list", ["/opt/env/$(touch /tmp/ion-csp-profile-sentinel)"]),
        ("source_list", ["/opt/env/`touch /tmp/ion-csp-profile-sentinel`"]),
        ("source_list", ["/opt/env;touch /tmp/ion-csp-profile-sentinel"]),
        ("envs", {"PATH": "/opt/bin;touch /tmp/ion-csp-profile-sentinel"}),
        ("envs", {"PATH": "/opt/bin$(touch /tmp/ion-csp-profile-sentinel)"}),
        ("envs", {"PATH": "/opt/bin`touch /tmp/ion-csp-profile-sentinel`"}),
    ],
)
def test_shell_metacharacters_in_source_and_environment_values_are_rejected_silently(
    tmp_path: Path, capsys, resource_field: str, value
):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(
        tmp_path / "resources.yaml", gpu=0, extra={resource_field: value}
    )
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "ion-csp-profile-sentinel" not in output
    assert not (tmp_path / "ion-csp-profile-sentinel").exists()


def test_simple_path_variable_expansion_is_preserved(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(
        tmp_path / "resources.yaml",
        gpu=0,
        extra={
            "source_list": ["/opt/intel/oneapi/setvars.sh"],
            "envs": {"PATH": "/opt/vasp/bin:$PATH"},
        },
    )
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    plan = load_profile(profile)
    assert plan.stages[0].resources["envs"]["PATH"] == "/opt/vasp/bin:$PATH"


def test_local_mode_rejects_a_remote_machine_context(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="local",
        scheduler="none",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    with pytest.raises(ProfileError, match="context_type and batch_type"):
        load_profile(profile)


def test_stage_requires_paired_machine_and_resources_paths(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": {"resource_class": "cpu", "machine": str(machine)}},
    )
    with pytest.raises(ProfileError, match="missing or unsupported fields"):
        load_profile(profile)


def test_remote_ssh_shell_is_rejected_without_disclosing_machine_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    machine = _machine(
        tmp_path / "machine.yaml",
        context="SSHContext",
        batch="Shell",
        credentials=True,
    )
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "private-host.example.invalid" not in output
    assert "private-login-user" not in output
    assert "private-login-password" not in output


def test_local_cpu_mode_needs_no_nvidia_smi_and_has_no_scheduler_flags(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="LocalContext", batch="Shell")
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="local",
        scheduler="none",
        stages={"gen_opt": _stage("cpu", machine, resources)},
    )
    result = render_profile(profile, (tmp_path / "rendered").resolve(), gpu_probe=lambda _name: None)
    fragment = yaml.safe_load(Path(result["outputs"]["config_fragment"]).read_text())
    rendered = yaml.safe_load(Path(fragment["gen_opt"]["resources"]).read_text())
    assert "custom_flags" not in rendered
    assert "queue_name" not in rendered


def test_local_gpu_mode_requires_nvidia_smi_but_never_executes_it(tmp_path: Path, monkeypatch):
    machine = _machine(tmp_path / "machine.yaml", context="LocalContext", batch="Shell")
    resources = _resources(tmp_path / "resources.yaml", gpu=1)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="local",
        scheduler="none",
        stages={"gen_opt": _stage("gpu", machine, resources)},
    )
    monkeypatch.setattr("ion_CSP.compute_profile.shutil.which", lambda _name: None)
    with pytest.raises(ProfileError, match="nvidia-smi"):
        load_profile(profile)

    calls: list[str] = []
    monkeypatch.setattr("ion_CSP.compute_profile.shutil.which", lambda name: calls.append(name) or "/usr/bin/nvidia-smi")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not execute a command"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("must not execute a command"))
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("must not connect"))
    plan = load_profile(profile)
    assert plan.stages[0].resource_class == "gpu"
    assert calls == ["nvidia-smi"]


def test_credentials_in_rejected_resources_are_not_echoed(tmp_path: Path, capsys):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(
        tmp_path / "resources.yaml",
        gpu=0,
        extra={"envs": {"AWS_SECRET_ACCESS_KEY": "secret-value-never-print"}},
    )
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    assert main(["validate", str(profile)]) == 2
    output = capsys.readouterr()
    assert "secret-value-never-print" not in output.out + output.err


def test_existing_run_directory_and_outputs_are_never_overwritten(tmp_path: Path):
    machine = _machine(tmp_path / "machine.yaml", context="SSHContext", batch="Slurm")
    resources = _resources(tmp_path / "resources.yaml", gpu=0)
    profile = _profile(
        tmp_path / "profile.yaml",
        mode="remote_slurm",
        scheduler="slurm",
        stages={"convert_SMILES": _stage("cpu", machine, resources)},
    )
    output_root = (tmp_path / "rendered").resolve()
    receipt = render_profile(profile, output_root)
    resource_path = Path(receipt["outputs"]["stages"][0]["resources"])
    resource_path.write_text("user sentinel\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="overwrite"):
        render_profile(profile, output_root)
    assert resource_path.read_text(encoding="utf-8") == "user sentinel\n"


def test_duplicate_yaml_mapping_keys_are_rejected(tmp_path: Path):
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "schema_version: 1\nowner_id: team\nowner_id: other\nrun_id: 1\nmode: local\nscheduler: none\nstages: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="duplicate"):
        load_profile(profile.resolve())
