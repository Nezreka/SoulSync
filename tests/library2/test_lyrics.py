"""Track-level lyrics fetch for Library v2 (deep-dive B3).

Mirrors ``core/library2/replaygain.py``'s orchestration-under-test shape: the
real LRClib client is injected out, so this covers path resolution, the
tag-cache rescan, and error reporting without hitting the network.
"""

from __future__ import annotations


from core.library2 import lyrics as L


class _FakeLyricsClient:
    def __init__(self, *, api=True, ok=True, calls=None):
        self.api = api
        self._ok = ok
        self.calls = calls if calls is not None else []

    def create_lrc_file(self, path, title, artist, album_name=None, duration_seconds=None):
        self.calls.append((path, title, artist, album_name, duration_seconds))
        return self._ok


def _one_dance_track_id(conn):
    return conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]


def test_fetch_track_lyrics_writes_and_rescans_tag_cache(imported_conn, monkeypatch):
    track_id = _one_dance_track_id(imported_conn)
    rescanned = []
    monkeypatch.setattr(
        "core.library2.tag_cache.read_and_persist_tag_cache",
        lambda conn, file_id, path: rescanned.append((file_id, path)) or True,
    )
    client = _FakeLyricsClient()

    result = L.fetch_track_lyrics(
        imported_conn,
        track_id,
        resolve_fn=lambda p: p,
        lyrics_client_obj=client,
    )

    assert result == {"fetched": True, "error": None}
    assert client.calls[0][0] == "/m/01.flac"
    assert client.calls[0][1] == "One Dance"
    assert client.calls[0][3] == "Views"
    file_id = imported_conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=?", (track_id,)
    ).fetchone()[0]
    assert rescanned == [(file_id, "/m/01.flac")]


def test_fetch_track_lyrics_reports_when_lrclib_has_nothing(imported_conn):
    track_id = _one_dance_track_id(imported_conn)
    client = _FakeLyricsClient(ok=False)

    result = L.fetch_track_lyrics(
        imported_conn, track_id, resolve_fn=lambda p: p, lyrics_client_obj=client,
    )

    assert result["fetched"] is False
    assert "no lyrics" in result["error"].lower() or "not available" in result["error"].lower()


def test_fetch_track_lyrics_reports_disabled_client(imported_conn):
    track_id = _one_dance_track_id(imported_conn)
    client = _FakeLyricsClient(api=None)

    result = L.fetch_track_lyrics(
        imported_conn, track_id, resolve_fn=lambda p: p, lyrics_client_obj=client,
    )

    assert result == {"fetched": False, "error": "LRClib is not enabled"}


def test_fetch_track_lyrics_reports_missing_file_on_disk(imported_conn):
    track_id = _one_dance_track_id(imported_conn)
    client = _FakeLyricsClient()

    result = L.fetch_track_lyrics(
        imported_conn, track_id, resolve_fn=lambda p: None, lyrics_client_obj=client,
    )

    assert result["fetched"] is False
    assert "not found" in result["error"].lower()
    assert client.calls == []


def test_fetch_track_lyrics_reports_fileless_track(imported_conn):
    # Legacy seed track 101 has no file.
    track_id = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=101"
    ).fetchone()[0]
    client = _FakeLyricsClient()

    result = L.fetch_track_lyrics(
        imported_conn, track_id, resolve_fn=lambda p: p, lyrics_client_obj=client,
    )

    assert result["fetched"] is False
    assert client.calls == []


def test_fetch_track_lyrics_reports_unknown_track(imported_conn):
    client = _FakeLyricsClient()

    result = L.fetch_track_lyrics(
        imported_conn, 999999, resolve_fn=lambda p: p, lyrics_client_obj=client,
    )

    assert result["fetched"] is False
    assert client.calls == []
