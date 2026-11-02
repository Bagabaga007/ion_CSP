import json
import os
import resource
import signal
import subprocess
import sys
from pathlib import Path

from ion_CSP import vasp_launch_diagnostics


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


def _run_script(
    tmp_path: Path,
    fake_mpirun_body: str,
    *,
    fake_vasp_body: str | None = None,
    **extra_env,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(fake_mpirun_body, encoding="utf-8")
    fake_mpirun.chmod(0o755)
    if fake_vasp_body is not None:
        fake_vasp = fake_bin / "vasp_std"
        fake_vasp.write_text(fake_vasp_body, encoding="utf-8")
        fake_vasp.chmod(0o755)

    env = dict(os.environ)
    env["DPDISPATCHER_CPU_PER_NODE"] = "1"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    src_path = str(SCRIPT.parents[2])
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_path
        if not existing_pythonpath
        else f"{src_path}{os.pathsep}{existing_pythonpath}"
    )
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


def test_sub_ori_default_launch_keeps_direct_vasp_command(tmp_path: Path):
    _write_inputs(tmp_path)
    arguments_file = tmp_path / "mpirun_arguments"
    fake_mpirun = _normal_vasp_body().replace(
        "#!/bin/bash\n",
        "#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$FAKE_ARGUMENTS\"\n",
        1,
    )

    completed = _run_script(
        tmp_path,
        fake_mpirun,
        ION_CSP_CAPTURE_VASP_DIAGNOSTICS="",
        FAKE_ARGUMENTS=str(arguments_file),
    )

    assert completed.returncode == 0
    assert arguments_file.read_text(encoding="utf-8").splitlines() == [
        "-n 1 vasp_std",
        "-n 1 vasp_std",
    ]
    assert not (tmp_path / "sample" / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json").exists()
    assert not (
        tmp_path
        / "sample"
        / "fine"
        / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
    ).exists()


def test_sub_ori_opt_in_records_rank_limits_and_metadata_only_outputs(tmp_path: Path):
    _write_inputs(tmp_path)
    fake_mpirun = """#!/bin/bash
shift 2
ulimit -S -f 17
export OMPI_COMM_WORLD_RANK=0
exec "$@"
"""
    fake_vasp = """#!/bin/bash
cp POSCAR CONTCAR
{
  echo ' vasp.6.3.0'
  echo ' executed on Linux'
  echo ' external pressure = 25.0 kB'
  echo ' PRIVATE_VASP_OUTPUT_CONTENT_MUST_NOT_BE_COPIED'
  echo ' General timing and accounting informations for this job'
} > OUTCAR
"""

    completed = _run_script(
        tmp_path,
        fake_mpirun,
        fake_vasp_body=fake_vasp,
        ION_CSP_CAPTURE_VASP_DIAGNOSTICS="1",
    )

    assert completed.returncode == 0
    for stage_dir, stage in (
        (tmp_path / "sample", "rough"),
        (tmp_path / "sample" / "fine", "fine"),
    ):
        marker_path = stage_dir / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
        marker_text = marker_path.read_text(encoding="utf-8")
        marker = json.loads(marker_text)
        assert marker["schema_version"] == 1
        assert marker["stage"] == stage
        assert marker["exit_code"] == 0
        assert marker["exit_class"] == "success"
        assert marker["rank_limits"] == [
            {"rank": 0, "soft_bytes": 17 * 1024, "hard_bytes": None}
        ] or marker["rank_limits"] == [
            {"rank": 0, "soft_bytes": 17 * 1024, "hard_bytes": 17 * 1024}
        ]
        assert marker["rank_limits_status"] == "recorded"
        outcar = next(item for item in marker["files"] if item["path"] == "OUTCAR")
        assert outcar["size_bytes"] > 0
        assert outcar["at_limit"] is False
        assert marker["candidate_status"] == "offender_unconfirmed"
        assert marker["candidate_basis"] == "size_match_not_fd_proof"
        assert "PRIVATE_VASP_OUTPUT_CONTENT_MUST_NOT_BE_COPIED" not in marker_text
        assert str(tmp_path) not in marker_text
        assert not (stage_dir / ".ion_csp_vasp_limit_ranks").exists()


def test_native_vasp_exec_restores_sigxfsz_and_preserves_rank_limit(tmp_path: Path):
    native_vasp = tmp_path / "vasp_std"
    source = r"""
#include <fcntl.h>
#include <signal.h>
#include <unistd.h>
int main(void) {
    struct sigaction disposition;
    if (sigaction(SIGXFSZ, 0, &disposition) != 0) return 71;
    if (disposition.sa_handler != SIG_DFL) return 72;
    int fd = open("OUTCAR", O_CREAT | O_TRUNC | O_WRONLY, 0600);
    if (fd < 0) return 73;
    char bytes[1024] = {0};
    if (write(fd, bytes, sizeof(bytes)) != (ssize_t)sizeof(bytes)) return 74;
    (void)write(fd, bytes, 1);
    return 75;
}
"""
    subprocess.run(
        ["cc", "-x", "c", "-o", str(native_vasp), "-"],
        input=source,
        text=True,
        capture_output=True,
        check=True,
    )
    direct = tmp_path / "direct"
    wrapped = tmp_path / "wrapped"
    direct.mkdir()
    wrapped.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["PYTHONPATH"] = str(SCRIPT.parents[2])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ION_CSP_VASP_DIAGNOSTIC_TOKEN"] = "a" * 32
    env["OMPI_COMM_WORLD_RANK"] = "0"

    def set_small_file_limit():
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))

    direct_result = subprocess.run(
        [str(native_vasp)], cwd=direct, env=env, preexec_fn=set_small_file_limit,
        capture_output=True, check=False,
    )
    wrapped_result = subprocess.run(
        [sys.executable, "-m", "ion_CSP.vasp_launch_diagnostics", "exec"],
        cwd=wrapped, env=env, preexec_fn=set_small_file_limit,
        capture_output=True, check=False,
    )
    assert direct_result.returncode == -signal.SIGXFSZ
    assert wrapped_result.returncode == direct_result.returncode
    assert (wrapped / "OUTCAR").stat().st_size == 1024
    stage_fd = os.open(wrapped, os.O_RDONLY | os.O_DIRECTORY)
    try:
        rank_limits, omitted, truncated = vasp_launch_diagnostics._collect_rank_limits(
            stage_fd, "a" * 32,
        )
    finally:
        os.close(stage_fd)
    assert rank_limits == [{"rank": 0, "soft_bytes": 1024, "hard_bytes": 1024}]
    assert omitted == 0 and truncated is False


def test_sub_ori_records_size_candidate_after_sigxfsz_like_failure(tmp_path: Path):
    _write_inputs(tmp_path)
    fake_mpirun = """#!/bin/bash
shift 2
ulimit -S -f 1
export OMPI_COMM_WORLD_RANK=0
exec "$@"
"""
    fake_vasp = """#!/usr/bin/env python3
import os
import signal

signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
fd = os.open("OUTCAR", os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
payload = (b"PRIVATE_VASP_OUTPUT_CONTENT_MUST_NOT_BE_COPIED " * 32)[:1024]
os.write(fd, payload)
os.write(fd, b"x")
"""

    completed = _run_script(
        tmp_path,
        fake_mpirun,
        fake_vasp_body=fake_vasp,
        ION_CSP_CAPTURE_VASP_DIAGNOSTICS="1",
    )

    assert completed.returncode == 0
    stage_dir = tmp_path / "sample"
    marker_path = stage_dir / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
    marker_text = marker_path.read_text(encoding="utf-8")
    marker = json.loads(marker_text)
    assert (stage_dir / "OUTCAR").stat().st_size == 1024
    assert marker["exit_code"] >= 128
    assert marker["exit_class"] == "signal_or_launcher_failure"
    assert marker["rank_limits"] == [
        {"rank": 0, "soft_bytes": 1024, "hard_bytes": None}
    ]
    outcar = next(item for item in marker["files"] if item["path"] == "OUTCAR")
    assert outcar == {"path": "OUTCAR", "size_bytes": 1024, "at_limit": True}
    assert marker["candidate_status"] == "candidate_at_limit"
    assert marker["candidate_basis"] == "size_match_not_fd_proof"
    assert "PRIVATE_VASP_OUTPUT_CONTENT_MUST_NOT_BE_COPIED" not in marker_text


def test_rank_limit_scanner_rejects_stale_symlink_and_hardlink_records(
    tmp_path: Path,
):
    token = "a" * 32
    stale_token = "b" * 32
    nonce = "c" * 32
    rank_dir = tmp_path / ".ion_csp_vasp_limit_ranks"
    rank_dir.mkdir(mode=0o700)

    def record_name(record_token: str, rank: str, nonce_token: str) -> str:
        return f"r-{record_token}-{rank}-512-1024-{nonce_token}.rec"

    (rank_dir / record_name(stale_token, "99", nonce)).touch(mode=0o600)
    valid_path = rank_dir / record_name(token, "0", nonce)
    valid_path.touch(mode=0o600)
    symlink_path = rank_dir / record_name(token, "97", "d" * 32)
    symlink_path.symlink_to(tmp_path / "not-a-rank-record")
    hardlink_source = rank_dir / "hardlink-source"
    hardlink_source.touch(mode=0o600)
    hardlink_path = rank_dir / record_name(token, "98", "e" * 32)
    os.link(hardlink_source, hardlink_path)

    stage_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        records, omitted_count, scan_truncated = (
            vasp_launch_diagnostics._collect_rank_limits(stage_fd, token)
        )
    finally:
        os.close(stage_fd)

    assert records == [{"rank": 0, "soft_bytes": 512, "hard_bytes": 1024}]
    assert omitted_count == 0
    assert scan_truncated is False


def test_trace_only_rank_record_binds_pre_exec_pid_without_changing_legacy_scan(
    tmp_path: Path, monkeypatch,
):
    token = "a" * 32
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ION_CSP_CAPTURE_FAILED_WRITE_TRACE", "1")
    monkeypatch.setenv("PMI_RANK", "2")
    vasp_launch_diagnostics._record_rank_limit(token, (2048, 4096))
    rank_names = [path.name for path in (tmp_path / ".ion_csp_vasp_limit_ranks").iterdir()]
    assert len(rank_names) == 1
    assert f"-p{os.getpid()}-" in rank_names[0]
    stage_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        records, omitted, truncated = vasp_launch_diagnostics._collect_rank_limits(
            stage_fd, token,
        )
    finally:
        os.close(stage_fd)
    assert records == [{"rank": 2, "soft_bytes": 2048, "hard_bytes": 4096, "pid": os.getpid()}]
    assert omitted == 0 and truncated is False


def test_trace_opt_in_denies_unpinned_strace_before_mpi_launch(tmp_path: Path):
    _write_inputs(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "unexpected_mpi_launch"
    fake_mpi = fake_bin / "mpirun"
    fake_mpi.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
    fake_mpi.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "PYTHONPATH": str(SCRIPT.parents[2]),
        "DPDISPATCHER_CPU_PER_NODE": "4",
        "ION_CSP_CAPTURE_VASP_DIAGNOSTICS": "1",
        "ION_CSP_CAPTURE_FAILED_WRITE_TRACE": "1",
        "ION_CSP_VASP_MAX_ATTEMPTS": "1",
    })
    env.pop("ION_CSP_STRACE_SHA256", None)
    subprocess.run(["bash", str(SCRIPT)], cwd=tmp_path, env=env,
                   capture_output=True, text=True, check=False)
    assert not marker.exists()
    assert "failed-write diagnostic launch gate denied" in (
        tmp_path / "sample" / "vasp.log"
    ).read_text(encoding="utf-8")
    assert not (tmp_path / "sample" / ".ion_csp_failed_write.strace").exists()


def test_trace_opt_in_cannot_fall_back_to_untraced_vasp(tmp_path: Path):
    _write_inputs(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "unexpected_mpi_launch"
    fake_mpi = fake_bin / "mpirun"
    fake_mpi.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
    fake_mpi.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "DPDISPATCHER_CPU_PER_NODE": "4",
        "ION_CSP_CAPTURE_VASP_DIAGNOSTICS": "0",
        "ION_CSP_CAPTURE_FAILED_WRITE_TRACE": "1",
    })
    result = subprocess.run(["bash", str(SCRIPT)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "no VASP launch occurred" in result.stderr
    assert not marker.exists()


def test_sub_ori_archives_diagnostic_marker_for_retryable_exit_255(tmp_path: Path):
    _write_inputs(tmp_path)
    counter = tmp_path / "diagnostic_attempt_count"
    fake_mpirun = """#!/bin/bash
shift 2
ulimit -S -f 17
export OMPI_COMM_WORLD_RANK=0
count=0
[[ ! -f "$FAKE_ATTEMPT_COUNTER" ]] || count=$(cat "$FAKE_ATTEMPT_COUNTER")
count=$((count + 1))
printf '%s\\n' "$count" > "$FAKE_ATTEMPT_COUNTER"
if [[ "$count" == "1" ]]; then
  "$@"
  exit 255
fi
exec "$@"
"""
    fake_vasp = _normal_vasp_body()

    completed = _run_script(
        tmp_path,
        fake_mpirun,
        fake_vasp_body=fake_vasp,
        ION_CSP_CAPTURE_VASP_DIAGNOSTICS="1",
        FAKE_ATTEMPT_COUNTER=str(counter),
    )

    assert completed.returncode == 0
    failed_marker = (
        tmp_path
        / "sample"
        / "failed_attempts"
        / "attempt_1_exit_255"
        / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
    )
    marker = json.loads(failed_marker.read_text(encoding="utf-8"))
    assert marker["stage"] == "rough"
    assert marker["exit_code"] == 255
    assert marker["exit_class"] == "signal_or_launcher_failure"
    assert marker["rank_limits_status"] == "recorded"
    assert failed_marker.stat().st_size <= 16 * 1024


def test_sub_supple_opt_in_records_final_stage_marker(tmp_path: Path):
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
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mpirun = fake_bin / "mpirun"
    fake_mpirun.write_text(
        """#!/bin/bash
shift 2
ulimit -S -f 17
export PMIX_RANK=0
exec "$@"
""",
        encoding="utf-8",
    )
    fake_mpirun.chmod(0o755)
    fake_vasp = fake_bin / "vasp_std"
    fake_vasp.write_text(_normal_vasp_body(pressure="18.0"), encoding="utf-8")
    fake_vasp.chmod(0o755)
    env = dict(os.environ)
    env["DPDISPATCHER_CPU_PER_NODE"] = "1"
    env["ION_CSP_CAPTURE_VASP_DIAGNOSTICS"] = "1"
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    src_path = str(SCRIPT.parents[2])
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        src_path
        if not existing_pythonpath
        else f"{src_path}{os.pathsep}{existing_pythonpath}"
    )

    completed = subprocess.run(
        ["bash", str(SUPPLE_SCRIPT)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    marker_path = fine_dir / "final" / "ION_CSP_VASP_LIMIT_DIAGNOSTIC.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["stage"] == "final"
    assert marker["exit_code"] == 0
    assert marker["rank_limits"] == [
        {"rank": 0, "soft_bytes": 17 * 1024, "hard_bytes": None}
    ] or marker["rank_limits"] == [
        {"rank": 0, "soft_bytes": 17 * 1024, "hard_bytes": 17 * 1024}
    ]
    assert marker_path.stat().st_size <= 16 * 1024


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
