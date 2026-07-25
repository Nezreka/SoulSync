"""Cache-bust versions come from one cached directory snapshot (perf25-01).

Building the ``?v=<mtime>`` token used to cost one ``Path.stat()`` per artist
per list request.  The list endpoint must instead read a snapshot that is only
refreshed when the artwork directory itself changed.
"""

from __future__ import annotations

import os

from core.library2 import artwork


def _shim(tmp_path):
    class _DB:
        database_path = str(tmp_path / "music.db")

    return _DB()


def test_versions_scan_directory_once_for_a_whole_page(tmp_path, monkeypatch):
    database = _shim(tmp_path)
    directory = artwork.artwork_dir(database)
    for entity_id in range(1, 76):
        (directory / f"artist_{entity_id}.jpg").write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database)

    scans = []
    real_scandir = os.scandir

    def counting_scandir(path):
        scans.append(str(path))
        return real_scandir(path)

    monkeypatch.setattr(artwork.os, "scandir", counting_scandir)

    first = [artwork.artwork_version(database, "artist", i) for i in range(1, 76)]
    second = [artwork.artwork_version(database, "artist", i) for i in range(1, 76)]

    assert all(value > 0 for value in first)
    assert first == second
    assert len(scans) == 1


def test_missing_artwork_has_no_version(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_dir(database)
    artwork.forget_artwork_versions(database)

    assert artwork.artwork_version(database, "artist", 4242) == 0


def test_new_artwork_file_is_picked_up(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_dir(database)
    artwork.forget_artwork_versions(database)
    assert artwork.artwork_version(database, "album", 7) == 0

    artwork.artwork_file(database, "album", 7).write_bytes(b"\xff\xd8\xff")
    artwork.forget_artwork_versions(database)

    assert artwork.artwork_version(database, "album", 7) > 0


def test_invalidation_drops_the_cached_version(tmp_path):
    database = _shim(tmp_path)
    artwork.artwork_file(database, "artist", 9).write_bytes(b"\xff\xd8\xff")
    artwork.thumb_file(database, "artist", 9).write_bytes(b"\xff\xd8\xff")
    assert artwork.artwork_version(database, "artist", 9) > 0

    artwork.invalidate_artwork(database, "artist", 9)

    assert artwork.artwork_version(database, "artist", 9) == 0
