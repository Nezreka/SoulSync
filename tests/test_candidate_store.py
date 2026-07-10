"""Candidate store (audit P0-03): indexer download URLs stay server-side.

Search results carry an opaque token; the download path resolves it back.
A client-supplied raw URL or an expired/unknown token must be rejected —
the browser can no longer make SoulSync forward arbitrary URLs to the
download client, and Prowlarr API keys never reach the frontend.
"""

from __future__ import annotations

from unittest.mock import patch

from core.download_plugins.candidate_store import CandidateStore, TOKEN_PREFIX


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
    with patch("core.download_plugins.candidate_store.time.monotonic", return_value=0.0):
        token = store.put("https://x/a.nzb")
    with patch("core.download_plugins.candidate_store.time.monotonic", return_value=50.0):
        assert store.resolve(token) == "https://x/a.nzb"
    with patch("core.download_plugins.candidate_store.time.monotonic", return_value=101.0):
        assert store.resolve(token) is None


def test_size_cap_evicts_oldest():
    store = CandidateStore(max_entries=3)
    tokens = [store.put(f"https://x/{i}.nzb") for i in range(5)]
    live = [t for t in tokens if store.resolve(t) is not None]
    assert len(live) == 3
    # The most recent entries survive.
    assert store.resolve(tokens[-1]) is not None


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
