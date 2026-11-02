import json
import hashlib
import os
import secrets
import sys
import subprocess
import time
import pytest
from ion_CSP.failed_write_trace import (
    MAX_RAW_BYTES,
    MAX_SUMMARY_BYTES,
    RANK_DIRECTORY,
    encode_summary,
    parse_trace,
    rank_pid_mapping,
    supervise,
    main,
)


def _mapping():
    return {1001: 0, 1002: 1, 1003: 2, 1004: 3}


def _trace(path="/scratch/task/OUTCAR", pid=1001):
    return (
        f'{pid} 1793590000.001 write(6<{path}>, ""..., 1) = -1 EFBIG (File size limit exceeded)\n'
        f'{pid} 1793590000.002 --- SIGXFSZ {{si_signo=SIGXFSZ}} ---\n'
    )


def test_failed_write_with_deleted_path_is_confirmed_but_reports_size_unavailable(tmp_path):
    trace = _trace(f"{tmp_path}/OUTCAR (deleted)")
    summary = parse_trace(trace, stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "failed_fd_path_confirmed"
    assert summary["events"][0]["rank"] == 0
    assert summary["events"][0]["path"] == "OUTCAR (deleted)"
    assert summary["events"][0]["path_state"] == "stage_relative"
    assert summary["events"][0]["requested_bytes"] == 1
    assert summary["events"][0]["requested_units"] == "bytes"
    assert "size" not in summary["events"][0]
    assert len(encode_summary(summary)) <= MAX_SUMMARY_BYTES


def test_multiple_failed_fd_paths_are_ambiguous(tmp_path):
    trace = _trace(f"{tmp_path}/OUTCAR") + _trace(f"{tmp_path}/vasprun.xml")
    summary = parse_trace(trace, stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "offender_unconfirmed"
    assert summary["trace_parse_ambiguous"]


def test_unresolved_second_failed_fd_prevents_false_confirmation(tmp_path):
    trace = _trace(f"{tmp_path}/OUTCAR") + _trace("/etc/unknown-offender")
    summary = parse_trace(trace, stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "offender_unconfirmed"
    assert summary["trace_parse_ambiguous"]


def test_trace_without_complete_rank_mapping_is_unconfirmed(tmp_path):
    summary = parse_trace(_trace(), stage_dir=tmp_path, pid_to_rank={1001: 0})
    assert summary["status"] == "offender_unconfirmed"
    assert summary["rank_mapping"] == "incomplete_or_invalid"
    assert summary["events"][0]["pid"] == 1001


def test_truncated_and_overlong_trace_fail_closed(tmp_path):
    truncated = parse_trace(_trace(), stage_dir=tmp_path, pid_to_rank=_mapping(), truncated=True)
    overlong = parse_trace(
        ("1001 1793590000.0 " + "x" * 17000 + "\n" + _trace()),
        stage_dir=tmp_path,
        pid_to_rank=_mapping(),
    )
    assert truncated["status"] == "offender_unconfirmed"
    assert truncated["trace_truncated"]
    assert overlong["status"] == "offender_unconfirmed"
    assert overlong["trace_overlong_line"]
    too_large = parse_trace(b"x" * (MAX_RAW_BYTES + 1), stage_dir=tmp_path, pid_to_rank=_mapping())
    assert too_large["status"] == "offender_unconfirmed"
    assert too_large["trace_truncated"]


def test_outside_path_is_not_exposed_or_confirmed(tmp_path):
    summary = parse_trace(_trace("/etc/shadow"), stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "offender_unconfirmed"
    assert summary["events"][0]["path"] is None
    assert summary["events"][0]["path_state"] == "path_outside_stage"
    assert "/etc/shadow" not in encode_summary(summary).decode()


def test_rank_pid_mapping_reads_only_exact_private_empty_records(tmp_path):
    token = secrets.token_hex(16)
    rank_dir = tmp_path / RANK_DIRECTORY
    rank_dir.mkdir(mode=0o700)
    os.chmod(rank_dir, 0o700)
    for rank, pid in enumerate((101, 102, 103, 104)):
        name = f"r-{token}-{rank}-p{pid}-2147483648-2147483648-{secrets.token_hex(16)}.rec"
        (rank_dir / name).touch(mode=0o600)
    mapping, state = rank_pid_mapping(tmp_path, token)
    assert state == "complete"
    assert mapping == {101: 0, 102: 1, 103: 2, 104: 3}
    assert all(item.stat().st_size == 0 for item in rank_dir.iterdir())


def test_rank_pid_mapping_rejects_duplicate_pid_or_rank(tmp_path):
    token = secrets.token_hex(16)
    rank_dir = tmp_path / RANK_DIRECTORY
    rank_dir.mkdir(mode=0o700)
    os.chmod(rank_dir, 0o700)
    for rank, pid in ((0, 101), (1, 101), (2, 103), (3, 104)):
        name = f"r-{token}-{rank}-p{pid}-1-1-{secrets.token_hex(16)}.rec"
        (rank_dir / name).touch(mode=0o600)
    mapping, state = rank_pid_mapping(tmp_path, token)
    assert mapping == {}
    assert state == "ambiguous_rank_mapping"


def test_summary_never_serializes_raw_trace_or_exceeds_limit(tmp_path):
    summary = parse_trace(_trace(f"{tmp_path}/OUTCAR"), stage_dir=tmp_path, pid_to_rank=_mapping())
    serialized = encode_summary(summary)
    assert len(serialized) <= MAX_SUMMARY_BYTES
    assert b"File size limit exceeded" not in serialized
    assert b'"path":"OUTCAR"' in serialized


def test_event_limit_cannot_hide_a_second_failed_file(tmp_path):
    trace = _trace(f"{tmp_path}/OUTCAR") * 64 + _trace(f"{tmp_path}/OTHER")
    summary = parse_trace(trace, stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "offender_unconfirmed"
    assert summary["trace_events_omitted"]
    assert summary["trace_parse_ambiguous"]


def test_huge_numeric_pid_is_ambiguous_not_a_parser_exception(tmp_path):
    trace = "9" * 5000 + " 1793590000.0 write(6</tmp/OUTCAR>, \"\"..., 1) = -1 EFBIG\n"
    summary = parse_trace(trace + _trace(f"{tmp_path}/OUTCAR"), stage_dir=tmp_path,
                          pid_to_rank=_mapping())
    assert summary["status"] == "offender_unconfirmed"
    assert summary["trace_parse_ambiguous"]


def test_path_input_rejects_symlink_and_fifo_without_reading(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(_trace(f"{tmp_path}/OUTCAR").encode())
    target.chmod(0o600)
    link = tmp_path / "trace-link"
    link.symlink_to(target)
    fifo = tmp_path / "trace-fifo"
    os.mkfifo(fifo, 0o600)
    for path in (link, fifo):
        result = parse_trace(path, stage_dir=tmp_path, pid_to_rank=_mapping())
        assert result["status"] == "offender_unconfirmed"
        assert result["trace_truncated"]


def test_real_strace_deleted_path_syntax_and_size_state(tmp_path):
    summary = parse_trace(_trace(f"{tmp_path}/OUTCAR>(deleted)").replace(">(deleted)>", ">(deleted)"),
                          stage_dir=tmp_path, pid_to_rank=_mapping())
    assert summary["status"] == "failed_fd_path_confirmed"
    assert summary["events"][0]["path"] == "OUTCAR (deleted)"
    assert summary["events"][0]["size_state"] == "size_unavailable"


def test_supervisor_traces_local_fake_writer_without_vasp(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    code = (
        "import os,resource,signal,time; "
        "resource.setrlimit(resource.RLIMIT_FSIZE,(1024,1024)); "
        "signal.signal(signal.SIGXFSZ,signal.SIG_IGN); "
        "fd=os.open('target.bin',os.O_CREAT|os.O_WRONLY,0o600); "
        "os.write(fd,b'x'*1024); time.sleep(.4); "
        "\ntry: os.write(fd,b'x')\nexcept OSError: pass\n"
    )
    summary = supervise([sys.executable, "-c", code], stage_dir=stage,
                        token=secrets.token_hex(16))
    # Some interpreter/ptrace combinations allow the inode witness and some
    # deny it. Both must preserve the truthful provenance flag.
    assert summary["trace_identity_unwitnessed"] is (not summary["trace_fd_witnessed"])
    assert not summary["trace_truncated"]
    assert not summary["trace_path_changed"]
    assert not summary["trace_overflow"]
    assert summary["raw_trace_cleanup_required"]
    assert summary["status"] == "offender_unconfirmed"  # no four-rank VASP mapping
    assert summary["events"][0]["path"] == "target.bin"
    assert (stage / "ION_CSP_VASP_FAILED_WRITE_TRACE.json").is_file()
    assert (stage / ".ion_csp_failed_write.strace").is_file()


def test_fixed_cli_denies_missing_hash_and_never_calls_mpirun(monkeypatch):
    monkeypatch.setenv("ION_CSP_CAPTURE_VASP_DIAGNOSTICS", "1")
    monkeypatch.setenv("ION_CSP_CAPTURE_FAILED_WRITE_TRACE", "1")
    monkeypatch.setenv("ION_CSP_VASP_MAX_ATTEMPTS", "1")
    monkeypatch.delenv("ION_CSP_STRACE_SHA256", raising=False)
    monkeypatch.setattr("ion_CSP.failed_write_trace.supervise",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("launched")))
    assert main(["run", "--token", secrets.token_hex(16), "--ranks", "4"]) == 125


def test_fixed_cli_passes_only_reviewed_mpi_command(monkeypatch, tmp_path):
    fake_strace = tmp_path / "strace"
    fake_strace.write_bytes(b"offline fixed hash fixture")
    monkeypatch.setattr("ion_CSP.failed_write_trace.STRACE", str(fake_strace))
    monkeypatch.setenv("ION_CSP_STRACE_SHA256", hashlib.sha256(fake_strace.read_bytes()).hexdigest())
    monkeypatch.setenv("ION_CSP_CAPTURE_VASP_DIAGNOSTICS", "1")
    monkeypatch.setenv("ION_CSP_CAPTURE_FAILED_WRITE_TRACE", "1")
    monkeypatch.setenv("ION_CSP_VASP_MAX_ATTEMPTS", "1")
    calls = []
    def fake_supervise(argv, *, stage_dir, token):
        calls.append((list(argv), stage_dir, token))
        return {"strace_returncode": 255}
    monkeypatch.setattr("ion_CSP.failed_write_trace.supervise", fake_supervise)
    token = secrets.token_hex(16)
    assert main(["run", "--token", token, "--ranks", "4"]) == 255
    assert calls == [(["mpirun", "-n", "4", "python", "-m",
                       "ion_CSP.vasp_launch_diagnostics", "exec"], ".", token)]


def test_supervisor_overflow_stops_fake_group(tmp_path, monkeypatch):
    stage = tmp_path / "overflow-stage"
    stage.mkdir(mode=0o700)
    monkeypatch.setattr("ion_CSP.failed_write_trace.MAX_RAW_BYTES", 1)
    code = (
        "import os,resource,signal,time; "
        "resource.setrlimit(resource.RLIMIT_FSIZE,(1024,1024)); "
        "signal.signal(signal.SIGXFSZ,signal.SIG_IGN); "
        "fd=os.open('target.bin',os.O_CREAT|os.O_WRONLY,0o600); "
        "os.write(fd,b'x'*1024); "
        "\ntry: os.write(fd,b'x')\nexcept OSError: pass\n"
        "time.sleep(30)\n"
    )
    summary = supervise([sys.executable, "-c", code], stage_dir=stage,
                        token=secrets.token_hex(16), watch_interval=0.01)
    assert summary["trace_overflow"]
    assert summary["status"] == "offender_unconfirmed"
    assert summary["trace_truncated"]


def test_supervisor_poll_exception_terminates_fake_group(tmp_path, monkeypatch):
    stage = tmp_path / "cancel-stage"
    stage.mkdir(mode=0o700)
    started = []
    real_popen = subprocess.Popen
    def record_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc
    from ion_CSP import failed_write_trace as trace_module
    real_identity = trace_module._raw_identity_ok
    def fail_after_child_ready(*args):
        if (stage / "ready").exists():
            raise RuntimeError("synthetic watchdog exception after child ready")
        return real_identity(*args)
    monkeypatch.setattr("ion_CSP.failed_write_trace.subprocess.Popen", record_popen)
    monkeypatch.setattr("ion_CSP.failed_write_trace._raw_identity_ok", fail_after_child_ready)
    child = ("import os,time; "
             "open('ready','w').write(str(os.getpid())); time.sleep(30)")
    with pytest.raises(RuntimeError, match="after child ready"):
        supervise([sys.executable, "-c", child], stage_dir=stage,
                  token=secrets.token_hex(16), watch_interval=0.01)
    assert len(started) == 1 and started[0].poll() is not None
    assert (stage / "ready").exists()
    # A just-killed descendant may remain briefly as a zombie under PID 1;
    # require no *live* member of the private process group.
    for _ in range(20):
        result = subprocess.run(["ps", "-eo", "pgid=,stat="], capture_output=True,
                                text=True, check=True)
        live = [line for line in result.stdout.splitlines()
                if line.split() and line.split()[0] == str(started[0].pid)
                and not line.split()[1].startswith("Z")]
        if not live:
            break
        time.sleep(0.05)
    assert not live
