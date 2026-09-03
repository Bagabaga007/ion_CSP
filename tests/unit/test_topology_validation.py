import json
from pathlib import Path

from ase import Atoms

from ion_CSP.topology_validation import (
    compare_smiles_to_geometry,
    validate_project_ion_topologies,
)


def _write_gjf(path: Path, atoms: Atoms, charge: int = 0):
    lines = ["# test", "", "topology test", "", f"{charge} 1"]
    lines.extend(
        f"{atom.symbol} {atom.x:.8f} {atom.y:.8f} {atom.z:.8f}"
        for atom in atoms
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n\n", encoding="utf-8")


def test_compare_smiles_to_geometry_accepts_isomorphic_chain():
    atoms = Atoms("N3", positions=[[0, 0, 0], [1.3, 0, 0], [2.6, 0, 0]])
    result = compare_smiles_to_geometry("[N][N][N]", atoms)
    assert result["topology_match"] is True
    assert result["elements_match"] is True


def test_compare_smiles_to_geometry_rejects_same_formula_different_graph():
    atoms = Atoms(
        "N3",
        positions=[[0, 0, 0], [1.3, 0, 0], [0.65, 1.125833, 0]],
    )
    result = compare_smiles_to_geometry("[N][N][N]", atoms)
    assert result["elements_match"] is True
    assert result["topology_match"] is False
    assert result["observed_edge_count"] == 3
    assert result["expected_edge_count"] == 2


def test_validate_project_topology_quarantines_changed_products(tmp_path):
    work = tmp_path / "project"
    optimized = work / "1_2_Gaussian_optimized/Optimized/charge_0"
    optimized.mkdir(parents=True)
    (work / "ions.csv").write_text(
        "Refcode,SMILES,Charge\nN3,NNN,0\n", encoding="utf-8"
    )
    _write_gjf(
        optimized / "N3.gjf",
        Atoms(
            "N3",
            positions=[[0, 0, 0], [1.3, 0, 0], [0.65, 1.125833, 0]],
        ),
    )
    (optimized / "N3.json").write_text("{}", encoding="utf-8")
    config = {"convert_SMILES": {"csv_file": "ions.csv"}}

    report = validate_project_ion_topologies(
        work, config, quarantine=True, raise_on_no_valid=False
    )

    assert report["valid_count"] == 0
    assert report["invalid_count"] == 1
    assert report["ions"]["N3"]["status"] == "topology_changed"
    bad = work / "1_2_Gaussian_optimized/Bad/topology_changed/charge_0"
    assert (bad / "N3.gjf").is_file()
    assert (bad / "N3.json").is_file()
    assert not (optimized / "N3.gjf").exists()
    saved = json.loads(
        (work / "1_2_Gaussian_optimized/topology_validation.json").read_text()
    )
    assert saved["ions"]["N3"]["topology_match"] is False
