"""Seam tests for the Similar-Artists enrichment worker's pure logic.

The worker fills the similar_artists table for LIBRARY artists (the watchlist
scanner only does watchlist artists). These tests exercise the import-light
seams in isolation — no DB, no MusicMap — via injected fakes:

  - pick_source_artist_id       → keying priority (and skip un-matched artists)
  - map_payload_to_store_kwargs → MusicMap {id,source} → the right id column
  - process_artist              → fetch→match→store orchestration + status codes
"""

from __future__ import annotations

import core.similar_artists_worker as w


# --------------------------------------------------------------------------
# pick_source_artist_id — which id keys the artist's similars (must match the
# watchlist scanner's priority so both write the SAME source_artist_id).
# --------------------------------------------------------------------------

def test_pick_source_artist_id_priority():
    row = {'spotify_artist_id': 'sp1', 'itunes_artist_id': 'it1', 'deezer_id': 'dz1'}
    assert w.pick_source_artist_id(row) == 'sp1'           # spotify wins
    assert w.pick_source_artist_id({'itunes_artist_id': 'it1', 'deezer_id': 'dz1'}) == 'it1'
    assert w.pick_source_artist_id({'deezer_id': 'dz1'}) == 'dz1'
    assert w.pick_source_artist_id({'musicbrainz_id': 'mb1'}) == 'mb1'


def test_pick_source_artist_id_none_when_unmatched():
    # Library artist not matched to any metadata source yet → skip (None).
    assert w.pick_source_artist_id({'spotify_artist_id': None, 'itunes_artist_id': ''}) is None
    assert w.pick_source_artist_id({}) is None


# --------------------------------------------------------------------------
# map_payload_to_store_kwargs — MusicMap payload {id, source} → store kwarg.
# --------------------------------------------------------------------------

def test_map_payload_each_source():
    assert w.map_payload_to_store_kwargs({'id': 'x', 'source': 'spotify'}) == {'similar_artist_spotify_id': 'x'}
    assert w.map_payload_to_store_kwargs({'id': 'x', 'source': 'itunes'}) == {'similar_artist_itunes_id': 'x'}
    assert w.map_payload_to_store_kwargs({'id': 'x', 'source': 'deezer'}) == {'similar_artist_deezer_id': 'x'}
    assert w.map_payload_to_store_kwargs({'id': 'x', 'source': 'musicbrainz'}) == {'similar_artist_musicbrainz_id': 'x'}


def test_map_payload_unknown_source_or_no_id():
    # discogs has no column → name-only (empty kwargs), not a crash.
    assert w.map_payload_to_store_kwargs({'id': 'x', 'source': 'discogs'}) == {}
    assert w.map_payload_to_store_kwargs({'source': 'spotify'}) == {}  # no id


# --------------------------------------------------------------------------
# process_artist — fetch → store, status classification, keying.
# --------------------------------------------------------------------------

def _capture_store():
    calls = []

    def store(**kwargs):
        calls.append(kwargs)
        return True
    return store, calls


def test_process_artist_matched_stores_with_keying():
    store, calls = _capture_store()
    payload = {
        'success': True,
        'similar_artists': [
            {'name': 'B', 'id': 'sp_b', 'source': 'spotify', 'genres': ['rap'], 'popularity': 70},
            {'name': 'C', 'id': 'it_c', 'source': 'itunes', 'image_url': 'http://x'},
        ],
    }
    status, count, detail = w.process_artist('SRC1', 'A', lambda n, l: payload, store, limit=25, profile_id=1)
    assert status == 'matched' and count == 2 and detail == ''
    # All similars keyed by the SOURCE artist id we passed (not the library PK).
    assert all(c['source_artist_id'] == 'SRC1' for c in calls)
    assert all(c['profile_id'] == 1 for c in calls)
    # Provider id mapped to the right column; rank preserved in order.
    assert calls[0]['similar_artist_spotify_id'] == 'sp_b' and calls[0]['similarity_rank'] == 1
    assert calls[1]['similar_artist_itunes_id'] == 'it_c' and calls[1]['similarity_rank'] == 2
    assert calls[0]['genres'] == ['rap'] and calls[0]['popularity'] == 70


def test_process_artist_not_found_when_no_matches():
    store, calls = _capture_store()
    status, count, detail = w.process_artist('S', 'A', lambda n, l: {'success': True, 'similar_artists': []}, store)
    assert status == 'not_found' and count == 0 and calls == [] and detail == 'no matches'


def test_process_artist_not_found_on_404():
    # Genuinely no MusicMap entry — shouldn't be retried as an error.
    store, _ = _capture_store()
    status, _, _ = w.process_artist('S', 'A', lambda n, l: {'success': False, 'status_code': 404}, store)
    assert status == 'not_found'


def test_process_artist_error_on_outage_is_retriable_and_explains_why():
    # 5xx / no providers → transient error (retried after retry_days), and the
    # reason is surfaced (code + message) so the cause is diagnosable, not silent.
    store, _ = _capture_store()
    status, _, detail = w.process_artist(
        'S', 'A', lambda n, l: {'success': False, 'status_code': 502, 'error': 'Failed to fetch from MusicMap'}, store)
    assert status == 'error'
    assert '502' in detail and 'MusicMap' in detail


def test_process_artist_error_when_fetch_raises_carries_detail():
    store, _ = _capture_store()

    def boom(n, l):
        raise RuntimeError('musicmap down')
    status, count, detail = w.process_artist('S', 'A', boom, store)
    assert status == 'error' and count == 0 and 'musicmap down' in detail


def test_process_artist_skips_similars_without_a_storable_source_id():
    # Every stored similar MUST carry a metadata source id (spotify/itunes/deezer/
    # musicbrainz) — otherwise it's not actionable. A match on a source with no id
    # column (e.g. discogs) is skipped, never stored name-only.
    store, calls = _capture_store()
    payload = {'success': True, 'similar_artists': [
        {'name': 'KeepMe', 'id': 'sp1', 'source': 'spotify'},
        {'name': 'DropMe', 'id': 'dg1', 'source': 'discogs'},   # no id column → must skip
        {'name': 'NoId', 'source': 'spotify'},                  # no id at all → must skip
    ]}
    status, count, _ = w.process_artist('S', 'A', lambda n, l: payload, store)
    assert count == 1 and len(calls) == 1
    assert calls[0]['similar_artist_name'] == 'KeepMe'
    assert calls[0]['similar_artist_spotify_id'] == 'sp1'


def test_process_artist_skips_unstorable_but_counts_real():
    # A store() that fails for one row shouldn't abort the rest.
    calls = []

    def store(**kwargs):
        calls.append(kwargs)
        return kwargs['similar_artist_name'] != 'B'  # B fails to store
    payload = {'success': True, 'similar_artists': [
        {'name': 'B', 'id': '1', 'source': 'spotify'},
        {'name': 'C', 'id': '2', 'source': 'spotify'},
    ]}
    status, count, _ = w.process_artist('S', 'A', lambda n, l: payload, store)
    assert status == 'matched' and count == 1 and len(calls) == 2
