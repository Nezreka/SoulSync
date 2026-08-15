"""Applying a Quality Upgrade after a full database refresh.

Reported on Discord: "the upgrade detector found plenty to upgrade, but when
I select any file to upgrade I get No Matched track in finding."

EVERY finding failing is the tell. A full refresh calls clear_server_data,
which DELETEs every track for the server and re-inserts it, so each row comes
back with a NEW autoincrement id. Every finding written before that refresh
then points at an id that no longer exists — and the resolver returned None on
a missing row, so the whole batch became unusable at once.

The finding's own details carry the title, artist and album, so the redownload
can be built without the row. Two bugs stacked, because the per-field
fallbacks that were supposed to cover this read `expected_title` /
`expected_artist` — names the download/quarantine flow uses and NO repair job
writes — so they could never fire either.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.repair_worker import RepairWorker


# What core/repair_jobs/quality_upgrade.py actually stores on a finding.
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
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("""CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT,
                     spotify_album_id TEXT, record_type TEXT, track_count INTEGER,
                     year INTEGER, thumb_url TEXT)""")
    conn.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                     track_number INTEGER, duration INTEGER, spotify_track_id TEXT,
                     itunes_track_id TEXT, deezer_id TEXT, artist_id INTEGER,
                     album_id INTEGER)""")
    conn.commit()

    class _KeepOpen:
        """The resolver closes the connection in a finally block; an in-memory
        DB dies with it. sqlite3.Connection.close is read-only, so the no-op
        goes on a proxy rather than the connection."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    w = RepairWorker.__new__(RepairWorker)

    class _DB:
        def _get_connection(self):
            return _KeepOpen(conn)

    w.db = _DB()
    w._raw_conn = conn
    return w


def _seed_track(worker, track_id=4242):
    conn = worker._raw_conn
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Pink Floyd')")
    conn.execute("INSERT INTO albums (id, title, record_type, track_count, year) "
                 "VALUES (77, 'The Wall', 'album', 26, 1979)")
    conn.execute("INSERT INTO tracks (id, title, track_number, duration, artist_id, album_id) "
                 "VALUES (?, 'Comfortably Numb', 6, 382000, 1, 77)", (track_id,))
    conn.commit()


# ── the reported failure ─────────────────────────────────────────────────────

def test_an_orphaned_finding_still_resolves_from_its_details(worker):
    """The bug. No track row (the refresh renumbered everything), but the
    finding knows perfectly well what the track was."""
    data = worker._track_identity_for_redownload('4242', FINDING_DETAILS)

    assert data is not None, 'returned None — this is the reported failure'
    assert data['name'] == 'Comfortably Numb'
    assert data['artists'][0]['name'] == 'Pink Floyd'
    assert data['album']['name'] == 'The Wall'


def test_the_wishlist_id_is_stable_without_a_row(worker):
    data = worker._track_identity_for_redownload('4242', FINDING_DETAILS)

    assert data['id'] == 'redownload_4242'


def test_a_present_row_is_still_preferred(worker):
    """The row is ground truth when it exists — details must not override it."""
    _seed_track(worker)

    data = worker._track_identity_for_redownload('4242', dict(FINDING_DETAILS,
                                                              track_title='Stale Title'))

    assert data['name'] == 'Comfortably Numb'
    assert data['duration_ms'] == 382000
    assert data['track_number'] == 6


def test_the_row_fills_album_fields_the_details_lack(worker):
    _seed_track(worker)

    data = worker._track_identity_for_redownload('4242', FINDING_DETAILS)

    assert data['album']['total_tracks'] == 26
    assert data['album']['release_date'] == '1979'


# ── the dead-fallback half ───────────────────────────────────────────────────

def test_it_reads_the_keys_the_job_actually_writes(worker):
    """`track_title`/`artist` are what quality_upgrade.py stores. The resolver
    used to look for `expected_title`/`expected_artist`, which no repair job
    writes, so the fallback was unreachable code."""
    data = worker._track_identity_for_redownload(
        '9', {'track_title': 'Song', 'artist': 'Band'})

    assert data['name'] == 'Song'
    assert data['artists'][0]['name'] == 'Band'


def test_the_older_expected_names_are_still_honoured(worker):
    """Kept so a producer using the download-flow vocabulary keeps working."""
    data = worker._track_identity_for_redownload(
        '9', {'expected_title': 'Song', 'expected_artist': 'Band'})

    assert data['name'] == 'Song'
    assert data['artists'][0]['name'] == 'Band'


# ── refusing rather than queueing garbage ────────────────────────────────────

def test_a_finding_with_nothing_usable_is_refused(worker):
    """"Unknown - Unknown" on the wishlist would search for nothing forever.
    Better to fail loudly than to queue a row that can never be satisfied."""
    assert worker._track_identity_for_redownload('9', {}) is None


def test_a_title_with_no_artist_is_refused(worker):
    assert worker._track_identity_for_redownload('9', {'track_title': 'Song'}) is None


def test_an_artist_with_no_title_is_refused(worker):
    assert worker._track_identity_for_redownload('9', {'artist': 'Band'}) is None


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

    result = worker._fix_quality_upgrade('track', '4242', '/music/x.mp3', FINDING_DETAILS)

    assert result['success'] is True, result.get('error')
    assert captured['track']['name'] == 'Comfortably Numb'
    assert captured['track']['artists'][0]['name'] == 'Pink Floyd'


def test_the_reported_error_no_longer_fires_for_an_orphaned_finding(worker):
    """Verbatim the message from the report."""
    worker.db.add_to_wishlist = lambda **kwargs: True

    result = worker._fix_quality_upgrade('track', '4242', '/music/x.mp3', FINDING_DETAILS)

    assert result.get('error') != 'No matched track in finding'


def test_a_finding_with_nothing_usable_still_reports_honestly(worker):
    """The error must survive for the case it was actually written for."""
    worker.db.add_to_wishlist = lambda **kwargs: True

    result = worker._fix_quality_upgrade('track', '9', '/music/x.mp3', {})

    assert result['success'] is False
    assert result['error'] == 'No matched track in finding'


# ── the scanner's unmatched-file findings ────────────────────────────────────
#
# Reported independently by Lil-Uzi-Chimp: "Quality Check tool fails every
# time — No matched track in finding", via bulk fix.
#
# The Quality Check scanner records entity_id=None for any file it could not
# match to a library track row (entity_type='file'). The call site was gated
# on `entity_id`, so the resolver was never reached for those findings even
# though their details carry the title and artist. That gate is why it failed
# EVERY time rather than occasionally.

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
    data = worker._track_identity_for_redownload(None, SCANNER_DETAILS)

    assert data is not None, 'entity_id=None must not mean unresolvable'
    assert data['name'] == 'Money'
    assert data['artists'][0]['name'] == 'Pink Floyd'


def test_clicking_upgrade_on_an_unmatched_file_succeeds(worker):
    """The exact user action that failed, end to end."""
    captured = {}
    worker.db.add_to_wishlist = lambda spotify_track_data=None, **kw: (
        captured.update(track=spotify_track_data) or True)

    result = worker._fix_quality_upgrade('file', None, '/music/money.mp3', SCANNER_DETAILS)

    assert result['success'] is True, result.get('error')
    assert captured['track']['name'] == 'Money'


def test_two_unmatched_files_do_not_share_a_wishlist_id(worker):
    """A literal "redownload_None" for every unmatched file would collide, and
    the second would be deduped away and silently never downloaded."""
    a = worker._track_identity_for_redownload(None, SCANNER_DETAILS)
    b = worker._track_identity_for_redownload(
        None, dict(SCANNER_DETAILS, expected_title='Time', file_path='/music/time.mp3'))

    assert a['id'] != b['id']
    assert 'None' not in a['id']


def test_the_same_unmatched_file_keeps_the_same_id(worker):
    """Stable across runs, or a re-scan would queue a duplicate."""
    first = worker._track_identity_for_redownload(None, SCANNER_DETAILS)
    second = worker._track_identity_for_redownload(None, dict(SCANNER_DETAILS))

    assert first['id'] == second['id']
