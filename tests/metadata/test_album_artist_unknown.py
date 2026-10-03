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


def test_poisoned_explicit_context_does_not_override_track_album_artist():
    """Regression for #1316: 899 wishlist tracks had album_artist overwritten
    with an unrelated artist because _explicit_artist_context (stamped from a
    poisoned batch) unconditionally overrode the track's own album artist.
    On disagreement the track data must win, loudly."""
    context = {
        "original_search_result": {"title": "Snoopafella", "artists": [{"name": "Snoop Dogg"}]},
        "album": {"artists": [{"name": "Snoop Dogg"}]},
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Alanis Morissette", "genres": []},
        },
        "source": "spotify",
    }
    md = _extract(context, {"name": "Snoop Dogg"})
    assert md["artist"] == "Snoop Dogg"
    assert md["album_artist"] == "Snoop Dogg"  # NOT "Alanis Morissette"


def test_agreeing_explicit_context_still_applies():
    """#1316 companion: when the explicit context agrees with (or the track
    lacks) an album artist, the batch hint still applies as before."""
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Singer"}]},
        "album": {"artists": [{"name": "Singer"}]},
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Singer", "genres": []},
        },
        "source": "spotify",
    }
    md = _extract(context, {"name": "Singer"})
    assert md["album_artist"] == "Singer"


def test_explicit_context_fills_missing_album_artist():
    """#1316 companion: with no album artist on the track at all, the batch
    hint remains the only signal and is used."""
    context = {
        "original_search_result": {"title": "Song", "artists": [{"name": "Singer"}]},
        "album": {},
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Singer", "genres": []},
        },
        "source": "spotify",
    }
    md = _extract(context, {"name": "Singer"})
    assert md["album_artist"] == "Singer"
