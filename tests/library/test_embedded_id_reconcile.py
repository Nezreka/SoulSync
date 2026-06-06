"""Tests for core/library/embedded_id_reconcile.py.

The reconcile job reads provider IDs already embedded in a file's tags
(by SoulSync or MusicBrainz Picard) and gap-fills them into the library
DB so enrichment workers skip the API call. These pin the guarantees that
make it safe to run across a whole library while workers run concurrently:

  1. gap-fill only — an existing id is NEVER overwritten,
  2. disagreements are reported as conflicts, not applied,
  3. the write is ATOMICALLY guarded — if a worker fills the column
     between plan and apply, the apply no-ops (no clobber).
"""

from __future__ import annotations

import sqlite3

from core.library.embedded_id_reconcile import (
    Fill,
    ReconcileApplied,
    ReconcilePlan,
    apply_reconcile_plan,
    plan_reconcile,
    reconcile_track_row,
)


# ---------------------------------------------------------------------------
# plan_reconcile — the pure planning layer
# ---------------------------------------------------------------------------

def test_empty_inputs_yield_empty_plan():
    plan = plan_reconcile(None, None)
    assert isinstance(plan, ReconcilePlan)
    assert plan.has_updates is False
    assert plan.filled == 0
    assert plan.conflicts == []


def test_fills_all_three_entities_from_one_file():
    tags = {'spotify_track_id': 'TRK', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'}
    plan = plan_reconcile(tags, {'track': {}, 'album': {}, 'artist': {}})

    assert plan.filled == 3
    by_entity = {(f.entity, f.id_column): f.value for f in plan.fills}
    assert by_entity[('track', 'spotify_track_id')] == 'TRK'
    assert by_entity[('album', 'spotify_album_id')] == 'ALB'
    assert by_entity[('artist', 'spotify_artist_id')] == 'ART'
    # status column pairing is carried on each Fill
    track_fill = plan.fills_for('track')[0]
    assert track_fill.status_column == 'spotify_match_status'


def test_never_overwrites_an_existing_id():
    plan = plan_reconcile({'spotify_artist_id': 'NEW'},
                          {'artist': {'spotify_artist_id': 'EXISTING'}})
    assert plan.filled == 0
    assert plan.fills_for('artist') == []
    assert len(plan.conflicts) == 1
    c = plan.conflicts[0]
    assert c['existing'] == 'EXISTING' and c['embedded'] == 'NEW' and c['entity'] == 'artist'


def test_matching_existing_id_is_noop_not_conflict():
    plan = plan_reconcile({'spotify_artist_id': 'SAME'},
                          {'artist': {'spotify_artist_id': 'SAME'}})
    assert plan.filled == 0
    assert plan.conflicts == []
    assert plan.already_present == 1


def test_blank_and_whitespace_values_ignored():
    tags = {'spotify_artist_id': '   ', 'spotify_album_id': '', 'itunes_track_id': None}
    plan = plan_reconcile(tags, {'track': {}, 'album': {}, 'artist': {}})
    assert plan.has_updates is False


def test_whitespace_padded_embedded_id_is_trimmed_and_filled():
    plan = plan_reconcile({'spotify_track_id': '  TRK  '}, {'track': {}})
    assert plan.fills_for('track')[0].value == 'TRK'


def test_single_column_provider_maps_per_entity():
    # Deezer/Tidal/AudioDB reuse one id column across entity types; fills
    # must be keyed by entity so they don't collide.
    tags = {'deezer_track_id': 'DT', 'deezer_album_id': 'DA', 'deezer_artist_id': 'DR'}
    plan = plan_reconcile(tags, {'track': {}, 'album': {}, 'artist': {}})
    vals = {f.entity: f.value for f in plan.fills}
    assert vals == {'track': 'DT', 'album': 'DA', 'artist': 'DR'}
    assert plan.filled == 3


def test_mb_album_and_artist_filled_track_recording_skipped():
    tags = {'musicbrainz_albumid': 'MBA', 'musicbrainz_artistid': 'MBR', 'musicbrainz_trackid': 'MBT'}
    plan = plan_reconcile(tags, {'track': {}, 'album': {}, 'artist': {}})
    cols = {(f.entity, f.id_column): f.value for f in plan.fills}
    assert cols[('album', 'musicbrainz_release_id')] == 'MBA'
    assert cols[('artist', 'musicbrainz_id')] == 'MBR'
    assert plan.fills_for('track') == []  # recording id not reconciled


def test_lastfm_url_maps_to_track_only():
    # The file carries a single LASTFM_URL = the TRACK's last.fm url. It must
    # fill tracks.lastfm_url and NOT be smeared onto album/artist (whose
    # last.fm urls are different urls entirely).
    plan = plan_reconcile({'lastfm_url': 'https://last.fm/music/A/_/Song'},
                          {'track': {}, 'album': {}, 'artist': {}})
    assert plan.filled == 1
    f = plan.fills_for('track')[0]
    assert f.id_column == 'lastfm_url' and f.status_column == 'lastfm_match_status'
    assert plan.fills_for('album') == [] and plan.fills_for('artist') == []


def test_partial_fill_when_one_entity_already_matched():
    tags = {'spotify_artist_id': 'ART', 'spotify_album_id': 'ALB'}
    current = {'artist': {'spotify_artist_id': 'ART'}, 'album': {}}
    plan = plan_reconcile(tags, current)
    assert plan.filled == 1
    assert plan.fills_for('album')[0].value == 'ALB'
    assert plan.fills_for('artist') == []
    assert plan.already_present == 1


# ---------------------------------------------------------------------------
# apply_reconcile_plan — the DB layer (in-memory sqlite)
# ---------------------------------------------------------------------------

def _make_db():
    conn = sqlite3.connect(':memory:')
    cur = conn.cursor()
    for table, idcol in (('tracks', 'spotify_track_id'), ('albums', 'spotify_album_id'),
                         ('artists', 'spotify_artist_id')):
        cur.execute(f"""CREATE TABLE {table} (id TEXT PRIMARY KEY, {idcol} TEXT,
            spotify_match_status TEXT, spotify_last_attempted TIMESTAMP)""")
    cur.execute("INSERT INTO tracks (id) VALUES ('t1')")
    cur.execute("INSERT INTO albums (id) VALUES ('al1')")
    cur.execute("INSERT INTO artists (id) VALUES ('ar1')")
    conn.commit()
    return conn, cur


def test_apply_writes_ids_status_and_timestamp():
    conn, cur = _make_db()
    plan = plan_reconcile(
        {'spotify_track_id': 'TRK', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'},
        {'track': {}, 'album': {}, 'artist': {}},
    )
    applied = apply_reconcile_plan(cur, {'track': 't1', 'album': 'al1', 'artist': 'ar1'}, plan)
    conn.commit()
    assert isinstance(applied, ReconcileApplied)
    assert applied.rows_updated == 3 and applied.ids_filled == 3

    cur.execute("SELECT spotify_track_id, spotify_match_status, spotify_last_attempted FROM tracks WHERE id='t1'")
    tid, status, attempted = cur.fetchone()
    assert tid == 'TRK' and status == 'matched' and attempted is not None


def test_apply_guard_blocks_overwrite_under_concurrency():
    # THE headline hardening: a worker fills the column AFTER we planned
    # (plan saw empty) but BEFORE we apply. The guarded UPDATE must no-op
    # and leave the worker's value intact.
    conn, cur = _make_db()
    plan = plan_reconcile({'spotify_artist_id': 'FROM_FILE'}, {'artist': {}})  # planned: empty
    # Simulate a concurrent enrichment worker matching it in the meantime.
    cur.execute("UPDATE artists SET spotify_artist_id='FROM_WORKER', spotify_match_status='matched' WHERE id='ar1'")
    conn.commit()

    applied = apply_reconcile_plan(cur, {'artist': 'ar1'}, plan)
    conn.commit()
    assert applied.ids_filled == 0 and applied.rows_updated == 0  # guard blocked it

    cur.execute("SELECT spotify_artist_id FROM artists WHERE id='ar1'")
    assert cur.fetchone()[0] == 'FROM_WORKER'  # worker's value preserved


def test_apply_guard_treats_empty_string_as_fillable():
    conn, cur = _make_db()
    cur.execute("UPDATE artists SET spotify_artist_id='' WHERE id='ar1'")  # empty string, not NULL
    conn.commit()
    plan = plan_reconcile({'spotify_artist_id': 'ART'}, {'artist': {}})
    applied = apply_reconcile_plan(cur, {'artist': 'ar1'}, plan)
    conn.commit()
    assert applied.ids_filled == 1
    cur.execute("SELECT spotify_artist_id FROM artists WHERE id='ar1'")
    assert cur.fetchone()[0] == 'ART'


def test_apply_skips_unknown_columns_without_erroring():
    # Schema missing a provider's columns must not raise — the plan targets
    # tidal_id which this minimal schema lacks; it's silently skipped.
    conn, cur = _make_db()
    plan = plan_reconcile({'tidal_artist_id': 'TID', 'spotify_artist_id': 'ART'},
                          {'track': {}, 'album': {}, 'artist': {}})
    applied = apply_reconcile_plan(cur, {'artist': 'ar1'}, plan)
    conn.commit()
    cur.execute("SELECT spotify_artist_id FROM artists WHERE id='ar1'")
    assert cur.fetchone()[0] == 'ART'
    assert applied.ids_filled == 1  # only the existing spotify column landed


def test_apply_skips_entity_with_no_id():
    conn, cur = _make_db()
    plan = plan_reconcile({'spotify_album_id': 'ALB'}, {'album': {}})
    applied = apply_reconcile_plan(cur, {'track': 't1'}, plan)  # no album id supplied
    assert applied.rows_updated == 0 and applied.ids_filled == 0


def test_apply_empty_plan_is_noop():
    conn, cur = _make_db()
    applied = apply_reconcile_plan(cur, {'track': 't1'}, ReconcilePlan())
    assert applied.rows_updated == 0 and applied.ids_filled == 0


# ---------------------------------------------------------------------------
# reconcile_track_row — the per-track orchestration (id extraction, plan→apply,
# sibling-map freshening)
# ---------------------------------------------------------------------------

def test_reconcile_track_row_unreadable_file_is_noop():
    conn, cur = _make_db()
    result = reconcile_track_row(cur, {'id': 't1'}, {}, {}, None)
    assert result.readable is False
    assert result.applied.ids_filled == 0


def test_reconcile_track_row_fills_track_and_parents():
    conn, cur = _make_db()
    track_row = {'id': 't1', 'album_id': 'al1', 'artist_id': 'ar1'}
    album_map = {'al1': {}}
    artist_map = {'ar1': {}}
    tags = {'spotify_track_id': 'TRK', 'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART'}
    result = reconcile_track_row(cur, track_row, album_map, artist_map, tags)
    conn.commit()
    assert result.readable is True
    assert result.applied.ids_filled == 3 and result.applied.rows_updated == 3
    # parent maps were freshened in place
    assert album_map['al1']['spotify_album_id'] == 'ALB'
    assert artist_map['ar1']['spotify_artist_id'] == 'ART'


def test_reconcile_sibling_tracks_dont_refill_shared_parent():
    # Two tracks on the same album/artist. The first fills the album+artist
    # ids; the second must see them already present (via the freshened map)
    # and NOT re-apply — proving the map keeps siblings from redundant work.
    conn, cur = _make_db()
    cur.execute("INSERT INTO tracks (id) VALUES ('t2')")
    conn.commit()
    album_map = {'al1': {}}
    artist_map = {'ar1': {}}
    tags = {'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART', 'spotify_track_id': 'T1'}

    r1 = reconcile_track_row(cur, {'id': 't1', 'album_id': 'al1', 'artist_id': 'ar1'},
                             album_map, artist_map, tags)
    # Second track: same album/artist ids embedded, its own track id.
    tags2 = {'spotify_album_id': 'ALB', 'spotify_artist_id': 'ART', 'spotify_track_id': 'T2'}
    r2 = reconcile_track_row(cur, {'id': 't2', 'album_id': 'al1', 'artist_id': 'ar1'},
                             album_map, artist_map, tags2)
    conn.commit()

    assert r1.applied.ids_filled == 3            # track + album + artist
    assert r2.applied.ids_filled == 1            # only t2's own track id; parents already filled
    assert r2.conflicts == 0


def test_reconcile_track_row_handles_null_parent_ids():
    conn, cur = _make_db()
    # Track with no album/artist linkage — only its own id should fill.
    result = reconcile_track_row(cur, {'id': 't1', 'album_id': None, 'artist_id': None},
                                 {}, {}, {'spotify_track_id': 'TRK', 'spotify_album_id': 'ALB'})
    conn.commit()
    assert result.applied.ids_filled == 1  # album fill has no album id to land on
    cur.execute("SELECT spotify_track_id FROM tracks WHERE id='t1'")
    assert cur.fetchone()[0] == 'TRK'
