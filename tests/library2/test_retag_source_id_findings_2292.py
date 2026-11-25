"""#2292: a re-tag finding that only writes source ids, an ISRC or a BPM.

the verifier judged every changed row by the validator's check list, and
that list only knows the core fields. a row for ISRC or SPOTIFY_ARTIST_ID
had no check, read as unverified, and the finding stayed pending however
many times it was applied.
"""

import json

from core.library2.validation import finding_verified

ALL_GOOD = {'title': 'correct', 'artist': 'correct', 'album': 'correct', 'cover': 'not_required'}


def _verify(diff, tags, checks=None):
    snapshot = dict(tags)
    snapshot['_validation'] = {'checks': checks or ALL_GOOD}
    finding = {'finding_type': 'library_retag',
               'details': {'diff': diff, 'validation': {'checks': checks or ALL_GOOD}}}
    return finding_verified(None, finding, [{'tags_json': json.dumps(snapshot)}])


def _row(key, wanted):
    return {'field': key, 'file_key': key, 'file_value': '', 'db_value': wanted, 'changed': True}


def test_a_source_id_now_in_the_file_closes_the_finding():
    diff = [_row('SPOTIFY_ARTIST_ID', '4gzpq5DPGxSnKTe4SA8HAU'), _row('isrc', 'GBAYE0000351')]
    tags = {'isrc': 'GBAYE0000351', 'source_ids': {'SPOTIFY_ARTIST_ID': '4gzpq5DPGxSnKTe4SA8HAU'}}
    assert _verify(diff, tags) is not False


def test_a_source_id_still_missing_keeps_it_open():
    diff = [_row('SPOTIFY_ARTIST_ID', '4gzpq5DPGxSnKTe4SA8HAU')]
    assert _verify(diff, {'source_ids': {}}) is False
    assert _verify(diff, {'source_ids': {'SPOTIFY_ARTIST_ID': 'someone-else'}}) is False


def test_a_bpm_is_compared_as_a_number():
    assert _verify([_row('bpm', '120')], {'bpm': 120.0}) is not False
    assert _verify([_row('bpm', '120')], {'bpm': 98}) is False


def test_a_core_field_is_still_judged_by_its_check():
    checks = dict(ALL_GOOD, title='mismatch')
    assert _verify([_row('title', 'Yellow')], {'title': 'Yellow'}, checks) is False
