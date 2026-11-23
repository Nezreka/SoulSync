"""Unit tests for Watch Party social/lifecycle rules, Home layout rows,
custom guide YouTube channels, and Discovery playlist artwork fallbacks.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from core.player import discovery, home_layout, lineup, parties


class _InMemoryPlayerDB:
    """Minimal sqlite wrapper matching core.player's db._get_connection() contract."""

    def __init__(self, name: str):
        self.database_path = f":memory:{name}"
        self._conn = sqlite3.connect(":memory:")

    @contextmanager
    def _get_connection(self):
        yield self._conn


def test_home_layout_replaces_watchlist_with_collections_and_discovery_playlists():
    cat = home_layout.catalog()
    row_ids = [r["id"] for r in cat["rows"]]
    assert "watchlist" not in row_ids
    assert "collections" in row_ids
    assert "discovery-playlists" in row_ids


def test_discovery_norm_track_artwork_fallbacks():
    # Direct album_id fallback to library v2 artwork endpoint
    owned = discovery.norm_track({"title": "Song A", "artist_name": "Artist", "album_id": 42})
    assert owned["image_url"] == "/api/library/v2/artwork/album/42?size=thumb"

    # Explicit image_url / cover_url fallback
    with_img = discovery.norm_track({"track_name": "Song B", "image_url": "https://img.example/b.jpg"})
    assert with_img["image_url"] == "https://img.example/b.jpg"

    with_cov = discovery.norm_track({"track_name": "Song C", "cover_url": "https://img.example/c.jpg"})
    assert with_cov["image_url"] == "https://img.example/c.jpg"


def test_custom_guide_lineup_supports_youtube_kind():
    assert "youtube" in lineup.KINDS
    assert "movies" in lineup.KINDS
    assert "tv" in lineup.KINDS


def test_watch_party_pause_resume_countdown_pee_break_and_leave_handoff():
    db = _InMemoryPlayerDB("wp_lifecycle")
    now = 1_700_000_000.0
    p = parties.create_party(
        db,
        host_profile_id=1,
        body={"title": "Test Movie", "item_type": "movie", "item_id": "101"},
        now=now,
    )
    pid = p["id"]

    # Second member joins
    parties.join_party(db, pid, profile_id=2, ready=True, now=now + 1)

    # Host starts playback
    started = parties.update_state(db, pid, 1, {"action": "start", "position": 0.0}, now=now + 2)
    assert started["status"] == "playing"

    # Any member (non-host) can pause
    paused = parties.update_state(db, pid, 2, {"action": "pause", "position": 120.0}, now=now + 122)
    assert paused["status"] == "paused"

    # Resuming triggers the 5-second synchronized countdown (resume_at > now)
    resumed = parties.update_state(db, pid, 1, {"action": "resume", "position": 120.0}, now=now + 130)
    assert resumed["status"] == "playing"
    assert resumed["resume_at"] is not None
    assert resumed["resume_at"] == pytest.approx(now + 130 + parties.RESUME_COUNTDOWN_SEC)

    # Member 2 calls a 5-minute Pee Break
    pb = parties.update_state(db, pid, 2, {"action": "pee_break", "position": 200.0}, now=now + 210)
    assert pb["status"] == "paused"
    assert pb["pee_break"] is not None
    assert pb["pee_break"]["profile_id"] == 2

    # Member 2 resumes after returning
    after_pb = parties.update_state(db, pid, 2, {"action": "resume", "position": 200.0}, now=now + 260)
    assert after_pb["status"] == "playing"
    assert after_pb["pee_break"] is None

    # Member 2 cannot immediately call another pee break (10-minute cooldown)
    with pytest.raises(parties.PartyError, match="cooldown|wait|minute|pee"):
        parties.update_state(db, pid, 2, {"action": "pee_break", "position": 210.0}, now=now + 270)

    # Emoji burst rate limit: 5 allowed, 6th within 4s rejected
    for i in range(parties.REACTION_BURST_LIMIT):
        parties.post_message(db, pid, 1, {"kind": "reaction", "body": "🍿"}, now=now + 300 + i * 0.2)
    with pytest.raises(parties.PartyError):
        parties.post_message(db, pid, 1, {"kind": "reaction", "body": "🔥"}, now=now + 301.2)

    # Host leaves -> ownership hands off to profile 2
    assert parties.leave_party(db, pid, profile_id=1, now=now + 310) is True
    after_host_leave = parties.get_party(db, pid, viewer_id=2, now=now + 311)
    assert after_host_leave is not None
    assert after_host_leave["host_profile_id"] == 2

    # Last member leaves -> party closes cleanly
    assert parties.leave_party(db, pid, profile_id=2, now=now + 320) is True
    after_last_leave = parties.get_party(db, pid, viewer_id=2, now=now + 321)
    assert after_last_leave is None or after_last_leave.get("status") == "ended"
