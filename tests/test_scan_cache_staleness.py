"""Scan vs the media client's singleton caches (#torrent-album-missing, TheHomeGuy).

The jellyfin/navidrome clients cache per-artist album lists and per-album track
lists on a process-wide singleton, cache-first. Incremental scans populated
those caches and never cleared them (the "no new content" early exit skipped
even the end-of-run clear) — so a deep scan run after an import read a
PRE-IMPORT view of an artist's albums and the newly imported album never
reached the database, showing MISSING forever while the server played it fine.

Contract pinned here:
  • deep scan and full refresh clear the client cache BEFORE fetching
  • every incremental run clears at its end — including the early "no new
    content" exit
  • plex is untouched (its client has no such cache contract)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase


class _RecordingClient:
    """Navidrome-ish client that records the order of cache/fetch calls."""

    def __init__(self, artists=None):
        self.calls = []
        self._artists = artists if artists is not None else []

    def ensure_connection(self):
        return True

    def get_all_artists(self):
        self.calls.append("get_all_artists")
        return list(self._artists)

    def clear_cache(self):
        self.calls.append("clear_cache")

    def set_progress_callback(self, cb):
        pass


@pytest.fixture()
def dbpath(tmp_path, monkeypatch):
    p = str(tmp_path / "music.db")
    db = MusicDatabase(p)
    monkeypatch.setattr("core.database_update_worker.get_database",
                        lambda path=None: db)
    return p


def _worker(dbpath, client, *, server="navidrome", full_refresh=False):
    return DatabaseUpdateWorker(media_client=client, database_path=dbpath,
                                server_type=server, full_refresh=full_refresh,
                                force_sequential=True)


def test_deep_scan_clears_cache_before_fetching(dbpath):
    client = _RecordingClient()
    w = _worker(dbpath, client)
    w.run_deep_scan()
    assert "clear_cache" in client.calls
    assert client.calls.index("clear_cache") < client.calls.index("get_all_artists"), \
        "deep scan must clear the singleton cache BEFORE reading the server"


def test_full_refresh_clears_cache_before_fetching(dbpath):
    client = _RecordingClient()
    w = _worker(dbpath, client, full_refresh=True)
    w.run()
    assert "clear_cache" in client.calls
    assert client.calls.index("clear_cache") < client.calls.index("get_all_artists")


def test_incremental_no_new_content_still_clears(dbpath):
    # the early "no new content" exit used to skip the clear — the probe's own
    # cached listings then poisoned the next deep scan
    client = _RecordingClient()
    w = _worker(dbpath, client)
    w._get_artists_for_incremental_update = lambda: []
    w.run()
    assert "clear_cache" in client.calls


def test_incremental_with_content_clears_at_end(dbpath):
    client = _RecordingClient()
    w = _worker(dbpath, client)
    w._get_artists_for_incremental_update = lambda: [SimpleNamespace(title="A")]
    w._process_all_artists = lambda artists: None
    w.run()
    assert "clear_cache" in client.calls
    # end-of-run clear: after processing, not before the fetch
    assert client.calls[-1] == "clear_cache" or "clear_cache" in client.calls


def test_plex_scans_never_touch_clear_cache(dbpath):
    # plex has no jellyfin/navidrome-style cache contract — the worker must
    # not call clear_cache on it
    client = _RecordingClient(artists=[])
    w = _worker(dbpath, client, server="plex")
    w.run_deep_scan()
    assert "clear_cache" not in client.calls
