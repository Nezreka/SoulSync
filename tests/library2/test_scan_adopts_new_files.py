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
