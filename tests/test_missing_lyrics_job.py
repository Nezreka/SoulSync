"""Missing Lyrics maintenance job + lyrics_client check-only seam (Sokhi).

Mirrors the Cover Art Filler: scan only flags tracks LRClib actually has
lyrics for (Option A — instrumentals never flagged), and applying writes the
.lrc via the shared LyricsClient.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.lyrics_client import LyricsClient
from core.repair_jobs.missing_lyrics import MissingLyricsJob, _has_lrc_sidecar


# ── lyrics_client.has_remote_lyrics (check-only seam) ────────────────────────

def _client_with_api(api):
    c = LyricsClient.__new__(LyricsClient)
    c.api = api
    return c


def test_has_remote_lyrics_true_when_synced():
    api = MagicMock()
    api.get_lyrics.return_value = SimpleNamespace(synced_lyrics="[00:01]hi", plain_lyrics=None)
    c = _client_with_api(api)
    assert c.has_remote_lyrics("Song", "Artist", "Album", 200) is True


def test_has_remote_lyrics_true_when_plain_only_via_search():
    api = MagicMock()
    api.get_lyrics.return_value = None
    api.search_lyrics.return_value = [SimpleNamespace(synced_lyrics=None, plain_lyrics="words")]
    c = _client_with_api(api)
    assert c.has_remote_lyrics("Song", "Artist") is True


def test_has_remote_lyrics_false_when_none():
    api = MagicMock()
    api.get_lyrics.return_value = None
    api.search_lyrics.return_value = []
    assert _client_with_api(api).has_remote_lyrics("Instrumental", "Artist") is False


def test_has_remote_lyrics_false_when_no_api():
    c = LyricsClient.__new__(LyricsClient)
    c.api = None
    assert c.has_remote_lyrics("Song", "Artist") is False


# ── sidecar detection ────────────────────────────────────────────────────────

def test_has_lrc_sidecar(tmp_path):
    audio = tmp_path / "track.flac"
    audio.write_bytes(b"x")
    assert _has_lrc_sidecar(str(audio)) is False
    (tmp_path / "track.lrc").write_text("[00:01]hi")
    assert _has_lrc_sidecar(str(audio)) is True


# ── the scan (Option A: only flag fixable tracks) ────────────────────────────

class _DB:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        cur = MagicMock()
        cur.execute.return_value = None
        cur.fetchone.return_value = [len(self._rows)]
        cur.fetchall.return_value = self._rows
        conn = MagicMock()
        conn.cursor.return_value = cur
        return conn


def _ctx(db, findings):
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: d),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def test_scan_flags_only_tracks_with_available_lyrics(tmp_path, monkeypatch):
    # Two tracks, neither has a .lrc. LRClib has lyrics for the first, not the second.
    t1 = tmp_path / "song.flac"; t1.write_bytes(b"x")
    t2 = tmp_path / "instrumental.flac"; t2.write_bytes(b"x")
    rows = [
        (1, "Song", "Artist", "Album", str(t1), 200),
        (2, "Interlude", "Artist", "Album", str(t2), 60),
    ]
    fake_client = SimpleNamespace(
        api=object(),
        has_remote_lyrics=lambda title, artist, album, dur: title == "Song",
    )
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))

    assert result.findings_created == 1
    assert findings[0]["entity_type"] == "track"
    assert findings[0]["finding_type"] == "missing_lyrics"
    assert findings[0]["details"]["track_title"] == "Song"   # the instrumental was skipped


def test_scan_skips_tracks_that_already_have_lrc(tmp_path, monkeypatch):
    t1 = tmp_path / "song.flac"; t1.write_bytes(b"x")
    (tmp_path / "song.lrc").write_text("[00:01]hi")   # already has lyrics
    rows = [(1, "Song", "Artist", "Album", str(t1), 200)]
    fake_client = SimpleNamespace(api=object(),
                                  has_remote_lyrics=lambda *a, **k: True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    findings = []
    result = MissingLyricsJob().scan(_ctx(_DB(rows), findings))
    assert result.findings_created == 0
    assert findings == []


def test_scan_noops_when_lrclib_disabled(monkeypatch):
    db = _DB([(1, "Song", "Artist", "Album", "/x.flac", 200)])
    ctx = _ctx(db, [])
    ctx.config_manager = SimpleNamespace(
        get=lambda k, d=None: False if k == 'metadata_enhancement.lrclib_enabled' else d)
    result = MissingLyricsJob().scan(ctx)
    assert result.scanned == 0 and result.findings_created == 0


# ── _fix_missing_lyrics apply handler ────────────────────────────────────────

def test_fix_missing_lyrics_calls_create_lrc(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker
    audio = tmp_path / "song.flac"; audio.write_bytes(b"x")

    w = RepairWorker.__new__(RepairWorker)
    w.transfer_folder = str(tmp_path)
    w._config_manager = SimpleNamespace(get=lambda k, d=None: d)

    calls = {}
    fake_client = SimpleNamespace(
        create_lrc_file=lambda path, title, artist, album_name=None, duration_seconds=None:
            calls.update(path=path, title=title, artist=artist) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)
    # _resolve_file_path: the file is already real, so identity is fine.
    monkeypatch.setattr("core.repair_worker._resolve_file_path",
                        lambda raw, *a, **k: raw)

    res = w._fix_missing_lyrics("track", "1", None, {
        "file_path": str(audio), "track_title": "Song", "artist": "Artist",
        "album_title": "Album", "duration": 200})
    assert res["success"] is True and res["action"] == "applied_lyrics"
    assert calls["title"] == "Song" and calls["path"] == str(audio)


def test_fix_missing_lyrics_missing_file(tmp_path, monkeypatch):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)
    w.transfer_folder = str(tmp_path)
    w._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    monkeypatch.setattr("core.repair_worker._resolve_file_path", lambda raw, *a, **k: raw)
    res = w._fix_missing_lyrics("track", "1", None, {"file_path": str(tmp_path / "gone.flac")})
    assert res["success"] is False


# ── retag apply_track_plans lyrics_action ────────────────────────────────────

def test_apply_track_plans_lyrics_action(tmp_path, monkeypatch):
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")

    monkeypatch.setattr(library_retag, "write_tags_to_file",
                        lambda *a, **k: {"success": True}, raising=False)
    seen = {}
    fake_client = SimpleNamespace(
        create_lrc_file=lambda path, title, artist, album_name=None, duration_seconds=None:
            seen.update(title=title) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    plans = [{"file_path": str(audio), "db_data": {},
              "lyrics_meta": {"title": "Song", "artist": "Artist", "album": "Album"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=True)
    assert res["lyrics_written"] == 1 and seen["title"] == "Song"


def test_apply_track_plans_lyrics_never_writes_tags(tmp_path, monkeypatch):
    # The lyrics query must come from lyrics_meta, NOT db_data — so an
    # unmatched track (db_data={}) gets lyrics fetched but NO tags written.
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")
    written = []
    monkeypatch.setattr("core.tag_writer.write_tags_to_file",
                        lambda fp, db_data, **k: written.append(db_data) or {"success": True})
    monkeypatch.setattr("core.lyrics_client.lyrics_client",
                        SimpleNamespace(create_lrc_file=lambda *a, **k: True))

    plans = [{"file_path": str(audio), "db_data": {},
              "lyrics_meta": {"title": "Song", "artist": "Artist", "album": "Al"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=True)
    assert res["lyrics_written"] == 1
    # write_tags_to_file was called with an EMPTY db_data — no title/artist leaked in.
    assert written == [{}]


def test_apply_track_plans_no_lyrics_when_disabled(tmp_path, monkeypatch):
    from core.repair_jobs import library_retag
    audio = tmp_path / "t.flac"; audio.write_bytes(b"x")
    monkeypatch.setattr(library_retag, "write_tags_to_file",
                        lambda *a, **k: {"success": True}, raising=False)
    called = []
    fake_client = SimpleNamespace(create_lrc_file=lambda *a, **k: called.append(1) or True)
    monkeypatch.setattr("core.lyrics_client.lyrics_client", fake_client)

    plans = [{"file_path": str(audio), "db_data": {"title": "Song"}}]
    res = library_retag.apply_track_plans(plans, lyrics_action=False)
    assert res["lyrics_written"] == 0 and called == []
