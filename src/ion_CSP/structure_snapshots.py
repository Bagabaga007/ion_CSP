"""Headless multi-view molecular snapshots with explicit topology diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import numpy as np
from ase import Atoms
from ase.data import chemical_symbols, covalent_radii
from ase.data.colors import jmol_colors
from ase.io import read
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from rdkit import Chem

from ion_CSP.topology_validation import connectivity_graph, smiles_graph


VIEWPOINTS = {
    "xy": (90, -90),
    "xz": (0, -90),
    "yz": (0, 0),
    "isometric": (24, 38),
}


def _expected_bonds(smiles: str) -> tuple[nx.Graph, dict[tuple[int, int], float]]:
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"Invalid SMILES: {smiles}")
    molecule = Chem.AddHs(molecule)
    bonds = {}
    for bond in molecule.GetBonds():
        edge = tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))
        bonds[edge] = float(bond.GetBondTypeAsDouble())
    return smiles_graph(smiles), bonds


def _mapped_expected_bonds(
    expected: nx.Graph,
    observed: nx.Graph,
    bonds: dict[tuple[int, int], float],
) -> tuple[bool, dict[int, int], dict[tuple[int, int], float]]:
    matcher = nx.algorithms.isomorphism.GraphMatcher(
        expected,
        observed,
        node_match=lambda left, right: left.get("element") == right.get("element"),
    )
    topology_match = matcher.is_isomorphic()
    mapping = dict(matcher.mapping) if topology_match else {
        index: index for index in expected.nodes if index in observed
    }
    mapped = {}
    for (first, second), order in bonds.items():
        if first in mapping and second in mapping:
            edge = tuple(sorted((mapping[first], mapping[second])))
            mapped[edge] = order
    return topology_match, mapping, mapped


def _configure_axes(ax, positions: np.ndarray, elev: float, azim: float) -> None:
    center = positions.mean(axis=0) if len(positions) else np.zeros(3)
    span = float(np.ptp(positions, axis=0).max()) if len(positions) else 1.0
    half = max(span * 0.68, 1.25)
    ax.set_xlim(center[0] - half, center[0] + half)
    ax.set_ylim(center[1] - half, center[1] + half)
    ax.set_zlim(center[2] - half, center[2] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.set_proj_type("ortho")
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()


def _draw_structure(
    ax,
    atoms: Atoms,
    expected_bonds: dict[tuple[int, int], float],
    observed_edges: set[tuple[int, int]],
) -> None:
    positions = atoms.get_positions()
    expected_edges = set(expected_bonds)
    for edge in sorted(expected_edges | observed_edges):
        first, second = edge
        if first >= len(atoms) or second >= len(atoms):
            continue
        xyz = positions[[first, second]]
        if edge in expected_edges and edge in observed_edges:
            color, style = "#555555", "-"
            width = 1.4 + 0.8 * expected_bonds[edge]
        elif edge in expected_edges:
            color, style, width = "#d62728", "--", 2.4
        else:
            color, style, width = "#ff8c00", ":", 2.8
        ax.plot(
            xyz[:, 0], xyz[:, 1], xyz[:, 2],
            color=color, linestyle=style, linewidth=width, alpha=0.9,
            zorder=1,
        )

    numbers = atoms.get_atomic_numbers()
    radii = np.asarray([covalent_radii[number] for number in numbers])
    sizes = 150.0 * np.square(np.clip(radii, 0.35, None))
    colors = [jmol_colors[number] for number in numbers]
    ax.scatter(
        positions[:, 0], positions[:, 1], positions[:, 2],
        s=sizes, c=colors, edgecolors="black", linewidths=0.55,
        depthshade=True, zorder=3,
    )
    for index, (symbol, position) in enumerate(
        zip(atoms.get_chemical_symbols(), positions), start=1
    ):
        ax.text(*position, f"{symbol}{index}", fontsize=7, color="black", zorder=4)


def render_structure_snapshots(
    atoms: Atoms,
    smiles: str | None,
    output_dir: Path,
    *,
    refcode: str,
    stage: str,
    source_path: Path | None = None,
    scale: float = 1.25,
    dpi: int = 160,
) -> dict:
    """Render four bonded views plus a contact sheet and topology manifest."""
    output_dir = Path(output_dir)
    dpi = int(dpi)
    if dpi < 72:
        raise ValueError("snapshot dpi must be at least 72")
    output_dir.mkdir(parents=True, exist_ok=True)
    observed = connectivity_graph(atoms, scale=scale)
    observed_edges = {tuple(sorted(edge)) for edge in observed.edges()}
    if smiles:
        expected, bond_orders = _expected_bonds(smiles)
        topology_match, mapping, mapped_bonds = _mapped_expected_bonds(
            expected, observed, bond_orders
        )
        expected_atom_count = expected.number_of_nodes()
        reference_source = "smiles"
        status = "MATCH" if topology_match else "MISMATCH"
    else:
        topology_match = None
        mapping = {index: index for index in observed.nodes}
        mapped_bonds = {edge: 1.0 for edge in observed_edges}
        expected_atom_count = None
        reference_source = "geometry"
        status = "GEOMETRY ONLY"
    expected_edges = set(mapped_bonds)
    missing_edges = sorted(expected_edges - observed_edges)
    unexpected_edges = sorted(observed_edges - expected_edges)
    positions = atoms.get_positions()

    image_files = {}
    for view_name, (elev, azim) in VIEWPOINTS.items():
        figure = Figure(figsize=(4.2, 4.2), dpi=dpi)
        FigureCanvasAgg(figure)
        ax = figure.add_subplot(111, projection="3d")
        _draw_structure(ax, atoms, mapped_bonds, observed_edges)
        _configure_axes(ax, positions, elev, azim)
        ax.set_title(f"{refcode} · {stage} · {view_name} · {status}", fontsize=10)
        path = output_dir / f"{refcode}_{stage}_{view_name}.png"
        figure.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
        figure.clear()
        image_files[view_name] = path.name

    figure = Figure(figsize=(9.2, 9.2), dpi=dpi)
    FigureCanvasAgg(figure)
    for index, (view_name, (elev, azim)) in enumerate(VIEWPOINTS.items(), start=1):
        ax = figure.add_subplot(2, 2, index, projection="3d")
        _draw_structure(ax, atoms, mapped_bonds, observed_edges)
        _configure_axes(ax, positions, elev, azim)
        ax.set_title(view_name, fontsize=10)
    if reference_source == "smiles":
        legend = [
            Line2D([0], [0], color="#555555", lw=2.2, label="SMILES bond present"),
            Line2D([0], [0], color="#d62728", lw=2.2, ls="--", label="SMILES bond missing"),
            Line2D([0], [0], color="#ff8c00", lw=2.2, ls=":", label="Unexpected bond"),
        ]
        subtitle = (
            f"{refcode} · {stage} · topology {status} · "
            "bond width follows SMILES order"
        )
    else:
        legend = [
            Line2D([0], [0], color="#555555", lw=2.2, label="Geometry-derived bond")
        ]
        subtitle = f"{refcode} · {stage} · {status} · no source SMILES available"
    figure.legend(handles=legend, loc="lower center", ncol=3, fontsize=9)
    figure.suptitle(subtitle, fontsize=12)
    figure.subplots_adjust(top=0.93, bottom=0.08, wspace=0.02, hspace=0.08)
    contact_sheet = output_dir / f"{refcode}_{stage}_multiview.png"
    figure.savefig(contact_sheet, dpi=dpi, bbox_inches="tight", facecolor="white")
    figure.clear()

    report = {
        "refcode": refcode,
        "stage": stage,
        "source_path": str(source_path) if source_path else None,
        "smiles": smiles,
        "reference_source": reference_source,
        "scale": scale,
        "atom_count": len(atoms),
        "topology_match": topology_match,
        "expected_atom_count": expected_atom_count,
        "observed_edge_count": len(observed_edges),
        "atom_mapping_expected_to_observed": {
            str(key): value for key, value in sorted(mapping.items())
        },
        "expected_bonds": [
            {"atoms": list(edge), "smiles_order": order}
            for edge, order in sorted(mapped_bonds.items())
        ],
        "observed_edges": [list(edge) for edge in sorted(observed_edges)],
        "missing_edges": [list(edge) for edge in missing_edges],
        "unexpected_edges": [list(edge) for edge in unexpected_edges],
        "views": image_files,
        "multiview": contact_sheet.name,
    }
    manifest = output_dir / f"{refcode}_{stage}_snapshot.json"
    manifest.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report["manifest"] = str(manifest)
    return report


def _read_gaussian_coordinates(gjf_path: Path) -> Atoms:
    """Read only the Cartesian block, tolerating trailing ModRedundant lines."""
    lines = Path(gjf_path).read_text(encoding="utf-8", errors="replace").splitlines()
    start = None
    for index, line in enumerate(lines):
        fields = line.split()
        if (
            len(fields) == 2
            and fields[0].lstrip("+-").isdigit()
            and fields[1].isdigit()
        ):
            start = index + 1
            break
    if start is None:
        raise ValueError(f"Gaussian charge/multiplicity line not found: {gjf_path}")

    symbols = []
    positions = []
    for line in lines[start:]:
        fields = line.split()
        if not fields:
            if symbols:
                break
            continue
        symbol_field = fields[0].split("(", 1)[0]
        if symbol_field.isdigit():
            atomic_number = int(symbol_field)
            if atomic_number <= 0 or atomic_number >= len(chemical_symbols):
                break
            symbol = chemical_symbols[atomic_number]
        else:
            symbol = symbol_field.capitalize()
            if symbol not in chemical_symbols:
                break
        coordinate_fields = fields[1:4]
        if len(fields) >= 5 and fields[1] in {"-1", "0", "1"}:
            coordinate_fields = fields[2:5]
        if len(coordinate_fields) != 3:
            break
        try:
            xyz = [float(value.replace("D", "E")) for value in coordinate_fields]
        except ValueError:
            break
        symbols.append(symbol)
        positions.append(xyz)
    if not symbols:
        raise ValueError(f"Gaussian Cartesian coordinates not found: {gjf_path}")
    return Atoms(symbols=symbols, positions=positions)


def render_gjf_snapshots(
    gjf_path: Path,
    smiles: str | None,
    output_dir: Path,
    *,
    refcode: str,
    stage: str,
    scale: float = 1.25,
    dpi: int = 160,
) -> dict:
    """Read a Gaussian structure with ASE and render its topology views."""
    gjf_path = Path(gjf_path)
    try:
        atoms = read(gjf_path)
    except Exception:
        atoms = _read_gaussian_coordinates(gjf_path)
    return render_structure_snapshots(
        atoms,
        smiles,
        output_dir,
        refcode=refcode,
        stage=stage,
        source_path=gjf_path,
        scale=scale,
        dpi=dpi,
    )
