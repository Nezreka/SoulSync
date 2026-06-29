"""Playlist export orchestrator (#903): dedup + stats + progress accounting.

Pins: repeated songs resolve once (deduped), per-source breakdown counts only fresh
resolutions, unmatched tracks carry recording_mbid=None but stay in order, alternate
field shapes (artist_name/track_name) are accepted, and a throwing progress callback
never fails the export.
"""

from __future__ import annotations

from core.exports.playlist_export import resolve_playlist_tracks

MBID = "e8f9b188-f819-4e43-ab0f-4bd26ce9ff56"
MBID2 = "8f3471b5-7e6a-4c1f-9c1a-2b2b2b2b2b2b"


def test_resolves_and_keeps_order_and_unmatched():
    table = {("A", "T1"): (MBID, "db"), ("A", "T2"): (None, None)}
    rf = lambda a, t: table.get((a, t), (None, None))
    out = resolve_playlist_tracks(
        [{"artist": "A", "title": "T1"}, {"artist": "A", "title": "T2"}], rf
    )
    res = out["resolved"]
    assert [r["title"] for r in res] == ["T1", "T2"]
    assert res[0]["recording_mbid"] == MBID
    assert res[1]["recording_mbid"] is None
    assert out["stats"]["resolved"] == 1
    assert out["stats"]["unmatched"] == 1
    assert out["stats"]["by_source"] == {"db": 1}


def test_dedup_resolves_repeated_song_once():
    calls = {"n": 0}
    def rf(a, t):
        calls["n"] += 1
        return (MBID, "musicbrainz")
    tracks = [{"artist": "A", "title": "Song"}, {"artist": "a", "title": "song"},  # same (normalized)
              {"artist": "A", "title": "Song"}]
    out = resolve_playlist_tracks(tracks, rf)
    assert calls["n"] == 1                       # resolve_fn called once for 3 identical
    assert out["stats"]["deduped"] == 2
    assert out["stats"]["resolved"] == 3         # all three tracks still get the mbid
    assert out["stats"]["by_source"] == {"musicbrainz": 1}  # counted once (fresh only)
    assert all(r["recording_mbid"] == MBID for r in out["resolved"])


def test_accepts_alternate_field_names():
    rf = lambda a, t: (MBID, "db") if (a, t) == ("Artist", "Track") else (None, None)
    out = resolve_playlist_tracks(
        [{"artist_name": "Artist", "track_name": "Track", "album_name": "Alb"}], rf
    )
    r = out["resolved"][0]
    assert r["artist"] == "Artist" and r["title"] == "Track" and r["album"] == "Alb"
    assert r["recording_mbid"] == MBID


def test_progress_called_per_track_and_safe_when_throwing():
    seen = []
    def prog(done, total, stats):
        seen.append((done, total))
        raise RuntimeError("display blew up")
    out = resolve_playlist_tracks(
        [{"artist": "A", "title": "x"}, {"artist": "B", "title": "y"}],
        lambda a, t: (MBID, "db"),
        on_progress=prog,
    )
    assert seen == [(1, 2), (2, 2)]              # called each track despite raising
    assert out["stats"]["resolved"] == 2          # export still completed


def test_empty_playlist():
    out = resolve_playlist_tracks([], lambda a, t: (None, None))
    assert out["resolved"] == []
    assert out["stats"]["total"] == 0


# ── id_key generalization (#945 service export reuses the LB resolver) ──

from core.exports.playlist_export import resolve_playlist_tracks as _rpt


def _const_resolver(mapping):
    return lambda artist, title: mapping.get((artist, title), (None, None))


def test_default_id_key_is_recording_mbid_unchanged():
    # ListenBrainz/JSPF callers must be byte-for-byte unaffected by the generalization.
    out = _rpt([{'artist': 'A', 'title': 'X'}], _const_resolver({('A', 'X'): ('mbid-1', 'db')}))
    assert out['resolved'][0]['recording_mbid'] == 'mbid-1'
    assert 'service_track_id' not in out['resolved'][0]


def test_custom_id_key_carries_service_id():
    out = _rpt(
        [{'artist': 'A', 'title': 'X'}, {'artist': 'B', 'title': 'Y'}],
        _const_resolver({('A', 'X'): ('spid-1', 'library')}),   # B/Y unmatched
        id_key='service_track_id',
    )
    assert out['resolved'][0]['service_track_id'] == 'spid-1'
    assert out['resolved'][1]['service_track_id'] is None
    assert 'recording_mbid' not in out['resolved'][0]
    assert out['stats']['resolved'] == 1 and out['stats']['unmatched'] == 1
