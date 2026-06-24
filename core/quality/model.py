"""Source-agnostic audio quality model.

Every download source maps its result into ``AudioQuality``.
The ``QualityTarget`` list in the user's profile defines the
priority order (1st choice, 2nd choice, …). ``rank_candidate``
scores any ``AudioQuality`` against that list so the same
logic drives Soulseek, Tidal, Deezer, torrent — no per-source
quality pipelines needed.

Soulseek attribute type codes (Soulseek protocol spec):
  0 = bitrate (kbps)
  1 = duration (seconds)
  2 = VBR flag
  4 = sample rate (Hz)  — FLAC / WAV only
  5 = bit depth         — FLAC / WAV only
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class AudioQuality:
    """Unified audio quality descriptor — source-agnostic."""

    format: str                        # 'flac', 'mp3', 'aac', 'ogg', 'wav', 'unknown'
    bitrate: Optional[int] = None      # kbps
    sample_rate: Optional[int] = None  # Hz  (e.g. 44100, 96000, 192000)
    bit_depth: Optional[int] = None    # bits per sample (16, 24, 32)

    def tier_score(self) -> float:
        """Continuous score for ranking within a matched target bucket.
        Higher = better.  Used as a tiebreaker after target-list matching.
        """
        format_base: dict[str, float] = {
            'flac': 100.0,
            'wav':   95.0,
            'ogg':   70.0,
            'aac':   60.0,
            'mp3':   50.0,
            'wma':   30.0,
        }
        base = format_base.get(self.format.lower(), 10.0)

        if self.format.lower() in ('flac', 'wav'):
            sr = self.sample_rate or 44100
            bd = self.bit_depth or 16
            # sample-rate contribution: 44.1 kHz = 0, 192 kHz = +20
            sr_score = min(sr / 192_000, 1.0) * 20
            # bit-depth contribution: 16-bit = 0, 24-bit = +10
            bd_score = max(bd - 16, 0) / 8 * 10
            return base + sr_score + bd_score
        else:
            br = self.bitrate or 0
            return base + min(br / 320, 1.0) * 10

    def matches_target(self, target: QualityTarget) -> bool:
        """True when this quality satisfies every constraint in *target*."""
        if target.format and target.format.lower() != self.format.lower():
            return False
        if target.min_bitrate and (self.bitrate or 0) < target.min_bitrate:
            return False
        if target.min_sample_rate:
            if self.sample_rate is not None:
                if self.sample_rate < target.min_sample_rate:
                    return False
            else:
                # No sample-rate metadata (common on slskd FLAC). Use the kbps
                # heuristic when a bitrate is present; otherwise we CANNOT
                # confirm the spec, so fail the strict target rather than
                # over-claim it — an unknown-spec FLAC must not outrank a known
                # 16/44 FLAC under a hi-res target (#896 review #4). It falls to
                # the plain-flac bucket instead.
                # 16-bit/44.1 kHz ≈ 1411 kbps; 24-bit/96 kHz ≈ 4608 kbps.
                if self.format.lower() == 'flac' and self.bitrate:
                    required_kbps = _sample_rate_to_min_kbps(target.min_sample_rate, target.bit_depth or 24)
                    if self.bitrate < required_kbps:
                        return False
                else:
                    return False
        if target.bit_depth:
            if self.bit_depth is not None:
                if self.bit_depth < target.bit_depth:
                    return False
            else:
                # No bit-depth metadata. A hi-res (>=24-bit) target needs proof:
                # use the kbps heuristic if a bitrate is present, else fail
                # rather than over-claim. The 16-bit baseline still matches an
                # unknown-spec FLAC (any FLAC is at least CD quality). #896 review #4.
                if self.format.lower() == 'flac' and target.bit_depth >= 24:
                    if self.bitrate:
                        if self.bitrate < 1450:
                            return False
                    else:
                        return False
        return True

    def label(self) -> str:
        """Human-readable label, e.g. 'FLAC 24-bit/192kHz' or 'MP3 320kbps'."""
        fmt = self.format.upper()
        if self.format.lower() in ('flac', 'wav'):
            bd = f"{self.bit_depth}-bit/" if self.bit_depth else ""
            sr = f"{self.sample_rate // 1000}kHz" if self.sample_rate else ""
            detail = f" {bd}{sr}".rstrip()
            return f"{fmt}{detail}" if detail.strip() else fmt
        else:
            br = f" {self.bitrate}kbps" if self.bitrate else ""
            return f"{fmt}{br}"

    @classmethod
    def from_slskd_file(cls, file_data: dict, extension: str) -> 'AudioQuality':
        """Build from a raw slskd API file entry.

        slskd exposes Soulseek protocol file attributes as:
          ``{"attributes": [{"type": 4, "value": 96000}, {"type": 5, "value": 24}, ...]}``
        """
        attrs = {a['type']: a['value'] for a in file_data.get('attributes', [])}
        return cls(
            format=extension.lower().lstrip('.'),
            bitrate=file_data.get('bitRate') or attrs.get(0),
            sample_rate=attrs.get(4),
            bit_depth=attrs.get(5),
        )

    @classmethod
    def from_tier(cls, fmt: str, bitrate: int, sample_rate: Optional[int] = None, bit_depth: Optional[int] = None) -> 'AudioQuality':
        """Build from a hardcoded quality tier (Tidal, Deezer, Qobuz)."""
        return cls(format=fmt, bitrate=bitrate, sample_rate=sample_rate, bit_depth=bit_depth)

    @classmethod
    def from_extension_and_bitrate(cls, extension: str, bitrate: Optional[int]) -> 'AudioQuality':
        """Minimal constructor when only format + bitrate are known (torrent, YouTube)."""
        return cls(format=extension.lower().lstrip('.'), bitrate=bitrate)


@dataclass
class QualityTarget:
    """One ranked entry in the user's quality priority list."""

    label: str = ""
    format: Optional[str] = None           # 'flac', 'mp3', 'aac', …
    bit_depth: Optional[int] = None        # 16, 24
    min_sample_rate: Optional[int] = None  # Hz
    min_bitrate: Optional[int] = None      # kbps (lossy)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "")}

    @classmethod
    def from_dict(cls, d: dict) -> 'QualityTarget':
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Default priority list ──────────────────────────────────────────────────────

DEFAULT_RANKED_TARGETS: List[QualityTarget] = [
    QualityTarget(label='FLAC 24-bit/192kHz', format='flac', bit_depth=24, min_sample_rate=192_000),
    QualityTarget(label='FLAC 24-bit/96kHz',  format='flac', bit_depth=24, min_sample_rate=96_000),
    QualityTarget(label='FLAC 24-bit/48kHz',  format='flac', bit_depth=24, min_sample_rate=48_000),
    QualityTarget(label='FLAC 24-bit/44.1kHz',format='flac', bit_depth=24, min_sample_rate=44_100),
    QualityTarget(label='FLAC 16-bit',        format='flac', bit_depth=16),
    QualityTarget(label='MP3 320kbps',        format='mp3',  min_bitrate=320),
    QualityTarget(label='MP3 256kbps',        format='mp3',  min_bitrate=256),
    QualityTarget(label='MP3 192kbps',        format='mp3',  min_bitrate=192),
]


# ── Ranking helpers ────────────────────────────────────────────────────────────

def rank_candidate(aq: AudioQuality, targets: List[QualityTarget]) -> Tuple[int, float]:
    """Return *(target_index, tier_score)* for sorting.

    Lower ``target_index`` → higher priority match.
    Candidates that satisfy no target get ``index = len(targets)``
    (they sort last but are not discarded — the caller decides that).
    """
    for i, target in enumerate(targets):
        if aq.matches_target(target):
            return (i, aq.tier_score())
    return (len(targets), aq.tier_score())


def filter_and_rank(
    candidates: list,
    targets: List[QualityTarget],
    *,
    fallback_enabled: bool = True,
) -> list:
    """Sort *candidates* (any objects with an ``audio_quality`` attribute)
    by quality priority.

    Returns the subset that matched the *highest-priority* satisfied target,
    sorted by ``tier_score`` descending within that group.
    Falls back to all candidates sorted by score when ``fallback_enabled``
    and nothing matches, or when targets list is empty.
    """
    if not targets:
        candidates_copy = list(candidates)
        candidates_copy.sort(key=lambda c: c.audio_quality.tier_score(), reverse=True)
        return candidates_copy

    scored = [(rank_candidate(c.audio_quality, targets), c) for c in candidates]

    # Best target index that any candidate reached
    best_idx = min((s[0][0] for s in scored), default=len(targets))

    if best_idx < len(targets):
        winners = [c for (idx, _), c in scored if idx == best_idx]
        winners.sort(key=lambda c: c.audio_quality.tier_score(), reverse=True)
        return winners

    if fallback_enabled:
        all_sorted = list(candidates)
        all_sorted.sort(key=lambda c: c.audio_quality.tier_score(), reverse=True)
        return all_sorted

    return []


# ── Migration helper ───────────────────────────────────────────────────────────

def v2_qualities_to_ranked_targets(qualities: dict) -> List[dict]:
    """Convert old v2 ``qualities`` dict to a ranked-targets list.

    Preserves the user's existing priority order while upgrading to the
    richer target format.
    """
    _FORMAT_MAP = {
        'flac':    {'format': 'flac', 'bit_depth': None},
        'mp3_320': {'format': 'mp3',  'min_bitrate': 320},
        'mp3_256': {'format': 'mp3',  'min_bitrate': 256},
        'mp3_192': {'format': 'mp3',  'min_bitrate': 192},
        # AAC (#886): opt-in tier. Match on format alone — Soulseek AAC/.m4a
        # rarely carries a bitrate attribute, so a min_bitrate gate would
        # reject every bitrate-less AAC. Priority order (above MP3, below FLAC)
        # is preserved by the caller's priority sort, not by min_bitrate.
        'aac':     {'format': 'aac'},
    }
    enabled = [
        (cfg.get('priority', 999), name, cfg)
        for name, cfg in qualities.items()
        if cfg.get('enabled', False)
    ]
    enabled.sort()
    targets = []
    for _, name, cfg in enabled:
        base = _FORMAT_MAP.get(name, {}).copy()
        if not base:
            continue
        if name == 'flac':
            bd = cfg.get('bit_depth', 'any')
            if bd == '24':
                base['bit_depth'] = 24
                base['label'] = 'FLAC 24-bit'
            elif bd == '16':
                base['bit_depth'] = 16
                base['label'] = 'FLAC 16-bit'
            else:
                base['label'] = 'FLAC (any)'
        else:
            base['label'] = name.upper().replace('_', ' ')
        targets.append(base)
    return targets


# ── Internal helpers ───────────────────────────────────────────────────────────

def _sample_rate_to_min_kbps(sample_rate: int, bit_depth: int) -> int:
    """Approximate minimum kbps for a lossless file at the given spec.
    Used as heuristic when actual sample-rate metadata is absent.
    """
    # kbps = sample_rate * channels * bit_depth / 1000 * compression_ratio
    # Assume stereo (2 ch) and ~0.6 FLAC compression ratio
    raw_kbps = sample_rate * 2 * bit_depth / 1000
    return int(raw_kbps * 0.55)  # conservative compressed estimate
