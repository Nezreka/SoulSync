"""Phase 1 provider interface: contract tests, registry, the hybrid chain, and the
OpenSubtitles provider. No network — provider HTTP is stubbed at the instance level.
"""

from __future__ import annotations

import pytest

from core.video.subtitles import providers as providers_pkg
from core.video.subtitles.providers import fetch_subtitle, get_providers
from core.video.subtitles.providers.base import (
    SubtitleCandidate,
    SubtitleProvider,
    SubtitleQuery,
)
from core.video.subtitles.providers.opensubtitles import OpenSubtitlesProvider


def _get_setting(key, default=None):
    return None  # fakes below don't read settings


# ── contract: the ABC shape ──────────────────────────────────────────────────
class FakeProvider(SubtitleProvider):
    id = "fake"
    display_name = "Fake"

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return True

    def search(self, query):
        return []

    def download(self, candidate):
        return None


def test_abc_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        SubtitleProvider()


def test_fake_provider_satisfies_the_abc_shape():
    p = FakeProvider()
    assert isinstance(p, SubtitleProvider)
    assert p.id == "fake"
    assert p.display_name == "Fake"
    assert p.needs_key is False
    assert p.is_configured(_get_setting) is True
    q = SubtitleQuery(identity={"tmdb_id": 1}, language="en")
    assert p.search(q) == []
    c = SubtitleCandidate("fake", "en", False, False, "t", 1)
    assert p.download(c) is None


def test_missing_abstract_member_blocks_instantiation():
    class Incomplete(SubtitleProvider):
        id = "x"
        display_name = "x"

        @property
        def needs_key(self):
            return False

        def is_configured(self, get_setting):
            return True

        def search(self, query):
            return []
        # download() not implemented

    with pytest.raises(TypeError):
        Incomplete()


def test_query_and_candidate_carry_hi_forced():
    q = SubtitleQuery(identity={"imdb_id": "tt0133093"}, language="en", hi=True, forced=True)
    assert (q.hi, q.forced, q.language) == (True, True, "en")
    c = SubtitleCandidate("p", "en", True, False, "Some.Release.srt", "ref-1")
    assert (c.provider_id, c.hi, c.forced, c.title, c.download_ref) == \
        ("p", True, False, "Some.Release.srt", "ref-1")


# ── registry ─────────────────────────────────────────────────────────────────
def test_registry_returns_the_registered_providers():
    providers = get_providers()
    assert set(providers) == {"opensubtitles"}
    p = providers["opensubtitles"]
    assert isinstance(p, SubtitleProvider)
    assert p.id == "opensubtitles"
    assert p.display_name == "OpenSubtitles"
    assert p.needs_key is True


def test_registry_returns_fresh_instances():
    # providers cache resolved config on is_configured(); shared instances would
    # leak one caller's settings into another's.
    assert get_providers()["opensubtitles"] is not get_providers()["opensubtitles"]


# ── the hybrid chain ─────────────────────────────────────────────────────────
class ScriptedProvider(SubtitleProvider):
    """A fake provider with scripted behavior for chain tests."""

    def __init__(self, pid, configured=True, candidates=(), texts=None,
                 search_raises=False, download_raises=False):
        self.id = pid
        self.display_name = pid.title()
        self._configured = configured
        self._candidates = list(candidates)
        self._texts = dict(texts or {})
        self._search_raises = search_raises
        self._download_raises = download_raises
        self.search_calls = []
        self.download_calls = []

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return self._configured

    def search(self, query):
        self.search_calls.append(query)
        if self._search_raises:
            raise RuntimeError("provider exploded")
        return list(self._candidates)

    def download(self, candidate):
        self.download_calls.append(candidate)
        if self._download_raises:
            raise RuntimeError("download exploded")
        return self._texts.get(candidate.download_ref)


def _cand(pid, ref, title="Some.Release"):
    return SubtitleCandidate(provider_id=pid, language="en", hi=False, forced=False,
                             title=title, download_ref=ref)


def _with_registry(monkeypatch, providers):
    monkeypatch.setattr(providers_pkg, "get_providers", lambda: dict(providers))


def _query():
    return SubtitleQuery(identity={"tmdb_id": 603}, language="en")


def test_chain_first_success_wins_and_later_providers_are_untouched(monkeypatch):
    a = ScriptedProvider("a", candidates=[_cand("a", "r1")], texts={"r1": "srt-a"})
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-a"
    assert b.search_calls == [] and b.download_calls == []   # never consulted


def test_chain_falls_back_to_next_provider_on_miss(monkeypatch):
    a = ScriptedProvider("a", candidates=[])                # miss
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-b"


def test_chain_falls_back_when_a_provider_raises(monkeypatch):
    a = ScriptedProvider("a", search_raises=True)
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-b"   # never raises


def test_chain_skips_unknown_provider_ids(monkeypatch):
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"b": b})
    assert fetch_subtitle(_query(), ["nope", "b"], _get_setting) == "srt-b"


def test_chain_skips_non_string_provider_ids(monkeypatch):
    # an unhashable pid (e.g. a dict) would make providers.get() raise
    # TypeError — the chain must skip it, never raise.
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"b": b})
    assert fetch_subtitle(_query(), [{"evil": 1}, "b"], _get_setting) == "srt-b"
    assert fetch_subtitle(_query(), [None, 42, ["x"], "b"], _get_setting) == "srt-b"


def test_chain_skips_unconfigured_providers(monkeypatch):
    a = ScriptedProvider("a", configured=False, candidates=[_cand("a", "r1")],
                         texts={"r1": "srt-a"})
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-b"
    assert a.search_calls == []                              # never searched


def test_chain_tries_every_candidate_before_the_next_provider(monkeypatch):
    c1, c2 = _cand("a", "r1"), _cand("a", "r2")
    a = ScriptedProvider("a", candidates=[c1, c2], texts={"r2": "srt-a2"})  # r1 misses
    b = ScriptedProvider("b", candidates=[_cand("b", "r3")], texts={"r3": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-a2"
    assert a.download_calls == [c1, c2]
    assert b.search_calls == []


def test_chain_download_exception_falls_through(monkeypatch):
    a = ScriptedProvider("a", candidates=[_cand("a", "r1")], download_raises=True)
    b = ScriptedProvider("b", candidates=[_cand("b", "r2")], texts={"r2": "srt-b"})
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) == "srt-b"


def test_chain_empty_order_returns_none(monkeypatch):
    _with_registry(monkeypatch, {"a": ScriptedProvider("a")})
    assert fetch_subtitle(_query(), [], _get_setting) is None
    assert fetch_subtitle(_query(), None, _get_setting) is None


def test_chain_all_miss_returns_none(monkeypatch):
    a = ScriptedProvider("a", candidates=[])
    b = ScriptedProvider("b", configured=False)
    _with_registry(monkeypatch, {"a": a, "b": b})
    assert fetch_subtitle(_query(), ["a", "b"], _get_setting) is None


# ── OpenSubtitles provider ───────────────────────────────────────────────────
def _configured_provider(key="k"):
    p = OpenSubtitlesProvider()
    assert p.is_configured(lambda k, d=None: key if k == "opensubtitles_api_key" else None)
    return p


def test_opensubtitles_is_configured_needs_a_key():
    p = OpenSubtitlesProvider()
    assert p.is_configured(lambda k, d=None: "secret") is True
    assert p.is_configured(lambda k, d=None: "  ") is False
    assert p.is_configured(lambda k, d=None: None) is False


def test_opensubtitles_search_orders_candidates_most_downloaded_first(monkeypatch):
    p = _configured_provider()
    found = {"data": [
        {"attributes": {"language": "en", "download_count": 10, "files": [{"file_id": 111}],
                        "release": "Movie.2020.1080p-GRP"}},
        {"attributes": {"language": "en", "download_count": 99, "files": [{"file_id": 222}],
                        "release": "Movie.2020.WEB-GRP2"}},
        {"attributes": {"language": "es", "download_count": 500, "files": [{"file_id": 333}]}},
        {"attributes": {"language": "en", "download_count": 5, "files": []}},      # no files
        {"attributes": {"language": "en", "download_count": 7}},                   # no files key
    ]}
    seen = {}
    monkeypatch.setattr(p, "_search_api", lambda params: seen.update(params) or found)
    cands = p.search(SubtitleQuery(identity={"tmdb_id": 603}, language="en"))
    assert [c.download_ref for c in cands] == [222, 111]      # most-downloaded first
    assert seen["tmdb_id"] == 603 and seen["languages"] == "en"
    first = cands[0]
    assert (first.provider_id, first.language, first.hi, first.forced) == \
        ("opensubtitles", "en", False, False)
    assert first.title == "Movie.2020.WEB-GRP2"
    assert all(c.title for c in cands)


def test_opensubtitles_search_filters_candidates_by_hi_and_forced_flags(monkeypatch):
    p = _configured_provider()
    found = {"data": [
        {"attributes": {"language": "en", "download_count": 1, "files": [{"file_id": 1}],
                        "release": "R1"}},                                        # regular
        {"attributes": {"language": "en", "download_count": 2, "files": [{"file_id": 2}],
                        "release": "R2", "hearing_impaired": True}},              # hi
        {"attributes": {"language": "en", "download_count": 3, "files": [{"file_id": 3}],
                        "release": "R3", "forced": True}},                        # forced
        {"attributes": {"language": "en", "download_count": 4, "files": [{"file_id": 4}],
                        "release": "R4", "hearing_impaired": True, "forced": True}},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: found)
    ident = {"tmdb_id": 603}
    assert [c.download_ref for c in
            p.search(SubtitleQuery(identity=ident, language="en"))] == [1]
    assert [c.download_ref for c in
            p.search(SubtitleQuery(identity=ident, language="en", hi=True))] == [2]
    assert [c.download_ref for c in
            p.search(SubtitleQuery(identity=ident, language="en", forced=True))] == [3]
    assert [c.download_ref for c in
            p.search(SubtitleQuery(identity=ident, language="en", hi=True,
                                   forced=True))] == [4]


def test_opensubtitles_search_degrades_to_inexact_flag_matches(monkeypatch):
    # m2: when no candidate matches the query's flags exactly, fall back to
    # the unfiltered list (most-downloaded first) instead of a total miss —
    # the legacy pick_best_file had no flag filtering, so an only-HI 'en'
    # sub used to be downloaded as X.en.srt.
    p = _configured_provider()
    found = {"data": [
        {"attributes": {"language": "en", "download_count": 5, "files": [{"file_id": 9}],
                        "release": "R-hi", "hearing_impaired": True}},
        {"attributes": {"language": "en", "download_count": 12, "files": [{"file_id": 10}],
                        "release": "R-hi2", "hearing_impaired": True}},
    ]}
    monkeypatch.setattr(p, "_search_api", lambda params: found)
    cands = p.search(SubtitleQuery(identity={"tmdb_id": 603}, language="en"))
    assert [c.download_ref for c in cands] == [10, 9]   # degraded, not empty
    assert all(c.hi for c in cands)


def test_opensubtitles_search_title_falls_back_to_file_name(monkeypatch):
    p = _configured_provider()
    found = {"data": [{"attributes": {"language": "en", "download_count": 1,
                                      "files": [{"file_id": 9, "file_name": "s1e1.srt"}]}}]}
    monkeypatch.setattr(p, "_search_api", lambda params: found)
    (cand,) = p.search(SubtitleQuery(identity={"tmdb_id": 1}, language="en"))
    assert cand.title == "s1e1.srt"


def test_opensubtitles_search_miss_and_unidentified_return_empty(monkeypatch):
    p = _configured_provider()
    monkeypatch.setattr(p, "_search_api", lambda params: {"data": []})
    assert p.search(SubtitleQuery(identity={"tmdb_id": 1}, language="de")) == []

    p2 = _configured_provider()
    calls = []
    monkeypatch.setattr(p2, "_search_api", lambda params: calls.append(params) or {"data": []})
    assert p2.search(SubtitleQuery(identity={}, language="en")) == []
    assert calls == []                                        # no HTTP for unidentified


def test_opensubtitles_search_before_is_configured_returns_empty(monkeypatch):
    p = OpenSubtitlesProvider()                               # never configured
    calls = []
    monkeypatch.setattr(p, "_search_api", lambda params: calls.append(params) or {"data": []})
    assert p.search(SubtitleQuery(identity={"tmdb_id": 1}, language="en")) == []
    assert calls == []


def test_opensubtitles_download_flow(monkeypatch):
    p = _configured_provider()
    monkeypatch.setattr(p, "_download_link", lambda file_id: "https://dl/x" if file_id == 222 else None)
    monkeypatch.setattr(p, "_fetch_text", lambda url: "1\n00:00 --> 00:01\n[x]\n")
    cand = SubtitleCandidate("opensubtitles", "en", False, False, "t", 222)
    assert p.download(cand).startswith("1\n")
    assert p.download(SubtitleCandidate("opensubtitles", "en", False, False, "t", 999)) is None


def test_opensubtitles_download_before_is_configured_returns_none():
    p = OpenSubtitlesProvider()
    cand = SubtitleCandidate("opensubtitles", "en", False, False, "t", 222)
    assert p.download(cand) is None
