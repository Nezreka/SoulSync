"""Tests for core.quality.source_map — each download source's tier/string
mapped into a unified AudioQuality.

These mappers express each source's *claimed* capability tier. The values
are later verified post-download by the quality guard, so over-claiming is
the failure mode to avoid: an unknown tier must NOT pretend to be lossless.
"""

import pytest

from core.quality.source_map import (
    quality_from_tidal_tier,
    quality_from_qobuz,
    quality_from_deezer,
    quality_from_amazon,
    format_from_extension,
    AUDIO_EXTENSIONS,
)


# ── Shared extension → format (used by every extension-based source) ────────

@pytest.mark.parametrize("ext,fmt", [
    ("flac", "flac"), (".flac", "flac"),
    ("mp3", "mp3"),
    ("m4a", "aac"), ("aac", "aac"), ("mp4", "aac"),
    ("ogg", "ogg"), ("oga", "ogg"),
    ("opus", "opus"),
    ("wav", "wav"), ("wave", "wav"),
    ("aiff", "wav"), ("aif", "wav"),   # PCM → wav tier
    ("wma", "wma"),
    ("alac", "alac"),
    ("dsf", "dsf"), (".dsf", "dsf"), ("dff", "dsf"),   # DSD → dsf tier (#939)
    ("xyz", "unknown"), ("", "unknown"), (None, "unknown"),
])
def test_format_from_extension(ext, fmt):
    assert format_from_extension(ext) == fmt


def test_audio_extensions_cover_all_known_formats():
    for e in (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".wma"):
        assert e in AUDIO_EXTENSIONS


# ── Tidal / HiFi (Tidal-backed) ────────────────────────────────────────────

@pytest.mark.parametrize("tier", ["HI_RES_LOSSLESS", "HI_RES", "hi_res", "hires", "HIRES"])
def test_tidal_hires_is_flac_24_96(tier):
    aq = quality_from_tidal_tier(tier)
    assert aq.format == "flac"
    assert aq.bit_depth == 24
    assert aq.sample_rate == 96000


@pytest.mark.parametrize("tier", ["LOSSLESS", "lossless"])
def test_tidal_lossless_is_flac_16_44(tier):
    aq = quality_from_tidal_tier(tier)
    assert aq.format == "flac"
    assert aq.bit_depth == 16
    assert aq.sample_rate == 44100


def test_tidal_high_is_lossy_aac_320():
    aq = quality_from_tidal_tier("HIGH")
    assert aq.format == "aac"
    assert aq.bitrate == 320
    assert aq.bit_depth is None  # lossy: no bit depth


def test_tidal_low_is_lossy_aac_96():
    aq = quality_from_tidal_tier("LOW")
    assert aq.format == "aac"
    assert aq.bitrate == 96


def test_tidal_unknown_tier_does_not_overclaim():
    # An unrecognised tier must not masquerade as lossless.
    aq = quality_from_tidal_tier("SOMETHING_NEW")
    assert aq.format == "unknown"
    assert aq.bit_depth is None
    assert aq.sample_rate is None


# ── Qobuz (real API values) ────────────────────────────────────────────────

def test_qobuz_hires_khz_to_hz():
    aq = quality_from_qobuz(96.0, 24)
    assert aq.format == "flac"
    assert aq.sample_rate == 96000
    assert aq.bit_depth == 24


def test_qobuz_cd_quality_fractional_khz():
    aq = quality_from_qobuz(44.1, 16)
    assert aq.format == "flac"
    assert aq.sample_rate == 44100
    assert aq.bit_depth == 16


def test_qobuz_192k():
    aq = quality_from_qobuz(192.0, 24)
    assert aq.sample_rate == 192000
    assert aq.bit_depth == 24


# ── Deezer (config code) ───────────────────────────────────────────────────

def test_deezer_flac_is_16_44():
    aq = quality_from_deezer("flac")
    assert aq.format == "flac"
    assert aq.bit_depth == 16
    assert aq.sample_rate == 44100


def test_deezer_mp3_320():
    aq = quality_from_deezer("mp3_320")
    assert aq.format == "mp3"
    assert aq.bitrate == 320
    assert aq.bit_depth is None


def test_deezer_mp3_128():
    aq = quality_from_deezer("mp3_128")
    assert aq.format == "mp3"
    assert aq.bitrate == 128


# ── Amazon (real sampleRate preferred, tier fallback) ──────────────────────

def test_amazon_prefers_real_sample_rate():
    aq = quality_from_amazon("HD", sample_rate=88200, bit_depth=24)
    assert aq.format == "flac"
    assert aq.sample_rate == 88200
    assert aq.bit_depth == 24


def test_amazon_hd_tier_fallback():
    aq = quality_from_amazon("HD")
    assert aq.format == "flac"
    assert aq.sample_rate == 44100
    assert aq.bit_depth == 16


def test_amazon_uhd_tier_fallback():
    aq = quality_from_amazon("UHD")
    assert aq.format == "flac"
    assert aq.bit_depth == 24
    assert aq.sample_rate == 96000
