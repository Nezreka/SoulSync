"""Expired Download Cleaner job: scan protection + findings vs auto-delete,
and the shared delete helper.

The pure expiry logic is tested in tests/library/test_expired_cleanup.py; this
covers the job's fact-gathering (play_count, active-mirror/watch protection)
and the two modes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.repair_jobs.expired_download_cleaner import (
    ExpiredDownloadCleanerJob,
    delete_origin_download,
)

OLD = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
NEW = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")


class _DB:
    def __init__(self, candidates, mirrored=None, watched=None):
        self._candidates = candidates
        self._mirrored = mirrored or []
        self._watched = watched or []
        self.deleted_paths = []
        self.deleted_history = []

    def get_origin_cleanup_candidates(self):
        return [dict(c) for c in self._candidates]

    def get_mirrored_playlists(self, profile_id=1):
        return [{'name': n} for n in self._mirrored]

    def get_watchlist_artists(self, profile_id=1):
        return [SimpleNamespace(artist_name=n) for n in self._watched]

    def delete_track_by_file_path(self, p):
        self.deleted_paths.append(p)
        return 1

    def delete_library_history_rows(self, ids):
        self.deleted_history.extend(ids)
        return len(ids)


def _ctx(db, settings, findings):
    return SimpleNamespace(
        db=db,
        config_manager=SimpleNamespace(get=lambda k, d=None: settings if k.endswith('.settings') else d),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def _cand(eid, origin="playlist", created=OLD, play_count=0, ctx="Some Playlist", path=None):
    return {"id": eid, "origin": origin, "origin_context": ctx, "created_at": created,
            "file_path": path or f"/music/{eid}.flac", "title": f"T{eid}",
            "artist_name": "Artist", "play_count": play_count}


# ── scan: findings mode + protections ────────────────────────────────────────

def test_scan_noop_when_both_retentions_off():
    db = _DB([_cand(1)])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(db, {}, findings))   # defaults: both off
    assert res.findings_created == 0 and findings == []


def test_scan_creates_findings_for_expired():
    db = _DB([
        _cand(1, created=OLD, play_count=0),            # expired
        _cand(2, created=NEW, play_count=0),            # too new
        _cand(3, created=OLD, play_count=5),            # listened → keep
    ])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(
        db, {'playlist_retention': '2mo', 'keep_if_played_at_least': 2}, findings))
    assert res.findings_created == 1
    assert findings[0]['details']['history_id'] == 1
    assert findings[0]['finding_type'] == 'expired_download'


def test_scan_protects_actively_mirrored_playlist():
    db = _DB([_cand(1, origin="playlist", ctx="My Mix", created=OLD)],
             mirrored=["My Mix"])
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'playlist_retention': '1w'}, findings))
    assert findings == []   # still mirrored → protected


def test_scan_protects_watched_artist():
    db = _DB([_cand(1, origin="watchlist", ctx="Drake", created=OLD)],
             watched=["Drake"])
    findings = []
    ExpiredDownloadCleanerJob().scan(_ctx(db, {'watchlist_retention': '1w'}, findings))
    assert findings == []   # still watched → protected


def test_scan_auto_delete_mode():
    db = _DB([_cand(1, created=OLD, path="/music/x.flac")])
    findings = []
    res = ExpiredDownloadCleanerJob().scan(_ctx(
        db, {'playlist_retention': '2mo', 'auto_delete': True}, findings))
    assert findings == []                       # no findings in auto mode
    assert res.auto_fixed == 1
    assert 1 in db.deleted_history             # history row removed
    assert "/music/x.flac" in db.deleted_paths # track row removed


# ── delete helper ────────────────────────────────────────────────────────────

def test_delete_origin_download_missing_file(tmp_path):
    # File doesn't exist → still cleans up the history row (orphan), no error.
    db = _DB([])
    entry = {"id": 9, "file_path": str(tmp_path / "gone.flac")}
    cfg = SimpleNamespace(get=lambda k, d=None: d)
    res = delete_origin_download(db, entry, cfg)
    assert res["error"] is None and res["file_deleted"] is False
    assert db.deleted_history == [9]


def test_delete_origin_download_removes_real_file(tmp_path):
    f = tmp_path / "song.flac"; f.write_bytes(b"x")
    db = _DB([])
    entry = {"id": 5, "file_path": str(f)}
    cfg = SimpleNamespace(get=lambda k, d=None: d)
    res = delete_origin_download(db, entry, cfg)
    assert res["file_deleted"] is True and not f.exists()
    assert db.deleted_history == [5]
