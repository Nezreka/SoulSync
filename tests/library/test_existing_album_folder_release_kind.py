"""Folder reuse must never merge different release kinds.

A single/EP named after its lead track ("Ocean Avenue") shares its title with
the album of the same name. The #829 folder-reuse logic matched on name+artist
only, so downloading the single routed its file into the ALBUM's folder —
colliding with (and replacing) the album's title track, while the single never
became its own release and stayed "Missing".

The resolver now takes the incoming release's type and refuses reuse on a
known type mismatch. Unknown on either side stays lenient (today's reuse) so
thin metadata never splits a real album.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

from core.library.existing_album_folder import resolve_existing_album_folder


class _Db:
    """Name match always finds album 1; the row carries a real album_type."""

    def __init__(self, tracks, album_type="album"):
        self._tracks = tracks
        self._album_type = album_type

    def _get_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE albums (id TEXT, musicbrainz_release_id TEXT, album_type TEXT)")
        conn.execute(
            "INSERT INTO albums VALUES ('1', NULL, ?)", (self._album_type,))
        return conn

    def get_album_by_spotify_album_id(self, sid):
        return None

    def check_album_exists_with_editions(self, title, artist, **kw):
        return SimpleNamespace(id=1, title=title), 0.95

    def get_tracks_by_album(self, album_id):
        return self._tracks


def _existing_album_folder(tmp_path):
    folder = tmp_path / "Yellowcard" / "Albums" / "Ocean Avenue"
    folder.mkdir(parents=True)
    f = folder / "01 - Ocean Avenue.mp3"
    f.write_text("x")
    return str(folder), [SimpleNamespace(file_path=str(f))]


def _resolve(tmp_path, db, **kw):
    return resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="Ocean Avenue",
        album_artist="Yellowcard", read_identity=lambda path: ("", ""), **kw)


def test_single_does_not_reuse_album_folder(tmp_path):
    # The reported bug: the "Ocean Avenue" single must not land in the
    # "Ocean Avenue" album's folder.
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="album")
    assert _resolve(tmp_path, db, incoming_album_type="single") is None
    assert os.path.isdir(folder)  # sanity: the album folder really exists


def test_album_does_not_reuse_single_folder(tmp_path):
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="single")
    assert _resolve(tmp_path, db, incoming_album_type="album") is None


def test_ep_does_not_reuse_album_folder(tmp_path):
    _folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="album")
    assert _resolve(tmp_path, db, incoming_album_type="ep") is None


def test_same_kind_still_reuses(tmp_path):
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="album")
    assert _resolve(tmp_path, db, incoming_album_type="album") == os.path.normpath(folder)
    db_single = _Db(tracks, album_type="single")
    assert _resolve(tmp_path, db_single, incoming_album_type="single") == os.path.normpath(folder)


def test_unknown_incoming_type_stays_lenient(tmp_path):
    # Thin metadata: no incoming type -> today's #829 reuse is preserved.
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="album")
    assert _resolve(tmp_path, db) == os.path.normpath(folder)
    assert _resolve(tmp_path, db, incoming_album_type="") == os.path.normpath(folder)


def test_unknown_stored_type_stays_lenient(tmp_path):
    # A row with no/blank album_type can't judge the incoming release.
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="")
    assert _resolve(tmp_path, db, incoming_album_type="single") == os.path.normpath(folder)
    db_none = _Db(tracks, album_type=None)
    assert _resolve(tmp_path, db_none, incoming_album_type="single") == os.path.normpath(folder)


def test_compile_normalizes_to_compilation(tmp_path):
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="compile")
    # incoming "compilation" == stored "compile" -> same kind -> reuse
    assert _resolve(tmp_path, db, incoming_album_type="compilation") == os.path.normpath(folder)
    # incoming single vs stored compilation -> different -> refuse
    assert _resolve(tmp_path, db, incoming_album_type="single") is None


class _ProductionDb(_Db):
    """Mirrors a real production DB: the albums table carries record_type
    (populated by the enrichment workers) and has NO album_type column at
    all. The kind gate must read the live column."""

    def _get_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE albums (id TEXT, musicbrainz_release_id TEXT, record_type TEXT)")
        conn.execute("INSERT INTO albums VALUES ('1', NULL, 'album')")
        return conn


def test_kind_gate_reads_record_type_in_production(tmp_path):
    # The album_type column is never written by current code; without the
    # record_type fallback the gate below would be lenient and reuse the
    # album folder for the single.
    _folder, tracks = _existing_album_folder(tmp_path)
    db = _ProductionDb(tracks)
    assert _resolve(tmp_path, db, incoming_album_type="single") is None


class _BothColumnsDb(_Db):
    """An older enriched DB carrying BOTH columns: a NULL/blank record_type
    must fall through to album_type; a populated record_type wins."""

    def __init__(self, tracks, record_type, album_type):
        super().__init__(tracks, album_type)
        self._record_type = record_type

    def _get_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE albums (id TEXT, musicbrainz_release_id TEXT, "
            "record_type TEXT, album_type TEXT)")
        conn.execute(
            "INSERT INTO albums VALUES ('1', NULL, ?, ?)",
            (self._record_type, self._album_type))
        return conn


def test_blank_record_type_falls_through_to_album_type(tmp_path):
    folder, tracks = _existing_album_folder(tmp_path)
    # record_type NULL/empty/whitespace -> the legacy album_type decides.
    for blank in (None, "", "   "):
        db = _BothColumnsDb(tracks, record_type=blank, album_type="album")
        assert _resolve(tmp_path, db, incoming_album_type="single") is None
    # A populated record_type wins over a conflicting album_type.
    db = _BothColumnsDb(tracks, record_type="single", album_type="album")
    assert _resolve(tmp_path, db, incoming_album_type="single") == os.path.normpath(folder)


def test_single_and_ep_share_a_folder(tmp_path):
    # spotify calls eps 'single', deezer/itunes call them 'ep'. same release,
    # so it must not split into a second folder.
    folder, tracks = _existing_album_folder(tmp_path)
    db = _Db(tracks, album_type="ep")
    assert _resolve(tmp_path, db, incoming_album_type="single") == os.path.normpath(folder)
    db_single = _Db(tracks, album_type="single")
    assert _resolve(tmp_path, db_single, incoming_album_type="ep") == os.path.normpath(folder)
