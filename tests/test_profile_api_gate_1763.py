"""#1763: a member's page load doesn't send api calls the server will refuse.

the behavior lives in tests/js/profile_api_gate_harness.mjs (real Response
objects under node); this runs it and pins the wiring.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import json
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_INIT = (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8", errors="replace")


def _node():
    return shutil.which("node") or shutil.which("node.exe")


def _gate_rules_json() -> str:
    """the server's open-path lists, passed to the JS harness so the gate
    and the server can't drift apart."""
    from core.security.session_profile import client_gate_rules
    return json.dumps(client_gate_rules())


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_behavioral_harness_passes():
    # relative path + cwd: under wsl node.exe can't open /mnt/... absolute paths
    res = subprocess.run(
        [_node(), "tests/js/profile_api_gate_harness.mjs", _gate_rules_json()],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"harness failed:\n{res.stdout}\n{res.stderr}"
    assert "all assertions passed" in res.stdout


def test_loaded_after_dedupe_and_before_every_app_script():
    def at(name):
        return _HTML.index(f"filename='{name}'")

    gate_at = at("profile-api-gate.js")
    assert at("fetch-dedupe.js") < gate_at
    for later in ("dist/shell.js", "core.js", "init.js", "video/video-side.js"):
        assert gate_at < at(later), later


def test_init_tells_the_gate_who_the_profile_is():
    assert "window.SoulSyncProfileGate?.setProfile(profile);" in _INIT
    assert "window.SoulSyncProfileGate?.setProfile(null);" in _INIT


def test_appearance_bootstrap_is_admin_only():
    # the member's guaranteed 403 on /api/settings at boot
    assert "\nbootstrapServerAppearanceSettings();" not in _INIT
    assert "bootstrapServerAppearanceForAdmin(profile);" in _INIT
