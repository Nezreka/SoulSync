"""#1398: iTunes search came back with the wrong songs.

reporter searched "Princess Of Egypt" by E-Type and "Ti Ti" by Helena
Paparizou. neither is in the US iTunes store (the default), so the US search
returned other songs and SoulSync never looked anywhere else. both are in the
GB/DE/SE/GR stores. searches now try a few other stores when the home store has
nothing close.
"""

from core.itunes_client import iTunesClient
from core.metadata.relevance import is_strong_match
from core.metadata.types import Track


def _t(name, artist, tid):
    return Track(id=tid, name=name, artists=[artist], album="A", duration_ms=200000)


class _Stores(iTunesClient):
    """iTunesClient with the network swapped for a per-store catalogue."""

    def __init__(self, catalogue, home="US"):
        super().__init__(country=home)
        self.catalogue = catalogue
        self.asked = []

    def search_tracks(self, query, limit=20, country=None):
        store = (country or self.country).upper()
        self.asked.append(store)
        return list(self.catalogue.get(store, []))


def test_strong_match_rules():
    assert is_strong_match(_t("Princess Of Egypt", "E-Type", "1"), "Princess Of Egypt", "E-type")
    assert is_strong_match(_t("Ti Ti", "Helena Paparizou & Antique", "2"), "Ti Ti", "Helena Paparizou")
    assert not is_strong_match(_t("Princess of Egypt", "TH3 ONE", "3"), "Princess Of Egypt", "E-type")
    assert not is_strong_match(_t("Stin Korifi Tou Kosmou", "Helena Paparizou", "4"), "Ti Ti", "Helena Paparizou")
    assert is_strong_match(_t("Ti Ti", "Anyone", "5"), "Ti Ti", "")
    assert not is_strong_match(_t("Ti Ti", "Anyone", "5"), "", "")


def test_missing_from_home_store_is_found_in_another():
    """the exact report: US has only the wrong songs, GB has the real one."""
    wrong = [_t("Princess of Egypt", "TH3 ONE", "th3"), _t("Prince of Egypt", "YOUNGSIMT2R", "ys")]
    real = _t("Princess Of Egypt", "E-Type", "etype")
    client = _Stores({"US": wrong, "GB": [real]})
    out = client.search_tracks_any_store("Princess Of Egypt E-type", 20, "Princess Of Egypt", "E-type")
    assert out[0].id == "etype"
    assert [t.id for t in out[1:]] == ["th3", "ys"]  # home results still there, after
    assert client.asked == ["US", "GB"]  # stops at the first store that has it


def test_home_store_hit_costs_no_extra_calls():
    real = _t("Ti Ti", "Helena Paparizou", "tt")
    client = _Stores({"US": [real], "GB": [real]})
    out = client.search_tracks_any_store("Ti Ti Helena Paparizou", 20, "Ti Ti", "Helena Paparizou")
    assert [t.id for t in out] == ["tt"]
    assert client.asked == ["US"]


def test_fallback_is_capped_and_skips_the_home_store():
    client = _Stores({}, home="GB")
    out = client.search_tracks_any_store("nothing anywhere", 20, "Nothing", "Nobody")
    assert out == []
    assert client.asked[0] == "GB"
    assert "GB" not in client.asked[1:]
    assert len(client.asked) == 1 + iTunesClient.SEARCH_FALLBACK_LIMIT


def test_no_expected_title_means_no_fallback():
    client = _Stores({"US": [_t("x", "y", "1")]})
    client.search_tracks_any_store("free text", 20)
    assert client.asked == ["US"]


def test_other_stores_are_cached_under_their_own_key(monkeypatch):
    """a GB search must not come back out of the cache as the US result."""
    import core.itunes_client as mod

    seen = []

    class _Cache:
        def get_search_results(self, source, kind, query, limit):
            seen.append(query)
            return None

        def store_entities_bulk(self, *a, **k):
            pass

        def store_search_results(self, *a, **k):
            pass

    monkeypatch.setattr(mod, "get_metadata_cache", lambda: _Cache())
    client = iTunesClient(country="US")
    monkeypatch.setattr(client, "_search", lambda *a, **k: [])
    client.search_tracks("Ti Ti", 20)
    client.search_tracks("Ti Ti", 20, country="gb")
    assert seen == ["Ti Ti", "Ti Ti@GB"]
