"""On an install where nobody keeps a library of their own, the library
scope adds nothing -- not even when a caller names 'shared' explicitly
(#1199). Removal detection and the matchers read exactly as before."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from core import library_scope
from core.library2 import library_roots
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


@pytest.fixture
def single(tmp_path, monkeypatch):
    shared = tmp_path / "Transfer"
    shared.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))
    import core.imports.paths as paths
    import database.music_database as md
    monkeypatch.setattr(md, "get_database", lambda: db)
    monkeypatch.setattr(library_scope, "own_library_supported", lambda: True)
    cfg = SimpleNamespace(get=lambda key, default=None: str(shared)
                          if key == "soulseek.transfer_path" else default,
                          get_active_media_server=lambda: "plex")
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    library_scope.invalidate_library_scope_cache()
    library_roots.sync_library_roots(db)
    yield SimpleNamespace(db=db, shared=str(shared))
    library_scope.invalidate_library_scope_cache()


def test_an_explicit_shared_scope_adds_no_clause(single):
    from core.library2.sql_util import in_library_sql
    assert not library_scope.any_own_library_exists()
    assert in_library_sql("artist", "a", scope="shared") == ""
    assert in_library_sql("track", "t") == ""


def test_removal_still_sees_an_artist_whose_only_file_went_missing(single):
    with single.db._get_connection() as conn:
        artist = seed_artist(conn, server_id="ar-2", name="Missing")
        album = seed_album(conn, server_id="al-2", title="M", artist_id=artist)
        seed_track(conn, server_id="t-2", title="T", album_id=album, artist_id=artist,
                   file_path=os.path.join(single.shared, "M", "M", "01.flac"))
        conn.execute("UPDATE lib2_track_files SET file_state='missing'")
        conn.executemany(
            "INSERT OR IGNORE INTO lib2_media_server_mappings(entity_type, entity_id,"
            " server_source, server_id) VALUES(?,?, 'plex', ?)",
            [("artist", artist, "ar-2"), ("album", album, "al-2")])
        conn.commit()
    assert "ar-2" in single.db.get_all_artist_ids_for_server("plex")
    assert "al-2" in single.db.get_all_album_ids_for_server("plex")
