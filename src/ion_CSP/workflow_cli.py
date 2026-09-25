"""Release-facing EE/CSP wrapper with an explicit configuration contract."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ion_CSP.preflight import check_core, write_report


WORKFLOW_MODULES = {
    "ee": "ion_CSP.run.main_EE",
    "csp": "ion_CSP.run.main_CSP",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ion-csp-workflow",
        description=(
            "Run an ion_CSP EE or CSP work directory after validating the fixed "
            "<work_dir>/config.yaml contract and controller environment."
        ),
    )
    subparsers = parser.add_subparsers(dest="workflow", required=True)
    for workflow, label in (("ee", "empirical-estimation"), ("csp", "crystal-prediction")):
        command = subparsers.add_parser(
            workflow,
            help=f"validate and run the {label} workflow",
        )
        command.add_argument(
            "work_dir",
            type=Path,
            help="work directory containing the authoritative config.yaml",
        )
        command.add_argument(
            "--project-root",
            type=Path,
            help="also verify release files in this source checkout",
        )
        command.add_argument(
            "--check-only",
            action="store_true",
            help="emit the preflight receipt without starting scientific work",
        )
        command.add_argument(
            "--json-output",
            default="-",
            help="write the JSON preflight receipt to this path as well as stdout",
        )
        command.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    work_dir = args.work_dir.expanduser().resolve(strict=False)
    report = check_core(
        workflow=args.workflow,
        project_root=args.project_root,
        work_dir=work_dir,
    )
    write_report(report, args.json_output, pretty=not args.compact)
    if not report.ok:
        return 1
    if args.check_only:
        return 0
    completed = subprocess.run(
        [sys.executable, "-m", WORKFLOW_MODULES[args.workflow], str(work_dir)],
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
