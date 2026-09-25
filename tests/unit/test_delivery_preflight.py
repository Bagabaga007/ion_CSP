import json
from pathlib import Path
from types import SimpleNamespace

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
    versions = {"ase": "3.23.0", "deepmd-kit": "3.2.0", "torch": "2.11.0"}
    monkeypatch.setattr(preflight.sys, "version_info", (3, 12, 1))
    monkeypatch.setattr(preflight, "_distribution_version", versions.get)
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        preflight.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )
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
    }


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
