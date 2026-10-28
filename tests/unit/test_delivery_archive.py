import os
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, relative: str, content: str = "test\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sdist_contains_delivery_inputs_and_excludes_private_or_historical_files(
    tmp_path,
):
    root = tmp_path / "ion-csp-fixture"
    root.mkdir()
    (root / "MANIFEST.in").write_text(
        (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _write(
        root,
        "pyproject.toml",
        "[build-system]\nrequires=['setuptools']\n"
        "build-backend='setuptools.build_meta'\n"
        "[project]\nname='ion-csp-archive-fixture'\nversion='0.0.1'\n",
    )
    _write(
        root,
        "setup.py",
        "from setuptools import setup\nsetup()\n",
    )
    _write(root, "setup.cfg", "[metadata]\nlicense_files = LICENSE\n")
    for relative in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "uv.lock",
        "environment.yml",
        "environment-mlp.yml",
        "AUTO_LINKING_GUIDE.md",
        "DOCKER.md",
        "DPA4_BACKEND.md",
        "MATTERSIM_BACKEND.md",
        "Dockerfile",
        "Dockerfile.gpu",
        "docker-compose.yml",
        ".dockerignore",
        ".coveragerc",
        ".gitignore",
        "pyrightconfig.json",
        "makefile",
        "run_tests.sh",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "config/complete_config.yaml",
        "docs/delivery.md",
        "docs/usage.md.orig",
        "docs/history/old.md",
        "examples/example_usage.py",
        "examples/server/example_local_machine.yaml",
        "examples/server/example_remote_machine.yaml",
        "tests/unit/test_contract.py",
        "scripts/release_preflight.py",
        "delivery/locks/core.lock.json",
        "delivery/release-manifest.json",
        "delivery/candidate-manifests/internal.json",
        "src/ion_CSP/__init__.py",
        "src/ion_CSP/sample.py",
    ):
        _write(root, relative)
    for relative in (
        ".claude/skills/private.md",
        ".github/workflows/release.yml",
        ".vscode/settings.json",
    ):
        _write(root, relative)

    dist = tmp_path / "dist"
    dist.mkdir()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = ""
    subprocess.run(
        [sys.executable, "setup.py", "sdist", "--dist-dir", str(dist)],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    archives = list(dist.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], "r:gz") as archive:
        members = {PurePosixPath(name) for name in archive.getnames()}

    relative_members = {
        PurePosixPath(*path.parts[1:]).as_posix()
        for path in members
        if len(path.parts) > 1
    }
    assert "config/complete_config.yaml" in relative_members
    assert "docs/delivery.md" in relative_members
    assert "examples/example_usage.py" in relative_members
    assert "examples/server/example_local_machine.yaml" in relative_members
    assert "tests/unit/test_contract.py" in relative_members
    assert "delivery/locks/core.lock.json" in relative_members
    assert "setup.cfg" in relative_members
    for excluded in (
        ".claude",
        ".github",
        ".vscode",
        "docs/history",
        "docs/usage.md.orig",
        "examples/server/example_remote_machine.yaml",
        "delivery/release-manifest.json",
        "delivery/candidate-manifests",
        ".coveragerc",
        ".gitignore",
        "pyrightconfig.json",
        "makefile",
        "run_tests.sh",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ):
        assert not any(
            path == excluded or path.startswith(f"{excluded}/")
            for path in relative_members
        ), excluded
