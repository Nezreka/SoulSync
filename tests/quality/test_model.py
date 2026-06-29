"""AudioQuality.matches_target + v2->v3 migration.

Locks the bitrate-as-threshold behaviour: lossy formats match on a MINIMUM
bitrate (>=, a range), and lossless matches on bit depth + sample rate — NOT
on exact bitrate, so a FLAC's wildly-varying bitrate (stereo vs mono, FLAC
compression) never falsely rejects it.
"""

import pytest

from core.quality.model import (
    AudioQuality,
    QualityTarget,
    v2_qualities_to_ranked_targets,
)


# ── lossy: bitrate is a minimum threshold (a range), never exact ───────────

def test_mp3_meets_minimum_bitrate():
    t = QualityTarget(format='mp3', min_bitrate=320)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is True
    assert AudioQuality('mp3', bitrate=400).matches_target(t) is True  # above floor ok


def test_mp3_below_minimum_bitrate_rejected():
    t = QualityTarget(format='mp3', min_bitrate=320)
    assert AudioQuality('mp3', bitrate=300).matches_target(t) is False


def test_mp3_matches_lower_threshold():
    t = QualityTarget(format='mp3', min_bitrate=192)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is True


# ── lossless: matched on bit depth + sample rate, NOT exact bitrate ────────

def test_flac_matches_on_depth_and_rate_regardless_of_bitrate():
    t = QualityTarget(format='flac', bit_depth=24, min_sample_rate=96000)
    # An unusual/low bitrate (e.g. a mono or highly-compressed FLAC) must
    # still match when bit depth + sample rate satisfy the target.
    weird = AudioQuality('flac', bitrate=300, sample_rate=96000, bit_depth=24)
    assert weird.matches_target(t) is True


def test_flac_below_target_sample_rate_rejected():
    t = QualityTarget(format='flac', bit_depth=24, min_sample_rate=96000)
    assert AudioQuality('flac', sample_rate=44100, bit_depth=24).matches_target(t) is False


def test_flac_below_target_bit_depth_rejected():
    t = QualityTarget(format='flac', bit_depth=24)
    assert AudioQuality('flac', sample_rate=96000, bit_depth=16).matches_target(t) is False


def test_format_mismatch_rejected():
    t = QualityTarget(format='flac', bit_depth=16)
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is False


# ── metadata-less FLAC must not over-claim a hi-res target (#896 review #4) ─

def test_metadata_less_flac_does_not_overclaim_hires_target():
    """A FLAC with no sample_rate/bit_depth metadata (common on slskd) must NOT
    satisfy a strict hi-res target — otherwise it outranks and discards a real
    16/44 FLAC. Unknown spec fails the high tier, falling to a plain flac bucket."""
    hires = QualityTarget(format='flac', bit_depth=24, min_sample_rate=192000)
    assert AudioQuality('flac').matches_target(hires) is False


def test_metadata_less_flac_does_not_overclaim_bit_depth_only_hires():
    """Same guard when the hi-res target constrains only bit depth."""
    hires = QualityTarget(format='flac', bit_depth=24)
    assert AudioQuality('flac').matches_target(hires) is False


def test_metadata_less_flac_matches_plain_flac_target():
    """A bare FLAC still matches a plain 'flac (any)' target (the baseline)."""
    assert AudioQuality('flac').matches_target(QualityTarget(format='flac')) is True


def test_metadata_less_flac_matches_16bit_baseline_target():
    """A bare FLAC satisfies the 16-bit baseline (any FLAC is >= CD quality)."""
    assert AudioQuality('flac').matches_target(QualityTarget(format='flac', bit_depth=16)) is True


# ── ranked targets work for EVERY format, not just flac/mp3 (universal) ────

def test_opus_target_matches_opus_candidate():
    t = QualityTarget(format='opus', min_bitrate=128)
    assert AudioQuality('opus', bitrate=160).matches_target(t) is True
    assert AudioQuality('opus', bitrate=96).matches_target(t) is False
    assert AudioQuality('mp3', bitrate=320).matches_target(t) is False  # wrong format


def test_wav_target_matches_on_bit_depth_and_sample_rate():
    t = QualityTarget(format='wav', bit_depth=24, min_sample_rate=96000)
    assert AudioQuality('wav', sample_rate=96000, bit_depth=24).matches_target(t) is True
    assert AudioQuality('wav', sample_rate=44100, bit_depth=16).matches_target(t) is False


def test_wma_and_alac_targets_match_their_formats():
    assert AudioQuality('wma', bitrate=192).matches_target(QualityTarget(format='wma', min_bitrate=128)) is True
    assert AudioQuality('alac', sample_rate=96000, bit_depth=24).matches_target(
        QualityTarget(format='alac', bit_depth=24)) is True


def test_only_listed_format_passes_others_rank_last():
    """An Opus-only target list: only opus matches; everything else ranks last
    (index == len(targets)), so with fallback off the caller drops them."""
    from core.quality.model import rank_candidate
    targets = [QualityTarget(format='opus')]
    assert rank_candidate(AudioQuality('opus', bitrate=160), targets)[0] == 0
    assert rank_candidate(AudioQuality('flac', sample_rate=96000, bit_depth=24), targets)[0] == 1
    assert rank_candidate(AudioQuality('mp3', bitrate=320), targets)[0] == 1


# ── v2 -> v3 migration preserves the user's priority order ─────────────────

def test_v2_to_v3_preserves_order_and_maps_fields():
    qualities = {
        'flac':    {'enabled': True,  'priority': 1, 'bit_depth': '24'},
        'mp3_320': {'enabled': True,  'priority': 2},
        'mp3_192': {'enabled': False, 'priority': 3},  # disabled → dropped
    }
    targets = v2_qualities_to_ranked_targets(qualities)
    formats = [t['format'] for t in targets]
    assert formats == ['flac', 'mp3']          # disabled mp3_192 omitted
    assert targets[0]['bit_depth'] == 24
    assert targets[1]['min_bitrate'] == 320


# ── DSD (#939): DSF must rank as lossless, never "Low Quality" below MP3 ──

def test_dsf_ranks_in_lossless_range():
    dsf = AudioQuality('dsf', bitrate=11290).tier_score()
    flac_cd = AudioQuality('flac', sample_rate=44100, bit_depth=16).tier_score()
    mp3_320 = AudioQuality('mp3', bitrate=320).tier_score()
    # DSD64 is hi-res lossless — at/above CD FLAC and well above any lossy format.
    assert dsf >= flac_cd
    assert dsf > mp3_320


def test_dsf_without_measured_bitrate_still_lossless():
    # .dff has no mutagen reader, so it classifies as 'dsf' with no measured detail —
    # it must still land in the lossless tier, not the 'unknown' floor.
    dsf = AudioQuality('dsf').tier_score()
    assert dsf > AudioQuality('mp3', bitrate=320).tier_score()
    assert dsf > AudioQuality('unknown').tier_score()
