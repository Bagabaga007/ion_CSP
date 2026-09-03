import json
from pathlib import Path

from ase import Atoms
from PIL import Image
import pytest

from ion_CSP.structure_snapshots import render_structure_snapshots


def test_render_structure_snapshots_writes_four_views_and_contact_sheet(tmp_path: Path):
    atoms = Atoms("N3", positions=[[0, 0, 0], [1.3, 0, 0], [2.6, 0, 0]])

    report = render_structure_snapshots(
        atoms,
        "[N][N][N]",
        tmp_path,
        refcode="N3",
        stage="initial",
        dpi=72,
    )

    assert report["topology_match"] is True
    assert report["missing_edges"] == []
    assert report["unexpected_edges"] == []
    files = [tmp_path / name for name in report["views"].values()]
    files.append(tmp_path / report["multiview"])
    assert len(files) == 5
    for path in files:
        assert path.stat().st_size > 1000
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.width > 100
            assert image.height > 100
    saved = json.loads(Path(report["manifest"]).read_text())
    assert saved["topology_match"] is True
    assert len(saved["expected_bonds"]) == 2


def test_render_structure_snapshots_highlights_unexpected_geometry_bond(tmp_path: Path):
    atoms = Atoms(
        "N3",
        positions=[[0, 0, 0], [1.3, 0, 0], [0.65, 1.125833, 0]],
    )

    report = render_structure_snapshots(
        atoms,
        "[N][N][N]",
        tmp_path,
        refcode="N3",
        stage="optimized",
        dpi=72,
    )

    assert report["topology_match"] is False
    assert report["unexpected_edges"] == [[0, 2]]
    assert report["observed_edge_count"] == 3


def test_render_structure_snapshots_rejects_too_low_dpi(tmp_path: Path):
    with pytest.raises(ValueError, match="at least 72"):
        render_structure_snapshots(
            Atoms("N2", positions=[[0, 0, 0], [1.3, 0, 0]]),
            "NN",
            tmp_path,
            refcode="N2",
            stage="initial",
            dpi=71,
        )
