"""Regression: Deezer enrichment must not overwrite an artist's deezer_id from a
collaboration/compilation track whose primary artist is someone else.

The Kendrick/Jorja bug: a track our library credits to Jorja Smith lives on
Kendrick Lamar's curated "Black Panther" album. The album/track search resolves
to that album, whose Deezer primary artist is Kendrick (id 525046). The old
``_verify_artist_id`` "corrected" Jorja's deezer_id to 525046 with no name
check — stamping one Deezer id across several unrelated artists, which later
broke the artist-detail page (it matched the wrong library artist by id).

The fix gates the correction on a name match between the result's primary
artist and our parent artist.
"""

from __future__ import annotations

import sqlite3

from core.deezer_worker import DeezerWorker


def _worker():
    # Bypass __init__ — it wants real clients/db. We only exercise the pure
    # _verify_artist_id / _name_matches / _normalize_name logic.
    w = DeezerWorker.__new__(DeezerWorker)
    w.name_similarity_threshold = 0.80
    w._corrections = []
    w._correct_artist_deezer_id = lambda item, cid: w._corrections.append((item['id'], cid))
    return w


def _item(artist_name, parent_deezer_id):
    return {
        'type': 'track', 'id': 1, 'name': 'Some Track',
        'artist': artist_name, 'artist_deezer_id': parent_deezer_id,
    }


def test_no_correction_when_result_artist_name_differs():
    # Jorja Smith (deezer 999) but the track resolved to Kendrick's album
    # (Deezer artist 525046, 'Kendrick Lamar') → must NOT overwrite.
    w = _worker()
    w._verify_artist_id(_item('Jorja Smith', '999'), '525046', 'Kendrick Lamar')
    assert w._corrections == []


def test_correction_when_names_match():
    # Same artist, stale/wrong stored id → legitimate correction proceeds.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', 'Kendrick Lamar')
    assert w._corrections == [(1, '525046')]


def test_name_match_tolerates_minor_variation():
    # Fuzzy match (feat. suffix / casing) still counts as the same artist.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', 'KENDRICK LAMAR')
    assert w._corrections == [(1, '525046')]


def test_no_correction_when_ids_already_equal():
    w = _worker()
    w._verify_artist_id(_item('Whoever', '525046'), '525046', 'Anyone')
    assert w._corrections == []


def test_no_parent_id_is_noop():
    w = _worker()
    w._verify_artist_id(_item('X', None), '525046', 'Y')
    assert w._corrections == []


def test_missing_result_name_skips_correction():
    # #988: a missing result artist name (compilation/collab Deezer payloads often
    # omit it) can NO LONGER bypass the guard — without a positive name match we
    # can't confirm it's the same artist, so the correction is skipped (id kept).
    # This blank-name bypass is how The Beatles' id 1 got smeared onto The Outfield.
    w = _worker()
    w._verify_artist_id(_item('Kendrick Lamar', '111'), '525046', None)
    assert w._corrections == []


# ── _correct_artist_deezer_id must not smear an id another artist owns (#988) ──
#
# Against Library v2 now (docs §32.3.1 stage 2): the id lives in
# `lib2_artists.external_ids` rather than a `deezer_id` column, and the track's
# parent artist is reached through its album because lib2 has no `tracks.artist_id`.
def _seed_db(tmp_path):
    from core.library2.schema import ensure_library_v2_schema

    path = str(tmp_path / "m.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists (id, name, sort_name, external_ids) "
        "VALUES (1, 'The Beatles', 'Beatles, The', '{\"deezer\": \"1\"}')")
    conn.execute(
        "INSERT INTO lib2_artists (id, name, sort_name) "
        "VALUES (2, 'The Outfield', 'Outfield, The')")
    conn.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title, album_type) "
        "VALUES (5, 2, 'Play Deep', 'album')")
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) "
                 "VALUES (10, 5, 'Your Love')")
    conn.commit()
    conn.close()
    return path


class _DB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)   # fresh conn each call (matches real db)
        conn.row_factory = sqlite3.Row
        return conn


def _worker_with_db(path):
    w = DeezerWorker.__new__(DeezerWorker)
    w.db = _DB(path)
    return w


def _deezer_id_of(path, artist_id):
    import json

    conn = sqlite3.connect(path)
    try:
        raw = conn.execute(
            "SELECT external_ids FROM lib2_artists WHERE id = ?", (artist_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    return json.loads(raw or "{}").get("deezer")


def test_correct_refuses_id_owned_by_a_differently_named_artist(tmp_path):
    """The exact #988 smear: a track credited to The Outfield resolves to a Deezer
    release whose primary artist is The Beatles (id 1). Correcting The Outfield's
    deezer_id to 1 must be REFUSED — id 1 is already owned by The Beatles."""
    path = _seed_db(tmp_path)
    _worker_with_db(path)._correct_artist_deezer_id({'type': 'track', 'id': 10, 'artist': 'The Outfield'}, '1')
    assert _deezer_id_of(path, 2) is None      # The Outfield NOT smeared
    assert _deezer_id_of(path, 1) == '1'       # The Beatles untouched


def test_correct_allows_a_free_id(tmp_path):
    """A genuinely unclaimed id still corrects (the feature keeps working)."""
    path = _seed_db(tmp_path)
    _worker_with_db(path)._correct_artist_deezer_id({'type': 'track', 'id': 10, 'artist': 'The Outfield'}, '777')
    assert _deezer_id_of(path, 2) == '777'
