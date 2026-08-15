"""quality_tier_for_source — derive a source's requested download tier from
the GLOBAL quality profile instead of a per-source setting.

Rule: pick the LOWEST source tier that satisfies the user's top (most
preferred) target — respecting the user's quality ceiling and saving
bandwidth — or the source's max tier when none can satisfy it (best effort).
"""

import pytest

import core.quality.source_map as sm
from core.quality.model import QualityTarget


def _patch_targets(monkeypatch, targets, fallback=True):
    monkeypatch.setattr(sm, 'load_profile_targets', lambda: (targets, fallback))


T_FLAC24_96 = [QualityTarget(label='', format='flac', bit_depth=24, min_sample_rate=96000)]
T_FLAC24_192 = [QualityTarget(label='', format='flac', bit_depth=24, min_sample_rate=192000)]
T_FLAC16 = [QualityTarget(label='', format='flac', bit_depth=16)]
T_MP3_320 = [QualityTarget(label='', format='mp3', min_bitrate=320)]


def test_tidal_hires_when_top_wants_24_96(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC24_96)
    assert sm.quality_tier_for_source('tidal') == 'hires'


def test_tidal_lossless_respects_16bit_ceiling(monkeypatch):
    # User caps at 16-bit → request lossless, NOT hires (saves bandwidth).
    _patch_targets(monkeypatch, T_FLAC16)
    assert sm.quality_tier_for_source('tidal') == 'lossless'


def test_tidal_best_effort_max_when_unsatisfiable(monkeypatch):
    # Source maxes at 24/96 but user wants 24/192 → best effort = max tier.
    _patch_targets(monkeypatch, T_FLAC24_192)
    assert sm.quality_tier_for_source('tidal') == 'hires'


def test_no_targets_requests_max(monkeypatch):
    _patch_targets(monkeypatch, [])
    assert sm.quality_tier_for_source('tidal') == 'hires'
    assert sm.quality_tier_for_source('deezer') == 'flac'
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_deezer_flac_and_mp3(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC16)
    assert sm.quality_tier_for_source('deezer') == 'flac'
    _patch_targets(monkeypatch, T_MP3_320)
    assert sm.quality_tier_for_source('deezer') == 'mp3_320'


def test_qobuz_hires_max(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC24_192)
    assert sm.quality_tier_for_source('qobuz') == 'hires_max'


T_OPUS = [QualityTarget(label='', format='opus')]
T_OPUS_192 = [QualityTarget(label='', format='opus', min_bitrate=192)]
T_AAC_128 = [QualityTarget(label='', format='aac', min_bitrate=128)]
T_AAC_192 = [QualityTarget(label='', format='aac', min_bitrate=192)]


def test_youtube_opus_without_floor_requests_160(monkeypatch):
    _patch_targets(monkeypatch, T_OPUS)
    assert sm.quality_tier_for_source('youtube') == 'opus_160'


def test_youtube_opus_192_requests_256(monkeypatch):
    _patch_targets(monkeypatch, T_OPUS_192)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_youtube_aac_128_does_not_fetch_256(monkeypatch):
    _patch_targets(monkeypatch, T_AAC_128)
    assert sm.quality_tier_for_source('youtube') == 'aac_128'


def test_youtube_aac_192_requests_256(monkeypatch):
    _patch_targets(monkeypatch, T_AAC_192)
    assert sm.quality_tier_for_source('youtube') == 'aac_256'


def test_youtube_flac_or_mp3_is_best_effort_max(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC16)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'
    _patch_targets(monkeypatch, T_MP3_320)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_unknown_source_returns_default(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC16)
    assert sm.quality_tier_for_source('nope', default='x') == 'x'


def test_youtube_aac_without_floor_requests_128(monkeypatch):
    _patch_targets(monkeypatch, [QualityTarget(label='', format='aac')])
    assert sm.quality_tier_for_source('youtube') == 'aac_128'


def test_youtube_opus_160_floor_stays_160_but_161_jumps(monkeypatch):
    _patch_targets(monkeypatch, [QualityTarget(label='', format='opus', min_bitrate=160)])
    assert sm.quality_tier_for_source('youtube') == 'opus_160'
    _patch_targets(monkeypatch, [QualityTarget(label='', format='opus', min_bitrate=161)])
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_youtube_ignores_second_target_when_top_is_unsatisfiable(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC16 + T_OPUS)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'
