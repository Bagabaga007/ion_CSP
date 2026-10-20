#!/usr/bin/env python3
"""Source-tree entry point for the local ion_CSP readiness checker."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ion_CSP.local_readiness import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
