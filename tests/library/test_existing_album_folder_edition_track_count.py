"""CAL's report: a standard 12-track download reused the 16-track deluxe folder.

Root-cause chain, all verified in code:
- ``check_album_exists_with_editions`` has an edition-UPGRADE bonus that
  deliberately matches standard→deluxe (right for "already own" checks,
  wrong for folder identity),
- the deluxe DB row carries the plain album title, so the ``_same_album_name``
  edition check in ``_find_album`` can't see the edition,
- ``_same_release`` failed open because the import knew no release id and the
  standard edition has no disambiguation.

The one signal present all along: the incoming release's own track total
(12) vs the existing roster (16). A smaller release never completes a bigger
edition's folder. The reverse — fewer existing tracks than expected — is the
normal multi-batch completion #829 exists for and must keep working.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

from core.library.existing_album_folder import resolve_existing_album_folder


class _Db:
    """Name match always finds album 1; its release id lives in a real table."""

    def __init__(self, tracks, row_release_id=None):
        self._tracks = tracks
        self._row_release_id = row_release_id

    def _get_connection(self):
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


def _existing_folder(tmp_path, n):
    folder = tmp_path / "A Day to Remember" / "Album" / "[2007] For Those Who Have Heart (Deluxe Edition) (20th anniversary)"
    folder.mkdir(parents=True)
    tracks = []
    for i in range(1, n + 1):
        f = folder / f"{i:02d} - track.mp3"
        f.write_text("x")
        tracks.append(SimpleNamespace(file_path=str(f)))
    return str(folder), tracks


def _resolve(tmp_path, db, n_expected, identity=("", "")):
    return resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="For Those Who Have Heart",
        album_artist="A Day to Remember", expected_track_count=n_expected,
        read_identity=lambda path: identity)


def test_smaller_release_does_not_reuse_bigger_edition_folder(tmp_path):
    # the reporter's exact shape: 16-track deluxe folder, 12-track standard
    # incoming, import knows no release id and has no disambiguation
    _folder, tracks = _existing_folder(tmp_path, 16)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 12) is None


def test_same_size_release_still_reuses(tmp_path):
    folder, tracks = _existing_folder(tmp_path, 12)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 12) == os.path.normpath(folder)


def test_incomplete_album_still_reuses_for_later_batches(tmp_path):
    # #829: first batch landed 10 of 12 tracks; the rest must join them
    folder, tracks = _existing_folder(tmp_path, 10)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 12) == os.path.normpath(folder)


def test_unknown_expected_count_keeps_todays_behavior(tmp_path):
    folder, tracks = _existing_folder(tmp_path, 16)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, None) == os.path.normpath(folder)


def test_unknown_fallback_total_of_one_keeps_todays_reuse(tmp_path):
    # album.py falls back to total_tracks=1 when the source said nothing (a
    # Soulseek single, say) — 1 means "unknown" as often as "single", so the
    # guard stays out and the track joins its album's folder instead of
    # splitting off under template drift
    folder, tracks = _existing_folder(tmp_path, 12)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 1) == os.path.normpath(folder)


def test_two_track_release_does_not_join_bigger_album_folder(tmp_path):
    # 2 is never the unknown-fallback, so it is authoritative: a 2-track
    # release is definitionally not the 12-track album in the folder
    _folder, tracks = _existing_folder(tmp_path, 12)
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 2) is None


def test_track_count_guard_is_independent_of_release_ids(tmp_path):
    # the count guard fires on the roster alone, before the id check: if the
    # stored roster contradicts the incoming release's own total, the data is
    # inconsistent somewhere and the safe answer is the template path
    _folder, tracks = _existing_folder(tmp_path, 16)
    db = _Db(tracks, row_release_id="some-release-id")
    assert _resolve(tmp_path, db, 12, identity=("some-release-id", "")) is None


def test_wanted_catalogue_rows_without_files_do_not_count(tmp_path):
    """Library v2: an album's rows include its wanted tracklist. Six files
    of a 13-row catalogue album are not a bigger edition than a 12-track
    release; only the files are what the folder holds."""
    folder, tracks = _existing_folder(tmp_path, 6)
    tracks += [SimpleNamespace(file_path=None) for _ in range(7)]
    db = _Db(tracks, row_release_id=None)
    assert _resolve(tmp_path, db, 12) == os.path.normpath(folder)
