"""playlist discovery couldn't find "Numb" by Linkin Park, and the fix popup
didn't list it either.

deezer's free-text search for "Numb Linkin Park" returns 50 results (live
cuts, covers, tribute albums) and never the studio Numb off Meteora. the
field-scoped form (Linkin Park track:"Numb") returns it first. and the
rerank then put tribute acts' plain "Numb" above Linkin Park's own live
cuts, because the title matched and the wrong artist barely cost anything.
"""

from unittest.mock import MagicMock

from core.deezer_client import DeezerClient
from core.metadata.relevance import rerank_tracks
from core.metadata.song_search import search_song
from core.metadata.types import Track


def _t(tid, name, artist, album="A"):
    return Track(id=tid, name=name, artists=[artist], album=album, duration_ms=187000)


# what deezer's free-text search really returned (trimmed)
FREE_TEXT = [
    _t("live1", "Numb (Live)", "Linkin Park", "Live in Texas"),
    _t("live2", "Numb (One More Light Live)", "Linkin Park", "One More Light Live"),
    _t("remix", "Numb (Linkin Park Remix)", "SYMPHONICAL", "Numb (Linkin Park Remix)"),
    _t("cover", "Numb (Linkin Park Cover)", "LiL BO WEEP", "SOLOS 2"),
    _t("seeko", "Numb", "Seeko", "Numb"),
    _t("piano", "Numb", "Piano Project", "Piano Renditions of Linkin Park"),
    _t("lucas", "Numb", "Lucas King", "Linkin Park Symphony"),
    _t("babies", "Numb", "Sweet Little Band", "Babies Go Linkin Park"),
]
STUDIO = _t("studio", "Numb", "Linkin Park", "Meteora")


def test_rerank_puts_the_right_artist_above_tributes():
    ranked = [t.id for t in rerank_tracks(FREE_TEXT, expected_title="Numb", expected_artist="Linkin Park")]
    assert ranked[:2] == ["live1", "live2"], ranked
    # tributes naming linkin park sink to the very bottom
    assert set(ranked[-5:]) == {"remix", "cover", "piano", "lucas", "babies"}, ranked


def test_a_different_song_by_the_right_artist_ranks_below_the_song():
    """live, deezer free-text only: "Good Goodbye" ranked above "Numb (Live)"."""
    other = _t("gg", "Good Goodbye (feat. Pusha T and Stormzy)", "Linkin Park", "One More Light")
    ranked = rerank_tracks([other] + FREE_TEXT[:2], expected_title="Numb", expected_artist="Linkin Park")
    assert [t.id for t in ranked][:2] == ["live1", "live2"]


def test_rerank_puts_the_studio_cut_first_when_it_is_there():
    ranked = rerank_tracks(FREE_TEXT + [STUDIO], expected_title="Numb", expected_artist="Linkin Park")
    assert ranked[0].id == "studio"


def test_a_real_song_called_lullaby_is_not_treated_as_a_tribute():
    cure = _t("cure", "Lullaby", "The Cure", "Disintegration")
    other = _t("x", "Lullaby (Live)", "The Cure", "Show")
    assert rerank_tracks([other, cure], expected_title="Lullaby", expected_artist="The Cure")[0].id == "cure"


def test_deezer_song_search_adds_the_scoped_results_first():
    client = MagicMock(spec=DeezerClient)

    def fake_search(query="", limit=20, *, track=None, artist=None, album=None):
        return [STUDIO, FREE_TEXT[0]] if track else list(FREE_TEXT)

    client.search_tracks.side_effect = fake_search
    out = search_song(client, "Numb", "Linkin Park", limit=50)
    assert out[0].id == "studio"
    assert [t.id for t in out].count("live1") == 1  # merged, no duplicates
    assert {t.id for t in FREE_TEXT} <= {t.id for t in out}  # nothing the old search found is lost


def test_scoped_search_failing_still_returns_free_text():
    client = MagicMock(spec=DeezerClient)

    def fake_search(query="", limit=20, *, track=None, artist=None, album=None):
        if track:
            raise RuntimeError("deezer hiccup")
        return list(FREE_TEXT)

    client.search_tracks.side_effect = fake_search
    assert [t.id for t in search_song(client, "Numb", "Linkin Park")] == [t.id for t in FREE_TEXT]


def test_other_sources_keep_free_text():
    client = MagicMock()
    client.search_tracks.return_value = [STUDIO]
    assert search_song(client, "Numb", "Linkin Park", limit=7) == [STUDIO]
    client.search_tracks.assert_called_once_with("Numb Linkin Park", limit=7)


def test_deezer_primary_discovery_finds_studio_numb():
    """the reported run, end to end through the real discovery worker: deezer
    primary, the free-text searches only return live cuts (scored too low),
    so the last-chance search has to be the field-scoped one."""
    from tests.discovery.test_discovery_playlist import _build_deps, _playlist, _track

    import core.discovery.playlist as dp

    class _Deezer(DeezerClient):
        def __init__(self):  # no network, no config
            self.calls = []

        def search_tracks(self, query="", limit=20, *, track=None, artist=None, album=None):
            self.calls.append("scoped" if track else "free")
            return [STUDIO] if track else [FREE_TEXT[0], FREE_TEXT[1]]

    deezer = _Deezer()

    def score(title, artist, duration_ms, results):
        ids = [t.id for t in results]
        if "studio" in ids:
            return STUDIO, 0.97, ids.index("studio")
        return FREE_TEXT[0], 0.55, 0  # live cut: under the 0.7 bar

    deps = _build_deps(
        tracks_by_playlist={"p1": [_track(track_id=1, name="Numb", artist="Linkin Park")]},
        spotify_auth=False,
        discovery_source="deezer",
        fallback_source="deezer",
    )
    deps.get_metadata_fallback_client = lambda: deezer
    deps.discovery_score_candidates = score

    dp.run_playlist_discovery_worker([_playlist("p1")], deps=deps)

    assert "scoped" in deezer.calls
    _, extra = deps._db.extra_data_writes[0]
    assert extra["discovered"] is True and extra.get("wing_it_fallback") is not True, extra
    assert extra["matched_data"]["album"] in ("Meteora", {"name": "Meteora"}) or \
        "Meteora" in str(extra["matched_data"].get("album")), extra["matched_data"]
