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
