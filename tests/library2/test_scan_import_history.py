"""a song a scan takes into the library shows in Library History's imports.

3.6.1 logged an 'import' for every new track a media-server scan found;
Library v2 stopped, so the History imports filter and its count stayed empty
for everything added outside SoulSync.
"""

from __future__ import annotations

from types import SimpleNamespace

from database.music_database import MusicDatabase


def test_a_scanned_arrival_is_an_import_in_history(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    song = SimpleNamespace(title="Teardrop", grandparentTitle="Massive Attack",
                           parentTitle="Mezzanine", trackNumber=3, data={})
    db._log_scanned_import(song, "/music/Massive Attack/Mezzanine/03 Teardrop.flac", "plex")
    entries, total = db.get_library_history(event_type="import")
    assert total == 1
    assert [(e["title"], e["artist_name"], e["album_name"]) for e in entries] == [
        ("Teardrop", "Massive Attack", "Mezzanine")]
