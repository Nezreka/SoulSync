"""#886: AAC as an opt-in Soulseek quality tier.

The whole point is "purely additive": with AAC OFF (the default, and every
profile that predates this), an AAC candidate must behave EXACTLY as before —
it lands in the 'other' bucket, which the waterfall never returns, so it's
dropped. Only a profile that explicitly enables AAC makes it a selectable tier,
ranked above MP3 and below FLAC.

filter_results_by_quality_preference reads db.get_quality_profile() and walks the
buckets; we stub the db + the quarantine sweep so it runs offline.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.soulseek_client import SoulseekClient
from core.download_plugins.types import TrackResult


def _client():
    c = SoulseekClient.__new__(SoulseekClient)
    c.base_url = 'http://localhost:5030'
    c.api_key = 'k'
    c.download_path = Path('./test_downloads')
    return c


def _cand(quality, size_mb, bitrate=None):
    return TrackResult(
        username='peer', filename=f'A/B/01 - Song.{quality}',
        size=int(size_mb * 1024 * 1024), bitrate=bitrate, duration=None,
        quality=quality, free_upload_slots=1, upload_speed=1_000_000,
        queue_length=0, artist='A', title='Song', album='B', track_number=1)


def _q(enabled_flac=True, enabled_mp3=True, aac=None, fallback=True):
    qualities = {
        'flac': {'enabled': enabled_flac, 'min_kbps': 500, 'max_kbps': 10000, 'priority': 1, 'bit_depth': 'any'},
        'mp3_320': {'enabled': enabled_mp3, 'min_kbps': 280, 'max_kbps': 500, 'priority': 2},
    }
    if aac is not None:               # None => omit the tier entirely
        qualities['aac'] = {'enabled': aac, 'min_kbps': 128, 'max_kbps': 400, 'priority': 1.5}
    return {'preset': 'custom', 'qualities': qualities, 'fallback_enabled': fallback}


def _filter(candidates, profile):
    c = _client()
    fake_db = types.SimpleNamespace(get_quality_profile=lambda: profile)
    with patch('database.music_database.MusicDatabase', return_value=fake_db), \
         patch.object(SoulseekClient, '_drop_quarantined_sources', lambda self, r: r):
        return c.filter_results_by_quality_preference(candidates)


# ── AAC follows the UNIVERSAL rule (no per-format special-casing) ──────────────
# AAC is just another format: it passes only if it matches a ranked target;
# when nothing matches, the fallback toggle decides — exactly like every format.

def test_aac_not_targeted_fallback_off_is_dropped():
    out = _filter([_cand('aac', 5)], _q(aac=None, fallback=False))
    assert out == []   # no AAC target + fallback off → nothing comes through


def test_aac_not_targeted_fallback_on_comes_through():
    out = _filter([_cand('aac', 5)], _q(aac=None, fallback=True))
    assert len(out) == 1 and out[0].quality == 'aac'   # fallback grabs it


def test_aac_disabled_tier_behaves_like_not_targeted():
    # An explicitly-disabled aac tier is not a target → same as absent.
    assert _filter([_cand('aac', 5)], _q(aac=False, fallback=False)) == []


def test_flac_mp3_selection_unchanged_when_aac_absent():
    flac, mp3 = _cand('flac', 30), _cand('mp3', 5, bitrate=320)
    out = _filter([mp3, flac], _q(aac=None))
    assert out and out[0].quality == 'flac'   # FLAC still wins


# ── AAC as a real ranked target ───────────────────────────────────────────────
def test_aac_selected_when_targeted():
    out = _filter([_cand('aac', 5)], _q(aac=True))
    assert len(out) == 1 and out[0].quality == 'aac'


def test_flac_beats_aac_when_listed_higher():
    flac, aac = _cand('flac', 30), _cand('aac', 5)
    out = _filter([aac, flac], _q(aac=True))
    assert out[0].quality == 'flac'   # flac target ranked above aac (priority 1 < 1.5)


def test_aac_beats_mp3_when_listed_higher():
    mp3, aac = _cand('mp3', 5, bitrate=320), _cand('aac', 5)
    out = _filter([mp3, aac], _q(aac=True))
    assert out[0].quality == 'aac'    # aac target (1.5) ranked above mp3 (2)
