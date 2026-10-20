import csv
import json
from pathlib import Path

import pytest
import yaml
from ase import Atoms
from ase.io import read as ase_read
from ase.io import write as ase_write

from ion_CSP.smoke_validation import (
    validate_local_csp_smoke,
    write_evidence_json,
)


FORMULA = "CH5NO2"
SITE_COUNT = 9
SCREENED_CANDIDATE_ID = "1.420_0"


def _write_gjf(
    path: Path, charge: int, atoms: list[tuple[str, float, float, float]]
) -> None:
    rows = "\n".join(
        f"{symbol:<2} {x: .6f} {y: .6f} {z: .6f}" for symbol, x, y, z in atoms
    )
    path.write_text(
        f"%chk={path.stem}.chk\n#P B3LYP/6-31G*\n\nSynthetic verifier fixture\n\n{charge} 1\n{rows}\n\n",
        encoding="utf-8",
    )


def _write_poscar(path: Path, symbols: str = "CH5NO2") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atoms = Atoms(symbols)
    atoms.positions = [
        [index * 0.2, (index % 3) * 0.3, (index % 2) * 0.4]
        for index in range(len(atoms))
    ]
    atoms.cell = [6.0, 6.0, 6.0]
    atoms.pbc = True
    ase_write(path, atoms, format="vasp")


def _write_marker(
    path: Path,
    stage: str,
    status: str = "SUCCESS",
    reason: str = "normal_termination_ionic_converged",
    exit_code: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"stage={stage}\nstatus={status}\nreason={reason}\nexit_code={exit_code}\n",
        encoding="utf-8",
    )


def _write_outcar(path: Path, *, converged: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "General timing and accounting informations for this job\n"
    if converged:
        content += (
            "reached required accuracy - stopping structural energy minimisation\n"
        )
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def completed_smoke(tmp_path: Path) -> Path:
    workdir = tmp_path / "smoke"
    workdir.mkdir()
    _write_gjf(
        workdir / "NH4.gjf",
        charge=1,
        atoms=[
            ("N", 0.0, 0.0, 0.0),
            ("H", 0.0, 0.0, 1.0),
            ("H", 0.94, 0.0, -0.33),
            ("H", -0.47, 0.81, -0.33),
            ("H", -0.47, -0.81, -0.33),
        ],
    )
    _write_gjf(
        workdir / "formate.gjf",
        charge=-1,
        atoms=[
            ("C", 0.0, 0.0, 0.0),
            ("O", 1.2, 0.0, 0.0),
            ("O", -0.6, 1.04, 0.0),
            ("H", -1.1, -0.6, 0.0),
        ],
    )

    machine = tmp_path / "machine.yaml"
    machine.write_text(
        yaml.safe_dump(
            {"context_type": "LocalContext", "batch_type": "Shell"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    mlp_resources = tmp_path / "mlp-resources.yaml"
    mlp_resources.write_text(
        yaml.safe_dump(
            {
                "number_node": 1,
                "cpu_per_node": 4,
                "gpu_per_node": 1,
                "group_size": 1,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    vasp_resources = tmp_path / "vasp-resources.yaml"
    vasp_resources.write_text(
        yaml.safe_dump(
            {
                "number_node": 1,
                "cpu_per_node": 4,
                "gpu_per_node": 0,
                "group_size": 1,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = {
        "gen_opt": {
            "species": ["NH4.gjf", "formate.gjf"],
            "ion_numbers": [1, 1],
            "num_per_group": 1,
            "space_groups_limit": 2,
            "nodes": 1,
            "mlp_workers": 1,
            "machine": str(machine),
            "resources": str(mlp_resources),
        },
        "read_mlp_density": {"n_screen": 1},
        "vasp_processing": {
            "nodes": 1,
            "machine": str(machine),
            "resources": str(vasp_resources),
        },
    }
    (workdir / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )

    workflow = {
        stage: {"current_status": "SUCCESS", "run_count": 1}
        for stage in (
            "1_generation",
            "1_optimization",
            "2_read_mlp_density",
            "3_vasp_optimization",
            "3_vasp_relaxation",
        )
    }
    (workdir / "workflow_status.yaml").write_text(
        yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8"
    )

    (workdir / "1_generated" / "primitive_cell").mkdir(parents=True)
    _write_poscar(workdir / "1_generated" / "primitive_cell" / "POSCAR_0")
    with (workdir / "1_generated" / "generation.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["Space_group", "POSCAR_num", "Bad_num", "Exception"])
        writer.writerow([1, 1, 0, "None"])
        writer.writerow([2, 0, 0, "None"])

    mlp = workdir / "2_mlp_optimized"
    _write_poscar(mlp / "CONTCAR_0")
    (mlp / "OUTCAR_0").write_text("synthetic MLP output\n", encoding="utf-8")
    max_density = mlp / "max_density"
    max_density.mkdir()
    with (max_density / "mlp_density_energy.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.writer(stream)
        writer.writerow(["Number", "MLP_E", "MLP_Density"])
        writer.writerow([0, -1.25, 1.42])

    vasp_input = workdir / "3_for_vasp_opt"
    _write_poscar(vasp_input / f"CONTCAR_{SCREENED_CANDIDATE_ID}")
    (vasp_input / f"OUTCAR_{SCREENED_CANDIDATE_ID}").write_text(
        "synthetic screened output\n", encoding="utf-8"
    )

    optimized = workdir / "4_vasp_optimized" / SCREENED_CANDIDATE_ID
    _write_marker(
        optimized / "ION_CSP_STAGE_STATUS",
        "rough",
        reason="intermediate_output_accepted_ionic_converged",
    )
    _write_outcar(optimized / "OUTCAR")
    _write_poscar(optimized / "CONTCAR")
    _write_marker(
        optimized / "fine" / "ION_CSP_STAGE_STATUS",
        "fine",
        reason="intermediate_output_accepted_ionic_converged",
    )
    _write_outcar(optimized / "fine" / "OUTCAR")
    _write_poscar(optimized / "fine" / "CONTCAR")
    final = optimized / "fine" / "final"
    _write_marker(final / "ION_CSP_STAGE_STATUS", "final")
    _write_outcar(final / "OUTCAR")
    _write_poscar(final / "CONTCAR")

    (workdir / "final_relaxation_summary.json").write_text(
        json.dumps(
            {
                "status": "SUBMISSION_COMPLETED",
                "eligible_count": 1,
                "eligible_structures": [SCREENED_CANDIDATE_ID],
                "skipped_count": 0,
                "skipped_structures": [],
            }
        ),
        encoding="utf-8",
    )
    with (workdir / "vasp_density_energy.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "Number",
                "MLP_Energy",
                "Rough_Energy",
                "Fine_Energy",
                "Final_Energy",
                "MLP_Density",
                "Rough_Density",
                "Fine_Density",
                "Final_Density",
                "Final_Ions_Check",
                "Final_PC",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Number": 0,
                "MLP_Energy": -1.25,
                "Rough_Energy": -1.3,
                "Fine_Energy": -1.4,
                "Final_Energy": -1.5,
                "MLP_Density": 1.42,
                "Rough_Density": 1.43,
                "Fine_Density": 1.44,
                "Final_Density": 1.45,
                "Final_Ions_Check": "True",
                "Final_PC": 0.72,
            }
        )
    _write_poscar(workdir / "POSCAR")
    return workdir


def _check(report, name: str) -> dict:
    return next(check for check in report["checks"] if check["check"] == name)


def test_valid_completed_smoke_passes_without_writes(completed_smoke: Path):
    report = validate_local_csp_smoke(completed_smoke)

    assert report["status"] == "PASS"
    assert report["read_only"] is True
    assert _check(report, "input_composition")["details"] == {
        "counts": {"C": 1, "H": 5, "N": 1, "O": 2},
        "formula": FORMULA,
        "site_count": SITE_COUNT,
    }
    assert _check(report, "final_poscar")["details"]["formula"] == FORMULA
    assert not (completed_smoke / "Final.cif").exists()
    assert not (completed_smoke / "smoke-evidence.json").exists()


def test_missing_workflow_stage_is_not_run_not_process_exit(completed_smoke: Path):
    status_path = completed_smoke / "workflow_status.yaml"
    statuses = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    statuses.pop("2_read_mlp_density")
    status_path.write_text(yaml.safe_dump(statuses), encoding="utf-8")

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "workflow:2_read_mlp_density")
    assert check["status"] == "NOT RUN"
    assert check["category"] == "missing"


def test_vasp_nonzero_exit_is_distinct_from_nonconvergence(completed_smoke: Path):
    marker = (
        completed_smoke
        / "4_vasp_optimized"
        / SCREENED_CANDIDATE_ID
        / "fine"
        / "final"
        / "ION_CSP_STAGE_STATUS"
    )
    _write_marker(marker, "final", status="FAILURE", reason="vasp_exit_1", exit_code=1)

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "vasp_stage:final")
    assert check["status"] == "FAIL"
    assert check["category"] == "process_exit"


def test_final_outcar_without_ionic_convergence_is_not_converged(completed_smoke: Path):
    final_outcar = (
        completed_smoke
        / "4_vasp_optimized"
        / SCREENED_CANDIDATE_ID
        / "fine"
        / "final"
        / "OUTCAR"
    )
    _write_outcar(final_outcar, converged=False)

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "vasp_stage:final")
    assert check["status"] == "FAIL"
    assert check["category"] == "not_converged"


def test_nonfinite_final_energy_fails_csv_validation(completed_smoke: Path):
    csv_path = completed_smoke / "vasp_density_energy.csv"
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
    rows[0]["Final_Energy"] = "NaN"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "final_csv")
    assert check["status"] == "FAIL"
    assert check["category"] == "invalid_scientific_value"


def test_final_ions_check_false_fails(completed_smoke: Path):
    csv_path = completed_smoke / "vasp_density_energy.csv"
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
    rows[0]["Final_Ions_Check"] = "False"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    report = validate_local_csp_smoke(completed_smoke)

    assert _check(report, "final_csv")["category"] == "topology_failure"


def test_final_poscar_composition_must_match_inputs(completed_smoke: Path):
    _write_poscar(completed_smoke / "POSCAR", symbols="H2")

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "final_poscar")
    assert check["status"] == "FAIL"
    assert check["category"] == "composition_mismatch"


def test_workload_caps_reject_more_than_two_attempts(completed_smoke: Path):
    config_path = completed_smoke / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"]["num_per_group"] = 2
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "smoke_caps")
    assert check["status"] == "FAIL"
    assert check["category"] == "workload_limit_exceeded"


def test_composition_site_count_is_bounded(completed_smoke: Path):
    config_path = completed_smoke / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["gen_opt"]["ion_numbers"] = [100, 100]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "input_composition")
    assert check["status"] == "FAIL"
    assert check["category"] == "workload_limit_exceeded"


def test_nonlocal_machine_config_is_rejected(completed_smoke: Path):
    config = yaml.safe_load(
        (completed_smoke / "config.yaml").read_text(encoding="utf-8")
    )
    machine_path = Path(config["gen_opt"]["machine"])
    machine_path.write_text(
        yaml.safe_dump({"context_type": "SSHContext", "batch_type": "Shell"}),
        encoding="utf-8",
    )

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "local_dispatch_config")
    assert check["status"] == "FAIL"
    assert check["category"] == "not_local_execution"


def test_oversized_metadata_is_rejected(completed_smoke: Path, monkeypatch):
    import ion_CSP.smoke_validation as smoke_validation

    monkeypatch.setattr(smoke_validation, "MAX_CSV_BYTES", 16)

    report = validate_local_csp_smoke(completed_smoke)

    check = _check(report, "generation")
    assert check["status"] == "FAIL"
    assert check["category"] == "file_too_large"


def test_optional_cif_is_roundtripped_and_never_overwritten(completed_smoke: Path):
    report = validate_local_csp_smoke(completed_smoke, write_cif=True)
    cif_path = completed_smoke / "Final.cif"

    assert report["status"] == "PASS"
    assert report["read_only"] is False
    assert _check(report, "cif_export")["status"] == "PASS"
    cif_atoms = ase_read(cif_path, format="cif")
    assert cif_atoms.get_chemical_formula(mode="hill") == FORMULA
    original = cif_path.read_bytes()

    second = validate_local_csp_smoke(completed_smoke, write_cif=True)

    assert second["status"] == "FAIL"
    assert _check(second, "cif_export")["category"] == "output_exists"
    assert cif_path.read_bytes() == original


def test_json_evidence_is_explicit_and_no_overwrite(completed_smoke: Path):
    report = validate_local_csp_smoke(completed_smoke)
    evidence = completed_smoke / "evidence.json"
    assert not evidence.exists()

    created = write_evidence_json(report, evidence)

    assert created == evidence
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "PASS"
    before = evidence.read_bytes()
    with pytest.raises(FileExistsError):
        write_evidence_json(report, evidence)
    assert evidence.read_bytes() == before


def test_verifier_rejects_unbounded_timeout(completed_smoke: Path):
    with pytest.raises(ValueError, match="timeout_seconds"):
        validate_local_csp_smoke(completed_smoke, timeout_seconds=10_000)
