"""Tests for ``core/listenbrainz_manager.py``.

Coverage focus: the new ``refresh_playlist(mbid)`` targeted refresh.
Pre-fix the manager only exposed ``update_all_playlists`` — every
caller that wanted to refresh ONE playlist had to re-pull all 14
cached LB playlists' details. Wasted API calls + slow + the LB
adapter's silent ``except Exception: pass`` wrapper masked the real
slowness as a UI hang.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from core.listenbrainz_manager import ListenBrainzManager


@pytest.fixture
def tmp_db():
    """Per-test SQLite file so ``_ensure_tables`` can build its schema."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        Path(path).unlink()
    except OSError:
        pass


def _seed_playlist(db_path: str, mbid: str, title: str, ptype: str, track_count: int):
    """Insert one cached LB playlist row + matching schema bootstrap."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listenbrainz_playlists
            (playlist_mbid, title, creator, playlist_type, track_count, annotation_data, profile_id)
            VALUES (?, ?, 'ListenBrainz', ?, ?, '{}', 1)
            """,
            (mbid, title, ptype, track_count),
        )
        conn.commit()
    finally:
        conn.close()


def _build_manager(db_path: str, *, authed: bool = True) -> ListenBrainzManager:
    """Construct a manager + stub the client so we don't hit the network."""
    mgr = ListenBrainzManager(db_path=db_path, profile_id=1)
    mgr.client = MagicMock()
    mgr.client.is_authenticated.return_value = authed
    return mgr


# ---------------------------------------------------------------------------
# refresh_playlist: happy path
# ---------------------------------------------------------------------------


def test_refresh_playlist_fetches_single_playlist_only(tmp_db):
    """``refresh_playlist`` calls ONLY ``get_playlist_details`` for the
    targeted mbid — not any of the list-pulling methods that
    ``update_all_playlists`` uses."""
    mgr = _build_manager(tmp_db)
    _seed_playlist(tmp_db, "mbid-1", "Weekly Jams", "created_for", 50)

    # Non-empty ``track`` so ``_update_playlist`` doesn't trigger its
    # own defensive re-fetch (that branch is for legacy callers that
    # pass slim list-row data — not us).
    mgr.client.get_playlist_details.return_value = {
        "playlist": {
            "identifier": "https://listenbrainz.org/playlist/mbid-1",
            "title": "Weekly Jams",
            "creator": "ListenBrainz",
            "track": [
                {
                    "identifier": "https://musicbrainz.org/recording/rec-1",
                    "title": "Song",
                    "creator": "Artist",
                }
            ],
            "annotation": "",
        }
    }

    result = mgr.refresh_playlist("mbid-1")

    assert result["success"] is True
    assert result["playlist_mbid"] == "mbid-1"
    assert result["playlist_type"] == "created_for"
    mgr.client.get_playlist_details.assert_called_once_with("mbid-1")
    # The wasteful list-pulling methods must NOT be touched.
    mgr.client.get_playlists_created_for_user.assert_not_called()
    mgr.client.get_user_playlists.assert_not_called()
    mgr.client.get_collaborative_playlists.assert_not_called()


def test_refresh_playlist_returns_skipped_when_track_count_unchanged(tmp_db):
    """``_update_playlist``'s smart-comparison returns "skipped" when
    the track count matches the cached value. ``refresh_playlist``
    propagates that signal back to the caller."""
    mgr = _build_manager(tmp_db)
    _seed_playlist(tmp_db, "mbid-stable", "Stable", "user", 7)

    # Build a payload with the same 7 tracks.
    tracks = [
        {
            "identifier": f"https://musicbrainz.org/recording/rec-{i}",
            "title": f"Track {i}",
            "creator": "Artist",
        }
        for i in range(7)
    ]
    mgr.client.get_playlist_details.return_value = {
        "playlist": {
            "identifier": "https://listenbrainz.org/playlist/mbid-stable",
            "title": "Stable",
            "creator": "ListenBrainz",
            "track": tracks,
            "annotation": "",
        }
    }

    result = mgr.refresh_playlist("mbid-stable")

    assert result["success"] is True
    assert result["result"] == "skipped"


# ---------------------------------------------------------------------------
# refresh_playlist: defensive / failure modes
# ---------------------------------------------------------------------------


def test_refresh_playlist_unauthenticated_returns_failure_without_fetching(tmp_db):
    """No auth → no LB API calls. Pre-fix, ``update_all_playlists``
    had this check; the new targeted entry-point must enforce it
    consistently."""
    mgr = _build_manager(tmp_db, authed=False)

    result = mgr.refresh_playlist("mbid-anything")

    assert result["success"] is False
    assert "Not authenticated" in result["error"]
    mgr.client.get_playlist_details.assert_not_called()


def test_refresh_playlist_empty_mbid_returns_failure(tmp_db):
    """Defensive — empty mbid is a caller bug; fail loud with a
    clear error message rather than firing a malformed API call."""
    mgr = _build_manager(tmp_db)

    result = mgr.refresh_playlist("")

    assert result["success"] is False
    assert "No playlist_mbid" in result["error"]
    mgr.client.get_playlist_details.assert_not_called()


def test_refresh_playlist_returns_failure_when_upstream_returns_none(tmp_db):
    """LB API returning ``None`` (deleted playlist, transient 404)
    is a clean failure — not a silent skip. The caller decides
    whether to retry / surface."""
    mgr = _build_manager(tmp_db)
    _seed_playlist(tmp_db, "mbid-gone", "Old", "user", 10)

    mgr.client.get_playlist_details.return_value = None

    result = mgr.refresh_playlist("mbid-gone")

    assert result["success"] is False
    assert "not found upstream" in result["error"]


def test_refresh_playlist_defaults_to_user_type_for_unknown_mbid(tmp_db):
    """When the mbid isn't in the cache yet (new discovery), the
    manager defaults the playlist_type to ``user`` so the insert
    path in ``_update_playlist`` works. Avoids a NULL playlist_type
    column on the new row."""
    mgr = _build_manager(tmp_db)
    # No seed — mbid isn't cached yet.

    mgr.client.get_playlist_details.return_value = {
        "playlist": {
            "identifier": "https://listenbrainz.org/playlist/mbid-new",
            "title": "Newly Discovered",
            "creator": "ListenBrainz",
            "track": [],
            "annotation": "",
        }
    }

    result = mgr.refresh_playlist("mbid-new")

    assert result["success"] is True
    assert result["playlist_type"] == "user"


def test_refresh_playlist_exception_propagates_not_swallowed(tmp_db):
    """If the LB client raises (network failure, JSON parse error),
    the exception must propagate. Pre-fix the wrapping adapter
    silently swallowed; the manager is the right layer to surface
    real errors so the adapter can decide how to log."""
    mgr = _build_manager(tmp_db)
    _seed_playlist(tmp_db, "mbid-boom", "Boom", "user", 1)
    mgr.client.get_playlist_details.side_effect = ConnectionError("LB unreachable")

    with pytest.raises(ConnectionError):
        mgr.refresh_playlist("mbid-boom")


# ---------------------------------------------------------------------------
# Cost guard: refresh_playlist is strictly cheaper than update_all_playlists.
# ---------------------------------------------------------------------------


def test_refresh_playlist_does_not_walk_cleanup_or_rolling_series_for_unrelated_playlists(tmp_db):
    """``update_all_playlists`` runs ``_cleanup_old_playlists`` +
    ``_ensure_rolling_mirrors_from_cache`` at the tail. Those are
    fine for a full-refresh batch but wasted work for a single-
    playlist refresh. Pin that the targeted method skips them."""
    mgr = _build_manager(tmp_db)
    _seed_playlist(tmp_db, "mbid-narrow", "Narrow", "user", 0)

    mgr.client.get_playlist_details.return_value = {
        "playlist": {
            "identifier": "https://listenbrainz.org/playlist/mbid-narrow",
            "title": "Narrow",
            "creator": "ListenBrainz",
            "track": [],
            "annotation": "",
        }
    }

    # Spy the cleanup method.
    cleanup_calls: List[Any] = []
    original_cleanup = mgr._cleanup_old_playlists
    mgr._cleanup_old_playlists = lambda: cleanup_calls.append(True) or original_cleanup()

    mgr.refresh_playlist("mbid-narrow")

    assert cleanup_calls == []  # Cleanup must NOT fire for targeted refresh.
