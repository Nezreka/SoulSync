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
# the loader and the tiles are React now (SETTINGS_PORT.md step 7a)
_TABS = _ROOT / "webui" / "src" / "features" / "settings" / "tabs"
_MODEL = (_TABS / "sources-model.ts").read_text(encoding="utf-8")
_TILES = (_TABS / "downloads" / "sources-view.tsx").read_text(encoding="utf-8")


def test_loader_never_unchecks_saved_sources():
    fn = _MODEL.split("export function musicFromSettings(", 1)[1].split("\n}", 1)[0]
    # unready sources are marked, never filtered out of the saved chain
    assert "if (music.includes(src.id) && status[src.id] === false) unready[src.id] = true;" in fn
    assert not re.search(r"music\s*=\s*music\.filter", fn)
    assert "status[" not in fn.split("const unready")[0]


def test_unready_row_explains_itself():
    # the old hidden legacy list named Prowlarr in a tooltip nobody could see
    # (the chain widget had replaced it). the place it shows is the tile
    # (settings-bic) the place it shows now is the order itself: the row says
    # it is skipped, its status says "Needs setup", it is drawn as a warning,
    # and its one coloured button is "Set up"
    words = (_TABS / "downloads" / "words.ts").read_text(encoding="utf-8")
    assert "if (unready[id]) return 'Skipped until it is set up';" in words
    assert "if (inOrder && state.unready[id]) return { tone: 'warn', text: 'Needs setup' };" in words
    assert "set up yet" in words  # and the sentence at the top names it
    assert "warn: !!unready[id]," in _TILES
    assert "Set up" in _TILES.split("const settingsButton", 1)[1].split("};", 1)[0]


def test_backend_skips_unready_sources_at_download_time():
    src = (_ROOT / "core" / "download_orchestrator.py").read_text(encoding="utf-8")
    assert "def configured_clients" in src
    assert "client.is_configured()" in src
