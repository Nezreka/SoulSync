"""Tests for `_verify_stream_tier` — the guard that rejects silent Tidal
quality downgrades so the fallback chain (or "HiRes only" with fallback
disabled) behaves the way users configure it to.

Without this check, a user with "HiRes only, no quality fallback" who
asks Tidal for a track that's only available in AAC 320kbps would
receive the 320kbps stream silently — Tidal never raises, it just
serves the highest tier available — and the downloader would accept
the m4a file and report success. Reported by Netti93.

Tiers ranked worst-to-best:
    LOW < HIGH < LOSSLESS < HI_RES < HI_RES_LOSSLESS

Accepting matches and upgrades, rejecting downgrades, rejecting
unrecognized values.

Note on the fake Quality values: tidalapi's real Quality enum has
VALUES that differ from the member names (e.g., `low_320k.value ==
'HIGH'`, `high_lossless.value == 'LOSSLESS'`). The stub mirrors real
values so the tests catch case-sensitivity regressions.
"""

import sys
import types


if 'tidalapi' not in sys.modules:
    _fake = types.ModuleType('tidalapi')

    class _FakeQuality:
        low_96k = 'LOW'
        low_320k = 'HIGH'
        high_lossless = 'LOSSLESS'
        hi_res = 'HI_RES'
        hi_res_lossless = 'HI_RES_LOSSLESS'

    _fake.Quality = _FakeQuality
    _fake.media = types.SimpleNamespace(Track=object)
    sys.modules['tidalapi'] = _fake


from core.tidal_download_client import QUALITY_MAP, _verify_stream_tier  # noqa: E402


class _FakeStream:
    """Minimal stand-in for tidalapi.media.Stream."""

    def __init__(self, audio_quality=None):
        if audio_quality is not None:
            self.audio_quality = audio_quality


# ---------------------------------------------------------------------------
# Match — served quality is exactly what was requested
# ---------------------------------------------------------------------------

def test_served_quality_matches_request():
    stream = _FakeStream(audio_quality='HI_RES_LOSSLESS')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['hires'], 'hires')
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# Upgrades — Tidal serving a higher tier than requested is accepted
# ---------------------------------------------------------------------------

def test_lossless_request_upgraded_to_hires_is_accepted():
    """If Tidal serves HI_RES_LOSSLESS on a LOSSLESS-tier request (rare
    but possible on tracks flagged as such in Tidal's catalog), we take
    the upgrade — rejecting a better-than-asked tier would be user-
    hostile."""
    stream = _FakeStream(audio_quality='HI_RES_LOSSLESS')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['lossless'], 'lossless')
    assert ok is True
    assert reason is None


def test_lossless_request_upgraded_to_mqa_hires_is_accepted():
    stream = _FakeStream(audio_quality='HI_RES')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['lossless'], 'lossless')
    assert ok is True
    assert reason is None


def test_low_request_upgraded_to_any_higher_tier_is_accepted():
    stream = _FakeStream(audio_quality='LOSSLESS')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['low'], 'low')
    assert ok is True
    assert reason is None


# ---------------------------------------------------------------------------
# Downgrades — the reported bug
# ---------------------------------------------------------------------------

def test_hires_downgraded_to_aac_is_rejected():
    """The exact case Netti93 reported: asked HiRes, Tidal served
    AAC 320kbps (`'HIGH'` in Tidal's API vocabulary)."""
    stream = _FakeStream(audio_quality='HIGH')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['hires'], 'hires')
    assert ok is False
    assert 'HIGH' in reason
    assert 'HI_RES_LOSSLESS' in reason


def test_hires_lossless_downgraded_to_mqa_hires_is_rejected():
    """User explicitly asked for HI_RES_LOSSLESS (true lossless HiRes).
    Getting MQA-encoded HI_RES is a downgrade even though both are
    "HiRes tier" marketing-wise — MQA is lossy."""
    stream = _FakeStream(audio_quality='HI_RES')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['hires'], 'hires')
    assert ok is False
    assert 'HI_RES_LOSSLESS' in reason


def test_lossless_downgraded_to_aac_is_rejected():
    stream = _FakeStream(audio_quality='HIGH')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['lossless'], 'lossless')
    assert ok is False
    assert 'LOSSLESS' in reason


# ---------------------------------------------------------------------------
# Unknown quality strings — reject conservatively
# ---------------------------------------------------------------------------

def test_unknown_served_quality_is_rejected():
    """If Tidal introduces a new tier we haven't mapped yet, we can't
    prove it's acceptable — reject rather than silently pass through,
    so the next fallback tier gets a chance and the final diagnostic
    log names the unknown value."""
    stream = _FakeStream(audio_quality='SPATIAL_360_DREAM_TIER')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['hires'], 'hires')
    assert ok is False
    assert 'SPATIAL_360_DREAM_TIER' in reason
    assert 'unrecognized' in reason.lower() or 'can\'t verify' in reason.lower()


# ---------------------------------------------------------------------------
# Defensive — missing attributes must not spuriously fail downloads
# ---------------------------------------------------------------------------

def test_stream_without_audio_quality_attr_is_accepted():
    """Older tidalapi versions may not expose audio_quality — treat as
    "can't verify" and let pre-existing codec / file-size guards decide.
    Better to miss a downgrade than break every Tidal download after a
    library upgrade."""
    stream = _FakeStream()
    assert not hasattr(stream, 'audio_quality')
    ok, reason = _verify_stream_tier(stream, QUALITY_MAP['hires'], 'hires')
    assert ok is True
    assert reason is None


def test_quality_info_without_tidal_quality_is_accepted():
    """If QUALITY_MAP somehow lacks 'tidal_quality' (tidalapi failed to
    import at module load), don't spuriously reject streams."""
    stream = _FakeStream(audio_quality='HI_RES_LOSSLESS')
    ok, reason = _verify_stream_tier(stream, {'label': 'x'}, 'hires')
    assert ok is True
    assert reason is None
