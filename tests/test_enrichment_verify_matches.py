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
from core.library2.match_status import set_library_v2_match
from core.library2.provider_attempts import record_attempt
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


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
    # Tidal ids live in Library-v2's provider-keyed external_ids mapping.
    queries = build_artist_collision_queries('tidal')
    assert queries is not None
    count_clusters, count_rows, reset = queries
    assert 'HAVING COUNT(DISTINCT LOWER(TRIM(a.name))) > 1' in count_clusters
    assert 'lib2_artists' in reset and 'SELECT a.id' in reset
    # Bandcamp has no artist support at all → no collision repair.
    assert build_artist_collision_queries('bandcamp') is None


def test_degenerate_reset_query_respects_entity_support_and_matched_guard():
    # Discogs doesn't enrich tracks — no reset there.
    assert build_degenerate_reset_query('discogs', 'track', [1]) is None
    # Empty id list → nothing to build.
    assert build_degenerate_reset_query('tidal', 'track', []) is None
    sql, params = build_degenerate_reset_query('tidal', 'track', [7, 8])
    # Only rows the service actually MATCHED reset — pending/not_found stay.
    assert "pa.status='matched'" in sql
    assert params == ['track', 'tidal', 7, 8]


# ------------------------------------------------------------ db orchestrator

def _build_db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'verify_matches.db'))
    with db._get_connection() as conn:
        # The smear: two artists sharing ONE tidal id. At most one is right,
        # so the whole cluster resets for a clean rematch.
        a1 = seed_artist(conn, server_id='a1', name='Kendrick Lamar')
        a2 = seed_artist(conn, server_id='a2', name='SZA')
        # A healthy unique match — must survive untouched.
        a3 = seed_artist(conn, server_id='a3', name='Radiohead')
        for artist_id, provider_id in ((a1, 'T-SMEAR'), (a2, 'T-SMEAR'),
                                       (a3, 'T-OK')):
            if artist_id == a2:
                # Seeded with raw SQL on purpose. `set_library_v2_match` now
                # REFUSES to give one provider id to a second entity, which is
                # the corruption this whole repair job exists to clean up — the
                # writer can no longer create it, but databases that predate the
                # guard still contain it.
                conn.execute(
                    "UPDATE lib2_artists SET external_ids=? WHERE id=?",
                    ('{"tidal":"T-SMEAR"}', artist_id),
                )
            else:
                set_library_v2_match(conn, 'artist', artist_id, 'tidal', provider_id)
            record_attempt(conn, entity_type='artist', entity_id=artist_id,
                           service='tidal', status='matched')
        album = seed_album(conn, server_id='al1', title='OK Computer', artist_id=a3)
        # A degenerate-titled track the old lookup 'matched' — resets.
        t1 = seed_track(conn, server_id='t1', title='!!!', album_id=album,
                        artist_id=a3)
        # A real title, matched — survives.
        t2 = seed_track(conn, server_id='t2', title='Airbag', album_id=album,
                        artist_id=a3)
        for track_id, provider_id in ((t1, 'T-JUNK'), (t2, 'T-AIRBAG')):
            set_library_v2_match(conn, 'track', track_id, 'tidal', provider_id)
            record_attempt(conn, entity_type='track', entity_id=track_id,
                           service='tidal', status='matched')
        # Degenerate but never matched by tidal — nothing to repair.
        seed_track(conn, server_id='t3', title='...', album_id=album,
                   artist_id=a3)
        conn.commit()
    return db


def test_degenerate_entity_ids_scans_every_table_once(tmp_path):
    db = _build_db(tmp_path)
    ids = db.degenerate_entity_ids()
    assert ids['artist'] == []
    assert ids['album'] == []
    assert sorted(ids['track']) == [1, 3]


def test_verify_resets_the_smear_cluster_and_degenerate_match_only(tmp_path):
    db = _build_db(tmp_path)
    result = db.verify_enrichment_matches('tidal')
    assert result == {'collision_clusters': 1, 'collision_rows': 2,
                      'degenerate_reset': 1}

    with db._get_connection() as conn:
        c = conn.cursor()
        rows = dict(c.execute(
            "SELECT id, json_extract(external_ids, '$.tidal') FROM lib2_artists"
        ).fetchall())
        assert rows[1] is None and rows[2] is None
        assert rows[3] == 'T-OK'

        tracks = dict(c.execute(
            "SELECT id, json_extract(external_ids, '$.tidal') FROM lib2_tracks"
        ).fetchall())
        # '!!!' fully reset for rematch; 'Airbag' untouched; the never-matched
        # degenerate row stays exactly as it was (nothing to repair).
        assert tracks[1] is None
        assert tracks[2] == 'T-AIRBAG'
        assert tracks[3] is None


def test_verify_is_idempotent_and_service_scoped(tmp_path):
    db = _build_db(tmp_path)
    # Another service's columns are untouched by a tidal repair. Seeded raw for
    # the same reason as the tidal smear: the writer refuses to create it now.
    with db._get_connection() as conn:
        set_library_v2_match(conn, 'artist', 1, 'deezer', 'D-SMEAR')
        conn.execute(
            "UPDATE lib2_artists SET external_ids=json_set("
            "COALESCE(external_ids,'{}'), '$.deezer', 'D-SMEAR') WHERE id=2")
        conn.commit()
    db.verify_enrichment_matches('tidal')
    second = db.verify_enrichment_matches('tidal')
    assert second == {'collision_clusters': 0, 'collision_rows': 0,
                      'degenerate_reset': 0}
    with db._get_connection() as conn:
        c = conn.cursor()
        deezer = c.execute(
            "SELECT json_extract(external_ids, '$.deezer') "
            "FROM lib2_artists WHERE id=1").fetchone()[0]
        assert deezer == 'D-SMEAR'
    # ...and the deezer repair then catches its own smear independently.
    deezer_result = db.verify_enrichment_matches('deezer')
    assert deezer_result['collision_rows'] == 2


def test_precomputed_degenerates_are_honored(tmp_path):
    # The global sweep computes the (service-independent) title scan ONCE and
    # hands it to each service — the per-service path must use it verbatim.
    db = _build_db(tmp_path)
    result = db.verify_enrichment_matches('tidal', degenerates={'track': [2]})
    # Track 2 is matched, so the caller-supplied list resets it even though
    # its title is real — proving the injected scan is what's used.
    assert result['degenerate_reset'] == 1
