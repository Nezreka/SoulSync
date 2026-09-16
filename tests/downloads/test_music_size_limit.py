from types import SimpleNamespace

import pytest

from core.downloads import size_limit


@pytest.mark.parametrize('size,duration,cap,exceeds', [
    (35_000_000, 210_000, 10, False),
    (35_000_001, 210_000, 10, True),
    (60_000_000, 360_000, 10, False),
    (180_000_000, 180_000, 10, True),
    (35_000_000, 210_000, 0, False),
    (35_000_000, 210_000, -1, False),
    (0, 210_000, 10, False),
    (35_000_000, None, 10, False),
    (35_000_000, 'bad', 10, False),
    (35_000_000, 210_000, float('nan'), False),
    (35_000_000, 210_000, float('inf'), False),
    (875_001, 210_000, 0.25, True),
])
def test_size_density_boundary(size, duration, cap, exceeds):
    assert size_limit.exceeds_size_limit(size, duration, cap) is exceeds


def test_advertised_duration_wins_and_unknown_duration_uses_track(monkeypatch):
    monkeypatch.setattr(size_limit, 'configured_limit', lambda: 10)
    extended = SimpleNamespace(size=60_000_000, duration=360_000)
    missing = SimpleNamespace(size=35_000_001, duration=None)
    fits = SimpleNamespace(size=35_000_000, duration=None)
    assert size_limit.filter_music_candidates([extended, missing, fits], expected_duration_ms=210_000) == [extended, fits]


def test_unknown_metadata_and_bundle_sizes_are_not_compared_to_track_length(monkeypatch):
    monkeypatch.setattr(size_limit, 'configured_limit', lambda: 10)
    rows = [SimpleNamespace(size=0, duration=210_000),
            SimpleNamespace(size=900_000_000, duration=None),
            SimpleNamespace(username='torrent', size=900_000_000, duration=None)]
    assert size_limit.filter_music_candidates(rows) == rows
    assert size_limit.filter_music_candidates(rows[-1:], expected_duration_ms=210_000) == rows[-1:]


def test_disabled_preserves_candidates(monkeypatch):
    monkeypatch.setattr(size_limit, 'configured_limit', lambda: 0)
    rows = [SimpleNamespace(size=900_000_000, duration=60_000)]
    assert size_limit.filter_music_candidates(rows) is rows
