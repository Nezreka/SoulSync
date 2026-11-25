"""A scan that finds music SoulSync never imported brings it into the catalogue.

Nezreka's review of #1062: a ripped CD, Lidarr or a copy into the music folder
reaches the server without passing through SoulSync, and the watchlist then
grabs it again. The scan hands such a file to the import writer -- but only a
file this process can open under a library folder, so ownership stays a
property of the file and never of the server's listing.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase

TAGS = {"title": "Plug In Baby", "artist": "Muse, Someone Else", "album_artist": "Muse",
        "album": "Origin of Symmetry", "track_number": 1, "disc_number": 1}


class _Track:
    def __init__(self, path, server_id="srv-new"):
        self.ratingKey = server_id
        self.title = "Plug In Baby"
        self.trackNumber = 1
        self.discNumber = 1
        self.duration = 219000
        self.path = str(path)


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    from core.library2.library_roots import apply_roots, desired_roots

    music = tmp_path / "music"
    music.mkdir()
    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        apply_roots(conn.cursor(), desired_roots(str(music), []))
        conn.commit()
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    monkeypatch.setattr("core.library2.tag_cache.read_tag_snapshot", lambda path: dict(TAGS))
    monkeypatch.setattr("core.library2.autolink._warm_new_artwork", lambda *a, **k: None)
    reconciles = []
    monkeypatch.setattr("core.library2.unmapped_trigger.schedule_unmapped_artist_reconcile",
                        lambda *a, **k: reconciles.append(True))
    return db, music, reconciles


def _file(folder, name="01 Plug In Baby.flac"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"ripped audio")
    return path


def _count(db, table):
    with db._get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_file_the_server_found_joins_the_catalogue_unmonitored(lib):
    db, music, reconciles = lib
    path = _file(music / "Muse" / "Origin of Symmetry")

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") == "inserted"

    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT ar.name AS artist, al.title AS album, al.origin, al.monitored AS album_monitored,"
            "       t.title, t.monitored, f.path, f.file_state"
            "  FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id"
            "  JOIN lib2_albums al ON al.id=t.album_id JOIN lib2_artists ar ON ar.id=al.primary_artist_id"
        ).fetchone()
    # the album artist, not the track's credit string
    assert (row["artist"], row["album"], row["title"]) == ("Muse", "Origin of Symmetry", "Plug In Baby")
    assert (row["path"], row["file_state"], row["origin"]) == (str(path), "active", "library")
    # found on disk, never asked for: nothing turns into a download or an upgrade
    assert (row["album_monitored"], row["monitored"]) == (0, 0)
    assert db.get_track_by_server_id("srv-new", "plex") is not None
    assert reconciles == [True]


def test_server_scan_skips_deleted_quarantine(lib):
    db, music, _ = lib
    path = _file(music / ".deleted" / "Muse" / "Origin of Symmetry")

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") is False
    assert _count(db, "lib2_track_files") == 0
    assert path.is_file()


def test_the_next_scan_maps_it_and_creates_nothing(lib):
    db, music, _ = lib
    path = _file(music / "Muse" / "Origin of Symmetry")
    db.insert_or_update_media_track(_Track(path), "al-new", "ar-new", server_source="plex")

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") == "updated"
    assert (_count(db, "lib2_tracks"), _count(db, "lib2_track_files")) == (1, 1)


def test_a_wanted_track_gets_the_file_instead_of_a_duplicate(lib):
    db, music, _ = lib
    with db._get_connection() as conn:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name,name_key) VALUES('Muse','muse')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,origin,monitored)"
            " VALUES(?,'Origin of Symmetry','discography',1)", (artist,)).lastrowid
        conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id,role) VALUES(?,?,'primary')",
                     (album, artist))
        wanted = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,monitored)"
            " VALUES(?,'Plug In Baby',1,1,1)", (album,)).lastrowid
        conn.commit()
    path = _file(music / "Muse" / "Origin of Symmetry")

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") == "inserted"
    with db._get_connection() as conn:
        assert conn.execute("SELECT track_id FROM lib2_track_files").fetchone()[0] == wanted
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?", (wanted,)).fetchone()[0] == 1
    assert _count(db, "lib2_tracks") == 1


def test_a_listed_file_that_is_gone_stays_out(lib):
    """A listed file that is gone from a library this process can see
    (Navidrome's missing files, a delayed removal) is not ours to adopt."""
    db, music, reconciles = lib
    path = music / "Muse" / "gone.flac"

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") is False
    assert (_count(db, "lib2_artists"), _count(db, "lib2_tracks"),
            _count(db, "lib2_track_files")) == (0, 0, 0)
    assert reconciles == []


def test_a_server_folder_outside_soulsyncs_own_is_still_your_music(lib, tmp_path):
    """dev's library showed every song the server shares, wherever its folder.
    A readable file under a server folder SoulSync doesn't manage joins too."""
    db, _music, _ = lib
    path = _file(tmp_path / "old-rips" / "Muse")

    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") == "inserted"
    assert _count(db, "lib2_track_files") == 1


def test_a_remote_server_adopts_by_what_the_server_says(tmp_path, monkeypatch):
    """Plex on another machine: SoulSync can't open the files or even see the
    library folder. dev listed those songs from the server, so they still
    join, named by the server's album artist and album."""
    from core.library2.library_roots import apply_roots, desired_roots
    db = MusicDatabase(database_path=str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        apply_roots(conn.cursor(), desired_roots(str(tmp_path / "not-mounted"), []))
        conn.commit()
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    monkeypatch.setattr("core.library2.autolink._warm_new_artwork", lambda *a, **k: None)
    monkeypatch.setattr("core.library2.unmapped_trigger.schedule_unmapped_artist_reconcile",
                        lambda *a, **k: None)
    track = _Track("/data/music/Muse/Origin of Symmetry/01 Plug In Baby.flac")
    track.grandparentTitle = "Muse"
    track.parentTitle = "Origin of Symmetry"

    assert db.insert_or_update_media_track(track, "al-new", "ar-new",
                                           server_source="plex") == "inserted"
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT ar.name AS artist, al.title AS album, t.title, t.monitored, f.path"
            "  FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id"
            "  JOIN lib2_albums al ON al.id=t.album_id JOIN lib2_artists ar ON ar.id=al.primary_artist_id"
        ).fetchone()
    assert (row["artist"], row["album"], row["title"]) == ("Muse", "Origin of Symmetry", "Plug In Baby")
    assert row["path"] == track.path and row["monitored"] == 0
    assert db.get_track_by_server_id("srv-new", "plex") is not None
    # and the next scan maps it instead of adding it again
    assert db.insert_or_update_media_track(track, "al-new", "ar-new",
                                           server_source="plex") == "updated"
    assert _count(db, "lib2_tracks") == 1


# ── the same file, reached through another path (#2288, #2290) ──────────────

class _Elsewhere(_Track):
    """the media server's view of a file: its own mount, its own tags"""

    def __init__(self, path, server_id, size, title="Plug In Baby - Remastered", number=7):
        super().__init__(path, server_id)
        self.title = title
        self.trackNumber = number
        self.file_size = size


def _owned(db, music):
    path = _file(music / "Muse" / "Origin of Symmetry")
    assert db.insert_or_update_media_track(_Track(path), "al-new", "ar-new",
                                           server_source="plex") == "inserted"
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_track_files SET size=?", (path.stat().st_size,))
        conn.commit()
        return path, conn.execute("SELECT id FROM lib2_tracks").fetchone()[0]


def test_a_server_that_sees_the_file_under_another_path_is_tied_to_the_owned_track(lib):
    """jellyfin reads the same folder through its own mount and tags the
    track a little differently. the path didn't match, the title and number
    didn't either, and the owned song was never tied to jellyfin's copy: in
    the library, and left out of every playlist pushed to the server."""
    db, music, _ = lib
    path, track_id = _owned(db, music)
    theirs = _Elsewhere("/media/music/Muse/Origin of Symmetry/01 Plug In Baby.flac", "jf-1",
                        path.stat().st_size)
    assert db.insert_or_update_media_track(theirs, "jf-album", "jf-artist", server_source="jellyfin")
    assert _count(db, "lib2_tracks") == 1
    with db._get_connection() as conn:
        mapped = conn.execute(
            "SELECT entity_id FROM lib2_media_server_mappings WHERE entity_type='track' "
            "AND server_source='jellyfin' AND server_id='jf-1'").fetchone()
    assert mapped is not None and int(mapped[0]) == track_id


def test_a_different_file_of_the_same_name_is_not_taken_for_it(lib):
    db, music, _ = lib
    path, _track_id = _owned(db, music)
    other = _Elsewhere("/media/music/Muse/Other/01 Plug In Baby.flac", "jf-2",
                       path.stat().st_size + 1)
    db.insert_or_update_media_track(other, "jf-album", "jf-artist", server_source="jellyfin")
    with db._get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM lib2_media_server_mappings WHERE entity_type='track' "
            "AND server_id='jf-2'").fetchone() is None


def test_two_owned_copies_of_one_file_are_a_question_not_an_answer(lib):
    from core.library2.media_server_sync import owned_track_by_file_twin
    db, music, _ = lib
    path, track_id = _owned(db, music)
    size = path.stat().st_size
    with db._get_connection() as conn:
        assert owned_track_by_file_twin(conn.cursor(), "/x/01 Plug In Baby.flac", size) == track_id
        assert owned_track_by_file_twin(conn.cursor(), "/x/01 PLUG IN BABY.FLAC", size) == track_id
        assert owned_track_by_file_twin(conn.cursor(), "/x/other.flac", size) is None
        assert owned_track_by_file_twin(conn.cursor(), "/x/01 Plug In Baby.flac", 0) is None
        album = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
        second = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,?)",
                              (album, "Plug In Baby", 2)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path, size) VALUES(?,?,?)",
                     (second, "/other/01 Plug In Baby.flac", size))
        assert owned_track_by_file_twin(conn.cursor(), "/x/01 Plug In Baby.flac", size) is None


def test_a_second_file_on_this_disk_is_not_a_view_of_the_first(lib):
    """the same song on two releases: two files, one name, one size. the twin
    rule is for a path that can't be opened from here, never for a file that
    is right there beside the other"""
    from core.library2.media_server_sync import owned_track_by_file_twin
    db, music, _ = lib
    path, track_id = _owned(db, music)
    second = _file(music / "Muse" / "Origin of Symmetry (Deluxe)")
    assert second.stat().st_size == path.stat().st_size and second.name == path.name
    with db._get_connection() as conn:
        assert owned_track_by_file_twin(conn.cursor(), str(second), second.stat().st_size) is None
        # the very same file, asked by its own path, is itself
        assert owned_track_by_file_twin(conn.cursor(), str(path), path.stat().st_size) == track_id
