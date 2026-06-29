"""Per-source quality mappers — turn each download source's tier string or
API values into a unified :class:`~core.quality.model.AudioQuality`.

Every streaming source describes quality differently (Tidal/HiFi use tier
strings, Qobuz reports real kHz + bit depth, Deezer uses config codes,
Amazon mixes real values with HD/UHD tiers). Centralising the knowledge
here keeps the per-client code to a single call and keeps the tier tables
in one auditable place.

Each value is a *claim*: the download client populates its ``TrackResult``
from it so the global ranker can choose a source, and the post-download
quality guard later verifies the real file. Over-claiming is the danger —
an unknown tier maps to ``format='unknown'`` rather than pretending to be
lossless.
"""

from __future__ import annotations

from typing import Optional

from core.quality.model import AudioQuality
from core.quality.selection import load_profile_targets


# ── Extension → format string (source-agnostic) ────────────────────────────
#
# The single source of truth for mapping a file extension to the unified
# AudioQuality ``format``. Every extension-based download source (Soulseek,
# torrent/usenet file lists, …) classifies through this, so the ranked-target
# system behaves identically across sources and adding a format here lights it
# up everywhere at once. Unknown extensions → 'unknown' (never matches a
# target, so it only ever comes through via the fallback toggle).
#
# AIFF/AIF are uncompressed PCM like WAV → the same 'wav' tier. ``.m4a``
# defaults to 'aac'; an ALAC-in-m4a file can't be told apart by extension
# alone, so probe_audio_quality corrects it from the real codec post-download.
_EXTENSION_FORMAT_MAP = {
    'flac': 'flac',
    'alac': 'alac',
    'wav': 'wav', 'wave': 'wav',
    'aiff': 'wav', 'aif': 'wav', 'aifc': 'wav',
    'mp3': 'mp3',
    'm4a': 'aac', 'mp4': 'aac', 'aac': 'aac',
    'ogg': 'ogg', 'oga': 'ogg',
    'opus': 'opus',
    'wma': 'wma',
    # DSD (DSD Stream File / DSDIFF) — 1-bit hi-res lossless (e.g. DSD64 ≈ 11 Mbps).
    # Both container types map to the single 'dsf' tier (#939).
    'dsf': 'dsf', 'dff': 'dsf',
}

# Audio extensions worth probing/classifying at all — derived from the map so
# the allow-list and the classifier never drift apart.
AUDIO_EXTENSIONS = {f'.{e}' for e in _EXTENSION_FORMAT_MAP}


def format_from_extension(ext: str) -> str:
    """Map a file extension (with or without leading dot) to the unified
    AudioQuality format string. Unknown → 'unknown'."""
    return _EXTENSION_FORMAT_MAP.get(str(ext or '').lower().lstrip('.'), 'unknown')


# ── Tidal / HiFi (Monochrome is Tidal-backed) ──────────────────────────────
#
# Tidal exposes UPPER_SNAKE tier strings (``HI_RES_LOSSLESS``); HiFi's config
# uses lowercase keys (``hires``/``lossless``). We normalise both into the
# same lookup so one mapper serves both sources.

_TIDAL_HIRES = AudioQuality(format='flac', sample_rate=96000, bit_depth=24)
_TIDAL_LOSSLESS = AudioQuality(format='flac', sample_rate=44100, bit_depth=16)
_TIDAL_HIGH = AudioQuality(format='aac', bitrate=320)
_TIDAL_LOW = AudioQuality(format='aac', bitrate=96)

TIDAL_TIER_MAP = {
    'HI_RES_LOSSLESS': _TIDAL_HIRES,
    'HI_RES': _TIDAL_HIRES,
    'HIRES': _TIDAL_HIRES,
    'LOSSLESS': _TIDAL_LOSSLESS,
    'HIGH': _TIDAL_HIGH,
    'LOW': _TIDAL_LOW,
}


def quality_from_tidal_tier(tier: str) -> AudioQuality:
    """Map a Tidal/HiFi quality tier string to an AudioQuality.

    Case-insensitive; accepts both ``HI_RES`` and ``hires`` spellings.
    Unrecognised tiers map to ``format='unknown'`` so they never
    over-claim lossless quality.
    """
    key = (tier or '').strip().upper()
    return TIDAL_TIER_MAP.get(key, AudioQuality(format='unknown'))


# ── Qobuz (real API values) ────────────────────────────────────────────────

def quality_from_qobuz(sampling_rate_khz: float, bit_depth: int) -> AudioQuality:
    """Qobuz reports ``maximum_sampling_rate`` in kHz (e.g. 44.1, 96, 192)
    and ``maximum_bit_depth``. These are real values from the API.
    """
    sample_rate = int(round(sampling_rate_khz * 1000)) if sampling_rate_khz else None
    return AudioQuality(format='flac', sample_rate=sample_rate, bit_depth=bit_depth)


# ── Deezer (config code) ───────────────────────────────────────────────────

DEEZER_CODE_MAP = {
    'flac': AudioQuality(format='flac', sample_rate=44100, bit_depth=16),
    'mp3_320': AudioQuality(format='mp3', bitrate=320),
    'mp3_128': AudioQuality(format='mp3', bitrate=128),
}


def quality_from_deezer(code: str) -> AudioQuality:
    """Map a Deezer download quality code to AudioQuality.

    Deezer FLAC is always CD-quality (16-bit/44.1 kHz).
    """
    return DEEZER_CODE_MAP.get((code or '').lower(), AudioQuality(format='unknown'))


# ── Amazon Music (real sampleRate preferred, HD/UHD tier fallback) ─────────

_AMAZON_TIER_MAP = {
    'UHD': AudioQuality(format='flac', sample_rate=96000, bit_depth=24),
    'HD': AudioQuality(format='flac', sample_rate=44100, bit_depth=16),
}


def quality_from_amazon(
    tier: str,
    sample_rate: Optional[int] = None,
    bit_depth: Optional[int] = None,
) -> AudioQuality:
    """Amazon Music is FLAC; prefer the real ``sampleRate``/``bitDepth`` from
    the stream info when present, otherwise fall back to the HD/UHD tier.
    """
    base = _AMAZON_TIER_MAP.get((tier or '').strip().upper(), AudioQuality(format='flac'))
    return AudioQuality(
        format='flac',
        sample_rate=sample_rate if sample_rate is not None else base.sample_rate,
        bit_depth=bit_depth if bit_depth is not None else base.bit_depth,
    )


# ── Profile-driven download tier (replaces per-source quality settings) ─────
#
# Each source's selectable download tiers, ordered best → worst, with the
# AudioQuality the tier delivers. ``quality_tier_for_source`` walks these to
# request the LOWEST tier that satisfies the user's top global target — so the
# global quality profile, not a per-source dropdown, decides what each source
# fetches.

_SOURCE_TIER_LADDERS: dict[str, list[tuple[str, AudioQuality]]] = {
    'tidal': [
        ('hires', AudioQuality('flac', sample_rate=96000, bit_depth=24)),
        ('lossless', AudioQuality('flac', sample_rate=44100, bit_depth=16)),
        ('high', AudioQuality('aac', bitrate=320)),
        ('low', AudioQuality('aac', bitrate=96)),
    ],
    'hifi': [
        ('hires', AudioQuality('flac', sample_rate=96000, bit_depth=24)),
        ('lossless', AudioQuality('flac', sample_rate=44100, bit_depth=16)),
        ('high', AudioQuality('aac', bitrate=320)),
        ('low', AudioQuality('aac', bitrate=96)),
    ],
    'qobuz': [
        ('hires_max', AudioQuality('flac', sample_rate=192000, bit_depth=24)),
        ('hires', AudioQuality('flac', sample_rate=96000, bit_depth=24)),
        ('lossless', AudioQuality('flac', sample_rate=44100, bit_depth=16)),
        ('mp3', AudioQuality('mp3', bitrate=320)),
    ],
    'deezer': [
        ('flac', AudioQuality('flac', sample_rate=44100, bit_depth=16)),
        ('mp3_320', AudioQuality('mp3', bitrate=320)),
        ('mp3_128', AudioQuality('mp3', bitrate=128)),
    ],
    'amazon': [
        ('flac', AudioQuality('flac', sample_rate=48000, bit_depth=24)),
        ('opus', AudioQuality('aac', bitrate=320)),
    ],
}


def quality_tier_for_source(source_name: str, *, default: Optional[str] = None) -> Optional[str]:
    """Return the source tier key to request, derived from the global profile.

    Picks the lowest tier in the source's ladder that satisfies the user's
    top (most-preferred) target — respecting the quality ceiling and saving
    bandwidth. Falls back to the source's max tier when none can satisfy it
    (best effort), or to the source's max when no targets are configured.
    Returns *default* for an unknown source.
    """
    ladder = _SOURCE_TIER_LADDERS.get(source_name)
    if not ladder:
        return default

    targets, _ = load_profile_targets()
    if not targets:
        return ladder[0][0]

    top = targets[0]
    for key, aq in reversed(ladder):           # low → high
        if aq.matches_target(top):
            return key
    return ladder[0][0]                         # best effort: max tier
