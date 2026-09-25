import json
from pathlib import Path

from ion_CSP.release_manifest import create_manifest, verify_manifest


def _minimal_project(root: Path):
    (root / "src/ion_CSP/model").mkdir(parents=True)
    (root / "src/ion_CSP/__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src/ion_CSP/model/model.pt").write_bytes(b"model")
    (root / "scripts").mkdir()
    (root / "scripts/tool.py").write_text("print('ok')\n", encoding="utf-8")
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
    (root / "uv.lock").write_text(
        "version=1\n[[package]]\nname='numpy'\nversion='1.26.4'\n", encoding="utf-8"
    )
    for filename in ("environment.yml", "environment-mlp.yml", "MANIFEST.in"):
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

    wheel.write_bytes(b"tampered")
    tampered = verify_manifest(root, output)
    assert not tampered["ok"]
    assert "sha256 mismatch: dist/release/ion_csp-2.3.5-py3-none-any.whl" in tampered["errors"]


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

    (root / "src/ion_CSP/new_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = verify_manifest(root, output)
    assert not result["ok"]
    assert any("source path inventory mismatch" in error for error in result["errors"])
