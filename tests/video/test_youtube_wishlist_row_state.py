"""A wished YouTube video has to say why it isn't downloading.

Movie and TV rows have carried an attempt count and a refusal sentence for a
while. YouTube rows carried nothing, so a video that had been given up on looked
exactly like one queued and waiting - which is how seventeen skipped videos
read as an empty wishlist. The rows now carry the same three fields the TV rows
do, under the same names, so the UI renders one shape for both lanes.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase, _yt_skip_reason


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _wish_video(db, vid, channel="UC1", title="A video"):
    conn = db._get_connection()
    try:
        # tmdb_id is a NOT NULL surrogate for YouTube rows (the real writer
        # passes a hash of the channel id); any stable int does here.
        conn.execute(
            "INSERT INTO video_wishlist (kind, source, source_id, parent_source_id, tmdb_id, "
            "title, episode_title, air_date, status) "
            "VALUES ('video','youtube',?,?,?,?,?, '2026-08-01','wanted')",
            (vid, channel, abs(hash(channel)) % 10**8, "Channel", title))
        conn.commit()
    finally:
        conn.close()


def _fail(db, vid, error, days_ago=0.0):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO video_download_history (kind, source, media_id, title, outcome, error, "
            "completed_at) VALUES ('youtube','youtube',?,?,'failed',?, datetime('now', ?))",
            (vid, "A video", error, "-%f days" % days_ago))
        conn.commit()
    finally:
        conn.close()


# ── the sentence ─────────────────────────────────────────────────────────────

def test_nothing_to_say_about_a_video_that_has_never_failed():
    assert _yt_skip_reason({}) is None
    assert _yt_skip_reason(None) is None


def test_a_deleted_video_says_it_is_unavailable():
    assert "Unavailable" in _yt_skip_reason({"permanent": True, "attempts": 1})


def test_a_backing_off_video_says_how_long():
    line = _yt_skip_reason({"attempts": 3, "strikes": 3, "hours_since_last": 0.2})
    assert "3 failed attempts" in line and "waiting 1h" in line


def test_a_video_past_its_wait_says_it_will_retry():
    line = _yt_skip_reason({"attempts": 3, "strikes": 3, "hours_since_last": 9})
    assert "will try again" in line


# ── the row the UI actually receives ─────────────────────────────────────────

def _first_video(db):
    items = db.query_youtube_wishlist()["items"]
    return items[0]["seasons"][0]["episodes"][0]


def test_a_clean_row_carries_no_warning(db):
    _wish_video(db, "vid-ok")
    row = _first_video(db)
    assert row["search_attempts"] == 0
    assert row["unavailable"] is False
    assert row["last_refusal"] is None


def test_a_members_only_video_is_marked_unavailable(db):
    _wish_video(db, "vid-gone")
    _fail(db, "vid-gone", "This video is available to this channel's members on level: X", 3)
    row = _first_video(db)
    assert row["unavailable"] is True
    assert "Unavailable" in row["last_refusal"]


def test_a_repeatedly_failing_video_carries_its_count(db):
    """The ten videos on Boulder's install that showed nothing at all."""
    _wish_video(db, "vid-flaky")
    for d in (0.9, 0.8, 0.7):
        _fail(db, "vid-flaky", "Connection reset by peer", d)
    row = _first_video(db)
    assert row["search_attempts"] == 3
    assert row["unavailable"] is False
    assert "failed attempt" in row["last_refusal"]


def test_the_fields_are_named_like_the_tv_rows(db):
    """One shape for both lanes — the UI renders the same badge either way."""
    _wish_video(db, "vid-x")
    row = _first_video(db)
    for field in ("search_attempts", "last_refusal", "status", "source_id"):
        assert field in row
