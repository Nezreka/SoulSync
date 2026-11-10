"""rotating playlists (#2039).

a playlist marked rotating gets its downloads cleaned up once the song leaves
it, no retention window. it rides the Expired Download Cleaner, so every keep
rule that job has still applies, and a song still in any other mirror stays.
all of this runs on a real database: the fake one in
test_expired_download_cleaner.py can't prove the column, the exits table or the
candidate query.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g

from core.repair_jobs import expired_download_cleaner as edc
from core.repair_jobs.expired_download_cleaner import (
    ExpiredDownloadCleanerJob,
    rotation_preview,
)
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
OLD = (NOW - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")

# curation is its own feature with its own tests. off here so a fresh test db
# (never swept, which keeps everything) doesn't hide what rotation decides
BASE = {"use_curation_signals": False, "rotation_grace": "none"}


class _Cfg:
    """the slice of config_manager the cleaner reads."""

    def __init__(self, settings=None, enabled=False, master=True):
        self.job = {"enabled": enabled, "settings": dict(settings or {})}
        self.master = master
        self.writes = []

    def get(self, key, default=None):
        if key == "repair.master_enabled":
            return self.master
        if key == "repair.jobs.expired_download_cleaner":
            return self.job
        if key == "repair.jobs.expired_download_cleaner.settings":
            return self.job["settings"]
        return default

    def set(self, key, value):
        self.writes.append((key, value))
        if key == "repair.jobs.expired_download_cleaner.enabled":
            self.job["enabled"] = value

    def get_active_media_server(self):
        return "navidrome"


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "rotation.db"))


def _tracks(*titles):
    return [{"track_name": t, "artist_name": "Artist", "source_track_id": f"sp-{t}"}
            for t in titles]


def _mirror(db, name="Mix", titles=("Stay",), profile_id=1, source="spotify", sid=None,
            rotating=False):
    pk = db.mirror_playlist(source=source, source_playlist_id=sid or f"pl-{name}-{profile_id}",
                            name=name, tracks=_tracks(*titles), profile_id=profile_id)
    if rotating:
        assert db.update_mirrored_playlist_preferences(
            pk, profile_id=profile_id, rotating=True) == "ok"
    return pk


_seq = {"n": 0}


def _download(db, title, ctx="Mix", plays=0, created=RECENT, origin="playlist"):
    """one origin-tracked download plus the library track that carries its
    play count. plays=None leaves it unmatched (play count unknown)."""
    _seq["n"] += 1
    n = _seq["n"]
    path = f"/music/Artist/Album/{n:02d} {title}.flac"
    conn = db._get_connection()
    hid = conn.execute(
        "INSERT INTO library_history (event_type, origin, origin_context, file_path, "
        "title, artist_name, created_at, source_track_id) VALUES (?,?,?,?,?,?,?,?)",
        ("download", origin, ctx, path, title, "Artist", created, f"dz-{title}"),
    ).lastrowid
    if plays is not None:
        artist_id = seed_artist(conn, server_id="ar1", name="Artist")
        album_id = seed_album(conn, server_id=f"al{n}", title=f"Album {n}", artist_id=artist_id)
        tid = seed_track(conn, server_id=f"t{n}", title=title, album_id=album_id,
                         artist_id=artist_id, file_path=path)
        conn.execute("UPDATE lib2_tracks SET play_count=? WHERE id=?", (plays, tid))
    conn.commit()
    return hid


def _scan(db, settings=None, findings=None):
    merged = dict(BASE)
    merged.update(settings or {})
    findings = findings if findings is not None else []
    ctx = SimpleNamespace(
        db=db, config_manager=_Cfg(merged),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        transfer_folder=None,
    )
    res = ExpiredDownloadCleanerJob().scan(ctx)
    return res, findings


def _titles(findings):
    return sorted(f["details"]["title"] for f in findings)


def _backdate_exits(db, hours):
    conn = db._get_connection()
    conn.execute("UPDATE playlist_rotation_exits SET left_at = datetime('now', ?)",
                 (f"-{hours} hours",))
    conn.commit()


# ── schema + preference ──────────────────────────────────────────────────────

def test_a_mirror_is_not_rotating_until_told(db):
    pk = _mirror(db)
    assert db.get_mirrored_playlist(pk)["rotating"] is False
    assert db.get_rotation_exits() == {}


def test_preference_sets_and_clears_rotating(db):
    pk = _mirror(db)
    assert db.update_mirrored_playlist_preferences(pk, profile_id=1, rotating=True) == "ok"
    assert db.get_mirrored_playlist(pk)["rotating"] is True
    # another preference leaves it alone
    assert db.update_mirrored_playlist_preferences(pk, profile_id=1, sync_mode="append") == "ok"
    assert db.get_mirrored_playlist(pk)["rotating"] is True
    assert db.update_mirrored_playlist_preferences(pk, profile_id=1, rotating=False) == "ok"
    assert db.get_mirrored_playlist(pk)["rotating"] is False


def test_another_profile_cannot_flip_it(db):
    pk = _mirror(db)
    assert db.update_mirrored_playlist_preferences(pk, profile_id=2, rotating=True) == "not_found"
    assert db.get_mirrored_playlist(pk)["rotating"] is False


def test_old_database_gains_the_column(tmp_path, monkeypatch):
    import database.music_database as mod
    # the schema runs once per path per process, a restart is a fresh set
    monkeypatch.setattr(mod, "_database_initialized_paths", set())
    path = str(tmp_path / "old.db")
    first = MusicDatabase(path)
    pk = _mirror(first)
    conn = first._get_connection()
    conn.execute("ALTER TABLE mirrored_playlists DROP COLUMN rotating")
    conn.execute("DROP TABLE playlist_rotation_exits")
    conn.commit()
    conn.close()
    mod._database_initialized_paths.clear()
    again = MusicDatabase(path)
    assert again.get_mirrored_playlist(pk)["rotating"] is False
    assert again.get_rotation_exits() == {}


# ── the job ──────────────────────────────────────────────────────────────────

def test_nothing_happens_without_a_rotating_playlist(db):
    _mirror(db, titles=("Stay",))
    _download(db, "Stay")
    _download(db, "Gone")
    res, findings = _scan(db)
    assert findings == [] and res.findings_created == 0
    assert db.get_rotation_exits() == {}


def test_a_song_that_left_a_rotating_playlist_is_proposed(db):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Stay")
    _download(db, "Gone")  # two days old: no retention window is involved
    res, findings = _scan(db)
    assert _titles(findings) == ["Gone"]
    assert res.findings_created == 1
    assert "left its rotating playlist" in findings[0]["description"]
    assert findings[0]["details"]["rotated_out"] is True


def test_default_wait_is_a_day(db):
    _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    wait = {"rotation_grace": "1d"}

    _res, findings = _scan(db, wait)
    assert findings == []
    assert list(db.get_rotation_exits()) == [gone]

    _backdate_exits(db, 10)
    assert _scan(db, wait)[1] == []

    # the daily run lands a little early or late, both count as a day
    _backdate_exits(db, 23)
    assert _titles(_scan(db, wait)[1]) == ["Gone"]


def test_the_default_setting_waits(db):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")
    # no rotation_grace in the stored settings at all
    ctx_settings = {"use_curation_signals": False}
    findings = []
    ctx = SimpleNamespace(
        db=db, config_manager=_Cfg(ctx_settings),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True), transfer_folder=None)
    ExpiredDownloadCleanerJob().scan(ctx)
    assert findings == []
    assert len(db.get_rotation_exits()) == 1


def test_first_sighting_keeps_its_clock(db):
    _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    wait = {"rotation_grace": "3d"}
    _scan(db, wait)
    _backdate_exits(db, 30)
    stamped = db.get_rotation_exits()[gone]
    _scan(db, wait)
    assert db.get_rotation_exits()[gone] == stamped


def test_a_song_that_comes_back_restarts_the_wait(db):
    _mirror(db, titles=("Stay",), rotating=True)
    back = _download(db, "Back")
    wait = {"rotation_grace": "1d"}
    _scan(db, wait)
    _backdate_exits(db, 48)
    assert back in db.get_rotation_exits()

    _mirror(db, titles=("Stay", "Back"))  # the source put it back
    assert _scan(db, wait)[1] == []
    assert db.get_rotation_exits() == {}

    _mirror(db, titles=("Stay",))  # and it left again
    assert _scan(db, wait)[1] == []
    assert back in db.get_rotation_exits()


def test_a_song_in_another_playlist_is_kept(db):
    _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    _mirror(db, name="Keepers", titles=("Shared",))
    _download(db, "Shared")
    _download(db, "Gone")
    assert _titles(_scan(db)[1]) == ["Gone"]


def test_a_song_in_another_profiles_playlist_is_kept(db):
    other = db.create_profile("Kid")
    assert other
    _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    _mirror(db, name="Theirs", titles=("Shared",), profile_id=other)
    _download(db, "Shared")
    assert _scan(db)[1] == []


def test_same_name_on_two_profiles_needs_both_rotating(db):
    other = db.create_profile("Kid")
    _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    theirs = _mirror(db, name="Mix", titles=("Stay",), profile_id=other)
    _download(db, "Gone")
    assert _scan(db)[1] == []

    db.update_mirrored_playlist_preferences(theirs, profile_id=other, rotating=True)
    assert _titles(_scan(db)[1]) == ["Gone"]


@pytest.mark.parametrize("plays", [2, 9])
def test_a_played_song_is_kept(db, plays):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Loved", plays=plays)
    assert _scan(db)[1] == []


def test_an_unknown_play_count_is_kept(db):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Orphan", plays=None)
    assert _scan(db)[1] == []


def test_unswept_curation_keeps_everything(db):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")
    assert _scan(db, {"use_curation_signals": True})[1] == []


def test_an_emptied_playlist_removes_nothing(db):
    pk = _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Stay")
    _download(db, "Gone")
    conn = db._get_connection()
    conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id=?", (pk,))
    conn.commit()
    assert _scan(db)[1] == []
    assert db.get_rotation_exits() == {}


def test_a_request_list_never_rotates(db):
    pk = _mirror(db, titles=("Stay",))
    conn = db._get_connection()
    conn.execute("UPDATE mirrored_playlists SET rotating=1, is_request_list=1 WHERE id=?", (pk,))
    conn.commit()
    _download(db, "Gone")
    assert _scan(db)[1] == []


def test_renamed_playlist_still_rotates(db):
    pk = _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    db.set_mirrored_playlist_custom_name(pk, "My Rotation", profile_id=1)
    _download(db, "Gone", ctx="My Rotation")
    assert _titles(_scan(db)[1]) == ["Gone"]


def test_other_playlists_downloads_are_untouched(db):
    _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    _mirror(db, name="Plain", titles=("Other",))
    _download(db, "Left Plain", ctx="Plain")
    _download(db, "Watched", ctx="Some Artist", origin="watchlist")
    assert _scan(db)[1] == []


def test_turning_rotation_off_drops_the_clocks(db):
    mix = _mirror(db, name="Mix", titles=("Stay",), rotating=True)
    _mirror(db, name="Second", titles=("Two",), rotating=True)
    _download(db, "Gone")
    wait = {"rotation_grace": "1w"}
    _scan(db, wait)
    assert len(db.get_rotation_exits()) == 1
    db.update_mirrored_playlist_preferences(mix, profile_id=1, rotating=False)
    _scan(db, wait)
    assert db.get_rotation_exits() == {}


def test_retention_also_keeps_a_song_in_another_playlist(db):
    """the keep rule is not rotation-only: one file, two playlists."""
    _mirror(db, name="Mix", titles=("Stay",))
    _mirror(db, name="Keepers", titles=("Shared",))
    _download(db, "Shared", created=OLD)
    _download(db, "Gone", created=OLD)
    assert _titles(_scan(db, {"playlist_retention": "1w"})[1]) == ["Gone"]


def test_an_unreadable_exit_record_keeps_everything(db, monkeypatch):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")

    def _boom():
        raise RuntimeError("locked")

    monkeypatch.setattr(db, "get_rotation_exits", _boom)
    assert _scan(db)[1] == []


def test_a_failed_exit_write_keeps_them_this_run(db, monkeypatch):
    _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")

    def _boom(_rows):
        raise RuntimeError("locked")

    monkeypatch.setattr(db, "record_rotation_exits", _boom)
    assert _scan(db)[1] == []


def test_auto_delete_removes_and_forgets(db, monkeypatch):
    _mirror(db, titles=("Stay",), rotating=True)
    stay = _download(db, "Stay")
    gone = _download(db, "Gone")
    deleted = []

    def _delete(database, entry, config_manager, transfer_folder=None):
        deleted.append(entry["id"])
        database.delete_library_history_rows([entry["id"]])
        return {"removed": 1, "file_deleted": True, "error": None, "library_v2": None}

    monkeypatch.setattr(edc, "delete_origin_download", _delete)
    wait = {"rotation_grace": "1d", "dry_run": False}
    res, findings = _scan(db, wait)
    assert deleted == [] and res.auto_fixed == 0

    _backdate_exits(db, 30)
    res, findings = _scan(db, wait)
    assert deleted == [gone] and res.auto_fixed == 1 and findings == []
    assert db.get_rotation_exits() == {}
    left = [c["id"] for c in db.get_origin_cleanup_candidates()]
    assert left == [stay]


# ── candidate query ──────────────────────────────────────────────────────────

def test_candidates_can_be_narrowed_to_a_playlist(db):
    a = _download(db, "A", ctx="Mix")
    _download(db, "B", ctx="Other")
    assert len(db.get_origin_cleanup_candidates()) == 2
    got = db.get_origin_cleanup_candidates(contexts={"mix"})
    assert [c["id"] for c in got] == [a]
    assert got[0]["play_count"] == 0
    assert db.get_origin_cleanup_candidates(contexts=set()) == []


# ── preview ──────────────────────────────────────────────────────────────────

def _preview(db, pk, settings=None, enabled=False):
    merged = dict(BASE)
    merged.update(settings or {})
    return rotation_preview(db, _Cfg(merged, enabled=enabled), db.get_mirrored_playlist(pk))


def test_preview_counts_what_left_and_why_the_rest_stays(db):
    pk = _mirror(db, name="Mix", titles=("Stay",))  # toggle still off
    _mirror(db, name="Keepers", titles=("Shared",))
    _download(db, "Stay")
    _download(db, "Gone")
    _download(db, "Gone Too")
    _download(db, "Shared")
    _download(db, "Loved", plays=5)
    _download(db, "Orphan", plays=None)
    _download(db, "Elsewhere", ctx="Keepers")

    got = _preview(db, pk)
    assert got["membership_known"] is True
    assert got["downloads"] == 6
    assert got["left"] == 5
    assert got["removing"] == 2 and got["waiting"] == 0
    assert got["kept"] == 3
    assert got["kept_reasons"] == {"other_playlist": 1, "played": 1, "unknown": 1}
    assert got["cleaner_enabled"] is False and got["dry_run"] is True


def test_preview_writes_nothing(db):
    pk = _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")
    got = _preview(db, pk, {"rotation_grace": "1d"})
    assert got["removing"] == 1 and got["waiting"] == 1
    assert db.get_rotation_exits() == {}


def test_preview_says_when_it_cannot_tell(db):
    pk = _mirror(db, titles=("Stay",))
    _download(db, "Gone")
    conn = db._get_connection()
    conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id=?", (pk,))
    conn.commit()
    got = _preview(db, pk)
    assert got["membership_known"] is False
    assert got["left"] == 0 and got["removing"] == 0


def test_preview_with_stale_curation_does_not_claim_a_favourite(db):
    pk = _mirror(db, titles=("Stay",))
    _download(db, "Gone")
    got = _preview(db, pk, {"use_curation_signals": True})
    assert got["removing"] == 0
    assert got["kept_reasons"] == {"unverified": 1}


def test_preview_reads_the_cleaner_state(db):
    pk = _mirror(db, titles=("Stay",))
    got = _preview(db, pk, {"dry_run": False, "rotation_grace": "3d"}, enabled=True)
    assert got["cleaner_enabled"] is True
    assert got["dry_run"] is False
    assert got["rotation_grace"] == "3d"
    assert got["downloads"] == 0


# ── routes ───────────────────────────────────────────────────────────────────

class _Worker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = []

    def set_job_enabled(self, job_id, enabled):
        self.calls.append((job_id, enabled))
        self.cfg.set(f"repair.jobs.{job_id}.enabled", enabled)


def _client(db, monkeypatch, cfg=None, worker=None):
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_database", lambda: db)
    monkeypatch.setattr(mod, "config_manager", cfg)
    monkeypatch.setattr(mod, "_repair_worker", (lambda: worker) if worker else None)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = True

    app.register_blueprint(mod.create_blueprint())
    return app.test_client()


def test_route_turns_rotating_on_and_the_cleaner_with_it(db, monkeypatch):
    pk = _mirror(db)
    cfg = _Cfg(BASE)
    worker = _Worker(cfg)
    c = _client(db, monkeypatch, cfg, worker)

    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["playlist"]["rotating"] is True
    assert body["cleaner_turned_on"] is True
    assert worker.calls == [("expired_download_cleaner", True)]

    # already on: nothing to turn on, and turning rotating off leaves the job alone
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.get_json()["cleaner_turned_on"] is False
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": False})
    assert resp.status_code == 200
    assert "cleaner_turned_on" not in resp.get_json()
    assert db.get_mirrored_playlist(pk)["rotating"] is False
    assert worker.calls == [("expired_download_cleaner", True)]
    assert cfg.job["enabled"] is True


def test_route_falls_back_to_config_without_a_worker(db, monkeypatch):
    pk = _mirror(db)
    cfg = _Cfg(BASE)
    c = _client(db, monkeypatch, cfg)
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.get_json()["cleaner_turned_on"] is True
    assert cfg.writes == [("repair.jobs.expired_download_cleaner.enabled", True)]


@pytest.mark.parametrize("bad", ["maybe", 2, None, [], {}])
def test_route_rejects_junk(db, monkeypatch, bad):
    pk = _mirror(db)
    c = _client(db, monkeypatch, _Cfg(BASE))
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": bad})
    assert resp.status_code == 400
    assert db.get_mirrored_playlist(pk)["rotating"] is False


def test_route_refuses_a_request_list(db, monkeypatch):
    pk = _mirror(db)
    conn = db._get_connection()
    conn.execute("UPDATE mirrored_playlists SET is_request_list=1 WHERE id=?", (pk,))
    conn.commit()
    cfg = _Cfg(BASE)
    c = _client(db, monkeypatch, cfg)
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.status_code == 400
    assert db.get_mirrored_playlist(pk)["rotating"] is False
    assert cfg.writes == []
    assert c.get(f"/api/mirrored-playlists/{pk}/rotation").get_json()["allowed"] is False


def test_route_refuses_liked_songs(db, monkeypatch):
    from core.playlists import user_playlists as up
    pk = db.mirror_playlist(source="soulsync", source_playlist_id=up.LIKED_SOURCE_ID,
                            name=up.LIKED_NAME, tracks=_tracks("Stay"), profile_id=1)
    c = _client(db, monkeypatch, _Cfg(BASE))
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.status_code == 400
    assert db.get_mirrored_playlist(pk)["rotating"] is False


def test_a_user_playlist_can_rotate(db, monkeypatch):
    pk = db.mirror_playlist(source="soulsync", source_playlist_id="mine-1",
                            name="Road Trip", tracks=_tracks("Stay"), profile_id=1)
    c = _client(db, monkeypatch, _Cfg(BASE))
    resp = c.patch(f"/api/mirrored-playlists/{pk}/preferences", json={"rotating": True})
    assert resp.status_code == 200, resp.data
    _download(db, "Removed By Hand", ctx="Road Trip")
    assert _titles(_scan(db)[1]) == ["Removed By Hand"]


def test_rotation_route_reports_the_preview(db, monkeypatch):
    pk = _mirror(db, titles=("Stay",), rotating=True)
    _download(db, "Gone")
    _download(db, "Loved", plays=3)
    c = _client(db, monkeypatch, _Cfg(BASE, enabled=True))
    resp = c.get(f"/api/mirrored-playlists/{pk}/rotation")
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["rotating"] is True and body["allowed"] is True
    assert (body["left"], body["removing"], body["kept"]) == (2, 1, 1)
    assert body["kept_reasons"] == {"played": 1}
    assert body["cleaner_enabled"] is True
    assert db.get_rotation_exits() == {}


def test_rotation_route_404s_an_unknown_playlist(db, monkeypatch):
    c = _client(db, monkeypatch, _Cfg(BASE))
    assert c.get("/api/mirrored-playlists/999/rotation").status_code == 404


# ── approving a finding later ────────────────────────────────────────────────

@pytest.fixture
def fresh_recheck():
    edc._recheck.update({"at": None, "db": None, "ids": frozenset()})
    yield
    edc._recheck.update({"at": None, "db": None, "ids": frozenset()})


def _worker(db, settings=None):
    from core.repair_worker import RepairWorker
    merged = dict(BASE)
    merged.update(settings or {})
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._config_manager = _Cfg(merged)
    return worker


def _fix(worker, hid, monkeypatch, rotated=True):
    deleted = []
    monkeypatch.setattr(
        edc, "delete_origin_download",
        lambda database, entry, cfg, transfer_folder=None: (
            deleted.append(entry["id"])
            or {"removed": 1, "file_deleted": True, "error": None, "library_v2": None}))
    out = worker._fix_expired_download(
        "download", str(hid), "/music/x.flac",
        {"history_id": hid, "file_path": "/music/x.flac", "rotated_out": rotated})
    return out, deleted


def test_a_finding_still_due_is_deleted(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    _scan(db)  # records the exit, as the run that filed the finding did
    out, deleted = _fix(_worker(db), gone, monkeypatch)
    assert out["success"] is True and deleted == [gone]


def test_a_song_back_in_the_playlist_is_not_deleted(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",), rotating=True)
    back = _download(db, "Back")
    assert _titles(_scan(db)[1]) == ["Back"]
    _mirror(db, titles=("Stay", "Back"))  # it returned before anyone approved
    out, deleted = _fix(_worker(db), back, monkeypatch)
    assert out["success"] is False and deleted == []
    assert "back in its playlist" in out["error"]


def test_a_song_played_since_is_not_deleted(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    _scan(db)
    conn = db._get_connection()
    conn.execute("UPDATE lib2_tracks SET play_count=4")
    conn.commit()
    out, deleted = _fix(_worker(db), gone, monkeypatch)
    assert out["success"] is False and deleted == []


def test_a_playlist_no_longer_rotating_is_not_deleted(db, monkeypatch, fresh_recheck):
    pk = _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    _scan(db)
    db.update_mirrored_playlist_preferences(pk, profile_id=1, rotating=False)
    out, deleted = _fix(_worker(db), gone, monkeypatch)
    assert out["success"] is False and deleted == []


def test_a_recheck_that_fails_deletes_nothing(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",), rotating=True)
    gone = _download(db, "Gone")
    _scan(db)

    def _boom(*_a, **_k):
        raise RuntimeError("locked")

    monkeypatch.setattr(edc, "_rotation_due_ids", _boom)
    out, deleted = _fix(_worker(db), gone, monkeypatch)
    assert out["success"] is False and deleted == []


def test_retention_findings_are_applied_as_before(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",))
    old = _download(db, "Old", created=OLD)
    out, deleted = _fix(_worker(db), old, monkeypatch, rotated=False)
    assert out["success"] is True and deleted == [old]


def test_one_answer_serves_a_burst_of_approvals(db, monkeypatch, fresh_recheck):
    _mirror(db, titles=("Stay",), rotating=True)
    a = _download(db, "A")
    b = _download(db, "B")
    _scan(db)
    calls = []
    real = edc._rotation_due_ids
    monkeypatch.setattr(edc, "_rotation_due_ids",
                        lambda *args: calls.append(1) or real(*args))
    cfg = _Cfg(BASE)
    assert edc.rotation_still_due(db, cfg, a) is True
    assert edc.rotation_still_due(db, cfg, b) is True
    assert edc.rotation_still_due(db, cfg, 99999) is False
    assert edc.rotation_still_due(db, cfg, "junk") is False
    assert len(calls) == 1
