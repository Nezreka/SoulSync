"""YouTube download tier — profile → requested rung → picked itag.

Pins the same lowest-satisfying-rung rule as Tidal/Deezer, plus the
client wrappers that must not break download when no quality profile
exists (empty targets, missing DB, malformed rows).
"""

from __future__ import annotations

import pytest

import core.quality.source_map as sm
from core.quality.model import QualityTarget
from core.quality.selection import targets_from_profile
from core.youtube_client import (
    _YOUTUBE_QUALITY_CHAIN,
    _YOUTUBE_QUALITY_MAP,
    _YOUTUBE_TIER_SELECTORS,
    _youtube_transcode_settings as _real_youtube_transcode_settings,
    pick_youtube_audio_format,
    youtube_audio_format_selector,
    youtube_claimed_quality,
    youtube_requested_tier,
    youtube_transcode_output_quality,
)


@pytest.fixture(autouse=True)
def _reencode_off(monkeypatch):
    """Profile-ladder tests assume remux (re-encode off)."""
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (False, 'mp3', '320'),
    )


def _patch_targets(monkeypatch, targets, fallback=True):
    monkeypatch.setattr(sm, 'load_profile_targets', lambda: (targets, fallback))


T_FLAC = [QualityTarget(label='', format='flac', bit_depth=16)]
T_MP3 = [QualityTarget(label='', format='mp3', min_bitrate=320)]
T_OPUS = [QualityTarget(label='', format='opus')]
T_OPUS_THEN_FLAC = [
    QualityTarget(label='', format='opus'),
    QualityTarget(label='', format='flac', bit_depth=16),
]
T_FLAC_THEN_OPUS = [
    QualityTarget(label='', format='flac', bit_depth=16),
    QualityTarget(label='', format='opus'),
]
T_AAC = [QualityTarget(label='', format='aac')]
T_AAC_THEN_OPUS = [
    QualityTarget(label='', format='aac', min_bitrate=128),
    QualityTarget(label='', format='opus', min_bitrate=192),
]


# ── ladder keys must not drift between source_map and the YouTube client ───


def test_youtube_client_chain_matches_source_map_ladder():
    ladder_keys = [key for key, _ in sm._SOURCE_TIER_LADDERS['youtube']]
    assert ladder_keys == _YOUTUBE_QUALITY_CHAIN
    assert list(_YOUTUBE_QUALITY_MAP) == _YOUTUBE_QUALITY_CHAIN
    assert set(_YOUTUBE_TIER_SELECTORS) == set(ladder_keys)


def test_youtube_quality_map_bitrates_match_ladder_claims():
    for key, claimed in sm._SOURCE_TIER_LADDERS['youtube']:
        assert _YOUTUBE_QUALITY_MAP[key].format == claimed.format
        assert _YOUTUBE_QUALITY_MAP[key].bitrate == claimed.bitrate


# ── quality_tier_for_source: missing / empty / only-top-target ─────────────


def test_no_quality_profile_targets_requests_max_opus_256(monkeypatch):
    _patch_targets(monkeypatch, [])
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_empty_profile_dict_is_treated_as_no_targets():
    targets, fallback = targets_from_profile({})
    assert targets == []
    assert fallback is True  # missing toggle defaults on


def test_missing_ranked_targets_and_null_list():
    assert targets_from_profile({'fallback_enabled': False})[0] == []
    assert targets_from_profile({'ranked_targets': None})[0] == []
    assert targets_from_profile({'ranked_targets': []})[0] == []


def test_blank_target_row_has_no_format_constraint():
    """A saved empty row is not 'no profile' — it matches every YouTube rung,
    so the lowest rung (aac_128) wins. Distinct from ranked_targets=[]."""
    targets, _ = targets_from_profile({'ranked_targets': [{}]})
    assert len(targets) == 1
    assert targets[0].format is None


def test_blank_target_row_requests_lowest_youtube_rung(monkeypatch):
    targets, _ = targets_from_profile({'ranked_targets': [{}]})
    _patch_targets(monkeypatch, targets)
    assert sm.quality_tier_for_source('youtube') == 'aac_128'


def test_only_top_target_drives_youtube_request(monkeypatch):
    """Second-row Opus must not pull the request down when the top target is FLAC."""
    _patch_targets(monkeypatch, T_FLAC_THEN_OPUS)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'
    _patch_targets(monkeypatch, T_OPUS_THEN_FLAC)
    assert sm.quality_tier_for_source('youtube') == 'opus_160'


def test_aac_with_no_min_bitrate_requests_128_not_256(monkeypatch):
    _patch_targets(monkeypatch, T_AAC)
    assert sm.quality_tier_for_source('youtube') == 'aac_128'


def test_aac_then_opus_192_still_requests_aac_128(monkeypatch):
    _patch_targets(monkeypatch, T_AAC_THEN_OPUS)
    assert sm.quality_tier_for_source('youtube') == 'aac_128'


@pytest.mark.parametrize("min_bitrate,want", [
    (None, 'opus_160'),
    (96, 'opus_160'),
    (160, 'opus_160'),
    (161, 'opus_256'),
    (192, 'opus_256'),
    (256, 'opus_256'),
])
def test_opus_bitrate_floor_boundary(monkeypatch, min_bitrate, want):
    _patch_targets(monkeypatch, [QualityTarget(label='', format='opus', min_bitrate=min_bitrate)])
    assert sm.quality_tier_for_source('youtube') == want


@pytest.mark.parametrize("min_bitrate,want", [
    (None, 'aac_128'),
    (96, 'aac_128'),
    (128, 'aac_128'),
    (129, 'aac_256'),
    (192, 'aac_256'),
    (256, 'aac_256'),
])
def test_aac_bitrate_floor_boundary(monkeypatch, min_bitrate, want):
    _patch_targets(monkeypatch, [QualityTarget(label='', format='aac', min_bitrate=min_bitrate)])
    assert sm.quality_tier_for_source('youtube') == want


def test_flac_and_mp3_profiles_are_best_effort_max(monkeypatch):
    _patch_targets(monkeypatch, T_FLAC)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'
    _patch_targets(monkeypatch, T_MP3)
    assert sm.quality_tier_for_source('youtube') == 'opus_256'


def test_mp3_only_profile_still_fetches_opus_not_mp3(monkeypatch):
    """Without re-encode, an MP3 profile still requests best-effort Opus.
    YouTube does not serve MP3."""
    _patch_targets(monkeypatch, T_MP3)
    assert youtube_requested_tier() == 'opus_256'
    selector = youtube_audio_format_selector(youtube_requested_tier())
    assert 'opus' in selector
    assert 'mp3' not in selector.lower()


def test_reencode_on_always_requests_best_original_stream(monkeypatch):
    """Re-encode converts after download — always fetch the best source,
    even if the profile is AAC 128 or MP3 320."""
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    _patch_targets(monkeypatch, T_AAC)
    assert youtube_requested_tier() == 'opus_256'
    _patch_targets(monkeypatch, T_MP3)
    assert youtube_requested_tier() == 'opus_256'
    _patch_targets(monkeypatch, T_OPUS)
    assert youtube_requested_tier() == 'opus_256'
    _patch_targets(monkeypatch, T_FLAC)
    assert youtube_requested_tier() == 'opus_256'


def test_transcode_output_quality_is_none_when_reencode_off():
    assert youtube_transcode_output_quality() is None


@pytest.mark.parametrize("bitrate,want_br", [
    ('0', 320),
    ('', 320),
    (None, 320),
    ('nope', 320),
    ('-5', 320),
    (0, 320),
    (-1, 320),
    ('320.9', 320),  # int() rejects the float string
])
def test_transcode_output_invalid_bitrate_falls_back_to_320(monkeypatch, bitrate, want_br):
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, 'mp3', bitrate),
    )
    aq = youtube_transcode_output_quality()
    assert aq.format == 'mp3'
    assert aq.bitrate == want_br


def test_transcode_output_codec_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, 'M4A', '256'),
    )
    aq = youtube_transcode_output_quality()
    assert aq.format == 'aac'
    assert aq.bitrate == 256


def _patch_get_defaults(monkeypatch, values=None):
    stored = values or {}

    def _get(key, default=None):
        if key in stored:
            return stored[key]
        return default

    monkeypatch.setattr('core.settings.config_manager.get', _get)


def test_real_transcode_settings_missing_keys_are_mp3_320(monkeypatch):
    _patch_get_defaults(monkeypatch)
    assert _real_youtube_transcode_settings() == (True, 'mp3', '320')


def test_real_transcode_settings_honour_explicit_off(monkeypatch):
    _patch_get_defaults(monkeypatch, {'youtube.transcode': False})
    transcode, codec, bitrate = _real_youtube_transcode_settings()
    assert transcode is False
    assert codec == 'mp3'
    assert bitrate == '320'


def test_real_transcode_settings_honour_explicit_on_aac(monkeypatch):
    _patch_get_defaults(monkeypatch, {
        'youtube.transcode': True,
        'youtube.transcode_codec': 'aac',
        'youtube.transcode_bitrate': '192',
    })
    assert _real_youtube_transcode_settings() == (True, 'aac', '192')


def test_real_transcode_settings_empty_codec_bitrate_use_mp3_320(monkeypatch):
    _patch_get_defaults(monkeypatch, {
        'youtube.transcode': True,
        'youtube.transcode_codec': '',
        'youtube.transcode_bitrate': '',
    })
    assert _real_youtube_transcode_settings() == (True, 'mp3', '320')


def test_real_transcode_settings_never_raise(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError('settings locked')

    monkeypatch.setattr('core.settings.config_manager.get', _boom)
    assert _real_youtube_transcode_settings() == (True, 'mp3', '320')


def test_product_default_claimed_quality_is_mp3_320(monkeypatch):
    """Missing youtube.transcode* keys: rank as MP3 320, fetch best original."""
    _patch_get_defaults(monkeypatch)
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        _real_youtube_transcode_settings,
    )
    aq = youtube_claimed_quality({'acodec': 'opus', 'abr': 160, 'ext': 'webm'})
    assert aq.format == 'mp3'
    assert aq.bitrate == 320
    assert youtube_requested_tier() == 'opus_256'


@pytest.mark.parametrize("codec,bitrate,want_fmt,want_br", [
    ('mp3', '320', 'mp3', 320),
    ('mp3', '128', 'mp3', 128),
    ('aac', '192', 'aac', 192),
    ('m4a', '256', 'aac', 256),
    ('opus', '160', 'opus', 160),
    ('ogg', '192', 'ogg', 192),
    ('flac', '320', 'mp3', 320),
    ('', '320', 'mp3', 320),
])
def test_transcode_output_quality_maps_codec_bitrate(monkeypatch, codec, bitrate, want_fmt, want_br):
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, codec, bitrate),
    )
    aq = youtube_transcode_output_quality()
    assert aq is not None
    assert aq.format == want_fmt
    assert aq.bitrate == want_br


def test_claimed_quality_uses_transcode_output_not_original_stream(monkeypatch):
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    aq = youtube_claimed_quality({'acodec': 'opus', 'abr': 160, 'ext': 'webm'})
    assert aq.format == 'mp3'
    assert aq.bitrate == 320


def test_claimed_quality_uses_original_stream_when_reencode_off(monkeypatch):
    aq = youtube_claimed_quality({'acodec': 'opus', 'abr': 160, 'ext': 'webm'})
    assert aq.format == 'opus'
    assert aq.bitrate == 160


def test_claimed_quality_none_is_typical_opus_when_reencode_off():
    aq = youtube_claimed_quality(None)
    assert aq.format == 'opus'
    assert aq.bitrate == 160


def test_claimed_quality_none_is_mp3_320_when_reencode_on(monkeypatch):
    monkeypatch.setattr(
        'core.youtube_client._youtube_transcode_settings',
        lambda: (True, 'mp3', '320'),
    )
    aq = youtube_claimed_quality(None)
    assert aq.format == 'mp3'
    assert aq.bitrate == 320


# ── youtube_requested_tier must never raise ────────────────────────────────


def test_requested_tier_empty_profile_is_opus_256(monkeypatch):
    _patch_targets(monkeypatch, [])
    assert youtube_requested_tier() == 'opus_256'


def test_requested_tier_follows_profile(monkeypatch):
    _patch_targets(monkeypatch, T_OPUS)
    assert youtube_requested_tier() == 'opus_160'
    _patch_targets(monkeypatch, T_AAC)
    assert youtube_requested_tier() == 'aac_128'


def test_requested_tier_when_load_profile_targets_raises(monkeypatch):
    def _boom():
        raise RuntimeError('database is locked')

    monkeypatch.setattr(sm, 'load_profile_targets', _boom)
    assert youtube_requested_tier() == 'opus_256'


def test_requested_tier_when_get_quality_profile_raises(monkeypatch):
    class _BoomDb:
        def get_quality_profile(self):
            raise RuntimeError('no such table: quality_profiles')

    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: _BoomDb())
    assert youtube_requested_tier() == 'opus_256'


def test_requested_tier_when_tier_helper_returns_none(monkeypatch):
    monkeypatch.setattr(sm, 'quality_tier_for_source', lambda *a, **k: None)
    monkeypatch.setattr('core.youtube_client.quality_tier_for_source', lambda *a, **k: None)
    assert youtube_requested_tier() == 'opus_256'


def test_requested_tier_when_tier_helper_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise ValueError('malformed ranked_targets')

    monkeypatch.setattr('core.youtube_client.quality_tier_for_source', _boom)
    assert youtube_requested_tier() == 'opus_256'


def test_requested_tier_malformed_ranked_targets_do_not_break_download(monkeypatch):
    class _Db:
        def get_quality_profile(self):
            return {'ranked_targets': ['not-a-dict'], 'fallback_enabled': True}

    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: _Db())
    assert youtube_requested_tier() == 'opus_256'


# ── format selector ────────────────────────────────────────────────────────


def test_selector_none_uses_requested_tier(monkeypatch):
    monkeypatch.setattr('core.youtube_client.youtube_requested_tier', lambda: 'aac_128')
    assert youtube_audio_format_selector(None) == _YOUTUBE_TIER_SELECTORS['aac_128']


def test_selector_unknown_and_blank_are_bestaudio():
    assert youtube_audio_format_selector('nope') == 'bestaudio/best'
    assert youtube_audio_format_selector('') == 'bestaudio/best'
    assert youtube_audio_format_selector('  ') == 'bestaudio/best'


def test_selector_is_case_insensitive():
    assert youtube_audio_format_selector('OPUS_160') == _YOUTUBE_TIER_SELECTORS['opus_160']
    assert youtube_audio_format_selector(' Aac_128 ') == _YOUTUBE_TIER_SELECTORS['aac_128']


@pytest.mark.parametrize("tier", ['opus_256', 'aac_256', 'opus_160', 'aac_128'])
def test_each_rung_has_a_selector(tier):
    sel = youtube_audio_format_selector(tier)
    assert sel.startswith('bestaudio')
    assert sel.endswith('bestaudio/best') or '/best' in sel


def test_high_rungs_prefer_abr_192_plus():
    assert 'abr>=192' in youtube_audio_format_selector('opus_256')
    assert 'abr>=192' in youtube_audio_format_selector('aac_256')
    assert 'abr>=192' not in youtube_audio_format_selector('opus_160')
    assert 'abr>=192' not in youtube_audio_format_selector('aac_128')


# ── pick_youtube_audio_format ──────────────────────────────────────────────

_OPUS_50 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 50, 'ext': 'webm', 'format_id': '249'}
_OPUS_70 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 70, 'ext': 'webm', 'format_id': '250'}
_OPUS_160 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 160, 'ext': 'webm', 'format_id': '251'}
_OPUS_191 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 191, 'ext': 'webm', 'format_id': 'x191'}
_OPUS_192 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 192, 'ext': 'webm', 'format_id': 'x192'}
_OPUS_256 = {'vcodec': 'none', 'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': '774'}
_AAC_48 = {'vcodec': 'none', 'acodec': 'mp4a.40.5', 'abr': 48, 'ext': 'm4a', 'format_id': '139'}
_AAC_95 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 95, 'ext': 'm4a', 'format_id': 'x95'}
_AAC_96 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 96, 'ext': 'm4a', 'format_id': 'x96'}
_AAC_128 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 128, 'ext': 'm4a', 'format_id': '140'}
_AAC_191 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 191, 'ext': 'm4a', 'format_id': 'x191a'}
_AAC_192 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 192, 'ext': 'm4a', 'format_id': 'x192a'}
_AAC_256 = {'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 256, 'ext': 'm4a', 'format_id': '141'}
_MUXED = {'vcodec': 'avc1', 'acodec': 'mp4a.40.2', 'abr': 96, 'ext': 'mp4', 'format_id': '18'}
_VIDEO = {'vcodec': 'avc1', 'acodec': 'none', 'ext': 'mp4', 'format_id': '137'}
_STORY = {'vcodec': 'none', 'acodec': 'none', 'ext': 'mhtml', 'format_id': 'sb0'}


@pytest.mark.parametrize("formats", [None, [], [_MUXED, _VIDEO, _STORY]])
def test_pick_returns_none_without_audio_only(formats):
    assert pick_youtube_audio_format(formats, tier='opus_256') is None


def test_pick_unknown_tier_starts_at_top_rung():
    formats = [_OPUS_160, _OPUS_256, _AAC_128]
    assert pick_youtube_audio_format(formats, tier='nope')['format_id'] == '774'
    assert pick_youtube_audio_format(formats, tier='')['format_id'] == '774'


def test_pick_none_tier_uses_requested_tier(monkeypatch):
    monkeypatch.setattr('core.youtube_client.youtube_requested_tier', lambda: 'aac_128')
    picked = pick_youtube_audio_format([_OPUS_256, _AAC_128], tier=None)
    assert picked['format_id'] == '140'


@pytest.mark.parametrize("abr,want_id", [
    (191, 'x191'),
    (192, 'x192'),
])
def test_pick_opus_abr_192_is_the_256_rung_boundary(abr, want_id):
    fmt = {'vcodec': 'none', 'acodec': 'opus', 'abr': abr, 'ext': 'webm', 'format_id': want_id}
    # 191 belongs on the 160 rung; 192 on the 256 rung.
    if abr < 192:
        assert pick_youtube_audio_format([fmt], tier='opus_160')['format_id'] == want_id
        # Asking for 256 walks down to 160 when 192+ is missing.
        assert pick_youtube_audio_format([fmt], tier='opus_256')['format_id'] == want_id
    else:
        assert pick_youtube_audio_format([fmt], tier='opus_256')['format_id'] == want_id
        # opus_160 must not upgrade to a 192+ stream.
        assert pick_youtube_audio_format([fmt, _OPUS_160], tier='opus_160')['format_id'] == '251'


def test_pick_aac_96_is_128_rung_and_95_is_not():
    assert pick_youtube_audio_format([_AAC_96], tier='aac_128')['format_id'] == 'x96'
    # 95 kbps is below the 128 rung; last-resort still returns it so ranking
    # can stamp an honest claim instead of pretending nothing exists.
    assert pick_youtube_audio_format([_AAC_95], tier='aac_128')['format_id'] == 'x95'


def test_pick_aac_191_stays_on_128_rung_192_is_256():
    assert pick_youtube_audio_format([_AAC_191], tier='aac_128')['format_id'] == 'x191a'
    assert pick_youtube_audio_format([_AAC_192, _AAC_128], tier='aac_128')['format_id'] == '140'
    assert pick_youtube_audio_format([_AAC_192], tier='aac_256')['format_id'] == 'x192a'


def test_pick_uses_tbr_when_abr_missing_at_192_boundary():
    fmt = {'vcodec': 'none', 'acodec': 'opus', 'tbr': 192, 'ext': 'webm', 'format_id': 'tbr192'}
    assert pick_youtube_audio_format([fmt], tier='opus_256')['format_id'] == 'tbr192'
    low = {'vcodec': 'none', 'acodec': 'opus', 'tbr': 160, 'ext': 'webm', 'format_id': 'tbr160'}
    assert pick_youtube_audio_format([fmt, low], tier='opus_160')['format_id'] == 'tbr160'


def test_pick_only_ultra_low_itags_is_last_resort_not_a_requested_rung():
    picked = pick_youtube_audio_format([_AAC_48, _OPUS_50, _OPUS_70], tier='opus_256')
    assert picked['format_id'] == '250'  # highest abr among leftovers
    # None of these satisfy a 160/128 rung, so we did not pretend they were 160.
    from core.quality.source_map import quality_from_youtube
    assert quality_from_youtube(picked).bitrate == 70


def test_pick_skips_missing_vcodec_and_muxed():
    formats = [
        {'acodec': 'opus', 'abr': 256, 'ext': 'webm', 'format_id': 'no-vcodec'},
        _MUXED,
        _OPUS_160,
    ]
    assert pick_youtube_audio_format(formats, tier='opus_160')['format_id'] == '251'


def test_pick_walks_down_premium_to_free():
    assert pick_youtube_audio_format([_OPUS_160, _AAC_128], tier='opus_256')['format_id'] == '251'
    assert pick_youtube_audio_format([_AAC_128], tier='aac_256')['format_id'] == '140'


def test_pick_does_not_cross_codec_to_satisfy_a_higher_rung():
    # Asking for AAC 256 must not grab Opus 256 when AAC 128 is present.
    picked = pick_youtube_audio_format([_OPUS_256, _AAC_128], tier='aac_256')
    assert picked['format_id'] == '140'
    # Asking for Opus 160 must not grab AAC 256.
    picked = pick_youtube_audio_format([_AAC_256, _OPUS_160], tier='opus_160')
    assert picked['format_id'] == '251'
