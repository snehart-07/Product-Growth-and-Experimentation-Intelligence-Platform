"""Shared test setup. Offline tests need no Google Cloud access."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VENV = Path(__file__).resolve().parents[1] / ".venv"
try:
    import duckdb  # noqa: F401
except ModuleNotFoundError as exc:  # usually: bare `pytest` resolved to another Python (e.g. Anaconda)
    raise pytest.UsageError(
        f"Project dependencies are not importable from {sys.executable}.\n"
        f"Run the tests with the project venv: {_VENV}/bin/python -m pytest (or: make test). "
        "Install with: make install") from exc
