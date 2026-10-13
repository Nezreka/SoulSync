"""Phase 3 upgrade loop: history-baselined re-fetch of 'have' wanted rows.

score_candidate is stubbed for deterministic scores; the provider registry is
stubbed like the worker tests; video files are real temp files (the hash +
sidecar paths are real).
"""

from __future__ import annotations

import os

import pytest

from core.video.subtitles import worker
from core.video.subtitles.providers.base import (
    SubtitleCandidate,
    SubtitleProvider,
)
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _video_file(tmp_path, name="Movie.2024.1080p.WEB-GROUP.mkv", size=200_000):
    p = tmp_path / name
    p.write_bytes(os.urandom(size))
    return str(p)


def _dl(db, dest_path):
    d = {"kind": "movie", "title": "T", "status": "completed",
         "media_id": "603", "media_source": "tmdb", "search_ctx": "{}"}
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
        self.downloads = []

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return True

    def search(self, query):
        self.searches += 1
        return list(self._candidates)

    def download(self, candidate):
        self.downloads.append(candidate.download_ref)
        return self._texts.get(candidate.download_ref)


def _cand(ref="new", title="Movie.2024.1080p.WEB-GROUP", **kw):
    kw.setdefault("hash_match", False)
    return SubtitleCandidate(provider_id="scripted", language="en", hi=False,
                             forced=False, title=title, download_ref=ref, **kw)


def _patch(monkeypatch, provider, scores):
    import core.video.subtitles.providers as pkg
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})
    import core.video.subtitles.scoring as scoring
    monkeypatch.setattr(scoring, "score_candidate",
                        lambda c, q: float(scores[c.download_ref]))


def _patch_registry(monkeypatch, provider):
    import core.video.subtitles.providers as pkg
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})


class FakeFS:
    def __init__(self):
        self.texts = {}

    def list_dir(self, folder):
        return []

    def write_text(self, path, content):
        self.texts[path] = content


def _settings(**kw):
    s = {"download_subtitles": True, "subtitle_provider_order": '["scripted"]',
         "subtitle_worker_batch": 10, "subtitle_daily_quota": 20,
         "subtitle_min_score": 24.0, "subtitle_upgrade_min_delta": 15.0}
    s.update(kw)
    return s


def _have_row(db, dl_id, score, user_set=False):
    db.subtitle_want("download", dl_id, "en", user_set=user_set)
    db.subtitle_mark("download", dl_id, "en", "have")
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded",
                          provider="scripted", candidate_title="old", score=score)


def _sidecar(tmp_path):
    return os.path.join(str(tmp_path), "Movie.2024.1080p.WEB-GROUP.en.srt")


# ── db layer ──────────────────────────────────────────────────────────────

def test_get_upgradable_filters(db):
    db.subtitle_want("download", 1, "en")
    db.subtitle_mark("download", 1, "en", "have")                    # in
    db.subtitle_want("download", 2, "en", user_set=True)
    db.subtitle_mark("download", 2, "en", "have")                    # user_set → out
    db.subtitle_want("download", 3, "en")                            # wanted → out
    db.subtitle_want("download", 4, "en")
    db.subtitle_mark("download", 4, "en", "failed")                  # failed → out
    rows = db.subtitle_get_upgradable(50)
    assert [(r["video_kind"], r["video_id"]) for r in rows] == [("download", 1)]
    db.subtitle_stamp_upgrade_check("download", 1, "en")             # cooldown → out
    assert db.subtitle_get_upgradable(50) == []


def test_latest_download_score(db):
    db.subtitle_log_fetch("download", 1, "en", "miss", provider="s", score=10.0)
    db.subtitle_log_fetch("download", 1, "en", "downloaded", provider="s", score=60.0)
    db.subtitle_log_fetch("download", 1, "en", "downloaded", provider="s", score=70.0)
    assert db.subtitle_latest_download_score("download", 1, "en") == 70.0
    assert db.subtitle_latest_download_score("download", 1, "fr") is None
    assert db.subtitle_latest_download_score("download", 2, "en") is None
    db.subtitle_log_fetch("download", 1, "en", "downloaded", provider="s",
                          score=80.0, hi=True)
    assert db.subtitle_latest_download_score("download", 1, "en") == 70.0
    assert db.subtitle_latest_download_score("download", 1, "en", hi=True) == 80.0


# ── upgrade decisions ─────────────────────────────────────────────────────

def test_upgrade_happens_on_sufficient_delta(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0)
    with open(_sidecar(tmp_path), "w") as f:
        f.write("old-srt")
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})      # 90 >= 60 + 15
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats == {"checked": 1, "upgraded": 1, "skipped": 0}
    with open(_sidecar(tmp_path)) as f:
        assert f.read() == "new-srt"                  # sidecar replaced
    assert db.subtitle_have("download", dl_id, "en")  # still 'have'
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["outcome"] == "downloaded" and hist[0]["score"] == 90.0
    assert db.subtitle_quota_used("scripted") == 1    # quota burned like a fetch


def test_no_upgrade_below_delta(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0)
    with open(_sidecar(tmp_path), "w") as f:
        f.write("old-srt")
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 70.0})      # 70 < 60 + 15
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats["upgraded"] == 0 and stats["checked"] == 1
    assert provider.downloads == []                   # nothing downloaded
    with open(_sidecar(tmp_path)) as f:
        assert f.read() == "old-srt"                  # untouched
    # cooldown: the row isn't re-searched on the next pass
    stats2 = worker.run_upgrade_cycle(db, _settings())
    assert stats2["checked"] == 0


def test_no_upgrade_below_min_score(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0)
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings(subtitle_min_score=200.0))
    assert stats["upgraded"] == 0
    assert provider.downloads == []


def test_user_set_rows_never_touched(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0, user_set=True)         # explicit user pick
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats == {"checked": 0, "upgraded": 0, "skipped": 0}
    assert provider.searches == 0                     # not even evaluated


def test_unknown_current_score_skipped(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    db.subtitle_want("download", dl_id, "en")
    db.subtitle_mark("download", dl_id, "en", "have")
    # no fetch history at all → can't prove the candidate is better
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats["skipped"] == 1 and stats["checked"] == 0
    assert provider.searches == 0 and provider.downloads == []


def test_quota_enforced(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0)
    for _ in range(20):
        db.subtitle_quota_bump("scripted")            # exhaust the daily quota
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats["upgraded"] == 0
    assert provider.searches == 0 and provider.downloads == []


def test_upgrade_cycle_runs_inside_worker_cycle(db, tmp_path, monkeypatch):
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    _have_row(db, dl_id, 60.0)
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})

    class FakeFS:
        def list_dir(self, folder):
            return []

        def write_text(self, path, content):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

    stats = worker.run_cycle(db, _settings(), fs=FakeFS())
    assert stats["upgraded"] == 1
    assert stats["upgrade_checked"] == 1


# ── Phase 3: fresh downloads start the upgrade re-check cooldown ──────────

def test_fresh_download_not_upgrade_rechecked_same_cycle(db, tmp_path, monkeypatch):
    # The retry pass just downloaded the best available candidate — the
    # upgrade pass in the same cycle must not waste a search on that row.
    path = _video_file(tmp_path)
    dl_id = _dl(db, path)
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider(candidates=[_cand(hash_match=True)], texts={"new": "srt-data"})
    _patch_registry(monkeypatch, prov)
    stats = worker.run_cycle(db, _settings(), fs=FakeFS())
    assert stats["downloaded"] == 1
    assert stats["upgrade_checked"] == 0 and prov.searches == 1
    row = db.subtitle_get_for_video("download", dl_id)[0]
    assert row["last_upgrade_check_at"] is not None


# ── Phase 3: residue + orphan cleanup in the upgrade loop ────────────────

def _movie_row(db, movie_id=42):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, title, tmdb_id) "
            "VALUES (?, 'plex', 'srv1', 'M', 603)", (movie_id,))
        conn.commit()
    finally:
        conn.close()


def _have_movie_row(db, movie_id, score):
    db.subtitle_want("movie", movie_id, "en")
    db.subtitle_mark("movie", movie_id, "en", "have")
    db.subtitle_log_fetch("movie", movie_id, "en", "downloaded",
                          provider="scripted", candidate_title="old", score=score)


def test_upgrade_deletes_residue_have_row(db, monkeypatch):
    # The override changed after import: the 'have' row's language is no
    # longer wanted — delete it instead of re-searching it every 24h.
    _movie_row(db)
    _have_movie_row(db, 42, 60.0)
    db.subtitle_override_set("movie", 42, ["es"])
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats == {"checked": 0, "upgraded": 0, "skipped": 1}
    assert provider.searches == 0                      # no quota burned
    assert db.subtitle_get_for_video("movie", 42) == []


def test_upgrade_cleans_up_orphan_rows(db, monkeypatch):
    # The movie row is gone: the 'have' row is an orphan — drop it (and log
    # the cleanup) instead of re-searching a file that can't resolve.
    _have_movie_row(db, 4242, 60.0)                    # no movies row for 4242
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    stats = worker.run_upgrade_cycle(db, _settings())
    assert stats["checked"] == 0 and provider.searches == 0
    assert db.subtitle_get_for_video("movie", 4242) == []
    hist = db.subtitle_get_history("movie", 4242)
    assert hist[0]["candidate_title"] == "orphaned row cleaned up"


def test_upgrade_keeps_row_inside_effective_languages(db, tmp_path, monkeypatch):
    # Sanity: a 'have' row whose language is still wanted is evaluated, not
    # deleted — the cleanup only fires outside the effective set.
    _movie_row(db)
    _have_movie_row(db, 42, 60.0)
    db.subtitle_override_set("movie", 42, ["en"])
    provider = ScriptedProvider([_cand("new")], {"new": "new-srt"})
    _patch(monkeypatch, provider, {"new": 90.0})
    # no resolvable file for the movie row → skipped after the checks pass
    stats = worker.run_upgrade_cycle(db, _settings())
    assert db.subtitle_get_for_video("movie", 42) != []
    assert provider.searches == 0                      # no file → no search
    assert stats["skipped"] == 1


def test_upgrade_skips_residue_delete_on_lookup_failure(db, monkeypatch):
    # BLOCK 2, upgrade pass: a transient override-lookup failure reads as
    # UNKNOWN, not "global" — the 'have' row must survive, not be deleted.
    import json as _json
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (11, 'plex', 's11', 'Show', 222)")
        conn.commit()
    finally:
        conn.close()
    db.subtitle_override_set("show", 11, ["fr"])   # the override EXISTS …
    dl_id = _dl(db, None)
    db.update_video_download(dl_id, kind="show", media_source="tmdb",
                             media_id="222",
                             search_ctx=_json.dumps({"season": 1, "episode": 2}))
    db.subtitle_want("download", dl_id, "fr")     # hook-created system row
    db.subtitle_mark("download", dl_id, "fr", "have")
    def _boom(*a, **k):                           # … but the lookup fails
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "subtitle_override_get", _boom)
    provider = ScriptedProvider([], {})
    _patch_registry(monkeypatch, provider)
    worker.run_upgrade_cycle(db, _settings(subtitle_langs="en"), FakeFS())
    rows = db.subtitle_get_for_video("download", dl_id)
    assert [r["language"] for r in rows] == ["fr"]  # kept, not deleted
    assert provider.searches == 0


def _show_episode_with_file(db, tmp_path):
    """Show 11 / S01E02 ingested under the tv root; returns (ep_id, abs_path)."""
    import os as _os
    db.set_setting("tv_path", str(tmp_path))
    rel = _os.path.join("Show", "S01E02.mkv")
    abs_path = str(tmp_path / "Show" / "S01E02.mkv")
    _os.makedirs(_os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(_os.urandom(200_000))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title) "
                     "VALUES (11, 'p', 's', 'Show')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) "
                     "VALUES (21, 11, 1)")
        conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, "
                     "episode_number) VALUES (31, 11, 21, 1, 2)")
        conn.execute("INSERT INTO media_files (episode_id, relative_path) "
                     "VALUES (31, ?)", (rel,))
        conn.commit()
    finally:
        conn.close()
    return 31, abs_path


def test_upgrade_rekeys_before_orphan_delete_when_history_has_file(db, tmp_path,
                                                                   monkeypatch):
    # BLOCK 3: the download row is gone (finished-download cleanup) but the
    # file was ingested before the lazy re-key ran. The permanent download
    # history still has the final placed path — the upgrade pass must re-key
    # onto the library episode row instead of deleting the 'have' row.
    ep_id, abs_path = _show_episode_with_file(db, tmp_path)
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "completed",
                                   "media_id": "222", "media_source": "tmdb",
                                   "search_ctx": "{}"})
    db.update_video_download(dl_id, dest_path=abs_path)
    db.subtitle_want("download", dl_id, "en")
    db.subtitle_mark("download", dl_id, "en", "have")
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded",
                          provider="scripted", candidate_title="old", score=60.0)
    db.record_download_history(db.get_video_download(dl_id))
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM video_downloads WHERE id=?", (dl_id,))
        conn.commit()
    finally:
        conn.close()
    provider = ScriptedProvider([], {})
    _patch_registry(monkeypatch, provider)
    worker.run_upgrade_cycle(db, _settings(subtitle_langs="en"), FakeFS())
    assert db.subtitle_get_for_video("download", dl_id) == []
    rows = db.subtitle_get_for_video("episode", ep_id)
    assert [(r["language"], r["status"]) for r in rows] == [("en", "have")]
    assert provider.searches == 0  # no baseline under the new key → skipped


def test_upgrade_still_deletes_true_orphans(db, monkeypatch):
    # No download row, no history, no library file: a genuine orphan is still
    # cleaned up (the re-key attempt finds nothing to re-key onto).
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "completed",
                                   "media_id": "222", "media_source": "tmdb",
                                   "search_ctx": "{}"})
    db.subtitle_want("download", dl_id, "en")
    db.subtitle_mark("download", dl_id, "en", "have")
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM video_downloads WHERE id=?", (dl_id,))
        conn.commit()
    finally:
        conn.close()
    provider = ScriptedProvider([], {})
    _patch_registry(monkeypatch, provider)
    worker.run_upgrade_cycle(db, _settings(subtitle_langs="en"), FakeFS())
    assert db.subtitle_get_for_video("download", dl_id) == []
    hist = db.subtitle_get_history("download", dl_id)
    assert hist[0]["candidate_title"] == "orphaned row cleaned up"
