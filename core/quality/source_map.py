"""Per-source quality mappers — turn each download source's tier string or
API values into a unified :class:`~core.quality.model.AudioQuality`.

Every streaming source describes quality differently (Tidal/HiFi use tier
strings, Qobuz reports real kHz + bit depth, Deezer uses config codes,
Amazon mixes real values with HD/UHD tiers, YouTube reports Opus/AAC
bestaudio). Centralising the knowledge here keeps the per-client code to
a single call and keeps the tier tables in one auditable place.

Each value is a *claim*: the download client populates its ``TrackResult``
from it so the global ranker can choose a source, and the post-download
quality guard later verifies the real file. Over-claiming is the danger —
an unknown tier maps to ``format='unknown'`` rather than pretending to be
lossless.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from core.quality.model import AudioQuality
from core.quality.selection import (
    load_profile_by_id,
    load_profile_targets,
    targets_from_profile,
)


# Item-level profiles must not be written onto process-wide client singletons:
# best-quality fan-out can search several items concurrently. Context-local
# state gives each async task (and its copied worker context) the right ladder.
_ACTIVE_QUALITY_PROFILE_ID: ContextVar[object] = ContextVar(
    'active_quality_profile_id',
    default=None,
)


@contextmanager
def quality_profile_context(profile_id=None):
    """Expose one item's profile to source-tier resolution for this call."""
    token = _ACTIVE_QUALITY_PROFILE_ID.set(profile_id)
    try:
        yield
    finally:
        _ACTIVE_QUALITY_PROFILE_ID.reset(token)


# Named lossy codecs. Deliberately a positive list rather than "not lossless":
# core.quality.lossless.LOSSLESS_FORMATS does not carry ape or wavpack, so an
# APE profile would read as lossy and get handed a source's worst tier.
_LOSSY_FORMATS = frozenset({'mp3', 'aac', 'ogg', 'opus', 'wma'})


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


# ── YouTube (lossy bestaudio; never MP3 320) ────────────────────────────────
#
# YouTube does not offer 320 kbps MP3. There is no MP3 DASH itag; streams are
# Opus (webm) or AAC (m4a). Audio-only DASH itags:
#   139  m4a  AAC HE   ~48 kbps
#   140  m4a  AAC LC   128 kbps     (free, ubiquitous)
#   141  m4a  AAC LC   256 kbps     (YouTube / Music Premium cookies)
#   171/172 webm Vorbis ~128/192    (legacy; retired ~2018, rare on old videos)
#   249  webm Opus     ~50 kbps
#   250  webm Opus     ~70 kbps
#   251  webm Opus     ~160 kbps    (typical bestaudio without Premium)
#   599  m4a  AAC      ~30 kbps     (ultra-low)
#   600  webm Opus     ~35 kbps     (ultra-low)
#   774  webm Opus     ~256 kbps    (Music Premium, not always present)
# Muxed fallback (format=best): itag 18 (360p AAC ~96) / 22 (720p AAC ~192).
# Search uses extract_flat, so format metadata is usually missing — claim the
# typical Opus 160 (itag 251) rather than pretending the stream is MP3.
# Premium itags (141 / 774) are stamped only when a real format dict has them.

_YOUTUBE_TYPICAL = AudioQuality(format='opus', bitrate=160)
_YOUTUBE_AAC_TYPICAL = AudioQuality(format='aac', bitrate=128)


def _youtube_abr(audio_format: dict) -> Optional[int]:
    for key in ('abr', 'tbr'):
        raw = audio_format.get(key)
        if raw is None or raw == '':
            continue
        try:
            bitrate = int(raw)
        except (TypeError, ValueError):
            continue
        if bitrate:
            return bitrate
    return None


def quality_from_youtube(audio_format: Optional[dict] = None) -> AudioQuality:
    """Map a yt-dlp audio format dict to the stream's real AudioQuality.

    Missing format metadata (extract_flat / Music catalog search) claims
    typical Opus 160 kbps (itag 251) — never invented MP3 320, never assumed
    Premium from cookies. YouTube does not serve 320 kbps MP3.
    """
    if not audio_format:
        return AudioQuality(format='opus', bitrate=160)

    acodec = str(audio_format.get('acodec') or '').lower()
    ext = str(audio_format.get('ext') or '').lower()
    bitrate = _youtube_abr(audio_format)

    if 'vorbis' in acodec:
        # Legacy itags 171/172 (Vorbis in webm). Check before ext=webm so
        # they are not misread as Opus.
        return AudioQuality(format='ogg', bitrate=bitrate)
    if 'opus' in acodec or ext in ('webm', 'opus'):
        return AudioQuality(format='opus', bitrate=bitrate or _YOUTUBE_TYPICAL.bitrate)
    if 'mp4a' in acodec or 'aac' in acodec or ext in ('m4a', 'aac'):
        return AudioQuality(format='aac', bitrate=bitrate or _YOUTUBE_AAC_TYPICAL.bitrate)

    # Unknown codec: still don't claim MP3 320.
    return AudioQuality(format='opus', bitrate=bitrate or _YOUTUBE_TYPICAL.bitrate)


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
        # T2Tunes names and serves this codec as Opus. Calling it AAC made an
        # AAC-only profile appear satisfied and prevented an Opus profile from
        # requesting the tier that actually matches it.
        ('opus', AudioQuality('opus')),
    ],
    'youtube': [
        ('opus_256', AudioQuality('opus', bitrate=256)),
        ('aac_256', AudioQuality('aac', bitrate=256)),
        ('opus_160', AudioQuality('opus', bitrate=160)),
        ('aac_128', AudioQuality('aac', bitrate=128)),
    ],
}


def quality_tier_for_source(
    source_name: str,
    *,
    default: Optional[str] = None,
    profile_id=None,
) -> Optional[str]:
    """Return the source tier key to request from the applicable profile.

    Picks the lowest tier in the source's ladder that satisfies the user's
    top (most-preferred) target — respecting the quality ceiling and saving
    bandwidth. Falls back to the source's max tier when none can satisfy it
    (best effort), or to the source's max when no targets are configured.
    Returns *default* for an unknown source.
    """
    ladder = _SOURCE_TIER_LADDERS.get(source_name)
    if not ladder:
        return default

    effective_profile_id = (
        profile_id
        if profile_id is not None
        else _ACTIVE_QUALITY_PROFILE_ID.get()
    )
    if effective_profile_id is None:
        targets, _ = load_profile_targets()
    else:
        targets, _ = targets_from_profile(
            load_profile_by_id(effective_profile_id)
        )
    if not targets:
        return ladder[0][0]

    top = targets[0]
    for key, aq in reversed(ladder):           # low → high
        if aq.matches_target(top):
            return key

    # Nothing in this source's ladder can satisfy the profile, so the answer is
    # best effort. It must not be a LOSSLESS tier for a request that asked for
    # a lossy format: an AAC-only profile matches nothing in Amazon's
    # flac/opus ladder, was handed flac, and the import guard then threw the
    # download away — the most bandwidth spent on the likeliest reject. The
    # best lossy tier is still best effort (YouTube serves no MP3 either, and
    # an MP3 profile keeps getting Opus 256 there).
    wanted = str(getattr(top, 'format', '') or '').lower()
    if wanted in _LOSSY_FORMATS:
        for key, aq in ladder:                  # high → low
            if str(aq.format or '').lower() in _LOSSY_FORMATS:
                return key
    return ladder[0][0]                         # best effort: max tier
