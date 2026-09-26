"""evaluate_candidates: every row gets a Decision, and the accepted ones are
exactly what get_valid_candidates would have picked.

Uses the real matching engine (scoring, normalization, the Soulseek path
matcher, version detection) so the rejections are the genuine article. Only
the pieces that need a DB or a network (quality profiles, the Soulseek client)
are stood in for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from core.download_plugins.types import TrackResult
from core.downloads import decisions, validation
from core.downloads.decisions import REASON_CODES, Decision, accept, reject
from core.downloads.validation import evaluate_candidates, get_valid_candidates
from core.matching_engine import MusicMatchingEngine
from core.quality.model import QualityTarget


@dataclass
class _Track:
    name: str
    artists: tuple
    duration_ms: int = 238_000
    album: Optional[str] = None


WANT = _Track(name='Fade Into You', artists=('Mazzy Star',), album='So Tonight That I Might See')
FLAC = QualityTarget(label='FLAC 16-bit', format='flac', bit_depth=16)


def _row(**over):
    base = dict(
        username='alice', filename='Mazzy Star/So Tonight/04 - Fade Into You.flac',
        size=30_000_000, bitrate=1411, duration=238_000, quality='flac',
        free_upload_slots=1, upload_speed=2_000_000, queue_length=0,
        artist='Mazzy Star', title='Fade Into You', bit_depth=16, sample_rate=44_100,
    )
    base.update(over)
    return TrackResult(**base)


class _Slsk:
    """Mirrors filter_results_by_quality_preference's order: quarantine,
    size cap, then the profile."""

    def __init__(self, quarantined=(), formats=None):
        self.quarantined = set(quarantined)
        self.formats = formats

    def _drop_quarantined_sources(self, rows):
        return [r for r in rows if (r.username, r.filename) not in self.quarantined]

    def filter_results_by_quality_preference(self, rows, profile_id=None):
        from core.downloads.size_limit import filter_music_candidates
        rows = filter_music_candidates(self._drop_quarantined_sources(rows))
        if self.formats is not None:
            rows = [r for r in rows if r.quality in self.formats]
        return rows


class _Orch:
    def __init__(self, slsk):
        self.slsk = slsk

    def client(self, name):
        return self.slsk if name == 'soulseek' else None


@pytest.fixture
def env(monkeypatch):
    """Real engine, permissive profile, no queue limit, no size cap."""
    slsk = _Slsk()
    monkeypatch.setattr(validation, 'matching_engine', MusicMatchingEngine())
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch(slsk))
    state = {'targets': [], 'fallback': True, 'max_q': 0, 'cap': 0}
    monkeypatch.setattr('core.quality.selection.load_profile_targets',
                        lambda: (state['targets'], state['fallback']))
    monkeypatch.setattr('core.quality.selection.load_profile_by_id',
                        lambda _pid=None: {'ranked_targets': [t.to_dict() for t in state['targets']],
                                           'fallback_enabled': state['fallback']})
    monkeypatch.setattr('core.downloads.size_limit.configured_limit', lambda: state['cap'])
    real_get = validation.config_manager.get

    def fake_get(key, default=None):
        if key == 'soulseek.max_peer_queue':
            return state['max_q']
        return real_get(key, default)

    monkeypatch.setattr(validation.config_manager, 'get', fake_get)
    state['slsk'] = slsk
    return state


def _decision_for(pairs, row) -> Decision:
    for r, d in pairs:
        if r is row:
            return d
    raise AssertionError(f"{row.filename} missing from evaluate_candidates output")


# ---------------------------------------------------------------------------
# decisions.py itself
# ---------------------------------------------------------------------------

def test_reject_refuses_codes_outside_the_table():
    with pytest.raises(ValueError):
        reject('wrong_vibes')
    with pytest.raises(ValueError):
        reject('accepted')


def test_every_code_has_a_known_stage():
    assert set(REASON_CODES.values()) == decisions.STAGES
    for code in REASON_CODES:
        if code != 'accepted':
            assert reject(code).stage == REASON_CODES[code]
    assert accept(0.9).to_dict() == {
        'accepted': True, 'code': 'accepted', 'detail': '', 'stage': 'decision', 'score': 0.9,
    }


def test_caller_set_codes_exist():
    # blacklisted / duplicate are stamped by the inspector, not validation.
    assert reject('blacklisted').stage == 'policy'
    assert reject('duplicate').stage == 'decision'


def test_validation_only_emits_codes_in_the_table():
    """Static guard: a typo'd code on a rarely-hit branch would otherwise only
    blow up on a real install."""
    src = Path(validation.__file__).read_text()
    codes = set(re.findall(r"_note\(\s*why,\s*\w+,\s*'([a-z_]+)'", src, flags=re.S))
    assert codes, "pattern stopped matching; update this guard"
    assert codes <= set(REASON_CODES), codes - set(REASON_CODES)


def test_rejection_counts_orders_by_frequency():
    ds = [reject('match_weak'), reject('preview'), reject('match_weak'), accept(1.0)]
    assert list(decisions.rejection_counts(ds).items()) == [('match_weak', 2), ('preview', 1)]


# ---------------------------------------------------------------------------
# one test per code validation can emit
# ---------------------------------------------------------------------------

def test_accepted_carries_the_match_score(env):
    good = _row()
    pairs = evaluate_candidates([good], WANT, 'q')
    d = _decision_for(pairs, good)
    assert d.accepted and d.code == 'accepted' and d.score and d.score > 0.58


def test_preview(env):
    clip = _row(username='soundcloud', filename='sc||1', duration=30_000)
    d = _decision_for(evaluate_candidates([clip], WANT, 'q'), clip)
    assert d.code == 'preview' and d.stage == 'preview'
    assert d.detail == '0:30 clip for a 3:58 track'


def test_duration_mismatch_on_strict_source(env):
    tidal = _row(username='tidal', filename='tid||1', duration=300_000)
    d = _decision_for(evaluate_candidates([tidal], WANT, 'q'), tidal)
    assert d.code == 'duration_mismatch'
    assert d.detail == '5:00 vs expected 3:58'


def test_artist_mismatch_torrent(env):
    tor = _row(username='torrent', filename='t||1', artist='Hope Sandoval Tribute Band', duration=None)
    d = _decision_for(evaluate_candidates([tor], WANT, 'q'), tor)
    assert d.code == 'artist_mismatch' and d.stage == 'identity'
    assert 'Hope Sandoval Tribute Band' in d.detail


def test_artist_mismatch_soulseek_path(env, monkeypatch):
    # Isolate the path gate: an engine that scores every row as a match, so
    # the only thing left to reject a path without the artist in it is the gate.
    class _PassEngine(MusicMatchingEngine):
        def find_best_slskd_matches_enhanced(self, track, results, max_peer_queue=0):
            for r in results:
                r.confidence = 0.9
            return list(results)

    monkeypatch.setattr(validation, 'matching_engine', _PassEngine())
    stray = _row(filename='Various/Covers 1994/04 - Fade Into You.flac', artist=None)
    d = _decision_for(evaluate_candidates([stray], WANT, 'q'), stray)
    assert d.code == 'artist_mismatch' and d.stage == 'identity'
    assert d.detail == 'no folder or filename names Mazzy Star'


def test_artist_unverified_youtube(env):
    yt = _row(username='youtube', filename='yt||1', artist='Random Channel',
              title='Fade Into You Tonight Forever', quality='opus', bitrate=160,
              bit_depth=None, sample_rate=None)
    d = _decision_for(evaluate_candidates([yt], WANT, 'q'), yt)
    assert d.code == 'artist_unverified' and d.stage == 'identity'


def test_fallthrough_score_miss_keeps_the_structured_lane_reason(env):
    # The structured lane rejects for no artist evidence; the fallthrough's
    # generic path matcher then misses too. The first reason is the useful one.
    yt = _row(username='youtube', filename='yt||1', artist='Random Channel',
              title='Fade Into You Slowed Down Forever', quality='opus', bitrate=160,
              bit_depth=None, sample_rate=None)
    d = _decision_for(evaluate_candidates([yt], WANT, 'q'), yt)
    assert d.code == 'artist_unverified'


def test_version_conflict_streaming(env):
    live = _row(username='qobuz', filename='qb||1', title='Fade Into You (Live)')
    d = _decision_for(evaluate_candidates([live], WANT, 'q'), live)
    assert d.code == 'version_conflict'
    assert d.detail.startswith('live version, asked for the original (match 0.58')


def test_version_conflict_when_a_version_was_asked_for(env):
    want_live = _Track(name='Fade Into You (Live)', artists=('Mazzy Star',))
    studio = _row(username='qobuz', filename='qb||1', title='Fade Into You')
    d = _decision_for(evaluate_candidates([studio], want_live, 'q'), studio)
    assert d.code == 'version_conflict'
    assert d.detail.startswith("asked for the live version, this isn't marked live")


def test_version_conflict_soulseek_zero_score(env):
    live = _row(filename='Mazzy Star/Live 1994/03 - Fade Into You (Live).flac', title=None)
    d = _decision_for(evaluate_candidates([live], WANT, 'q'), live)
    assert d.code == 'version_conflict' and d.score == 0.0


def test_match_weak(env):
    other = _row(filename='Mazzy Star/So Tonight/01 - Halah.flac', title='Halah')
    d = _decision_for(evaluate_candidates([other], WANT, 'q'), other)
    assert d.code == 'match_weak'
    assert 'needs over 0.58' in d.detail


def test_outranked_youtube(env):
    best = _row(username='youtube', filename='yt||a', quality='opus', bitrate=160,
                bit_depth=None, sample_rate=None)
    far = _row(username='youtube', filename='yt||b', quality='opus', bitrate=256,
               bit_depth=None, sample_rate=None, title='Fade Into You Lyrics Video HD')
    pairs = evaluate_candidates([best, far], WANT, 'q')
    assert _decision_for(pairs, best).accepted
    d = _decision_for(pairs, far)
    assert d.code == 'outranked'
    assert d.detail == 'match 0.77, too far behind the best (0.99) to compete on quality'


def test_below_profile_youtube(env):
    env['targets'], env['fallback'] = [FLAC], False
    yt = _row(username='youtube', filename='yt||a', quality='opus', bitrate=160,
              bit_depth=None, sample_rate=None)
    d = _decision_for(evaluate_candidates([yt], WANT, 'q'), yt)
    assert d.code == 'below_profile' and d.stage == 'quality'


def test_below_profile_prowlarr(env):
    env['targets'], env['fallback'] = [FLAC], False
    tor = _row(username='torrent', filename='t||1', quality='mp3', bitrate=320,
               bit_depth=None, duration=None)
    d = _decision_for(evaluate_candidates([tor], WANT, 'q'), tor)
    assert d.code == 'below_profile'


def test_below_profile_soulseek(env):
    env['slsk'].formats = {'flac'}
    mp3 = _row(filename='Mazzy Star/So Tonight/04 - Fade Into You.mp3', quality='mp3', bitrate=320)
    d = _decision_for(evaluate_candidates([mp3], WANT, 'q'), mp3)
    assert d.code == 'below_profile'
    assert "doesn't meet the quality profile" in d.detail


def test_peer_queue(env):
    env['max_q'] = 5
    free = _row(username='bob')
    busy = _row(username='carol', queue_length=40)
    pairs = evaluate_candidates([free, busy], WANT, 'q')
    assert _decision_for(pairs, free).accepted
    d = _decision_for(pairs, busy)
    assert d.code == 'peer_queue' and d.detail == "40 in the peer's queue, limit 5"


def test_queue_limit_not_blamed_when_every_peer_is_busy(env):
    # The engine falls back to the full list when nobody is under the limit.
    env['max_q'] = 5
    busy = _row(username='carol', queue_length=40)
    assert _decision_for(evaluate_candidates([busy], WANT, 'q'), busy).accepted


def test_quarantined(env):
    bad = _row(username='dave')
    env['slsk'].quarantined = {('dave', bad.filename)}
    d = _decision_for(evaluate_candidates([bad], WANT, 'q'), bad)
    assert d.code == 'quarantined' and d.stage == 'policy'


def test_too_large(env):
    env['cap'] = 10  # MB per minute; 3:58 allows ~39.7 MB
    huge = _row(size=90_000_000)
    d = _decision_for(evaluate_candidates([huge], WANT, 'q'), huge)
    assert d.code == 'too_large'
    assert d.detail == '90 MB, over the 10 MB/min limit'


# ---------------------------------------------------------------------------
# parity: evaluate_candidates must not change what gets downloaded
# ---------------------------------------------------------------------------

def _pool():
    return [
        _row(username='zed', filename='Mazzy Star/So Tonight/04 - Fade Into You.mp3',
             quality='mp3', bitrate=320, bit_depth=None),
        _row(username='amy'),
        _row(username='ben', filename='Mazzy Star/Live/03 - Fade Into You (Live).flac', title=None),
        _row(username='cat', filename='Mazzy Star/So Tonight/01 - Halah.flac', title='Halah'),
        _row(username='dan', queue_length=60),
        _row(username='tidal', filename='tid||1'),
        _row(username='qobuz', filename='qb||1', duration=300_000),
        _row(username='deezer_dl', filename='dz||1', artist='Someone Else', title='Fade Into You'),
        _row(username='youtube', filename='yt||1', quality='opus', bitrate=160,
             bit_depth=None, sample_rate=None),
        _row(username='youtube', filename='yt||2', quality='opus', bitrate=160,
             bit_depth=None, sample_rate=None, title='Fade Into You (Cover)'),
        _row(username='torrent', filename='t||1', duration=None, title='So Tonight That I Might See'),
        _row(username='torrent', filename='t||2', duration=None, quality='mp3', bitrate=320,
             bit_depth=None, artist='Nobody'),
        _row(username='usenet', filename='u||1', duration=None, quality='mp3', bitrate=320,
             bit_depth=None, sample_rate=None),
        _row(username='soundcloud', filename='sc||1', duration=30_000),
    ]


def _key(row):
    return (row.username, row.filename)


CONFIGS = [
    dict(),
    dict(targets=[FLAC], fallback=False),
    dict(targets=[FLAC], fallback=True),
    dict(max_q=10),
    dict(cap=5),
    dict(formats={'flac'}),
    dict(quarantine={('amy', 'Mazzy Star/So Tonight/04 - Fade Into You.flac')}),
]


def _apply(env, cfg):
    env['targets'] = cfg.get('targets', [])
    env['fallback'] = cfg.get('fallback', True)
    env['max_q'] = cfg.get('max_q', 0)
    env['cap'] = cfg.get('cap', 0)
    env['slsk'].formats = cfg.get('formats')
    env['slsk'].quarantined = cfg.get('quarantine', set())


@pytest.mark.parametrize('cfg', CONFIGS, ids=lambda c: ','.join(c) or 'default')
@pytest.mark.parametrize('order', ['as_is', 'reversed'])
def test_accepted_rows_match_get_valid_candidates_exactly(env, cfg, order):
    _apply(env, cfg)

    def pool():
        rows = _pool()
        return rows[::-1] if order == 'reversed' else rows

    legacy = [_key(r) for r in get_valid_candidates(pool(), WANT, 'q')]
    pairs = evaluate_candidates(pool(), WANT, 'q')
    accepted = [_key(r) for r, d in pairs if d.accepted]

    assert accepted == legacy
    # Every input row comes back exactly once.
    assert sorted(_key(r) for r, _ in pairs) == sorted(_key(r) for r in pool())
    # Accepted block first, then rejections.
    flags = [d.accepted for _, d in pairs]
    assert flags == sorted(flags, reverse=True)
    # The fixture is only a guard if both outcomes happen.
    assert legacy and len(legacy) < len(pairs)


@pytest.mark.parametrize('cfg', CONFIGS, ids=lambda c: ','.join(c) or 'default')
def test_no_rejection_goes_unexplained(env, cfg):
    _apply(env, cfg)
    for row, d in evaluate_candidates(_pool(), WANT, 'q'):
        assert d.code != 'unexplained', (row.username, row.filename)
        assert d.code in REASON_CODES


def test_automatic_path_records_nothing(env, monkeypatch):
    """get_valid_candidates must not build decisions: it's the hot path."""
    def boom(*_a, **_kw):
        raise AssertionError('decision built on the automatic path')

    monkeypatch.setattr(decisions, 'reject', boom)
    monkeypatch.setattr(decisions, 'accept', boom)
    env['slsk'].formats = {'flac'}
    out = get_valid_candidates(_pool(), WANT, 'q')
    assert out

