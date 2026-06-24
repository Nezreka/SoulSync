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
#
# The old extension-only classifier (meets_preferred_quality / classify_track_
# quality / preferred_quality_floor / RANK_*) was deleted in favour of the
# shared v3 path: probe → AudioQuality → quality_meets_profile against the
# profile's ranked targets. These pin the same behavioural contract through the
# new API. (Bps→kbps normalisation now lives in probe_audio_quality and is
# covered by its own tests; the deleted-internals tests for it were removed.)

def meets(path, bitrate, profile):
    """Does a file of this format+bitrate satisfy the profile? Mirrors the
    scanner's decision: build the measured AudioQuality and check it against the
    v3 ranked targets derived from the profile (empty targets → nothing flagged)."""
    from core.quality.model import AudioQuality
    from core.quality.selection import quality_meets_profile, targets_from_profile

    ext = path.rsplit('.', 1)[-1].lower()
    targets, _ = targets_from_profile(profile)
    return quality_meets_profile(AudioQuality(format=ext, bitrate=bitrate), targets)


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


# --- scan produces a finding (seam) ----------------------------------------

class _FakeConn:
    def __init__(self, rows, finding_ids=()):
        self._rows = rows
        self._finding_ids = list(finding_ids)
        self._sql = ''

    def execute(self, sql='', *a, **k):
        self._sql = sql or ''
        return self

    def fetchall(self):
        # The existing-findings query reads repair_findings; everything else is the
        # track load.
        if 'repair_findings' in self._sql:
            return [(fid,) for fid in self._finding_ids]
        return self._rows

    def close(self):
        pass


class _FakeDB:
    def __init__(self, rows, profile, finding_ids=()):
        self._rows = rows
        self._profile = profile
        self._finding_ids = finding_ids

    def get_quality_profile(self):
        return self._profile

    def _get_connection(self):
        return _FakeConn(self._rows, self._finding_ids)

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


def _row(track_id=1, title='Song One', path='/music/a.mp3', bitrate=128, duration=180000,
         artist='Artist A', album='Album X', album_id=10, track_number=6):
    """A track row in _TRACK_COLS order (album source-id columns default to None)."""
    return (track_id, title, path, bitrate, duration, artist, album, album_id, track_number)


def _stub_quality(monkeypatch, *, meets: bool):
    """Stub the v3 quality path so scan() works on fake (non-existent) file paths.

    The job now probes the REAL file (mutagen) and checks it against the v3
    ranked targets. Tests use fake paths, so we resolve the path to itself,
    return a dummy measured quality, and force the meets/below verdict.
    """
    from core.quality.model import AudioQuality, QualityTarget
    monkeypatch.setattr(qu, 'targets_from_profile',
                        lambda profile: ([QualityTarget(label='MP3 320', format='mp3', min_bitrate=320)], False))
    monkeypatch.setattr(qu, 'resolve_library_file_path', lambda p, **kw: p)
    monkeypatch.setattr(qu, 'probe_audio_quality',
                        lambda p: AudioQuality(format='mp3', bitrate=128))
    monkeypatch.setattr(qu, 'quality_meets_profile', lambda aq, targets: meets)


def _stub_engine(monkeypatch):
    # Below-profile by default — the finding-creating tests want a flagged track.
    _stub_quality(monkeypatch, meets=False)
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


def test_scan_creates_finding_for_low_quality_track(monkeypatch):
    db = _FakeDB([_row(bitrate=128)], BALANCED)
    _stub_engine(monkeypatch)
    fake_match = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'],
                  'album': {'name': 'Album X', 'images': []}}
    # No track-id / ISRC / album hit → exercise the search tier.
    monkeypatch.setattr(qu, '_read_file_ids', lambda fp, **kw: {})
    monkeypatch.setattr(qu, '_match_via_track_id', lambda *a, **k: (None, None))
    monkeypatch.setattr(qu, '_match_via_album', lambda *a, **k: (None, None))
    monkeypatch.setattr(qu, '_find_best_match',
                        lambda *a, **k: (fake_match, 0.95, 'spotify', True))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda track, src: dict(fake_match))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))

    assert result.findings_created == 1
    assert len(findings) == 1
    f = findings[0]
    assert f['finding_type'] == 'quality_upgrade'
    assert f['entity_id'] == '1'
    # Album context + matched track carried for the apply step.
    assert f['details']['matched_track_data']['id'] == 'sp1'
    assert f['details']['album_title'] == 'Album X'
    assert f['details']['provider'] == 'spotify'


def test_match_via_track_id_fetches_exact_by_id(monkeypatch):
    """Most-direct tier: a per-source track ID in the tags → get_track_details by ID."""
    track = {'id': 'sp9', 'name': 'Song One', 'album': {'name': 'Album X'}}
    client = types.SimpleNamespace(get_track_details=lambda tid: track if tid == 'sp9' else None)
    monkeypatch.setattr(qu, 'get_client_for_source', lambda src: client)
    best, source = qu._match_via_track_id({'spotify_track_id': 'sp9'}, ['spotify'])
    assert best['id'] == 'sp9'
    assert source == 'spotify'
    assert qu._match_via_track_id({}, ['spotify']) == (None, None)  # no ID → nothing


def test_duration_ok_guard():
    assert qu._duration_ok(180000, 181000) is True      # within 5s
    assert qu._duration_ok(180000, 200000) is False     # 20s off — wrong cut
    assert qu._duration_ok(None, 200000) is True         # unknown → lenient
    assert qu._duration_ok(180000, 0) is True            # unknown → lenient


def test_scan_prefers_track_id_tier(monkeypatch):
    """The source's own track ID (from file tags) wins over every other tier."""
    db = _FakeDB([_row()], BALANCED)
    _stub_engine(monkeypatch)
    monkeypatch.setattr(qu, '_read_file_ids', lambda fp, **kw: {'spotify_track_id': 'sp9', 'isrc': 'X'})
    fake = {'id': 'sp9', 'name': 'Song One', 'album': {'name': 'Album X'}}
    monkeypatch.setattr(qu, '_match_via_track_id', lambda ids, sp: (fake, 'spotify'))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda t, s: dict(fake))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    def _boom(*a, **k):
        raise AssertionError("no lower tier should run when the track-ID tier matches")
    monkeypatch.setattr(qu, '_match_via_isrc', _boom)
    monkeypatch.setattr(qu, '_match_via_album', _boom)
    monkeypatch.setattr(qu, '_find_best_match', _boom)

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 1
    assert findings[0]['details']['matched_via'] == 'track_id'


def test_scan_skips_already_proposed_tracks(monkeypatch):
    """A re-run must not re-resolve a track that already has a finding."""
    db = _FakeDB([_row(track_id=1)], BALANCED, finding_ids=['1'])
    monkeypatch.setattr(qu, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(qu, 'get_source_priority', lambda src: ['spotify'])

    def _boom(*a, **k):
        raise AssertionError("no matching for an already-proposed track")
    monkeypatch.setattr(qu, '_match_via_track_id', _boom)
    monkeypatch.setattr(qu, '_find_best_match', _boom)

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert findings == []
    assert result.findings_skipped_dedup == 1


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
    """No track-ID, but the file carries an ISRC that resolves → use the exact match
    and do NOT run the album/search tiers."""
    db = _FakeDB([_row()], BALANCED)
    _stub_engine(monkeypatch)
    monkeypatch.setattr(qu, '_read_file_ids', lambda fp, **kw: {'isrc': 'USRC17607839'})
    monkeypatch.setattr(qu, '_match_via_track_id', lambda *a, **k: (None, None))
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


def test_scan_falls_back_to_search_without_ids(monkeypatch):
    """No track-ID / ISRC / album hit → fall back to fuzzy search."""
    db = _FakeDB([_row()], BALANCED)
    _stub_engine(monkeypatch)
    monkeypatch.setattr(qu, '_read_file_ids', lambda fp, **kw: {})  # un-enriched
    monkeypatch.setattr(qu, '_match_via_track_id', lambda *a, **k: (None, None))
    monkeypatch.setattr(qu, '_match_via_album', lambda *a, **k: (None, None))
    fake = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'], 'album': {'name': 'Album X'}}
    monkeypatch.setattr(qu, '_find_best_match', lambda *a, **k: (fake, 0.88, 'spotify', True))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda t, s: dict(fake))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 1
    assert findings[0]['details']['matched_via'] == 'search'


def test_scan_uses_album_tier_when_no_ids(monkeypatch):
    """No track-ID / ISRC, but the album→track lookup resolves it → matched_via
    'album', and the fuzzy search is never reached."""
    db = _FakeDB([_row()], BALANCED)
    _stub_engine(monkeypatch)
    monkeypatch.setattr(qu, '_read_file_ids', lambda fp, **kw: {})
    monkeypatch.setattr(qu, '_match_via_track_id', lambda *a, **k: (None, None))
    fake = {'id': 'sp1', 'name': 'Song One', 'artists': ['Artist A'], 'album': {'name': 'Album X'}}
    monkeypatch.setattr(qu, '_match_via_album', lambda *a, **k: (fake, 'spotify'))
    monkeypatch.setattr(qu, '_normalize_track_match', lambda t, s: dict(fake))
    monkeypatch.setattr(qu, '_track_name', lambda t: 'Song One')

    def _boom(*a, **k):
        raise AssertionError("fuzzy search must not run when the album tier matches")
    monkeypatch.setattr(qu, '_find_best_match', _boom)

    findings = []
    result = qu.QualityUpgradeJob().scan(_ctx(db, findings))
    assert result.findings_created == 1
    assert findings[0]['details']['matched_via'] == 'album'
    assert findings[0]['details']['match_confidence'] == 1.0


def test_find_track_in_album_exact_title_with_track_number(monkeypatch):
    items = [
        {'id': 'a', 'name': 'Intro', 'track_number': 1},
        {'id': 'b', 'name': 'Karma Police', 'track_number': 6},
        {'id': 'c', 'name': 'Karma Police (Live)', 'track_number': 12},
    ]
    eng = types.SimpleNamespace(similarity_score=lambda a, b: 0.0, normalize_string=lambda s: s)
    got = qu._find_track_in_album(items, 'Karma Police', 6, eng)
    assert got['id'] == 'b'


def test_scan_skips_tracks_meeting_quality(monkeypatch):
    # A track that meets the profile → no finding, no metadata calls.
    db = _FakeDB([_row(track_id=2, title='Good Song', bitrate=320)], BALANCED)
    _stub_quality(monkeypatch, meets=True)

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
