"""#1047 P3 — playlist writes are chunked and read back.

A single createPlaylist/addItems call carrying ~1000 ratingKeys builds an
oversized request that Plex (or a reverse proxy) can reject or silently
truncate — the sync then reports success while the server playlist is short.
All Plex playlist writes now go through bounded chunks, and the result is
read back so a partial add is logged honestly instead of over-reporting.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.plex_client import PlexClient

_ROOT = Path(__file__).resolve().parent.parent.parent


class _FakePlaylist:
    def __init__(self):
        self.batches = []

    def addItems(self, items):
        self.batches.append(list(items))

    def items(self):
        return [t for b in self.batches for t in b]


def _tracks(n):
    return [SimpleNamespace(ratingKey=i, title=f"T{i}") for i in range(n)]


def _client():
    # bare instance — chunk helpers don't touch the connection
    return PlexClient.__new__(PlexClient)


class TestChunkedAdds:
    def test_large_add_is_split_into_bounded_batches(self):
        c = _client()
        pl = _FakePlaylist()
        c._add_items_chunked(pl, _tracks(1000))
        assert len(pl.batches) == 5
        assert all(len(b) <= PlexClient._PLAYLIST_ADD_CHUNK for b in pl.batches)
        assert len(pl.items()) == 1000

    def test_small_add_is_a_single_batch(self):
        c = _client()
        pl = _FakePlaylist()
        c._add_items_chunked(pl, _tracks(37))
        assert len(pl.batches) == 1

    def test_failed_chunk_raises_not_swallowed(self):
        c = _client()

        class _Boom(_FakePlaylist):
            def addItems(self, items):
                if len(self.batches) == 1:
                    raise RuntimeError("plex rejected")
                super().addItems(items)

        pl = _Boom()
        try:
            c._add_items_chunked(pl, _tracks(500))
            raise AssertionError("a failed chunk must raise")
        except RuntimeError:
            pass


def test_all_three_write_paths_use_the_chunker():
    src = (_ROOT / "core" / "plex_client.py").read_text(encoding="utf-8",
                                                        errors="replace")
    # create (rest-after-first-chunk), append, reconcile
    assert src.count("self._add_items_chunked(") >= 4
    # and no direct oversized addItems remains on the three sync write paths
    assert "existing_playlist.addItems(new_tracks)" not in src
    assert "existing.addItems(to_add)" not in src


def test_writes_are_verified_against_the_server():
    src = (_ROOT / "core" / "plex_client.py").read_text(encoding="utf-8",
                                                        errors="replace")
    assert src.count("self._verify_playlist_count(") >= 3
    assert "partial add" in src
