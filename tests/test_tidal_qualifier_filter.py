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


def _make_track(name: str, album_name: str = '', version: str = ''):
    """Build a minimal duck-typed track object matching what the Tidal
    SDK returns: a `name` attribute, a `version` attribute (remix/live/
    edit qualifier — separate from the title), and an `album` attribute
    with its own `name`."""
    track = MagicMock()
    track.name = name
    track.version = version
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


# ──────────────────────────────────────────────────────────────────────
# track.version — Tidal stores the remix/live/edit qualifier in a
# dedicated `version` attribute, NOT in track.name. Real-world: the
# exact recording is present in the search results but was discarded
# because neither the qualifier filter nor the matcher looked at
# `version`. Cases below are real Tidal tracks (id in comments).
# ──────────────────────────────────────────────────────────────────────

def test_qualifier_in_version_field_passes():
    # Tidal 124341 — name="Emerge", version="Junkie XL Remix"
    track = _make_track('Emerge', album_name='#1', version='Junkie XL Remix')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['remix']) is True


def test_qualifier_in_version_field_radio_version():
    # Tidal 122127 — name="Black Horse And The Cherry Tree",
    # version="Radio Version"
    track = _make_track('Black Horse And The Cherry Tree', version='Radio Version')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['version']) is True


def test_qualifier_missing_from_name_version_and_album_fails():
    # The studio cut: nothing carries "remix" anywhere — must still reject.
    track = _make_track('Emerge', album_name='#1', version='')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['remix']) is False


def test_empty_version_does_not_pollute_haystack():
    # Regression guard: a falsy version must not let unrelated qualifiers
    # match (e.g. via a stringified mock).
    track = _make_track('Emerge', album_name='#1', version='')
    assert TidalDownloadClient._track_matches_qualifiers(track, ['live']) is False


# ──────────────────────────────────────────────────────────────────────
# _tidal_to_track_result — folds `version` into the candidate title so
# the matcher (which scores title only) can see the qualifier.
# ──────────────────────────────────────────────────────────────────────

def _make_full_track(name, artist, version='', album='', duration=240,
                     isrc='X', tid=1):
    track = MagicMock()
    track.id = tid
    track.name = name
    track.version = version
    track.artist = MagicMock()
    track.artist.name = artist
    track.artist.id = 99
    track.album = MagicMock()
    track.album.name = album
    track.duration = duration
    track.track_num = 1
    track.isrc = isrc
    track.bpm = 0
    track.copyright = ''
    return track


_QINFO = {'codec': 'flac', 'bitrate': 1411}


def test_version_folded_into_title():
    track = _make_full_track('Emerge', 'Fischerspooner', version='Junkie XL Remix')
    result = TidalDownloadClient._tidal_to_track_result(None, track, _QINFO)
    assert result.title == 'Emerge (Junkie XL Remix)'


def test_no_version_leaves_title_unchanged():
    track = _make_full_track('Emerge', 'Fischerspooner', version='')
    result = TidalDownloadClient._tidal_to_track_result(None, track, _QINFO)
    assert result.title == 'Emerge'


def test_version_already_in_name_not_duplicated():
    # Some Tidal tracks redundantly carry the version in the name too;
    # don't produce "Song (Remix) (Remix)".
    track = _make_full_track('Song (Remix)', 'Artist', version='Remix')
    result = TidalDownloadClient._tidal_to_track_result(None, track, _QINFO)
    assert result.title == 'Song (Remix)'


# ──────────────────────────────────────────────────────────────────────
# End-to-end: folding version in is what lets MusicMatchingEngine accept
# the exact recording. Before the fix the bare name scores below the
# 0.60 gate; after it, the full title matches. Real repro cases.
# ──────────────────────────────────────────────────────────────────────

import pytest
from core.matching_engine import MusicMatchingEngine


# (wanted title, artist, tidal name, tidal version)
_REPROS = [
    ('Emerge (Junkie XL Remix)', 'Fischerspooner', 'Emerge', 'Junkie XL Remix'),
    ('We Are The People (Shazam Remix)', 'Empire Of The Sun', 'We Are The People', 'Shazam Remix'),
    ('Black Horse And The Cherry Tree (Radio Version)', 'KT Tunstall', 'Black Horse And The Cherry Tree', 'Radio Version'),
    ('All Night (Live @ Pukkelpop)', 'Parov Stelar', 'All Night', 'Live @ Pukkelpop'),
    ('Fleur de Lille (Extended)', 'Parov Stelar', 'Fleur de Lille', 'Extended'),
]


@pytest.mark.parametrize('wanted,artist,tidal_name,tidal_version', _REPROS)
def test_version_fold_lets_matcher_accept(wanted, artist, tidal_name, tidal_version):
    me = MusicMatchingEngine()
    track = _make_full_track(tidal_name, artist, version=tidal_version)
    folded = TidalDownloadClient._tidal_to_track_result(None, track, _QINFO).title

    # Before the fix the candidate title was the bare Tidal name → rejected.
    bare_conf, _ = me.score_track_match(wanted, [artist], 0, tidal_name, [artist], 0)
    # After the fix it's "Name (Version)" → accepted.
    folded_conf, _ = me.score_track_match(wanted, [artist], 0, folded, [artist], 0)

    assert folded_conf >= 0.60, f'{wanted!r}: folded {folded_conf:.2f} should clear 0.60'
    assert folded_conf > bare_conf, (
        f'{wanted!r}: folding version in ({folded_conf:.2f}) must beat bare name '
        f'({bare_conf:.2f})')
