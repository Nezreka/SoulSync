"""Tests for #2271 (separate sync execution history records + trigger source)
and #2265 (multiple torrent clients with per-library assignment)."""

from __future__ import annotations

from types import SimpleNamespace

from core.downloads.history import record_sync_history_start
from core.torrent_clients import (
    DelugeAdapter,
    QBittorrentAdapter,
    get_adapter_for_client,
    get_adapter_for_library,
    list_configured_clients,
)


def test_sync_history_preserves_separate_runs_and_trigger_source_2271():
    added = []

    class _FakeDB:
        def add_sync_history_entry(self, **kwargs):
            added.append(kwargs)

    db = _FakeDB()
    tracks = [{"id": "t1", "name": "Track 1", "artists": [{"name": "Artist"}]}]

    record_sync_history_start(
        db,
        "b1",
        "auto_mirror_42",
        "Chill Mix",
        tracks,
        False,
        None,
        None,
        False,
    )
    record_sync_history_start(
        db,
        "b2",
        "auto_mirror_42",
        "Chill Mix",
        tracks,
        False,
        None,
        None,
        False,
        source_page="manual",
    )

    assert len(added) == 2
    assert added[0]["batch_id"] == "b1"
    assert added[0]["source_page"] == "scheduled"
    assert added[1]["batch_id"] == "b2"
    assert added[1]["source_page"] == "manual"


def test_multi_torrent_clients_and_per_library_assignment_2265(monkeypatch):
    import core.torrent_clients as tc

    store = {
        "torrent_client.type": "qbittorrent",
        "torrent_client.url": "http://qbit:8080",
        "torrent_client.username": "admin",
        "torrent_client.password": "secret",
        "torrent_client.category": "soulsync",
        "torrent_client.save_path": "/downloads/qbit",
        "torrent_client.clients": [
            {
                "id": "deluge-4k",
                "name": "Deluge 4K",
                "type": "deluge",
                "url": "http://deluge:8112",
                "password": "delugepass",
                "category": "movies-4k",
                "save_path": "/downloads/4k",
            }
        ],
        "torrent_client.library_assignments": {
            "movies:4K Movies": "deluge-4k",
        },
    }
    monkeypatch.setattr(tc, "config_manager", SimpleNamespace(get=lambda k, d=None: store.get(k, d)))

    clients = list_configured_clients()
    assert [c["id"] for c in clients] == ["default", "deluge-4k"]

    default_adapter = get_adapter_for_client("default")
    assert isinstance(default_adapter, QBittorrentAdapter)

    deluge_adapter = get_adapter_for_library("movies:4K Movies")
    assert isinstance(deluge_adapter, DelugeAdapter)

    # Explicit library_cfg overrides assignment map
    explicit = get_adapter_for_library("audiobooks", {"torrent_client_id": "deluge-4k"})
    assert isinstance(explicit, DelugeAdapter)

    # Unassigned library falls back to global default
    fallback = get_adapter_for_library("audiobooks")
    assert isinstance(fallback, QBittorrentAdapter)
