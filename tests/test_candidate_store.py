"""Candidate store (audit P0-03 / §16.1): indexer download URLs stay server-side.

Search results carry an opaque token; the download path resolves it back.
A client-supplied raw URL or an expired/unknown token must be rejected —
the browser can no longer make SoulSync forward arbitrary URLs to the
download client, and Prowlarr API keys never reach the frontend.

§16.1 follow-ups covered here too: tokens are bound to the profile (and
optionally the lib2 entity) that searched, resolution revalidates that
binding, and the store is SQLite-backed so it is shared across workers
and survives a restart between search and grab.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from core.download_plugins.candidate_store import (
    CandidateStore,
    TOKEN_PREFIX,
    candidate_binding,
)


def test_put_resolve_roundtrip():
    store = CandidateStore()
    token = store.put("https://indexer/dl?apikey=SECRET")
    assert token.startswith(TOKEN_PREFIX)
    assert "SECRET" not in token
    assert store.resolve(token) == "https://indexer/dl?apikey=SECRET"


def test_same_url_reuses_token():
    store = CandidateStore()
    assert store.put("https://x/a.nzb") == store.put("https://x/a.nzb")
    assert store.put("https://x/a.nzb") != store.put("https://x/b.nzb")


def test_unknown_and_tampered_tokens_are_rejected():
    store = CandidateStore()
    store.put("https://x/a.nzb")
    assert store.resolve(TOKEN_PREFIX + "forged") is None
    # A raw URL is not a token — the pre-fix behaviour (client sends the
    # URL, server fetches it) must not resolve.
    assert store.resolve("https://evil.example/payload.nzb") is None
    assert store.resolve("") is None
    assert store.resolve(None) is None


def test_expired_token_is_rejected():
    store = CandidateStore(ttl_seconds=100)
    with patch("core.download_plugins.candidate_store.time.time", return_value=1000.0):
        token = store.put("https://x/a.nzb")
    with patch("core.download_plugins.candidate_store.time.time", return_value=1050.0):
        assert store.resolve(token) == "https://x/a.nzb"
    with patch("core.download_plugins.candidate_store.time.time", return_value=1101.0):
        assert store.resolve(token) is None


def test_size_cap_evicts_oldest():
    store = CandidateStore(max_entries=3)
    tokens = [store.put(f"https://x/{i}.nzb") for i in range(5)]
    live = [t for t in tokens if store.resolve(t) is not None]
    assert len(live) == 3
    # The most recent entries survive.
    assert store.resolve(tokens[-1]) is not None


# ---------------------------------------------------------------------------
# §16.1 — profile / entity binding
# ---------------------------------------------------------------------------

def test_token_is_bound_to_the_searching_profile():
    store = CandidateStore()
    with candidate_binding(2):
        token = store.put("https://x/a.nzb")
    # Another profile (or the admin default) cannot grab it.
    assert store.resolve(token) is None
    with candidate_binding(3):
        assert store.resolve(token) is None
    with candidate_binding(2):
        assert store.resolve(token) == "https://x/a.nzb"


def test_unbound_put_defaults_to_admin_profile():
    store = CandidateStore()
    token = store.put("https://x/a.nzb")  # background flow, no binding
    with candidate_binding(1):
        assert store.resolve(token) == "https://x/a.nzb"
    with candidate_binding(2):
        assert store.resolve(token) is None


def test_same_url_different_profile_gets_a_different_token():
    store = CandidateStore()
    with candidate_binding(1):
        t1 = store.put("https://x/a.nzb")
    with candidate_binding(2):
        t2 = store.put("https://x/a.nzb")
    assert t1 != t2


def test_entity_bound_token_rejects_a_different_entity():
    store = CandidateStore()
    with candidate_binding(1, lib2_track_id=5):
        token = store.put("https://x/a.nzb")
    # Redirecting the candidate at another track is refused.
    with candidate_binding(1, lib2_track_id=6):
        assert store.resolve(token) is None
    with candidate_binding(1, lib2_track_id=5):
        assert store.resolve(token) == "https://x/a.nzb"
    # A context-free grab of an entity-scoped token merely loses lib2
    # linking; the profile still has to match.
    with candidate_binding(1):
        assert store.resolve(token) == "https://x/a.nzb"


def test_album_binding_checked_independently():
    store = CandidateStore()
    with candidate_binding(1, lib2_album_id=10):
        token = store.put("https://x/a.nzb")
    with candidate_binding(1, lib2_album_id=11):
        assert store.resolve(token) is None
    with candidate_binding(1, lib2_album_id=10, lib2_track_id=99):
        # Token never claimed a track — only the album binding is enforced.
        assert store.resolve(token) == "https://x/a.nzb"


def test_generic_token_stays_usable_for_entity_grabs():
    """Today's main UI flow: generic /api/search feeds a lib2-entity grab."""
    store = CandidateStore()
    with candidate_binding(1):
        token = store.put("https://x/a.nzb")
    with candidate_binding(1, lib2_track_id=7, lib2_album_id=3):
        assert store.resolve(token) == "https://x/a.nzb"


# ---------------------------------------------------------------------------
# §16.1 — shared, restart-safe backing store
# ---------------------------------------------------------------------------

def test_store_is_shared_between_instances_via_file(tmp_path):
    """Two store instances over the same file see each other's tokens —
    the multi-worker / restart-between-search-and-grab case."""
    path = os.path.join(str(tmp_path), "candidates.db")
    writer = CandidateStore(path=path)
    token = writer.put("https://x/a.nzb")
    reader = CandidateStore(path=path)
    assert reader.resolve(token) == "https://x/a.nzb"


def test_plugin_download_rejects_raw_url(monkeypatch):
    """End-to-end guard: a client that still submits `<url>||<name>` (the
    pre-fix wire format, or a tampered request) must be refused."""
    from core.download_plugins.torrent import TorrentDownloadPlugin, _FILENAME_SEP
    from utils.async_helpers import run_async

    plugin = TorrentDownloadPlugin()
    monkeypatch.setattr(plugin, "is_configured", lambda: True)
    result = run_async(plugin.download(
        "torrent", f"https://evil.example/x.torrent{_FILENAME_SEP}Album"))
    assert result is None
    assert plugin.active_downloads == {}


def test_plugin_download_rejects_foreign_profiles_token(monkeypatch):
    """End-to-end guard: a token minted by one profile's search cannot be
    grabbed by another profile."""
    from core.download_plugins import candidate_store as cs_mod
    from core.download_plugins.torrent import TorrentDownloadPlugin, _FILENAME_SEP
    from utils.async_helpers import run_async

    store = CandidateStore()
    monkeypatch.setattr(cs_mod, "_store", store)

    with candidate_binding(2):
        token = store.put("https://indexer/dl?apikey=SECRET")

    plugin = TorrentDownloadPlugin()
    monkeypatch.setattr(plugin, "is_configured", lambda: True)

    async def _grab():
        with candidate_binding(3):
            return await plugin.download("torrent", f"{token}{_FILENAME_SEP}Album")

    assert run_async(_grab()) is None
    assert plugin.active_downloads == {}
