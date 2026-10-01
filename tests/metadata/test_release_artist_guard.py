"""#1426: a musicbrainz release found by album title alone ("Mammoth" by Mammoth
Mammoth) must not be applied to another band's album, and a track slot must hold
the same song before its recording and credits are written."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import core.album_consistency as ac
from core.metadata import source as ms
from core.metadata.musicbrainz_tags import release_by_artist, track_title_agrees

WVH = "49a6efb9-9b52-44ce-8167-7cb1c21a8c45"
MM = "16838bec-eb0d-4609-917f-f594c0d3e1c4"


def _credit(name, mbid, entity_name=None):
    return [{"name": name, "joinphrase": "", "artist": {"id": mbid, "name": entity_name or name}}]


def _release(rid, credit, titles):
    return {"id": rid, "title": "Mammoth", "status": "Official", "country": "AU", "date": "2009-10-06",
            "artist-credit": credit, "release-group": {"id": rid + "-rg", "first-release-date": "2009-10-06"},
            "media": [{"position": 1, "format": "CD", "tracks": [
                {"id": f"{rid}-t{i}", "position": i, "title": t,
                 "recording": {"id": f"{rid}-r{i}", "title": t, "artist-credit": credit}}
                for i, t in enumerate(titles, 1)]}]}


MAMMOTH_MAMMOTH = _release("c1daac15", _credit("Mammoth Mammoth", MM),
                           ["500 Horsepower", "Go", "Bad Dream", "Lies", "Up", "Down", "Left", "Right", "Fire", "Ice"])
WVH_ALBUM = _release("wvh", _credit("Mammoth WVH", WVH, entity_name="Mammoth"),
                     ["Mr. Ed", "Over and Over", "Over and Out", "Distance"])


# ── the guards ──

def test_another_band_is_not_the_artist():
    assert not release_by_artist(MAMMOTH_MAMMOTH, "Mammoth")


def test_old_credit_name_still_matches_by_current_name_or_id():
    assert release_by_artist(WVH_ALBUM, "Mammoth")                     # entity name
    assert release_by_artist(WVH_ALBUM, "Mammoth WVH")                 # credit name
    other_script = _release("u", _credit("宇多田ヒカル", "utada"), ["First Love"])
    assert not release_by_artist(other_script, "Hikaru Utada")
    assert release_by_artist(other_script, "Hikaru Utada", artist_mbid="utada")


def test_collab_credit_matches_the_joined_name():
    rel = {"artist-credit": [{"name": "Jay-Z", "joinphrase": " & ", "artist": {"id": "a"}},
                             {"name": "Kanye West", "joinphrase": "", "artist": {"id": "b"}}]}
    assert release_by_artist(rel, "JAY-Z & Kanye West")
    assert release_by_artist(rel, "Kanye West")


def test_slot_must_hold_the_same_song():
    track = {"title": "500 Horsepower", "recording": {"title": "500 Horsepower"}}
    assert not track_title_agrees("Mr. Ed", track)
    assert track_title_agrees("Song - Remastered 2011", {"title": "Song"})
    assert track_title_agrees("Song (feat. X)", {"title": "Song"})
    assert track_title_agrees("", track)                               # nothing to judge


# ── album consistency ──

def _mb(releases, artist_mbid=None):
    client = SimpleNamespace(
        search_release=Mock(return_value=[{"id": r["id"]} for r in releases]),
        get_release=Mock(side_effect=lambda mbid, includes=None: next(r for r in releases if r["id"] == mbid)))
    return SimpleNamespace(mb_client=client, match_release=Mock(return_value=None),
                           match_artist=Mock(return_value={"mbid": artist_mbid} if artist_mbid else None))


def test_find_best_release_rejects_the_other_band():
    assert ac._find_best_release("Mammoth", "Mammoth", 14, _mb([MAMMOTH_MAMMOTH], artist_mbid=WVH)) is None


def test_find_best_release_keeps_a_release_matched_by_artist_id():
    rel = _release("u", _credit("宇多田ヒカル", "utada"), ["First Love"])
    mb = _mb([rel], artist_mbid="utada")
    assert ac._find_best_release("First Love", "Hikaru Utada", 1, mb)["id"] == "u"


def test_pinned_release_by_another_band_is_not_reused(monkeypatch):
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup", lambda a, ar: "c1daac15")
    recorded = []
    monkeypatch.setattr("core.metadata.album_mbid_cache.record", lambda *a: recorded.append(a) or True)
    assert ac._resolve_album_release("Mammoth", "Mammoth", 14, _mb([MAMMOTH_MAMMOTH])) is None
    assert recorded == []


def test_positional_match_needs_the_title():
    files = [{"path": f"/m/{i:02d}.mp3", "disc_number": 1, "track_number": i, "title": t}
             for i, t in enumerate(["Mr. Ed", "Another Celebration", "Epiphany", "Mammoth"], 1)]
    assert ac._match_files_to_tracklist(files, MAMMOTH_MAMMOTH) == {}
    right = ac._match_files_to_tracklist(files[:1], WVH_ALBUM)
    assert right["/m/01.mp3"]["id"] == "wvh-t1"


# ── per-track enrichment ──

class _Cfg:
    def get(self, key, default=None):
        return default


@pytest.fixture
def hermetic(monkeypatch):
    for name in ("mb_release_cache", "mb_release_detail_cache", "mb_artist_cache", "mb_artist_detail_cache"):
        monkeypatch.setattr(ms, name, type(getattr(ms, name))())
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup", lambda *a: None)
    monkeypatch.setattr("core.metadata.album_mbid_cache.record", lambda *a: True)


def _enrich(release, recording_artist):
    client = SimpleNamespace(
        get_release=Mock(return_value=release),
        get_recording=Mock(return_value={"isrcs": [], "artist-credit": recording_artist}),
        get_artist=Mock(return_value={}))
    service = SimpleNamespace(mb_client=client,
                              match_recording=Mock(return_value={"mbid": "right-recording"}),
                              match_artist=Mock(return_value={"mbid": WVH}),
                              match_release=Mock(return_value={"mbid": release["id"]}))
    runtime = SimpleNamespace(mb_worker=SimpleNamespace(mb_service=service))
    metadata = {"title": "Mr. Ed", "album": "Mammoth", "artist": "Mammoth", "album_artist": "Mammoth",
                "track_number": 1, "disc_number": 1}
    pp = ms._blank_post_process_state()
    ms._process_musicbrainz_source(pp, metadata, _Cfg(), runtime, "Mr. Ed", "Mammoth")
    return pp["id_tags"]


def test_enrichment_drops_a_release_by_another_band(hermetic):
    tags = _enrich(MAMMOTH_MAMMOTH, _credit("Mammoth Mammoth", MM))
    assert "MUSICBRAINZ_RELEASE_ID" not in tags
    assert "DATE" not in tags and "MUSICBRAINZ_RELEASETRACKID" not in tags
    assert tags["MUSICBRAINZ_RECORDING_ID"] == "right-recording"
    assert tags["MUSICBRAINZ_ARTIST_ID"] == WVH
    assert tags.get("ARTISTS") != ["Mammoth Mammoth"]


def test_enrichment_keeps_the_bands_own_release(hermetic):
    tags = _enrich(WVH_ALBUM, _credit("Mammoth WVH", WVH, entity_name="Mammoth"))
    assert tags["MUSICBRAINZ_RELEASE_ID"] == "wvh"
    assert tags["MUSICBRAINZ_RELEASETRACKID"] == "wvh-t1"
    assert tags["MUSICBRAINZ_RECORDING_ID"] == "wvh-r1"


def test_enrichment_skips_a_slot_holding_another_song(hermetic):
    """the band's own release, but another edition whose track 1 is a different
    song: the file keeps its own recording."""
    other_edition = _release("wvh-ed", _credit("Mammoth WVH", WVH, entity_name="Mammoth"), ["Intro", "Mr. Ed"])
    tags = _enrich(other_edition, _credit("Mammoth WVH", WVH))
    assert tags["MUSICBRAINZ_RELEASE_ID"] == "wvh-ed"
    assert "MUSICBRAINZ_RELEASETRACKID" not in tags
    assert tags["MUSICBRAINZ_RECORDING_ID"] == "right-recording"
