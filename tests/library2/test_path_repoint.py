"""Update file locations: records follow a music library folder that moved.

Storm (Discord): the music library folder went from /app/Transfer to /music,
the docker mount with it. Every file still opened (the resolver's suffix walk
found /music/Artist/Album/x.flac for a stored /app/Transfer/Artist/Album/x.flac)
but no stored path was ever rewritten, so the old root showed everywhere.

The real resolver runs in every test here; only the config is a stand-in.
"""

from __future__ import annotations

import os
from contextlib import closing

import pytest

from core.library2 import path_repoint, paths, scan
from database.music_database import MusicDatabase

AUDIO = b"flac-bytes-of-one-song"


class _Config:
    def __init__(self, transfer, music_paths=()):
        self.values = {"soulseek.transfer_path": str(transfer),
                       "library.music_paths": [str(p) for p in music_paths]}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def storm(tmp_path, monkeypatch):
    """/app/Transfer is gone, /music holds the same tree, the setting moved."""
    old_root = tmp_path / "app" / "Transfer"
    new_root = tmp_path / "music"
    names = [("Artist", "Album", f"0{n} - Song {n}.flac") for n in range(1, 4)]
    for parts in names:
        target = new_root.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(AUDIO)
    config = _Config(new_root)
    monkeypatch.setattr(path_repoint, "_config", lambda cm=None: cm or config)
    real_resolve = paths.resolve_lib2_path
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda path, config_manager=None: real_resolve(path, config))
    real_healthy = paths.missing_path_root_is_healthy
    monkeypatch.setattr(paths, "missing_path_root_is_healthy",
                        lambda path, config_manager=None: real_healthy(path, config))
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    ids = []
    with closing(db._get_connection()) as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
                             (artist,)).lastrowid
        for n, parts in enumerate(names, 1):
            track = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) "
                                 "VALUES(?,?,?)", (album, f"Song {n}", n)).lastrowid
            stored = str(old_root.joinpath(*parts))
            ids.append(conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                                    (track, stored, len(AUDIO))).lastrowid)
        conn.commit()
    return {"db": db, "config": config, "old_root": old_root, "new_root": new_root,
            "ids": ids, "names": names, "tmp": tmp_path}


def _path(db, file_id):
    with closing(db._get_connection()) as conn:
        return conn.execute("SELECT path FROM lib2_track_files WHERE id=?", (file_id,)).fetchone()[0]


def _add_file(db, path, size=len(AUDIO)):
    with closing(db._get_connection()) as conn:
        file_id = conn.execute("INSERT INTO lib2_track_files(path, size) VALUES(?,?)",
                               (str(path), size)).lastrowid
        conn.commit()
        return file_id


def _set(db, sql, params=()):
    with closing(db._get_connection()) as conn:
        conn.execute(sql, params)
        conn.commit()


# ── the Storm case ───────────────────────────────────────────────────────


def test_preview_groups_the_move_by_old_and_new_root(storm):
    result = path_repoint.preview(storm["db"])

    assert result["counts"]["repoint"] == 3
    assert result["groups"] == [{
        "old_root": str(storm["old_root"]), "new_root": str(storm["new_root"]), "count": 3,
        "examples": result["groups"][0]["examples"],
    }]
    example = result["groups"][0]["examples"][0]
    assert example["old"].startswith(str(storm["old_root"]))
    assert example["new"].startswith(str(storm["new_root"]))
    # a preview writes nothing
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_apply_rewrites_the_records_and_never_touches_the_files(storm):
    before = sorted(p for p in storm["tmp"].rglob("*.flac"))
    result = path_repoint.walk(storm["db"], apply=True)

    assert result["repointed"] == 3
    for file_id, parts in zip(storm["ids"], storm["names"]):
        assert _path(storm["db"], file_id) == str(storm["new_root"].joinpath(*parts))
    assert sorted(p for p in storm["tmp"].rglob("*.flac")) == before
    runs = path_repoint.list_runs(storm["db"])
    assert runs[0]["status"] == "done" and runs[0]["repointed"] == 3
    assert runs[0]["groups"] == [{"old_root": str(storm["old_root"]),
                                  "new_root": str(storm["new_root"]), "count": 3}]


def test_a_second_apply_finds_nothing_left_to_move(storm):
    path_repoint.walk(storm["db"], apply=True)
    again = path_repoint.preview(storm["db"])
    assert again["counts"]["repoint"] == 0
    assert again["counts"]["present"] == 3


def test_rows_naming_the_file_by_its_path_follow_it(storm):
    old = _path(storm["db"], storm["ids"][0])
    _set(storm["db"], "INSERT INTO track_downloads(track_id, file_path, source_service) "
                      "VALUES('1', ?, 'soulseek')", (old,))
    path_repoint.walk(storm["db"], apply=True)
    new = _path(storm["db"], storm["ids"][0])
    with closing(storm["db"]._get_connection()) as conn:
        assert conn.execute("SELECT file_path FROM track_downloads").fetchone()[0] == new


def test_the_media_servers_own_path_is_never_rewritten(storm):
    """server_path is what Plex/Jellyfin/Navidrome reported (#1573); only the
    disk path SoulSync opens moves"""
    with closing(storm["db"]._get_connection()) as conn:
        track = conn.execute("SELECT track_id FROM lib2_track_files WHERE id=?",
                             (storm["ids"][0],)).fetchone()[0]
        server_path = "/data/plex/Artist/Album/01 - Song 1.flac"
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type, entity_id, server_source, "
                     "server_id, server_path) VALUES('track', ?, 'plex', 'p1', ?)", (track, server_path))
        conn.commit()
    path_repoint.walk(storm["db"], apply=True)
    with closing(storm["db"]._get_connection()) as conn:
        assert conn.execute("SELECT server_path FROM lib2_media_server_mappings").fetchone()[0] \
            == server_path


# ── what is left alone ───────────────────────────────────────────────────


def test_a_file_that_exists_as_stored_is_unchanged(storm):
    here = storm["new_root"] / "Artist" / "Album" / "01 - Song 1.flac"
    _set(storm["db"], "DELETE FROM lib2_track_files")
    file_id = _add_file(storm["db"], here)
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["present"] == 1 and result["repointed"] == 0
    assert _path(storm["db"], file_id) == str(here)


def test_two_different_files_at_the_same_place_in_two_roots_are_left_alone(storm):
    other_root = storm["tmp"] / "music2"
    twin = other_root / "Artist" / "Album" / "01 - Song 1.flac"
    twin.parent.mkdir(parents=True)
    twin.write_bytes(AUDIO)
    storm["config"].values["library.music_paths"] = [str(other_root)]
    result = path_repoint.walk(storm["db"], apply=True)

    assert result["counts"]["ambiguous"] == 1
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))
    # the other two only exist in one root
    assert result["repointed"] == 2


def test_a_different_size_is_a_different_file(storm):
    _set(storm["db"], "UPDATE lib2_track_files SET size=size+1 WHERE id=?", (storm["ids"][0],))
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["different_file"] == 1
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_a_record_with_no_size_is_not_guessed(storm):
    _set(storm["db"], "UPDATE lib2_track_files SET size=NULL WHERE id=?", (storm["ids"][0],))
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["unverified"] == 1
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_an_unmounted_drive_with_no_copies_changes_nothing(storm):
    for flac in storm["new_root"].rglob("*.flac"):
        flac.unlink()
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["not_found"] == 3
    assert "run_id" not in result
    assert path_repoint.list_runs(storm["db"]) == []


def test_a_drive_still_in_settings_never_gives_its_records_to_another_copy(storm):
    """the old root is configured but not mounted: the resolver only looks in
    it, so a copy elsewhere is never taken for its file"""
    storm["config"].values["library.music_paths"] = [str(storm["old_root"])]
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["repointed"] == 0
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_a_copy_in_the_download_folder_is_not_the_librarys_file(storm):
    downloads = storm["tmp"] / "downloads"
    loose = downloads / "Artist" / "Album" / "01 - Song 1.flac"
    loose.parent.mkdir(parents=True)
    loose.write_bytes(AUDIO)
    storm["config"].values["soulseek.download_path"] = str(downloads)
    for flac in storm["new_root"].rglob("01 - *.flac"):
        flac.unlink()
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["outside_library"] == 1
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_a_bare_filename_match_is_too_little(storm):
    loose = storm["new_root"] / "Loose.flac"
    loose.write_bytes(AUDIO)
    file_id = _add_file(storm["db"], storm["old_root"] / "Elsewhere" / "Loose.flac")
    _set(storm["db"], "UPDATE lib2_track_files SET path=? WHERE id=?",
         (str(storm["tmp"] / "gone" / "Loose.flac"), file_id))
    result = path_repoint.walk(storm["db"], apply=True)
    assert result["counts"]["partial_match"] == 1
    assert _path(storm["db"], file_id) == str(storm["tmp"] / "gone" / "Loose.flac")


def test_a_path_another_record_already_holds_is_not_taken(storm):
    target = storm["new_root"] / "Artist" / "Album" / "01 - Song 1.flac"
    holder = _add_file(storm["db"], target)
    result = path_repoint.walk(storm["db"], apply=True)
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))
    assert _path(storm["db"], holder) == str(target)
    assert result["repointed"] == 2


# ── batches, resume, undo ────────────────────────────────────────────────


def test_an_interrupted_apply_resumes_into_the_same_run(storm):
    calls = {"n": 0}

    def stop_after_first_batch():
        calls["n"] += 1
        return calls["n"] > 1

    first = path_repoint.walk(storm["db"], apply=True, batch=2, should_stop=stop_after_first_batch)
    assert first["stopped"] and first["repointed"] == 2
    assert path_repoint.list_runs(storm["db"])[0]["status"] == "stopped"

    second = path_repoint.walk(storm["db"], apply=True, batch=2)
    assert second["repointed"] == 1
    assert second["run_id"] == first["run_id"]
    run = path_repoint.list_runs(storm["db"])[0]
    assert run["status"] == "done" and run["repointed"] == 3
    assert len(path_repoint.list_runs(storm["db"])) == 1


def test_apply_can_be_limited_to_chosen_groups(storm):
    result = path_repoint.walk(storm["db"], apply=True,
                               groups=[("/somewhere/else", str(storm["new_root"]))])
    assert result["repointed"] == 0
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_undo_puts_every_record_and_reference_back(storm):
    old = _path(storm["db"], storm["ids"][0])
    _set(storm["db"], "INSERT INTO track_downloads(track_id, file_path, source_service) "
                      "VALUES('1', ?, 'soulseek')", (old,))
    run_id = path_repoint.walk(storm["db"], apply=True)["run_id"]
    stats = path_repoint.undo_run(storm["db"], run_id)

    assert stats == {"restored": 3, "skipped": 0}
    assert _path(storm["db"], storm["ids"][0]) == old
    with closing(storm["db"]._get_connection()) as conn:
        assert conn.execute("SELECT file_path FROM track_downloads").fetchone()[0] == old
    assert path_repoint.list_runs(storm["db"])[0]["status"] == "undone"


def test_undo_leaves_a_record_that_changed_since(storm):
    run_id = path_repoint.walk(storm["db"], apply=True)["run_id"]
    _set(storm["db"], "UPDATE lib2_track_files SET path='/renamed/by/hand.flac' WHERE id=?",
         (storm["ids"][0],))
    stats = path_repoint.undo_run(storm["db"], run_id)
    assert stats == {"restored": 2, "skipped": 1}
    assert _path(storm["db"], storm["ids"][0]) == "/renamed/by/hand.flac"


# ── the automatic pass in a library scan ─────────────────────────────────


def test_a_scan_moves_the_records_of_a_library_folder_that_moved(storm):
    stats = scan.rescan_files(storm["db"], file_ids=storm["ids"])

    assert stats.get("paths_relocated") == 3
    for file_id, parts in zip(storm["ids"], storm["names"]):
        assert _path(storm["db"], file_id) == str(storm["new_root"].joinpath(*parts))
    assert path_repoint.list_runs(storm["db"])[0]["kind"] == "scan"


def test_a_scan_leaves_records_alone_while_the_old_folder_still_has_files(storm):
    """a live old root is not a move: something to look at, not to fix"""
    keep = storm["old_root"] / "Someone Else" / "Album" / "01 - Other.flac"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"other")
    stats = scan.rescan_files(storm["db"], file_ids=storm["ids"])
    assert not stats.get("paths_relocated")
    assert _path(storm["db"], storm["ids"][0]).startswith(str(storm["old_root"]))


def test_a_scan_with_the_library_unmounted_moves_nothing(storm):
    for flac in storm["new_root"].rglob("*.flac"):
        flac.unlink()
    stats = scan.rescan_files(storm["db"], file_ids=storm["ids"])
    assert not stats.get("paths_relocated")
    assert all(_path(storm["db"], i).startswith(str(storm["old_root"])) for i in storm["ids"])
    assert path_repoint.list_runs(storm["db"]) == []


def test_an_empty_mount_point_counts_as_gone(storm):
    """docker images often keep the old folder, empty"""
    storm["old_root"].mkdir(parents=True)
    stats = scan.rescan_files(storm["db"], file_ids=storm["ids"])
    assert stats.get("paths_relocated") == 3


def test_judge_answers_present_for_a_path_on_disk(storm):
    here = storm["new_root"] / "Artist" / "Album" / "01 - Song 1.flac"
    verdict = path_repoint.judge(str(here), len(AUDIO), roots=[str(storm["new_root"])])
    assert verdict.status == "present"
    assert os.path.exists(here)


def test_a_relative_stored_path_is_left_to_the_tools_check(storm):
    """a media server's library-relative path names no folder that could be
    seen to be gone, so only the check a person runs moves it"""
    _set(storm["db"], "UPDATE lib2_track_files SET path=? WHERE id=?",
         ("Artist/Album/01 - Song 1.flac", storm["ids"][0]))
    stats = scan.rescan_files(storm["db"], file_ids=[storm["ids"][0]])
    assert not stats.get("paths_relocated")
    assert _path(storm["db"], storm["ids"][0]) == "Artist/Album/01 - Song 1.flac"

    result = path_repoint.walk(storm["db"], apply=True)
    relative = [g for g in result["groups"] if g["old_root"] == ""]
    assert relative and relative[0]["new_root"] == str(storm["new_root"])
    assert _path(storm["db"], storm["ids"][0]) == str(
        storm["new_root"] / "Artist" / "Album" / "01 - Song 1.flac")


def test_a_windows_server_path_follows_to_the_local_folder(storm):
    """Plex on Windows reported H:\\Music\\...; the disk path here is /music"""
    _set(storm["db"], "UPDATE lib2_track_files SET path=? WHERE id=?",
         ("H:\\Music\\Artist\\Album\\01 - Song 1.flac", storm["ids"][0]))
    result = path_repoint.walk(storm["db"], apply=True)
    assert {"old_root": "H:\\Music", "new_root": str(storm["new_root"]), "count": 1,
            "examples": [{"old": "H:\\Music\\Artist\\Album\\01 - Song 1.flac",
                          "new": str(storm["new_root"] / "Artist" / "Album" / "01 - Song 1.flac")}]} \
        in result["groups"]


def test_only_the_exact_suffix_walk_is_asked(storm, monkeypatch):
    """this writes, so the resolver's guesses (case folding, a sibling album
    folder, a made-up filename) are never asked for"""
    from core.library import path_resolver

    asked = []
    real = path_resolver.resolve_library_file_path_with_diagnostic

    def spy(path, **kwargs):
        asked.append(kwargs.get("allow_fuzzy", True))
        return real(path, **kwargs)

    monkeypatch.setattr(path_resolver, "resolve_library_file_path_with_diagnostic", spy)
    path_repoint.preview(storm["db"])
    assert asked and not any(asked)


def test_a_folder_that_differs_only_by_case_is_not_followed(storm):
    _set(storm["db"], "UPDATE lib2_track_files SET path=? WHERE id=?",
         (str(storm["old_root"] / "artist" / "album" / "01 - Song 1.flac"), storm["ids"][0]))
    path_repoint.walk(storm["db"], apply=True)
    assert _path(storm["db"], storm["ids"][0]).endswith("artist/album/01 - Song 1.flac")
