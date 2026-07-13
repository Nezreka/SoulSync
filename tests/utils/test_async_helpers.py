"""Regression tests for the shared synchronous-to-async bridge."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_first_run_async_call_waits_for_event_loop_startup():
    repo_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio; "
                "from utils.async_helpers import run_async; "
                "assert run_async(asyncio.sleep(0, result='ready')) == 'ready'"
            ),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
