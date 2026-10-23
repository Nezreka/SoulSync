"""A track length is a check on every import; 0 or a lost value switches it off.

"Devil Eyes - Slowed" reached the importer with duration_ms 0, so the duration
check was skipped and the studio cut (136 s for a 193 s edit) was imported.
"""

from __future__ import annotations

import json

from core.library2.schema import ensure_library_v2_schema, run_library_v2_backfills
from core.library2.wishlist_mirror import fill_catalogue_durations
from tests.lib2_seed import RowDb, row_conn, track


def _db(tmp_path):
    db = RowDb(str(tmp_path / "music.db"))
    conn = db._get_connection()
    ensure_library_v2_schema(conn)
    return db, conn


def test_backfill_turns_stored_zeros_back_into_unknown(tmp_path):
    _db_, conn = _db(tmp_path)
    zero = track(conn, "ZODIVK", "Devil Eyes", "Devil Eyes", owned=False, bpm=0.0, duration=0)
    real = track(conn, "ZODIVK", "Devil Eyes", "Other", owned=False, bpm=128.0, duration=136651)
    run_library_v2_backfills(conn)
    rows = dict((r[0], (r[1], r[2])) for r in conn.execute("SELECT id, bpm, duration FROM lib2_tracks"))
    assert rows[zero] == (None, None)
    assert rows[real] == (128.0, 136651)


def test_queue_rows_without_a_length_take_the_catalogues(tmp_path):
    db, conn = _db(tmp_path)
    slowed = track(conn, "ZODIVK", "Devil Eyes (Slowed)", "Devil Eyes - Slowed",
                   owned=False, duration=193697, spotify_id="4GuP")
    conn.commit()
    by_link = {"id": "x::y", "duration_ms": 0, "track_data": {"duration_ms": 0},
               "source_info": json.dumps({"lib2_track_id": slowed})}
    by_provider_id = {"id": "4GuP::07z7", "duration_ms": 0, "source_info": "{}"}
    known = {"id": "4GuP::07z7", "duration_ms": 1000, "source_info": "{}"}
    fill_catalogue_durations(db, [by_link, by_provider_id, known])
    assert by_link["duration_ms"] == by_link["track_data"]["duration_ms"] == 193697
    assert by_provider_id["duration_ms"] == 193697
    assert known["duration_ms"] == 1000
