"""'Prefer explicit versions' matching preference (#923).

Two pieces:
  * detect_version_type grows a 'clean' class — bare "(Clean)" / "Censored" /
    "Edited Version" markers used to be invisible (only "clean edit"/"radio
    edit" were), so a clean rip scored like the original.
  * an opt-in scoring nudge (content_filter.prefer_explicit, gated on
    allow_explicit): explicit-marked files rank up, clean/censored/radio-edit
    files rank down, unmarked untouched. Pure ORDERING — a clean edit still
    matches when it's all that exists (the requester's explicit → unmarked →
    clean ladder, never a skip).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.matching_engine as me


@pytest.fixture
def engine():
    return me.MusicMatchingEngine()


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _set_config(monkeypatch, **values):
    monkeypatch.setattr(me, 'config_manager', _Config(values))


def _score(engine, filename, base=0.80):
    """Adjusted confidence for a candidate with a fixed base confidence, so
    only the version handling is under test."""
    src = SimpleNamespace(name='Song Title')
    cand = SimpleNamespace(filename=filename)
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    conf, version = engine.calculate_slskd_match_confidence_enhanced(src, cand)
    return conf, version


# ── the new 'clean' version class ────────────────────────────────────────────

def test_clean_markers_are_detected(engine):
    for name in ('01 - Song Title (Clean).flac',
                 'Song Title [clean].mp3',
                 'Artist - Song Title - Clean.flac',
                 'Song Title (Clean Version).flac',
                 'Song Title (Censored).mp3',
                 'Song Title (Edited Version).flac'):
        vt, penalty = engine.detect_version_type(name)
        assert vt == 'clean', name
        assert penalty > 0


def test_clean_never_matches_inside_real_titles(engine):
    # "clean" as part of a song/artist name is not a version marker
    for name in ('Mr. Clean - Song Title.flac',
                 'Cleaner Days.mp3',
                 'Clean Bandit - Rather Be.flac'):
        vt, _ = engine.detect_version_type(name)
        assert vt == 'original', name


def test_explicit_markers_unchanged(engine):
    assert engine.detect_version_type('Song Title (Explicit).flac')[0] == 'explicit'
    assert engine.detect_version_type('Song Title (Uncensored).flac')[0] == 'explicit'


# ── preference OFF: today's behavior, byte-stable ────────────────────────────

def test_default_scoring_unchanged(engine, monkeypatch):
    _set_config(monkeypatch)  # empty config = defaults (prefer off)
    explicit, _ = _score(engine, 'Song Title (Explicit).flac')
    original, _ = _score(engine, 'Song Title.flac')
    clean, _ = _score(engine, 'Song Title (Clean).flac')
    assert explicit == pytest.approx(0.80 - 0.02 * 0.5)   # the historical -2%
    assert original == pytest.approx(0.80)
    assert clean == pytest.approx(0.80 - 0.08 * 0.5)      # like a radio edit


# ── preference ON: the fallback ladder through ordering ──────────────────────

def test_preference_orders_explicit_over_unmarked_over_clean(engine, monkeypatch):
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': True})
    explicit, vt_e = _score(engine, 'Song Title (Explicit).flac')
    original, _ = _score(engine, 'Song Title.flac')
    clean, vt_c = _score(engine, 'Song Title (Clean).flac')
    radio, _ = _score(engine, 'Song Title (Radio Version).flac')

    assert vt_e == 'explicit' and vt_c == 'clean'
    assert explicit > original > clean          # the requested ladder
    assert original > radio
    assert clean > 0.5                          # never skipped — still a live candidate
    assert explicit <= 1.0


def test_preference_boost_caps_at_one(engine, monkeypatch):
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': True})
    conf, _ = _score(engine, 'Song Title (Explicit).flac', base=0.99)
    assert conf == 1.0


def test_preference_is_inert_when_explicit_content_blocked(engine, monkeypatch):
    """The parent filter wins: preferring explicit while blocking it is a
    contradiction, so the sub-setting is ignored (UI greys it out too)."""
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': False})
    explicit, _ = _score(engine, 'Song Title (Explicit).flac')
    clean, _ = _score(engine, 'Song Title (Clean).flac')
    assert explicit == pytest.approx(0.80 - 0.02 * 0.5)   # default penalties
    assert clean == pytest.approx(0.80 - 0.08 * 0.5)


def test_config_errors_mean_feature_off(engine, monkeypatch):
    class _Boom:
        def get(self, *_a, **_k):
            raise RuntimeError('config unavailable')
    monkeypatch.setattr(me, 'config_manager', _Boom())
    conf, _ = _score(engine, 'Song Title (Explicit).flac')
    assert conf == pytest.approx(0.80 - 0.02 * 0.5)


# ── the settings UI contract ─────────────────────────────────────────────────

def test_settings_ui_wires_the_sub_toggle():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8', errors='replace')
    settings_js = (root / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')

    assert 'id="prefer-explicit"' in index
    assert 'syncPreferExplicitState' in index               # parent onchange hook
    assert 'function syncPreferExplicitState' in settings_js
    assert "prefer_explicit: document.getElementById('prefer-explicit').checked" in settings_js
    assert "settings.content_filter?.prefer_explicit === true" in settings_js
