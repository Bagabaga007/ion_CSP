#!/usr/bin/env python3
"""Repository wrapper for :mod:`ion_CSP.preflight`."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ion_CSP.preflight import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
