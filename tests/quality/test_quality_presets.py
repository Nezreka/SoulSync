"""Quality presets — the built-in ranked-target ladders behind the preset
buttons. `audiophile` must be STRICT hi-res (no 16-bit, no lossy, fallback off)
so a user who wants "24-bit only" gets it in one click; `balanced` keeps the
fuller ladder (16-bit + MP3) with fallback on.
"""

from database.music_database import MusicDatabase


def _preset(name):
    # get_quality_preset doesn't touch self/DB — call unbound to avoid setup.
    return MusicDatabase.get_quality_preset(None, name)


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
