"""order_candidates — the candidate sort used by attempt_download_with_candidates.

Default (priority mode) sorts confidence-first, then peer quality — today's
behaviour, locked here as a regression guard. quality_first=True (best-quality
mode) makes the user's profile quality rank dominate, with confidence as the
tiebreaker. Both keep correctly-matched candidates; ordering only changes which
is tried first.
"""

from core.downloads.candidates import order_candidates
from core.quality.model import AudioQuality, QualityTarget


class _Cand:
    def __init__(self, name, aq, confidence, quality_score=0,
                 upload_speed=0, queue_length=0, free_upload_slots=0, size=0):
        self.name = name
        self.audio_quality = aq
        self.confidence = confidence
        self.quality_score = quality_score
        self.upload_speed = upload_speed
        self.queue_length = queue_length
        self.free_upload_slots = free_upload_slots
        self.size = size


FLAC_HI = AudioQuality('flac', sample_rate=96000, bit_depth=24)
FLAC_CD = AudioQuality('flac', sample_rate=44100, bit_depth=16)

TARGETS = [
    QualityTarget(label='FLAC 24', format='flac', bit_depth=24, min_sample_rate=96000),
    QualityTarget(label='FLAC 16', format='flac', bit_depth=16),
]


def test_priority_mode_is_confidence_first():
    hi = _Cand('hi-flac', FLAC_HI, confidence=0.80)
    lo = _Cand('cd-flac', FLAC_CD, confidence=0.95)

    ordered = order_candidates([hi, lo], quality_first=False, targets=TARGETS)

    assert [c.name for c in ordered] == ['cd-flac', 'hi-flac']  # higher confidence wins


def test_quality_first_lets_better_quality_win_over_confidence():
    hi = _Cand('hi-flac', FLAC_HI, confidence=0.80)
    lo = _Cand('cd-flac', FLAC_CD, confidence=0.95)

    ordered = order_candidates([hi, lo], quality_first=True, targets=TARGETS)

    assert [c.name for c in ordered] == ['hi-flac', 'cd-flac']  # 24-bit beats higher-confidence 16-bit


def test_quality_first_uses_confidence_as_tiebreak_within_same_quality():
    a = _Cand('a', FLAC_HI, confidence=0.70)
    b = _Cand('b', FLAC_HI, confidence=0.90)

    ordered = order_candidates([a, b], quality_first=True, targets=TARGETS)

    assert [c.name for c in ordered] == ['b', 'a']  # same quality → confidence breaks tie


def test_quality_first_ranks_unmatched_quality_last():
    matched = _Cand('matched', FLAC_CD, confidence=0.50)
    off_list = _Cand('off', AudioQuality('mp3', bitrate=320), confidence=0.99)

    ordered = order_candidates([off_list, matched], quality_first=True, targets=TARGETS)

    assert [c.name for c in ordered] == ['matched', 'off']  # off-list sorts last despite high confidence


# User Audiophile ladder: Opus ≥192 is the top target. YouTube itag 774 is
# Opus 256. Soulseek FLAC matches a later rung and must not win the walk.
AUDIOPHILE = [
    QualityTarget(label='OPUS ≥ 192', format='opus', min_bitrate=192),
    QualityTarget(label='AAC ≥ 192', format='aac', min_bitrate=192),
    QualityTarget(label='MP3 ≥ 256', format='mp3', min_bitrate=256),
    QualityTarget(label='MP3 ≥ 320', format='mp3', min_bitrate=320),
    QualityTarget(label='FLAC 16', format='flac', bit_depth=16),
    QualityTarget(label='FLAC 24', format='flac', bit_depth=24, min_sample_rate=44100),
]


class _NamedCand(_Cand):
    def __init__(self, name, aq, confidence, username, **kw):
        super().__init__(name, aq, confidence, **kw)
        self.username = username


def test_quality_first_youtube_774_beats_soulseek_flac():
    yt = _NamedCand('yt', AudioQuality('opus', bitrate=256), 0.80, 'youtube')
    peer = _NamedCand(
        'flac', AudioQuality('flac', sample_rate=44100, bit_depth=16), 0.99, 'alice',
        quality_score=1.0,
    )

    ordered = order_candidates([peer, yt], quality_first=True, targets=AUDIOPHILE)

    assert [c.name for c in ordered] == ['yt', 'flac']


def test_mixed_pool_ranks_by_profile_even_in_priority_mode():
    """Best-quality search concatenates YouTube + Soulseek. A confidence-first
    walk uses quality_score (FLAC 1.0, Opus 0.3) and would always pick the
    peer — even when itag 774 matches the top target."""
    yt = _NamedCand('yt', AudioQuality('opus', bitrate=256), 0.80, 'youtube')
    peer = _NamedCand(
        'flac', AudioQuality('flac', sample_rate=44100, bit_depth=16), 0.99, 'alice',
        quality_score=1.0,
    )

    ordered = order_candidates([peer, yt], quality_first=False, targets=AUDIOPHILE)

    assert [c.name for c in ordered] == ['yt', 'flac']


def test_same_target_prefers_earlier_hybrid_source():
    yt = _NamedCand('yt', AudioQuality('mp3', bitrate=320), 0.70, 'youtube')
    peer = _NamedCand('slsk', AudioQuality('mp3', bitrate=320), 0.95, 'alice')

    ordered = order_candidates(
        [peer, yt], quality_first=True, targets=AUDIOPHILE,
        source_order=['youtube', 'soulseek'],
    )

    assert [c.name for c in ordered] == ['yt', 'slsk']
