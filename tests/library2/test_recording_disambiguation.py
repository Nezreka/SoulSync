"""Upstream 5170059ed on Library v2: the MusicBrainz recording comment is kept
with the track's recording id ("acoustic", "live", "Connect Sets ...").

It belongs to exactly one recording, so it stays only while the track keeps
that MBID: a different recording id clears it, whoever writes the id.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.musicbrainz_service import MusicBrainzService
from database.music_database import MusicDatabase
from tests.lib2_seed import track


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _row(db, track_id):
    with db._get_connection() as conn:
        return tuple(conn.execute(
            "SELECT musicbrainz_id, recording_disambiguation FROM lib2_tracks WHERE id=?",
            (track_id,)).fetchone())


def _service(db):
    service = MusicBrainzService.__new__(MusicBrainzService)
    service.db = db
    return service


def test_the_worker_keeps_the_comment_only_for_the_same_recording(db):
    with db._get_connection() as conn:
        track_id = track(conn, "Artist", "Album", "Song")
        conn.commit()
    service = _service(db)

    service.update_track_mbid(track_id, "rec-a", "matched", "acoustic")
    assert _row(db, track_id) == ("rec-a", "acoustic")
    # same recording, no comment from this lookup: the stored one stays
    service.update_track_mbid(track_id, "rec-a", "matched")
    assert _row(db, track_id) == ("rec-a", "acoustic")
    # another recording: the comment was the old one's
    service.update_track_mbid(track_id, "rec-b", "matched")
    assert _row(db, track_id) == ("rec-b", None)
    service.update_track_mbid(track_id, "rec-b", "matched", "  live  ")
    assert _row(db, track_id) == ("rec-b", "live")
    # a miss leaves id and comment alone
    service.update_track_mbid(track_id, None, "not_found")
    assert _row(db, track_id) == ("rec-b", "live")


def test_any_writer_changing_the_recording_id_clears_the_comment(db):
    with db._get_connection() as conn:
        track_id = track(conn, "Artist", "Album", "Song")
        conn.execute("UPDATE lib2_tracks SET musicbrainz_id='rec-a', "
                     "recording_disambiguation='acoustic' WHERE id=?", (track_id,))
        conn.execute("UPDATE lib2_tracks SET title='Song!' WHERE id=?", (track_id,))
        conn.commit()
    assert _row(db, track_id) == ("rec-a", "acoustic")
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET musicbrainz_id='REC-A' WHERE id=?", (track_id,))
        conn.commit()
    assert _row(db, track_id) == ("REC-A", "acoustic")   # same id, other case
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET musicbrainz_id='rec-c' WHERE id=?", (track_id,))
        conn.commit()
    assert _row(db, track_id) == ("rec-c", None)


def test_provenance_fills_the_comment_of_its_own_recording(db):
    with db._get_connection() as conn:
        track_id = track(conn, "Artist", "Album", "Song", path="/lib/Track.mp3")
        conn.commit()
    db.record_track_download(
        file_path="/lib/Track.mp3", source_service="soulseek", source_username="u",
        source_filename="Track.mp3", musicbrainz_recording_id="mb-acoustic",
        recording_disambiguation="Connect Sets acoustic")

    db.backfill_track_external_ids_from_provenance(track_id, "/lib/Track.mp3")
    assert _row(db, track_id) == ("mb-acoustic", "Connect Sets acoustic")

    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET musicbrainz_id='mb-other' WHERE id=?", (track_id,))
        conn.commit()
    db.backfill_track_external_ids_from_provenance(track_id, "/lib/Track.mp3")
    assert _row(db, track_id) == ("mb-other", None)


def test_an_import_stores_the_comment_with_the_recording(db, tmp_path, monkeypatch):
    import core.imports.side_effects as side_effects

    monkeypatch.setattr(side_effects, "get_database", lambda: db)
    monkeypatch.setattr(side_effects, "_get_config_manager",
                        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"))
    final_path = tmp_path / "song.flac"
    final_path.write_bytes(b"audio")
    side_effects.record_soulsync_library_entry({
        "source": "spotify",
        "artist": {"id": "sp-artist", "name": "Artist"},
        "album": {"id": "sp-album", "name": "Album", "total_tracks": 1},
        "track_info": {"id": "sp-track", "name": "Song", "track_number": 1,
                       "duration_ms": 200000, "artists": [{"name": "Artist"}],
                       "musicbrainz_recording_id": "rec-acoustic",
                       "disambiguation": "Connect Sets acoustic", "_source": "spotify"},
        "_final_processed_path": str(final_path),
    }, {"name": "Artist", "genres": []}, {"album_name": "Album", "track_number": 1})

    with db._get_connection() as conn:
        row = conn.execute("SELECT musicbrainz_id, recording_disambiguation FROM lib2_tracks").fetchone()
    assert tuple(row) == ("rec-acoustic", "Connect Sets acoustic")
