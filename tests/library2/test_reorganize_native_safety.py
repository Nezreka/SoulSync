"""The real native planner, mover and updater must agree on exact files."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.reorganize_runner import build_runner


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return "jellyfin"


class _Database:
    def __init__(self, path, profiles=()):
        self.path = str(path)
        self.profiles = profiles

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_profile_library(self, profile_id):
        profile = next((p for p in self.profiles if p["id"] == profile_id), None)
        return {"mode": "own", "root": profile["root"]} if profile else {"mode": "shared"}

    def get_own_library_profiles(self):
        return self.profiles


def _seed(tmp_path, files, profiles=()):
    db = _Database(tmp_path / "native.db", profiles)
    with closing(db._get_connection()) as conn:
        ensure_library_v2_schema(conn)
        artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Album')", (artist,)
        ).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number) VALUES(?,'Song',1,1)",
            (album,),
        ).lastrowid
        conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id) VALUES(?,?)", (track, artist))
        ids = []
        for path, primary, owner, fmt in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fmt.encode())
            ids.append(conn.execute(
                "INSERT INTO lib2_track_files(track_id,path,owner_profile_id,format) VALUES(?,?,?,?)",
                (track, str(path), owner, fmt),
            ).lastrowid)
        from core.library2.track_files import set_primary_file
        for file_id, (_, primary, _, _) in zip(ids, files):
            if primary:
                set_primary_file(conn, track, file_id)
        conn.commit()
    return db, album, track, ids


@pytest.fixture
def run_reorganize(monkeypatch, tmp_path):
    import core.imports.paths as paths
    import core.library_scope as scope
    import core.settings as settings
    import database.music_database as music_database

    def run(db, album, destination, *, library=None, config=None, before_move=None):
        cm = _Config({"soulseek.transfer_path": str(tmp_path / "library"),
                      "playlists.materialize_path": str(tmp_path / "Playlists"), **(config or {})})
        monkeypatch.setattr(music_database, "get_database", lambda: db)
        monkeypatch.setattr(settings, "config_manager", cm)
        monkeypatch.setattr(paths, "_get_config_manager", lambda: cm)
        monkeypatch.setattr("core.reorganize_queue.get_queue", lambda: SimpleNamespace(
            update_active_progress=lambda **kwargs: None))
        scope.invalidate_library_scope_cache()
        def build_path(*args, **kwargs):
            if before_move is not None:
                before_move()
            return str(destination), True

        runner = build_runner(
            get_database=lambda: db,
            resolve_file_path_fn=lambda p: p if os.path.exists(p) else None,
            cleanup_empty_directories_fn=lambda *a, **kw: None,
            is_shutting_down_fn=lambda: False,
            get_transfer_path=lambda: str(tmp_path / "library"),
            get_config_manager=lambda: cm,
            build_final_path_fn=build_path,
        )
        result = runner(SimpleNamespace(album_id=album, library=library, queue_id="test",
                                        source=None, metadata_source="api"))
        with closing(db._get_connection()) as conn:
            rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM lib2_track_files")}
        return result, rows

    yield run
    scope.invalidate_library_scope_cache()


def test_scoped_nonprimary_move_updates_the_file_the_planner_selected(tmp_path, run_reorganize):
    user2 = tmp_path / "user2"
    user3 = tmp_path / "user3"
    canonical, older, selected = user2 / "01.flac", user3 / "older.mp3", user3 / "newer.flac"
    dest = user3 / "New" / "01 - Song.flac"
    db, album, _, ids = _seed(tmp_path, [(canonical, 1, 2, "flac"), (older, 0, 3, "mp3"),
                                        (selected, 0, 3, "flac")],
                              [{"id": 2, "root": str(user2)}, {"id": 3, "root": str(user3)}])

    result, rows = run_reorganize(db, album, dest, library=3)

    assert result["moved"] == 1 and result["failed"] == 0
    assert rows[ids[2]]["path"] == str(dest)
    assert rows[ids[1]]["path"] == str(older) and older.read_bytes() == b"mp3"
    assert rows[ids[0]]["path"] == str(canonical) and canonical.exists()
    assert dest.read_bytes() == b"flac" and not selected.exists()


def test_same_stem_playlist_copy_in_another_directory_keeps_its_identity(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Old" / "01.flac"
    copy = tmp_path / "Playlists" / "Mix" / "01.flac"
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, ids = _seed(tmp_path, [(source, 1, None, "flac"), (copy, 0, None, "flac")])

    result, rows = run_reorganize(db, album, dest)

    assert result["moved"] == 1
    assert rows[ids[0]]["path"] == str(dest)
    assert rows[ids[1]]["path"] == str(copy) and copy.read_bytes() == b"flac"


def test_refused_sibling_destination_does_not_repoint_its_file_row(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Old" / "01.flac"
    sibling = source.with_suffix(".opus")
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, ids = _seed(tmp_path, [(source, 1, None, "flac"), (sibling, 0, None, "opus")])
    dest.parent.mkdir()
    dest.with_suffix(".opus").write_bytes(b"OTHER RECORDING")

    result, rows = run_reorganize(db, album, dest)

    assert result["moved"] == 1
    assert rows[ids[1]]["path"] == str(sibling) and sibling.read_bytes() == b"opus"
    assert dest.with_suffix(".opus").read_bytes() == b"OTHER RECORDING"


def test_unregistered_same_stem_audio_is_not_assumed_to_be_the_same_recording(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Old" / "01.flac"
    other = source.with_suffix(".mp3")
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, _ = _seed(tmp_path, [(source, 1, None, "flac")])
    other.write_bytes(b"DIFFERENT RECORDING")

    result, _ = run_reorganize(db, album, dest)

    assert result["moved"] == 1
    assert other.read_bytes() == b"DIFFERENT RECORDING"
    assert not dest.with_suffix(".mp3").exists()


def test_proven_same_track_sibling_moves_and_updates_its_exact_row(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Old" / "01.flac"
    sibling = source.with_suffix(".opus")
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, ids = _seed(tmp_path, [(source, 1, None, "flac"), (sibling, 0, None, "opus")])

    result, rows = run_reorganize(db, album, dest)

    assert result["moved"] == 1 and result["failed"] == 0
    assert rows[ids[1]]["path"] == str(dest.with_suffix(".opus"))
    assert dest.with_suffix(".opus").read_bytes() == b"opus" and not sibling.exists()


def test_reorganize_repairs_existing_managed_playlist_symlink(tmp_path, run_reorganize):
    from core.playlists.materialize import rebuild_playlist_folder

    source = tmp_path / "library" / "Old" / "01.flac"
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, _ = _seed(tmp_path, [(source, 1, None, "flac")])
    summary = rebuild_playlist_folder(str(tmp_path / "Playlists"), "Mix", [str(source)])
    link = Path(summary.playlist_dir) / "01.flac"

    result, _ = run_reorganize(db, album, dest)

    assert result["moved"] == 1
    assert link.is_symlink() and link.read_bytes() == b"flac"
    assert link.resolve() == dest


def test_reorganize_repairs_exact_local_m3u_entries_and_preserves_foreign_files(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Mix" / "Artist - Song.flac"
    dest = tmp_path / "library" / "Artist" / "Album" / "Song.flac"
    db, album, _, _ = _seed(tmp_path, [(source, 1, None, "flac")])
    m3u = source.parent / "Mix.m3u"
    m3u.write_bytes(b"#EXTM3U\r\n#EXTINF:10,Artist - Song\r\nArtist - Song.flac\r\nhttps://example.test/live\r\n")
    foreign = source.parent / "Foreign.m3u"
    foreign.write_bytes(b"#EXTM3U\n/another/library/Artist - Song.flac\n")
    unparseable = source.parent / "Unreadable.m3u"
    unparseable.write_bytes(b"#EXTM3U\n\xffArtist - Song.flac\n")

    result, _ = run_reorganize(db, album, dest)

    assert result["moved"] == 1
    assert m3u.read_bytes() == (b"#EXTM3U\r\n#EXTINF:10,Artist - Song\r\n"
                                b"../Artist/Album/Song.flac\r\nhttps://example.test/live\r\n")
    assert foreign.read_bytes() == b"#EXTM3U\n/another/library/Artist - Song.flac\n"
    assert unparseable.read_bytes() == b"#EXTM3U\n\xffArtist - Song.flac\n"


@pytest.mark.parametrize('occupied_sibling', [False, True])
def test_failed_catalogue_update_rolls_back_only_successfully_moved_files(
    tmp_path, run_reorganize, occupied_sibling,
):
    source = tmp_path / "library" / "Old" / "01.flac"
    sibling = source.with_suffix('.opus')
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, ids = _seed(tmp_path, [(source, 1, None, "flac"), (sibling, 0, None, "opus")])
    source.with_suffix('.lrc').write_bytes(b'lyrics')
    if occupied_sibling:
        dest.parent.mkdir()
        dest.with_suffix('.opus').write_bytes(b'OTHER RECORDING')
    with closing(db._get_connection()) as conn:
        conn.execute("""CREATE TRIGGER reject_path_write BEFORE UPDATE OF path ON lib2_track_files
                        BEGIN SELECT RAISE(ABORT,'catalogue write refused'); END""")
        conn.commit()

    result, rows = run_reorganize(db, album, dest)

    assert result['moved'] == 0 and result['failed'] == 1
    assert 'catalogue write refused' in result['errors'][0]['error']
    assert source.read_bytes() == b'flac' and sibling.read_bytes() == b'opus'
    assert source.with_suffix('.lrc').read_bytes() == b'lyrics'
    assert rows[ids[0]]['path'] == str(source) and rows[ids[1]]['path'] == str(sibling)
    assert not dest.exists()
    if occupied_sibling:
        assert dest.with_suffix('.opus').read_bytes() == b'OTHER RECORDING'
    else:
        assert not dest.with_suffix('.opus').exists()


def test_stale_planned_file_identity_is_refused_and_audio_is_restored(tmp_path, run_reorganize):
    source = tmp_path / "library" / "Old" / "01.flac"
    dest = tmp_path / "library" / "New" / "Song.flac"
    db, album, _, ids = _seed(tmp_path, [(source, 1, None, "flac")])
    changed_path = str(source.parent / 'concurrent-change.flac')

    def concurrent_update():
        with closing(db._get_connection()) as conn:
            conn.execute('UPDATE lib2_track_files SET path=? WHERE id=?', (changed_path, ids[0]))
            conn.commit()

    result, rows = run_reorganize(db, album, dest, before_move=concurrent_update)

    assert result['moved'] == 0 and result['failed'] == 1
    assert 'changed since planning' in result['errors'][0]['error']
    assert rows[ids[0]]['path'] == changed_path
    assert source.read_bytes() == b'flac' and not dest.exists()


def test_native_runner_refuses_cross_library_destination(tmp_path, run_reorganize):
    user2, user3 = tmp_path / 'user2', tmp_path / 'user3'
    source, dest = user2 / 'Old' / '01.flac', user3 / 'New' / 'Song.flac'
    db, album, _, ids = _seed(tmp_path, [(source, 1, 2, 'flac')],
                              [{'id': 2, 'root': str(user2)}, {'id': 3, 'root': str(user3)}])

    result, rows = run_reorganize(db, album, dest, library=2)

    assert result['moved'] == 0 and result['failed'] == 1
    assert rows[ids[0]]['path'] == str(source)
    assert source.read_bytes() == b'flac' and not dest.exists()
