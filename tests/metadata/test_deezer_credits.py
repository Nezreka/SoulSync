"""#2104: Deezer's album tracklist names only a track's first artist, so a
release the artist co-leads read as "artist mismatch" and every track of it
was dropped (Bad Bunny's joint album with J Balvin, 8 of 8)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.metadata import deezer_credits
from core.metadata.discography_filters import track_artist_matches


class _Deezer:
    """the three calls, answering as api.deezer.com does for these releases"""

    def __init__(self, albums=None, tracks=None):
        self.albums, self.tracks = albums or {}, tracks or {}
        self.album_calls, self.track_calls = [], []

    def get_album_metadata(self, album_id, include_tracks=True):
        self.album_calls.append(album_id)
        raw = self.albums.get(album_id)
        return {"id": album_id, "_raw_data": raw} if raw else None

    def get_track_details(self, track_id):
        self.track_calls.append(track_id)
        names = self.tracks.get(track_id)
        return {"id": track_id, "artists": names} if names else None


OASIS = {"record_type": "album", "contributors": [{"name": "J. Balvin"}, {"name": "Bad Bunny"}]}


def test_the_tracklist_alone_turns_the_co_main_artist_down():
    """the bug: all the tracklist says is J. Balvin"""
    assert not track_artist_matches([{"name": "J. Balvin"}], "Bad Bunny")


def test_a_joint_albums_co_main_artist_is_credited_on_its_tracks():
    client = _Deezer(albums={"101490842": OASIS})
    assert deezer_credits.credits_artist(
        "Bad Bunny", album_id="101490842", track_id="702421782", client=client)
    assert client.track_calls == []          # the album answered, no track lookups


def test_the_album_is_asked_once_for_all_its_tracks():
    client, memo = _Deezer(albums={"101490842": OASIS}), {}
    for track_id in ("1", "2", "3"):
        assert deezer_credits.credits_artist(
            "Bad Bunny", album_id="101490842", track_id=track_id, memo=memo, client=client)
    assert client.album_calls == ["101490842"]


def test_a_feature_the_album_does_not_name_is_found_on_the_track():
    client = _Deezer(albums={"9": {"record_type": "album", "contributors": [{"name": "Arcangel"}]}},
                     tracks={"77": ["Arcangel", "Bad Bunny"]})
    assert deezer_credits.credits_artist("Bad Bunny", album_id="9", track_id="77", client=client)


def test_an_artist_on_neither_is_still_dropped():
    """#559: a compilation track the artist is not on"""
    client = _Deezer(albums={"9": {"record_type": "album", "contributors": [{"name": "Arcangel"}]}},
                     tracks={"77": ["Arcangel", "De La Ghetto"]})
    assert not deezer_credits.credits_artist("Bad Bunny", album_id="9", track_id="77", client=client)


@pytest.mark.parametrize("album", [
    {"record_type": "compile", "contributors": [{"name": "Bad Bunny"}, {"name": "Someone"}]},
    {"record_type": "album", "contributors": [{"name": n} for n in
                                              ("Bad Bunny", "A", "B", "C", "D")]},
])
def test_a_compilation_naming_the_artist_does_not_vouch_for_every_track(album):
    """being one of a crowd on the album says nothing about one track: each
    track is asked about on its own"""
    client = _Deezer(albums={"5": album}, tracks={"1": ["Someone Else"], "2": ["A", "Bad Bunny"]})
    assert not deezer_credits.credits_artist("Bad Bunny", album_id="5", track_id="1", client=client)
    assert deezer_credits.credits_artist("Bad Bunny", album_id="5", track_id="2", client=client)


def test_it_never_raises_and_answers_no_with_nothing_to_ask():
    class _Broken:
        def get_album_metadata(self, *a, **k):
            raise RuntimeError("deezer is down")

        def get_track_details(self, *a, **k):
            raise RuntimeError("deezer is down")

    assert not deezer_credits.credits_artist("Bad Bunny", album_id="1", track_id="2", client=_Broken())
    assert not deezer_credits.credits_artist("", album_id="1", track_id="2", client=_Deezer())
    assert not deezer_credits.credits_artist("Bad Bunny", client=_Deezer())


def test_the_watchlist_scan_asks_deezer_before_dropping_a_co_led_release(monkeypatch):
    from core.watchlist_scanner import WatchlistScanner

    client = _Deezer(albums={"101490842": OASIS})
    monkeypatch.setattr(deezer_credits, "_client", lambda: client)
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    watched = SimpleNamespace(artist_name="Bad Bunny")
    track = {"id": "702421782", "name": "MOJAITA", "artists": [{"name": "J. Balvin"}],
             "_source": "deezer"}
    album = {"id": "101490842", "artists": [{"name": "J. Balvin"}], "_source": "deezer"}
    assert scanner._track_credits_watched_artist(track, album, watched)
    # another source's tracklist is taken at its word, as before
    spotify = dict(track, _source="spotify")
    assert not scanner._track_credits_watched_artist(spotify, dict(album, _source="spotify"), watched)
    assert client.album_calls == ["101490842"]
