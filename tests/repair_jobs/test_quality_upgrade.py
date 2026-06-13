"""Quality Upgrade Finder job — the findings-based replacement for the old
auto-acting Quality Scanner.

The old tool judged quality by file EXTENSION only and used min() of the enabled
tiers, so with the default profile (FLAC + MP3-320 + MP3-256 enabled) it flagged
EVERY non-lossless file — a 320 kbps MP3 included — and dumped them all into the
wishlist with no review. These tests pin the corrected behavior: bitrate-aware,
honors every enabled bucket, and only proposes (findings) rather than auto-acting.
"""

from __future__ import annotations

import types

import core.repair_jobs.quality_upgrade as qu
from core.repair_jobs.base import JobContext, JobResult


# Profiles ------------------------------------------------------------------

BALANCED = {  # default: FLAC + MP3-320 + MP3-256 enabled, MP3-192 off
    'qualities': {
        'flac': {'enabled': True, 'min_kbps': 500},
        'mp3_320': {'enabled': True, 'min_kbps': 280},
        'mp3_256': {'enabled': True, 'min_kbps': 200},
        'mp3_192': {'enabled': False, 'min_kbps': 150},
    }
}
LOSSLESS_ONLY = {
    'qualities': {
        'flac': {'enabled': True, 'min_kbps': 500},
        'mp3_320': {'enabled': False, 'min_kbps': 280},
        'mp3_256': {'enabled': False, 'min_kbps': 200},
        'mp3_192': {'enabled': False, 'min_kbps': 150},
    }
}
NOTHING_ENABLED = {'qualities': {'flac': {'enabled': False}, 'mp3_320': {'enabled': False}}}


# --- pure quality decision -------------------------------------------------

def test_balanced_profile_accepts_320_mp3_REGRESSION():
    """The headline bug: with FLAC+320+256 enabled, a 320 kbps MP3 is acceptable.
    The old min()-tier logic flagged it (and every other MP3) for re-download."""
    assert meets('song.mp3', 320, BALANCED) is True


def test_balanced_profile_accepts_256_mp3():
    assert meets('song.mp3', 256, BALANCED) is True


def test_balanced_profile_flags_low_bitrate_mp3():
    assert meets('song.mp3', 128, BALANCED) is False
    assert meets('song.mp3', 192, BALANCED) is False  # below the 256 floor


def test_flac_always_meets_when_flac_enabled():
    assert meets('song.flac', 900, BALANCED) is True
    assert meets('song.flac', 900, LOSSLESS_ONLY) is True


def test_lossless_only_flags_every_lossy_regardless_of_bitrate():
    assert meets('song.mp3', 320, LOSSLESS_ONLY) is False
    assert meets('song.m4a', 256, LOSSLESS_ONLY) is False


def test_nothing_enabled_flags_nothing():
    """Empty/disabled profile must NOT flag the whole library."""
    assert meets('song.mp3', 64, NOTHING_ENABLED) is True


def test_bitrate_in_bps_is_normalized():
    """Library bitrate stored as bps (320000) classifies the same as 320 kbps."""
    assert qu.classify_track_quality('song.mp3', 320000) == qu.RANK_320
    assert meets('song.mp3', 320000, BALANCED) is True


def test_unknown_lossy_bitrate_not_flagged_under_lossy_floor():
    """A lossy file with no bitrate can't be judged against a lossy floor → don't
    flag (avoid false positives); but under a lossless floor it's clearly below."""
    assert meets('song.mp3', None, BALANCED) is True
    assert meets('song.mp3', None, LOSSLESS_ONLY) is False


def test_floor_is_worst_enabled_not_best():
    # FLAC+320+256 enabled → floor is MP3-256 (rank 2), not FLAC.
    assert qu.preferred_quality_floor(BALANCED) == qu.RANK_256
    assert qu.preferred_quality_floor(LOSSLESS_ONLY) == qu.RANK_LOSSLESS
    assert qu.preferred_quality_floor(NOTHING_ENABLED) is None


def meets(path, bitrate, profile):
    return qu.meets_preferred_quality(path, bitrate, profile)


# --- scan produces a finding (seam) ----------------------------------------

class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows, profile):
        self._rows = rows
        self._profile = profile

    def get_quality_profile(self):
        return self._profile

    def _get_connection(self):
        return _FakeConn(self._rows)

    def get_watchlist_artists(self, profile_id=1):
        return [types.SimpleNamespace(artist_name='Artist A')]


def _ctx(db, findings):
    return JobContext(
        db=db,
        transfer_folder='/tmp',
        config_manager=None,
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False,
        is_paused=lambda: False,
    )


def test_scan_creates_finding_for_low_quality_track(monkeypatch):
    # One 128 kbps MP3 (below the balanced floor) for Artist A.
    rows = [(1, 'Song One', '/music/a.mp3', 128, 'Artist A', 'Album X', 10)]
    db = _FakeDB(rows, BALANCED)

    # Stub the metadata side so the test stays offline.
    monkeypatch.setattr(qu, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(qu, 'get_source_priority', lambda src: ['spotify'])
    monkeypatch.setattr(
        'core.matching_engine.MusicMatchingEngine',
        lambda: types.SimpleNamespace(
            generate_download_queries=lambda t: ['q'],
            similarity_score=lambda a, b: 1.0,
            normalize_string=lambda s: s,
        ),
    )
    fake_match = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'],
                  'album': {'name': 'Album X', 'images': []}}
    monkeypatch.setattr(qu, '_find_best_match',
                        lambda *a, **k: (fake_match, 0.95, 'spotify', True))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda track, src: dict(fake_match))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    findings = []
    job = qu.QualityUpgradeJob()
    # default scope 'watchlist'; config_manager None → defaults used
    result = job.scan(_ctx(db, findings))

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f['finding_type'] == 'quality_upgrade'
    assert f['entity_id'] == '1'
    # Album context + matched track carried for the apply step.
    assert f['details']['matched_track_data']['id'] == 'sp1'
    assert f['details']['album_title'] == 'Album X'
    assert f['details']['provider'] == 'spotify'


def test_match_via_isrc_accepts_exact_match(monkeypatch):
    """The guard accepts only a candidate whose own ISRC equals ours (dash/case
    insensitive), so it survives a source returning unrelated hits first."""
    monkeypatch.setattr(qu, 'get_client_for_source',
                        lambda src: types.SimpleNamespace(search_tracks=lambda *a, **k: []))
    monkeypatch.setattr(qu, '_search_tracks_for_source', lambda *a, **k: [
        {'id': 'x', 'name': 'Wrong', 'isrc': 'ZZISRC000000'},
        {'id': 'sp1', 'name': 'Right', 'isrc': 'US-RC1-76-07839'},  # dashed form
    ])
    best, source = qu._match_via_isrc('USRC17607839', ['spotify'])
    assert best['id'] == 'sp1'
    assert source == 'spotify'


def test_match_via_isrc_rejects_all_mismatches(monkeypatch):
    monkeypatch.setattr(qu, 'get_client_for_source',
                        lambda src: types.SimpleNamespace(search_tracks=lambda *a, **k: []))
    monkeypatch.setattr(qu, '_search_tracks_for_source', lambda *a, **k: [
        {'id': 'x', 'name': 'Wrong', 'external_ids': {'isrc': 'ZZISRC000000'}},
    ])
    assert qu._match_via_isrc('USRC17607839', ['spotify']) == (None, None)


def test_scan_prefers_isrc_exact_match_over_fuzzy(monkeypatch):
    """When the file carries an ISRC and it resolves, use the exact match and do
    NOT run the fuzzy search at all."""
    rows = [(1, 'Song One', '/music/a.mp3', 128, 'Artist A', 'Album X', 10)]
    db = _FakeDB(rows, BALANCED)
    monkeypatch.setattr(qu, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(qu, 'get_source_priority', lambda src: ['spotify'])
    monkeypatch.setattr('core.matching_engine.MusicMatchingEngine', lambda: types.SimpleNamespace())
    monkeypatch.setattr(qu, '_read_track_isrc', lambda fp: 'USRC17607839')
    fake = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'], 'album': {'name': 'Album X'}}
    monkeypatch.setattr(qu, '_match_via_isrc', lambda isrc, sp: (fake, 'spotify'))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda t, s: dict(fake))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    def _boom(*a, **k):
        raise AssertionError("fuzzy search must not run when an ISRC match exists")
    monkeypatch.setattr(qu, '_find_best_match', _boom)

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 1
    assert findings[0]['details']['matched_via'] == 'isrc'
    assert findings[0]['details']['match_confidence'] == 1.0


def test_scan_falls_back_to_search_without_isrc(monkeypatch):
    """No usable ISRC → fall back to fuzzy search."""
    rows = [(1, 'Song One', '/music/a.mp3', 128, 'Artist A', 'Album X', 10)]
    db = _FakeDB(rows, BALANCED)
    monkeypatch.setattr(qu, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(qu, 'get_source_priority', lambda src: ['spotify'])
    monkeypatch.setattr('core.matching_engine.MusicMatchingEngine', lambda: types.SimpleNamespace())
    monkeypatch.setattr(qu, '_read_track_isrc', lambda fp: '')  # un-enriched
    fake = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'], 'album': {'name': 'Album X'}}
    monkeypatch.setattr(qu, '_find_best_match', lambda *a, **k: (fake, 0.88, 'spotify', True))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda t, s: dict(fake))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 1
    assert findings[0]['details']['matched_via'] == 'search'


def test_scan_skips_tracks_meeting_quality(monkeypatch):
    # A 320 kbps MP3 meets the balanced profile → no finding, no metadata calls.
    rows = [(2, 'Good Song', '/music/b.mp3', 320, 'Artist A', 'Album Y', 11)]
    db = _FakeDB(rows, BALANCED)

    def _boom(*a, **k):  # must never be called for an acceptable track
        raise AssertionError("matching should not run for an acceptable track")

    monkeypatch.setattr(qu, '_find_best_match', _boom)

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 0
    assert result.skipped == 1
    assert findings == []


# --- fix handler adds to wishlist ------------------------------------------

def test_fix_handler_adds_matched_track_to_wishlist():
    from core.repair_worker import RepairWorker

    captured = {}

    class _DB:
        def add_to_wishlist(self, **kw):
            captured.update(kw)
            return True

    worker = object.__new__(RepairWorker)
    worker.db = _DB()

    details = {
        'matched_track_data': {'id': 'sp1', 'name': 'Song One',
                               'album': {'name': 'Album X'}},
        'current_format': 'MP3 192', 'current_bitrate': 192,
        'album_title': 'Album X', 'provider': 'spotify', 'match_confidence': 0.9,
    }
    res = worker._fix_quality_upgrade('track', '1', '/music/a.mp3', details)

    assert res['success'] is True
    assert captured['spotify_track_data']['id'] == 'sp1'
    assert captured['source_type'] == 'repair'
    assert captured['source_info']['job'] == 'quality_upgrade'
    assert captured['source_info']['album_title'] == 'Album X'
