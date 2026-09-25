from types import SimpleNamespace

from ion_CSP import workflow_cli
from ion_CSP.preflight import Report


def test_check_only_does_not_start_workflow(monkeypatch, tmp_path):
    report = Report(target="core", workflow="ee")
    report.add("ready", True, "ready")
    monkeypatch.setattr(workflow_cli, "check_core", lambda **_kwargs: report)
    monkeypatch.setattr(workflow_cli, "write_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_cli.subprocess, "run", lambda *_args, **_kwargs: None)

    assert workflow_cli.main(["ee", str(tmp_path), "--check-only"]) == 0


def test_wrapper_uses_explicit_ee_module(monkeypatch, tmp_path):
    report = Report(target="core", workflow="ee")
    report.add("ready", True, "ready")
    called = {}
    monkeypatch.setattr(workflow_cli, "check_core", lambda **_kwargs: report)
    monkeypatch.setattr(workflow_cli, "write_report", lambda *_args, **_kwargs: None)

    def fake_run(command, check):
        called["command"] = command
        called["check"] = check
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(workflow_cli.subprocess, "run", fake_run)

    assert workflow_cli.main(["ee", str(tmp_path)]) == 7
    assert called["command"][1:3] == ["-m", "ion_CSP.run.main_EE"]
    assert called["command"][-1] == str(tmp_path.resolve())
    assert called["check"] is False


def test_wrapper_refuses_to_start_after_failed_preflight(monkeypatch, tmp_path):
    report = Report(target="core", workflow="csp")
    report.add("missing", False, "missing")
    monkeypatch.setattr(workflow_cli, "check_core", lambda **_kwargs: report)
    monkeypatch.setattr(workflow_cli, "write_report", lambda *_args, **_kwargs: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("workflow must not start")

    monkeypatch.setattr(workflow_cli.subprocess, "run", forbidden)
    assert workflow_cli.main(["csp", str(tmp_path)]) == 1
