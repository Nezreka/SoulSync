"""Logo-badge resolver (value -> pack/name) + the AssetStore drop-in pack reader."""

from __future__ import annotations

from core.video.overlays.logos import logo_ref, LOGO_FIELDS
from core.video.overlays.assets import AssetStore


def test_resolver_maps_media_values_to_canonical_names():
    assert logo_ref("resolution", "2160p") == ("resolution", "4k")
    assert logo_ref("resolution", "1080p") == ("resolution", "1080p")
    assert logo_ref("hdr", "Dolby Vision") == ("hdr", "dolby_vision")
    assert logo_ref("hdr", "HDR10+") == ("hdr", "hdr10plus")
    assert logo_ref("video_codec", "h265") == ("video_codec", "hevc")
    assert logo_ref("audio_codec", "TrueHD Atmos") == ("audio_codec", "atmos")
    assert logo_ref("audio_codec", "DTS-HD MA") == ("audio_codec", "dts_hd")
    assert logo_ref("source", "Blu-ray REMUX") == ("source", "remux")


def test_resolver_slugs_free_text_fields():
    assert logo_ref("network", "HBO Max") == ("network", "hbo_max")
    assert logo_ref("studio", "A24") == ("studio", "a24")
    assert logo_ref("content_rating", "PG-13") == ("content_rating", "pg_13")


def test_resolver_returns_none_for_unknown_or_empty():
    assert logo_ref("resolution", None) is None
    assert logo_ref("resolution", "") is None
    assert logo_ref("title", "anything") is None      # not a logo-able field
    assert logo_ref("genre", "Action") is None


def test_asset_store_reads_a_dropped_in_logo(tmp_path):
    store = AssetStore(tmp_path)
    assert store.read_logo("audio_codec", "atmos") is None    # no pack present
    assert store.has_logo_pack("audio_codec") is False
    d = tmp_path / "logos" / "audio_codec"
    d.mkdir(parents=True)
    (d / "atmos.png").write_bytes(b"\x89PNG-fake")
    assert store.has_logo_pack("audio_codec") is True
    assert store.read_logo("audio_codec", "atmos") == b"\x89PNG-fake"
    assert store.read_logo("../../etc", "passwd") is None      # traversal-guarded


def test_logo_fields_registered():
    for f in ("resolution", "hdr", "audio_codec", "video_codec", "source", "network", "studio"):
        assert f in LOGO_FIELDS
