"""#1130 — "Why are FLACs downloading when Quality Profile is set to MP3s?"

The reporter set the **Space Saver** preset (an MP3-only ranked ladder,
`fallback_enabled=True`) and still got FLACs, while their log showed MP3s
present in the same search results:

    Quality Filter: returning 10 candidate(s), best=FLAC
    Quality Filter: returning 10 candidate(s), best=MP3 320kbps   <- occasionally

That inconsistency is the tell. `AudioQuality.matches_target` fails a
`min_bitrate` target whenever the bitrate is unknown (`(self.bitrate or 0) <
min_bitrate`), and slskd frequently omits the bitrate attribute. So:

  * MP3 WITH a reported bitrate  -> matches an MP3 tier -> MP3 wins.
  * MP3 WITHOUT one             -> matches NO tier      -> fallback path.

and the fallback ranked purely by `tier_score`, where FLAC's base (100) always
beats MP3's (50). A Space Saver user got handed the one format they were
explicitly trying to avoid.

Note the asymmetry this exposes: the FLAC targets carry documented heuristics
for missing sample-rate/bit-depth metadata; the lossy side has none. Rather than
letting a bitrate-less MP3 over-claim a 320kbps tier (the same over-claiming the
FLAC code deliberately refuses), the fallback now honours the user's FORMAT
preference: formats named in their ladder outrank formats that aren't.
"""

from __future__ import annotations

import pytest

from core.quality.model import AudioQuality, QualityTarget, filter_and_rank


class Candidate:
    """Minimal stand-in for a TrackResult — filter_and_rank only needs
    `.audio_quality`."""

    def __init__(self, audio_quality: AudioQuality, name: str):
        self.audio_quality = audio_quality
        self.name = name

    def __repr__(self) -> str:  # keeps assertion output readable
        return self.name


# The three shipped presets, as `get_quality_preset` builds them.
SPACE_SAVER = [
    QualityTarget(label="MP3 320kbps", format="mp3", min_bitrate=320),
    QualityTarget(label="MP3 256kbps", format="mp3", min_bitrate=256),
    QualityTarget(label="MP3 192kbps", format="mp3", min_bitrate=192),
]
BALANCED = [
    QualityTarget(label="FLAC 24-bit/96kHz", format="flac", bit_depth=24, min_sample_rate=96_000),
    QualityTarget(label="FLAC 16-bit", format="flac", bit_depth=16),
] + SPACE_SAVER
AUDIOPHILE = [
    QualityTarget(label="FLAC 24-bit/96kHz", format="flac", bit_depth=24, min_sample_rate=96_000),
]


def flac(bitrate=1000, sample_rate=44_100, bit_depth=16):
    return Candidate(AudioQuality("flac", bitrate, sample_rate, bit_depth), "FLAC")


def mp3(bitrate):
    label = f"MP3-{bitrate}" if bitrate else "MP3-unknown-bitrate"
    return Candidate(AudioQuality("mp3", bitrate), label)


# ── the reported bug ──────────────────────────────────────────────────────────

def test_space_saver_prefers_mp3_when_slskd_omitted_the_bitrate():
    """The exact #1130 scenario: no MP3 can prove its bitrate, so nothing
    matches the ladder and we fall back — MP3 must still win."""
    candidates = [flac(), mp3(None)]
    ranked = filter_and_rank(candidates, SPACE_SAVER, fallback_enabled=True)
    assert ranked[0].audio_quality.format == "mp3"


def test_space_saver_still_offers_flac_rather_than_dropping_it():
    """Fallback means "take what's on offer" — FLAC is demoted, not discarded,
    so a user whose only option is FLAC still gets the track."""
    candidates = [flac(), mp3(None)]
    ranked = filter_and_rank(candidates, SPACE_SAVER, fallback_enabled=True)
    assert len(ranked) == 2
    assert [c.audio_quality.format for c in ranked] == ["mp3", "flac"]


def test_space_saver_with_only_flac_available_still_returns_it():
    ranked = filter_and_rank([flac()], SPACE_SAVER, fallback_enabled=True)
    assert [c.name for c in ranked] == ["FLAC"]


def test_space_saver_orders_unknown_bitrate_mp3s_among_themselves_by_score():
    """Within the preferred-format group, tier_score still decides."""
    candidates = [flac(), mp3(None), mp3(128)]
    ranked = filter_and_rank(candidates, SPACE_SAVER, fallback_enabled=True)
    assert [c.name for c in ranked] == ["MP3-128", "MP3-unknown-bitrate", "FLAC"]


# ── the path that already worked, pinned so it can't regress ─────────────────

def test_space_saver_matched_tier_still_wins_outright():
    """When an MP3 does prove 320kbps it matches tier 0, and the fallback
    branch is never reached — FLAC is excluded entirely, not just demoted."""
    candidates = [flac(), mp3(320)]
    ranked = filter_and_rank(candidates, SPACE_SAVER, fallback_enabled=True)
    assert [c.name for c in ranked] == ["MP3-320"]


# ── no behaviour change for the other presets ────────────────────────────────

def test_balanced_is_unaffected_because_it_names_both_formats():
    """FLAC and MP3 are both in the ladder, so both are 'preferred' and
    tier_score alone orders them — exactly as before the fix."""
    candidates = [mp3(None), flac(bitrate=None, sample_rate=None, bit_depth=None)]
    ranked = filter_and_rank(candidates, BALANCED, fallback_enabled=True)
    assert ranked[0].audio_quality.format == "flac"


def test_audiophile_with_fallback_disabled_still_returns_nothing():
    """fallback_enabled=False must keep rejecting everything that misses the
    ladder — the fix must not smuggle candidates through."""
    candidates = [mp3(320), flac(sample_rate=44_100, bit_depth=16)]
    assert filter_and_rank(candidates, AUDIOPHILE, fallback_enabled=False) == []


def test_empty_target_list_is_pure_tier_score():
    """No ladder at all -> no format preference to honour."""
    candidates = [mp3(320), flac()]
    ranked = filter_and_rank(candidates, [], fallback_enabled=True)
    assert ranked[0].audio_quality.format == "flac"


def test_targets_without_a_format_do_not_crash_the_preference_set():
    """A bare bitrate target carries no format — building the preferred-format
    set must skip it rather than dereference `None`.

    Both candidates are given an unknown bitrate so neither matches the target
    and we genuinely reach the fallback branch. With no format named anywhere,
    there is no preference to honour and tier_score alone orders them.
    """
    targets = [QualityTarget(label="anything 320", min_bitrate=320)]
    candidates = [flac(bitrate=None, sample_rate=None, bit_depth=None), mp3(None)]
    ranked = filter_and_rank(candidates, targets, fallback_enabled=True)
    assert len(ranked) == 2
    assert ranked[0].audio_quality.format == "flac"
