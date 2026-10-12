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
            assert params["movieId"] == 77 and params["language"] == "en"
            return {"data": [
                {"id": 5, "language": "en", "releaseName": "The.Matrix.1999.1080p",
                 "hearingImpaired": False, "downloadCount": 11},
                {"id": 6, "language": "fr", "releaseName": "x", "hearingImpaired": False},
            ]}
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
