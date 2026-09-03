"""Molecular connectivity validation for Gaussian and crystal structures."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable
import csv
import json
import logging
import shutil

import networkx as nx
from ase import Atoms
from ase.data import covalent_radii
from ase.io import read
from rdkit import Chem


def connectivity_graph(atoms: Atoms, scale: float = 1.25) -> nx.Graph:
    """Build an element-labelled graph from geometry using covalent radii."""
    graph = nx.Graph()
    symbols = atoms.get_chemical_symbols()
    for index, symbol in enumerate(symbols):
        graph.add_node(index, element=symbol)
    if not symbols:
        return graph

    distances = atoms.get_all_distances(mic=bool(any(atoms.pbc)))
    for first in range(len(atoms)):
        for second in range(first + 1, len(atoms)):
            cutoff = scale * (
                covalent_radii[atoms.numbers[first]]
                + covalent_radii[atoms.numbers[second]]
            )
            if distances[first, second] <= cutoff:
                graph.add_edge(first, second)
    return graph


def smiles_graph(smiles: str) -> nx.Graph:
    """Build an element-labelled adjacency graph from a SMILES string."""
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"Invalid SMILES: {smiles}")
    molecule = Chem.AddHs(molecule)
    graph = nx.Graph()
    for atom in molecule.GetAtoms():
        graph.add_node(atom.GetIdx(), element=atom.GetSymbol())
    for bond in molecule.GetBonds():
        graph.add_edge(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())
    return graph


def component_graphs(atoms: Atoms, scale: float = 1.25) -> list[nx.Graph]:
    """Return one copied graph for each connected molecular component."""
    graph = connectivity_graph(atoms, scale=scale)
    return [graph.subgraph(nodes).copy() for nodes in nx.connected_components(graph)]


def graph_formula(graph: nx.Graph) -> dict[str, int]:
    """Return the element-count formula for one graph."""
    return dict(Counter(data["element"] for _, data in graph.nodes(data=True)))


def graphs_isomorphic(first: nx.Graph, second: nx.Graph) -> bool:
    """Compare adjacency and element labels while allowing atom reordering."""
    return nx.is_isomorphic(
        first,
        second,
        node_match=lambda left, right: left.get("element") == right.get("element"),
    )


def graph_multiset_matches(
    expected: Iterable[nx.Graph], observed: Iterable[nx.Graph]
) -> bool:
    """Compare molecular graphs with multiplicity."""
    remaining = list(observed)
    expected_graphs = list(expected)
    if len(expected_graphs) != len(remaining):
        return False
    for graph in expected_graphs:
        for index, candidate in enumerate(remaining):
            if graphs_isomorphic(graph, candidate):
                remaining.pop(index)
                break
        else:
            return False
    return not remaining


def compare_smiles_to_geometry(
    smiles: str, atoms: Atoms, scale: float = 1.25
) -> dict:
    """Compare a SMILES adjacency graph with a geometry-derived graph."""
    expected = smiles_graph(smiles)
    observed = connectivity_graph(atoms, scale=scale)
    expected_elements = Counter(
        data["element"] for _, data in expected.nodes(data=True)
    )
    observed_elements = Counter(
        data["element"] for _, data in observed.nodes(data=True)
    )
    return {
        "topology_match": graphs_isomorphic(expected, observed),
        "elements_match": expected_elements == observed_elements,
        "expected_formula": dict(expected_elements),
        "observed_formula": dict(observed_elements),
        "expected_atom_count": expected.number_of_nodes(),
        "observed_atom_count": observed.number_of_nodes(),
        "expected_edge_count": expected.number_of_edges(),
        "observed_edge_count": observed.number_of_edges(),
        "expected_edges": sorted(
            [sorted(edge) for edge in expected.edges()]
        ),
        "observed_edges": sorted(
            [sorted(edge) for edge in observed.edges()]
        ),
    }


def _quarantine_products(
    optimized_dir: Path, bad_dir: Path, refcode: str
) -> list[str]:
    """Move invalid optimized GJF/JSON files without overwriting older evidence."""
    moved = []
    bad_dir.mkdir(parents=True, exist_ok=True)
    for suffix in (".gjf", ".json"):
        source = optimized_dir / f"{refcode}{suffix}"
        if not source.exists() and not source.is_symlink():
            continue
        destination = bad_dir / source.name
        if destination.exists() or destination.is_symlink():
            counter = 1
            while True:
                candidate = bad_dir / f"{source.name}.previous_{counter}"
                if not candidate.exists() and not candidate.is_symlink():
                    destination = candidate
                    break
                counter += 1
        shutil.move(str(source), str(destination))
        moved.append(str(destination))
    return moved


def validate_project_ion_topologies(
    work_dir: Path,
    config: dict,
    *,
    quarantine: bool = True,
    raise_on_no_valid: bool = True,
    scale: float = 1.25,
) -> dict:
    """Validate project-input ions against CSV SMILES before combinations."""
    work_dir = Path(work_dir).resolve()
    csv_name = config.get("convert_SMILES", {}).get("csv_file", "")
    if not csv_name:
        raise ValueError("convert_SMILES.csv_file is required for topology validation")
    csv_path = work_dir / csv_name
    if not csv_path.is_file():
        raise FileNotFoundError(f"Topology validation CSV not found: {csv_path}")

    gaussian_root = work_dir / "1_2_Gaussian_optimized"
    optimized_root = gaussian_root / "Optimized"
    convert_config = config.get("convert_SMILES", {})
    snapshot_enabled = bool(convert_config.get("structure_snapshots", True))
    snapshot_dpi = int(convert_config.get("snapshot_dpi", 160))
    report = {
        "csv_file": str(csv_path),
        "scale": scale,
        "ions": {},
        "valid_count": 0,
        "invalid_count": 0,
        "missing_count": 0,
    }

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        id_column = "Refcode" if "Refcode" in fieldnames else (
            "Number" if "Number" in fieldnames else None
        )
        if id_column is None or "SMILES" not in fieldnames or "Charge" not in fieldnames:
            raise ValueError(
                "Topology validation CSV requires Refcode/Number, SMILES, and Charge"
            )
        for row in reader:
            refcode = str(row[id_column])
            charge = int(row["Charge"])
            charge_folder = f"charge_{charge}"
            optimized_dir = optimized_root / charge_folder
            gjf_path = optimized_dir / f"{refcode}.gjf"
            item = {
                "refcode": refcode,
                "smiles": row["SMILES"],
                "charge": charge,
                "optimized_gjf": str(gjf_path),
            }
            if not gjf_path.is_file():
                item["status"] = "missing_optimized_geometry"
                report["missing_count"] += 1
            else:
                try:
                    comparison = compare_smiles_to_geometry(
                        row["SMILES"], read(gjf_path), scale=scale
                    )
                    item.update(comparison)
                    if snapshot_enabled:
                        try:
                            from ion_CSP.structure_snapshots import (
                                render_gjf_snapshots,
                            )

                            snapshot = render_gjf_snapshots(
                                gjf_path,
                                row["SMILES"],
                                work_dir
                                / "structure_snapshots"
                                / "optimized"
                                / charge_folder
                                / refcode,
                                refcode=refcode,
                                stage="optimized",
                                scale=scale,
                                dpi=snapshot_dpi,
                            )
                            item["snapshot_manifest"] = snapshot["manifest"]
                        except Exception as error:
                            item["snapshot_error"] = (
                                f"{type(error).__name__}: {error}"
                            )
                            logging.warning(
                                "Unable to render optimized snapshots for %s: %s",
                                refcode,
                                item["snapshot_error"],
                            )
                    if comparison["topology_match"]:
                        item["status"] = "valid"
                        report["valid_count"] += 1
                    else:
                        item["status"] = "topology_changed"
                        report["invalid_count"] += 1
                        if quarantine:
                            item["quarantined_files"] = _quarantine_products(
                                optimized_dir,
                                gaussian_root / "Bad" / "topology_changed" / charge_folder,
                                refcode,
                            )
                except Exception as error:
                    item["status"] = "validation_error"
                    item["error"] = f"{type(error).__name__}: {error}"
                    report["invalid_count"] += 1
                    if quarantine:
                        item["quarantined_files"] = _quarantine_products(
                            optimized_dir,
                            gaussian_root / "Bad" / "topology_changed" / charge_folder,
                            refcode,
                        )
            report["ions"][refcode] = item

    report_path = gaussian_root / "topology_validation.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    if raise_on_no_valid and report["valid_count"] == 0:
        raise ValueError(
            "No project input ions preserved their SMILES connectivity after "
            f"Gaussian optimization; see {report_path}"
        )
    return report
