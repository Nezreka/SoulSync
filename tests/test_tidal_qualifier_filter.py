"""Tests for Tidal qualifier filtering across primary + fallback search.

Issue #589 — when a download query carries a version qualifier ("live",
"unplugged", "acoustic", etc), the qualifier filter must apply to BOTH
the primary search AND fallback variants. Previously it only fired on
fallbacks, so a primary search for "Shy Away (MTV Unplugged Live)" that
happened to surface the studio cut first would accept the wrong file
and only get caught by AcoustID downstream.

Also covers the album-context extension: for concert / unplugged
releases the live signal lives in the album title, not the track
title. The filter inspects both ``track.name`` AND ``track.album.name``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.tidal_download_client import TidalDownloadClient


def _make_track(name: str, album_name: str = ''):
    """Build a minimal duck-typed track object matching what the Tidal
    SDK returns: a `name` attribute and an `album` attribute with its
    own `name`."""
    track = MagicMock()
    track.name = name
    track.album = MagicMock()
    track.album.name = album_name
    return track


# ──────────────────────────────────────────────────────────────────────
# _track_name_contains_qualifiers — legacy track-only behavior preserved
# ──────────────────────────────────────────────────────────────────────

def test_legacy_helper_passes_when_track_name_has_qualifier():
    assert TidalDownloadClient._track_name_contains_qualifiers(
        'Shy Away (MTV Unplugged Live)', ['live']
    ) is True


def test_legacy_helper_fails_when_track_name_lacks_qualifier():
    assert TidalDownloadClient._track_name_contains_qualifiers(
        'Shy Away', ['live']
    ) is False


def test_legacy_helper_passes_when_no_qualifiers_required():
    assert TidalDownloadClient._track_name_contains_qualifiers(
        'Anything', []
    ) is True


# ──────────────────────────────────────────────────────────────────────
# _track_matches_qualifiers — new helper inspects track + album
# ──────────────────────────────────────────────────────────────────────

def test_qualifier_in_track_name_alone_passes():
    track = _make_track('Shy Away (Live)', 'DAMN.')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['live']) is True


def test_qualifier_in_album_name_alone_passes():
    # MTV Unplugged scenario — track titled "Shy Away" but album
    # carries the live context. Pre-fix this returned False because
    # only track.name was checked.
    track = _make_track('Shy Away', 'MTV Unplugged Live')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['live']) is True


def test_qualifier_missing_from_both_fails():
    # User asked for live, Tidal returned the studio cut on a studio
    # album. Must reject so the search keeps looking.
    track = _make_track('Shy Away', 'Trench')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['live']) is False


def test_unplugged_qualifier_in_album_name():
    track = _make_track('Only If For A Night', 'MTV Unplugged')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['unplugged']) is True


def test_multiple_qualifiers_all_required():
    # Both "live" AND "acoustic" must be present somewhere
    track = _make_track('Hello', 'Live Acoustic Sessions')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['live', 'acoustic']) is True
    track2 = _make_track('Hello', 'Live Sessions')  # missing acoustic
    assert TidalDownloadClient._track_matches_qualifiers(track2, ['live', 'acoustic']) is False


def test_no_qualifiers_required_always_passes():
    track = _make_track('Anything', 'Anything')
    assert TidalDownloadClient._track_matches_qualifiers(track, []) is True


def test_track_with_no_album_attribute():
    # Defensive — duck-typed tracks may not all have album. Use a
    # plain object instead of MagicMock so missing .album is real.
    class BareTrack:
        name = 'Live Track'
        album = None
    assert TidalDownloadClient._track_matches_qualifiers(BareTrack(), ['live']) is True
    assert TidalDownloadClient._track_matches_qualifiers(BareTrack(), ['unplugged']) is False


def test_track_with_empty_name_and_album():
    class BareTrack:
        name = ''
        album = None
    assert TidalDownloadClient._track_matches_qualifiers(BareTrack(), ['live']) is False


def test_word_boundary_avoids_false_match_on_substring():
    # "session" should NOT match "obsession"
    track = _make_track('Obsession', 'Pop Hits')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['session']) is False


def test_extract_qualifiers_picks_up_live_unplugged():
    quals = TidalDownloadClient._extract_qualifiers('Shy Away (MTV Unplugged Live)')
    assert 'live' in quals
    assert 'unplugged' in quals
