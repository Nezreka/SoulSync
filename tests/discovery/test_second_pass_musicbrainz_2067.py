"""#2067 (Storm): with MusicBrainz as the metadata source, the second pass kept
nearly every Spotify track "as spotify, musicbrainz had no match", although
the recording was there.

Two reasons. MusicBrainz has an exact lookup by ISRC and the second pass never
used it. And a MusicBrainz recording is on many releases while its Track names
one: the album check compared that one name with Spotify's album and threw
the recording away whenever MusicBrainz happened to pick another release.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.discovery.spotify_second_pass as ssp
from core.discovery.isrc_match import isrc_match
from core.musicbrainz_search import MusicBrainzSearchClient

SP_ID = "3Mbcuf19xIPVwec1RFeg44"
REC_ID = "29bfc79f-7617-40ee-8556-7f100e1880f5"


def _raw(isrc=""):
    return {
        "id": SP_ID, "name": "There's Nothing Like This",
        "artists": [{"name": "Omar", "id": "a1"}],
        "duration_ms": 241000, "track_number": 1, "disc_number": 1,
        "external_ids": {"isrc": isrc},
        "album": {"id": "alb", "name": "There's Nothing Like This", "release_date": "1990",
                  "album_type": "album", "total_tracks": 10,
                  "artists": [{"name": "Omar", "id": "a1"}]},
    }


def _release(title, rid, date, secondary=(), tracks=10):
    return {"id": rid, "title": title, "date": date,
            "release-group": {"id": "rg-" + rid, "primary-type": "Album",
                              "secondary-types": list(secondary)},
            "media": [{"track-count": tracks}]}


def _recording(releases, length=240000, artist="Omar", score=100):
    return {"id": REC_ID, "title": "There's Nothing Like This", "length": length, "score": score,
            "artist-credit": [{"name": artist, "artist": {"id": "x", "name": artist}}],
            "releases": releases}


@pytest.fixture
def mb(monkeypatch):
    client = MusicBrainzSearchClient.__new__(MusicBrainzSearchClient)
    client._client = MagicMock()
    monkeypatch.setattr(client, "_cached_art", lambda *a, **k: None, raising=False)
    return client


def _scorer(title, artist, duration_ms, results):
    return (results[0], 0.9, 0) if results else (None, 0.0, -1)


def _second_pass(mb):
    return ssp.spotify_second_pass(
        id_source="spotify", source_track_id=SP_ID, discovery_source="musicbrainz",
        use_spotify=False, title="There's Nothing Like This", artist="Omar",
        primary_client=mb, score_candidates=_scorer, fetch_free=lambda _id: None)


def test_a_recording_on_spotifys_album_matches_even_when_it_is_not_the_first_release(mb, monkeypatch):
    # the recording's first release is a best-of; the album spotify names is second
    best_of = _release("Best By Far", "rel-best", "2005", secondary=["Compilation"], tracks=18)
    album = _release("There's Nothing Like This", "rel-album", "1990")
    track = mb._recording_to_track(_recording([best_of, album]), "")
    assert track.album == "Best By Far"
    monkeypatch.setattr(mb, "search_tracks", lambda q, limit=10: [track], raising=False)
    monkeypatch.setattr(ssp, "fetch_spotify_track", lambda *a, **k: _raw())

    got = _second_pass(mb)

    assert got.provider == "musicbrainz" and got.how == "corroborated"
    assert got.track.id == REC_ID
    # and it is filed under the album spotify has it on, not the best-of
    assert (got.track.album, got.track.album_id) == ("There's Nothing Like This", "rel-album")
    assert got.track.release_date == "1990" and got.track.total_tracks == 10
    assert got.track.album_type == "album"


def test_a_recording_on_no_release_of_that_name_is_still_not_a_match(mb, monkeypatch):
    track = mb._recording_to_track(_recording([_release("Best By Far", "rel-best", "2005")]), "")
    monkeypatch.setattr(mb, "search_tracks", lambda q, limit=10: [track], raising=False)
    monkeypatch.setattr(ssp, "fetch_spotify_track", lambda *a, **k: _raw())

    got = _second_pass(mb)

    assert got.provider == "spotify" and got.how == "spotify"


def test_the_length_still_has_to_agree(mb, monkeypatch):
    album = _release("There's Nothing Like This", "rel-album", "1990")
    track = mb._recording_to_track(_recording([album], length=400000), "")
    monkeypatch.setattr(mb, "search_tracks", lambda q, limit=10: [track], raising=False)
    monkeypatch.setattr(ssp, "fetch_spotify_track", lambda *a, **k: _raw())

    assert _second_pass(mb).provider == "spotify"


def test_the_isrc_finds_the_recording_without_a_search(mb, monkeypatch):
    album = _release("There's Nothing Like This", "rel-album", "1990")
    best_of = _release("Best By Far", "rel-best", "2005", secondary=["Compilation"])
    mb._client.lookup_recordings_by_isrc.return_value = [_recording([])]
    response = MagicMock()
    response.json.return_value = {"recordings": [_recording([best_of, album])]}
    mb._client._get.return_value = response
    searched = []
    monkeypatch.setattr(mb, "search_tracks", lambda q, limit=10: searched.append(q) or [], raising=False)
    monkeypatch.setattr(ssp, "fetch_spotify_track", lambda *a, **k: _raw(isrc="GBAAA9000001"))

    got = _second_pass(mb)

    assert got.provider == "musicbrainz" and got.how == "isrc" and got.confidence == 1.0
    assert got.track.id == REC_ID and got.track.album == "There's Nothing Like This"
    mb._client.lookup_recordings_by_isrc.assert_called_once_with("GBAAA9000001")
    assert mb._client._get.call_args.kwargs["params"]["query"] == f"rid:{REC_ID}"
    assert not searched


def test_an_isrc_that_names_another_act_is_not_trusted(mb):
    mb._client.lookup_recordings_by_isrc.return_value = [_recording([], artist="Someone Else")]

    assert isrc_match("GBAAA9000001", "musicbrainz", False, fallback_client=mb,
                      artists=["Omar"]) is None
    mb._client._get.assert_not_called()


def test_the_isrc_match_survives_a_release_lookup_that_fails(mb):
    # a self-hosted server whose search index is still building
    mb._client.lookup_recordings_by_isrc.return_value = [_recording([])]
    mb._client._get.side_effect = RuntimeError("503")

    track = isrc_match("GBAAA9000001", "musicbrainz", False, fallback_client=mb, artists=["Omar"])

    assert track is not None and track.id == REC_ID


def test_the_releases_ride_along_without_becoming_part_of_the_track(mb):
    from dataclasses import asdict
    track = mb._recording_to_track(_recording([_release("Best By Far", "rel-best", "2005")]), "")
    assert "releases" not in asdict(track) and "_mb_releases" not in asdict(track)
