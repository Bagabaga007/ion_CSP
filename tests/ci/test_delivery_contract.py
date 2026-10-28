import json
import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[2]

CORE_DEPENDENCY_FLOORS = {
    "ase": "3.23.0",
    "scipy": "1.15.2",
    "numpy": "1.26.4",
    "pandas": "2.2.3",
    "psutil": "7.0.0",
    "pyxtal": "1.0.4",
    "pyyaml": "6.0.2",
    "phonopy": "2.34.1",
    "rdkit": "2024.3.3",
}

CORE_CONDA_FLOORS = {
    **CORE_DEPENDENCY_FLOORS,
    # Keep Conda's existing canonical spelling for the date-based RDKit
    # release while testing the same semantic minimum as the PyPI metadata.
    "rdkit": "2024.03.3",
}

MLP_EXACT_REQUIREMENTS = {
    "deepmd-kit[torch]": "3.2.0",
    "torch": "2.11.0",
    "triton": "3.6.0",
    "ase": "3.23.0",
}


def _project_requirement_map():
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return {
        requirement.name.lower(): requirement
        for requirement in map(Requirement, project["project"]["dependencies"])
    }


def _conda_requirement_map(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        str(spec).split(">=", 1)[0].lower(): str(spec)
        for spec in data["dependencies"]
        if isinstance(spec, str) and ">=" in spec
    }


def test_core_dependencies_use_minimum_floors_and_keep_mlp_separate():
    project_requirements = _project_requirement_map()
    for name, floor in CORE_DEPENDENCY_FLOORS.items():
        requirement = project_requirements[name]
        assert str(requirement.specifier) == f">={floor}"
        assert "==" not in str(requirement.specifier)

    conda_requirements = _conda_requirement_map(PROJECT_ROOT / "environment.yml")
    for name, floor in CORE_CONDA_FLOORS.items():
        assert conda_requirements[name] == f"{name}>={floor}"

    project_names = set(project_requirements)
    assert not project_names.intersection({"deepmd-kit", "torch", "triton"})

    mlp_environment = yaml.safe_load(
        (PROJECT_ROOT / "environment-mlp.yml").read_text(encoding="utf-8")
    )
    mlp_specs = {
        str(spec).split("==", 1)[0].split("[", 1)[0].lower(): str(spec)
        for spec in mlp_environment["dependencies"][-1]["pip"]
    }
    for package, version in MLP_EXACT_REQUIREMENTS.items():
        key = package.split("[", 1)[0].lower()
        expected = f"{package}=={version}"
        assert mlp_specs[key] == expected

    mlp_requirements = {
        line.split("==", 1)[0].split("[", 1)[0].lower(): line
        for line in (
            PROJECT_ROOT / "delivery/locks/mlp-requirements.txt"
        ).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    for package, version in MLP_EXACT_REQUIREMENTS.items():
        key = package.split("[", 1)[0].lower()
        assert mlp_requirements[key] == f"{package}=={version}"


def test_core_lock_metadata_matches_relaxed_project_requirements():
    """A frozen install must not silently retain the old exact requirements."""
    lock = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    root = next(package for package in lock["package"] if package["name"] == "ion-csp")
    locked_requirements = {
        item["name"].lower(): item["specifier"]
        for item in root["metadata"]["requires-dist"]
    }
    project_requirements = _project_requirement_map()
    for name, floor in CORE_DEPENDENCY_FLOORS.items():
        assert locked_requirements[name] == str(project_requirements[name].specifier)
        assert locked_requirements[name] == f">={floor}"
        locked_packages = [
            package for package in lock["package"] if package["name"] == name
        ]
        assert locked_packages
        assert all(
            package["version"] in project_requirements[name].specifier
            for package in locked_packages
        )


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
        "scripts/ion_csp_profile.py",
        "scripts/ion_csp_local_check.py",
        "scripts/verify_local_csp_smoke.py",
        "src/ion_CSP/smoke_validation.py",
        "docs/local_smoke_validation.md",
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
        "triton": "3.6.0",
        "ase": "3.23.0",
    }
    assert mlp["cuda"]["policy"] == "runtime-receipt-required"


def test_csp_delivery_contract_declares_mlp_receipt_gate():
    schema = json.loads(
        (PROJECT_ROOT / "delivery/config-contract.schema.json").read_text(
            encoding="utf-8"
        )
    )
    generation = schema["$defs"]["csp"]["properties"]["gen_opt"]
    assert "mlp_preflight_receipt" in generation["properties"]
    assert "mlp_allow_unallocated_probe" in generation["properties"]
    assert any(
        "mlp_preflight_receipt" in clause.get("then", {}).get("required", [])
        for clause in generation["allOf"]
    )


def test_console_scripts_publish_wrapper_preflight_and_manifest():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["ion-csp-workflow"] == "ion_CSP.workflow_cli:main"
    assert scripts["ion-csp-profile"] == "ion_CSP.compute_profile:main"
    assert scripts["ion-csp-local-check"] == "ion_CSP.local_readiness:main"
    assert scripts["ion-csp-preflight"] == "ion_CSP.preflight:main"
    assert scripts["ion-csp-release-manifest"] == "ion_CSP.release_manifest:main"
    assert scripts["ion-csp-verify-local-smoke"] == "ion_CSP.smoke_validation:main"
    package_discovery = project["tool"]["setuptools"]["packages"]["find"]
    assert "ion_CSP*" in package_discovery["include"]
    excluded = project["tool"]["setuptools"]["exclude-package-data"]["ion_CSP"]
    assert "param/*.orig" in excluded
    manifest_rules = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "exclude delivery/release-manifest.json" in manifest_rules
    assert "prune delivery/candidate-manifests" in manifest_rules
    assert "prune .claude" in manifest_rules
    assert "prune .github" in manifest_rules
    assert "prune .vscode" in manifest_rules
    assert "prune docs/history" in manifest_rules
    assert "exclude examples/server/example_remote_machine.yaml" in manifest_rules
    for include in (
        "recursive-include config *",
        "recursive-include docs *",
        "recursive-include examples *",
        "recursive-include tests *",
        "recursive-include scripts *",
        "recursive-include delivery *",
        "recursive-include src/ion_CSP *",
    ):
        assert include in manifest_rules
