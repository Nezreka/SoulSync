"""Regression: tracks whose playlist entry never carried an album
(paste-link Spotify playlists, discovery-pool rows, TXT imports) store the
literal 'Unknown Album' in the album slot. Post-processing used to treat
that placeholder as an identified album: detection "succeeded" with it,
smart grouping grouped under it, and the tagger stamped it over the
downloaded file's own correct album tag (CriminyJickets, Oct 2026).

The placeholder is the ABSENCE of an album. These tests pin that every
stage of the import chain treats it that way.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from core.imports.context import (
    build_import_album_info,
    detect_album_info_web,
    get_import_clean_album,
    has_real_album_name,
    is_placeholder_album,
    normalize_import_context,
)

UNKNOWN = "Unknown Album"
REAL = "What the Brothers Sang"


def _kentucky_context(album_value):
    """The CriminyJickets shape: a Discovery Shuffle track whose stored
    album is the placeholder in every slot it can occupy."""
    return normalize_import_context(
        {
            "source": "spotify",
            "artist": {"name": "Dawn McCarthy"},
            "album": {"name": album_value, "total_tracks": 0, "album_type": "album"},
            "track_info": {
                "name": "Kentucky",
                "artists": [{"name": "Dawn McCarthy"}],
            },
            "original_search_result": {
                "title": "Kentucky",
                "album": album_value,
                "clean_album": album_value,
                "clean_artist": "Dawn McCarthy",
            },
        }
    )


# ---------------------------------------------------------------------------
# the predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Unknown Album",
        "unknown album",
        "  Unknown Album  ",
        "Unknown  Album",  # inner whitespace collapses
        "[Unknown Album]",  # media-server bracketed form
        "[unknown album]",
    ],
)
def test_placeholder_spellings(value):
    assert is_placeholder_album(value)
    assert not has_real_album_name(value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_non_placeholder_values(value):
    assert not is_placeholder_album(value)
    assert not has_real_album_name(value)


def test_other_strings_are_real_album_names():
    # 'Unknown Artist' is an ARTIST placeholder; as an album value it is a
    # usable name (the placeholder set is album-specific on purpose).
    assert has_real_album_name(REAL)
    assert has_real_album_name("Unknown Artist")


# ---------------------------------------------------------------------------
# get_import_clean_album: the placeholder must not shadow real albums
# ---------------------------------------------------------------------------


def test_clean_album_placeholder_does_not_shadow_album_info():
    context = _kentucky_context(UNKNOWN)
    album = get_import_clean_album(context, album_info={"album_name": REAL}, default="")
    assert album == REAL


def test_clean_album_placeholder_does_not_shadow_album_context():
    context = _kentucky_context(UNKNOWN)
    context["album"] = {"name": REAL}
    assert get_import_clean_album(context, default="") == REAL


def test_clean_album_all_placeholder_returns_no_name():
    context = _kentucky_context(UNKNOWN)
    # Even the getter's own 'Unknown Album' default is the same absence —
    # a placeholder default must not reintroduce the placeholder as a name
    # (one caller builds its default from a placeholder album context).
    assert get_import_clean_album(context) == ""
    assert get_import_clean_album(context, default="") == ""


def test_clean_album_real_default_still_passes_through():
    context = _kentucky_context(UNKNOWN)
    assert get_import_clean_album(context, default="Fallback Album") == "Fallback Album"


# ---------------------------------------------------------------------------
# build_import_album_info: a placeholder alone is not album evidence
# ---------------------------------------------------------------------------


def test_build_album_info_placeholder_is_not_an_album():
    info = build_import_album_info(_kentucky_context(UNKNOWN))
    assert info["is_album"] is False
    assert info["album_name"] == ""


def test_build_album_info_real_album_still_detected():
    info = build_import_album_info(_kentucky_context(REAL))
    assert info["album_name"] == REAL


# ---------------------------------------------------------------------------
# detect_album_info_web: detection must not "identify" the placeholder
# ---------------------------------------------------------------------------


def test_detect_returns_none_for_placeholder_only_context():
    assert detect_album_info_web(_kentucky_context(UNKNOWN)) is None


def test_detect_still_identifies_a_real_album():
    detected = detect_album_info_web(_kentucky_context(REAL))
    assert detected is not None
    assert detected["is_album"] is True
    assert detected["album_name"] == REAL


def test_detect_uses_real_album_from_later_slot():
    # placeholder in the album context, real name only in the search result:
    # the old `or` chain stopped at the truthy placeholder.
    context = _kentucky_context(UNKNOWN)
    context["original_search_result"]["album"] = REAL
    context["original_search_result"]["clean_album"] = REAL
    detected = detect_album_info_web(context)
    assert detected is not None
    assert detected["album_name"] == REAL


# ---------------------------------------------------------------------------
# resolve_album_group: never group under the placeholder
# ---------------------------------------------------------------------------


def test_resolve_album_group_placeholder_only_returns_empty():
    from core.imports.album_naming import resolve_album_group

    result = resolve_album_group(
        {"name": "Dawn McCarthy"}, {"album_name": UNKNOWN}, original_album=UNKNOWN
    )
    assert result == ""


def test_resolve_album_group_uses_real_original_when_detected_is_placeholder():
    from core.imports.album_naming import resolve_album_group

    result = resolve_album_group(
        {"name": "Dawn McCarthy"}, {"album_name": UNKNOWN}, original_album=REAL
    )
    assert result == REAL


# ---------------------------------------------------------------------------
# _backfill_album_context: hydration fills the name like every other field
# ---------------------------------------------------------------------------


def test_backfill_replaces_placeholder_name():
    from core.downloads.track_metadata_backfill import _backfill_album_context

    ctx = {"name": UNKNOWN}
    _backfill_album_context(ctx, {"album": {"name": REAL, "release_date": "2013-01-01"}})
    assert ctx["name"] == REAL
    assert ctx["release_date"] == "2013-01-01"


def test_backfill_never_overwrites_a_real_name():
    from core.downloads.track_metadata_backfill import _backfill_album_context

    ctx = {"name": REAL}
    _backfill_album_context(ctx, {"album": {"name": "Some Other Album"}})
    assert ctx["name"] == REAL


# ---------------------------------------------------------------------------
# read_embedded_album: the file's own tag is the recovery source
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


def test_read_embedded_album_reads_the_tag(tmp_path):
    from core.imports.track_number import read_embedded_album

    flac = tmp_path / "13 - Kentucky.flac"
    _write_flac(flac, REAL)
    assert read_embedded_album(str(flac)) == REAL


def test_read_embedded_album_missing_or_untagged(tmp_path):
    from core.imports.track_number import read_embedded_album

    assert read_embedded_album(str(tmp_path / "nope.flac")) is None
    flac = tmp_path / "untagged.flac"
    _write_flac(flac, None)
    assert read_embedded_album(str(flac)) is None


# ---------------------------------------------------------------------------
# generate_download_queries: the placeholder must not poison the search
# ---------------------------------------------------------------------------


def test_download_queries_never_contain_the_placeholder():
    from core.matching_engine import MusicMatchingEngine

    engine = MusicMatchingEngine()
    track = SimpleNamespace(
        name="Kentucky", artists=["Dawn McCarthy"], album=UNKNOWN
    )
    queries = engine.generate_download_queries(track)
    assert queries, "expected fallback queries to still be generated"
    assert all("unknown album" not in q.lower() for q in queries)


def test_download_queries_still_use_a_real_album():
    from core.matching_engine import MusicMatchingEngine

    engine = MusicMatchingEngine()
    track = SimpleNamespace(name="Kentucky", artists=["Dawn McCarthy"], album=REAL)
    queries = engine.generate_download_queries(track)
    assert any(REAL.lower() in q.lower() for q in queries)


# ---------------------------------------------------------------------------
# The file-album adoption guard: whose album is it?
# ---------------------------------------------------------------------------

from core.imports.track_number import (  # noqa: E402
    embedded_album_belongs_to_track,
    read_embedded_album_artist,
)


def test_embedded_album_belongs_to_track():
    assert embedded_album_belongs_to_track(None, "Dawn McCarthy") is True
    assert embedded_album_belongs_to_track("", "Dawn McCarthy") is True
    assert embedded_album_belongs_to_track("Dawn McCarthy", "Dawn McCarthy") is True
    assert embedded_album_belongs_to_track("Dawn McCarthy & Bonnie Prince Billy", "Dawn McCarthy") is True
    assert embedded_album_belongs_to_track("Various Artists", "Dawn McCarthy") is False
    assert embedded_album_belongs_to_track("Various", "Dawn McCarthy") is False
    assert embedded_album_belongs_to_track("The Beatles", "Dawn McCarthy") is False
    assert embedded_album_belongs_to_track("The Beatles", "") is True


def test_read_embedded_album_artist(tmp_path):
    path = tmp_path / "va.flac"
    _write_flac(path, "X", album_artist="Various Artists")
    assert read_embedded_album_artist(str(path)) == "Various Artists"
    path2 = tmp_path / "plain.flac"
    _write_flac(path2, "X")
    assert read_embedded_album_artist(str(path2)) is None


# ---------------------------------------------------------------------------
# The library writer must never record the placeholder as the album
# ---------------------------------------------------------------------------

def test_library_entry_never_records_the_placeholder_album(tmp_path, monkeypatch):
    """record_soulsync_library_entry's fallback chain reads the raw
    context/original_search when album_info names nothing — both can hold
    the placeholder, and the row it writes is what the album-mismatch
    tool reads. The recorded album must fall through to the track title."""
    import core.imports.side_effects as se
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "music.db"))

    class _Cfg:
        def get_active_media_server(self):
            return "soulsync"

        def get(self, *a, **k):
            return k.get("default") if k else (a[1] if len(a) > 1 else None)

    monkeypatch.setattr(se, "_get_config_manager", lambda: _Cfg())
    monkeypatch.setattr(se, "get_database", lambda: db)

    audio = tmp_path / "kentucky.flac"
    audio.write_bytes(b"fLaC")
    context = {
        "source": "spotify",
        "artist": {"name": "Dawn McCarthy"},
        "album": {"name": "Unknown Album"},
        "track_info": {
            "name": "Kentucky",
            "artists": [{"name": "Dawn McCarthy"}],
            "track_number": 13,
            "duration_ms": 180000,
        },
        "original_search_result": {"title": "Kentucky", "album": "Unknown Album"},
        "_final_processed_path": str(audio),
    }
    se.record_soulsync_library_entry(context, {"name": "Dawn McCarthy"}, {"album_name": ""})

    with db._get_connection() as conn:
        rows = conn.execute("SELECT title FROM albums").fetchall()
    assert rows, "no album row written"
    assert all(r[0] != "Unknown Album" for r in rows)
    assert rows[0][0] == "Kentucky"


def test_backfill_leaves_a_missing_name_alone():
    """A missing name is a normal complete-context shape (the name
    arrives via the track object); only the placeholder is filled."""
    from core.downloads.track_metadata_backfill import _backfill_album_context

    ctx = {"release_date": "2013-01-01", "total_tracks": 12}
    _backfill_album_context(ctx, {"album": {"name": REAL}})
    assert "name" not in ctx


def test_bracketed_placeholder_is_lean_and_filled():
    from core.downloads.track_metadata_backfill import (
        _album_is_lean,
        _backfill_album_context,
    )

    ctx = {"name": "[Unknown Album]", "release_date": "2013-01-01", "total_tracks": 12}
    assert _album_is_lean(ctx) is True
    _backfill_album_context(ctx, {"album": {"name": REAL}})
    assert ctx["name"] == REAL


def test_embedded_album_belongs_requires_whole_credited_names():
    assert embedded_album_belongs_to_track("Steve Lacy", "Eve") is False
    assert embedded_album_belongs_to_track("Kanye West", "Ye") is False
    assert embedded_album_belongs_to_track("Bonnie 'Prince' Billy", "Dawn McCarthy") is False
    assert embedded_album_belongs_to_track("Dawn McCarthy", "Dawn McCarthy feat. Someone") is True


def test_embedded_album_name_is_real_rejects_junk_tags():
    from core.imports.track_number import embedded_album_name_is_real

    assert embedded_album_name_is_real("What the Brothers Sang") is True
    assert embedded_album_name_is_real("Unknown Album") is False
    assert embedded_album_name_is_real("Unknown") is False
    assert embedded_album_name_is_real("Untitled Album") is False
    assert embedded_album_name_is_real("") is False
    assert embedded_album_name_is_real(None) is False
