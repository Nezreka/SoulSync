"""#1299: folder reuse never steers one release into another's folder.

the resolver finds an album by name. two musicbrainz releases can share that
name, so before reusing the found album's folder the release has to agree: the
db row's release id first, the files' own tags when the row has none.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

from core.library.existing_album_folder import resolve_existing_album_folder

ORIGINAL = "55c7242f-1b4b-485d-b5f2-d6a8feeee088"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"


class _Db:
    """name match always finds album 1; its release id lives in a real table."""

    def __init__(self, tracks, row_release_id=None):
        self._tracks = tracks
        self._row_release_id = row_release_id

    def _get_connection(self):
        # a fresh connection per call, like the real one (the resolver closes it)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE albums (id TEXT, musicbrainz_release_id TEXT)")
        conn.execute("INSERT INTO albums VALUES ('1', ?)", (self._row_release_id,))
        return conn

    def get_album_by_spotify_album_id(self, sid):
        return None

    def check_album_exists_with_editions(self, title, artist, **kw):
        return SimpleNamespace(id=1, title=title), 0.95

    def get_tracks_by_album(self, album_id):
        return self._tracks


def _existing_folder(tmp_path):
    folder = tmp_path / "кис-кис" / "кис-кис - юность в стиле панк"
    folder.mkdir(parents=True)
    f = folder / "01 - рэпер.flac"
    f.write_text("x")
    return str(folder), [SimpleNamespace(file_path=str(f))]


def _resolve(tmp_path, db, identity=("", ""), **kw):
    return resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="юность в стиле панк",
        album_artist="кис-кис", read_identity=lambda path: identity, **kw)


def test_other_release_by_row_id_is_not_reused(tmp_path):
    _folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=ORIGINAL)
    assert _resolve(tmp_path, db, musicbrainz_release_id=BABY_PUNK,
                    disambiguation="baby punk version") is None


def test_same_release_by_row_id_is_reused(tmp_path):
    folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=BABY_PUNK)
    got = _resolve(tmp_path, db, musicbrainz_release_id=BABY_PUNK,
                   disambiguation="baby punk version")
    assert got == os.path.normpath(folder)


def test_file_tags_decide_when_the_row_has_no_id(tmp_path):
    folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=None)
    # the folder's files say they're the baby punk release
    tagged = (BABY_PUNK, "baby punk version")
    assert _resolve(tmp_path, db, tagged, musicbrainz_release_id=ORIGINAL) is None
    assert _resolve(tmp_path, db, tagged, musicbrainz_release_id=BABY_PUNK) == os.path.normpath(folder)


def test_edition_without_ids_joins_only_a_folder_tagged_as_that_edition(tmp_path):
    folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, ("", ""), disambiguation="baby punk version") is None
    assert _resolve(tmp_path, db, ("", "Baby Punk Version"),
                    disambiguation="baby punk version") == os.path.normpath(folder)


def test_an_import_that_knows_nothing_keeps_todays_reuse(tmp_path):
    # no release id, no disambiguation: a spotify download of a track, say
    folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=ORIGINAL)
    assert _resolve(tmp_path, db, (ORIGINAL, "")) == os.path.normpath(folder)
    assert _resolve(tmp_path, db, ("", "explicit")) == os.path.normpath(folder)


def test_ids_compare_case_insensitively(tmp_path):
    folder, tracks = _existing_folder(tmp_path)
    db = _Db(tracks, row_release_id=BABY_PUNK.upper())
    assert _resolve(tmp_path, db, musicbrainz_release_id=BABY_PUNK) == os.path.normpath(folder)


def test_db_without_the_column_falls_back_to_file_tags(tmp_path):
    folder, tracks = _existing_folder(tmp_path)

    class _NoConnDb(_Db):
        def _get_connection(self):
            raise RuntimeError("no db here")

    db = _NoConnDb(tracks)
    assert _resolve(tmp_path, db, (ORIGINAL, ""), musicbrainz_release_id=BABY_PUNK) is None
    assert _resolve(tmp_path, db, (BABY_PUNK, ""), musicbrainz_release_id=BABY_PUNK) == os.path.normpath(folder)
