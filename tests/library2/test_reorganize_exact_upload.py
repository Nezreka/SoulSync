"""reorganize keeps an exact soundcloud upload on its own path.

an exact upload is tagged with the original's names, so its import files it
with ``[<soundcloud id>]`` on the stem (imports/paths.exact_upload_path), and
the original can sit on the unsuffixed path beside it. the planner builds its
context from the library track, which carried no exact id: it planned the
upload onto the original's path. the audio move refuses an occupied file, but
the sidecars move without that check, so the upload's .lrc replaced the
original's. the planner now reads the id from the match the import recorded.

the catalogue files the upload on the ORIGINAL's track: its album is named
after the track, so it is the original's single, and autolink matches the
track by title. one track, two file rows; the planner moves the track's
primary file. the id names the file only when that file is the upload's.
"""

from __future__ import annotations

import pytest

from core.library2 import reorganize_plan
from database.music_database import MusicDatabase
from tests import lib2_seed

TEMPLATES = {"album_path": "$albumartist/$album/$track - $title"}


class _Cfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return "primary"


@pytest.fixture()
def builder(monkeypatch, tmp_path):
    from core.imports import paths
    cfg = _Cfg({
        "file_organization.templates": TEMPLATES,
        "file_organization.enabled": True,
        "file_organization.collab_artist_mode": "first",
        "soulseek.transfer_path": str(tmp_path / "Transfer"),
        "features.library_v2": True,
    })
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    monkeypatch.setattr(paths, "_get_album_tracks_for_source", lambda *a: None)
    return paths.build_final_path_for_track


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _track(db, tmp_path, rel, album="Song (DJ Flip)", fmt=None):
    """a library track and its file on disk."""
    path = tmp_path / "Transfer" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    with db._get_connection() as conn:
        lib_id = lib2_seed.track(conn, "Artist A", album, "Song (DJ Flip)", path=str(path),
                                 track_number=1, disc_number=1)
        if fmt:
            conn.execute("UPDATE lib2_track_files SET format = ? WHERE track_id = ?", (fmt, lib_id))
        album_id = conn.execute("SELECT album_id FROM lib2_tracks WHERE id = ?", (lib_id,)).fetchone()[0]
        conn.commit()
    return lib_id, album_id, path


def _upload_on_the_originals_track(db, tmp_path, rel, album_id, fmt="mp3"):
    """the upload's import as the catalogue files it: its album is the
    original's single and autolink's title match finds the original's track,
    so its file is a second file row there (never a track of its own)."""
    from core.library2.autolink import _find_or_create_track
    path = tmp_path / "Transfer" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    with db._get_connection() as conn:
        artist_id = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id = ?",
                                 (album_id,)).fetchone()[0]
        lib_id = _find_or_create_track(conn, album_id, artist_id, "Song (DJ Flip)", track_number=1)
        file_id = conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, format) VALUES (?, ?, ?)",
            (lib_id, str(path), fmt)).lastrowid
        conn.commit()
    # resolution._record_exact_match: the track its file resolves to,
    # another profile's: reorganize moves the library's file
    db.save_manual_library_match(2, "soundcloud_exact", "soundcloud_exact_123",
                                 db.find_track_id_by_file_path(str(path)),
                                 library_file_path=str(path))
    return lib_id, file_id, path


def _primary_file(db, lib_id):
    with db._get_connection() as conn:
        return conn.execute("SELECT id FROM lib2_track_files WHERE track_id = ? AND is_primary = 1",
                            (lib_id,)).fetchone()[0]


def _plan(db, album_id, build, tmp_path):
    with db._get_connection() as conn:
        plan = reorganize_plan.plan_album_reorganize(
            conn, album_id, build_final_path_fn=build,
            transfer_dir=str(tmp_path / "Transfer"), resolve_file_path_fn=lambda p: p)
    assert plan["status"] == "planned"
    return plan


def _paths(plan):
    return {t["track_id"]: t["new_path"] for t in plan["tracks"]}


def _planned(plan):
    return [(t["track_id"], t["file_id"], t["new_path"]) for t in plan["tracks"]]


ORIGINAL = "Artist A/Song (DJ Flip)/01 - Song (DJ Flip).mp3"
ORIGINAL_FLAC = "Artist A/Song (DJ Flip)/01 - Song (DJ Flip).flac"
UPLOAD = "Artist A/Song (DJ Flip)/01 - Song (DJ Flip) [123].mp3"


def test_the_originals_file_on_a_shared_track_keeps_its_own_name(db, builder, tmp_path):
    """a FLAC original beside the lossy upload: the original is the shared
    track's primary file, so the plan moves the original. it is no upload,
    and never gets the upload's id on its name."""
    orig_id, album_id, _original = _track(db, tmp_path, ORIGINAL_FLAC, fmt="flac")
    lib_id, upload_file, _upload = _upload_on_the_originals_track(db, tmp_path, UPLOAD, album_id)
    assert lib_id == orig_id
    original_file = _primary_file(db, lib_id)
    assert original_file != upload_file

    plan = _plan(db, album_id, builder, tmp_path)
    assert _planned(plan) == [(lib_id, original_file, ORIGINAL_FLAC)]
    assert plan["tracks"][0]["unchanged"] and plan["tracks"][0]["reason"] is None


@pytest.mark.parametrize("how", ["newer_same_quality", "original_deleted"])
def test_the_uploads_file_on_a_shared_track_keeps_its_soundcloud_id(db, builder, tmp_path,
                                                                     monkeypatch, how):
    """the upload's own file is planned (the shared track's primary: the
    newer of two same-quality files, or the one left after the original's
    file was deleted): it keeps the id its import filed it with."""
    from core.library2.track_files import set_file_state
    same = how == "newer_same_quality"
    orig_id, album_id, original = _track(db, tmp_path, ORIGINAL if same else ORIGINAL_FLAC,
                                         fmt="mp3" if same else "flac")
    lib_id, upload_file, _upload = _upload_on_the_originals_track(db, tmp_path, UPLOAD, album_id)
    assert lib_id == orig_id
    if not same:
        with db._get_connection() as conn:
            original_file = conn.execute("SELECT id FROM lib2_track_files WHERE path = ?",
                                         (str(original),)).fetchone()[0]
            assert set_file_state(conn, original_file, "deleted")
            conn.commit()
    assert _primary_file(db, lib_id) == upload_file

    plan = _plan(db, album_id, builder, tmp_path)
    assert _planned(plan) == [(lib_id, upload_file, UPLOAD)]
    assert plan["tracks"][0]["unchanged"]

    # without the id the plan put the upload on the original's name
    monkeypatch.setattr(reorganize_plan, "_exact_upload_id", lambda *a: None)
    assert _planned(_plan(db, album_id, builder, tmp_path)) == [(lib_id, upload_file, ORIGINAL)]


def test_a_moved_upload_keeps_its_soundcloud_id(db, builder, tmp_path):
    """a reorganize moved the upload since its import: the match still names
    the old path, and the file carries the upload's id on its name."""
    from core.library2.track_files import repoint_file_path
    orig_id, album_id, _original = _track(db, tmp_path, ORIGINAL, fmt="mp3")
    lib_id, upload_file, old = _upload_on_the_originals_track(
        db, tmp_path, "Old/Song (DJ Flip) [123].mp3", album_id)
    assert lib_id == orig_id
    moved = tmp_path / "Transfer" / "Artist A" / "Song (DJ Flip)" / "Song (DJ Flip) [123].mp3"
    old.rename(moved)
    with db._get_connection() as conn:
        assert repoint_file_path(conn, str(old), str(moved)) == 1
        conn.commit()
    assert _primary_file(db, lib_id) == upload_file
    assert _planned(_plan(db, album_id, builder, tmp_path)) == [(lib_id, upload_file, UPLOAD)]


def test_a_catalogue_album_plans_as_before(db, builder, tmp_path, monkeypatch):
    orig_id, album_id, original = _track(db, tmp_path, "Artist A/Album/01 - Song (DJ Flip).mp3", album="Album")
    upload_id, _upload_album, upload = _track(db, tmp_path, UPLOAD)
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(upload_id),
                                 library_file_path=str(upload))
    # a catalogue id's match on the original changes nothing either
    db.save_manual_library_match(1, "spotify", "sp1", str(orig_id), library_file_path=str(original))

    plan = _plan(db, album_id, builder, tmp_path)
    assert _paths(plan) == {orig_id: "Artist A/Album/01 - Song (DJ Flip).mp3"}
    monkeypatch.setattr(reorganize_plan, "_exact_upload_id", lambda *a: None)
    assert _plan(db, album_id, builder, tmp_path) == plan


def test_a_media_server_match_is_not_read_as_this_track(db, builder, tmp_path):
    """a 'server' match holds the media server's id; the same number on a
    library track is someone else."""
    lib_id, album_id, _path = _track(db, tmp_path, ORIGINAL)
    db.save_manual_library_match(1, "soundcloud_exact", "soundcloud_exact_123", str(lib_id),
                                 library_track_id_kind="server")
    assert _paths(_plan(db, album_id, builder, tmp_path)) == {lib_id: ORIGINAL}
