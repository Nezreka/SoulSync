"""Verify matches — the targeted repair of pre-fix enrichment corruption.

Before the Aug 2026 matching fixes (Enrichment P1) a worker crash-loop could
smear ONE source id across many artists, and empty-normalization lookups
could "match" titles that carry no real content. Full rematch would cost
weeks of API calls; the repair instead resets exactly the two corruption
fingerprints so the FIXED workers rematch them on their next pass:

  1. artist id-collision clusters — several artists sharing one source id
     (only artists: duplicate albums/tracks are legitimate ownership);
  2. matched rows whose title is DEGENERATE — normalizes to nothing.

All hermetic: tmp-path MusicDatabase, pure SQL, no network.
"""

from __future__ import annotations

from core.enrichment.unmatched import (
    build_artist_collision_queries,
    build_degenerate_reset_query,
    degenerate_title,
)
from database.music_database import MusicDatabase


# ---------------------------------------------------------------- pure core

def test_degenerate_title_flags_contentless_names_only():
    # Nothing left after stripping brackets + non-word chars → degenerate.
    assert degenerate_title('') is True
    assert degenerate_title(None) is True
    assert degenerate_title('!!!') is True
    assert degenerate_title('(Live)') is True
    assert degenerate_title('[...] - ...') is True
    # Real content survives — including punctuation-heavy and CJK names.
    assert degenerate_title('Intro') is False
    assert degenerate_title('AC/DC') is False
    assert degenerate_title('残酷な天使のテーゼ') is False
    assert degenerate_title('99 Problems') is False


def test_collision_queries_only_for_services_with_an_artist_id_column():
    # Tidal enriches artists via tidal_id → three parameterless SQLs.
    queries = build_artist_collision_queries('tidal')
    assert queries is not None
    count_clusters, count_rows, reset = queries
    assert 'HAVING COUNT(*) > 1' in count_clusters
    assert 'tidal_id' in reset and 'tidal_match_status = NULL' in reset
    # Bandcamp has no artist support at all → no collision repair.
    assert build_artist_collision_queries('bandcamp') is None


def test_degenerate_reset_query_respects_entity_support_and_matched_guard():
    # Discogs doesn't enrich tracks — no reset there.
    assert build_degenerate_reset_query('discogs', 'track', [1]) is None
    # Empty id list → nothing to build.
    assert build_degenerate_reset_query('tidal', 'track', []) is None
    sql, params = build_degenerate_reset_query('tidal', 'track', [7, 8])
    # Only rows the service actually MATCHED reset — pending/not_found stay.
    assert "tidal_match_status = 'matched'" in sql
    assert params == [7, 8]


# ------------------------------------------------------------ db orchestrator

def _build_db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'verify_matches.db'))
    with db._get_connection() as conn:
        c = conn.cursor()
        # The smear: two artists sharing ONE tidal id. At most one is right,
        # so the whole cluster resets for a clean rematch.
        c.execute("""INSERT INTO artists (id, name, tidal_id, tidal_match_status)
                     VALUES (1, 'Kendrick Lamar', 'T-SMEAR', 'matched')""")
        c.execute("""INSERT INTO artists (id, name, tidal_id, tidal_match_status)
                     VALUES (2, 'SZA', 'T-SMEAR', 'matched')""")
        # A healthy unique match — must survive untouched.
        c.execute("""INSERT INTO artists (id, name, tidal_id, tidal_match_status)
                     VALUES (3, 'Radiohead', 'T-OK', 'matched')""")
        c.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 3, 'OK Computer')")
        # A degenerate-titled track the old lookup 'matched' — resets.
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title,
                                         tidal_id, tidal_match_status)
                     VALUES (1, 1, 3, '!!!', 'T-JUNK', 'matched')""")
        # A real title, matched — survives.
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title,
                                         tidal_id, tidal_match_status)
                     VALUES (2, 1, 3, 'Airbag', 'T-AIRBAG', 'matched')""")
        # Degenerate but never matched by tidal — nothing to repair.
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title,
                                         tidal_id, tidal_match_status)
                     VALUES (3, 1, 3, '...', NULL, NULL)""")
        conn.commit()
    return db


def test_degenerate_entity_ids_scans_every_table_once(tmp_path):
    db = _build_db(tmp_path)
    ids = db.degenerate_entity_ids()
    assert ids['artist'] == []
    assert ids['album'] == []
    # ids are TEXT in this schema (polymorphic-id design) — string contract.
    assert sorted(ids['track']) == ['1', '3']


def test_verify_resets_the_smear_cluster_and_degenerate_match_only(tmp_path):
    db = _build_db(tmp_path)
    result = db.verify_enrichment_matches('tidal')
    assert result == {'collision_clusters': 1, 'collision_rows': 2,
                      'degenerate_reset': 1}

    with db._get_connection() as conn:
        c = conn.cursor()
        rows = dict(c.execute(
            'SELECT id, tidal_id FROM artists').fetchall())
        # The whole cluster reset; the unique match untouched. (TEXT ids.)
        assert rows['1'] is None and rows['2'] is None
        assert rows['3'] == 'T-OK'
        statuses = dict(c.execute(
            'SELECT id, tidal_match_status FROM artists').fetchall())
        assert statuses['1'] is None and statuses['2'] is None
        assert statuses['3'] == 'matched'

        tracks = {tid: (sid, ms) for tid, sid, ms in c.execute(
            'SELECT id, tidal_id, tidal_match_status FROM tracks').fetchall()}
        # '!!!' fully reset for rematch; 'Airbag' untouched; the never-matched
        # degenerate row stays exactly as it was (nothing to repair).
        assert tracks['1'] == (None, None)
        assert tracks['2'] == ('T-AIRBAG', 'matched')
        assert tracks['3'] == (None, None)


def test_verify_is_idempotent_and_service_scoped(tmp_path):
    db = _build_db(tmp_path)
    # Another service's columns are untouched by a tidal repair.
    with db._get_connection() as conn:
        conn.execute("""UPDATE artists SET deezer_id = 'D-SMEAR',
                        deezer_match_status = 'matched' WHERE id IN (1, 2)""")
        conn.commit()
    db.verify_enrichment_matches('tidal')
    second = db.verify_enrichment_matches('tidal')
    assert second == {'collision_clusters': 0, 'collision_rows': 0,
                      'degenerate_reset': 0}
    with db._get_connection() as conn:
        c = conn.cursor()
        deezer = c.execute(
            'SELECT deezer_id FROM artists WHERE id = 1').fetchone()[0]
        assert deezer == 'D-SMEAR'
    # ...and the deezer repair then catches its own smear independently.
    deezer_result = db.verify_enrichment_matches('deezer')
    assert deezer_result['collision_rows'] == 2


def test_precomputed_degenerates_are_honored(tmp_path):
    # The global sweep computes the (service-independent) title scan ONCE and
    # hands it to each service — the per-service path must use it verbatim.
    db = _build_db(tmp_path)
    result = db.verify_enrichment_matches('tidal', degenerates={'track': ['2']})
    # Track 2 is matched, so the caller-supplied list resets it even though
    # its title is real — proving the injected scan is what's used.
    assert result['degenerate_reset'] == 1
