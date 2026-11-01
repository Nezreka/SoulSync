"""Opt-in Docker acceptance for the Roadmap-3 strict correlation gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.phase3_deployment,
    pytest.mark.skipif(
        os.environ.get("SOULSYNC_PHASE3_ACCEPTANCE") != "1",
        reason="set SOULSYNC_PHASE3_ACCEPTANCE=1 for Docker acceptance",
    ),
]


def test_manual_and_scheduled_consumers_are_ready_under_strict_gate() -> None:
    probe = Path(__file__).with_name("contract_enforcement_probe.py")
    completed = subprocess.run(
        [sys.executable, str(probe)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SOULSYNC_PHASE3_ACCEPTANCE": "1"},
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"probe stdout:\n{completed.stdout}\nprobe stderr:\n{completed.stderr}"
    )
    assert "deployment acceptance: PASS" in completed.stdout
