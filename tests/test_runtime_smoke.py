"""Offline runtime smoke tests for the public entrypoint."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_help_is_offline_and_successful() -> None:
    """The packaged CLI must start without credentials or external services."""
    result = subprocess.run(
        [sys.executable, "run.py", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    assert "--once" in result.stdout
    assert "--health" in result.stdout
    assert "--skip-preflight" in result.stdout
