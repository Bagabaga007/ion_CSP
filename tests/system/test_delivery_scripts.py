import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_release_preflight_script_emits_json_and_nonzero_failure():
    env = os.environ.copy()
    env["PATH"] = ""
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts/release_preflight.py"),
            "--target",
            "external",
            "--external-profile",
            "gaussian",
            "--compact",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["target"] == "external"
    assert payload["ok"] is False
    assert {item["check_id"] for item in payload["checks"]} >= {
        "external.gaussian.g16",
        "external.gaussian.formchk",
    }


def test_workflow_wrapper_help_exposes_separate_entrypoints():
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts/ion_csp_workflow.py"), "--help"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "{ee,csp}" in completed.stdout
    assert "config.yaml" in completed.stdout
