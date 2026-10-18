"""Page-load 4xx guards (#1763): the shell must not fire requests the
current profile will only be refused for.

These are source pins in the style of test_video_side_shell.py — the
classic scripts have no unit harness for their boot paths, which is
exactly how the unguarded inits shipped. Each pin names the server gate
it mirrors so a future edit that drops a guard fails loudly here.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "webui" / "static"


def _read(*parts):
    return (_STATIC.joinpath(*parts)).read_text(encoding="utf-8")


INIT = _read("init.js")
DOWNLOADS = _read("downloads.js")
WISHLIST_TOOLS = _read("wishlist-tools.js")
VIDEO_SCAN = _read("video", "video-scan.js")
VIDEO_SERVER_SCAN = _read("video", "video-server-scan.js")
VIDEO_WATCHLIST = _read("video", "video-watchlist.js")
VIDEO_WISHLIST = _read("video", "video-wishlist.js")


def test_init_defines_the_profile_guards_and_resolution_signal():
    # The two gate mirrors (video blueprint side gate + admin prefixes)
    # and the deferral helper boot callers use instead of racing the
    # profile fetch.
    assert "function canAccessVideoSide()" in INIT
    assert "function isAdminProfile()" in INIT
    assert "function whenProfileKnown(fn)" in INIT
    # initProfileSystem marks resolution done wherever the app may proceed
    # (session profile, auto-selects, and the error fall-through).
    assert INIT.count("_markProfileResolutionDone();") >= 4


def test_appearance_bootstrap_runs_once_from_initapp_not_at_parse_time():
    # /api/settings is admin-only; the bootstrap may only run from
    # initApp (the funnel every successful boot passes through), gated.
    assert INIT.count("bootstrapServerAppearanceSettings();") == 1
    init_app = INIT.split("function initApp()")[1].split("function initializeNavigation")[0]
    assert "bootstrapServerAppearanceSettings();" in init_app


def test_bell_seeds_are_gated_and_deferred_until_profile_known():
    # overlays/collections status endpoints are admin-only for any method;
    # bulk status is side-gated.
    overlay = DOWNLOADS.split("function _seedOverlayTask()")[1].split("}")[0]
    assert "isAdminProfile" in overlay
    col_sync = DOWNLOADS.split("function _seedCollectionSyncTask()")[1].split("}")[0]
    assert "isAdminProfile" in col_sync
    bulk = DOWNLOADS.split("function _seedVideoBulkTask()")[1].split("}")[0]
    assert "canAccessVideoSide" in bulk
    health = DOWNLOADS.split("function _seedNotifHealth()")[1].split("}")[0]
    assert "canAccessVideoSide" in health
    # The load-time seed block waits for the profile context.
    assert "whenProfileKnown" in DOWNLOADS.split("_overlayTaskInit")[1]


def test_wishlist_pollers_treat_not_found_body_as_the_old_404():
    # Both sync pollers in wishlist-tools.js only stopped on !resp.ok;
    # the 200 + status 'not_found' answer must clean them up, not leak.
    assert WISHLIST_TOOLS.count("state.status === 'not_found'") >= 2
    assert "_cleanupSyncHistoryResync(entryId, 'error')" in WISHLIST_TOOLS
    assert "_cleanupModalSync(playlistId, 'error')" in WISHLIST_TOOLS


def test_video_boot_probes_are_side_gated_and_profile_deferred():
    for src in (VIDEO_SCAN, VIDEO_SERVER_SCAN, VIDEO_WATCHLIST, VIDEO_WISHLIST):
        assert "canAccessVideoSide" in src
        assert "whenProfileKnown" in src
    # The wishlist badge poll must not even start for a refused profile.
    poll = VIDEO_WISHLIST.split("function scheduleBadgePoll()")[1].split("}")[0]
    assert "canAccessVideoSide" in poll


def test_no_static_file_still_points_at_the_dead_weekly_route():
    # /api/discover/discovery-weekly never existed; the route is
    # /api/discover/weekly. (The React copy is pinned by its MSW tests.)
    for path in _STATIC.rglob("*.js"):
        assert "/api/discover/discovery-weekly" not in path.read_text(
            encoding="utf-8", errors="replace"
        ), f"dead weekly route still referenced in {path.name}"
