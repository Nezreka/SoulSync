"""Phase 3: subtitle_overrides table + effective_subtitle_languages.

Covers: set/get/clear round-trips, language validation, invalid kind
rejection, override-beats-global, episode-inherits-show-override, and the
global fallback paths.
"""

from __future__ import annotations

import pytest

from core.video.subtitles import effective_subtitle_languages, validate_lang_codes
from database.video_database import SCHEMA_VERSION, VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_schema_version_is_52():
    assert SCHEMA_VERSION == 52


def test_set_get_roundtrip(db):
    assert db.subtitle_override_set("movie", 7, ["en", "es"]) == ["en", "es"]
    assert db.subtitle_override_get("movie", 7) == ["en", "es"]


def test_set_normalizes_languages(db):
    assert db.subtitle_override_set("show", 3, ["EN", "es", "en"]) == ["en", "es"]
    assert db.subtitle_override_get("show", 3) == ["en", "es"]


def test_set_overwrites(db):
    db.subtitle_override_set("movie", 7, ["en"])
    db.subtitle_override_set("movie", 7, ["fr", "de"])
    assert db.subtitle_override_get("movie", 7) == ["fr", "de"]


def test_get_missing_returns_none(db):
    assert db.subtitle_override_get("movie", 999) is None
    assert db.subtitle_override_get("show", 999) is None


def test_clear(db):
    db.subtitle_override_set("movie", 7, ["en"])
    assert db.subtitle_override_clear("movie", 7) is True
    assert db.subtitle_override_get("movie", 7) is None


def test_clear_missing_returns_false(db):
    assert db.subtitle_override_clear("show", 4242) is False


def test_movie_and_show_namespaces_are_independent(db):
    db.subtitle_override_set("movie", 7, ["en"])
    db.subtitle_override_set("show", 7, ["es"])
    assert db.subtitle_override_get("movie", 7) == ["en"]
    assert db.subtitle_override_get("show", 7) == ["es"]


@pytest.mark.parametrize("method", ["set", "get", "clear"])
def test_invalid_kind_rejected(db, method):
    with pytest.raises(ValueError):
        if method == "set":
            db.subtitle_override_set("episode", 1, ["en"])
        elif method == "get":
            db.subtitle_override_get("episode", 1)
        else:
            db.subtitle_override_clear("download", 1)


@pytest.mark.parametrize("bad", [[], ["english"], ["e"], ["en1"], ["en", ""], "en", None, [123]])
def test_invalid_languages_rejected(db, bad):
    with pytest.raises(ValueError):
        db.subtitle_override_set("movie", 1, bad)


def test_validate_lang_codes():
    assert validate_lang_codes(["EN", "es", "EN"]) == ["en", "es"]
    assert validate_lang_codes(["pt-BR", "zh-CN", "zh-TW"]) == ["pt-br", "zh-cn", "zh-tw"]
    assert validate_lang_codes(["sr-latn"]) == ["sr-latn"]
    for bad in ([], ["e"], ["engl"], ["en-"], ["en-b"], ["en-toolong"], ["en", ""], "en", None, [42]):
        with pytest.raises(ValueError):
            validate_lang_codes(bad)


def test_validate_lang_codes_rejects_huge_list():
    # N2: an unbounded override list would materialize one wanted row per
    # code per import — cap it.
    import itertools
    from core.video.subtitles import MAX_LANG_CODES
    codes = [a + b for a, b in itertools.product("abcdef", "abcdef")]
    assert len(codes) > MAX_LANG_CODES
    assert validate_lang_codes(codes[:MAX_LANG_CODES]) == codes[:MAX_LANG_CODES]
    with pytest.raises(ValueError):
        validate_lang_codes(codes[:MAX_LANG_CODES + 1])


def _settings(**kw):
    s = {"subtitle_langs": "en"}
    s.update(kw)
    return s


def test_effective_override_beats_global_for_movie(db):
    db.subtitle_override_set("movie", 7, ["es", "fr"])
    assert effective_subtitle_languages(db, "movie", 7, _settings()) == ["es", "fr"]


def test_effective_falls_back_to_global_without_override(db):
    assert effective_subtitle_languages(db, "movie", 7, _settings()) == ["en"]
    assert effective_subtitle_languages(
        db, "movie", 7, _settings(subtitle_langs="en, de")) == ["en", "de"]


def _show_with_episode(db):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title) "
                     "VALUES (11, 't', 's11', 'Show')")
        conn.execute("INSERT INTO seasons (id, show_id, season_number) "
                     "VALUES (21, 11, 1)")
        conn.execute("INSERT INTO episodes (id, show_id, season_id, season_number, "
                     "episode_number) VALUES (31, 11, 21, 1, 2)")
        conn.commit()
    finally:
        conn.close()
    return 31


def test_effective_episode_inherits_show_override(db):
    ep_id = _show_with_episode(db)
    assert db.subtitle_show_for_episode(ep_id) == 11
    db.subtitle_override_set("show", 11, ["es"])
    assert effective_subtitle_languages(db, "episode", ep_id, _settings()) == ["es"]


def test_effective_episode_without_show_override_uses_global(db):
    ep_id = _show_with_episode(db)
    assert effective_subtitle_languages(db, "episode", ep_id, _settings()) == ["en"]


def test_effective_episode_without_show_row_uses_global(db):
    assert effective_subtitle_languages(db, "episode", 424242, _settings()) == ["en"]


def test_effective_download_kind_always_global(db):
    db.subtitle_override_set("movie", 7, ["es"])
    assert effective_subtitle_languages(db, "download", 7, _settings()) == ["en"]


def test_effective_download_episode_resolves_show_override(db):
    # A download-kind row for a fresh episode grab sees the show override
    # through the download's show TMDB id (the episode row doesn't exist yet).
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (11, 'plex', 's11', 'Show', 1396)")
        conn.commit()
    finally:
        conn.close()
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "completed",
                                   "media_id": "1396", "media_source": "tmdb",
                                   "search_ctx": "{}"})
    db.subtitle_override_set("show", 11, ["de"])
    assert effective_subtitle_languages(db, "download", dl_id, _settings()) == ["de"]


def test_effective_download_episode_without_show_override_uses_global(db):
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "completed",
                                   "media_id": "1396", "media_source": "tmdb",
                                   "search_ctx": "{}"})
    assert effective_subtitle_languages(db, "download", dl_id, _settings()) == ["en"]


def test_effective_download_movie_kind_ignores_show_override(db):
    # A fresh movie grab can never resolve a show override — global only.
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (11, 'plex', 's11', 'Show', 1396)")
        conn.commit()
    finally:
        conn.close()
    dl_id = db.add_video_download({"kind": "movie", "title": "M", "status": "completed",
                                   "media_id": "1396", "media_source": "tmdb",
                                   "search_ctx": "{}"})
    db.subtitle_override_set("show", 11, ["de"])
    assert effective_subtitle_languages(db, "download", dl_id, _settings()) == ["en"]


def test_effective_raises_lookup_error_on_db_failure(db, monkeypatch):
    # A transient override-lookup failure is UNKNOWN, not "global": the
    # dedicated exception lets deletion callers skip instead of degrading.
    from core.video.subtitles import SubtitleLookupError

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "subtitle_override_get", _boom)
    with pytest.raises(SubtitleLookupError):
        effective_subtitle_languages(db, "movie", 7, _settings())


# ── import-hook + worker wiring ────────────────────────────────────────────

class FakeFS:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.texts = {}

    def list_dir(self, path):
        return list(self.existing)

    def write_text(self, path, content):
        self.texts[str(path).replace("\\", "/")] = content


def _dl(**kw):
    d = {"id": 7, "kind": "movie", "media_source": "tmdb", "media_id": "603",
         "search_ctx": "{}"}
    d.update(kw)
    return d


def _stub_fetch(monkeypatch, results):
    """Stub fetch_subtitle_detailed: lang -> srt text (None = miss)."""
    calls = []

    def fake(query, provider_order, get_setting, on_download=None):
        calls.append(query.language)
        text = results.get(query.language)
        if text is None:
            return None, None, None, 0.0
        if callable(on_download):
            on_download("stub")
        return text, "stub", None, 100.0

    monkeypatch.setattr("core.video.subtitles.providers.fetch_subtitle_detailed", fake)
    return calls


def _movie_row(db, movie_id=42, tmdb_id=603):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO movies (id, server_source, server_id, title, tmdb_id) "
            "VALUES (?, 'plex', 'srv1', 'M', ?)", (movie_id, tmdb_id))
        conn.commit()
    finally:
        conn.close()


def test_hook_table_override_beats_global(db, monkeypatch):
    from core.video.download_monitor import write_subtitles_for
    _movie_row(db)
    db.subtitle_override_set("movie", 42, ["es"])
    calls = _stub_fetch(monkeypatch, {"es": "srt-es"})
    fs = FakeFS()
    dl = _dl(id=9, media_source="library", media_id="42")
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en,fr"}, fs)
    assert calls == ["es"]                       # global en/fr ignored
    assert fs.texts == {"/lib/M.es.srt": "srt-es"}
    rows = db.subtitle_get_for_video("movie", 42)
    assert {r["language"] for r in rows} == {"es"}


def test_hook_episode_inherits_show_override(db, monkeypatch):
    import json as _json
    from core.video.download_monitor import write_subtitles_for
    ep_id = _show_with_episode(db)
    conn = db._get_connection()
    try:
        show_id = conn.execute("SELECT id FROM shows").fetchone()["id"]
    finally:
        conn.close()
    assert ep_id is not None and show_id == 11
    db.subtitle_override_set("show", 11, ["de"])
    calls = _stub_fetch(monkeypatch, {"de": "srt-de"})
    fs = FakeFS()
    dl = _dl(id=9, kind="show", media_source="library", media_id="11",
             search_ctx=_json.dumps({"season": 1, "episode": 2}))
    # identity needs a tmdb id on the show for the fetch to run
    conn = db._get_connection()
    try:
        conn.execute("UPDATE shows SET tmdb_id=1396 WHERE id=11")
        conn.commit()
    finally:
        conn.close()
    write_subtitles_for(db, dl, "/lib/S/S - S01E02.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["de"]
    assert db.subtitle_have("episode", ep_id, "de")


def test_hook_fresh_episode_grab_inherits_show_override(db, monkeypatch):
    # Fresh TMDB episode grab: no library episode row exists yet (the scanner
    # ingests it after the file lands), so wanted rows key on the download —
    # but the per-show override must still apply via the show's TMDB id.
    import json as _json
    from core.video.download_monitor import write_subtitles_for
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (11, 'plex', 's11', 'Show', 1396)")
        conn.commit()
    finally:
        conn.close()
    db.subtitle_override_set("show", 11, ["de"])
    calls = _stub_fetch(monkeypatch, {"de": "srt-de"})
    fs = FakeFS()
    # The monitor hands the hook the real DB row (via get_active_video_downloads),
    # so the hook re-reads the same row the worker's residue check will see —
    # one code path, no in-memory identity.
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "completed",
                                   "media_source": "tmdb", "media_id": "1396",
                                   "search_ctx": _json.dumps({"season": 1, "episode": 2})})
    dl = db.get_video_download(dl_id)
    write_subtitles_for(db, dl, "/lib/S/S - S01E02.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["de"]                                   # global en ignored
    rows = db.subtitle_get_for_video("download", dl_id)
    assert {r["language"] for r in rows} == {"de"}


def test_hook_fresh_episode_grab_falls_back_to_global(db, monkeypatch):
    # No override on the show (or the show isn't in the library yet) →
    # global languages, not a crash or an empty fetch.
    import json as _json
    from core.video.download_monitor import write_subtitles_for
    calls = _stub_fetch(monkeypatch, {"en": "srt-en"})
    fs = FakeFS()
    dl = _dl(id=9, kind="show", media_source="tmdb", media_id="1396",
             search_ctx=_json.dumps({"season": 1, "episode": 2}))
    write_subtitles_for(db, dl, "/lib/S/S - S01E02.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["en"]
    rows = db.subtitle_get_for_video("download", 9)
    assert {r["language"] for r in rows} == {"en"}


def test_hook_user_set_rows_still_win_over_table_override(db, monkeypatch):
    # Explicit per-video picks (user_set=1) outrank the table override.
    from core.video.download_monitor import write_subtitles_for
    _movie_row(db)
    db.subtitle_override_set("movie", 42, ["es"])
    db.subtitle_want("movie", 42, "fr", user_set=True)
    calls = _stub_fetch(monkeypatch, {"fr": "srt-fr"})
    fs = FakeFS()
    dl = _dl(id=9, media_source="library", media_id="42")
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["fr"]
    assert db.subtitle_have("movie", 42, "fr")


def test_hook_no_override_uses_global(db, monkeypatch):
    from core.video.download_monitor import write_subtitles_for
    _movie_row(db)
    calls = _stub_fetch(monkeypatch, {"en": "srt-en"})
    fs = FakeFS()
    dl = _dl(id=9, media_source="library", media_id="42")
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["en"]


def test_manual_import_persisted_identity_survives_worker_residue_check(db, monkeypatch):
    # BLOCK 1: the manual-import endpoint persists the user's corrected
    # identity onto the download row BEFORE the subtitle hook runs. The hook
    # and the worker then share one code path (the DB row) — the worker's
    # residue check must not delete the hook's rows.
    #
    # Scenario: show tmdb 222 has override ['fr']; the download row still
    # carries the stale grab identity tmdb 111; global is en.
    import json as _json
    from core.video.download_monitor import write_subtitles_for
    from core.video.subtitles import effective_subtitle_languages
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, server_source, server_id, title, tmdb_id) "
                     "VALUES (11, 'plex', 's11', 'Show', 222)")
        conn.commit()
    finally:
        conn.close()
    db.subtitle_override_set("show", 11, ["fr"])
    dl_id = db.add_video_download({"kind": "show", "title": "S", "status": "import_failed",
                                   "media_id": "111", "media_source": "tmdb",
                                   "search_ctx": _json.dumps({"season": 1, "episode": 2})})
    # The endpoint's persist step (api/video/manual_import.py).
    db.update_video_download(dl_id, media_source="tmdb", media_id="222")
    row = db.get_video_download(dl_id)
    assert (row["media_source"], str(row["media_id"])) == ("tmdb", "222")

    # The hook with the endpoint's synthetic dict (user's identity, not the row's).
    calls = _stub_fetch(monkeypatch, {"fr": "srt-fr"})
    fs = FakeFS()
    sidecar_dl = {"id": dl_id, "kind": "show", "media_source": "tmdb",
                  "media_id": "222",
                  "search_ctx": _json.dumps({"scope": "episode", "season": 1, "episode": 2})}
    write_subtitles_for(db, sidecar_dl, "/lib/S/S - S01E02.mkv",
                        {"subtitle_langs": "en"}, fs)
    assert calls == ["fr"]
    rows = db.subtitle_get_for_video("download", dl_id)
    assert {r["language"] for r in rows} == {"fr"}

    # The worker's residue check re-reads the DB row — it must resolve the
    # SAME override, so 'fr' is kept, not deleted as residue.
    assert effective_subtitle_languages(db, "download", dl_id,
                                        {"subtitle_langs": "en"}) == ["fr"]


def test_hook_skips_legacy_traversal_row(db, monkeypatch):
    # N1: a junk row written before parse_langs filtered codes must never
    # reach the filesystem, even though the hook never deletes system rows
    # itself (the worker's residue check removes them next pass).
    import json as _json
    from core.video.download_monitor import write_subtitles_for
    dl_id = db.add_video_download({"kind": "movie", "title": "M", "status": "completed",
                                   "media_id": "603", "media_source": "tmdb",
                                   "search_ctx": _json.dumps({})})
    db.subtitle_want("download", dl_id, "../../evil")   # legacy junk row
    calls = _stub_fetch(monkeypatch, {"en": "srt-en"})
    fs = FakeFS()
    dl = db.get_video_download(dl_id)
    write_subtitles_for(db, dl, "/lib/M.mkv", {"subtitle_langs": "en"}, fs)
    assert calls == ["en"]                              # junk never fetched
    assert fs.texts == {"/lib/M.en.srt": "srt-en"}      # nothing escaped
