from types import SimpleNamespace

from core.imports import guards


class _FakeDB:
    def __init__(self, quality_profile):
        self._quality_profile = quality_profile

    def get_quality_profile(self):
        return self._quality_profile


def test_check_flac_bit_depth_rejects_strict_mismatch(monkeypatch):
    monkeypatch.setattr(
        guards,
        "MusicDatabase",
        lambda: _FakeDB({"qualities": {"flac": {"bit_depth": "16", "bit_depth_fallback": False}}}),
    )
    monkeypatch.setattr(
        guards,
        "_get_config_manager",
        lambda: SimpleNamespace(get=lambda _key, default=None: False),
    )

    context = {"_audio_quality": "FLAC 24bit", "track_info": {"name": "Song One"}}

    assert guards.check_flac_bit_depth("/tmp/Song One.flac", context) == (
        "FLAC bit depth mismatch: file is 24-bit, preference is 16-bit"
    )


def test_check_flac_bit_depth_allows_fallback_when_enabled(monkeypatch):
    monkeypatch.setattr(
        guards,
        "MusicDatabase",
        lambda: _FakeDB({"qualities": {"flac": {"bit_depth": "16", "bit_depth_fallback": True}}}),
    )
    monkeypatch.setattr(
        guards,
        "_get_config_manager",
        lambda: SimpleNamespace(get=lambda _key, default=None: False),
    )

    context = {"_audio_quality": "FLAC 24bit", "track_info": {"name": "Song One"}}

    assert guards.check_flac_bit_depth("/tmp/Song One.flac", context) is None
