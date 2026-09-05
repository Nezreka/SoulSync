"""quality_tier_for_source — derive a source's requested download tier from
the applicable item/default quality profile instead of a per-source setting.

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


def test_amazon_opus_is_not_misreported_as_aac(monkeypatch):
    _patch_targets(monkeypatch, [QualityTarget(label='', format='opus')])
    assert sm.quality_tier_for_source('amazon') == 'opus'

    # An AAC profile matches nothing in this ladder. It used to be answered
    # with flac, which the import guard then rejected — see
    # test_a_lossy_only_profile_never_requests_lossless_from_a_source.
    _patch_targets(monkeypatch, [QualityTarget(label='', format='aac')])
    assert sm.quality_tier_for_source('amazon') == 'opus'


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


def test_item_profile_context_overrides_default_without_leaking(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC16)
    seen = []

    def _load(profile_id):
        seen.append(profile_id)
        return {
            'ranked_targets': [
                {'format': 'mp3', 'min_bitrate': 320},
            ],
            'fallback_enabled': False,
        }

    monkeypatch.setattr(sm, 'load_profile_by_id', _load)

    with sm.quality_profile_context(44):
        assert sm.quality_tier_for_source('deezer') == 'mp3_320'

    assert seen == [44]
    assert sm.quality_tier_for_source('deezer') == 'flac'


def test_item_profile_context_reaches_blocking_provider_pool(monkeypatch):
    import asyncio

    from core.async_utils import run_blocking

    _patch_targets(monkeypatch, T_FLAC16)
    monkeypatch.setattr(sm, 'load_profile_by_id', lambda profile_id: {
        'ranked_targets': [{'format': 'mp3', 'min_bitrate': 320}],
        'fallback_enabled': False,
    })

    async def _resolve_in_pool():
        with sm.quality_profile_context(9):
            return await run_blocking(sm.quality_tier_for_source, 'deezer')

    assert asyncio.run(_resolve_in_pool()) == 'mp3_320'


def test_deezer_fallback_never_wraps_above_item_quality_ceiling(monkeypatch):
    import core.deezer_download_client as deezer_module

    client = deezer_module.DeezerDownloadClient.__new__(
        deezer_module.DeezerDownloadClient
    )
    client.shutdown_check = None
    client._engine = None
    client._quality = 'flac'
    client._config = type('Config', (), {
        'get': staticmethod(lambda key, default=None: True),
    })()
    client._get_track_data = lambda _track_id: {'TRACK_TOKEN': 'token'}
    attempted = []
    client._get_media_url = lambda _token, quality: attempted.append(quality)
    client._set_error = lambda *_args: None
    monkeypatch.setattr(
        deezer_module,
        'quality_tier_for_source',
        lambda *_args, **_kwargs: 'mp3_320',
    )

    assert client._download_sync('id', 'track', 'Artist - Track') is None
    assert attempted == ['mp3_320', 'mp3_128']


def test_amazon_fallback_never_wraps_above_item_quality_ceiling(monkeypatch):
    import core.amazon_download_client as amazon_module

    client = amazon_module.AmazonDownloadClient.__new__(
        amazon_module.AmazonDownloadClient
    )
    client._quality = 'flac'
    client._allow_fallback = True
    client._engine = None
    attempted = []

    class _MediaClient:
        @staticmethod
        def media_from_asin(_asin, codec):
            attempted.append(codec)
            return []

    client._client = _MediaClient()
    monkeypatch.setattr(
        amazon_module,
        'quality_tier_for_source',
        lambda *_args, **_kwargs: 'opus',
    )

    assert client._download_sync('id', 'asin', 'Artist - Track') is None
    assert attempted == ['opus', 'eac3']


def test_a_lossy_only_profile_never_requests_lossless_from_a_source(monkeypatch):
    """Falling back to the top tier spends the most bandwidth on a reject.

    Amazon's lossy tier is Opus, so an AAC-only profile matches nothing in that
    ladder. The fallback handed it `ladder[0]`, which is FLAC, and the import
    guard then threw the download away. When the profile asked for a lossy
    format, the honest miss is the lowest tier, not the highest.
    """
    _patch_targets(monkeypatch, [QualityTarget(label='', format='aac')], fallback=False)

    assert sm.quality_tier_for_source('amazon') == 'opus'


def test_a_lossless_profile_that_matches_nothing_still_gets_the_top_tier(monkeypatch):
    """Best effort still means the best the source has, in that direction."""
    _patch_targets(monkeypatch, [QualityTarget(label='', format='ape')], fallback=False)

    assert sm.quality_tier_for_source('amazon') == 'flac'
