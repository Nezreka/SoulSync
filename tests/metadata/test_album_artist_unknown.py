"""Regression for #735 (CubeComming): importing media overwrites the
album-artist tag ('Albuminterpret') to 'Unknown Artist'.

When the metadata source resolves the track artist correctly (e.g. Billie
Eilish) but the album CONTEXT comes back with an unresolved 'Unknown Artist'
placeholder, extract_source_metadata used to take album_ctx['artists'][0]['name']
unconditionally — clobbering the real album artist. The fix: an 'Unknown Artist'
placeholder must not override a real artist.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _cfg():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "metadata_enhancement.enabled": True,
        "metadata_enhancement.tags.write_multi_artist": False,
        "metadata_enhancement.tags.feat_in_title": False,
        "metadata_enhancement.tags.artist_separator": ", ",
        "file_organization.collab_artist_mode": "first",
    }.get(key, default)
    return cfg


def _extract(context, artist_dict, album_info=None):
    from core.metadata import source as src
    with patch.object(src, "get_config_manager", return_value=_cfg()):
        return src.extract_source_metadata(context, artist_dict, album_info or {})


def test_unknown_album_artist_placeholder_does_not_clobber_real_artist():
    # Track resolves to a real artist; album context is an unresolved placeholder.
    context = {
        "original_search_result": {"title": "Therefore I Am", "artists": [{"name": "Billie Eilish"}]},
        "album": {"artists": [{"name": "Unknown Artist"}]},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Billie Eilish"})
    assert md["artist"] == "Billie Eilish"
    assert md["album_artist"] == "Billie Eilish"  # NOT "Unknown Artist"


def test_real_album_artist_still_overrides():
    # A genuine album artist (not the placeholder) should still be used.
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Some Singer"}]},
        "album": {"artists": [{"name": "Various Artists"}]},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Some Singer"})
    assert md["album_artist"] == "Various Artists"


def test_no_album_context_falls_back_to_track_artist():
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Solo Act"}]},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Solo Act"})
    assert md["album_artist"] == "Solo Act"


def test_empty_album_artist_does_not_clobber():
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Real Name"}]},
        "album": {"artists": [{"name": ""}]},
        "source": "spotify",
    }
    md = _extract(context, {"name": "Real Name"})
    assert md["album_artist"] == "Real Name"
