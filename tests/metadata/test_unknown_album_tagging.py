"""Regression: extract_source_metadata stamped the literal 'Unknown Album'
over downloaded files whose playlist entry never carried an album
(paste-link Spotify playlists, discovery-pool rows, TXT imports).

'Unknown Album' is the no-album placeholder, not a name. When every album
candidate is the placeholder, the tag must fall through the album-less
rungs — including the downloaded file's own album tag, which the
enrichment writer snapshots onto the context as ``_file_album_tag``
before clearing the file's tags — instead of stamping the placeholder.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

UNKNOWN = "Unknown Album"
REAL = "What the Brothers Sang"


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


def _context(**overrides):
    context = {
        "source": "spotify",
        "artist": {"name": "Dawn McCarthy"},
        "album": {"name": UNKNOWN, "total_tracks": 0, "album_type": "album"},
        "track_info": {"name": "Kentucky", "artists": [{"name": "Dawn McCarthy"}]},
        "original_search_result": {
            "title": "Kentucky",
            "album": UNKNOWN,
            "artists": [{"name": "Dawn McCarthy"}],
        },
    }
    context.update(overrides)
    return context


def test_placeholder_album_info_with_file_tag_uses_file_tag():
    md = _extract(
        _context(_file_album_tag=REAL),
        {"name": "Dawn McCarthy"},
        {"is_album": True, "album_name": UNKNOWN, "track_number": 13},
    )
    assert md["album"] == REAL


def test_placeholder_album_info_with_real_album_context_uses_context():
    md = _extract(
        _context(album={"name": REAL, "total_tracks": 13, "album_type": "album"}),
        {"name": "Dawn McCarthy"},
        {"is_album": True, "album_name": UNKNOWN, "track_number": 13},
    )
    assert md["album"] == REAL


def test_placeholder_everywhere_without_file_tag_falls_back_to_title():
    # The standing album-less convention (a single models album = title) —
    # anything but the placeholder.
    md = _extract(
        _context(),
        {"name": "Dawn McCarthy"},
        {"is_album": True, "album_name": UNKNOWN, "track_number": 13},
    )
    assert md["album"] == "Kentucky"


def test_real_album_info_name_is_stamped_unchanged():
    md = _extract(
        _context(),
        {"name": "Dawn McCarthy"},
        {"is_album": True, "album_name": REAL, "track_number": 13},
    )
    assert md["album"] == REAL


def test_placeholder_file_tag_is_ignored():
    md = _extract(
        _context(_file_album_tag=UNKNOWN),
        {"name": "Dawn McCarthy"},
        {"is_album": False, "album_name": "", "track_number": 1},
    )
    assert md["album"] == "Kentucky"


# ---------------------------------------------------------------------------
# End to end through enhance_file_metadata: the writer snapshots the file's
# own album tag before clearing, so a placeholder context can't stamp over
# it — and the snapshot must not leak into the next file that reuses the
# same context dict (contexts are shared across a task's attempts).
# ---------------------------------------------------------------------------


def _write_flac(path, album, album_artist=None):
    streaminfo = bytearray(34)
    streaminfo[0:2] = (4096).to_bytes(2, "big")
    streaminfo[2:4] = (4096).to_bytes(2, "big")
    streaminfo[10:18] = ((44100 << 44) | (1 << 41) | (15 << 36)).to_bytes(8, "big")
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big") + bytes(streaminfo))
    from mutagen.flac import FLAC

    audio = FLAC(str(path))
    if album is not None:
        audio["album"] = [album]
    if album_artist is not None:
        audio["albumartist"] = [album_artist]
    audio["title"] = ["Kentucky"]
    audio.save()


def _read_album(path):
    from mutagen import File as MutagenFile

    audio = MutagenFile(str(path), easy=True)
    values = audio.get("album") if audio else None
    return values[0] if values else None


def _enhance(path, context):
    from core.metadata import enrichment, source as src

    with patch.object(enrichment, "get_config_manager", return_value=_cfg()), \
            patch.object(src, "get_config_manager", return_value=_cfg()):
        return enrichment.enhance_file_metadata(
            str(path),
            context,
            {"name": "Dawn McCarthy"},
            {"is_album": True, "album_name": UNKNOWN, "track_number": 13},
        )


def test_enhance_keeps_file_album_when_context_is_placeholder(tmp_path):
    flac = tmp_path / "13 - Kentucky.flac"
    _write_flac(flac, REAL)
    assert _enhance(flac, _context()) is True
    assert _read_album(flac) == REAL


def test_enhance_snapshot_does_not_leak_to_next_file_on_shared_context(tmp_path):
    context = _context()
    tagged = tmp_path / "tagged.flac"
    _write_flac(tagged, REAL)
    assert _enhance(tagged, context) is True
    assert _read_album(tagged) == REAL

    untagged = tmp_path / "untagged.flac"
    _write_flac(untagged, None)
    assert _enhance(untagged, context) is True
    # The first file's album must not ride the shared context into the
    # second file's tags; an album-less file falls back to the title.
    assert _read_album(untagged) == "Kentucky"


def test_enhance_does_not_stamp_a_various_artists_compilation_album(tmp_path):
    """The file-tag fallback has the same adoption guard as the pipeline:
    a compilation's album name credited to Various Artists is not this
    track's album, however real the name is."""
    flac = tmp_path / "track.flac"
    _write_flac(flac, "Now That's What I Call Music! 85",
                album_artist="Various Artists")
    assert _enhance(flac, _context()) is True
    assert _read_album(flac) == "Kentucky"


def test_distrusted_batch_never_stamps_the_file_album_tag():
    """#1567: in a distrusted batch the downloaded file is the stranger's
    bundle-mate — its album tag must not be stamped even when its album
    artist matches the (poisoned) batch artist param."""
    context = {
        "original_search_result": {
            "title": "How Far I'll Go",
            "artist": "Auli'i Cravalho",
            "artists": [{"name": "Auli'i Cravalho"}],
        },
        "album": {
            "id": "wishlist_album",
            "name": "Moana Soundtrack",
            "total_tracks": 14,
            "release_date": "2016",
            "artists": [{"name": "Lin-Manuel Miranda"}],
        },
        "track_info": {
            "_explicit_artist_context": {
                "id": "wishlist", "name": "Lin-Manuel Miranda", "genres": [],
            },
            "spotify_data": {
                "artists": [{"name": "Auli'i Cravalho"}],
            },
        },
        "source": "spotify",
        "_file_album_tag": "Moana Soundtrack",
        "_file_album_artist_tag": "Lin-Manuel Miranda",
    }
    md = _extract(context, {"id": "wishlist", "name": "Lin-Manuel Miranda", "genres": []})
    assert context["_batch_album_distrusted"] is True
    assert md["album"] == "How Far I'll Go"
