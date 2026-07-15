"""MB artist search same-name dedup must keep the FAMOUS artist (#1036).

Every artist sharing the searched name ties at score 100 on an exact match,
and MusicBrainz's ordering among ties is arbitrary — searching "Korn"
surfaced a Thai pop duo as the ONLY artist card and silently dropped the
metal band. The dedup now tie-breaks by community tag weight (the artist
people actually search for has hundreds of tag votes; namesakes have none).
"""

from __future__ import annotations

from core.musicbrainz_search import MusicBrainzSearchClient


def _client_with(raw):
    c = MusicBrainzSearchClient.__new__(MusicBrainzSearchClient)

    class _Fake:
        def search_artist(self, query, limit=10, strict=False):
            return raw

    c._client = _Fake()
    return c


_THAI_DUO = {
    "id": "mbid-duo", "name": "Korn", "score": 100,
    "tags": [],                                             # nobody tags them
}
_METAL_BAND = {
    "id": "mbid-band", "name": "Korn", "score": 100,
    "tags": [{"name": "nu metal", "count": 312}, {"name": "metal", "count": 150}],
}


def test_equal_scores_tie_break_on_tag_weight():
    # MB lists the obscure namesake FIRST — the famous band must still win.
    c = _client_with([_THAI_DUO, _METAL_BAND])
    artists = c.search_artists("korn", limit=5)
    assert len(artists) == 1
    assert artists[0].id == "mbid-band"
    assert artists[0].genres[:1] == ["nu metal"]


def test_higher_score_still_beats_tag_weight():
    # Score stays the primary key: a better textual match wins even untagged.
    better_match = {"id": "mbid-exact", "name": "Korn", "score": 100, "tags": []}
    worse_match = {"id": "mbid-fuzzy", "name": "Korn", "score": 90,
                   "tags": [{"name": "rock", "count": 500}]}
    c = _client_with([worse_match, better_match])
    artists = c.search_artists("korn", limit=5)
    assert artists[0].id == "mbid-exact"


def test_distinct_names_all_survive_sorted_by_score_then_weight():
    other = {"id": "mbid-koRn-tribute", "name": "Korn Again", "score": 85,
             "tags": [{"name": "tribute", "count": 3}]}
    c = _client_with([_THAI_DUO, _METAL_BAND, other])
    artists = c.search_artists("korn", limit=5)
    assert [a.id for a in artists] == ["mbid-band", "mbid-koRn-tribute"]


def test_score_floor_still_applies():
    junk = {"id": "mbid-junk", "name": "Kornelius", "score": 60, "tags": []}
    c = _client_with([junk])
    assert c.search_artists("korn", limit=5) == []


# ── the other half of #1036: the CARD IMAGE, resolved by exact relation ──────
# The card's identity was right (correct MBID, correct discography on click)
# but its photo came from a by-name fallback that took the first source's top
# hit — the Thai duo again. MB url relations carry the artist's EXACT
# Deezer/Spotify/Apple ids; the image resolves through those first now.

import core.metadata.artist_image as artist_image_mod


def _fake_mb_client(monkeypatch, relations, calls):
    import core.musicbrainz_client as mbc

    class _FakeMB:
        def __init__(self, *a, **k):
            pass

        def get_artist(self, mbid, includes=None):
            calls.append(mbid)
            return {"relations": relations}

    monkeypatch.setattr(mbc, "MusicBrainzClient", _FakeMB)
    artist_image_mod._MB_RELATION_IMAGE_CACHE.clear()


def test_mb_image_resolves_via_deezer_relation_not_name(monkeypatch):
    calls = []
    _fake_mb_client(monkeypatch, [
        {"type": "streaming", "url": {"resource": "https://www.deezer.com/artist/1235"}},
    ], calls)
    seen = {}

    def fake_source_image(source, artist_id):
        seen["asked"] = (source, artist_id)
        return "https://dzcdn/real-band.jpg"

    monkeypatch.setattr(artist_image_mod, "_get_artist_image_from_source", fake_source_image)
    monkeypatch.setattr(artist_image_mod, "_lookup_artist_image_by_name",
                        lambda name: (_ for _ in ()).throw(AssertionError("name fallback must not run")))

    url = artist_image_mod.get_artist_image_url(
        "ac865b2e-mbid", source_override="musicbrainz", artist_name="Korn")
    assert url == "https://dzcdn/real-band.jpg"
    assert seen["asked"] == ("deezer", "1235")


def test_mb_image_relation_lookup_is_cached(monkeypatch):
    calls = []
    _fake_mb_client(monkeypatch, [
        {"url": {"resource": "https://open.spotify.com/artist/3RNrq3jvMZxD9ZyoOZbQOD"}},
    ], calls)
    monkeypatch.setattr(artist_image_mod, "_get_artist_image_from_source",
                        lambda s, i: "https://sp/img.jpg")

    assert artist_image_mod._image_from_musicbrainz_relations("mbid-x") == "https://sp/img.jpg"
    assert artist_image_mod._image_from_musicbrainz_relations("mbid-x") == "https://sp/img.jpg"
    assert calls == ["mbid-x"]                   # second hit served from cache


def test_mb_image_falls_back_to_name_when_no_relations(monkeypatch):
    calls = []
    _fake_mb_client(monkeypatch, [], calls)
    monkeypatch.setattr(artist_image_mod, "_lookup_artist_image_by_name",
                        lambda name: "https://fallback/by-name.jpg")

    url = artist_image_mod.get_artist_image_url(
        "mbid-norel", source_override="musicbrainz", artist_name="Obscure Act")
    assert url == "https://fallback/by-name.jpg"
