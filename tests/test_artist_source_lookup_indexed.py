"""clicking a search-result artist looks the library artist up by provider
id. the bare json_extract scanned every artist and never matched an id
stored as a JSON number."""

from __future__ import annotations

from core.artist_source_lookup import find_library_artist_for_source
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_a_numeric_deezer_id_finds_the_library_artist(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    lib2_seed.track(conn, "Muse", "Absolution", "Hysteria")
    conn.execute("UPDATE lib2_artists SET external_ids=json_object('deezer', 705) WHERE name='Muse'")
    muse = conn.execute("SELECT id FROM lib2_artists WHERE name='Muse'").fetchone()[0]
    conn.commit()
    conn.close()

    assert str(find_library_artist_for_source(db, "deezer", "705")) == str(muse)
