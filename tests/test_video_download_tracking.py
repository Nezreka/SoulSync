"""Live download tracking wiring: after a grab the user must (a) see WHICH release
was selected, (b) get a live progress bar on it, and (c) get a button to the
Downloads page — and a movie's detail page shows live progress for an in-flight
download that jumps back to Downloads.

Backed by GET /api/video/downloads/status (tested in test_video_api.py
::test_downloads_status_lookup_by_id_and_media). These pin the frontend wiring so
a refactor can't quietly unhook the tracker, the detail chip, or the navigation.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_VIEW = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")
_DETAIL = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_SIDE = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
_GETMODAL = (_ROOT / "webui" / "static" / "video" / "video-get-modal.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


# --- result card: redesign + selected/tracking state ----------------------

def test_result_card_redesigned_as_column_with_get_button():
    assert 'vdl-res-main' in _VIEW          # row wrapper so a tracker can dock below
    assert 'data-vdl-card="' in _VIEW       # addressable card (auto-pick targets it)
    assert 'vdl-res-grab-ic' in _VIEW       # the grab button is now a labelled "Get" pill
    assert '.vdl-res-main' in _CSS


def test_grab_begins_live_tracking_on_the_card():
    assert 'function beginTracking(' in _VIEW
    assert 'vdl-res--grabbed' in _VIEW
    assert 'data-vdl-track-fill' in _VIEW          # the progress bar fill
    assert "/api/video/downloads/status?id=" in _VIEW
    # both the manual grab and the auto-pick start tracking the chosen card
    assert _VIEW.count('beginTracking(') >= 3      # 1 def + doGrab + _autoPick


def test_track_button_goes_to_downloads_and_closes_modal():
    assert 'Track on Downloads' in _VIEW
    assert 'function gotoDownloads(' in _VIEW
    assert 'VideoGet.close' in _VIEW
    assert "'soulsync:video-navigate'" in _VIEW
    assert '.vdl-res-track' in _CSS


def test_modal_exposes_close():
    assert 'close: closeModal' in _GETMODAL


# --- movie detail page: live download chip --------------------------------

def test_detail_page_watches_movie_download():
    assert 'function watchMovieDownload(' in _DETAIL
    assert '/api/video/downloads/status?media_id=' in _DETAIL
    assert 'data-vd-dlchip' in _DETAIL
    # the chip jumps to the Downloads page
    assert "'soulsync:video-navigate'" in _DETAIL
    assert '.vd-dlchip' in _CSS


def test_detail_watch_started_for_library_movies():
    assert 'watchMovieDownload(id)' in _DETAIL
    assert 'stopMovieDownloadWatch()' in _DETAIL   # cleared on (re)load / navigate away


# --- navigation plumbing --------------------------------------------------

def test_video_side_handles_navigate_event():
    assert "'soulsync:video-navigate'" in _SIDE
    assert 'navigate(pageId)' in _SIDE
