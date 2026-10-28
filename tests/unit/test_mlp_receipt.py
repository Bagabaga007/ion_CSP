import hashlib
import json
import sys
from pathlib import Path

import pytest

from ion_CSP.mlp_receipt import validate_mlp_receipt


def _write_receipt(
    tmp_path: Path,
    *,
    model_bytes: bytes = b"model",
    require_gpu: bool = True,
    probe_status: str = "pass",
    triton_status: str = "pass",
    torch_version: str = "2.11.0",
    triton_version: str = "3.6.0",
) -> tuple[Path, Path]:
    model = tmp_path / "model.pt"
    model.write_bytes(model_bytes)
    digest = hashlib.sha256(model_bytes).hexdigest()
    checks = [
        {"check_id": "python.mlp_profile", "status": "pass", "message": "ok"},
        {"check_id": "python.distribution.ase", "status": "pass", "message": "ok"},
        {"check_id": "python.import.deepmd", "status": "pass", "message": "ok"},
        {
            "check_id": "python.import.deepmd.calculator",
            "status": "pass",
            "message": "ok",
        },
        {
            "check_id": "python.distribution.deepmd-kit",
            "status": "pass",
            "message": "ok",
        },
        {"check_id": "python.distribution.torch", "status": "pass", "message": "ok"},
        {"check_id": "python.import.triton", "status": triton_status, "message": "triton"},
        {"check_id": "mlp.model", "status": "pass", "message": "ok"},
        {
            "check_id": "mlp.runtime_probe",
            "status": probe_status,
            "message": "probe",
        },
        {"check_id": "mlp.cuda", "status": "pass", "message": "cuda"},
        {"check_id": "mlp.nvidia_smi", "status": "pass", "message": "smi"},
    ]
    receipt = {
        "schema_version": 1,
        "target": "mlp",
        "ok": True,
        "host": {
            "python_executable": sys.executable,
            "python_version": "3.12.10",
            "hostname": "gpu-node-01",
        },
        "facts": {
            "backend": "deepmd",
            "packages": {
                "ase": "3.23.0",
                "deepmd-kit": "3.2.0",
                "torch": torch_version,
                "triton": triton_version,
            },
            "pairing": {
                "expected": {
                    "python": "3.12",
                    "deepmd-kit": "3.2.0",
                    "torch": "2.11.0",
                    "triton": "3.6.0",
                },
                "actual": {
                    "deepmd-kit": "3.2.0",
                    "torch": torch_version,
                    "triton": triton_version,
                },
            },
            "model": {
                "path": str(model),
                "sha256": digest,
                "size_bytes": len(model_bytes),
            },
            "cuda": {
                "required": require_gpu,
                "available": require_gpu,
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
            },
            "probe": {
                "requested": True,
                "status": probe_status,
                "backend": "deepmd",
                "model_sha256": digest,
                "forces": {"shape": [1, 3], "finite": True},
                "stress": {"shape": [6], "finite": True},
                "unit_cell_filter": "pass",
                "optimizer_steps_completed": 1,
                "allocation": {"scheduler_env": "SLURM_JOB_ID", "job_id": "12345"},
            },
        },
        "checks": checks,
    }
    receipt_path = tmp_path / "mlp-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, model


def _validate(receipt_path: Path, model: Path, *, interpreter: str | None = None):
    return validate_mlp_receipt(
        receipt_path,
        backend="deepmd",
        python_executable=interpreter or sys.executable,
        model_path=model,
        require_gpu=True,
    )


def test_validate_mlp_receipt_accepts_matching_runtime(tmp_path):
    receipt, model = _write_receipt(tmp_path)

    valid, reason = _validate(receipt, model)

    assert valid
    assert "matches" in reason


def test_validate_mlp_receipt_requires_successful_probe(tmp_path):
    receipt, model = _write_receipt(tmp_path, probe_status="fail")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "runtime_probe" in reason


def test_validate_mlp_receipt_rejects_interpreter_mismatch(tmp_path):
    receipt, model = _write_receipt(tmp_path)

    valid, reason = _validate(receipt, model, interpreter="/opt/other/bin/python")

    assert not valid
    assert "interpreter" in reason


def test_validate_mlp_receipt_rejects_model_hash_mismatch(tmp_path):
    receipt, model = _write_receipt(tmp_path)
    model.write_bytes(b"different model")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "SHA-256" in reason


def test_validate_mlp_receipt_rejects_torch_pair_mismatch(tmp_path):
    receipt, model = _write_receipt(tmp_path, torch_version="2.12.0")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "torch" in reason


def test_validate_mlp_receipt_accepts_torch_cuda_local_version_verbatim(tmp_path):
    receipt, model = _write_receipt(tmp_path, torch_version="2.11.0+cu126")

    valid, reason = _validate(receipt, model)

    assert valid, reason
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["facts"]["packages"]["torch"] == "2.11.0+cu126"
    assert payload["facts"]["pairing"]["actual"]["torch"] == "2.11.0+cu126"
    assert payload["facts"]["cuda"]["torch_runtime"] == "12.8"


def test_validate_mlp_receipt_rejects_triton_pair_mismatch(tmp_path):
    receipt, model = _write_receipt(tmp_path, triton_version="3.5.0")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "triton" in reason


def test_validate_mlp_receipt_rejects_missing_triton_for_gpu_deepmd(tmp_path):
    receipt, model = _write_receipt(tmp_path, triton_status="fail")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "triton" in reason


def test_validate_mlp_receipt_requires_recorded_cuda_runtime(tmp_path):
    receipt, model = _write_receipt(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    del payload["facts"]["cuda"]["torch_runtime"]
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "CUDA runtime" in reason


def test_validate_mlp_receipt_requires_scheduler_probe_identity(tmp_path):
    receipt, model = _write_receipt(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["facts"]["probe"]["allocation"] = {"scheduler_env": None, "job_id": None}
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "allocation" in reason


@pytest.mark.parametrize("steps", [None, 0, True, False])
def test_validate_mlp_receipt_rejects_missing_zero_or_boolean_completed_steps(
    tmp_path, steps
):
    receipt, model = _write_receipt(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if steps is None:
        payload["facts"]["probe"].pop("optimizer_steps_completed")
    else:
        payload["facts"]["probe"]["optimizer_steps_completed"] = steps
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    valid, reason = _validate(receipt, model)

    assert not valid
    assert "optimizer step" in reason


def test_unallocated_receipt_requires_explicit_local_opt_in(tmp_path):
    receipt, model = _write_receipt(tmp_path)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["facts"]["probe"]["allocation"] = {"scheduler_env": None, "job_id": None}
    payload["facts"]["probe"]["noncompute_override"] = True
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    valid, reason = _validate(receipt, model)
    assert not valid
    assert "allocation" in reason

    valid, reason = validate_mlp_receipt(
        receipt,
        backend="deepmd",
        python_executable=sys.executable,
        model_path=model,
        require_gpu=True,
        allow_noncompute_probe=True,
    )
    assert valid, reason

    payload["facts"]["probe"]["noncompute_override"] = False
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    valid, reason = validate_mlp_receipt(
        receipt,
        backend="deepmd",
        python_executable=sys.executable,
        model_path=model,
        require_gpu=True,
        allow_noncompute_probe=True,
    )
    assert not valid
    assert "allocation" in reason


def test_validate_mlp_receipt_requires_absolute_inputs(tmp_path):
    receipt, model = _write_receipt(tmp_path)

    valid, reason = validate_mlp_receipt(
        receipt.relative_to(Path.cwd()) if receipt.is_relative_to(Path.cwd()) else receipt,
        backend="deepmd",
        python_executable="python",
        model_path=model,
        require_gpu=True,
    )

    # The model/receipt fixture is absolute; the explicit relative interpreter
    # is rejected before any model or runtime operation is attempted.
    assert not valid
    assert "absolute" in reason
