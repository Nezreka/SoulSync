"""Applying a preserved Quality Upgrade finding after a full database refresh.

Reported on Discord: "the upgrade detector found plenty to upgrade, but when
I select any file to upgrade I get No Matched track in finding."

EVERY finding failing is the tell. A full refresh calls clear_server_data,
which DELETEs every legacy track row and re-inserts it, so each row comes back
with a NEW autoincrement id. Every finding written before that refresh then
points at an id that no longer exists — and the resolver returned None on a
missing row, so the whole batch became unusable at once. Two bugs stacked,
because the per-field fallbacks that were supposed to cover this read
`expected_title` / `expected_artist` — names the download/quarantine flow uses
and the upgrade job never writes — so they could never fire either.

Ported onto Library v2. The native scan (`lib2_upgrade_scan`) raises
`quality_below_cutoff` findings whose subject is `lib2:<id>`, and those ids
survive a refresh, so the orphaning cause is gone for anything scanned today.
What remains is the migrated pre-V2 `quality_upgrade` findings sitting in
users' databases: their legacy subject may well be dead, and the only thing
left to build a redownload from is the finding's own details. That is what
`_legacy_quality_track_data` does and what these tests cover.
"""

from __future__ import annotations

import pytest

from core.repair_worker import RepairWorker


# What the pre-V2 core/repair_jobs/quality_upgrade.py stored on a finding.
FINDING_DETAILS = {
    'track_id': 4242,
    'track_title': 'Comfortably Numb',
    'artist': 'Pink Floyd',
    'album_id': 77,
    'album_title': 'The Wall',
    'current_format': 'MP3 320',
    'current_bitrate': 320,
    'quality_profile_id': 1,
}


@pytest.fixture()
def worker():
    w = RepairWorker.__new__(RepairWorker)

    class _DB:
        pass

    w.db = _DB()
    return w


# ── the reported failure ─────────────────────────────────────────────────────

def test_an_orphaned_finding_still_resolves_from_its_details(worker):
    """The bug. No track row (the refresh renumbered everything), but the
    finding knows perfectly well what the track was."""
    data = worker._legacy_quality_track_data('4242', FINDING_DETAILS)

    assert data is not None, 'returned None — this is the reported failure'
    assert data['name'] == 'Comfortably Numb'
    assert data['artists'][0]['name'] == 'Pink Floyd'
    assert data['album']['name'] == 'The Wall'


def test_the_wishlist_id_is_stable_without_a_row(worker):
    data = worker._legacy_quality_track_data('4242', FINDING_DETAILS)

    assert data['id'] == 'redownload_4242'


def test_a_stored_source_id_is_preferred_over_the_fallback(worker):
    """A finding that carried a real track id must queue under it, or the
    wishlist cannot dedupe it against the same track from another route."""
    data = worker._legacy_quality_track_data(
        '4242', dict(FINDING_DETAILS, spotify_track_id='abc123'))

    assert data['id'] == 'abc123'
    assert data['uri'] == 'spotify:track:abc123'


def test_album_context_the_details_carry_is_kept(worker):
    data = worker._legacy_quality_track_data(
        '4242', dict(FINDING_DETAILS, year=1979, track_count=26))

    assert data['album']['total_tracks'] == 26
    assert data['album']['release_date'] == '1979'


# ── the dead-fallback half ───────────────────────────────────────────────────

def test_it_reads_the_keys_the_job_actually_writes(worker):
    """`track_title`/`artist` are what quality_upgrade.py stored. The resolver
    used to look for `expected_title`/`expected_artist`, which that job never
    wrote, so the fallback was unreachable code."""
    data = worker._legacy_quality_track_data('9', {'track_title': 'Song', 'artist': 'Band'})

    assert data['name'] == 'Song'
    assert data['artists'][0]['name'] == 'Band'


def test_the_older_expected_names_are_still_honoured(worker):
    """The flag-only Quality Check scanner used this vocabulary."""
    data = worker._legacy_quality_track_data(
        '9', {'expected_title': 'Song', 'expected_artist': 'Band'})

    assert data['name'] == 'Song'
    assert data['artists'][0]['name'] == 'Band'


# ── refusing rather than queueing garbage ────────────────────────────────────

def test_a_finding_with_nothing_usable_is_refused(worker):
    """"Unknown - Unknown" on the wishlist would search for nothing forever.
    Better to fail loudly than to queue a row that can never be satisfied."""
    assert worker._legacy_quality_track_data('9', {}) is None


def test_a_title_with_no_artist_is_refused(worker):
    assert worker._legacy_quality_track_data('9', {'track_title': 'Song'}) is None


def test_an_artist_with_no_title_is_refused(worker):
    assert worker._legacy_quality_track_data('9', {'artist': 'Band'}) is None


# ── end to end: what the user actually clicks ────────────────────────────────
#
# The tests above exercise the resolver. This exercises the ACTION — the
# handler the Upgrade button reaches — because a resolver that returns data
# proves nothing if the caller still fails.

def test_clicking_upgrade_on_an_orphaned_finding_now_succeeds(worker):
    captured = {}

    def _add_to_wishlist(spotify_track_data=None, **kwargs):
        captured['track'] = spotify_track_data
        captured['kwargs'] = kwargs
        return True

    worker.db.add_to_wishlist = _add_to_wishlist

    result = worker._fix_legacy_quality_upgrade('track', '4242', '/music/x.mp3',
                                                FINDING_DETAILS)

    assert result['success'] is True, result.get('error')
    assert captured['track']['name'] == 'Comfortably Numb'
    assert captured['track']['artists'][0]['name'] == 'Pink Floyd'
    # The profile the old finding was raised against still gates the download.
    assert captured['kwargs']['quality_profile_id'] == 1


def test_a_pre_searched_match_still_wins(worker):
    """Most migrated findings DO carry a matched replacement; the details
    fallback must not push it aside."""
    captured = {}
    worker.db.add_to_wishlist = lambda spotify_track_data=None, **kw: (
        captured.update(track=spotify_track_data) or True)

    result = worker._fix_legacy_quality_upgrade(
        'track', '4242', '/music/x.mp3',
        dict(FINDING_DETAILS, matched_track_data={'id': 'matched', 'name': 'Matched Version'}))

    assert result['success'] is True, result.get('error')
    assert captured['track']['name'] == 'Matched Version'


def test_a_finding_with_nothing_usable_still_reports_honestly(worker):
    """The refusal must survive for the case it was actually written for."""
    worker.db.add_to_wishlist = lambda **kwargs: True

    result = worker._fix_legacy_quality_upgrade('track', '9', '/music/x.mp3', {})

    assert result['success'] is False
    assert 'no reusable track payload' in result['error']


# ── the scanner's unmatched-file findings ────────────────────────────────────
#
# Reported independently by Lil-Uzi-Chimp: "Quality Check tool fails every
# time — No matched track in finding", via bulk fix.
#
# The pre-V2 Quality Check scanner recorded entity_id=None for any file it
# could not match to a library track row (entity_type='file'). Those findings
# still exist in migrated databases, and their details are all there is.

SCANNER_DETAILS = {
    'quality_issue': 'below_target',
    'current_quality': 'MP3 128',
    'current_format': 'mp3',
    'current_bitrate': 128,
    'expected_title': 'Money',
    'expected_artist': 'Pink Floyd',
    'album_title': 'The Dark Side of the Moon',
    'track_number': 6,
    'file_path': '/music/money.mp3',
}


def test_an_unmatched_file_finding_resolves_from_its_details(worker):
    data = worker._legacy_quality_track_data(None, SCANNER_DETAILS)

    assert data is not None, 'entity_id=None must not mean unresolvable'
    assert data['name'] == 'Money'
    assert data['artists'][0]['name'] == 'Pink Floyd'
    assert data['track_number'] == 6


def test_clicking_upgrade_on_an_unmatched_file_succeeds(worker):
    """The exact user action that failed, end to end."""
    captured = {}
    worker.db.add_to_wishlist = lambda spotify_track_data=None, **kw: (
        captured.update(track=spotify_track_data) or True)

    result = worker._fix_legacy_quality_upgrade('file', None, '/music/money.mp3',
                                                SCANNER_DETAILS)

    assert result['success'] is True, result.get('error')
    assert captured['track']['name'] == 'Money'


def test_two_unmatched_files_do_not_share_a_wishlist_id(worker):
    """A literal "redownload_None" for every unmatched file would collide, and
    the second would be deduped away and silently never downloaded."""
    a = worker._legacy_quality_track_data(None, SCANNER_DETAILS)
    b = worker._legacy_quality_track_data(
        None, dict(SCANNER_DETAILS, expected_title='Time', file_path='/music/time.mp3'))

    assert a['id'] != b['id']
    assert 'None' not in a['id']


def test_the_same_unmatched_file_keeps_the_same_id(worker):
    """Stable across runs, or a re-scan would queue a duplicate."""
    first = worker._legacy_quality_track_data(None, SCANNER_DETAILS)
    second = worker._legacy_quality_track_data(None, dict(SCANNER_DETAILS))

    assert first['id'] == second['id']
