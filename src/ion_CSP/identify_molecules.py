import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import yaml
from ase.io import read

from ion_CSP.topology_validation import (
    component_graphs,
    graph_formula,
    graph_multiset_matches,
)


def _initial_graphs(base_dir: Path):
    """Load expected molecular graphs, including configured ion multiplicity."""
    base_dir = Path(base_dir)
    specs = []
    config_path = base_dir / "config.yaml"
    if config_path.is_file():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            gen_opt = config.get("gen_opt", {})
            species = gen_opt.get("species", [])
            ion_numbers = gen_opt.get("ion_numbers", [])
            if (
                isinstance(species, list)
                and isinstance(ion_numbers, list)
                and len(species) == len(ion_numbers)
            ):
                specs = [
                    (base_dir / species_name, int(count))
                    for species_name, count in zip(species, ion_numbers)
                ]
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            logging.warning("Unable to read molecule multiplicity from %s: %s", config_path, error)

    if not specs:
        try:
            specs = [(path, 1) for path in base_dir.iterdir() if path.suffix == ".gjf"]
        except (OSError, TypeError, AttributeError):
            specs = []

    graphs = []
    initial_formulas = []
    seen_formulas = set()
    for path, count in specs:
        if not path.is_file() or count < 1:
            continue
        for graph in component_graphs(read(path)):
            formula = graph_formula(graph)
            formula_key = tuple(sorted(formula.items()))
            if formula_key not in seen_formulas:
                initial_formulas.append(formula)
                seen_formulas.add(formula_key)
            graphs.extend([graph.copy() for _ in range(count)])
    return graphs, initial_formulas


def identify_molecules(
    atoms, base_dir: Path = Path("./")
) -> Tuple[List[Dict[str, int]], bool]:
    """Identify molecular components and compare element-labelled graphs.

    The comparison is graph-isomorphic and multiplicity-sensitive. It detects
    fragmentation, merging, new bonds, broken bonds, and the wrong number of
    repeated ions while allowing atom reordering.
    """
    observed_graphs = component_graphs(atoms)
    merged_molecules = defaultdict(int)
    for graph in observed_graphs:
        molecule_tuple = frozenset(graph_formula(graph).items())
        merged_molecules[molecule_tuple] += 1

    expected_graphs, initial_information = _initial_graphs(base_dir)
    molecules_flag = graph_multiset_matches(expected_graphs, observed_graphs)
    return merged_molecules, molecules_flag, initial_information


def format_molecule_output(molecule_dict):
    """Format a molecular formula in a stable element order."""
    fixed_order = ["C", "N", "O", "H"]
    total_atoms = sum(molecule_dict.values())
    output = []
    for element in fixed_order:
        if element in molecule_dict:
            output.append(f"{element}{molecule_dict[element]}")
    other_elements = [elem for elem in molecule_dict if elem not in fixed_order]
    for element in sorted(other_elements):
        output.append(f"{element}{molecule_dict[element]}")
    return "".join(output), total_atoms


def molecules_information(
    molecules: List[Dict[str, int]],
    molecules_flag: bool,
    initial_info: List[Dict[str, int]],
):
    """Log initial and geometry-identified molecular formulas."""
    logging.info("Initial molecules:")
    for idx, molecule in enumerate(initial_info):
        formatted_output, total_atoms = format_molecule_output(molecule)
        logging.info(
            f"  Molecule {idx + 1} (Total Atoms: {total_atoms}): {formatted_output}"
        )

    logging.info("Identified independent molecules:")
    for idx, (molecule, count) in enumerate(molecules.items()):
        molecule_dict = dict(molecule)
        formatted_output, total_atoms = format_molecule_output(molecule_dict)
        logging.info(
            f"  Molecule {idx + 1} (Total Atoms: {total_atoms}, Count: {count}): {formatted_output}"
        )

    if molecules_flag:
        logging.info("Molecular Comparison Successful\n")
    else:
        logging.warning("Molecular Comparison Failed\n")
