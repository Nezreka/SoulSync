"""Fl3m — 'Usenet settings not saving': the save persisted fine; the settings
LOADER silently unchecked any enabled source the server reported as not fully
configured (usenet needs Prowlarr on top of SABnzbd), and the next save then
persisted the loss. Source-contract pins: the auto-disable is gone, the saved
toggle survives, and the row explains itself with a needs-setup chip instead.
The backend's configured_clients() filter (which skips unready sources at
download time) is what makes keeping the toggle enabled safe.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def test_loader_never_unchecks_saved_sources():
    # the old auto-disable flipped _hybridSourceEnabled[...] = false on load
    assert "Auto-disable sources" not in _JS
    assert "_hybridSourceUnready = {};" in _JS
    # intent preserved: the unready branch must NOT touch the enabled map
    m = re.search(r"_hybridSourceUnready = \{\};((?:\n.*){1,10})", _JS)
    assert m and "_hybridSourceEnabled[src.id] = false" not in m.group(1)


def test_unready_row_explains_itself():
    assert "hybrid-source-unready" in _JS
    assert "needs setup" in _JS
    assert "Prowlarr" in _JS                      # the actual missing piece is named
    assert "hybrid-source-unready" in _CSS


def test_backend_skips_unready_sources_at_download_time():
    src = (_ROOT / "core" / "download_orchestrator.py").read_text(encoding="utf-8")
    assert "def configured_clients" in src
    assert "client.is_configured()" in src
