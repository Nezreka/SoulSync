"""Exact-ID album discovery (Sokhi): Spotify comment-links + ISRC consensus.

Files from Spotify-derived tools carry an ISRC tag and the track's Spotify URL
in the comment; the text-based identification fails on JP releases while those
IDs answer 'which album is this' exactly. One recording lives on many releases,
so ISRCs resolve by folder CONSENSUS — the real album contains most of the
folder's codes, the single/compilation doesn't.
"""

from __future__ import annotations

from core.imports.exact_id_discovery import (
    consensus_album,
    discover_album_from_ids,
    extract_spotify_track_id,
)


def test_extract_spotify_track_id_url_and_uri():
    assert extract_spotify_track_id(
        "downloaded via https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp?si=x"
    ) == "3n3Ppam7vgaVa1iaRUc9Lp"
    assert extract_spotify_track_id("spotify:track:3n3Ppam7vgaVa1iaRUc9Lp") == "3n3Ppam7vgaVa1iaRUc9Lp"
    assert extract_spotify_track_id("https://open.spotify.com/intl-ja/track/abcdefghij12") == "abcdefghij12"
    assert extract_spotify_track_id("no link here") is None
    assert extract_spotify_track_id(None) is None


def _res(album_key, album="THE BOOK", artist="YOASOBI"):
    return {"album_key": album_key, "album": album, "artist": artist}


def test_consensus_album_majority_wins():
    rows = [_res("sp:album1")] * 8 + [_res("sp:single1", album="Yoru ni Kakeru - Single")] * 2
    assert consensus_album(rows)["album_key"] == "sp:album1"


def test_consensus_album_split_folder_refuses():
    rows = [_res("sp:a")] * 5 + [_res("sp:b", album="Other")] * 5
    assert consensus_album(rows) is None


def test_single_resolution_wins_outright():
    assert consensus_album([_res("sp:one")])["album_key"] == "sp:one"
    assert consensus_album([]) is None


def test_discovery_prefers_spotify_links_over_isrc():
    isrc_calls = []

    def by_link(tid):
        return {"album_key": "sp:album1", "album": "THE BOOK", "artist": "YOASOBI", "title": "Ansya"}

    def by_isrc(code):
        isrc_calls.append(code)
        return None

    tags = [{"spotify_track_id": "t1", "isrc": "JPU902000001"},
            {"spotify_track_id": "t2", "isrc": "JPU902000002"}]
    found = discover_album_from_ids(tags, resolve_spotify_track=by_link, resolve_isrc=by_isrc)
    assert found == {"artist": "YOASOBI", "album": "THE BOOK", "title": "Ansya", "via": "spotify-link"}
    assert isrc_calls == []                      # links answered; no isrc spend


def test_discovery_falls_back_to_isrc_consensus():
    def by_link(tid):
        return None

    def by_isrc(code):
        return {"album_key": "dz:9", "album": "THE BOOK", "artist": "YOASOBI"}

    tags = [{"isrc": f"JPU90200000{i}"} for i in range(3)]
    found = discover_album_from_ids(tags, resolve_spotify_track=by_link, resolve_isrc=by_isrc)
    assert found["via"] == "isrc" and found["album"] == "THE BOOK"


def test_discovery_caps_lookups_and_swallows_resolver_errors():
    calls = []

    def by_isrc(code):
        calls.append(code)
        raise RuntimeError("api down")

    tags = [{"isrc": f"CODE{i}"} for i in range(20)]
    assert discover_album_from_ids(tags, resolve_spotify_track=lambda t: None,
                                   resolve_isrc=by_isrc) is None
    assert len(calls) == 5                       # MAX_ID_LOOKUPS


def test_discovery_no_ids_is_none():
    assert discover_album_from_ids([{"title": "x"}],
                                   resolve_spotify_track=lambda t: None,
                                   resolve_isrc=lambda c: None) is None
