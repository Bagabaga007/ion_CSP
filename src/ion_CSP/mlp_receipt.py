"""Pure validation of an MLP runtime preflight receipt.

The validator intentionally does not import Torch, DeepMD, ASE, or execute a
configured interpreter.  It is safe to call from the controller immediately
before a dispatcher submission.  The receipt itself must have been produced by
an explicit runtime probe on the target compute node.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ion_CSP.preflight import (
    DEEPMD_TORCH_PAIR,
    MLP_RUNTIME_PROBE_CHECK_ID,
    MAX_MODEL_BYTES,
    MODEL_HASH_CHUNK_BYTES,
    _is_supported_torch_version,
)


DEEPMD_BACKENDS = {"deepmd", "dpa4", "dpa4_ion_ft"}
SUPPORTED_BACKENDS = DEEPMD_BACKENDS | {"mattersim"}


def _normal_absolute_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        return None
    return os.path.normpath(str(path))


def _checks_by_id(receipt: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    raw_checks = receipt.get("checks")
    if not isinstance(raw_checks, list):
        return None
    checks: dict[str, dict[str, Any]] = {}
    for item in raw_checks:
        if not isinstance(item, dict) or not isinstance(item.get("check_id"), str):
            return None
        check_id = item["check_id"]
        if check_id in checks:
            return None
        checks[check_id] = item
    return checks


def _check_failure(checks: dict[str, dict[str, Any]], check_id: str) -> str | None:
    item = checks.get(check_id)
    if not isinstance(item, dict) or item.get("status") != "pass":
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            return f"receipt check {check_id!r} did not pass: {item['message']}"
        return f"receipt is missing required passing check {check_id!r}"
    return None


def _model_sha256(path: Path) -> tuple[str | None, str | None]:
    try:
        if not path.is_file():
            return None, "configured model file is missing"
        size = path.stat().st_size
        if size < 1:
            return None, "configured model file is empty"
        if size > MAX_MODEL_BYTES:
            return None, f"configured model exceeds {MAX_MODEL_BYTES} byte hash limit"
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as model_file:
            while chunk := model_file.read(MODEL_HASH_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_MODEL_BYTES:
                    return None, f"configured model exceeds {MAX_MODEL_BYTES} byte hash limit"
                digest.update(chunk)
        return digest.hexdigest(), None
    except OSError as error:
        return None, f"could not hash configured model: {error}"


def _receipt_model_hash(receipt: dict[str, Any], checks: dict[str, dict[str, Any]]) -> str | None:
    facts = receipt.get("facts")
    if isinstance(facts, dict):
        model = facts.get("model")
        if isinstance(model, dict) and isinstance(model.get("sha256"), str):
            return model["sha256"]
    model_check = checks.get("mlp.model")
    details = model_check.get("details") if isinstance(model_check, dict) else None
    if isinstance(details, dict) and isinstance(details.get("sha256"), str):
        return details["sha256"]
    return None


def _receipt_probe(receipt: dict[str, Any], checks: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    facts = receipt.get("facts")
    probe = facts.get("probe") if isinstance(facts, dict) else None
    return probe if isinstance(probe, dict) else None


def validate_mlp_receipt(
    path: str | Path,
    *,
    backend: str,
    python_executable: str | Path,
    model_path: str | Path,
    require_gpu: bool = True,
    allow_noncompute_probe: bool = False,
) -> tuple[bool, str]:
    """Validate a target-node MLP receipt before dispatcher submission.

    Parameters are deliberately explicit so a caller cannot accidentally
    validate a receipt for a different backend, interpreter, or model.  The
    function is pure apart from reading the receipt and hashing ``model_path``;
    it never imports a scientific runtime or launches a command.
    """

    backend = backend.lower() if isinstance(backend, str) else ""
    if backend not in SUPPORTED_BACKENDS:
        return False, f"unsupported MLP backend for receipt validation: {backend!r}"

    receipt_path = Path(path).expanduser()
    if not receipt_path.is_absolute():
        return False, "MLP receipt path must be absolute"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return False, f"MLP receipt could not be read as JSON: {error}"
    if not isinstance(receipt, dict):
        return False, "MLP receipt top-level value must be an object"
    if receipt.get("target") != "mlp":
        return False, "MLP receipt target is not 'mlp'"
    if receipt.get("ok") is not True:
        return False, "MLP receipt did not pass its required checks"

    facts = receipt.get("facts")
    if not isinstance(facts, dict):
        return False, "MLP receipt is missing facts"
    if facts.get("backend") != backend:
        return False, "MLP receipt backend does not match the configured backend"

    checks = _checks_by_id(receipt)
    if checks is None:
        return False, "MLP receipt checks are missing, malformed, or duplicated"

    required_ids = [
        "python.mlp_profile",
        "python.distribution.ase",
        "mlp.model",
        MLP_RUNTIME_PROBE_CHECK_ID,
    ]
    if backend in DEEPMD_BACKENDS:
        required_ids.extend(
            (
                "python.import.deepmd",
                "python.import.deepmd.calculator",
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
        if backend in DEEPMD_BACKENDS:
            required_ids.append("python.import.triton")
    for check_id in required_ids:
        failure = _check_failure(checks, check_id)
        if failure:
            return False, failure

    host = receipt.get("host")
    if not isinstance(host, dict):
        return False, "MLP receipt is missing host/interpreter metadata"
    python_version = host.get("python_version")
    if not isinstance(python_version, str) or not python_version.startswith("3.12."):
        return False, "MLP receipt does not report the documented Python 3.12 runtime"
    receipt_interpreter = _normal_absolute_path(host.get("python_executable"))
    configured_interpreter = _normal_absolute_path(str(python_executable))
    if receipt_interpreter is None or configured_interpreter is None:
        return False, "configured and receipted MLP interpreters must be absolute paths"
    if receipt_interpreter != configured_interpreter:
        return (
            False,
            "MLP receipt interpreter does not match the configured mlp_python: "
            f"receipt={receipt_interpreter}, configured={configured_interpreter}",
        )

    model = Path(model_path).expanduser()
    if not model.is_absolute():
        return False, "configured MLP model path must be absolute for receipt validation"
    model_sha256, model_error = _model_sha256(model)
    if model_sha256 is None:
        return False, model_error or "configured MLP model could not be hashed"
    receipt_sha256 = _receipt_model_hash(receipt, checks)
    if receipt_sha256 is None:
        return False, "MLP receipt does not record a model SHA-256"
    if receipt_sha256 != model_sha256:
        return (
            False,
            "MLP receipt model SHA-256 does not match the model staged for submission",
        )

    probe = _receipt_probe(receipt, checks)
    if probe is None or probe.get("requested") is not True or probe.get("status") != "pass":
        return False, "MLP receipt does not contain a successful runtime probe"
    if probe.get("backend") != backend:
        return False, "MLP runtime probe backend does not match the configured backend"
    if probe.get("model_sha256") != model_sha256:
        return False, "MLP runtime probe model SHA-256 does not match the configured model"
    allocation = probe.get("allocation")
    has_allocation = (
        isinstance(allocation, dict)
        and bool(allocation.get("scheduler_env"))
        and bool(allocation.get("job_id"))
    )
    if not has_allocation and not (
        allow_noncompute_probe and probe.get("noncompute_override") is True
    ):
        return False, "MLP runtime probe does not identify a scheduler compute allocation"
    for result_name in ("forces", "stress"):
        result = probe.get(result_name)
        if not isinstance(result, dict) or result.get("finite") is not True:
            return False, f"MLP runtime probe did not confirm finite {result_name}"
    if probe.get("unit_cell_filter") != "pass":
        return False, "MLP runtime probe did not complete the UnitCellFilter path"
    completed_steps = probe.get("optimizer_steps_completed")
    if type(completed_steps) is not int or completed_steps < 1:
        return False, "MLP runtime probe did not execute an optimizer step"

    if backend in DEEPMD_BACKENDS:
        pairing = facts.get("pairing")
        actual = pairing.get("actual") if isinstance(pairing, dict) else None
        if not isinstance(actual, dict):
            return False, "MLP receipt is missing the DeepMD/Torch pairing"
        for package, expected in DEEPMD_TORCH_PAIR.items():
            if package == "python":
                continue
            actual_version = actual.get(package)
            supported = (
                _is_supported_torch_version(actual_version)
                if package == "torch"
                else actual_version == expected
            )
            if not supported:
                return (
                    False,
                    f"MLP receipt has unsupported {package} version: "
                    f"expected {expected}, found {actual_version!r}",
                )

    if require_gpu:
        cuda = facts.get("cuda")
        if not isinstance(cuda, dict) or cuda.get("required") is not True:
            return False, "MLP receipt does not mark CUDA as required for this submission"
        if cuda.get("available") is not True:
            return False, "MLP receipt does not confirm CUDA availability"
        if not isinstance(cuda.get("torch_runtime"), str) or not cuda["torch_runtime"].strip():
            return False, "MLP receipt does not record the Torch CUDA runtime"
        nvidia_smi = cuda.get("nvidia_smi")
        gpus = nvidia_smi.get("gpus") if isinstance(nvidia_smi, dict) else None
        resolved_smi = nvidia_smi.get("resolved") if isinstance(nvidia_smi, dict) else None
        if not isinstance(resolved_smi, str) or not Path(resolved_smi).is_absolute():
            return False, "MLP receipt does not identify the nvidia-smi runtime query"
        if not isinstance(gpus, list) or not 1 <= len(gpus) <= 64:
            return False, "MLP receipt does not record a bounded visible GPU inventory"
        for gpu in gpus:
            if (
                not isinstance(gpu, dict)
                or type(gpu.get("index")) is not int
                or gpu["index"] < 0
                or not all(
                    isinstance(gpu.get(field), str) and gpu[field].strip()
                    for field in ("uuid", "name", "driver_version")
                )
            ):
                return False, "MLP receipt contains incomplete nvidia-smi GPU facts"

    return True, "MLP receipt matches the configured backend, interpreter, model, runtime probe, and GPU policy"


__all__ = ["validate_mlp_receipt"]
