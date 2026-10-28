"""#1831 (glebosotov): when some prowlarr indexers fail during a search, say so
next to the results instead of only in the log.

the per-indexer fan-out (#1151) already kept the results from the indexers that
answered and knew which ones didn't. that only reached app.log, so a search
missing half its indexers looked exactly like a complete one on the page.
"""

from __future__ import annotations

import asyncio

import pytest

from core.prowlarr_client import (
    IndexerFailure,
    ProwlarrClient,
    ProwlarrIndexer,
    ProwlarrSearchError,
    ProwlarrSearchResult,
)
from core.search import basic
from core.search.warnings import collect, note_indexer_failure


def _indexer(indexer_id, name, protocol="torrent"):
    return ProwlarrIndexer(id=indexer_id, name=name, protocol=protocol,
                           enable=True, privacy="private")


def _result(indexer_id):
    return ProwlarrSearchResult(
        guid=f"g{indexer_id}", title="Some Artist - Some Album [FLAC]",
        indexer_id=indexer_id, indexer_name=f"indexer{indexer_id}",
        protocol="torrent", download_url=f"http://t/{indexer_id}.torrent",
        size=400 * 1024 * 1024,
    )


INDEXERS = [_indexer(1, "GoodTracker"), _indexer(2, "BrokenTracker"), _indexer(3, "OtherTracker")]


@pytest.fixture
def prowlarr(monkeypatch):
    """a real client with the http calls stubbed: indexer 2 answers 502."""
    import core.prowlarr_client as pc
    import core.prowlarr_throttle as throttle

    monkeypatch.setattr(pc, "_INDEXER_PRIORITY_CACHE", None)
    monkeypatch.setattr(throttle, "wait_for_slot", lambda *a, **k: True)
    client = ProwlarrClient()
    lookups = []

    def _indexers():
        lookups.append(1)
        return list(INDEXERS)

    def _search(query, categories, indexer_ids, limit, *args, **kwargs):
        if indexer_ids == [2]:
            raise ProwlarrSearchError("Prowlarr returned HTTP 502")
        return [_result(indexer_ids[0])]

    monkeypatch.setattr(client, "is_configured", lambda: True)
    monkeypatch.setattr(client, "_get_indexers_sync", _indexers)
    monkeypatch.setattr(client, "_search_sync", _search)
    client.lookups = lookups
    yield client
    monkeypatch.setattr(pc, "_INDEXER_PRIORITY_CACHE", None)


# ── naming the failed indexer ────────────────────────────────────────────────

def test_a_failed_indexer_is_named(prowlarr):
    results, failures = asyncio.run(prowlarr.search_each_indexer("q", [1, 2, 3]))

    assert [r.indexer_id for r in results] == [1, 3]
    assert len(failures) == 1
    failure = failures[0]
    assert failure.indexer_name == "BrokenTracker"
    assert "502" in failure.error
    # still the plain string the log and the all-failed error always used
    assert "indexer 2" in failure and "BrokenTracker" in failure


def test_no_name_lookup_when_every_indexer_failed(prowlarr, monkeypatch):
    # prowlarr may be the thing that's down, asking it again is another wait
    def _down(*args, **kwargs):
        raise ProwlarrSearchError("Prowlarr did not answer within 75s")

    monkeypatch.setattr(prowlarr, "_search_sync", _down)
    _, failures = asyncio.run(prowlarr.search_each_indexer("q", [1, 2]))

    assert prowlarr.lookups == []
    assert [f.indexer_name for f in failures] == ["indexer 1", "indexer 2"]


def test_an_unlisted_indexer_falls_back_to_its_id():
    failure = IndexerFailure(7, "boom")
    assert failure == "indexer 7: boom"
    assert failure.indexer_name == "indexer 7"


# ── the collector ────────────────────────────────────────────────────────────

def test_notes_outside_a_search_go_nowhere():
    note_indexer_failure("torrent", "X", "boom")     # no collector, no error


def test_a_retried_indexer_is_listed_once():
    async def _search():
        note_indexer_failure("torrent", "BrokenTracker", "first")
        note_indexer_failure("torrent", "BrokenTracker", "second")
        note_indexer_failure("usenet", "BrokenTracker", "other side")
        return "done"

    result, warnings = asyncio.run(collect(_search()))

    assert result == "done"
    assert warnings == [
        {"source": "torrent", "indexer": "BrokenTracker", "error": "second"},
        {"source": "usenet", "indexer": "BrokenTracker", "error": "other side"},
    ]


def test_two_searches_never_share_warnings():
    async def _both():
        async def _noisy():
            note_indexer_failure("torrent", "A", "x")
            await asyncio.sleep(0)
            return 1

        async def _quiet():
            await asyncio.sleep(0)
            return 2

        return await asyncio.gather(collect(_noisy()), collect(_quiet()))

    (_, noisy), (_, quiet) = asyncio.run(_both())
    assert len(noisy) == 1 and quiet == []


# ── the seam: torrent plugin -> basic search -> shared run_async loop ───────

class _Orch:
    def __init__(self, plugin):
        self.plugin = plugin

    def client(self, name):
        return self.plugin


def test_basic_search_hands_back_the_failed_indexer(prowlarr):
    """the whole report. the real torrent plugin, the real fan-out, and the
    real shared loop thread run_async submits to, which is where a contextvar
    could have been lost."""
    from core.download_plugins.torrent import TorrentDownloadPlugin
    from utils.async_helpers import run_async

    plugin = TorrentDownloadPlugin()
    plugin._prowlarr = prowlarr
    warnings = []

    results = basic.run_basic_search("Some Artist Some Album", _Orch(plugin), run_async,
                                     source="torrent", warnings=warnings)

    assert results, "the indexers that answered still return their results"
    assert warnings == [{"source": "torrent", "indexer": "BrokenTracker",
                         "error": "Prowlarr returned HTTP 502"}]


def test_a_clean_search_has_no_warnings(prowlarr, monkeypatch):
    from core.download_plugins.torrent import TorrentDownloadPlugin
    from utils.async_helpers import run_async

    monkeypatch.setattr(prowlarr, "_search_sync",
                        lambda q, c, ids, limit, *a, **k: [_result(ids[0])])
    plugin = TorrentDownloadPlugin()
    plugin._prowlarr = prowlarr
    warnings = []

    basic.run_basic_search("Some Artist Some Album", _Orch(plugin), run_async,
                           source="torrent", warnings=warnings)

    assert warnings == []
