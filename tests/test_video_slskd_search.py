"""Real slskd search — the pure, testable seams (query building + grouping slskd
responses into per-release hits). The HTTP poll itself is thin I/O glue, not tested
here. Isolated from music."""

from __future__ import annotations

from core.video.slskd_search import build_query, group_video_files


def test_build_query_per_scope():
    assert build_query("movie", "The Matrix", year=1999) == "The Matrix 1999"
    assert build_query("movie", "The Matrix") == "The Matrix"
    assert build_query("episode", "The Wire", season=2, episode=3) == "The Wire S02E03"
    assert build_query("season", "The Wire", season=2) == "The Wire S02"
    assert build_query("series", "The Wire") == "The Wire"


def test_group_video_files_groups_by_release_folder():
    responses = [
        {"username": "alice", "uploadSpeed": 900, "freeUploadSlots": 1, "files": [
            {"filename": r"@@x\The.Wire.S02.1080p.BluRay.x265-GRP\the.wire.s02e01.mkv", "size": 3_000_000_000},
            {"filename": r"@@x\The.Wire.S02.1080p.BluRay.x265-GRP\the.wire.s02e02.mkv", "size": 3_200_000_000},
            {"filename": r"@@x\The.Wire.S02.1080p.BluRay.x265-GRP\readme.nfo", "size": 1024},
        ]},
        {"username": "bob", "uploadSpeed": 1500, "freeUploadSlots": 2, "files": [
            {"filename": "The.Wire.S02.1080p.BluRay.x265-GRP/the.wire.s02e01.mkv", "size": 3_000_000_000},
        ]},
    ]
    hits = group_video_files(responses)
    assert len(hits) == 1                      # both users → one release
    h = hits[0]
    assert h["title"] == "The.Wire.S02.1080p.BluRay.x265-GRP"
    assert h["peers"] == 2                      # alice + bob
    assert h["username"] == "bob"               # bob is faster → the chosen source
    assert h["slots"] == 2
    assert h["size_bytes"] == 3_200_000_000     # largest video file in the folder


def test_group_skips_non_video_and_samples():
    responses = [{"username": "u", "uploadSpeed": 1, "freeUploadSlots": 0, "files": [
        {"filename": "Movie.2020.1080p/sample.mkv", "size": 50_000_000},   # sample dropped
        {"filename": "Movie.2020.1080p/Movie.2020.1080p.srt", "size": 40000},  # subs dropped
        {"filename": "Movie.2020.1080p/movie.mp4", "size": 8_000_000_000},
    ]}]
    hits = group_video_files(responses)
    assert len(hits) == 1 and hits[0]["size_bytes"] == 8_000_000_000


def test_group_handles_garbage():
    assert group_video_files(None) == []
    assert group_video_files([{"nope": 1}, "junk"]) == []


# ── search-creation rate-limit throttle (avoids slskd 429s) ──────────────────
import core.video.slskd_search as _ss  # noqa: E402


def _reset_throttle():
    _ss._SEARCH_TIMES.clear()
    _ss._COOLDOWN_UNTIL[0] = 0.0


def test_throttle_spaces_consecutive_creations():
    _reset_throttle()
    t1 = _ss._reserve_search_slot()
    t2 = _ss._reserve_search_slot()
    assert t2 - t1 >= _ss._MIN_GAP_SECONDS - 0.01       # min gap between creations


def test_throttle_window_cap_holds_the_overflow():
    _reset_throttle()
    times = [_ss._reserve_search_slot() for _ in range(_ss._MAX_PER_WINDOW + 1)]
    # the one past the window cap waits ~a full window past the first
    assert times[-1] >= times[0] + _ss._WINDOW_SECONDS - 0.5


def test_429_sets_a_cooldown():
    import time
    _reset_throttle()
    _ss._note_rate_limited("10")                        # Retry-After: 10s
    nxt = _ss._reserve_search_slot()
    assert nxt >= time.monotonic() + 8                  # next search waits out the cooldown
    _reset_throttle()
