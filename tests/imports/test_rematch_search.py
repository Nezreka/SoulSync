"""#889 Phase 3: re-identify search — normalize results across sources, infer the
release-type badge, and resolve the picked row's album_id.

Locks down: same song surfaces as multiple rows (single/EP/album), the EP
inference from a multi-track 'single', graceful empty on a dead source, and that
resolve_hint_fields pulls album_id (and refuses a result without one).
"""

from __future__ import annotations

import types

from core.imports.rematch_search import (
    available_sources,
    infer_release_type,
    normalize_search_result,
    resolve_hint_fields,
    search_release_candidates,
)


# typed-Track-ish object (mirrors core.metadata.types.Track attrs the modal reads)
def _track(tid, title, album, album_type, total, isrc=None, year="2020"):
    return types.SimpleNamespace(
        id=tid, name=title, artists=["Artist"], album=album,
        album_type=album_type, total_tracks=total, release_date=year + "-01-01",
        image_url="http://img/" + tid, isrc=isrc, external_ids={},
    )


# ── release-type inference ────────────────────────────────────────────────────
def test_infer_album_stays_album():
    assert infer_release_type("album", 12) == "album"


def test_infer_single_one_track_is_single():
    assert infer_release_type("single", 1) == "single"


def test_infer_multitrack_single_promoted_to_ep():
    # Spotify labels EPs as album_type='single' — promote on track count.
    assert infer_release_type("single", 5) == "ep"


def test_infer_compilation():
    assert infer_release_type("compilation", 40) == "compilation"


def test_infer_unknown_falls_back_to_count():
    assert infer_release_type(None, 10) == "album"
    assert infer_release_type("", 4) == "ep"
    assert infer_release_type(None, 1) == "single"


# ── normalization ─────────────────────────────────────────────────────────────
def test_normalize_builds_display_row():
    row = normalize_search_result(_track("t1", "Song", "Album1", "album", 12, isrc="US1234567890"), "spotify")
    assert row["track_id"] == "t1"
    assert row["album_name"] == "Album1" and row["album_type"] == "album"
    assert row["artist_name"] == "Artist"
    assert row["year"] == "2020" and row["isrc"] == "US1234567890"


def test_normalize_skips_result_without_id_or_title():
    assert normalize_search_result(types.SimpleNamespace(id="", name="X"), "spotify") is None
    assert normalize_search_result(types.SimpleNamespace(id="t", name=""), "spotify") is None


def test_same_song_multiple_collections():
    """The headline case: one song, three releases, three distinct rows + badges."""
    results = [
        _track("t_alb", "Song", "Album1", "album", 12),
        _track("t_ep", "Song", "EP1", "single", 5),       # multi-track single → EP
        _track("t_sgl", "Song", "Song (Single)", "single", 1),
    ]
    client = types.SimpleNamespace(search_tracks=lambda q, limit=25: results)
    rows = search_release_candidates("spotify", "Song", client_factory=lambda s: client)
    badges = {r["album_name"]: r["album_type"] for r in rows}
    assert badges == {"Album1": "album", "EP1": "ep", "Song (Single)": "single"}


def test_search_empty_on_missing_client():
    assert search_release_candidates("spotify", "x", client_factory=lambda s: None) == []


def test_search_empty_on_blank_query():
    called = []
    search_release_candidates("spotify", "   ", client_factory=lambda s: called.append(1))
    assert called == []   # never even fetches a client for an empty query


def test_search_swallows_client_error():
    def boom(q, limit=25):
        raise RuntimeError("rate limited")
    client = types.SimpleNamespace(search_tracks=boom)
    assert search_release_candidates("spotify", "x", client_factory=lambda s: client) == []


# ── resolve on select ─────────────────────────────────────────────────────────
def test_resolve_pulls_album_id_and_fields():
    details = {
        "name": "Song", "track_number": 5, "disc_number": 1, "isrc": "US1234567890",
        "album": {"id": "alb_album1", "name": "Album1", "album_type": "album", "total_tracks": 12},
        "artists": [{"id": "art_1", "name": "Artist"}],
    }
    client = types.SimpleNamespace(get_track_details=lambda tid: details)
    out = resolve_hint_fields("spotify", "t_alb", client_factory=lambda s: client)
    assert out["album_id"] == "alb_album1"
    assert out["artist_id"] == "art_1"
    assert out["track_number"] == 5 and out["disc_number"] == 1
    assert out["album_type"] == "album" and out["isrc"] == "US1234567890"


def test_resolve_refuses_result_without_album_id():
    details = {"name": "Song", "album": {"name": "NoId Album"}}   # no album id
    client = types.SimpleNamespace(get_track_details=lambda tid: details)
    assert resolve_hint_fields("spotify", "t", client_factory=lambda s: client) is None


def test_resolve_none_on_missing_client():
    assert resolve_hint_fields("spotify", "t", client_factory=lambda s: None) is None
