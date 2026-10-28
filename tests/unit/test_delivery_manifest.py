import json
from pathlib import Path

from ion_CSP.release_manifest import create_manifest, verify_manifest


def _minimal_project(root: Path):
    (root / "src/ion_CSP/model").mkdir(parents=True)
    (root / "src/__init__.py").write_text("", encoding="utf-8")
    (root / "src/ion_CSP/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src/ion_CSP/model/model.pt").write_bytes(b"model")
    (root / "scripts").mkdir()
    (root / "scripts/tool.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "scripts/verify_local_csp_smoke.py").write_text(
        "from ion_CSP.smoke_validation import main\n", encoding="utf-8"
    )
    (root / "src/ion_CSP/smoke_validation.py").write_text(
        "def main():\n    return 0\n", encoding="utf-8"
    )
    (root / "docs").mkdir()
    (root / "docs/local_smoke_validation.md").write_text(
        "Read-only local smoke result validation.\n", encoding="utf-8"
    )
    for directory, filename, content in (
        ("config", "complete_config.yaml", "gen_opt: {}\n"),
        ("examples", "example_usage.py", "print('example')\n"),
        ("tests", "test_example.py", "def test_example():\n    assert True\n"),
    ):
        target = root / directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (root / "docs/history").mkdir(parents=True)
    (root / "docs/history/old.md").write_text("archive only\n", encoding="utf-8")
    (root / "docs/usage.md.orig").write_text("backup\n", encoding="utf-8")
    (root / "examples/server").mkdir(parents=True)
    (root / "examples/server/example_remote_machine.yaml").write_text(
        "machine: private\n", encoding="utf-8"
    )
    (root / "delivery/locks").mkdir(parents=True)
    (root / "delivery/locks/core.lock.json").write_text("{}\n", encoding="utf-8")
    (root / "delivery/locks/mlp-deepmd-torch.lock.toml").write_text(
        "[pairing]\npython='3.12.x'\ntorch='2.11.0'\n"
        "[cuda]\npolicy='runtime-receipt-required'\n",
        encoding="utf-8",
    )
    (root / "delivery/locks/mlp-requirements.txt").write_text(
        "torch==2.11.0\n", encoding="utf-8"
    )
    (root / "delivery/external-software.json").write_text(
        json.dumps({"software": [{"name": "Gaussian 16"}]}), encoding="utf-8"
    )
    (root / "delivery/config-contract.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='ion_CSP'\nversion='2.3.5'\n", encoding="utf-8"
    )
    (root / "setup.py").write_text("from setuptools import setup\nsetup()\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT test license\n", encoding="utf-8")
    (root / "uv.lock").write_text(
        "version=1\n[[package]]\nname='numpy'\nversion='1.26.4'\n", encoding="utf-8"
    )
    for filename in (
        "environment.yml",
        "environment-mlp.yml",
        "MANIFEST.in",
        "setup.cfg",
        "README.md",
        "CHANGELOG.md",
        "AUTO_LINKING_GUIDE.md",
        "DOCKER.md",
        "DPA4_BACKEND.md",
        "MATTERSIM_BACKEND.md",
        "Dockerfile",
        "Dockerfile.gpu",
        "docker-compose.yml",
        ".dockerignore",
    ):
        (root / filename).write_text("test\n", encoding="utf-8")


def test_manifest_binds_and_verifies_sources_artifacts_and_profiles(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _minimal_project(root)
    artifact_dir = root / "dist/release"
    artifact_dir.mkdir(parents=True)
    wheel = artifact_dir / "ion_csp-2.3.5-py3-none-any.whl"
    sdist = artifact_dir / "ion_csp-2.3.5.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    output = root / "delivery/release-manifest.json"

    manifest = create_manifest(root, artifact_dir, output)
    result = verify_manifest(root, output)

    assert result["ok"]
    assert {item["role"] for item in manifest["artifacts"]} == {"wheel", "sdist"}
    assert manifest["profiles"]["core"]["resolved_package_count"] == 1
    assert manifest["profiles"]["mlp"]["pairing"]["torch"] == "2.11.0"
    assert manifest["source_validation"]["python_parse"]["status"] == "pass"
    inventory_paths = {
        item["path"] for item in manifest["source_validation"]["inventory"]
    }
    assert {
        "src/ion_CSP/smoke_validation.py",
        "src/__init__.py",
        "scripts/verify_local_csp_smoke.py",
        "docs/local_smoke_validation.md",
        "MANIFEST.in",
        "setup.py",
        "setup.cfg",
        "LICENSE",
        "config/complete_config.yaml",
        "examples/example_usage.py",
        "tests/test_example.py",
    } <= inventory_paths
    assert "docs/history/old.md" not in inventory_paths
    assert "docs/usage.md.orig" not in inventory_paths
    assert "examples/server/example_remote_machine.yaml" not in inventory_paths

    wheel.write_bytes(b"tampered")
    tampered = verify_manifest(root, output)
    assert not tampered["ok"]
    assert "sha256 mismatch: dist/release/ion_csp-2.3.5-py3-none-any.whl" in tampered["errors"]

    (root / "setup.py").write_text("from setuptools import setup\nsetup(name='changed')\n", encoding="utf-8")
    changed_build_input = verify_manifest(root, output)
    assert "sha256 mismatch: setup.py" in changed_build_input["errors"]

    (root / "config/complete_config.yaml").write_text(
        "gen_opt:\n  mlp_backend: changed\n", encoding="utf-8"
    )
    changed_shipped_config = verify_manifest(root, output)
    assert (
        "sha256 mismatch: config/complete_config.yaml"
        in changed_shipped_config["errors"]
    )


def test_manifest_rejects_new_source_but_ignores_local_backup(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _minimal_project(root)
    artifact_dir = root / "dist/release"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "ion_csp-2.3.5-py3-none-any.whl").write_bytes(b"wheel")
    (artifact_dir / "ion_csp-2.3.5.tar.gz").write_bytes(b"sdist")
    output = root / "delivery/release-manifest.json"
    create_manifest(root, artifact_dir, output)

    (root / "src/ion_CSP/notes.orig").write_text("backup\n", encoding="utf-8")
    assert verify_manifest(root, output)["ok"]

    candidate_dir = root / "delivery/candidate-manifests"
    candidate_dir.mkdir()
    (candidate_dir / "internal-build.json").write_text('{"candidate":true}\n', encoding="utf-8")
    assert verify_manifest(root, output)["ok"]

    (root / "src/ion_CSP/new_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = verify_manifest(root, output)
    assert not result["ok"]
    assert any("source path inventory mismatch" in error for error in result["errors"])
