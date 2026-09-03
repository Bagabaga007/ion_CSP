import os
import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2] / "src" / "ion_CSP" / "param" / "sub_ori.sh"
)
SUPPLE_SCRIPT = (
    Path(__file__).resolve().parents[2] / "src" / "ion_CSP" / "param" / "sub_supple.sh"
)


def _write_inputs(tmp_path: Path):
    (tmp_path / "INCAR_1").write_text(
        "EDIFF = 1e-4\nISIF = 8\nNSW = 200\n", encoding="utf-8"
    )
    (tmp_path / "INCAR_2").write_text(
        "EDIFF = 1e-6\nISIF = 8\nNSW = 250\n", encoding="utf-8"
    )
    (tmp_path / "POTCAR_N").write_text("N-potential\n", encoding="utf-8")
    (tmp_path / "POTCAR_O").write_text("O-potential\n", encoding="utf-8")
    (tmp_path / "CONTCAR_sample").write_text(
        """sample
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
N O
1 1
Direct
0.0 0.0 0.0
0.25 0.25 0.25
""",
        encoding="utf-8",
    )


def _run_script(tmp_path: Path, fake_mpirun_body: str, **extra_env):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(fake_mpirun_body, encoding="utf-8")
    fake_mpirun.chmod(0o755)

    env = dict(os.environ)
    env["DPDISPATCHER_CPU_PER_NODE"] = "1"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_sub_ori_preserves_ediff_and_skips_unneeded_volume_step(tmp_path: Path):
    _write_inputs(tmp_path)
    completed = _run_script(
        tmp_path,
        """#!/bin/bash
cp POSCAR CONTCAR
{
  echo ' vasp.6.3.0'
  echo ' executed on Linux'
  echo ' external pressure = 0.0 kB'
  echo ' reached required accuracy - stopping structural energy minimisation'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
""",
    )

    assert completed.returncode == 0
    assert (tmp_path / "sample" / "POTCAR").read_text(encoding="utf-8") == (
        "N-potential\nO-potential\n"
    )
    rough_incar = (
        tmp_path / "sample" / "constrained_cycles" / "cycle_1" / "ions" / "INCAR"
    ).read_text(encoding="utf-8")
    fine_incar = (
        tmp_path
        / "sample"
        / "fine"
        / "constrained_cycles"
        / "cycle_1"
        / "ions"
        / "INCAR"
    ).read_text(encoding="utf-8")
    assert "EDIFF = 1e-4" in rough_incar
    assert "ISIF = 2" in rough_incar
    assert "EDIFF = 1e-6" in fine_incar
    assert "ISIF = 2" in fine_incar
    assert not (
        tmp_path / "sample" / "constrained_cycles" / "cycle_1" / "volume"
    ).exists()
    assert "NOT_NEEDED" in (
        tmp_path / "sample" / "CONSTRAINED_RELAXATION_HISTORY.tsv"
    ).read_text(encoding="utf-8")
    assert "status=SUCCESS" in (tmp_path / "sample" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    assert "status=SUCCESS" in (
        tmp_path / "sample" / "fine" / "ION_CSP_STAGE_STATUS"
    ).read_text(encoding="utf-8")


def test_sub_ori_uses_bounded_isif7_step_when_pressure_is_high(tmp_path: Path):
    _write_inputs(tmp_path)
    marker = tmp_path / "volume_step_seen"
    completed = _run_script(
        tmp_path,
        """#!/bin/bash
cp POSCAR CONTCAR
isif=$(awk -F= '/^[[:space:]]*ISIF[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2}' INCAR)
pressure=0.0
if [[ "$isif" == "7" ]]; then
  touch "$FAKE_VOLUME_MARKER"
elif [[ ! -f "$FAKE_VOLUME_MARKER" ]]; then
  pressure=10.0
fi
{
  echo ' vasp.6.3.0'
  echo ' executed on Linux'
  echo " external pressure = $pressure kB"
  echo ' reached required accuracy - stopping structural energy minimisation'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
""",
        FAKE_VOLUME_MARKER=str(marker),
    )

    assert completed.returncode == 0
    assert marker.exists()
    volume_incar = (
        tmp_path / "sample" / "constrained_cycles" / "cycle_1" / "volume" / "INCAR"
    ).read_text(encoding="utf-8")
    assert "EDIFF = 1e-4" in volume_incar
    assert "ISIF = 7" in volume_incar
    assert "NSW = 3" in volume_incar
    history = (tmp_path / "sample" / "CONSTRAINED_RELAXATION_HISTORY.tsv").read_text(
        encoding="utf-8"
    )
    assert "STEP_COMPLETE" in history
    assert "NOT_NEEDED" in history
    assert "status=SUCCESS" in (tmp_path / "sample" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )


def test_sub_supple_skips_explicitly_failed_fine_stage(tmp_path: Path):
    _write_inputs(tmp_path)
    (tmp_path / "INCAR_3").write_text(
        "EDIFF = 1e-6\nISIF = 3\nNSW = 250\n", encoding="utf-8"
    )
    fine_dir = tmp_path / "sample" / "fine"
    fine_dir.mkdir(parents=True)
    (fine_dir / "CONTCAR").write_text(
        (tmp_path / "CONTCAR_sample").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fine_dir / "ION_CSP_STAGE_STATUS").write_text(
        "stage=fine\nstatus=FAILURE\nreason=pressure_not_converged\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "unexpected_vasp_run"
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(
        f"#!/bin/bash\ntouch {marker}\n",
        encoding="utf-8",
    )
    fake_mpirun.chmod(0o755)
    env = dict(os.environ)
    env["DPDISPATCHER_CPU_PER_NODE"] = "1"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    completed = subprocess.run(
        ["bash", str(SUPPLE_SCRIPT)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert not marker.exists()
    final_status = (fine_dir / "final" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    assert "status=FAILURE" in final_status
    assert "reason=fine_stage_not_successful" in final_status
