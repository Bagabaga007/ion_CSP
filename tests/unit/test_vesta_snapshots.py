"""Final POSCAR snapshot provenance and non-fatal VESTA diagnostics."""

import hashlib
import json

from ase import Atoms
from ase.io import write

from ion_CSP.vasp_processing import VaspProcessing
from ion_CSP.vesta_snapshots import _cached_manifest, _ensure_vesta_user_config


def test_cached_manifest_requires_all_three_matching_images(tmp_path):
    source_hash = "a" * 64
    directory = tmp_path / (source_hash[:12] + "-case")
    directory.mkdir()
    views = {}
    for axis in "abc":
        image = directory / f"{axis}.png"
        image.write_bytes(axis.encode())
        views[axis] = {
            "file": image.name,
            "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        }
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"source_sha256": source_hash, "views": views}))

    assert _cached_manifest(tmp_path, source_hash) == manifest
    (directory / "b.png").write_bytes(b"changed")
    assert _cached_manifest(tmp_path, source_hash) is None


def test_missing_vesta_records_failure_without_changing_final_poscar(tmp_path):
    work = tmp_path / "combo_1"
    work.mkdir()
    poscar = work / "POSCAR"
    write(poscar, Atoms("N2", positions=[[0, 0, 0], [1, 1, 1]], cell=[5, 5, 5], pbc=True), format="vasp")
    original = poscar.read_bytes()

    result = VaspProcessing(work).render_final_vesta_snapshots(
        poscar, vesta_executable=str(tmp_path / "missing-VESTA")
    )

    assert result is None
    assert poscar.read_bytes() == original
    status = json.loads((work / "crystal_snapshots" / "final_snapshot_status.json").read_text())
    assert status["status"] == "failed"
    assert status["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert "VESTA executable is unavailable" in status["error"]


def test_existing_vesta_user_configuration_is_never_overwritten(monkeypatch, tmp_path):
    home = tmp_path / "home"
    configuration = home / ".VESTA" / "VESTA.ini"
    configuration.parent.mkdir(parents=True)
    configuration.write_text("custom user settings", encoding="utf-8")
    monkeypatch.setattr("ion_CSP.vesta_snapshots.Path.home", lambda: home)

    _ensure_vesta_user_config(tmp_path / "VESTA-gui")

    assert configuration.read_text(encoding="utf-8") == "custom user settings"


def test_no_final_candidate_status_has_no_stale_poscar_reference(tmp_path):
    work = tmp_path / "combo_1"
    work.mkdir()
    (work / "POSCAR").write_text("older result", encoding="utf-8")

    VaspProcessing(work).record_no_final_vesta_snapshot()

    status = json.loads((work / "crystal_snapshots" / "final_snapshot_status.json").read_text())
    assert status["status"] == "no_exported_final_poscar"
    assert status["source_poscar"] is None
    assert status["source_sha256"] is None
    assert status["manifest"] is None


def test_success_status_points_to_rendered_final_manifest(monkeypatch, tmp_path):
    work = tmp_path / "combo_1"
    work.mkdir()
    poscar = work / "POSCAR"
    write(poscar, Atoms("N2", positions=[[0, 0, 0], [1, 1, 1]], cell=[5, 5, 5], pbc=True), format="vasp")
    manifest = work / "crystal_snapshots" / "case" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    calls = []

    def fake_render(source, output_dir, *, executable):
        calls.append((source, output_dir, executable))
        return manifest

    monkeypatch.setattr("ion_CSP.vesta_snapshots.render_vesta_views", fake_render)

    result = VaspProcessing(work).render_final_vesta_snapshots(poscar)

    assert result == manifest
    assert calls == [(poscar, work / "crystal_snapshots", None)]
    status = json.loads((work / "crystal_snapshots" / "final_snapshot_status.json").read_text())
    assert status["status"] == "success"
    assert status["manifest"] == str(manifest)
    assert status["source_sha256"] == hashlib.sha256(poscar.read_bytes()).hexdigest()
