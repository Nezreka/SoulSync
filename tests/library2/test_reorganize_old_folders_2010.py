"""#2010: a reorganize leaves no old folder behind.

QT3496's 2 Pistols singles, Nov 13: the audio moved and $explicit renamed
the folders, but every old folder stayed because cover.jpg was still in it.
These run the real planner, mover and catalogue update the artist page's
reorganize queue runs, on files in a temp library.
"""

import os
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.reorganize_runner import build_runner

TEMPLATES = {
    "album_path": "$albumartist/$albumtype/$album $explicit ($year)/$track - $title",
    "single_path": "$albumartist/$albumtype/$album $explicit ($year)/$track - $title",
}


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_active_media_server(self):
        return "jellyfin"


class _Database:
    def __init__(self, path):
        self.path = str(path)

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_profile_library(self, profile_id):
        return {"mode": "shared"}

    def get_own_library_profiles(self):
        return []


# (album artist, album, year, explicit, track title, guests, old folder)
SINGLES = [
    ("2 Pistols", "Bhad (feat. Tory Lanez)", 2018, 1, "Bhad (feat. Tory Lanez)",
     ("Tory Lanez",), "2 Pistols/2 Pistols - Bhad (feat. Tory Lanez)"),
    ("Big4zone", "F**k Opp (feat. Big4zone & 2 Pistols)", 2023, 1,
     "F**k Opp (feat. Big4zone & 2 Pistols)", ("2 Pistols",),
     "2 Pistols/2 Pistols - Fk Opp (feat. Big4zone & 2 Pistols)"),
    ("2 Pistols", "Fantasy (feat. Sutton)", 2018, 1, "Fantasy (feat. Sutton)",
     ("Sutton",), "2 Pistols/2 Pistols - Fantasy (feat. Sutton)"),
    ("2 Pistols", "Private Dancer", 2011, 0, "Private Dancer", (),
     "2 Pistols/2 Pistols - Private Dancer"),
]


def _artist(conn, name):
    row = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()
    return row[0] if row else conn.execute(
        "INSERT INTO lib2_artists(name) VALUES(?)", (name,)).lastrowid


def _seed_singles(db, root):
    albums = []
    with closing(db._get_connection()) as conn:
        ensure_library_v2_schema(conn)
        for artist, title, year, explicit, track_title, guests, old in SINGLES:
            artist_id = _artist(conn, artist)
            album = conn.execute(
                "INSERT INTO lib2_albums(primary_artist_id,title,year,album_type,explicit)"
                " VALUES(?,?,?,'single',?)", (artist_id, title, year, explicit)).lastrowid
            conn.execute("INSERT INTO lib2_album_artists(album_id,artist_id) VALUES(?,?)",
                         (album, artist_id))
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,explicit)"
                " VALUES(?,?,1,1,?)", (album, track_title, explicit)).lastrowid
            for pos, name in enumerate((artist, *guests)):
                conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,position)"
                             " VALUES(?,?,?)", (track, _artist(conn, name), pos))
            folder = root / old
            folder.mkdir(parents=True, exist_ok=True)
            song = folder / "01 - song.flac"
            song.write_bytes(b"audio")
            (folder / "cover.jpg").write_bytes(b"cover " + title.encode())
            conn.execute("INSERT INTO lib2_track_files(track_id,path,format) VALUES(?,?,'flac')",
                         (track, str(song)))
            albums.append((album, folder))
        conn.commit()
    return albums


@pytest.fixture
def library(monkeypatch, tmp_path):
    import core.imports.paths as paths
    import core.library_scope as scope
    import core.settings as settings
    import database.music_database as music_database

    root = tmp_path / "library"
    root.mkdir()
    db = _Database(tmp_path / "native.db")
    cm = _Config({"soulseek.transfer_path": str(root),
                  "file_organization.enabled": True,
                  "file_organization.templates": TEMPLATES,
                  "playlists.materialize_path": str(tmp_path / "Playlists")})
    monkeypatch.setattr(music_database, "get_database", lambda: db)
    monkeypatch.setattr(settings, "config_manager", cm)
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cm)
    monkeypatch.setattr("core.reorganize_queue.get_queue", lambda: SimpleNamespace(
        update_active_progress=lambda **kwargs: None))
    scope.invalidate_library_scope_cache()
    runner = build_runner(
        get_database=lambda: db,
        resolve_file_path_fn=lambda p: p if p and os.path.exists(p) else None,
        cleanup_empty_directories_fn=lambda *a, **kw: None,
        is_shutting_down_fn=lambda: False,
        get_transfer_path=lambda: str(root),
        get_config_manager=lambda: cm,
        build_final_path_fn=paths.build_final_path_for_track,
    )

    def run(album_id):
        return runner(SimpleNamespace(album_id=album_id, library=None, queue_id="q",
                                      source=None, metadata_source="api"))

    yield SimpleNamespace(root=root, db=db, run=run)
    scope.invalidate_library_scope_cache()


def _files(root):
    return sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
                  for d, _, fs in os.walk(root) for f in fs)


def test_the_2_pistols_singles_leave_no_old_folder_behind(library):
    albums = _seed_singles(library.db, library.root)

    for album_id, _old in albums:
        result = library.run(album_id)
        assert result["moved"] == 1, result

    for _album_id, old in albums:
        assert not old.exists(), _files(library.root)
    files = _files(library.root)
    assert len([f for f in files if f.endswith("cover.jpg")]) == 4, files
    assert len([f for f in files if f.endswith(".flac")]) == 4, files
    print("\n".join(files))


def _one(library, *, old, cover=b"cover"):
    """seed only Private Dancer, at ``old`` under the library root"""
    global SINGLES
    keep = SINGLES
    SINGLES = [(*keep[3][:6], old)]
    try:
        (album_id, folder), = _seed_singles(library.db, library.root)
    finally:
        SINGLES = keep
    (folder / "cover.jpg").write_bytes(cover)
    return album_id, folder


NEW = "2 Pistols/Single/Private Dancer (2011)"


def test_an_existing_destination_with_the_same_cover_still_empties_the_old_folder(library):
    album_id, old = _one(library, old="2 Pistols/Private Dancer")
    (library.root / NEW).mkdir(parents=True)
    (library.root / NEW / "cover.jpg").write_bytes(b"cover")

    assert library.run(album_id)["moved"] == 1

    assert not old.exists(), _files(library.root)
    assert (library.root / NEW / "cover.jpg").read_bytes() == b"cover"


def test_a_different_cover_at_the_destination_is_never_overwritten(library):
    album_id, old = _one(library, old="2 Pistols/Private Dancer", cover=b"mine")
    (library.root / NEW).mkdir(parents=True)
    (library.root / NEW / "cover.jpg").write_bytes(b"theirs")

    library.run(album_id)

    assert (library.root / NEW / "cover.jpg").read_bytes() == b"theirs"
    assert (old / "cover.jpg").read_bytes() == b"mine"
