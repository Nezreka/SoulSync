"""A track auto-downloaded from the playlist pipeline / wishlist / watchlist is
identified as belonging to an album, but Deezer's search/track and MusicBrainz's
recording lookups don't carry a track POSITION — so detect_album_info_web left
track_number=None, the pipeline floored it to 1, and album tracks landed as 01/1
(verified live: Deezer says "Obelisk" is track 9 of The Grand Mirage, tagged 1/1).

The fix resolves the real position from the album's OWN track list. These pin the
pure matcher and the (fail-safe) integration wrapper.
"""

from __future__ import annotations

import pytest

from core.imports.album_position import resolve_track_position_in_album


def _album_12():
    # a realistic 12-track album payload shape (get_album_tracks_for_source -> 'tracks')
    return [
        {"id": f"t{i}", "name": n, "track_number": i, "disc_number": 1,
         "isrc": f"ISRC{i:03d}"}
        for i, n in enumerate(
            ["Intro", "Drift", "Mirage", "Haze", "Pulse", "Glow", "Echo", "Tide",
             "Obelisk", "Comet", "Dawn", "Outro"], start=1)
    ]


# ── pure matcher ─────────────────────────────────────────────────────────────

def test_resolves_position_by_title():
    tn, dn = resolve_track_position_in_album(_album_12(), title="Obelisk")
    assert (tn, dn) == (9, 1)


def test_title_match_is_case_and_punctuation_insensitive():
    tracks = [{"id": "t1", "name": "Lueur Déclinante!!!", "track_number": 3, "disc_number": 1}]
    tn, _ = resolve_track_position_in_album(tracks, title="lueur déclinante")
    assert tn == 3


def test_resolves_by_isrc_exactly():
    tn, dn = resolve_track_position_in_album(_album_12(), isrc="isrc009")  # case-insensitive
    assert (tn, dn) == (9, 1)


def test_resolves_by_track_id():
    tn, _ = resolve_track_position_in_album(_album_12(), track_id="t9")
    assert tn == 9


def test_isrc_beats_id_beats_title_on_conflict():
    # craft a list where ISRC, id, and title each point at a DIFFERENT track
    tracks = [
        {"id": "byid", "name": "other", "track_number": 2, "disc_number": 1, "isrc": "X"},
        {"id": "z", "name": "WANT", "track_number": 3, "disc_number": 1, "isrc": "Y"},
        {"id": "z2", "name": "other2", "track_number": 4, "disc_number": 1, "isrc": "WANTISRC"},
    ]
    # all three signals provided -> ISRC wins (track 4)
    tn, _ = resolve_track_position_in_album(tracks, title="WANT", track_id="byid", isrc="wantisrc")
    assert tn == 4
    # no ISRC -> id wins (track 2)
    tn, _ = resolve_track_position_in_album(tracks, title="WANT", track_id="byid")
    assert tn == 2
    # only title -> title wins (track 3)
    tn, _ = resolve_track_position_in_album(tracks, title="want")
    assert tn == 3


def test_carries_disc_number():
    tracks = [{"id": "t1", "name": "B-Side", "track_number": 2, "disc_number": 2}]
    assert resolve_track_position_in_album(tracks, title="B-Side") == (2, 2)


def test_no_match_returns_none():
    assert resolve_track_position_in_album(_album_12(), title="Not On This Album") == (None, None)
    assert resolve_track_position_in_album([], title="x") == (None, None)
    assert resolve_track_position_in_album(None, title="x") == (None, None)


def test_skips_entries_without_a_valid_position():
    tracks = [
        {"id": "t1", "name": "Obelisk", "track_number": 0},      # 0 -> skip
        {"id": "t2", "name": "Obelisk", "track_number": None},   # None -> skip
        {"id": "t3", "name": "Obelisk", "track_number": "junk"}, # junk -> skip
    ]
    assert resolve_track_position_in_album(tracks, title="Obelisk") == (None, None)


# ── integration wrapper (fail-safe, real album lookup) ───────────────────────

def test_wrapper_resolves_from_source_album(monkeypatch):
    import core.imports.context as ctx
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source",
                        lambda source, album_id: {"tracks": _album_12()})
    context = {"source": "deezer",
               "track_info": {"id": "t9", "name": "Obelisk", "deezer_album_id": "232620572"}}
    tn, dn = ctx._resolve_album_position_from_source(context, {}, 1)
    assert tn == 9 and dn == 1


def test_wrapper_is_failsafe_on_empty_or_missing(monkeypatch):
    import core.imports.context as ctx
    # no album id at all -> keeps current disc, no number
    assert ctx._resolve_album_position_from_source({"source": "deezer", "track_info": {}}, {}, 1) == (None, 1)
    # fetcher returns nothing -> fail-safe
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source",
                        lambda source, album_id: {"tracks": []})
    context = {"source": "deezer", "track_info": {"name": "Obelisk", "deezer_album_id": "x"}}
    assert ctx._resolve_album_position_from_source(context, {}, 2) == (None, 2)


def test_wrapper_never_raises_on_fetch_error(monkeypatch):
    import core.imports.context as ctx
    def _boom(source, album_id):
        raise RuntimeError("deezer down")
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source", _boom)
    context = {"source": "deezer", "track_info": {"name": "Obelisk", "deezer_album_id": "x"}}
    assert ctx._resolve_album_position_from_source(context, {}, 1) == (None, 1)
