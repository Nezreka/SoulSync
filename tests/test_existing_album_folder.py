"""Reuse an album's existing on-disk folder for new downloads (#829).

Tacobell444: tracks added to an album across batches split into different folders
when $albumtype/$year drift. The resolver finds the album's existing single
folder (under the transfer dir) so the new track joins it. These pin the safety
rails: strict match, transfer-dir-only, single-folder-only, id-first.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

from core.library.existing_album_folder import resolve_existing_album_folder


class _FakeDb:
    def __init__(self, album=None, album_conf=0.0, tracks=None, by_spotify=None):
        self._album = album
        self._album_conf = album_conf
        self._tracks = tracks or []
        self._by_spotify = by_spotify

    def get_album_by_spotify_album_id(self, sid):
        return self._by_spotify

    def check_album_exists_with_editions(self, title, artist, confidence_threshold=0.8,
                                         expected_track_count=None, server_source=None, **kw):
        return (self._album, self._album_conf)

    def get_tracks_by_album(self, album_id):
        return self._tracks


def _track(path):
    return SimpleNamespace(file_path=path)


def _album(id=1, title="Ocean Avenue"):
    return SimpleNamespace(id=id, title=title)


def _mkfile(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_text("x")
    return str(f)


def test_reuses_single_folder_under_transfer(tmp_path):
    album_dir = tmp_path / "Yellowcard - Ocean Avenue"
    f1 = _mkfile(album_dir, "01 - Way Away.mp3")
    f2 = _mkfile(album_dir, "02 - Breathing.mp3")
    db = _FakeDb(album=_album(), album_conf=0.95, tracks=[_track(f1), _track(f2)])
    out = resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path),
        album_name="Ocean Avenue", album_artist="Yellowcard")
    assert out == os.path.normpath(str(album_dir))


def test_no_match_returns_none(tmp_path):
    db = _FakeDb(album=None, album_conf=0.0)
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="X", album_artist="Y") is None


def test_below_strict_threshold_returns_none(tmp_path):
    f = _mkfile(tmp_path / "A", "1.mp3")
    # 0.80 < the resolver's 0.85 strict gate -> not reused.
    db = _FakeDb(album=_album(), album_conf=0.80, tracks=[_track(f)])
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="A", album_artist="B") is None


def test_multi_folder_defers_to_template(tmp_path):
    f1 = _mkfile(tmp_path / "Album" / "Disc 01", "1.mp3")
    f2 = _mkfile(tmp_path / "Album" / "Disc 02", "1.mp3")
    db = _FakeDb(album=_album(), album_conf=0.95, tracks=[_track(f1), _track(f2)])
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="A", album_artist="B") is None


def test_folder_outside_transfer_returns_none(tmp_path):
    f = _mkfile(tmp_path / "outside", "1.mp3")
    transfer = tmp_path / "transfer"
    transfer.mkdir()
    db = _FakeDb(album=_album(), album_conf=0.95, tracks=[_track(f)])
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(transfer), album_name="A", album_artist="B") is None


def test_id_first_match_skips_name_lookup(tmp_path):
    album_dir = tmp_path / "Album"
    f = _mkfile(album_dir, "1.mp3")
    # name match would FAIL (album=None); the stored spotify id hits.
    db = _FakeDb(album=None, album_conf=0.0, tracks=[_track(f)], by_spotify=_album())
    out = resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), spotify_album_id="sp123",
        album_name="X", album_artist="Y")
    assert out == os.path.normpath(str(album_dir))


def test_missing_transfer_dir_returns_none(tmp_path):
    db = _FakeDb(album=_album(), album_conf=0.95, tracks=[])
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path / "nope"), album_name="A", album_artist="B") is None


def test_album_with_no_files_on_disk_returns_none(tmp_path):
    # Album matched but its tracks have no resolvable file -> nothing to reuse.
    db = _FakeDb(album=_album(), album_conf=0.95,
                 tracks=[_track("/gone/1.mp3"), _track(None)])
    assert resolve_existing_album_folder(
        db=db, transfer_dir=str(tmp_path), album_name="A", album_artist="B") is None
