"""Quality presets — the built-in ranked-target ladders behind the preset
buttons. `audiophile` must be STRICT hi-res (no 16-bit, no lossy, fallback off)
so a user who wants "24-bit only" gets it in one click; `balanced` keeps the
fuller ladder (16-bit + MP3) with fallback on.
"""

from database.music_database import MusicDatabase


def _preset(name):
    # _factory_quality_preset is pure (no self/DB) — call unbound to avoid setup.
    return MusicDatabase._factory_quality_preset(None, name)


def _labels(profile):
    return [t['label'] for t in profile['ranked_targets']]


def test_audiophile_is_strict_24bit_only():
    p = _preset('audiophile')
    assert p['fallback_enabled'] is False
    labels = _labels(p)
    assert all('24-bit' in l for l in labels)       # only 24-bit FLAC
    assert 'FLAC 16-bit' not in labels
    assert not any('MP3' in l for l in labels)


def test_balanced_still_includes_16bit_and_mp3():
    p = _preset('balanced')
    labels = _labels(p)
    assert 'FLAC 16-bit' in labels
    assert any('MP3' in l for l in labels)
    assert p['fallback_enabled'] is True


# ── v2 → v3 migration seeds Hi-Res from the old per-source dropdowns (#896 #5) ──

class _FakeCfg:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


_V2_LOSSLESS = {
    'version': 2, 'preset': 'balanced',
    'qualities': {
        'flac': {'enabled': True, 'priority': 1, 'bit_depth': 'any'},
        'mp3_320': {'enabled': True, 'priority': 2},
    },
}


def _migrate(profile, cfg_values, monkeypatch):
    monkeypatch.setattr('config.settings.config_manager', _FakeCfg(cfg_values), raising=False)
    db = MusicDatabase.__new__(MusicDatabase)
    return db._migrate_v2_to_v3(profile)


def test_v2_to_v3_seeds_hires_when_a_source_was_hires(monkeypatch):
    """A user who had Tidal on Hi-Res keeps it: the migrated profile gains 24-bit
    targets at the top so quality_tier_for_source resolves to 'hires', not a
    silent drop to lossless."""
    out = _migrate(dict(_V2_LOSSLESS), {'tidal_download.quality': 'hires'}, monkeypatch)
    top = out['ranked_targets'][0]
    assert top['format'] == 'flac' and top['bit_depth'] == 24
    # the user's existing lossy fallback is preserved below the seeded ladder
    assert any(t.get('format') == 'mp3' for t in out['ranked_targets'])


def test_v2_to_v3_no_seed_without_hires_preference(monkeypatch):
    out = _migrate(dict(_V2_LOSSLESS), {'tidal_download.quality': 'lossless'}, monkeypatch)
    assert not any(t.get('bit_depth') == 24 for t in out['ranked_targets'])


def test_v2_to_v3_no_duplicate_when_profile_already_24bit(monkeypatch):
    v2 = dict(_V2_LOSSLESS)
    v2['qualities'] = {'flac': {'enabled': True, 'priority': 1, 'bit_depth': '24'}}
    out = _migrate(v2, {'qobuz.quality': 'hires_max'}, monkeypatch)
    assert sum(1 for t in out['ranked_targets'] if t.get('bit_depth') == 24) == 1
