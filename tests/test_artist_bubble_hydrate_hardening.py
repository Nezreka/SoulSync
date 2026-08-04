"""#1038 — one malformed artist-bubble snapshot entry broke Library page init.

Mechanism: a bubble snapshot row (persisted server-side, per profile) held an
entry whose ``artist`` (or a download's ``album``) was null. ``/api/artist_bubbles/
hydrate`` passed it straight through, the client rebuilt the poisoned map, and
the Library page's ``showLibraryDownloadsSection()`` threw a TypeError inside
``initializeLibraryPage``'s try — surfacing as the opaque "Failed to initialize
Library page" toast on EVERY visit until an app restart happened to wipe the
snapshot (hydrate self-cleans when no batches are active).

Hardening is layered — server drops malformed entries at hydrate time, the
client skips them at restore AND render time, and the toast now names the real
error so the next report carries the cause.
"""

from __future__ import annotations

import re

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HELPERS_JS = (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Backend: /api/artist_bubbles/hydrate drops malformed entries
# ---------------------------------------------------------------------------

@pytest.fixture
def hydrate_client(monkeypatch):
    with patch("web_server.SpotifyClient"), patch("core.tidal_client.TidalClient"):
        from web_server import app as flask_app
        import web_server

        flask_app.config["TESTING"] = True

        # An active batch, so hydrate does NOT take the "app restarted →
        # wipe snapshot" early-out and actually walks the entries.
        monkeypatch.setattr(web_server, "download_batches", {
            "b1": {"phase": "downloading", "playlist_id": "vp-good"},
        })

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "data": {
                "42": {  # healthy entry — must survive
                    "artist": {"id": "42", "name": "Korn", "image_url": ""},
                    "downloads": [
                        {"virtualPlaylistId": "vp-good",
                         "album": {"name": "Untouchables"},
                         "albumType": "album", "status": "in_progress",
                         "startTime": datetime.now().isoformat()},
                        # album is null → this download must be dropped
                        {"virtualPlaylistId": "vp-bad", "album": None,
                         "albumType": "album", "status": "in_progress"},
                        # no virtualPlaylistId → dropped
                        {"album": {"name": "Issues"}, "status": "in_progress"},
                    ],
                    "hasCompletedDownloads": False,
                },
                "13": {  # artist is null → whole entry dropped
                    "artist": None,
                    "downloads": [{"virtualPlaylistId": "vp-x",
                                   "album": {"name": "X"}, "status": "in_progress"}],
                },
                "14": {  # artist dict without id → dropped
                    "artist": {"name": "Ghost"},
                    "downloads": [],
                },
                "15": "not-even-a-dict",  # dropped
            },
        }
        fake_db = MagicMock()
        fake_db.get_bubble_snapshot.return_value = snapshot
        monkeypatch.setattr(web_server, "get_database", lambda: fake_db)

        yield flask_app.test_client()


def test_hydrate_drops_malformed_entries_keeps_healthy(hydrate_client):
    r = hydrate_client.get("/api/artist_bubbles/hydrate")
    out = r.get_json()
    assert out["success"] is True, f"hydrate refused: {out}"
    bubbles = out["bubbles"]
    assert set(bubbles.keys()) == {"42"}, \
        f"malformed entries leaked through hydrate: {sorted(bubbles)}"
    dls = bubbles["42"]["downloads"]
    assert [d["virtualPlaylistId"] for d in dls] == ["vp-good"], \
        f"malformed downloads leaked: {dls}"
    assert dls[0]["album"]["name"] == "Untouchables"
    # the live batch made it 'in_progress', not the completed fallback
    assert dls[0]["status"] == "in_progress"


def test_hydrate_still_selfcleans_on_restart(hydrate_client, monkeypatch):
    """No active batches → hydrate wipes the snapshot (the behavior that made
    a restart 'fix' #1038). The hardening must not break this."""
    import web_server
    monkeypatch.setattr(web_server, "download_batches", {})
    r = hydrate_client.get("/api/artist_bubbles/hydrate")
    out = r.get_json()
    assert out["success"] is True and out["bubbles"] == {}


# ---------------------------------------------------------------------------
# Client contracts: the render/restore guards + the self-describing toast
# ---------------------------------------------------------------------------

def test_bubble_card_skips_malformed_instead_of_throwing():
    fn = _HELPERS_JS.split("function createArtistBubbleCard")[1].split("function monitorArtistDownload")[0]
    assert "artist.id == null" in fn and "return ''" in fn
    assert "d.album && d.album.name" in fn          # no bare d.album.name deref


def test_hydrate_restore_skips_malformed_entries():
    fn = _HELPERS_JS.split("async function hydrateArtistBubblesFromSnapshot")[1].split("// --- Search Bubble Snapshot System ---")[0]
    assert "Skipping malformed bubble snapshot entry" in fn
    assert "Array.isArray(bubbleData.downloads)" in fn


def test_downloads_sections_filter_malformed_bubbles():
    # both showArtistDownloadsSection and showLibraryDownloadsSection
    assert _HELPERS_JS.count("Array.isArray(artistDownloadBubbles[artistId].downloads)") >= 2


# initializeLibraryPage's opaque-toast fix retired with the function itself when
# React took over /library. The equivalent guarantee — a failed artist load
# surfaces a toast rather than a blank page — is pinned by the Library v2 page's
# own error state (webui/src/routes/library/-ui/library-v2-page.tsx), which
# renders the failure instead of blanking the route.


def test_library_downloads_section_anchors_on_the_grid_not_a_class_query():
    """#1038 round two (named by the improved toast): insertBefore threw because
    document.querySelector('.library-content') matched the VIDEO library's copy
    of the class (first in the DOM) while #library-artists-grid lives in the
    music one. The section must derive its container FROM the grid."""
    fn = _HELPERS_JS.split("function showLibraryDownloadsSection")[1].split("function createArtistBubbleCard")[0]
    assert "getElementById('library-artists-grid')" in fn
    assert "artistGrid.parentElement" in fn
    # The ambiguity is still real: index.html carries the VIDEO library's
    # .library-content, and a class query could still hit it. The music side is
    # Library v2 now — it is CSS-module scoped and owns no global class at all,
    # so the only safe anchor left is the data attribute asserted below.
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    react_page = (
        _ROOT / "webui" / "src" / "routes" / "library" / "-ui" / "library-v2-page.tsx"
    ).read_text(encoding="utf-8")
    assert index.count('class="library-content"') >= 1, "video library lost its .library-content"
    assert "data-library-downloads-host" in react_page, "music library lost the bubbles host"

    # The React library page renders a dedicated host and is preferred when
    # present, so a querySelector is no longer disqualifying on its own — but it
    # must target a selector that CANNOT match the video side. Anything matching
    # by class (.library-content and friends) reopens #1038.
    queried = re.findall(r"document\.querySelector\((['\"])(.+?)\1\)", fn)
    assert [sel for _, sel in queried] == ["[data-library-downloads-host]"], (
        f"only the unique React host may be queried here, got {queried}"
    )


def test_react_library_downloads_host_is_unique_to_the_music_page():
    """The anchor above is only safe while exactly one page renders the host —
    a second one (notably a video-side copy) would recreate #1038 with a
    different selector."""
    hosts = [
        path
        for path in _ROOT.joinpath("webui").rglob("*")
        if path.suffix in {".tsx", ".jsx", ".js", ".html"}
        and "node_modules" not in path.parts
        and "dist" not in path.parts
        and not path.name.endswith(".test.tsx")
        and "data-library-downloads-host" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    rendered = [p for p in hosts if p.name != "shared-helpers.js"]
    assert [p.name for p in rendered] == ["library-v2-page.tsx"], (
        f"the host must be rendered by the music library page alone, got {rendered}"
    )
