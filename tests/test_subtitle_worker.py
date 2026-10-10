"""Phase 2 worker: eligibility/backoff, cycle behavior, quota, re-key.

Provider chain is stubbed at the registry level; video files are real temp files
(needed for the hash + sidecar write paths).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from core.video.subtitles import worker
from core.video.subtitles.providers.base import (
    SubtitleCandidate,
    SubtitleProvider,
    SubtitleQuery,
)
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class FakeFS:
    def __init__(self):
        self.texts = {}

    def list_dir(self, folder):
        return []

    def write_text(self, path, content):
        self.texts[path] = content


def _video_file(tmp_path, name="Movie.2024.1080p.WEB-GROUP.mkv", size=200_000):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return str(p)


def _dl(db, dest_path, **kw):
    d = {"kind": "movie", "title": "T", "status": "completed",
         "media_id": "603", "media_source": "tmdb", "search_ctx": "{}"}
    d.update(kw)
    dl_id = db.add_video_download(d)
    db.update_video_download(dl_id, dest_path=dest_path)
    return dl_id


class ScriptedProvider(SubtitleProvider):
    id = "scripted"
    display_name = "Scripted"

    def __init__(self, candidates=(), texts=None):
        self._candidates = list(candidates)
        self._texts = dict(texts or {})
        self.searches = 0

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return True

    def search(self, query):
        self.searches += 1
        return list(self._candidates)

    def download(self, candidate):
        return self._texts.get(candidate.download_ref)


def _cand(title="Movie.2024.1080p.WEB-GROUP", ref="r1", hash_match=False, dl=100):
    return SubtitleCandidate(provider_id="scripted", language="en", hi=False,
                             forced=False, title=title, download_ref=ref,
                             hash_match=hash_match, download_count=dl)


def _patch_registry(monkeypatch, provider):
    import core.video.subtitles.providers as pkg
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})


def _settings(**kw):
    s = {"download_subtitles": True, "subtitle_provider_order": '["scripted"]',
         "subtitle_worker_batch": 10, "subtitle_daily_quota": 20,
         "subtitle_min_score": 24.0}
    s.update(kw)
    return s


# ── row_eligible ────────────────────────────────────────────────────────────

def test_eligible_wanted_never_tried():
    now = datetime.now(timezone.utc)
    assert worker.row_eligible({"status": "wanted", "attempts": 0,
                                "last_attempt_at": None}, now)


def test_eligible_failed_inside_backoff():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert not worker.row_eligible({"status": "failed", "attempts": 0,
                                    "last_attempt_at": last}, now)


def test_eligible_failed_after_backoff():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert worker.row_eligible({"status": "failed", "attempts": 0,
                                "last_attempt_at": last}, now)


def test_eligible_backoff_grows_with_attempts():
    now = datetime.now(timezone.utc)
    last = (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    row = {"status": "failed", "attempts": 2, "last_attempt_at": last}
    assert not worker.row_eligible(row, now)  # 4h backoff at attempts=2
    row["attempts"] = 1
    assert worker.row_eligible(row, now)      # 2h backoff at attempts=1


# ── run_cycle ───────────────────────────────────────────────────────────────

def test_cycle_skips_when_disabled(db, tmp_path):
    stats = worker.run_cycle(db, _settings(download_subtitles=False), FakeFS())
    assert stats["skipped"] == "disabled"
    assert stats["attempted"] == 0


def test_cycle_downloads_and_marks_have(db, tmp_path, monkeypatch):
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"r1": "srt-data"})
    _patch_registry(monkeypatch, prov)
    fs = FakeFS()
    stats = worker.run_cycle(db, _settings(), fs)
    assert stats == {"attempted": 1, "downloaded": 1, "missed": 0, "skipped": 0}
    assert db.subtitle_have("download", dl_id, "en")
    assert fs.texts[os.path.join(str(tmp_path), "Movie.2024.1080p.WEB-GROUP.en.srt")] == "srt-data"
    assert db.subtitle_quota_used("scripted") == 1
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["outcome"] == "downloaded" and hist[0]["score"] >= 100.0


def test_cycle_miss_marks_failed_and_logs(db, tmp_path, monkeypatch):
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider(candidates=[])  # total miss
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(), FakeFS())
    assert stats["downloaded"] == 0 and stats["missed"] == 1
    rows = db.subtitle_get_retryable()
    assert rows[0]["status"] == "failed" and rows[0]["attempts"] == 1
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["outcome"] == "miss"


def test_cycle_below_threshold_is_not_downloaded(db, tmp_path, monkeypatch):
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    # Weak filename match, low downloads → below the 24.0 gate.
    prov = ScriptedProvider(candidates=[_cand(title="unrelated xyz", dl=1)],
                            texts={"r1": "srt-data"})
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(), FakeFS())
    assert stats["downloaded"] == 0
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["outcome"] == "below_threshold"
    assert db.subtitle_quota_used("scripted") == 0  # no quota burned on a miss


def test_cycle_stops_at_quota(db, tmp_path, monkeypatch):
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"r1": "srt"})
    _patch_registry(monkeypatch, prov)
    for _ in range(20):
        db.subtitle_quota_bump("scripted")
    stats = worker.run_cycle(db, _settings(), FakeFS())
    assert stats["attempted"] == 0  # quota exhausted → nothing attempted
    assert prov.searches == 0


def test_cycle_batch_limit(db, tmp_path, monkeypatch):
    paths = [_video_file(tmp_path, f"m{i}.mkv") for i in range(3)]
    for i, p in enumerate(paths):
        db.subtitle_want("download", _dl(db, p), "en")
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"r1": "srt"})
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(subtitle_worker_batch=2), FakeFS())
    assert stats["attempted"] == 2 and stats["downloaded"] == 2


def test_cycle_unresolvable_file_marks_failed(db, tmp_path, monkeypatch):
    dl_id = _dl(db, "/no/such/file.mkv")
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"r1": "srt"})
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(), FakeFS())
    assert stats["missed"] == 1 and prov.searches == 0
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["outcome"] == "error"


def test_cycle_never_raises_on_db_errors(tmp_path, monkeypatch):
    class BadDB:
        pass
    stats = worker.run_cycle(BadDB(), _settings(), FakeFS())
    assert stats["attempted"] == 0  # no raise, empty stats


def test_cycle_cleans_up_orphaned_rows(db, tmp_path, monkeypatch):
    # Download row deleted (user cleared finished downloads) → the wanted row
    # is orphaned: the worker deletes it instead of retrying forever.
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM video_downloads WHERE id = ?", (dl_id,))
        conn.commit()
    finally:
        conn.close()
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"r1": "srt"})
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(), FakeFS())
    assert prov.searches == 0  # never attempted
    assert db.subtitle_get_for_video("download", dl_id) == []
    hist = db.subtitle_get_history("download", dl_id)
    assert any("orphaned" in (h["candidate_title"] or "") for h in hist)


def test_on_download_fires_per_successful_download(monkeypatch):
    # on_download fires for each successful provider.download() (a quota burn),
    # not for failed ones — quota accounting tracks real burns.
    import core.video.subtitles.providers as pkg
    from core.video.subtitles.providers import fetch_subtitle_detailed
    from core.video.subtitles.providers.base import SubtitleQuery

    class MultiBurn(SubtitleProvider):
        id = "multi"
        display_name = "Multi"

        @property
        def needs_key(self):
            return False

        def is_configured(self, get_setting):
            return True

        def search(self, query):
            return [_cand(title="Movie.2024.1080p.WEB-GROUP", ref="r1"),
                    _cand(title="Movie.2024.1080p.WEB-GROUP", ref="r2"),
                    _cand(title="Movie.2024.1080p.WEB-GROUP", ref="r3")]

        def download(self, candidate):
            return "srt" if candidate.download_ref == "r3" else None

    monkeypatch.setattr(pkg, "get_providers", lambda: {"multi": MultiBurn()})
    burns = []
    q = SubtitleQuery(identity={}, language="en",
                      filename="Movie.2024.1080p.WEB-GROUP.mkv")
    text, pid, _c, _s = fetch_subtitle_detailed(
        q, ["multi"], lambda k, d=None: 0.0, on_download=burns.append)
    assert text == "srt"
    # Only the SUCCESSFUL download() calls burn quota — the two Nones don't.
    assert burns == ["multi"]
