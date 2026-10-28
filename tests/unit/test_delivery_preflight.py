import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ion_CSP import preflight


def _write_machine_files(tmp_path: Path):
    machine = tmp_path / "machine.yaml"
    resources = tmp_path / "resources.json"
    machine.write_text(
        "batch_type: Slurm\ncontext_type: SSHContext\nlocal_root: /tmp/local\n"
        "remote_root: /tmp/remote\n",
        encoding="utf-8",
    )
    resources.write_text(
        json.dumps(
            {
                "number_node": 1,
                "cpu_per_node": 8,
                "gpu_per_node": 0,
                "group_size": 1,
                "queue_name": "test",
            }
        ),
        encoding="utf-8",
    )
    return machine, resources


def _mock_nvidia_smi_query(monkeypatch, output=None, *, returncode=0):
    if output is None:
        output = (
            "0, GPU-11111111-1111-1111-1111-111111111111, "
            "NVIDIA Test GPU, 575.57.08\n"
        )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=returncode, stdout=output, stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", run)
    return calls


def test_validate_ee_contract_accepts_complete_absolute_config(tmp_path):
    machine, resources = _write_machine_files(tmp_path)
    (tmp_path / "ions.csv").write_text(
        "SMILES,Refcode,Charge\n[NH4+],AMMONIUM,1\n", encoding="utf-8"
    )
    (tmp_path / "config.yaml").write_text(
        f"""convert_SMILES:
  csv_file: ions.csv
  machine: {machine}
  resources: {resources}
  nodes: 1
empirical_estimate:
  folders: [charge_1]
  ratios: [1]
  ion_numbers: [1]
  sort_by: density
""",
        encoding="utf-8",
    )

    report = preflight.validate_workflow_config("ee", tmp_path)

    assert report.ok
    assert all(item.status == "pass" for item in report.checks)


def test_validate_csp_contract_rejects_relative_machine_paths(tmp_path):
    (tmp_path / "cation.gjf").write_text("test\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        """gen_opt:
  species: [cation.gjf]
  ion_numbers: [1]
  mlp_backend: deepmd
  machine: machine.yaml
  resources: resources.yaml
  nodes: 1
read_mlp_density: {}
vasp_processing:
  machine: machine.yaml
  resources: resources.yaml
  nodes: 1
""",
        encoding="utf-8",
    )

    report = preflight.validate_workflow_config("csp", tmp_path)

    assert not report.ok
    failed = {item.check_id for item in report.checks if item.status == "fail"}
    assert "config.csp.gen_opt.machine.absolute" in failed
    assert "config.csp.vasp_processing.resources.absolute" in failed


def test_external_check_is_visibility_only(monkeypatch):
    available = {
        "g16": "/opt/g16/g16",
        "formchk": "/opt/g16/formchk",
    }
    monkeypatch.setattr(preflight.shutil, "which", available.get)

    report = preflight.check_external(profiles=["gaussian"])

    assert report.ok
    assert all("execut" not in item.details for item in report.checks)


def test_mlp_check_binds_pair_and_cuda_receipt(monkeypatch, tmp_path):
    versions = {
        "ase": "3.23.0",
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "triton": "3.6.0",
    }
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    calls = _mock_nvidia_smi_query(monkeypatch)
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.8"),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)
    model = tmp_path / "model.pt"
    model.write_bytes(b"model")

    report = preflight.check_mlp(model=model, require_gpu=True)

    assert report.ok
    assert report.facts["pairing"]["expected"]["torch"] == "2.11.0"
    assert report.facts["cuda"] == {
        "required": True,
        "available": True,
        "torch_runtime": "12.8",
        "nvidia_smi": {
            "resolved": "/usr/bin/nvidia-smi",
            "gpus": [
                {
                    "index": 0,
                    "uuid": "GPU-11111111-1111-1111-1111-111111111111",
                    "name": "NVIDIA Test GPU",
                    "driver_version": "575.57.08",
                }
            ],
        },
    }
    assert calls[0][0] == [
        "/usr/bin/nvidia-smi",
        "--query-gpu=index,uuid,name,driver_version",
        "--format=csv,noheader",
    ]
    assert calls[0][1]["timeout"] == preflight.NVIDIA_SMI_TIMEOUT_SECONDS
    assert calls[0][1]["timeout"] <= 5
    assert report.facts["model"]["sha256"]
    assert report.facts["probe"]["status"] == "not_requested"


def test_mlp_cuda_metadata_is_json_safe_for_unexpected_runtime_values(monkeypatch):
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: MagicMock()),
        version=SimpleNamespace(cuda=MagicMock()),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)

    report = preflight.check_mlp(backend="mattersim", require_gpu=False)

    assert report.facts["cuda"]["available"] is False
    assert report.facts["cuda"]["torch_runtime"] is None
    json.dumps(report.to_dict())


def test_mlp_check_accepts_torch_cuda_local_version_and_preserves_receipt(
    monkeypatch, tmp_path
):
    versions = {
        "ase": "3.23.0",
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0+cu126",
        "triton": "3.6.0",
    }
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    _mock_nvidia_smi_query(monkeypatch)
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.6"),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)
    model = tmp_path / "model.pt"
    model.write_bytes(b"model")

    report = preflight.check_mlp(model=model, require_gpu=True)

    assert report.ok
    assert report.facts["packages"]["torch"] == "2.11.0+cu126"
    assert report.facts["pairing"]["actual"]["torch"] == "2.11.0+cu126"
    assert report.facts["cuda"]["torch_runtime"] == "12.6"


def test_mlp_check_rejects_torch_public_version_drift(monkeypatch, tmp_path):
    versions = {
        "ase": "3.23.0",
        "deepmd-kit": "3.2.0",
        "torch": "2.12.0+cu126",
        "triton": "3.6.0",
    }
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    _mock_nvidia_smi_query(monkeypatch)
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.6"),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)
    model = tmp_path / "model.pt"
    model.write_bytes(b"model")

    report = preflight.check_mlp(model=model, require_gpu=True)

    assert not report.ok
    torch_check = next(
        item for item in report.checks if item.check_id == "python.distribution.torch"
    )
    assert torch_check.status == "fail"
    assert torch_check.details["installed"] == "2.12.0+cu126"


def test_mlp_gpu_check_rejects_missing_triton(monkeypatch, tmp_path):
    versions = {"ase": "3.23.0", "deepmd-kit": "3.2.0", "torch": "2.11.0"}
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(
        preflight.importlib.util,
        "find_spec",
        lambda name: None if name == "triton" else object(),
    )
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    _mock_nvidia_smi_query(monkeypatch)
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.8"),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)
    model = tmp_path / "model.pt"
    model.write_bytes(b"model")

    report = preflight.check_mlp(model=model, require_gpu=True)

    assert not report.ok
    failed = {item.check_id for item in report.checks if item.status == "fail"}
    assert "python.import.triton" in failed


def test_runtime_probe_refuses_unallocated_login_node(monkeypatch, tmp_path):
    monkeypatch.setattr(preflight, "_has_compute_allocation", lambda: False)
    monkeypatch.setattr(
        preflight,
        "_run_runtime_probe",
        lambda **_kwargs: pytest.fail("probe must not run on a login node"),
    )
    model = tmp_path / "model.pt"
    poscar = tmp_path / "POSCAR"
    model.write_bytes(b"model")
    poscar.write_text("POSCAR", encoding="utf-8")

    report = preflight.check_mlp(
        model=model,
        probe_poscar=poscar,
        require_compute_node=True,
    )

    assert not report.ok
    probe = next(item for item in report.checks if item.check_id == "mlp.runtime_probe")
    assert "login node" in probe.message
    assert report.facts["probe"]["status"] == "fail"


def test_explicit_runtime_probe_records_result(monkeypatch, tmp_path):
    versions = {
        "ase": "3.23.0",
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "triton": "3.6.0",
    }
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
    _mock_nvidia_smi_query(monkeypatch)
    torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(cuda="12.8"),
    )
    monkeypatch.setitem(preflight.sys.modules, "torch", torch)
    monkeypatch.setattr(preflight, "_has_compute_allocation", lambda: True)
    monkeypatch.setenv("SLURM_JOB_ID", "12345")
    probe_calls = []

    def fake_probe(**kwargs):
        probe_calls.append(kwargs)
        return {
            "status": "pass",
            "backend": kwargs["backend"],
            "forces": {"shape": [1, 3], "finite": True},
            "stress": {"shape": [6], "finite": True},
            "unit_cell_filter": "pass",
            "optimizer_steps_completed": 1,
        }

    monkeypatch.setattr(
        preflight,
        "_run_runtime_probe",
        fake_probe,
    )
    model = tmp_path / "model.pt"
    poscar = tmp_path / "POSCAR"
    model.write_bytes(b"model")
    poscar.write_text("POSCAR", encoding="utf-8")

    report = preflight.check_mlp(
        model=model,
        probe_poscar=poscar,
        require_gpu=True,
    )

    assert report.ok
    assert report.facts["probe"]["status"] == "pass"
    assert report.facts["probe"]["model_sha256"] == report.facts["model"]["sha256"]
    assert report.facts["probe"]["completed_stages"] == []
    assert report.facts["probe"]["failed_stage"] is None
    assert probe_calls[0]["model"] == model
    assert probe_calls[0]["poscar"] == poscar
    assert probe_calls[0]["require_gpu"] is True


@pytest.mark.parametrize(
    ("failure_stage", "completed_stages"),
    [
        ("poscar_read", []),
        ("calculator_creation", ["poscar_read"]),
        ("forces", ["poscar_read", "calculator_creation"]),
        ("stress", ["poscar_read", "calculator_creation", "forces"]),
        (
            "unit_cell_filter_creation",
            ["poscar_read", "calculator_creation", "forces", "stress"],
        ),
        (
            "optimizer_step",
            [
                "poscar_read",
                "calculator_creation",
                "forces",
                "stress",
                "unit_cell_filter_creation",
            ],
        ),
    ],
)
def test_runtime_probe_failure_records_full_traceback_and_stage(
    monkeypatch, tmp_path, failure_stage, completed_stages
):
    """A probe exception keeps its type/message and records its exact stage."""

    from ase import filters as ase_filters
    from ase import io as ase_io
    from ase import optimize as ase_optimize

    class FakeAtoms:
        def __init__(self):
            self._calculator = None

        @property
        def calc(self):
            return self._calculator

        @calc.setter
        def calc(self, value):
            self._calculator = value

        def get_forces(self):
            if failure_stage == "forces":
                raise StopIteration("injected forces failure")
            return [[0.0, 0.0, 0.0]]

        def get_stress(self):
            if failure_stage == "stress":
                raise StopIteration("injected stress failure")
            return [0.0] * 6

    def fail(stage):
        raise StopIteration(f"injected {stage} failure")

    atoms = FakeAtoms()
    if failure_stage == "poscar_read":
        monkeypatch.setattr(ase_io, "read", lambda _path: fail("poscar_read"))
    else:
        monkeypatch.setattr(ase_io, "read", lambda _path: atoms)

    if failure_stage == "calculator_creation":
        monkeypatch.setattr(
            preflight,
            "_build_probe_calculator",
            lambda *_args, **_kwargs: fail("calculator_creation"),
        )
    else:
        monkeypatch.setattr(
            preflight,
            "_build_probe_calculator",
            lambda *_args, **_kwargs: object(),
        )

    def fake_unit_cell_filter(value):
        if failure_stage == "unit_cell_filter_creation":
            return fail("unit_cell_filter_creation")
        return value

    monkeypatch.setattr(ase_filters, "UnitCellFilter", fake_unit_cell_filter)

    if failure_stage == "optimizer_step":
        class FailingLBFGS:
            nsteps = 0

            def __init__(self, *_args, **_kwargs):
                pass

            def run(self, **_kwargs):
                return fail("optimizer_step")

        monkeypatch.setattr(ase_optimize, "LBFGS", FailingLBFGS)

    model = tmp_path / "model.pt"
    poscar = tmp_path / "POSCAR"
    model.write_bytes(b"model")
    poscar.write_text("POSCAR", encoding="utf-8")

    report = preflight.check_mlp(
        backend="mattersim",
        model=model,
        probe_poscar=poscar,
        require_compute_node=False,
    )

    probe = report.facts["probe"]
    assert probe["status"] == "fail"
    assert probe["error_type"] == "StopIteration"
    assert probe["error"] == f"injected {failure_stage} failure"
    assert probe["failed_stage"] == failure_stage
    assert probe["completed_stages"] == completed_stages
    assert "Traceback (most recent call last)" in probe["traceback"]
    assert "_run_runtime_probe" in probe["traceback"]
    assert "StopIteration" in probe["traceback"]
    assert json.dumps(report.to_dict())


def test_runtime_probe_success_records_all_completed_stages(monkeypatch, tmp_path):
    from ase import filters as ase_filters
    from ase import io as ase_io
    from ase import optimize as ase_optimize

    class FakeAtoms:
        calc = None

        def get_forces(self):
            return [[0.0, 0.0, 0.0]]

        def get_stress(self):
            return [0.0] * 6

    class FakeLBFGS:
        nsteps = 1

        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, **_kwargs):
            return None

    monkeypatch.setattr(ase_io, "read", lambda _path: FakeAtoms())
    monkeypatch.setattr(
        preflight, "_build_probe_calculator", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(ase_filters, "UnitCellFilter", lambda atoms: atoms)
    monkeypatch.setattr(ase_optimize, "LBFGS", FakeLBFGS)

    result = preflight._run_runtime_probe(
        backend="mattersim",
        model=tmp_path / "model.pt",
        poscar=tmp_path / "POSCAR",
    )

    assert result["completed_stages"] == [
        "poscar_read",
        "calculator_creation",
        "forces",
        "stress",
        "unit_cell_filter_creation",
        "optimizer_step",
    ]
    assert result["failed_stage"] is None
    assert result["optimizer_steps_completed"] == 1
    assert json.dumps(result)


@pytest.mark.parametrize("actual_steps", [0, True, None])
def test_runtime_probe_requires_actual_positive_integer_step(
    monkeypatch, tmp_path, actual_steps
):
    from ase import filters as ase_filters
    from ase import io as ase_io
    from ase import optimize as ase_optimize

    class FakeAtoms:
        calc = None

        def get_forces(self):
            return [[0.0, 0.0, 0.0]]

        def get_stress(self):
            return [0.0] * 6

    if actual_steps is None:
        class FakeLBFGS:
            def __init__(self, *_args, **_kwargs):
                pass

            def run(self, **_kwargs):
                return None
    else:
        class FakeLBFGS:
            nsteps = actual_steps

            def __init__(self, *_args, **_kwargs):
                pass

            def run(self, **_kwargs):
                return None

    monkeypatch.setattr(ase_io, "read", lambda _path: FakeAtoms())
    monkeypatch.setattr(
        preflight, "_build_probe_calculator", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(ase_filters, "UnitCellFilter", lambda atoms: atoms)
    monkeypatch.setattr(ase_optimize, "LBFGS", FakeLBFGS)

    with pytest.raises(RuntimeError, match="did not complete at least one step") as error:
        preflight._run_runtime_probe(
            backend="mattersim",
            model=tmp_path / "model.pt",
            poscar=tmp_path / "POSCAR",
        )

    assert getattr(error.value, preflight._PROBE_STAGE_ERROR_ATTR) == "optimizer_step"


def test_nvidia_smi_gpu_facts_fail_closed_on_invalid_or_unbounded_output(monkeypatch):
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="0, GPU-1, Test GPU\n", stderr=""
        ),
    )
    facts, error = preflight._query_nvidia_smi_gpus("/usr/bin/nvidia-smi")
    assert facts is None
    assert "field count" in error

    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="x" * (preflight.MAX_NVIDIA_SMI_OUTPUT_BYTES + 1),
            stderr="",
        ),
    )
    facts, error = preflight._query_nvidia_smi_gpus("/usr/bin/nvidia-smi")
    assert facts is None
    assert "output limit" in error


def test_runtime_probe_preserves_stage_for_exception_without_attributes(
    monkeypatch, tmp_path
):
    """The diagnostic fallback does not replace the original error in receipt."""

    from ase import io as ase_io

    class NonAnnotatableStopIteration(StopIteration):
        def __setattr__(self, _name, _value):
            raise AttributeError("exception attributes are disabled")

    class FakeAtoms:
        calc = None

        def get_forces(self):
            raise NonAnnotatableStopIteration("immutable forces failure")

        def get_stress(self):
            return [0.0] * 6

    monkeypatch.setattr(ase_io, "read", lambda _path: FakeAtoms())
    monkeypatch.setattr(
        preflight, "_build_probe_calculator", lambda *_args, **_kwargs: object()
    )
    model = tmp_path / "model.pt"
    poscar = tmp_path / "POSCAR"
    model.write_bytes(b"model")
    poscar.write_text("POSCAR", encoding="utf-8")

    report = preflight.check_mlp(
        backend="mattersim",
        model=model,
        probe_poscar=poscar,
        require_compute_node=False,
    )

    probe = report.facts["probe"]
    assert probe["error_type"] == "NonAnnotatableStopIteration"
    assert probe["error"] == "immutable forces failure"
    assert probe["failed_stage"] == "forces"
    assert probe["completed_stages"] == ["poscar_read", "calculator_creation"]
    assert "NonAnnotatableStopIteration" in probe["traceback"]
    assert "_run_runtime_probe" in probe["traceback"]
    assert json.dumps(report.to_dict())


def test_cli_writes_json_and_returns_nonzero_on_missing_external(monkeypatch, capsys):
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)

    exit_code = preflight.main(
        ["--target", "external", "--external-profile", "gaussian"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["target"] == "external"
    assert payload["ok"] is False


def test_validate_rejects_unknown_workflow(tmp_path):
    with pytest.raises(ValueError, match="workflow"):
        preflight.validate_workflow_config("unknown", tmp_path)
