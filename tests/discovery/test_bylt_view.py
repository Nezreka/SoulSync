"""What the BYLT endpoint says, and what it refuses to hide.

the old endpoint dropped saved ids it could not resolve, returned an empty
section list on exception with success: true, and never said whether a track
was already in the library. each of those is a test here.
"""

from core.discovery.bylt_view import (
    UNAVAILABLE_NOT_IN_POOL,
    UNAVAILABLE_NO_ID,
    UNAVAILABLE_SOURCE_UNSUPPORTED,
    empty_payload,
    library_pairs,
    owned_lookup_from_library,
    payload_from_generation,
    payload_from_legacy,
    pool_row_to_dict,
    section_payload,
)


def _row(tid='1', name='Millicent', artist='Halogen', album='Baked'):
    return {'track_id': tid, 'track_name': name, 'artist_name': artist,
            'album_name': album, 'album_cover_url': 'http://c.jpg',
            'duration_ms': 180000, 'popularity': 7, 'source': 'deezer',
            'deezer_track_id': tid, 'relation': 'direct',
            'relation_detail': 'Halogen'}


def _generation(tracks=None):
    return {
        'schema': 1, 'algorithm': 'bylt-v1', 'generation_id': 'g1',
        'profile_id': 1, 'source': 'deezer', 'generated_at': '2026-09-05T09:00:00',
        'status': 'ok',
        'sections': [{
            'seed_key': 'deezer:111', 'seed_name': 'Katy Perry',
            'seed_image': 'http://a.jpg',
            'reason': {'kind': 'direct', 'label': 'Artists similar to Katy Perry'},
            'presentation': 'compact', 'diagnostics': {},
            'tracks': tracks if tracks is not None else [_row()],
        }],
    }


def test_a_section_reports_what_it_asked_for_and_what_it_resolved():
    payload = payload_from_generation(_generation())
    section = payload['sections'][0]
    assert (section['requested'], section['resolved'], section['unavailable']) == (1, 1, 0)


def test_a_row_with_no_id_is_counted_not_silently_dropped():
    broken = dict(_row()); broken['track_id'] = ''
    section = payload_from_generation(_generation([_row(), broken]))['sections'][0]
    assert section['requested'] == 2
    assert section['resolved'] == 1
    assert section['unavailable_reasons'] == {UNAVAILABLE_NO_ID: 1}


def test_the_payload_carries_generation_identity():
    payload = payload_from_generation(_generation())
    assert payload['generation_id'] == 'g1'
    assert payload['source'] == 'deezer'
    assert payload['algorithm'] == 'bylt-v1'
    assert payload['status'] == 'ok'


def test_a_failure_marker_makes_a_served_generation_stale_not_missing():
    payload = payload_from_generation(
        _generation(), failure={'message': 'provider exploded',
                                'attempted_at': '2026-09-05T10:00:00'})
    assert payload['status'] == 'stale'
    assert payload['error'] == 'provider exploded'
    assert payload['sections'][0]['tracks']       # the good content is still served


def test_durations_stay_in_milliseconds_through_the_boundary():
    track = payload_from_generation(_generation())['sections'][0]['tracks'][0]
    assert track['duration_ms'] == 180000


def test_identity_survives_into_the_payload():
    track = payload_from_generation(_generation())['sections'][0]['tracks'][0]
    assert track['id'] == '1'
    assert track['deezer_track_id'] == '1'
    assert track['relation'] == 'direct'


def test_owned_tracks_are_labelled_owned():
    resolved = {('millicent', 'halogen'): {'id': 42}}
    payload = payload_from_generation(
        _generation(), owned_lookup=owned_lookup_from_library(resolved))
    track = payload['sections'][0]['tracks'][0]
    assert track['owned'] is True
    assert track['library_track_id'] == 42


def test_unowned_tracks_are_not_claimed_as_owned():
    payload = payload_from_generation(
        _generation(), owned_lookup=owned_lookup_from_library({}))
    assert payload['sections'][0]['tracks'][0]['owned'] is False


def test_images_pass_through_the_supplied_normaliser():
    payload = payload_from_generation(_generation(), image_fix=lambda u: f'/proxy/{u}')
    assert payload['sections'][0]['artist_image'] == '/proxy/http://a.jpg'
    assert payload['sections'][0]['tracks'][0]['image_url'] == '/proxy/http://c.jpg'


def test_history_scope_is_declared():
    payload = payload_from_generation(
        _generation(), history_scope='shared',
        history_note='Listening history is shared across profiles on this install.')
    assert payload['history_scope'] == 'shared'
    assert 'shared' in payload['history_note']


# ── the legacy path ─────────────────────────────────────────────────────────


def _slot(ids):
    return {'slot': 2, 'seed_key': 'legacy:2', 'seed_name': 'Ariana Grande',
            'track_ids': ids, 'legacy': True, 'heading_scope': 'global'}


def test_legacy_missing_ids_are_explained_not_hidden():
    payload = payload_from_legacy([_slot(['1', '2', '3'])],
                                  {'1': _row('1')}, 'deezer')
    section = payload['sections'][0]
    assert section['requested'] == 3
    assert section['resolved'] == 1
    assert section['unavailable'] == 2
    assert section['unavailable_reasons'] == {UNAVAILABLE_NOT_IN_POOL: 2}


def test_an_unsupported_source_says_so_rather_than_rendering_blank():
    payload = payload_from_legacy([_slot(['1'])], {}, 'discogs')
    assert payload['sections'] == []      # nothing resolved, so no shelf
    # and the reason is available on the section before it is filtered
    section = section_payload({'seed_name': 'x', 'tracks': []})
    assert section['resolved'] == 0
    payload2 = payload_from_legacy([_slot(['1'])], {'1': _row('1')}, 'discogs')
    assert payload2['sections'][0]['unavailable_reasons'] == {}
    payload3 = payload_from_legacy([_slot(['1', '2'])], {'1': _row('1')}, 'discogs')
    assert payload3['sections'][0]['unavailable_reasons'] == {
        UNAVAILABLE_SOURCE_UNSUPPORTED: 1}


def test_legacy_sections_are_marked_legacy():
    payload = payload_from_legacy([_slot(['1'])], {'1': _row('1')}, 'deezer')
    assert payload['legacy'] is True
    assert payload['status'] == 'legacy'
    assert payload['sections'][0]['legacy'] is True
    assert payload['sections'][0]['diagnostics']['heading_scope'] == 'global'


def test_empty_payload_distinguishes_empty_from_failed():
    assert empty_payload(source='deezer')['status'] == 'empty'
    failed = empty_payload(source='deezer', failure={'message': 'boom'})
    assert failed['status'] == 'failed'
    assert failed['error'] == 'boom'


# ── helpers ─────────────────────────────────────────────────────────────────


def test_library_pairs_collects_every_recording_once_per_row():
    pairs = library_pairs(_generation()['sections'])
    assert pairs == [('Millicent', 'Halogen')]


class _Track:
    spotify_track_id = None
    itunes_track_id = None
    deezer_track_id = '9884087'
    track_name = 'Millicent'
    artist_name = 'Halogen'
    album_name = 'Baked'
    album_cover_url = 'http://c.jpg'
    duration_ms = 180000
    popularity = 0
    source = 'deezer'
    track_data_json = None


def test_a_hydrated_pool_row_lands_in_the_stored_shape():
    row = pool_row_to_dict(_Track())
    assert row['track_id'] == '9884087'
    assert row['track_name'] == 'Millicent'
    assert row['duration_ms'] == 180000
