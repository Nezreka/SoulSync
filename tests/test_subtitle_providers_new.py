"""SubDL + SubSource providers: mapping, failures-as-misses, key gating.

No network — provider HTTP is stubbed at the instance level, same pattern as
the OpenSubtitles tests in test_subtitle_providers.py.
"""

from __future__ import annotations

import io
import zipfile

from core.video.subtitles.providers import get_providers
from core.video.subtitles.providers.base import SubtitleQuery
from core.video.subtitles.providers.subdl import (
    SubDLProvider,
    _as_dl_url,
    _candidates_from_entries,
    _extract_srt as subdl_extract,
    _search_params,
)
from core.video.subtitles.providers.subsource import (
    SubSourceProvider,
    _extract_srt as subsource_extract,
)


def _srt_zip(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    return buf.getvalue()


def _configured(provider, key="k"):
    assert provider.is_configured(lambda k, d=None: key if k.endswith("api_key") else d)


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_includes_subdl_and_subsource_with_whisper_last():
    providers = get_providers()
    assert set(providers) == {"opensubtitles", "subdl", "subsource", "embedded", "whisper"}
    assert list(providers).index("subdl") < list(providers).index("whisper")
    assert list(providers).index("subsource") < list(providers).index("whisper")
    assert list(providers)[-1] == "whisper"
    for pid in ("subdl", "subsource"):
        p = providers[pid]
        assert p.id == pid
        assert p.needs_key is True
        assert p.burns_network_quota is True
        assert p.background_download_only is False


# ── SubDL ────────────────────────────────────────────────────────────────────
def test_subdl_is_configured_needs_a_key():
    p = SubDLProvider()
    assert p.is_configured(lambda k, d=None: None) is False
    assert p.is_configured(lambda k, d=None: "  ") is False
    assert p.is_configured(lambda k, d=None: "secret") is True
    assert p._api_key == "secret"


def test_subdl_search_params_movie_and_episode():
    mp = _search_params({"imdb_id": "tt0133093"}, "en", "k")
    assert mp["imdb_id"] == "tt0133093" and mp["type"] == "movie"
    assert mp["languages"] == "EN"
    ep = _search_params({"tmdb_id": 1396, "season": 1, "episode": 2}, "es", "k")
    assert ep["type"] == "tv" and ep["season_number"] == 1
    assert ep["episode_number"] == 2 and ep["tmdb_id"] == 1396
    assert _search_params({}, "en", "k") is None
    assert _search_params({"season": 1}, "en", "k") is None  # no id


def test_subdl_search_before_is_configured_returns_empty():
    p = SubDLProvider()
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    assert p.search(q) == []


def test_subdl_search_maps_entries_to_candidates(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "en", "release_name": "The.Matrix.1999.1080p",
         "url": "/subtitle/1-1.zip", "hi": False, "download_count": 42},
        {"language": "fr", "release_name": "The.Matrix.1999.1080p",
         "url": "/subtitle/1-2.zip", "hi": False},
        {"language": "en", "release_name": "The.Matrix.HI",
         "url": "/subtitle/1-3.zip", "hi": True},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    cands = p.search(q)
    assert len(cands) == 1  # exact hi=False match preferred
    c = cands[0]
    assert (c.provider_id, c.language, c.hi, c.forced) == ("subdl", "en", False, False)
    assert c.title == "The.Matrix.1999.1080p"
    assert c.download_ref == "https://dl.subdl.com/subtitle/1-1.zip"
    assert c.download_count == 42


def test_subdl_search_degrades_to_inexact_flag_matches(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "en", "release_name": "HI.only", "url": "/s/9.zip", "hi": True},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en", hi=False)
    cands = p.search(q)
    assert len(cands) == 1 and cands[0].hi is True  # degraded, not a miss


def test_subdl_search_episode_uses_unpack_files(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "en", "release_name": "Show.S01.PACK",
         "url": "/subtitle/pack.zip", "hi": False,
         "unpack_files": [
             {"season": 1, "episode": 1, "language": "en",
              "release_name": "Show.S01E01.720p", "hi": False,
              "url": "https://dl.subdl.com/subtitle/7/70"},
             {"season": 1, "episode": 2, "language": "en",
              "release_name": "Show.S01E02.720p", "hi": False,
              "url": "https://dl.subdl.com/subtitle/7/71"},
         ]},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"tmdb_id": 1396, "season": 1, "episode": 2},
                      language="en")
    cands = p.search(q)
    assert len(cands) == 1
    assert cands[0].title == "Show.S01E02.720p"
    assert cands[0].download_ref == "https://dl.subdl.com/subtitle/7/71"


def test_subdl_search_episode_skips_pack_without_per_episode_files(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "en", "release_name": "Show.S01.PACK",
         "url": "/subtitle/pack.zip", "hi": False},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"tmdb_id": 1396, "season": 1, "episode": 2},
                      language="en")
    assert p.search(q) == []  # pack zip can't be attributed — never guess


def test_subdl_search_miss_on_http_error_and_bad_status(monkeypatch):
    p = SubDLProvider()
    _configured(p)

    def boom(params):
        raise OSError("down")
    monkeypatch.setattr(p, "_search_api", boom)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    assert p.search(q) == []

    monkeypatch.setattr(p, "_search_api",
                        lambda params: {"status": False, "error": "nope"})
    assert p.search(q) == []
    assert p.search(SubtitleQuery(identity={}, language="en")) == []


def test_subdl_as_dl_url_refuses_off_host():
    assert _as_dl_url("/subtitle/1-1.zip") == "https://dl.subdl.com/subtitle/1-1.zip"
    assert _as_dl_url("https://dl.subdl.com/subtitle/1-1.zip") == \
        "https://dl.subdl.com/subtitle/1-1.zip"
    assert _as_dl_url("http://dl.subdl.com/s.zip") == "https://dl.subdl.com/s.zip"
    assert _as_dl_url("https://evil.com/s.zip") is None
    assert _as_dl_url("") is None


def test_subdl_download_extracts_srt_from_zip(monkeypatch):
    from core.video.subtitles.providers.base import SubtitleCandidate
    p = SubDLProvider()
    _configured(p)
    monkeypatch.setattr(p, "_fetch_bytes", lambda url: _srt_zip("movie.srt"))
    c = SubtitleCandidate("subdl", "en", False, False, "t",
                          "https://dl.subdl.com/subtitle/1-1.zip")
    text = p.download(c)
    assert text is not None and "-->" in text and "Hello" in text


def test_subdl_download_before_is_configured_returns_none():
    from core.video.subtitles.providers.base import SubtitleCandidate
    p = SubDLProvider()
    c = SubtitleCandidate("subdl", "en", False, False, "t", "https://dl.subdl.com/x.zip")
    assert p.download(c) is None


def test_subdl_download_miss_on_http_error(monkeypatch):
    from core.video.subtitles.providers.base import SubtitleCandidate
    p = SubDLProvider()
    _configured(p)

    def boom(url):
        raise OSError("down")
    monkeypatch.setattr(p, "_fetch_bytes", boom)
    c = SubtitleCandidate("subdl", "en", False, False, "t", "https://dl.subdl.com/x.zip")
    assert p.download(c) is None


def test_subdl_extract_srt_handles_raw_text_and_bad_zip():
    assert "-->" in (subdl_extract(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n") or "")
    assert subdl_extract(b"PK\x03\x04bogus") is None
    assert subdl_extract(b"") is None
    # zip without any .srt
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "nope")
    assert subdl_extract(buf.getvalue()) is None


# ── SubSource ────────────────────────────────────────────────────────────────
def test_subsource_is_configured_needs_a_key():
    p = SubSourceProvider()
    assert p.is_configured(lambda k, d=None: None) is False
    assert p.is_configured(lambda k, d=None: "abc") is True
    assert p._api_key == "abc"


def test_subsource_search_before_is_configured_returns_empty():
    p = SubSourceProvider()
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    assert p.search(q) == []


def test_subsource_search_resolves_title_then_lists(monkeypatch):
    p = SubSourceProvider()
    _configured(p)
    calls = []

    def fake_get(path, params):
        calls.append((path, params))
        if path == "/movies/search":
            assert params["searchType"] == "imdb" and params["imdb"] == "tt0133093"
            return {"data": [{"movieId": 77, "title": "The Matrix", "imdbId": "tt0133093"}]}
        if path == "/subtitles":
            # the live api wants the english name, not the code (oct 14 check:
            # language=en returned nothing at all)
            assert params["movieId"] == 77 and params["language"] == "english"
            return {"data": [
                {"subtitleId": 5, "language": "english", "releaseInfo": ["The.Matrix.1999.1080p"],
                 "hearingImpaired": False, "foreignParts": False, "downloads": 11,
                 "preview": "1\n00:00:01,000 --> 00:00:02,000\nhi"},
                {"subtitleId": 6, "language": "french", "releaseInfo": ["x"],
                 "hearingImpaired": False},
            ], "pagination": {"page": 1, "pages": 1}}
        raise AssertionError("unexpected " + path)
    monkeypatch.setattr(p, "_api_get", fake_get)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    cands = p.search(q)
    assert len(cands) == 1
    c = cands[0]
    assert (c.provider_id, c.language, c.download_ref) == ("subsource", "en", 5)
    assert c.title == "The.Matrix.1999.1080p" and c.download_count == 11


def test_subsource_search_prefers_exact_imdb_title_match(monkeypatch):
    p = SubSourceProvider()
    _configured(p)

    def fake_get(path, params):
        if path == "/movies/search":
            return {"data": [
                {"movieId": 1, "title": "The Matrix Reloaded", "imdbId": "tt0234215"},
                {"movieId": 2, "title": "The Matrix", "imdbId": "tt0133093"},
            ]}
        return {"data": []}
    monkeypatch.setattr(p, "_api_get", fake_get)
    seen = {}

    def fake_list(movie_id, query):
        seen["movie_id"] = movie_id
        return []
    monkeypatch.setattr(p, "_list_subtitles", fake_list)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    assert p.search(q) == []
    assert seen["movie_id"] == 2


def test_subsource_search_episode_filters_season_episode(monkeypatch):
    p = SubSourceProvider()
    _configured(p)

    def fake_get(path, params):
        if path == "/movies/search":
            return {"data": [{"movieId": 9, "title": "Show", "imdbId": "tt0903747"}]}
        return {"data": [
            {"id": 1, "language": "en", "releaseName": "Show.S01E01", "season": 1, "episode": 1},
            {"id": 2, "language": "en", "releaseName": "Show.S01E02", "season": 1, "episode": 2},
            {"id": 3, "language": "en", "releaseName": "Show.S02E01", "season": 2, "episode": 1},
        ]}
    monkeypatch.setattr(p, "_api_get", fake_get)
    q = SubtitleQuery(identity={"imdb_id": "tt0903747", "season": 1, "episode": 2},
                      language="en")
    cands = p.search(q)
    assert [c.download_ref for c in cands] == [2]


def test_subsource_search_episode_skips_unattributable(monkeypatch):
    """Fail closed: the API is queried by show, not episode, so an entry whose
    season/episode can't be determined is never attributed to an episode."""
    p = SubSourceProvider()
    _configured(p)

    def fake_get(path, params):
        if path == "/movies/search":
            return {"data": [{"movieId": 9, "title": "Show", "imdbId": "tt0903747"}]}
        return {"data": [
            {"id": 1, "language": "en", "releaseName": "Show.S01E02", "season": 1, "episode": 2},
            {"id": 2, "language": "en", "releaseName": "Show - some release"},
            {"id": 3, "language": "en", "releaseName": "Show.S01E02.HDTV"},
        ]}
    monkeypatch.setattr(p, "_api_get", fake_get)
    q = SubtitleQuery(identity={"imdb_id": "tt0903747", "season": 1, "episode": 2},
                      language="en")
    cands = p.search(q)
    assert [c.download_ref for c in cands] == [1, 3]


def test_subsource_language_names_map_to_codes():
    """Full language names map via the shared name→code table — naive [:2]
    truncation would produce 'po'/'du'/'ch' and drop these subtitles."""
    from core.video.subtitles.providers.subsource import _sub_lang
    assert _sub_lang({"language": "portuguese"}) == "pt"
    assert _sub_lang({"language": "dutch"}) == "nl"
    assert _sub_lang({"language": "chinese"}) == "zh"
    assert _sub_lang({"language": "en"}) == "en"
    assert _sub_lang({"language": ""}) == ""


def test_subsource_search_miss_paths(monkeypatch):
    p = SubSourceProvider()
    _configured(p)

    def boom(path, params):
        raise OSError("down")
    monkeypatch.setattr(p, "_api_get", boom)
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en")
    assert p.search(q) == []

    # title resolves to nothing
    monkeypatch.setattr(p, "_api_get", lambda path, params: {"data": []})
    assert p.search(q) == []
    # no imdb id at all
    assert p.search(SubtitleQuery(identity={"tmdb_id": 1}, language="en")) == []


def test_subsource_download_extracts_srt_from_zip(monkeypatch):
    from core.video.subtitles.providers.base import SubtitleCandidate
    p = SubSourceProvider()
    _configured(p)
    monkeypatch.setattr(p, "_download_bytes", lambda sid: _srt_zip("sub.srt"))
    c = SubtitleCandidate("subsource", "en", False, False, "t", 42)
    text = p.download(c)
    assert text is not None and "-->" in text


def test_subsource_download_miss_paths(monkeypatch):
    from core.video.subtitles.providers.base import SubtitleCandidate
    p = SubSourceProvider()
    c = SubtitleCandidate("subsource", "en", False, False, "t", 42)
    assert p.download(c) is None  # not configured

    _configured(p)

    def boom(sid):
        raise OSError("down")
    monkeypatch.setattr(p, "_download_bytes", boom)
    assert p.download(c) is None
    assert subsource_extract(b"") is None


# ── review fixes: flag filter / zip picker / season 0 ─────────────────────

def test_flag_filter_pref_bypasses_prune():
    """With a HI/forced preference set and no explicit flag requirement, the
    candidate list stays unpruned — otherwise every HI candidate is dropped
    whenever a plain one exists and the scoring tiebreak never sees it."""
    from core.video.subtitles.providers.base import (
        SubtitleCandidate, SubtitleQuery, flag_filter)
    plain = SubtitleCandidate("p", "en", False, False, "plain", "r1")
    hi = SubtitleCandidate("p", "en", True, False, "hi", "r2")
    q = SubtitleQuery(language="en", hi_forced_pref="prefer_hi")
    assert flag_filter([plain, hi], q) == [plain, hi]
    # pref off → exact-flag prune as before
    q2 = SubtitleQuery(language="en")
    assert flag_filter([plain, hi], q2) == [plain]
    # explicit requirement still filters even with a pref set
    q3 = SubtitleQuery(language="en", hi=True, hi_forced_pref="prefer_hi")
    assert flag_filter([plain, hi], q3) == [hi]


def test_pick_zip_member_skips_macos_shadows():
    from core.video.subtitles.providers.subdl import _pick_zip_member as pick_dl
    from core.video.subtitles.providers.subsource import _pick_zip_member as pick_ss
    names = ["__MACOSX/._movie.srt", "movie.srt"]
    assert pick_dl(names) == "movie.srt"
    assert pick_ss(names) == "movie.srt"
    assert pick_dl(["__MACOSX/._movie.srt"]) is None
    assert pick_ss([".hidden.srt", "real.srt"]) == "real.srt"


def test_subsource_episode_season_zero():
    """Season 0 (Specials) is falsy — it must still resolve."""
    from core.video.subtitles.providers.subsource import _sub_episode
    assert _sub_episode({"season": 0, "episode": 3}) == (0, 3)
    assert _sub_episode({"season": 0, "episodeNumber": 5}) == (0, 5)
    assert _sub_episode({"season": 2, "episode": 1}) == (2, 1)


# ── review fixes (oct 14) ────────────────────────────────────────────────────
def test_subdl_episode_accepts_plain_single_episode_entries(monkeypatch):
    # the real api answers an episode query with per-episode entries (no
    # unpack_files); dropping them meant subdl never found an episode sub.
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "EN", "release_name": "Show.S01E02.1080p.WEB",
         "url": "/subtitle/1-2.zip", "hi": False, "season": 1, "episode": 2,
         "episode_from": None, "episode_end": 0, "full_season": False},
        {"language": "EN", "release_name": "Show.S01E03.1080p.WEB",
         "url": "/subtitle/1-3.zip", "hi": False, "season": 1, "episode": 3,
         "full_season": False},
        {"language": "EN", "release_name": "Show.S01E02.AI",
         "url": "/subtitle/1-9.zip", "season": 1, "episode": 2,
         "ai_translated": True},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"tmdb_id": 1396, "season": 1, "episode": 2},
                      language="en")
    cands = p.search(q)
    assert [c.download_ref for c in cands] == ["https://dl.subdl.com/subtitle/1-2.zip"]


def test_subdl_episode_falls_back_to_release_tag_and_skips_full_season(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        # season/episode sent as 0: the tag places it
        {"language": "EN", "release_name": "Show.S01E02.720p",
         "url": "/subtitle/a.zip", "season": 0, "episode": 0},
        # full-season pack without unpack files: never guessed
        {"language": "EN", "release_name": "Show.S01E02-E10",
         "url": "/subtitle/b.zip", "season": 1, "full_season": True},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"tmdb_id": 1396, "season": 1, "episode": 2},
                      language="en")
    assert [c.download_ref for c in p.search(q)] == ["https://dl.subdl.com/subtitle/a.zip"]


def test_subdl_unpack_file_with_season_zero_uses_the_pack_season(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"language": "en", "release_name": "Show.S01.PACK", "season": 1,
         "full_season": True, "url": "/subtitle/pack.zip",
         "unpack_files": [
             {"season": 0, "episode": 2, "name": "Show.S01E02.srt",
              "url": "https://dl.subdl.com/subtitle/7/71"},
         ]},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    q = SubtitleQuery(identity={"tmdb_id": 1396, "season": 1, "episode": 2},
                      language="en")
    assert [c.download_ref for c in p.search(q)] == ["https://dl.subdl.com/subtitle/7/71"]


def test_opensubtitles_hash_rides_the_id_query_and_reads_moviehash_match(monkeypatch):
    # a moviehash query returns every sub for the feature; only rows the api
    # flags with moviehash_match were timed for this exact file.
    from core.video.subtitles.providers.opensubtitles import OpenSubtitlesProvider
    p = OpenSubtitlesProvider()
    _configured(p)
    calls = []
    found = {"data": [
        {"attributes": {"language": "en", "release": "Movie.2019.WEB", "download_count": 900,
                        "moviehash_match": False, "files": [{"file_id": 1}]}},
        {"attributes": {"language": "en", "release": "Movie.2019.BluRay", "download_count": 5,
                        "moviehash_match": True, "files": [{"file_id": 2}]}},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: calls.append(dict(params)) or found)
    cands = p.search(SubtitleQuery(identity={"imdb_id": "tt0137523"}, language="en",
                                   moviehash="8e245d9679d31e12"))
    assert len(calls) == 1
    assert calls[0]["moviehash"] == "8e245d9679d31e12"
    assert calls[0]["imdb_id"] == "0137523"
    assert calls[0]["ai_translated"] == "exclude"
    assert {c.download_ref: c.hash_match for c in cands} == {1: False, 2: True}


def test_opensubtitles_hash_only_search_when_unidentified(monkeypatch):
    from core.video.subtitles.providers.opensubtitles import OpenSubtitlesProvider
    p = OpenSubtitlesProvider()
    _configured(p)
    calls = []
    monkeypatch.setattr(p, "_search_api", lambda params: calls.append(dict(params)) or {"data": []})
    p.search(SubtitleQuery(identity={}, language="en", moviehash="abc"))
    assert calls and calls[0]["moviehash"] == "abc" and "imdb_id" not in calls[0]


def test_opensubtitles_episode_with_only_show_imdb_uses_parent_imdb_id():
    # episodes carry the SHOW's ids — a bare imdb_id would search the show
    # feature itself and come back with random episodes.
    from core.video.subtitles.providers.opensubtitles import search_params
    params = search_params({"imdb_id": "tt0903747", "season": 1, "episode": 2}, "en")
    assert params == {"languages": "en", "parent_imdb_id": "0903747",
                      "season_number": 1, "episode_number": 2}
    no_ep = search_params({"tmdb_id": 1396, "season": 1, "episode": None}, "en")
    assert "episode_number" not in no_ep


def test_flag_filter_never_degrades_across_the_forced_axis():
    from core.video.subtitles.providers.base import SubtitleCandidate, flag_filter

    def c(hi, forced):
        return SubtitleCandidate("x", "en", hi, forced, "t", (hi, forced))

    forced_only = [c(False, True)]
    assert flag_filter(forced_only, SubtitleQuery(language="en")) == []
    assert flag_filter(forced_only, SubtitleQuery(language="en",
                                                  hi_forced_pref="prefer_forced")) == []
    full_only = [c(False, False)]
    assert flag_filter(full_only, SubtitleQuery(language="en", forced=True)) == []
    # hi still degrades
    hi_only = [c(True, False)]
    assert flag_filter(hi_only, SubtitleQuery(language="en")) == hi_only


# ── shapes from the live apis (oct 14, with real keys) ───────────────────────
def test_subdl_never_sends_the_hi_param():
    # hi=1 made subdl mark every entry hearing-impaired (live: all true with
    # it, all false without), so the flag filter couldn't tell them apart
    params = _search_params({"imdb_id": "tt0137523"}, "en", "k")
    assert "hi" not in params


def test_subdl_movie_uses_per_file_links_and_skips_commentary(monkeypatch):
    p = SubDLProvider()
    _configured(p)
    payload = {"status": True, "subtitles": [
        {"release_name": "Fight.Club.1999.10th.Anniversary.1080p.Remux", "language": "EN",
         "hi": False, "season": 0, "episode": None, "full_season": False,
         "url": "/subtitle/2918869-2909902.zip",
         "unpack_files": [
             # live data had both an SDH and a plain commentary track
             {"name": "Fight Club (1999)_commentary1.srt", "release_name": "Fight Club (1999)_commentary1",
              "season": 0, "episode": 0, "language": "EN", "hi": False, "format": "srt",
              "url": "/subtitle/s26/c1"},
             {"name": "Fight Club (1999).srt", "release_name": "Fight Club (1999)",
              "season": 0, "episode": 0, "language": "EN", "hi": False, "format": "srt",
              "url": "/subtitle/s26/main"},
             {"name": "Fight Club (1999).sub", "release_name": "Fight Club (1999)",
              "season": 0, "episode": 0, "language": "EN", "hi": False, "format": "sub",
              "url": "/subtitle/s26/microdvd"},
         ]},
        # no file list and not a zip: a microdvd file that would never extract
        {"release_name": "Fight Club", "language": "EN", "hi": False,
         "url": "/subtitle/487037-1773.sub"},
        # no file list, a zip: the whole entry is the candidate
        {"release_name": "Fight.Club.1999.DVDRip", "language": "EN", "hi": False,
         "url": "/subtitle/1-2.zip"},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: payload)
    cands = p.search(SubtitleQuery(identity={"imdb_id": "tt0137523"}, language="en"))
    assert [c.download_ref for c in cands] == ["https://dl.subdl.com/subtitle/s26/main",
                                               "https://dl.subdl.com/subtitle/1-2.zip"]


def test_subdl_regional_language_codes():
    from core.video.subtitles.providers.subdl import _norm_lang
    assert _norm_lang("BR_PT") == "pt-br"
    assert _norm_lang("EN") == "en"
    assert _norm_lang("ZH_TW") == "zh-tw"


def test_subsource_reads_release_info_foreign_parts_and_skips_non_srt(monkeypatch):
    p = SubSourceProvider()
    _configured(p)

    def fake_get(path, params):
        if path == "/movies/search":
            return {"data": [{"movieId": 36835, "imdbId": "tt0137523"}]}
        return {"data": [
            {"subtitleId": 1, "language": "english", "releaseInfo": ["Fight.Club.1999.1080p.BluRay"],
             "hearingImpaired": False, "foreignParts": False, "downloads": 9,
             "preview": "1\n00:02:00,300 --> 00:02:03,200\nPeople always ask me"},
            {"subtitleId": 2, "language": "english", "releaseInfo": ["funkar"],
             "hearingImpaired": False, "foreignParts": False,
             # subviewer: no '-->' timings, would fail after burning a download
             "preview": "[INFORMATION]\n00:02:28.70,00:02:30.56\nNarrator"},
            {"subtitleId": 3, "language": "english", "releaseInfo": ["Fight.Club.Forced"],
             "hearingImpaired": False, "foreignParts": True,
             "preview": "1\n00:10:00,000 --> 00:10:02,000\n(in french)"},
        ], "pagination": {"page": 1, "pages": 1}}
    monkeypatch.setattr(p, "_api_get", fake_get)
    full = p.search(SubtitleQuery(identity={"imdb_id": "tt0137523"}, language="en"))
    assert [(c.download_ref, c.title, c.download_count) for c in full] == \
        [(1, "Fight.Club.1999.1080p.BluRay", 9)]
    forced = p.search(SubtitleQuery(identity={"imdb_id": "tt0137523"}, language="en", forced=True))
    assert [c.download_ref for c in forced] == [3] and forced[0].forced


def test_subsource_language_names_and_pages(monkeypatch):
    from core.video.subtitles.providers.subsource import _language_name
    assert [_language_name(c) for c in ("en", "pt-br", "fa", "zh-tw", "de", "xx")] == \
        ["english", "brazilian_portuguese", "farsi_persian", "big_5_code", "german", None]
    p = SubSourceProvider()
    _configured(p)
    pages = []

    def fake_get(path, params):
        if path == "/movies/search":
            return {"data": [{"movieId": 1, "imdbId": "tt1"}]}
        pages.append(params["page"])
        return {"data": [{"subtitleId": params["page"], "language": "brazilian_portuguese",
                          "releaseInfo": ["Show.S01E02.WEB"]}],
                "pagination": {"page": params["page"], "pages": 2}}
    monkeypatch.setattr(p, "_api_get", fake_get)
    cands = p.search(SubtitleQuery(identity={"imdb_id": "tt1", "season": 1, "episode": 2},
                                   language="pt-br"))
    assert pages == [1, 2] and [c.download_ref for c in cands] == [1, 2]
    # a language subsource doesn't carry is never a search
    pages.clear()
    assert p.search(SubtitleQuery(identity={"imdb_id": "tt1"}, language="xx")) == []
    assert pages == []


def test_subsource_never_attributes_an_episode_range():
    from core.video.subtitles.providers.subsource import _sub_episode
    assert _sub_episode({"releaseInfo": ["Breaking.Bad.S01E02.480p"]}) == (1, 2)
    assert _sub_episode({"releaseInfo": ["Show.S01E01-7.DSR-HDTV"]})[1] is None
    assert _sub_episode({"releaseInfo": ["Show.S01E01E02.720p"]})[1] is None


def test_archive_extracts_zip_skips_commentary_and_handles_rar(monkeypatch, tmp_path):
    import io as _io
    import subprocess as _sp
    import zipfile as _zf
    from core.video.subtitles import archive
    buf = _io.BytesIO()
    with _zf.ZipFile(buf, "w") as zf:
        zf.writestr("__MACOSX/._Movie.srt", "junk")
        zf.writestr("Movie_commentary1.srt", "1\n00:00:01,000 --> 00:00:02,000\ncommentary\n")
        zf.writestr("Movie.srt", "1\n00:00:01,000 --> 00:00:02,000\nthe film\n")
    assert "the film" in archive.extract_srt(buf.getvalue())
    # RAR goes through bsdtar (real subsource uploads are RAR)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = cmd[cmd.index("-C") + 1]
        with open(out + "/Fight.Club.srt", "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:02,000\nPeople always ask me\n")
        return _sp.CompletedProcess(cmd, 0, b"", b"")
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/bsdtar" if name == "bsdtar" else None)
    monkeypatch.setattr(archive.subprocess, "run", fake_run)
    assert "People always ask me" in archive.extract_srt(b"Rar!\x1a\x07\x00" + b"x" * 100)
    assert calls and calls[0][:2] == ["/usr/bin/bsdtar", "-xf"]
    # no bsdtar: a miss, never a crash
    monkeypatch.setattr(archive.shutil, "which", lambda name: None)
    assert archive.extract_srt(b"Rar!\x1a\x07\x00" + b"x" * 100) is None
