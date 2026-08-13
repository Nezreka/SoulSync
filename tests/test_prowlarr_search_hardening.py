"""Regression tests for the §27 Prowlarr/interactive-search findings.

dd28-02  a search that times out must not look like a zero-hit search
dd28-05  the plugins' ``timeout`` argument must actually reach Prowlarr
dd28-07  long/bracketed titles need a relaxed-query ladder, like Tidal has
dd28-34  Audio/Foreign (3060) must be searched, or non-Latin releases vanish
dd28-37  the shared indexer allowlist must be narrowed per protocol
dd28-52  bracket stripping must be nesting-aware
"""

from __future__ import annotations

import asyncio

import pytest

from core.download_plugins.query_variants import (
    indexer_query_variants,
    strip_bracket_groups,
    strip_trailing_bracket_group,
)
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    MUSIC_CATEGORY_FOREIGN,
    ProwlarrClient,
    ProwlarrSearchError,
    ProwlarrSearchResult,
)


# --------------------------------------------------------------------------
# dd28-34
# --------------------------------------------------------------------------


def test_foreign_audio_category_is_searched():
    assert MUSIC_CATEGORY_FOREIGN in DEFAULT_MUSIC_CATEGORIES


# --------------------------------------------------------------------------
# dd28-52 / dd28-07 — bracket handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Song (Live (2015))", "Song"),
        ("Song [Remastered]", "Song"),
        ("Freed from Desire (feat. Indiiana)", "Freed from Desire"),
        ("Album (Deluxe) - Track (Live)", "Album - Track"),
        ("No brackets here", "No brackets here"),
        ("Unbalanced (open", "Unbalanced open"),
    ],
)
def test_strip_bracket_groups_is_nesting_aware(text, expected):
    assert strip_bracket_groups(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Song (Live (2015))", "Song"),
        ("Album (Deluxe) - Track (Live)", "Album (Deluxe) - Track"),
        ("OK Computer", "OK Computer"),
        ("Trailing (unbalanced", "Trailing (unbalanced"),
    ],
)
def test_strip_trailing_bracket_group(text, expected):
    assert strip_trailing_bracket_group(text) == expected


def test_query_ladder_keeps_the_exact_query_first():
    variants = indexer_query_variants("Artist Simple Title")
    assert variants[0] == "Artist Simple Title"


def test_query_ladder_relaxes_a_bracketed_feature_credit():
    variants = indexer_query_variants(
        "Drenchill Freed from Desire (feat. Indiiana) - DNF Extended Remix"
    )
    assert variants[0].startswith("Drenchill Freed from Desire (feat.")
    # The release on the indexer is "Drenchill-Freed_From_Desire-WEB-2019-FLAC";
    # some variant has to drop the parenthesized credit for that to match.
    assert any("(" not in v and "feat" not in v.lower() for v in variants)


def test_query_ladder_shortens_an_overlong_query():
    long_query = "Artist " + " ".join(f"word{i}" for i in range(40))
    variants = indexer_query_variants(long_query)
    assert len(long_query) > 100
    assert any(len(v) <= 100 for v in variants)


def test_query_ladder_is_a_single_entry_for_a_plain_title():
    assert indexer_query_variants("Radiohead Creep") == ["Radiohead Creep"]


def test_query_ladder_ignores_an_empty_query():
    assert indexer_query_variants("   ") == []


# --------------------------------------------------------------------------
# dd28-02 / dd28-05 — timeouts
# --------------------------------------------------------------------------


class _StubResponse:
    ok = True

    def json(self):
        return []


def test_search_timeout_prefers_the_caller(monkeypatch):
    client = ProwlarrClient()
    assert client.resolve_search_timeout(42) == 42


def test_search_timeout_falls_back_to_the_user_setting(monkeypatch):
    import core.prowlarr_client as module

    monkeypatch.setattr(
        module.config_manager, "get_source_search_timeout", lambda: 33, raising=False
    )
    assert ProwlarrClient().resolve_search_timeout() == 33


def test_search_timeout_default_is_larger_than_the_metadata_timeout():
    # dd28-02: a search fans out to every indexer; 15s was the metadata budget.
    assert ProwlarrClient.DEFAULT_SEARCH_TIMEOUT > ProwlarrClient.DEFAULT_TIMEOUT


def test_search_passes_its_timeout_to_the_http_call(monkeypatch):
    """dd28-05: the plugin's timeout has to survive all the way to requests."""
    import core.prowlarr_client as module

    seen: dict = {}

    def _fake_get(url, headers=None, params=None, timeout=None):
        seen["timeout"] = timeout
        return _StubResponse()

    client = ProwlarrClient()
    monkeypatch.setattr(client, "_url", "http://prowlarr.test")
    monkeypatch.setattr(client, "_api_key", "key")
    monkeypatch.setattr(module.http_requests, "get", _fake_get)

    asyncio.run(client.search("anything", timeout=57))

    assert seen["timeout"] == 57


def test_search_raises_instead_of_reporting_zero_hits_on_timeout(monkeypatch):
    """dd28-02: the core of the bug — a timeout looked like 'nothing matched'."""
    import core.prowlarr_client as module

    def _timeout(*_args, **_kwargs):
        raise module.http_requests.exceptions.Timeout("too slow")

    client = ProwlarrClient()
    monkeypatch.setattr(client, "_url", "http://prowlarr.test")
    monkeypatch.setattr(client, "_api_key", "key")
    monkeypatch.setattr(module.http_requests, "get", _timeout)

    with pytest.raises(ProwlarrSearchError):
        asyncio.run(client.search("anything"))


def test_metadata_calls_keep_their_best_effort_none(monkeypatch):
    import core.prowlarr_client as module

    def _timeout(*_args, **_kwargs):
        raise module.http_requests.exceptions.Timeout("too slow")

    client = ProwlarrClient()
    monkeypatch.setattr(client, "_url", "http://prowlarr.test")
    monkeypatch.setattr(client, "_api_key", "key")
    monkeypatch.setattr(module.http_requests, "get", _timeout)

    assert client._get_indexers_sync() == []


# --------------------------------------------------------------------------
# dd28-37 — protocol-scoped indexer allowlist
# --------------------------------------------------------------------------


def _indexer(indexer_id: int, protocol: str):
    from core.prowlarr_client import ProwlarrIndexer

    return ProwlarrIndexer(
        id=indexer_id, name=f"i{indexer_id}", protocol=protocol,
        enable=True, privacy="public",
    )


def test_allowlist_is_narrowed_to_the_requested_protocol(monkeypatch):
    client = ProwlarrClient()
    monkeypatch.setattr(
        client, "_get_indexers_sync",
        lambda: [_indexer(1, "torrent"), _indexer(2, "usenet"), _indexer(3, "usenet")],
    )
    assert client.indexer_ids_for_protocol([1, 2, 3], "usenet") == [2, 3]
    assert client.indexer_ids_for_protocol([1, 2, 3], "torrent") == [1]


def test_a_torrent_only_allowlist_does_not_silence_usenet(monkeypatch):
    """dd28-37: the exact reported shape — usenet found nothing, forever."""
    client = ProwlarrClient()
    monkeypatch.setattr(
        client, "_get_indexers_sync",
        lambda: [_indexer(1, "torrent"), _indexer(2, "torrent")],
    )
    # Empty result = "search every enabled indexer", not "search none".
    assert client.indexer_ids_for_protocol([1, 2], "usenet") == []


def test_an_empty_allowlist_stays_empty(monkeypatch):
    client = ProwlarrClient()
    monkeypatch.setattr(client, "_get_indexers_sync", lambda: [_indexer(1, "usenet")])
    assert client.indexer_ids_for_protocol([], "usenet") == []


def test_unknown_ids_are_kept_rather_than_widening_the_allowlist(monkeypatch):
    client = ProwlarrClient()
    monkeypatch.setattr(client, "_get_indexers_sync", lambda: [_indexer(1, "usenet")])
    assert client.indexer_ids_for_protocol([1, 99], "usenet") == [1, 99]


# --------------------------------------------------------------------------
# plugin-level integration of the above
# --------------------------------------------------------------------------


class _FakeProwlarr:
    def __init__(self, answers):
        self.answers = answers
        self.calls: list = []

    def indexer_ids_for_protocol(self, ids, protocol):
        return list(ids)

    async def search(self, query, categories=None, indexer_ids=None, timeout=None):
        self.calls.append({"query": query, "timeout": timeout})
        result = self.answers.get(query, [])
        if isinstance(result, Exception):
            raise result
        return result


def _usenet_result(title="Some-Release-WEB-FLAC"):
    return ProwlarrSearchResult(
        guid="g", title=title, indexer_id=1, indexer_name="i",
        protocol="usenet", download_url="http://nzb",
    )


def test_plugin_helper_forwards_the_timeout(monkeypatch):
    from core.download_plugins.torrent import prowlarr_search_with_variants

    fake = _FakeProwlarr({"Query": [_usenet_result()]})
    asyncio.run(prowlarr_search_with_variants(fake, "Query", "usenet", timeout=61))

    assert fake.calls[0]["timeout"] == 61


def test_plugin_helper_retries_a_relaxed_query(monkeypatch):
    from core.download_plugins.torrent import prowlarr_search_with_variants

    original = "Drenchill Freed from Desire (feat. Indiiana)"
    relaxed = "Drenchill Freed from Desire"
    fake = _FakeProwlarr({original: [], relaxed: [_usenet_result()]})

    results = asyncio.run(prowlarr_search_with_variants(fake, original, "usenet"))

    assert len(results) == 1
    assert [c["query"] for c in fake.calls][0] == original
    assert relaxed in [c["query"] for c in fake.calls]


def test_plugin_helper_stops_at_the_first_hit(monkeypatch):
    from core.download_plugins.torrent import prowlarr_search_with_variants

    original = "Drenchill Freed from Desire (feat. Indiiana)"
    fake = _FakeProwlarr({original: [_usenet_result()]})

    asyncio.run(prowlarr_search_with_variants(fake, original, "usenet"))

    assert len(fake.calls) == 1, "an exact hit must not trigger relaxed retries"


def test_plugin_helper_propagates_a_total_failure():
    from core.download_plugins.torrent import prowlarr_search_with_variants

    fake = _FakeProwlarr({})
    fake.answers = {}

    async def _always_fail(query, categories=None, indexer_ids=None, timeout=None):
        raise ProwlarrSearchError("Prowlarr did not answer within 75s")

    fake.search = _always_fail

    with pytest.raises(ProwlarrSearchError):
        asyncio.run(prowlarr_search_with_variants(fake, "Anything", "usenet"))


def test_plugin_helper_ignores_other_protocols_when_deciding_to_retry():
    """A torrent-only answer is not a usenet hit — keep relaxing the query."""
    from core.download_plugins.torrent import prowlarr_search_with_variants

    torrent_only = ProwlarrSearchResult(
        guid="g", title="t", indexer_id=1, indexer_name="i",
        protocol="torrent", download_url="http://torrent",
    )
    original = "Title (Deluxe Edition)"
    fake = _FakeProwlarr({original: [torrent_only], "Title": [_usenet_result()]})

    results = asyncio.run(prowlarr_search_with_variants(fake, original, "usenet"))

    assert any(r.protocol == "usenet" for r in results)


def test_plugin_helper_returns_only_the_requested_protocol():
    """PR #1121 review: the protocol filter decided whether to keep retrying
    but the unfiltered list was returned, so the name ``usable`` promised
    something the return value didn't deliver. Every caller re-filtered by
    protocol anyway — do it once, here."""
    from core.download_plugins.torrent import prowlarr_search_with_variants

    torrent_hit = ProwlarrSearchResult(
        guid="g", title="t", indexer_id=1, indexer_name="i",
        protocol="torrent", download_url="http://torrent",
    )
    query = "Mixed Protocols"
    fake = _FakeProwlarr({query: [torrent_hit, _usenet_result()]})

    results = asyncio.run(prowlarr_search_with_variants(fake, query, "usenet"))

    assert [r.protocol for r in results] == ["usenet"]


# --------------------------------------------------------------------------
# PR #1121 review — one canonical protocol spelling
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["Torrent", "TORRENT", " torrent "])
def test_parsed_results_carry_a_canonical_protocol(raw):
    """Prowlarr answers with 'Torrent' as readily as 'torrent'. The helper
    compared case-INsensitively while every caller re-filters with
    ``result.protocol != 'torrent'``, so a capitalised release survived the
    search, ended the relaxed-query ladder, and was then dropped by the
    projection — a search that found hits returning nothing. Normalising once
    at the parse boundary is what makes both spellings agree."""
    client = ProwlarrClient.__new__(ProwlarrClient)
    parsed = ProwlarrClient._parse_result(client, {
        'guid': 'g', 'title': 't', 'indexerId': 1, 'indexer': 'i',
        'protocol': raw, 'downloadUrl': 'http://x',
    })
    assert parsed.protocol == 'torrent'


def test_parsed_indexers_carry_a_canonical_protocol():
    client = ProwlarrClient.__new__(ProwlarrClient)
    parsed = ProwlarrClient._parse_indexer(client, {
        'id': 3, 'name': 'n', 'protocol': 'Usenet',
    })
    assert parsed.protocol == 'usenet'


def test_a_capitalised_release_survives_to_the_caller_filter():
    """End to end: the helper's answer must satisfy the case-SENSITIVE filter
    every caller applies before projecting or grabbing."""
    from core.download_plugins.torrent import prowlarr_search_with_variants

    client = ProwlarrClient.__new__(ProwlarrClient)
    capitalised = ProwlarrClient._parse_result(client, {
        'guid': 'g', 'title': 't', 'indexerId': 1, 'indexer': 'i',
        'protocol': 'Torrent', 'magnetUrl': 'magnet:?xt=1',
    })
    query = "Some Album"
    fake = _FakeProwlarr({query: [capitalised]})

    results = asyncio.run(prowlarr_search_with_variants(fake, query, "torrent"))

    assert results, "the helper dropped a hit it had already accepted"
    assert [r for r in results if r.protocol == 'torrent'] == results


@pytest.mark.parametrize(
    "text, expected",
    [
        # A mismatched pair is NOT a balanced group: the '(' must not be
        # closed by a ']'. Popping on any closer dropped "Live] Remix"'s
        # first half and returned a query the user never typed.
        ("Song (Live] Remix", "Song Live] Remix"),
        ("Song [Live) Remix", "Song Live) Remix"),
        # …while genuine groups of either kind still go.
        ("Song (Live) [2015] Remix", "Song Remix"),
        # An opener that never closes still yields its own character only.
        ("Song (Live Remix", "Song Live Remix"),
    ],
)
def test_strip_bracket_groups_requires_a_matching_closer(text, expected):
    assert strip_bracket_groups(text) == expected
