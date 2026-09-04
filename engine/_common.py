"""Shared plumbing for the engine scripts: repo root, hard-fail, .env loading.

Deliberately tiny. Both engine scripts must stay runnable as
`python3 engine/<script>.py` from a fresh clone with nothing installed but
pandas/numpy, so this module has no dependencies beyond the stdlib.
"""
from __future__ import annotations

import os
import sys
from typing import NoReturn
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")


def fatal(msg: str) -> NoReturn:
    """Print FATAL to stderr and exit 1. A failed run trades nothing."""
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_env(path: str | None = None) -> None:
    """Load repo-root .env into os.environ (no override of existing vars).

    Missing file is allowed — cloud routines inject keys as process env vars;
    the ALPACA_API_KEY/SECRET_KEY check in each script still hard-fails if keys
    arrive from neither source.
    """
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
