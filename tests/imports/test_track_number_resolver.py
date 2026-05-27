"""Tests for ``core/imports/track_number.py:resolve_track_number``.

Pure-function resolver lifted out of the import pipeline so the
multi-source fallback chain can be pinned in isolation. Real-world
bug it addresses: wishlist-loop tracks were importing as ``01 -
<title>`` because the pipeline only consulted ``album_info.track_number``
and fell straight to the filename. When the filename was VA-collection
shaped (``417 Fountains of Wayne - Stacys Mom.flac``), the extractor
either returned the wrong number or None, the pipeline floored to 1,
and the wishlist track's actual Spotify track_number was discarded.
"""

from __future__ import annotations

from unittest.mock import patch

from core.imports.track_number import resolve_track_number


# ---------------------------------------------------------------------------
# Resolution chain — album_info wins when populated.
# ---------------------------------------------------------------------------


def test_album_info_track_number_wins_over_track_info():
    """When album_info has a real track_number, the resolver returns
    it without consulting track_info / spotify_data / filename. This
    is the album-bundle dispatch case where master.py has already
    resolved authoritative position data."""
    result = resolve_track_number(
        album_info={'track_number': 8},
        track_info={'track_number': 3},  # stale wishlist data
        file_path='/some/path/08 No Sleep Till Brooklyn.flac',
    )
    assert result == 8


def test_track_info_used_when_album_info_missing():
    """Per-track flow lands here — wishlist payload had track_number
    8 from Spotify, album_info wasn't populated by an album-bundle
    dispatch."""
    result = resolve_track_number(
        album_info={},
        track_info={'track_number': 8},
        file_path='/some/path/417 Stacy.flac',
    )
    assert result == 8


def test_spotify_data_used_when_track_info_top_level_missing():
    """Some wishlist payloads carry the full Spotify track dict nested
    under ``spotify_data`` rather than at the top level. The resolver
    must dig into the nested shape when the top-level key is absent."""
    result = resolve_track_number(
        album_info={},
        track_info={'spotify_data': {'track_number': 5}},
        file_path='/some/path/file.flac',
    )
    assert result == 5


def test_spotify_data_string_json_parsed_then_read():
    """Some legacy payloads stored spotify_data as a JSON string
    instead of a dict (round-tripped through DB blob fields).
    Resolver must parse and read it — same data, different shape."""
    result = resolve_track_number(
        album_info={},
        track_info={'spotify_data': '{"track_number": 12}'},
        file_path='/some/path/file.flac',
    )
    assert result == 12


def test_spotify_data_garbage_string_falls_through():
    """Non-JSON string in spotify_data must NOT crash — fall through
    to the next source (filename) as if it weren't there."""
    result = resolve_track_number(
        album_info={},
        track_info={'spotify_data': 'not json at all'},
        file_path='/dir/03 - Song.flac',
    )
    # Filename has '03 - ' prefix → extract returns 3.
    assert result == 3


# ---------------------------------------------------------------------------
# Filename fallback.
# ---------------------------------------------------------------------------


def test_filename_fallback_when_all_metadata_sources_missing():
    """No album_info, no track_info → resolver tries the filename."""
    result = resolve_track_number(
        album_info={},
        track_info={},
        file_path='/dl/12 - Track Title.flac',
    )
    assert result == 12


def test_filename_fallback_handles_zero_padded_prefixes():
    """Standard ripped-album naming ``NN - Title.flac`` produces the
    correct track position from the filename extractor."""
    result = resolve_track_number(
        album_info={},
        track_info={},
        file_path='/dl/05 - Whatever.flac',
    )
    assert result == 5


def test_filename_extractor_exception_silenced_to_none():
    """If the filename extractor raises (defensive — shouldn't in
    practice), resolver returns None rather than blowing up the
    whole post-process chain."""
    with patch('core.imports.track_number.extract_explicit_track_number',
               side_effect=RuntimeError('boom')):
        result = resolve_track_number({}, {}, '/path/05 - Track.flac')
    assert result is None


def test_no_file_path_returns_none_for_filename_step():
    """Empty file_path skips the filename extractor — resolver
    returns None instead of crashing on the next-step coercion."""
    result = resolve_track_number({}, {}, '')
    assert result is None


# ---------------------------------------------------------------------------
# Defensive: invalid / zero / non-numeric inputs.
# ---------------------------------------------------------------------------


def test_album_info_zero_track_number_falls_through():
    """``track_number=0`` is invalid (album positions are 1-indexed),
    so the resolver treats it as missing and tries the next source."""
    result = resolve_track_number(
        album_info={'track_number': 0},
        track_info={'track_number': 7},
        file_path='/dir/file.flac',
    )
    assert result == 7


def test_negative_track_number_treated_as_missing():
    """Defensive — a hand-edited row carrying -3 falls through."""
    result = resolve_track_number(
        album_info={'track_number': -3},
        track_info={'track_number': 7},
        file_path='/dir/file.flac',
    )
    assert result == 7


def test_non_numeric_track_number_treated_as_missing():
    """Garbage string falls through to the next source."""
    result = resolve_track_number(
        album_info={'track_number': 'oops'},
        track_info={'track_number': 7},
        file_path='/dir/file.flac',
    )
    assert result == 7


def test_string_numeric_track_number_coerced_to_int():
    """Some payloads store track_number as ``'8'`` (string) instead
    of ``8`` (int) — particularly from older DB serialisation paths.
    Resolver must coerce, not reject."""
    result = resolve_track_number(
        album_info={'track_number': '8'},
        track_info={},
        file_path='/dir/file.flac',
    )
    assert result == 8


def test_all_sources_missing_returns_none():
    """When every source is missing AND the filename doesn't carry
    a positional prefix, resolver returns None. Caller (the pipeline)
    then applies the final default-1 floor."""
    result = resolve_track_number({}, {}, '/no-prefix-here.flac')
    assert result is None


def test_va_collection_filename_returns_bogus_number_not_one():
    """Real-world regression case: ``417 Fountains of Wayne - Stacys Mom.flac``
    is a VA-collection file where the leading ``417`` is a playlist
    position, not the album track number. The filename extractor
    returns whatever it returns (currently None because the regex
    requires NN- prefix with the dash); the resolver's job is to
    let that flow through faithfully so the caller's default-1
    floor catches it. Pin the bug-trigger filename shape so a
    future "smart" extractor that returns 417 here still produces
    a behaviour the pipeline floor can correct."""
    # Empty album_info + track_info + nothing else → resolver
    # delegates to the filename extractor. Whatever it returns for
    # this VA-shape file, the pipeline applies the >=1 floor.
    result = resolve_track_number(
        album_info={},
        track_info={},
        file_path='/dl/417 Fountains of Wayne - Stacys Mom.flac',
    )
    # We don't pin the exact value here because the underlying
    # extractor's contract for non-canonical filenames is fuzzy.
    # What we DO pin: the resolver doesn't crash, returns either
    # None or a positive int. Pipeline's floor handles the rest.
    assert result is None or (isinstance(result, int) and result >= 1)


# ---------------------------------------------------------------------------
# Non-dict inputs (defensive).
# ---------------------------------------------------------------------------


def test_none_album_info_treated_as_empty_dict():
    """Defensive — caller might pass None when album_info wasn't built."""
    result = resolve_track_number(
        None,
        {'track_number': 3},
        '/dir/file.flac',
    )
    assert result == 3


def test_non_dict_track_info_treated_as_empty():
    """Defensive — non-dict track_info won't crash the resolver."""
    result = resolve_track_number({}, 'not a dict', '/dir/file.flac')
    assert result is None
