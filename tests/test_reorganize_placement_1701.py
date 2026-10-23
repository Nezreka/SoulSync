"""#1701: reorganize findings never settled.

nothing remembered where reorganize put an album, so every scan re-planned it
from a live lookup. a pin written after the move, one rate-limited source, or a
naming change in a soulsync update each moved the album again. now a placed
album keeps its release, a failed lookup of it skips instead of falling through
to another source, and the scan leaves it alone until the naming settings or a
locked pin change.
"""

from __future__ import annotations

import os

import pytest

import core.library_reorganize as lr
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reorganize import LibraryReorganizeJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _patch_fetch(monkeypatch, tracklists):
    def get_album(source, aid):
        return {"name": f"{source}:{aid}"} if tracklists.get((source, aid)) else None

    def get_tracks(source, aid):
        return tracklists.get((source, aid))

    monkeypatch.setattr(lr, "get_album_for_source", get_album)
    monkeypatch.setattr(lr, "get_album_tracks_for_source", get_tracks)
    monkeypatch.setattr(lr, "_normalize_album_tracks", lambda items: items or [])
    monkeypatch.setattr(lr, "get_source_priority", lambda primary: ["spotify", "itunes", "deezer"])


# ── _resolve_source: the placed release ─────────────────────────────────


def test_placed_release_beats_a_later_auto_pin(monkeypatch):
    _patch_fetch(monkeypatch, {
        ("itunes", "it1"): [{"name": "x"}],
        ("musicbrainz", "mb1"): [{"name": "y"}],
    })
    album = {
        "itunes_album_id": "it1",
        "canonical_source": "musicbrainz", "canonical_album_id": "mb1",
        "reorganize_source": "itunes", "reorganize_album_id": "it1",
    }
    resolved = {}
    source, _, _ = lr._resolve_source(album, "itunes", resolved=resolved)
    assert source == "itunes"
    assert resolved == {"source": "itunes", "album_id": "it1"}


def test_locked_pin_beats_the_placed_release(monkeypatch):
    _patch_fetch(monkeypatch, {
        ("itunes", "it1"): [{"name": "x"}],
        ("musicbrainz", "mb1"): [{"name": "y"}],
    })
    album = {
        "canonical_source": "musicbrainz", "canonical_album_id": "mb1", "canonical_locked": 1,
        "reorganize_source": "itunes", "reorganize_album_id": "it1",
    }
    source, _, _ = lr._resolve_source(album, "itunes")
    assert source == "musicbrainz"


def test_placed_release_not_answering_skips_instead_of_walking(monkeypatch):
    # spotify would answer, but the album was placed from itunes: planning it
    # from spotify is exactly the rename the issue reported.
    _patch_fetch(monkeypatch, {("spotify", "sp1"): [{"name": "x"}]})
    album = {
        "spotify_album_id": "sp1", "itunes_album_id": "it1",
        "reorganize_source": "itunes", "reorganize_album_id": "it1",
    }
    resolved = {}
    source, _, _ = lr._resolve_source(album, "spotify", resolved=resolved)
    assert source is None
    assert resolved.get("unavailable") is True


def test_placed_release_raising_skips_too(monkeypatch):
    _patch_fetch(monkeypatch, {("spotify", "sp1"): [{"name": "x"}]})

    def boom(source, aid):
        if source == "itunes":
            raise RuntimeError("429")
        return {"name": "x"}

    monkeypatch.setattr(lr, "get_album_for_source", boom)
    album = {
        "spotify_album_id": "sp1",
        "reorganize_source": "itunes", "reorganize_album_id": "it1",
    }
    resolved = {}
    assert lr._resolve_source(album, "spotify", resolved=resolved)[0] is None
    assert resolved.get("unavailable") is True


def test_unplaced_album_still_walks_and_reports_its_release(monkeypatch):
    _patch_fetch(monkeypatch, {("itunes", "it1"): [{"name": "x"}]})
    album = {"spotify_album_id": "sp1", "itunes_album_id": "it1"}
    resolved = {}
    source, _, _ = lr._resolve_source(album, "spotify", resolved=resolved)
    assert source == "itunes"
    assert resolved == {"source": "itunes", "album_id": "it1"}


def test_planner_reports_source_unavailable(monkeypatch):
    _patch_fetch(monkeypatch, {("spotify", "sp1"): [{"name": "x"}]})
    album = {
        "spotify_album_id": "sp1", "title": "A",
        "reorganize_source": "itunes", "reorganize_album_id": "it1",
    }
    plan = lr.plan_album_reorganize(album, [{"id": 1, "title": "t"}], primary_source="spotify")
    assert plan["status"] == "source_unavailable"


def test_planner_carries_the_release_id(monkeypatch):
    _patch_fetch(monkeypatch, {
        ("itunes", "it1"): [{"name": "t", "track_number": 1, "disc_number": 1}],
    })
    album = {"itunes_album_id": "it1", "title": "A"}
    plan = lr.plan_album_reorganize(album, [{"id": 1, "title": "t", "track_number": 1}],
                                    primary_source="itunes")
    assert plan["status"] == "planned"
    assert plan["source_album_id"] == "it1"


# ── placement on a real database ────────────────────────────────────────


def _db(tmp_path):
    """Library v2 port: albums 100 and 200, one owned track each."""
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (1, 'Artist', 'artist')")
    for album_id in (100, 200):
        conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (?, 1, 'Album')",
                     (album_id,))
    for track_id, album_id, title in ((1, 100, 'One'), (2, 200, 'Two')):
        conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, ?, ?)",
                     (track_id, album_id, title))
        conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                     (track_id, f"/m/{track_id}.flac"))
    conn.commit()
    conn.close()
    return db


def _finding(db, entity_id):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, entity_type, entity_id, title) "
        "VALUES ('library_reorganize', 'path_mismatch', 'track', ?, 'Would move')",
        (entity_id,),
    )
    conn.commit()
    conn.close()


def _statuses(db):
    conn = db._get_connection()
    rows = conn.execute(
        "SELECT entity_id, status FROM repair_findings ORDER BY entity_id").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _album(db, album_id):
    with db._get_connection() as conn:
        return dict(conn.execute('SELECT * FROM lib2_albums WHERE id=?', (album_id,)).fetchone())


def test_mark_placed_closes_only_that_albums_findings(tmp_path):
    """Library v2 port: the album's pending "would move" findings describe a
    move that's done and are closed; nothing else is touched. No placement is
    stored (open decision), so the call reports False."""
    db = _db(tmp_path)
    _finding(db, "lib2:1")
    _finding(db, "lib2:2")
    assert lr.mark_album_placed(db, 100, "itunes", "it1", "fp1") is True
    assert _statuses(db) == {"lib2:1": "resolved", "lib2:2": "pending"}


def test_settled_needs_same_naming_and_no_other_locked_pin():
    row = {"reorganize_source": "itunes", "reorganize_album_id": "it1", "reorganize_fp": "fp1"}
    assert lr.album_placement_settled(row, "fp1") is True
    assert lr.album_placement_settled(row, "fp2") is False      # user changed naming
    assert lr.album_placement_settled({}, "fp1") is False       # never placed
    locked_other = {**row, "canonical_locked": 1,
                    "canonical_source": "musicbrainz", "canonical_album_id": "mb1"}
    assert lr.album_placement_settled(locked_other, "fp1") is False
    locked_same = {**row, "canonical_locked": 1,
                   "canonical_source": "itunes", "canonical_album_id": "it1"}
    assert lr.album_placement_settled(locked_same, "fp1") is True
    auto_other = {**row, "canonical_locked": 0,
                  "canonical_source": "musicbrainz", "canonical_album_id": "mb1"}
    assert lr.album_placement_settled(auto_other, "fp1") is True


def test_fingerprint_follows_the_naming_settings(monkeypatch):
    import core.settings as settings

    values = {"file_organization": {"templates": {"album_path": "$albumartist/$album"}}}
    monkeypatch.setattr(settings.config_manager, "get",
                        lambda key, default=None: values.get(key, default))
    first = lr.naming_fingerprint()
    assert lr.naming_fingerprint() == first
    values["file_organization"] = {"templates": {"album_path": "$albumartist/($year) $album"}}
    assert lr.naming_fingerprint() != first


# ── the scan ────────────────────────────────────────────────────────────


class _Cfg:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def get_active_media_server(self):
        return None


def _ctx(db, settings=None):
    findings = []
    ctx = JobContext(db=db, transfer_folder="/m", config_manager=_Cfg(settings),
                     create_finding=lambda **kw: findings.append(kw) or True)
    ctx._findings = findings  # type: ignore[attr-defined]
    return ctx


def _planned(album_id, unchanged, source="itunes", source_album_id="it1"):
    return {
        "success": True, "status": "planned", "source": source,
        "source_album_id": source_album_id, "album": "Album", "artist": "Artist",
        "tracks": [{
            "track_id": 1 if album_id == "100" else 2, "title": "t",
            "current_path": "/m/old.flac", "new_path": "/m/old.flac" if unchanged else "/m/new.flac",
            "matched": True, "unchanged": unchanged, "file_exists": True,
        }],
    }


def _stub_preview(monkeypatch, answers, calls):
    def preview(db, config, album_id, **kw):
        calls.append(str(album_id))
        return answers[str(album_id)]

    monkeypatch.setattr('core.library2.reorganize_bridge.preview_album_reorganize', preview)
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp1")


def test_scan_stamps_an_album_already_in_place_then_skips_it(tmp_path, monkeypatch):
    db = _db(tmp_path)
    calls = []
    _stub_preview(monkeypatch, {"100": _planned("100", True), "200": _planned("200", False)}, calls)

    LibraryReorganizeJob().scan(_ctx(db))
    assert _album(db, "100")["reorganize_placement"]
    assert _album(db, "200")["reorganize_placement"] is None    # still needs a move

    calls.clear()
    result = LibraryReorganizeJob().scan(_ctx(db))
    assert calls == ["200"]          # the placed album isn't re-planned
    assert result.skipped >= 1


def test_scan_rechecks_placed_albums_when_the_naming_changes(tmp_path, monkeypatch):
    db = _db(tmp_path)
    lr.mark_album_placed(db, "100", "itunes", "it1", "old-fp")
    calls = []
    _stub_preview(monkeypatch, {"100": _planned("100", False), "200": _planned("200", True)}, calls)
    ctx = _ctx(db)
    LibraryReorganizeJob().scan(ctx)
    assert "100" in calls
    assert len(ctx._findings) == 1   # type: ignore[attr-defined]


def test_scan_recheck_setting_replans_placed_albums(tmp_path, monkeypatch):
    db = _db(tmp_path)
    lr.mark_album_placed(db, "100", "itunes", "it1", "fp1")
    calls = []
    _stub_preview(monkeypatch, {"100": _planned("100", True), "200": _planned("200", True)}, calls)
    settings = {"repair.jobs.library_reorganize.settings": {"recheck_placed_albums": True}}
    LibraryReorganizeJob().scan(_ctx(db, settings))
    assert "100" in calls


def test_source_outage_does_not_replan_a_settled_album(tmp_path, monkeypatch):
    db = _db(tmp_path)
    lr.mark_album_placed(db, "100", "itunes", "it1", "fp1")
    lr.mark_album_placed(db, "200", "itunes", "it2", "fp1")
    calls = []
    _stub_preview(monkeypatch, {}, calls)
    assert LibraryReorganizeJob().scan(_ctx(db)).skipped == 2
    assert not calls


# ── apply stamps the placement ──────────────────────────────────────────


def _rename_preview(tmp_path, collision=False):
    src = tmp_path / "old" / "1.flac"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    dst = tmp_path / "new" / "1.flac"

    def preview(**_kw):
        return {
            "success": True, "status": "planned", "source": "itunes", "source_album_id": "it1",
            "tracks": [{
                "track_id": 1, "title": "One", "matched": True, "unchanged": False,
                "collision": collision, "file_exists": True, "new_path_abs": str(dst), "current_path_abs": str(src),
            }],
        }
    return preview, dst


def test_rename_only_apply_stamps_placement(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp1")
    preview, dst = _rename_preview(tmp_path)
    summary = lr.reorganize_album_rename_only(
        album_id="100", db=db, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p, build_final_path_fn=None, preview_fn=preview,
    )
    assert summary["moved"] == 1 and os.path.exists(dst)
    row = _album(db, "100")
    assert row["reorganize_placement"]


def test_rename_only_with_a_collision_does_not_stamp(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp1")
    preview, _ = _rename_preview(tmp_path, collision=True)
    lr.reorganize_album_rename_only(
        album_id="100", db=db, transfer_dir=str(tmp_path),
        resolve_file_path_fn=lambda p: p, build_final_path_fn=None, preview_fn=preview,
    )
    assert _album(db, "100")["reorganize_placement"] is None


def test_run_placed_everything():
    assert lr._run_placed_everything({"status": "completed", "failed": 0}, None) is True
    assert lr._run_placed_everything({"status": "completed", "failed": 1}, None) is False
    assert lr._run_placed_everything({"status": "no_source_id", "failed": 0}, None) is False
    assert lr._run_placed_everything({"status": "completed", "failed": 0}, lambda: True) is False


# ── cover art after a move ──────────────────────────────────────────────


def test_cover_fix_reports_stale_when_the_files_moved(tmp_path):
    db = _db(tmp_path)
    folder = tmp_path / "Artist" / "Album"
    folder.mkdir(parents=True)
    conn = db._get_connection()
    conn.execute("UPDATE lib2_track_files SET path = ? WHERE track_id = 1", (str(folder / "gone.flac"),))
    conn.commit()
    conn.close()
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    res = w._fix_missing_cover_art("album", "lib2:100", None, {
        "album_id": "lib2:100", "found_artwork_url": "http://new/album.jpg"})
    assert res["success"] is False and res.get("stale") is True


def test_cover_fix_media_server_only_album_still_succeeds(tmp_path):
    # paths this process can't see at all (folder absent too) stay the
    # database-only success they always were.
    db = _db(tmp_path)
    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w.transfer_folder = str(tmp_path)
    w._config_manager = None
    res = w._fix_missing_cover_art("album", "lib2:100", None, {
        "album_id": "lib2:100", "found_artwork_url": "http://new/album.jpg"})
    assert res["success"] is True
