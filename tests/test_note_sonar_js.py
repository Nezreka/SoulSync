"""Run the Note Sonar controller tests under the regular pytest sweep.

The behavioural contract is `tests/static/test_note_sonar.mjs`, which loads the
real `webui/static/note-sonar.js` into a VM with a small DOM/media shim. This
wrapper surfaces it in CI, and carries a node-independent tripwire for the
wiring that matters even on a Python-only runner: the sheet is loaded, the
original raster is a normal tracked asset (not an inlined data URI), and the
controller's tests actually exist.

Run directly:
    node --test tests/static/test_note_sonar.mjs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._node_runner import assert_node_suite, node_available

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "webui" / "static" / "note-sonar.js"
_CSS = _ROOT / "webui" / "static" / "note-sonar.css"
_ART = _ROOT / "webui" / "static" / "note-sonar.png"
_INDEX = _ROOT / "webui" / "index.html"


def test_note_sonar_controller_js():
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the Note Sonar controller tests")
    assert_node_suite(_ROOT / "tests" / "static" / "test_note_sonar.mjs",
                      "Note Sonar controller", cwd=_ROOT)


def test_sheet_and_assets_are_wired():
    index = _INDEX.read_text(encoding="utf-8", errors="replace")
    js = _JS.read_text(encoding="utf-8", errors="replace")
    css = _CSS.read_text(encoding="utf-8", errors="replace")

    assert "filename='note-sonar.css'" in index
    assert "filename='note-sonar.js'" in index
    assert "data-listen-disclosure" in js
    assert "sent to Shazam" in js
    # The approved silhouette is a normal tracked raster, not a base64 payload
    # pasted into the script (which is how the NAS patch shipped it).
    assert "data:image/png;base64" not in js
    assert _ART.is_file() and _ART.stat().st_size > 1000
    assert "url('note-sonar.png')" in css


def test_no_private_installer_or_host_paths():
    """The feature must not carry the NAS installer or a personal address."""
    for path in (_JS, _CSS):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "install-shazam" not in text
        assert "/mnt/" not in text
        assert "tailscale" not in text.lower()
