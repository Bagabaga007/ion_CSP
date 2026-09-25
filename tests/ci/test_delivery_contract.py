import json
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_delivery_contract_files_and_common_manifest_fields_exist():
    required = (
        "delivery/config-contract.schema.json",
        "delivery/external-software.json",
        "delivery/locks/core.lock.json",
        "delivery/locks/mlp-deepmd-torch.lock.toml",
        "delivery/locks/mlp-requirements.txt",
        "scripts/release_preflight.py",
        "scripts/build_release_manifest.py",
        "scripts/ion_csp_workflow.py",
    )
    for relative in required:
        assert (PROJECT_ROOT / relative).is_file(), relative

    external = json.loads(
        (PROJECT_ROOT / "delivery/external-software.json").read_text(encoding="utf-8")
    )
    names = {item["name"] for item in external["software"]}
    assert names >= {"Multiwfn", "Phonopy", "Gaussian 16", "VASP"}


def test_core_and_mlp_locks_are_separate_and_pin_supported_pair():
    core = json.loads(
        (PROJECT_ROOT / "delivery/locks/core.lock.json").read_text(encoding="utf-8")
    )
    mlp = tomllib.loads(
        (PROJECT_ROOT / "delivery/locks/mlp-deepmd-torch.lock.toml").read_text(
            encoding="utf-8"
        )
    )
    assert core["authoritative_lock"] == "../../uv.lock"
    assert {"deepmd-kit", "torch", "CUDA"} <= set(core["excluded_stacks"])
    assert mlp["pairing"] == {
        "python": "3.12.x",
        "deepmd-kit": "3.2.0",
        "torch": "2.11.0",
        "ase": "3.23.0",
    }
    assert mlp["cuda"]["policy"] == "runtime-receipt-required"


def test_console_scripts_publish_wrapper_preflight_and_manifest():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["ion-csp-workflow"] == "ion_CSP.workflow_cli:main"
    assert scripts["ion-csp-preflight"] == "ion_CSP.preflight:main"
    assert scripts["ion-csp-release-manifest"] == "ion_CSP.release_manifest:main"
    excluded = project["tool"]["setuptools"]["exclude-package-data"]["ion_CSP"]
    assert "param/*.orig" in excluded
    manifest_rules = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "exclude delivery/release-manifest.json" in manifest_rules
