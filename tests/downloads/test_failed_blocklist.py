"""Persistent failed-download blocklist (area F).

Files that terminally failed import (quarantine retries exhausted) are
fingerprinted — SHA1(service | normalized artist | normalized title | size),
Soulseek peers collapsing to 'soulseek' — and skipped by future searches for
90 days. Separate from the user's download blocklist and the quarantine.
Everything fails open.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.downloads import failed_blocklist as fb
from core.downloads.candidate_pool import build_source_rows
from core.downloads.decisions import accept
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _candidate(**over):
    fields = dict(
        username='somepeer', filename='/music/Tool - Xtal.flac',
        artist='Tool', title='Xtal', size=48203911, bitrate=900,
        quality='FLAC', duration=307, confidence=0.9,
        bit_depth=None, sample_rate=None,
        free_upload_slots=1, upload_speed=100, queue_length=0)
    fields.update(over)
    return SimpleNamespace(**fields)


# ── fingerprinting ───────────────────────────────────────────────────────────

def test_fingerprint_is_stable_and_hex():
    fp = fb.fingerprint('soulseek', 'Tool', 'Xtal', 48203911)
    assert fp == fb.fingerprint('soulseek', 'Tool', 'Xtal', 48203911)
    assert len(fp) == 40 and all(c in '0123456789abcdef' for c in fp)


def test_soulseek_peers_collapse_to_the_soulseek_service():
    assert fb.service_for_username('randompeer') == 'soulseek'
    assert fb.service_for_username('SomePeer') == 'soulseek'
    assert fb.normalize_service('slskd') == 'soulseek'
    assert fb.service_for_username('tidal') == 'tidal'
    a = fb.fingerprint('soulseek', 'Tool', 'Xtal', 1)
    assert fb.fingerprint('slskd', 'Tool', 'Xtal', 1) == a
    assert fb.fingerprint('SoulSeek', 'Tool', 'Xtal', 1) == a
    assert fb.fingerprint('tidal', 'Tool', 'Xtal', 1) != a


def test_artist_and_title_are_normalized():
    a = fb.fingerprint('soulseek', 'Tool', 'Xtal', 1)
    assert fb.fingerprint('soulseek', '  TOOL  ', 'xtal', 1) == a
    assert fb.fingerprint('soulseek', 'Tool', 'Xtal', 2) != a


def test_size_is_part_of_the_identity():
    assert fb.fingerprint('soulseek', 'Tool', 'Xtal', '48203911') == \
        fb.fingerprint('soulseek', 'Tool', 'Xtal', 48203911)
    assert fb.fingerprint('soulseek', 'Tool', 'Xtal', 'nope') == \
        fb.fingerprint('soulseek', 'Tool', 'Xtal', 0)


# ── storage ──────────────────────────────────────────────────────────────────

def test_record_and_is_blocked_roundtrip(db):
    assert fb.record(db, service='soulseek', artist='Tool', title='Xtal',
                      size=48203911, reason='acoustid mismatch')
    assert fb.is_blocked(db, service='soulseek', artist='Tool', title='Xtal',
                         size=48203911)
    assert not fb.is_blocked(db, service='soulseek', artist='Tool', title='Xtal',
                             size=1)                      # different size: not it
    assert not fb.is_blocked(db, service='tidal', artist='Tool', title='Xtal',
                             size=48203911)               # different service


def test_expired_entries_do_not_block(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1)
    with db._get_connection() as conn:
        conn.execute("UPDATE failed_download_blocklist SET expires_at = '2000-01-01 00:00:00'")
        conn.commit()
    assert not fb.is_blocked(db, service='soulseek', artist='Tool', title='Xtal', size=1)
    assert fb.clear_expired(db) == 1
    assert fb.list_entries(db) == []


def test_entries_are_capped_oldest_first(db, monkeypatch):
    monkeypatch.setattr(fb, 'MAX_ENTRIES', 3)
    for i in range(5):
        fb.record(db, service='soulseek', artist='Tool', title=f'Track {i}', size=i)
    entries = fb.list_entries(db)
    assert len(entries) == 3
    assert [e['title'] for e in entries] == ['Track 4', 'Track 3', 'Track 2']


def test_re_recording_refreshes_the_entry(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1, reason='old')
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1, reason='new')
    entries = fb.list_entries(db)
    assert len(entries) == 1 and entries[0]['reason'] == 'new'


def test_remove_unblocks(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1)
    fp = fb.fingerprint('soulseek', 'Tool', 'Xtal', 1)
    assert fb.remove(db, fp)
    assert not fb.is_blocked(db, service='soulseek', artist='Tool', title='Xtal', size=1)
    assert not fb.remove(db, fp)


def test_everything_fails_open():
    class Broken:
        def _get_connection(self):
            raise RuntimeError('db gone')

    broken = Broken()
    assert fb.is_blocked(broken, service='s', artist='a', title='t') is False
    assert fb.record(broken, service='s', artist='a', title='t') is False
    assert fb.list_entries(broken) == []
    assert fb.remove(broken, 'x') is False
    assert fb.clear_expired(broken) == 0
    assert fb.is_candidate_blocked(broken, _candidate()) is False
    assert fb.record_task_giveup(broken, {'track_info': {'artist': 'a', 'track': 't'}}) is False


# ── call sites ───────────────────────────────────────────────────────────────

def test_is_candidate_blocked_matches_the_worker_check(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=48203911)
    assert fb.is_candidate_blocked(db, _candidate())
    assert fb.is_candidate_blocked(db, _candidate(), 'soulseek')
    assert not fb.is_candidate_blocked(db, _candidate(username='tidal'))
    assert not fb.is_candidate_blocked(db, _candidate(size=1))


def test_record_task_giveup_fingerprints_the_failed_file(db):
    task = {'username': 'somepeer', 'filename': 'Tool - Xtal.flac',
            'track_info': {'artist': 'Tool', 'track': 'Xtal'},
            'picked_candidate': {'size': 48203911}}
    assert fb.record_task_giveup(db, task, reason='acoustid mismatch')
    assert fb.is_candidate_blocked(db, _candidate())
    # no identity at all: nothing recorded, still fail-open True-ish False
    assert fb.record_task_giveup(db, {}) is False


def test_inspector_rejects_via_the_existing_blacklisted_code(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=48203911)
    rows = build_source_rows(
        [('q', [(_candidate(), accept(0.9))])],
        source_name='soulseek',
        is_blacklisted=lambda u, f: False,
        is_failed_blocked=lambda c: fb.is_candidate_blocked(db, c, 'soulseek'))
    assert rows['candidates'] == []
    assert rows['rejected_total'] == 1
    row = rows['rejected'][0]
    assert row['decision']['code'] == 'blacklisted'
    assert row['blacklisted'] is True
    assert rows['rejected_counts'] == {'blacklisted': 1}


def test_inspector_without_the_blocklist_hook_accepts_as_before(db):
    rows = build_source_rows(
        [('q', [(_candidate(), accept(0.9))])],
        source_name='soulseek',
        is_blacklisted=lambda u, f: False)
    assert len(rows['candidates']) == 1
    assert rows['rejected_total'] == 0


# ── endpoint payloads ────────────────────────────────────────────────────────

def test_list_route_returns_newest_first_and_honours_limit(db):
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1)
    fb.record(db, service='soulseek', artist='Tool', title='Forty Six & 2', size=2)

    data, status = fb.list_route(db)
    assert status == 200
    assert [e['title'] for e in data['entries']] == ['Forty Six & 2', 'Xtal']

    data, status = fb.list_route(db, limit=1)
    assert status == 200 and len(data['entries']) == 1

    data, status = fb.list_route(db, limit='junk')
    assert status == 200 and len(data['entries']) == 2


def test_delete_route_removes_and_reports_missing(db):
    fp = fb.fingerprint('soulseek', 'Tool', 'Xtal', 1)
    fb.record(db, service='soulseek', artist='Tool', title='Xtal', size=1)

    data, status = fb.delete_route(db, None)
    assert (status, data['message']) == (400, "Missing 'fingerprint' in body.")

    data, status = fb.delete_route(db, '0' * 40)
    assert status == 404

    data, status = fb.delete_route(db, fp)
    assert status == 200
    assert not fb.is_blocked(db, service='soulseek', artist='Tool',
                             title='Xtal', size=1)
