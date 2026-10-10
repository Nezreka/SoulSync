"""subtitle_wanted: the "Replace Bazarr" wanted queue + the import-hook integration.

DB layer: want/mark/get_wanted/get_for_video/have state transitions.
Hook layer: core.video.download_monitor.write_subtitles_for with a stubbed
fetch_subtitle_detailed — monkeypatched on the providers module (the hook does a
function-level ``from ... import fetch_subtitle_detailed``, so the double is
picked up at call time; the global provider registry is never mutated).
"""

from __future__ import annotations

import json

import pytest

from core.video import organization
from core.video.download_monitor import _episode_row, _wanted_video_key, write_subtitles_for
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class FakeFS:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.texts = {}          # path -> content

    def list_dir(self, path):
        return list(self.existing)

    def write_text(self, path, content):
        # Normalize separators: on Windows os.path.join produces backslashes
        # while the assertions use POSIX forward slashes.
        self.texts[str(path).replace("\\", "/")] = content


def _dl(**kw):
    d = {"id": 7, "kind": "movie", "media_source": "tmdb", "media_id": "603",
         "search_ctx": "{}"}
    d.update(kw)
    return d


def _stub_fetch(monkeypatch, results):
    """results: lang -> srt text (None = miss). Returns (calls,).

    Stubs fetch_subtitle_detailed (the hook's call) with the 4-tuple contract:
    (text, provider_id, candidate, score)."""
    calls = []

    def fake(query, provider_order, get_setting, on_download=None):
        calls.append((query, list(provider_order)))
        text = results.get(query.language)
        if text is None:
            return None, None, None, 0.0
        if callable(on_download):
            on_download("stub")
        return text, "stub", None, 100.0

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", fake)
    return calls


# ── wanted-table state transitions ──────────────────────────────────────────

def test_want_creates_row_with_defaults(db):
    row_id = db.subtitle_want("download", 7, "en")
    assert isinstance(row_id, int)
    rows = db.subtitle_get_for_video("download", 7)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == row_id
    assert row["status"] == "wanted"
    assert row["attempts"] == 0
    assert row["last_attempt_at"] is None
    assert row["hi"] == 0 and row["forced"] == 0


def test_want_is_idempotent_on_unique(db):
    first = db.subtitle_want("download", 7, "en")
    second = db.subtitle_want("download", 7, "EN")   # language normalised
    assert first == second
    assert len(db.subtitle_get_for_video("download", 7)) == 1


def test_want_distinguishes_language_and_flags(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_want("download", 7, "es")
    db.subtitle_want("download", 7, "en", hi=True)
    db.subtitle_want("download", 7, "en", forced=True)
    assert len(db.subtitle_get_for_video("download", 7)) == 4
    assert not db.subtitle_have("download", 7, "en", hi=True)


def test_mark_have_and_failed_record_an_attempt(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_mark("download", 7, "en", "have")
    assert db.subtitle_have("download", 7, "en")
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "have" and row["attempts"] == 1
    assert row["last_attempt_at"] is not None

    db.subtitle_want("download", 7, "es")
    db.subtitle_mark("download", 7, "es", "failed")
    assert not db.subtitle_have("download", 7, "es")
    row = [r for r in db.subtitle_get_for_video("download", 7) if r["language"] == "es"][0]
    assert row["status"] == "failed" and row["attempts"] == 1


def test_mark_wanted_requeues_without_bumping_attempts(db):
    db.subtitle_want("download", 7, "en")
    db.subtitle_mark("download", 7, "en", "failed")
    db.subtitle_mark("download", 7, "en", "wanted")
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "wanted" and row["attempts"] == 1


def test_mark_have_with_count_attempt_false_leaves_attempts_alone(db):
    # the sidecar was already on disk: 'have' is honest, but no fetch ran, so
    # attempts must not move — Phase 2's backoff measures FETCH attempts.
    db.subtitle_want("download", 7, "en")
    db.subtitle_mark("download", 7, "en", "have", count_attempt=False)
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "have"
    assert row["attempts"] == 0
    assert row["last_attempt_at"] is None
    # a real fetch afterwards still counts
    db.subtitle_mark("download", 7, "en", "failed")
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["attempts"] == 1 and row["last_attempt_at"] is not None


def test_get_for_video_returns_rows_in_insertion_order(db):
    # insertion order = the user's priority order (the settings UI promises
    # langs "in priority order"); with the ~20/day quota the order decides
    # which languages actually get fetched, so this must NOT be alphabetical.
    db.subtitle_want("download", 7, "es")
    db.subtitle_want("download", 7, "en")
    langs = [r["language"] for r in db.subtitle_get_for_video("download", 7)]
    assert langs == ["es", "en"]   # alphabetical would give ["en", "es"]


def test_get_wanted_returns_only_wanted_oldest_first(db):
    db.subtitle_want("download", 1, "en")
    db.subtitle_want("download", 2, "en")
    db.subtitle_want("download", 3, "en")
    db.subtitle_mark("download", 2, "en", "have")
    db.subtitle_mark("download", 3, "en", "failed")
    wanted = db.subtitle_get_wanted()
    assert [(r["video_kind"], r["video_id"]) for r in wanted] == [("download", 1)]
    # limit + a re-queued row
    db.subtitle_mark("download", 3, "en", "wanted")
    assert len(db.subtitle_get_wanted(limit=1)) == 1
    assert {r["video_id"] for r in db.subtitle_get_wanted(limit=10)} == {1, 3}


def test_episode_id_for_resolves_and_misses(db):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (title, tmdb_id) VALUES ('S', 1396)")
        show_id = conn.execute("SELECT id FROM shows").fetchone()["id"]
        conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 1)", (show_id,))
        season_id = conn.execute("SELECT id FROM seasons").fetchone()["id"]
        conn.execute(
            "INSERT INTO episodes (show_id, season_id, season_number, episode_number, title) "
            "VALUES (?, ?, 1, 2, 'E2')", (show_id, season_id))
        ep_id = conn.execute("SELECT id FROM episodes").fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    assert db.episode_id_for(show_id, 1, 2) == ep_id
    assert db.episode_id_for(show_id, 1, 3) is None      # no such episode
    assert db.episode_id_for(show_id, "x", 2) is None    # unparseable season


# ── import hook ─────────────────────────────────────────────────────────────

def test_hook_creates_wanted_row_before_fetching(db, monkeypatch):
    seen = {}

    def fake(query, provider_order, get_setting, on_download=None):
        rows = db.subtitle_get_for_video("download", 7)
        seen["rows"] = [(r["language"], r["status"]) for r in rows]
        return "1\n00:00:00,000 --> 00:00:01,000\nHi\n", "stub", None, 100.0

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", fake)
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)
    assert seen["rows"] == [("en", "wanted")]   # durable row existed pre-fetch
    assert db.subtitle_have("download", 7, "en")


def test_hook_writes_sidecar_and_marks_have(db, monkeypatch):
    calls = _stub_fetch(monkeypatch, {"en": "srt-bytes"})
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)
    assert fs.texts == {"/lib/M (2020)/M (2020).en.srt": "srt-bytes"}
    assert db.subtitle_have("download", 7, "en")
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["attempts"] == 1 and row["last_attempt_at"] is not None
    # the query carried the media identity through
    assert calls[0][0].identity == {"tmdb_id": 603}
    assert calls[0][0].language == "en"


def test_hook_marks_failed_on_miss(db, monkeypatch):
    # a provider was configured and tried → the miss is a real 'failed' row
    db.set_setting("opensubtitles_api_key", "test-key")
    _stub_fetch(monkeypatch, {})   # every language misses
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en,es"}, fs)
    assert fs.texts == {}
    rows = {r["language"]: r for r in db.subtitle_get_for_video("download", 7)}
    assert set(rows) == {"en", "es"}
    assert all(r["status"] == "failed" and r["attempts"] == 1 for r in rows.values())
    # durable misses: Phase 2's loop sees them via get_wanted after re-queue
    db.subtitle_mark("download", 7, "en", "wanted")
    assert [r["video_id"] for r in db.subtitle_get_wanted()] == [7]


def test_hook_bumps_quota_on_successful_download(db, monkeypatch):
    # Phase 2: the import hook records the download against the provider's
    # daily quota so the worker's cap sees what the hook spent.
    calls = _stub_fetch(monkeypatch, {"en": "srt-bytes"})
    fs = FakeFS()
    assert db.subtitle_quota_used("stub") == 0
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)
    assert db.subtitle_quota_used("stub") == 1


def test_hook_never_raises_when_fetch_explodes(db, monkeypatch):
    def boom(query, provider_order, get_setting, on_download=None):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", boom)
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)   # must not raise
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "wanted" and row["attempts"] == 0


def test_hook_uses_existing_rows_as_per_item_override(db, monkeypatch):
    # only user_set=1 rows count as an override — rows the hook itself
    # created on a previous import (user_set=0) are residue, not intent
    db.subtitle_want("download", 7, "es", user_set=True)   # user-set override
    calls = _stub_fetch(monkeypatch, {"es": "srt"})
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en,fr"}, fs)
    assert [q.language for q, _ in calls] == ["es"]     # global list ignored
    assert fs.texts == {"/lib/M.es.srt": "srt"}
    assert db.subtitle_have("download", 7, "es")
    assert len(db.subtitle_get_for_video("download", 7)) == 1   # no en/fr rows


def test_hook_skips_youtube_and_unidentified(db, monkeypatch):
    calls = _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    write_subtitles_for(db, _dl(kind="youtube"), "/lib/v.mkv",
                        {"subtitle_langs": "en"}, fs)
    write_subtitles_for(db, _dl(media_id=None), "/lib/v.mkv",
                        {"subtitle_langs": "en"}, fs)
    assert calls == [] and fs.texts == {}
    assert db.subtitle_get_wanted() == []


def test_hook_skips_fetch_when_sidecar_already_on_disk(db, monkeypatch):
    calls = _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS(existing=["M (2020).en.srt"])
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)
    assert calls == []                    # no fetch needed
    assert fs.texts == {}
    assert db.subtitle_have("download", 7, "en")   # truthfully 'have'
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["attempts"] == 0 and row["last_attempt_at"] is None  # no fetch ran


def test_hook_prefers_library_movie_id_for_owned_regrab(db, monkeypatch):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, title, tmdb_id) "
            "VALUES (42, 'plex', 'srv1', 'M', 603)")
        conn.commit()
    finally:
        conn.close()
    _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    dl = _dl(id=9, media_source="library", media_id="42")
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert db.subtitle_have("movie", 42, "en")          # real library id, not dl id
    assert db.subtitle_get_for_video("download", 9) == []


def test_hook_resolves_library_episode_id(db, monkeypatch):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (title, tmdb_id) VALUES ('S', 1396)")
        show_id = conn.execute("SELECT id FROM shows").fetchone()["id"]
        conn.execute("INSERT INTO seasons (show_id, season_number) VALUES (?, 1)", (show_id,))
        season_id = conn.execute("SELECT id FROM seasons").fetchone()["id"]
        conn.execute(
            "INSERT INTO episodes (show_id, season_id, season_number, episode_number, title) "
            "VALUES (?, ?, 1, 2, 'E2')", (show_id, season_id))
        ep_id = conn.execute("SELECT id FROM episodes").fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    dl = _dl(id=9, kind="show", media_source="library", media_id=str(show_id),
             search_ctx=json.dumps({"season": 1, "episode": 2}))
    write_subtitles_for(db, dl, "/lib/S/Season 1/S - S01E02.mkv",
                        {"subtitle_langs": "en"}, fs)
    assert db.subtitle_have("episode", ep_id, "en")
    assert db.subtitle_get_for_video("download", 9) == []


def test_hook_falls_back_to_download_key_when_episode_row_missing(db, monkeypatch):
    # The show row exists (identity resolves) but this episode was never
    # scanner-ingested — the download row is the only durable key available.
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (title, tmdb_id) VALUES ('S', 1396)")
        show_id = conn.execute("SELECT id FROM shows").fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    dl = _dl(id=9, kind="show", media_source="library", media_id=str(show_id),
             search_ctx=json.dumps({"season": 1, "episode": 2}))
    write_subtitles_for(db, dl, "/lib/S.mkv", {"subtitle_langs": "en"}, fs)
    assert db.subtitle_have("download", 9, "en")   # no episode row -> download key


def test_hook_without_any_key_stays_fire_and_forget(db, monkeypatch):
    _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    dl = _dl()
    del dl["id"]   # like the manual-import path before it threaded the id
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert fs.texts == {"/lib/M.en.srt": "srt"}   # still fetched, legacy behaviour
    assert db.subtitle_get_wanted() == []          # but nothing durable


def test_hook_passes_parsed_provider_order(db, monkeypatch):
    # provider order lives in the organization blob (what GET /organization
    # serves and POST persists), not a standalone setting.
    calls = _stub_fetch(monkeypatch, {"en": "srt"})
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls[0][1] == ["opensubtitles"]                    # default when unset

    organization.save(db, {"subtitle_provider_order": ["opensubtitles", "podnapisi"]})
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls[1][1] == ["opensubtitles", "podnapisi"]
    assert organization.load(db)["subtitle_provider_order"] == \
        '["opensubtitles", "podnapisi"]'                        # stored as a JSON string

    organization.save(db, {"subtitle_provider_order": "garbage {{{"})
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls[2][1] == ["opensubtitles"]                    # fallback on garbage

    organization.save(db, {"subtitle_provider_order": ["ok", 42, ""]})
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls[3][1] == ["ok"]                               # non-strings dropped


# ── download_subtitles default flip (Broque's call: on for new imports) ─────

def test_download_subtitles_defaults_to_true_but_explicit_values_win():
    assert organization.default_settings()["download_subtitles"] is True
    assert organization.normalize({})["download_subtitles"] is True
    assert organization.normalize({"download_subtitles": False})["download_subtitles"] is False
    assert organization.normalize({"download_subtitles": True})["download_subtitles"] is True




def test_hook_threshold_tunable_via_settings_blob(db, monkeypatch):
    # R2 MAJOR 1: subtitle_min_score lives in the organization blob — the hook
    # must honor it, not just the top-level default.
    calls = _stub_fetch(monkeypatch, {"en": "srt-bytes"})
    fs = FakeFS()
    # A weak candidate: with the default 24.0 it downloads; at 90.0 it must not.
    weak_calls = []
    def weak_fetch(query, provider_order, get_setting, on_download=None):
        weak_calls.append(get_setting("subtitle_min_score", None))
        return None, None, None, 0.0
    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", weak_fetch)
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en", "subtitle_min_score": 90.0}, fs)
    assert weak_calls == [90.0], "hook did not read the blob's subtitle_min_score"

# ── Phase 2: pack episodes get per-episode durable rows ─────────────────────

def test_pack_episode_child_row_gets_own_wanted_rows(db, monkeypatch):
    # Phase 2: the pack importer calls write_subtitles_for AFTER _record_pack_episode
    # with the child's own download id (pack_episode marker removed) — each
    # episode gets its own ('download', <child_id>) row set.
    calls = _stub_fetch(monkeypatch, {"en": None})  # miss → rows stay, marked failed
    child_dl = {"id": 100, "kind": "show", "media_source": "tmdb", "media_id": "1396",
                "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 1})}
    assert _wanted_video_key(db, child_dl) == ("download", 100)
    fs = FakeFS()
    write_subtitles_for(db, child_dl, "/lib/Show/Season 1/Show - S01E01.mkv",
                        {"subtitle_langs": "en"}, fs)
    rows = db.subtitle_get_for_video("download", 100)
    assert len(rows) == 1 and rows[0]["language"] == "en"
    # No provider configured in this test → no phantom attempt: the row stays
    # 'wanted' (still retryable once a key exists).
    assert rows[0]["status"] == "wanted"
    # A sibling episode's child row is independent.
    sib_dl = dict(child_dl, id=101,
                  search_ctx=json.dumps({"scope": "episode", "season": 1, "episode": 2}))
    write_subtitles_for(db, sib_dl, "/lib/Show/Season 1/Show - S01E02.mkv",
                        {"subtitle_langs": "en"}, fs)
    assert len(db.subtitle_get_for_video("download", 101)) == 1
    assert len(db.subtitle_get_for_video("download", 100)) == 1  # untouched


# ── review round 2 regressions ──────────────────────────────────────────────

def test_hook_pack_episodes_do_not_share_wanted_rows(db, monkeypatch):
    # M1: two episodes imported from one season pack both carry the pack row's
    # id — keying on it would make every episode share ONE
    # ('download', <pack_id>) row set (misses recorded as each other's 'have',
    # attempts conflated). The pack-episode marker forces a None key, so no
    # rows are created at all, while the per-episode fetch still runs.
    calls = _stub_fetch(monkeypatch, {"en": "srt-bytes"})
    pack_dl = {"id": 42, "kind": "show", "media_source": "tmdb", "media_id": "1396",
               "search_ctx": json.dumps({"scope": "season", "season": 1})}
    ep1 = _episode_row(pack_dl, 1, 1, "/dl/Show.S01E01.mkv", 100)
    ep2 = _episode_row(pack_dl, 1, 2, "/dl/Show.S01E02.mkv", 200)
    assert ep1["pack_episode"] is True and ep2["pack_episode"] is True
    assert _wanted_video_key(db, ep1) is None
    assert _wanted_video_key(db, ep2) is None
    fs = FakeFS()
    write_subtitles_for(db, ep1, "/lib/Show/Season 1/Show - S01E01.mkv",
                        {"subtitle_langs": "en"}, fs)
    write_subtitles_for(db, ep2, "/lib/Show/Season 1/Show - S01E02.mkv",
                        {"subtitle_langs": "en"}, fs)
    assert db.subtitle_get_wanted() == []                 # no rows created
    assert db.subtitle_get_for_video("download", 42) == []  # not even the pack's
    assert fs.texts == {
        "/lib/Show/Season 1/Show - S01E01.en.srt": "srt-bytes",
        "/lib/Show/Season 1/Show - S01E02.en.srt": "srt-bytes",
    }
    assert len(calls) == 2                                # fetch still ran per episode


def test_hook_miss_with_no_provider_configured_leaves_wanted(db, monkeypatch):
    # m1: fetch_subtitle returns None both for "tried and missed" and for
    # "nothing configured" — a keyless setup must not accumulate phantom
    # attempts. The row stays 'wanted' with attempts untouched so Phase 2
    # retries it once a key is added.
    _stub_fetch(monkeypatch, {})   # every language misses (no key configured)
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "wanted" and row["attempts"] == 0
    assert db.subtitle_get_wanted()[0]["language"] == "en"   # Phase 2 still sees it


def test_hook_refreshes_languages_when_settings_change(db, monkeypatch):
    # m3: rows the hook itself created on a previous import (user_set=0) are
    # residue, not an override — adding a language to subtitle_langs must
    # take effect on re-grab.
    db.subtitle_want("download", 7, "en")          # residue from a previous import
    calls = _stub_fetch(monkeypatch, {"de": "srt"})
    fs = FakeFS()
    write_subtitles_for(db, _dl(), "/lib/M.mkv", {"subtitle_langs": "en,de"}, fs)
    rows = {r["language"]: r for r in db.subtitle_get_for_video("download", 7)}
    assert set(rows) == {"en", "de"}               # new language got a wanted row
    assert rows["de"]["user_set"] == 0
    assert rows["de"]["status"] == "have"          # ...and was fetched
    assert {q.language for q, _ in calls} == {"en", "de"}


def test_hook_marks_failed_when_sidecar_write_fails(db, monkeypatch):
    # m4: the fetch succeeded (quota burned) but fs.write_text raised — the
    # row goes 'failed' WITH the attempt counted, or Phase 2 retries blind
    # and burns quota again. The hook itself must not raise.
    db.set_setting("opensubtitles_api_key", "test-key")
    _stub_fetch(monkeypatch, {"en": "srt-bytes"})   # fetch succeeds...

    class FailingFS(FakeFS):
        def write_text(self, path, content):
            raise OSError("disk is full")

    fs = FailingFS()
    write_subtitles_for(db, _dl(), "/lib/M (2020)/M (2020).mkv",
                        {"subtitle_langs": "en"}, fs)   # must not raise
    assert fs.texts == {}
    row = db.subtitle_get_for_video("download", 7)[0]
    assert row["status"] == "failed" and row["attempts"] == 1


def test_want_user_set_marks_override_rows(db):
    # m3 plumbing: the Phase 4 UI writes overrides with user_set=1; the hook
    # creates user_set=0 rows by default.
    hook_row = db.subtitle_want("download", 7, "en")
    ui_row = db.subtitle_want("download", 7, "es", user_set=True)
    assert hook_row != ui_row
    rows = {r["language"]: r for r in db.subtitle_get_for_video("download", 7)}
    assert rows["en"]["user_set"] == 0
    assert rows["es"]["user_set"] == 1


def test_old_db_without_user_set_column_gets_migrated(tmp_path):
    # m3: a tester DB created before the user_set column existed (old CREATE
    # TABLE, no column) must still work — _ensure_columns ALTERs it in.
    import sqlite3

    path = str(tmp_path / "old_video_library.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE subtitle_wanted ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "video_kind TEXT NOT NULL, video_id INTEGER NOT NULL,"
        "language TEXT NOT NULL, hi INTEGER NOT NULL DEFAULT 0,"
        "forced INTEGER NOT NULL DEFAULT 0,"
        "status TEXT NOT NULL DEFAULT 'wanted',"
        "attempts INTEGER NOT NULL DEFAULT 0,"
        "last_attempt_at TEXT,"
        "created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        "UNIQUE(video_kind, video_id, language, hi, forced))")
    conn.commit()
    conn.close()
    db = VideoDatabase(database_path=path)   # init runs _ensure_columns
    row_id = db.subtitle_want("download", 7, "en")   # must not raise "no such column"
    assert isinstance(row_id, int)
    rows = db.subtitle_get_for_video("download", 7)
    assert len(rows) == 1 and rows[0]["user_set"] == 0
