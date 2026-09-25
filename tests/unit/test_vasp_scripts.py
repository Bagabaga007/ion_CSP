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


def _normal_vasp_body(*, pressure: str = "25.0", converged: bool = False):
    ionic = (
        "  echo ' reached required accuracy - stopping structural energy minimisation'\n"
        if converged
        else ""
    )
    return f"""#!/bin/bash
cp POSCAR CONTCAR
{{
  echo ' vasp.6.3.0'
  echo ' executed on Linux'
  echo ' external pressure = {pressure} kB'
{ionic}  echo ' General timing and accounting informations for this job'
}} > OUTCAR
"""


def test_sub_ori_uses_isif8_and_accepts_normal_metastable_output(tmp_path: Path):
    _write_inputs(tmp_path)
    completed = _run_script(tmp_path, _normal_vasp_body())

    assert completed.returncode == 0
    sample = tmp_path / "sample"
    assert (sample / "POTCAR").read_text(encoding="utf-8") == (
        "N-potential\nO-potential\n"
    )
    assert "ISIF = 8" in (sample / "INCAR").read_text(encoding="utf-8")
    assert "IBRION = 3" in (sample / "INCAR").read_text(encoding="utf-8")
    assert "SMASS = 0.4" in (sample / "INCAR").read_text(encoding="utf-8")
    assert "ISIF = 8" in (sample / "fine" / "INCAR").read_text(encoding="utf-8")
    assert "IBRION = 1" in (sample / "fine" / "INCAR").read_text(encoding="utf-8")
    assert "status=SUCCESS" in (sample / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    assert "reason=intermediate_output_accepted_relaxed_gate" in (
        sample / "ION_CSP_STAGE_STATUS"
    ).read_text(encoding="utf-8")
    assert "status=SUCCESS" in (sample / "fine" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    history = (sample / "CONSTRAINED_RELAXATION_HISTORY.tsv").read_text(
        encoding="utf-8"
    )
    assert "25.0" in history
    assert "NOT_REACHED" in history


def test_sub_ori_ignores_strict_ionic_gate_for_intermediate_stages(tmp_path: Path):
    _write_inputs(tmp_path)
    completed = _run_script(
        tmp_path,
        _normal_vasp_body(),
        ION_CSP_REQUIRE_IONIC_CONVERGENCE="1",
    )

    assert completed.returncode == 0
    sample = tmp_path / "sample"
    assert "status=SUCCESS" in (sample / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    assert "status=SUCCESS" in (sample / "fine" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )


def test_sub_ori_keeps_fatal_intermediate_output_for_final(tmp_path: Path):
    _write_inputs(tmp_path)
    completed = _run_script(
        tmp_path,
        """#!/bin/bash
cp POSCAR CONTCAR
{
  echo ' vasp.6.3.0'
  echo ' ZBRENT: fatal error in bracketing'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
exit 1
""",
    )

    assert completed.returncode == 0
    sample = tmp_path / "sample"
    rough_status = (sample / "ION_CSP_STAGE_STATUS").read_text(encoding="utf-8")
    fine_status = (sample / "fine" / "ION_CSP_STAGE_STATUS").read_text(encoding="utf-8")
    assert "status=SUCCESS" in rough_status
    assert "intermediate_output_accepted_vasp_exit_1" in rough_status
    assert "status=SUCCESS" in fine_status
    assert "intermediate_output_accepted_vasp_exit_1" in fine_status
    assert (sample / "fine" / "OUTCAR").exists()


def test_sub_ori_retries_transient_mpi_exit_255(tmp_path: Path):
    _write_inputs(tmp_path)
    counter = tmp_path / "vasp_attempt_count"
    completed = _run_script(
        tmp_path,
        """#!/bin/bash
count=0
[[ ! -f "$FAKE_ATTEMPT_COUNTER" ]] || count=$(cat "$FAKE_ATTEMPT_COUNTER")
count=$((count + 1))
printf '%s\\n' "$count" > "$FAKE_ATTEMPT_COUNTER"
if [[ "$count" == "1" ]]; then
  echo 'Caught signal 8 (Floating point exception)'
  exit 255
fi
cp POSCAR CONTCAR
{
  echo ' vasp.6.3.0'
  echo ' executed on Linux'
  echo ' external pressure = 0.0 kB'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
""",
        FAKE_ATTEMPT_COUNTER=str(counter),
    )

    assert completed.returncode == 0
    assert counter.read_text(encoding="utf-8").strip() == "3"
    failed_attempt = tmp_path / "sample" / "failed_attempts" / "attempt_1_exit_255"
    assert "Floating point exception" in (failed_attempt / "vasp.log").read_text(
        encoding="utf-8"
    )
    assert "reason=transient_vasp_exit_255" in (
        failed_attempt / "ION_CSP_STAGE_STATUS"
    ).read_text(encoding="utf-8")
    assert "status=SUCCESS" in (
        tmp_path / "sample" / "ION_CSP_STAGE_STATUS"
    ).read_text(encoding="utf-8")


def test_sub_supple_keeps_final_isif3_and_accepts_normal_metastable_output(
    tmp_path: Path,
):
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
        "stage=fine\nstatus=SUCCESS\nreason=normal_termination_relaxed_gate\n",
        encoding="utf-8",
    )

    marker = tmp_path / "final_incar_seen"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(
        f"""#!/bin/bash
+grep -Eq '^[[:space:]]*ISIF[[:space:]]*=[[:space:]]*3([[:space:]#!]|$)' INCAR && touch {marker}
+cp POSCAR CONTCAR
+{{
+  echo ' vasp.6.3.0'
+  echo ' executed on Linux'
+  echo ' external pressure = 18.0 kB'
+  echo ' General timing and accounting informations for this job'
+}} > OUTCAR
+""".replace("\n+", "\n"),
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
    assert marker.exists()
    final_dir = fine_dir / "final"
    assert "ISIF = 3" in (final_dir / "INCAR").read_text(encoding="utf-8")
    assert "status=SUCCESS" in (
        final_dir / "ION_CSP_STAGE_STATUS"
    ).read_text(encoding="utf-8")


def test_sub_supple_rejects_fatal_final_output(tmp_path: Path):
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
        "stage=fine\nstatus=SUCCESS\nreason=intermediate_output_accepted_fatal_VASP_marker\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(
        """#!/bin/bash
cp POSCAR CONTCAR
{
  echo ' vasp.6.3.0'
  echo ' ZBRENT: fatal error in bracketing'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
exit 1
""",
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
    final_status = (fine_dir / "final" / "ION_CSP_STAGE_STATUS").read_text(
        encoding="utf-8"
    )
    assert "status=FAILURE" in final_status
    assert "reason=vasp_exit_1" in final_status


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
    fake_mpirun.write_text(f"#!/bin/bash\ntouch {marker}\n", encoding="utf-8")
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
