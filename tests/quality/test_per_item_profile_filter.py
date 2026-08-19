"""#1150 — the Soulseek quality filter ignored per-item quality profiles.

Zombiehamser reported a FLAC-only profile letting MP3s through on the Soulseek
fallback. That specific claim didn't hold up (the MP3s were transfers resumed
after an slskd restart, and he withdrew it), but checking it turned up a real
bug next door.

A wishlist row can carry its own ``quality_profile_id``. Every stage resolves it
live — candidate ordering (``task_worker._candidate_ordering``), the import
guard (``core/imports/guards.py``), the import pipeline, the album-bundle format
veto. ``filter_results_by_quality_preference`` was the one stage that didn't: it
read the app-wide default unconditionally.

So assigning a profile to an item changed what survived IMPORT but not what was
CONSIDERED, and it broke both ways:

  * strict item under a loose default — lossy candidates pass the filter, get
    downloaded, and only fail at the guard. Bandwidth burnt, item fails.
  * loose item under a strict default — Zombiehamser's actual setup. His 10
    rows on a fallback-enabled profile were filtered by his FLAC-only default,
    so they were held to a stricter bar than he set.

``load_profile_by_id(None)`` already falls back to the default, so passing no id
is exactly the old behaviour — that's what keeps every album/manual caller
unchanged.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_plugins.types import TrackResult
from core.soulseek_client import SoulseekClient
import core.quality.selection as selection


def _client():
    c = SoulseekClient.__new__(SoulseekClient)
    c.base_url = 'http://localhost:5030'
    c.api_key = 'k'
    c.download_path = Path('./test_downloads')
    return c


def _cand(quality, size_mb=30, bitrate=None):
    return TrackResult(
        username='peer', filename=f'A/B/01 - Song.{quality}',
        size=int(size_mb * 1024 * 1024), bitrate=bitrate, duration=None,
        quality=quality, free_upload_slots=1, upload_speed=1_000_000,
        queue_length=0, artist='A', title='Song', album='B', track_number=1)


def _profile(*, flac_only: bool, fallback: bool):
    """A v2-shaped profile; the filter converts it to ranked targets itself."""
    qualities = {
        'flac': {'enabled': True, 'min_kbps': 500, 'max_kbps': 10000,
                 'priority': 1, 'bit_depth': 'any'},
        'mp3_320': {'enabled': not flac_only, 'min_kbps': 280, 'max_kbps': 500,
                    'priority': 2},
    }
    return {'preset': 'custom', 'qualities': qualities, 'fallback_enabled': fallback}


STRICT = _profile(flac_only=True, fallback=False)     # FLAC only, no fallback
LOOSE = _profile(flac_only=True, fallback=True)       # FLAC targets, fallback ON


@pytest.fixture()
def profiles(monkeypatch):
    """Map profile id -> profile. ``None`` is the app-wide default."""
    table = {}
    seen = []

    def _load(profile_id):
        seen.append(profile_id)
        return table.get(profile_id, table.get(None))

    monkeypatch.setattr(selection, 'load_profile_by_id', _load)
    return types.SimpleNamespace(table=table, seen=seen)


def _filter(candidates, profile_id=None):
    c = _client()
    with patch.object(SoulseekClient, '_drop_quarantined_sources', lambda self, r: r):
        if profile_id is None:
            return c.filter_results_by_quality_preference(candidates)
        return c.filter_results_by_quality_preference(candidates, profile_id=profile_id)


# ── no id behaves exactly as before ──────────────────────────────────────────

def test_no_id_still_uses_the_app_default(profiles):
    profiles.table[None] = STRICT
    assert _filter([_cand('mp3', bitrate=320)]) == []
    assert profiles.seen == [None]


def test_no_id_keeps_flac_under_the_default(profiles):
    profiles.table[None] = STRICT
    assert len(_filter([_cand('flac')])) == 1


def test_empty_input_short_circuits_without_touching_config(profiles):
    profiles.table[None] = STRICT
    assert _filter([]) == []
    assert profiles.seen == [], "no profile read for an empty candidate list"


# ── the bug: the item's own profile now decides ──────────────────────────────

def test_strict_item_under_a_loose_default_rejects_lossy(profiles):
    """The damaging direction. Before, the loose default let the MP3 through and
    it was only caught after downloading, at the import guard."""
    profiles.table[None] = LOOSE       # default accepts anything via fallback
    profiles.table[7] = STRICT         # this item is FLAC-only
    assert _filter([_cand('mp3', bitrate=320)], profile_id=7) == []
    assert 7 in profiles.seen


def test_loose_item_under_a_strict_default_accepts_its_fallback(profiles):
    """Zombiehamser's actual setup: a strict default was over-filtering the 10
    rows he'd put on a fallback-enabled profile."""
    profiles.table[None] = STRICT
    profiles.table[7] = LOOSE
    assert len(_filter([_cand('mp3', bitrate=320)], profile_id=7)) == 1


def test_the_two_profiles_genuinely_disagree(profiles):
    """Guards the fixture itself — if STRICT and LOOSE behaved the same, the two
    tests above would pass while proving nothing."""
    profiles.table[None] = STRICT
    strict_result = _filter([_cand('mp3', bitrate=320)])
    profiles.table[None] = LOOSE
    loose_result = _filter([_cand('mp3', bitrate=320)])
    assert strict_result == [] and len(loose_result) == 1


def test_an_unknown_item_profile_falls_back_to_the_default(profiles):
    """load_profile_by_id resolves a missing row to the default rather than
    raising, so a stale id can never wedge a download."""
    profiles.table[None] = STRICT
    assert _filter([_cand('mp3', bitrate=320)], profile_id=999) == []


# ── the id actually reaches the filter from the call chain ───────────────────

class _FakeEngine:
    """Only what the Soulseek lane of get_valid_candidates touches."""

    def find_best_slskd_matches_enhanced(self, *_a, **_k):
        return [_cand('flac')]

    def normalize_string(self, text):
        return str(text or '').lower()


class _FakeConfig:
    def get(self, *_a, **_k):
        return 0


def test_get_valid_candidates_forwards_the_profile_id(monkeypatch):
    """The wiring, not the filtering. A per-item profile that never leaves
    validation.py would look fixed and change nothing."""
    from core.downloads import validation

    forwarded = {}

    class _Soulseek:
        def filter_results_by_quality_preference(self, results, profile_id=None):
            forwarded['profile_id'] = profile_id
            return []

    class _Orch:
        def client(self, _name):
            return _Soulseek()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, 'matching_engine', _FakeEngine())
    monkeypatch.setattr(validation, 'config_manager', _FakeConfig())

    validation.get_valid_candidates([_cand('flac')], types.SimpleNamespace(
        name='Song', artists=['A'], duration_ms=1000, album='B'), 'q', 42)
    assert forwarded['profile_id'] == 42


def test_get_valid_candidates_defaults_to_no_profile(monkeypatch):
    from core.downloads import validation

    forwarded = {}

    class _Soulseek:
        def filter_results_by_quality_preference(self, results, profile_id=None):
            forwarded['profile_id'] = profile_id
            return []

    class _Orch:
        def client(self, _name):
            return _Soulseek()

    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, 'matching_engine', _FakeEngine())
    monkeypatch.setattr(validation, 'config_manager', _FakeConfig())

    validation.get_valid_candidates([_cand('flac')], types.SimpleNamespace(
        name='Song', artists=['A'], duration_ms=1000, album='B'), 'q')
    assert forwarded['profile_id'] is None


def test_the_worker_reads_the_id_off_the_wishlist_row():
    """task_worker pulls quality_profile_id off track_data and hands it to
    get_valid_candidates (primary query loop, YouTube catalog ytsearch
    fallback, and hybrid fallback)."""
    source = Path('core/downloads/task_worker.py').read_text(encoding='utf-8')
    assert "_profile_id = track_data.get('quality_profile_id')" in source
    assert source.count('deps.get_valid_candidates(') == 3
    assert source.count('_profile_id)') >= 4
