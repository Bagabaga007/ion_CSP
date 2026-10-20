"""Scope and durable summary checks for historical VESTA backfill."""

import json
import hashlib

from PIL import Image

from ion_CSP.backfill_vesta_snapshots import audit_backfill, discover_root_poscars, run_backfill


def test_discover_root_poscars_excludes_nested_vasp_and_stable(tmp_path):
    results = tmp_path / "results"
    combo = results / "N7_1val_single_salt" / "2_nitrogen_combos" / "combo_2"
    (combo / "stable").mkdir(parents=True)
    (combo / "4_vasp_optimized" / "structure" / "fine" / "final").mkdir(parents=True)
    (combo / "POSCAR").write_text("root")
    (combo / "stable" / "POSCAR").write_text("nested stable")
    (combo / "4_vasp_optimized" / "structure" / "fine" / "final" / "POSCAR").write_text("nested VASP")
    unrelated = results / "N7_1val_single_salt" / "2_nitrogen_combos" / "combo_other"
    unrelated.mkdir()
    (unrelated / "POSCAR").write_text("not numbered")

    assert discover_root_poscars(results) == [combo / "POSCAR"]


def test_limited_backfill_writes_resumable_summary(monkeypatch, tmp_path):
    results = tmp_path / "results"
    for number in (1, 2):
        combo = results / "N8+_1val_single_salt" / "2_nitrogen_combos" / f"combo_{number}"
        combo.mkdir(parents=True)
        (combo / "POSCAR").write_text(f"POSCAR {number}")

    def fake_render(poscar, _executable):
        return {
            "combo": str(poscar.parent),
            "poscar": str(poscar),
            "updated_at": "test",
            "status": "success",
            "manifest": str(poscar.parent / "crystal_snapshots" / "manifest.json"),
            "error": None,
        }

    monkeypatch.setattr("ion_CSP.backfill_vesta_snapshots._render_one", fake_render)
    result = run_backfill(results, workers=2, limit=1)

    assert result["state"] == "PARTIAL_LIMIT"
    assert result["discovered_count"] == 2
    assert result["success_count"] == 1
    persisted = json.loads((results / "vesta_snapshot_backfill.json").read_text())
    assert persisted["state"] == "PARTIAL_LIMIT"
    assert len(persisted["items"]) == 1


def test_backfill_defers_combo_with_running_csp_process(monkeypatch, tmp_path):
    results = tmp_path / "results"
    combos = []
    for number in (1, 2):
        combo = results / "N8-_1val_single_salt" / "2_nitrogen_combos" / f"combo_{number}"
        combo.mkdir(parents=True)
        (combo / "POSCAR").write_text(f"POSCAR {number}")
        combos.append(combo)

    monkeypatch.setattr(
        "ion_CSP.backfill_vesta_snapshots.active_combo_roots",
        lambda _root: {combos[1].resolve()},
    )
    monkeypatch.setattr(
        "ion_CSP.backfill_vesta_snapshots._render_one",
        lambda poscar, _executable: {
            "combo": str(poscar.parent), "poscar": str(poscar),
            "updated_at": "test", "status": "success", "manifest": "test", "error": None,
        },
    )

    result = run_backfill(results, workers=2)

    assert result["state"] == "DEFERRED_ACTIVE"
    assert result["success_count"] == 1
    assert result["deferred_count"] == 1
    assert result["deferred_combos"] == [str(combos[1])]
    assert not (combos[1] / "crystal_snapshots").exists()


def test_audit_accepts_direct_hash_timestamp_directory(tmp_path):
    results = tmp_path / "results"
    combo = results / "N7_1val_single_salt" / "2_nitrogen_combos" / "combo_1"
    combo.mkdir(parents=True)
    poscar = combo / "POSCAR"
    poscar.write_bytes(b"frozen source")
    source_hash = hashlib.sha256(poscar.read_bytes()).hexdigest()
    folder = combo / "crystal_snapshots" / f"{source_hash[:12]}-20261019T151500123456"
    folder.mkdir(parents=True)
    (folder / "POSCAR").write_bytes(poscar.read_bytes())
    views = {}
    for axis in "abc":
        image_path = folder / f"{axis}.png"
        Image.new("RGB", (240, 220), "white").save(image_path)
        views[axis] = {
            "file": image_path.name,
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        }
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps({"source_sha256": source_hash, "views": views}))
    (combo / "crystal_snapshots" / "final_snapshot_status.json").write_text(
        json.dumps({"status": "success", "source_sha256": source_hash, "manifest": str(manifest)})
    )

    report = audit_backfill(results)

    assert report["verified_count"] == 1
    assert report["issue_count"] == 0
    assert (results / "vesta_snapshot_audit.json").is_file()
