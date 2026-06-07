"""Pure blocklist matching — the cascade + ID/name rules.

Block an artist/album/track by metadata-source ID; a candidate track is
blocked if its track, album, or any artist matches (cascade). Same-source ID
match is primary; artist NAME is a fallback (covers the backfill window);
albums/tracks are ID-only to avoid common-title false positives.
"""

from __future__ import annotations

from core.blocklist.matching import (
    ENTITY_ALBUM,
    ENTITY_ARTIST,
    ENTITY_TRACK,
    build_index,
    candidate_block_reason,
)


def _row(entity_type, name="", **ids):
    return {"entity_type": entity_type, "name": name, **ids}


def _check(index, source="spotify", **kw):
    return candidate_block_reason(index, source=source, **kw)


# ── empty / no-match ─────────────────────────────────────────────────────────

def test_empty_blocklist_blocks_nothing():
    idx = build_index([])
    assert idx.is_empty
    assert _check(idx, track_id="t1", album_id="al1",
                  artists=[{"id": "ar1", "name": "X"}]) is None


def test_unrelated_candidate_not_blocked():
    idx = build_index([_row(ENTITY_ARTIST, "Drake", spotify_id="drake-sp")])
    assert _check(idx, artists=[{"id": "other", "name": "Adele"}]) is None


# ── artist level + cascade ───────────────────────────────────────────────────

def test_artist_blocked_by_id_blocks_their_track():
    idx = build_index([_row(ENTITY_ARTIST, "Drake", spotify_id="drake-sp")])
    reason = _check(idx, track_id="t9", album_id="al9",
                    artists=[{"id": "drake-sp", "name": "Drake"}])
    assert reason == (ENTITY_ARTIST, "Drake")


def test_artist_blocked_by_name_fallback():
    # No id resolved for this source yet (backfill window) — name still catches it.
    idx = build_index([_row(ENTITY_ARTIST, "Drake", deezer_id="drake-dz")])
    reason = _check(idx, source="spotify", artists=[{"id": "drake-sp", "name": "drake"}])
    assert reason == (ENTITY_ARTIST, "drake")


def test_artist_name_match_is_case_insensitive():
    idx = build_index([_row(ENTITY_ARTIST, "Tyler, The Creator", spotify_id="x")])
    assert _check(idx, artists=[{"id": None, "name": "tyler, the creator"}]) is not None


# ── album level ──────────────────────────────────────────────────────────────

def test_album_blocked_by_id_blocks_its_track():
    idx = build_index([_row(ENTITY_ALBUM, "Scorpion", spotify_id="scorp-sp")])
    reason = _check(idx, track_id="t1", album_id="scorp-sp", album_name="Scorpion",
                    artists=[{"id": "drake-sp", "name": "Drake"}])
    assert reason == (ENTITY_ALBUM, "Scorpion")


def test_album_name_does_not_match_without_id():
    # Common title must NOT block across artists on name alone.
    idx = build_index([_row(ENTITY_ALBUM, "Greatest Hits", spotify_id="gh-queen")])
    reason = _check(idx, album_id="gh-abba", album_name="Greatest Hits",
                    artists=[{"id": "abba", "name": "ABBA"}])
    assert reason is None


# ── track level ──────────────────────────────────────────────────────────────

def test_track_blocked_by_id():
    idx = build_index([_row(ENTITY_TRACK, "Hotline Bling", spotify_id="hb-sp")])
    reason = _check(idx, track_id="hb-sp", track_name="Hotline Bling",
                    album_id="al", artists=[{"id": "drake", "name": "Drake"}])
    assert reason == (ENTITY_TRACK, "Hotline Bling")


def test_track_name_alone_does_not_block():
    idx = build_index([_row(ENTITY_TRACK, "Intro", spotify_id="intro-1")])
    assert _check(idx, track_id="intro-2", track_name="Intro",
                  artists=[{"id": "z", "name": "Z"}]) is None


# ── source isolation (numeric id collision guard) ────────────────────────────

def test_same_id_different_source_does_not_collide():
    # Deezer artist 12246 is blocked; an iTunes artist that happens to be 12246
    # is a DIFFERENT entity and must NOT match (candidate source = itunes).
    idx = build_index([_row(ENTITY_ARTIST, "Some Deezer Artist", deezer_id="12246")])
    reason = _check(idx, source="itunes", artists=[{"id": "12246", "name": "Other"}])
    assert reason is None


def test_same_id_same_source_matches():
    idx = build_index([_row(ENTITY_ARTIST, "A", deezer_id="12246")])
    reason = _check(idx, source="deezer", artists=[{"id": "12246", "name": "A"}])
    assert reason is not None


# ── multi-source row ─────────────────────────────────────────────────────────

def test_row_with_multiple_source_ids_matches_each():
    idx = build_index([_row(ENTITY_ARTIST, "Drake",
                            spotify_id="sp", itunes_id="it", deezer_id="dz")])
    assert _check(idx, source="spotify", artists=[{"id": "sp", "name": "Drake"}])
    assert _check(idx, source="itunes", artists=[{"id": "it", "name": "Drake"}])
    assert _check(idx, source="deezer", artists=[{"id": "dz", "name": "Drake"}])


# ── cascade precedence ───────────────────────────────────────────────────────

def test_track_hit_reported_before_artist():
    idx = build_index([
        _row(ENTITY_ARTIST, "Drake", spotify_id="drake"),
        _row(ENTITY_TRACK, "God's Plan", spotify_id="gp"),
    ])
    reason = _check(idx, track_id="gp", track_name="God's Plan",
                    artists=[{"id": "drake", "name": "Drake"}])
    assert reason[0] == ENTITY_TRACK   # most specific hit wins the reason
