"""Tests for PlexClient's "All Libraries (combined)" mode.

When the user picks `ALL_LIBRARIES_SENTINEL` as their saved library
preference, every read method must dispatch through
`server.library.search(libtype=...)` instead of the single-section
`self.music_library.X` path. Pin both modes so future refactors can't
silently break either path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.plex_client import PlexClient, ALL_LIBRARIES_SENTINEL


def _make_client(*, all_libraries_mode: bool = False, music_library=None, server=None):
    client = PlexClient.__new__(PlexClient)
    client.server = server
    client.music_library = music_library
    client._all_libraries_mode = all_libraries_mode
    client._connection_attempted = server is not None
    client._is_connecting = False
    client._last_connection_check = 0
    client._connection_check_interval = 30
    return client


# ---------------------------------------------------------------------------
# Mode flag setup — sentinel handling
# ---------------------------------------------------------------------------


def test_set_music_library_by_name_sentinel_enables_all_libraries_mode(monkeypatch):
    """Pin: passing the sentinel as library_name flips the client into
    all-libraries mode and stores the sentinel as the saved preference."""
    server = MagicMock()
    client = _make_client(server=server, music_library=MagicMock())

    saved_pref = {}

    class _StubDB:
        def set_preference(self, key, value):
            saved_pref[key] = value

    import database.music_database as db_mod
    monkeypatch.setattr(db_mod, 'MusicDatabase', _StubDB)

    result = client.set_music_library_by_name(ALL_LIBRARIES_SENTINEL)

    assert result is True
    assert client._all_libraries_mode is True
    assert client.music_library is None
    assert saved_pref == {'plex_music_library': ALL_LIBRARIES_SENTINEL}


def test_set_music_library_by_name_specific_library_disables_all_libraries_mode(monkeypatch):
    """Pin: when user switches FROM all-libraries to a specific section,
    the mode flag clears."""
    server = MagicMock()
    section = MagicMock(type='artist', title='Music')
    server.library.sections.return_value = [section]
    client = _make_client(server=server, all_libraries_mode=True)

    class _StubDB:
        def set_preference(self, key, value):
            pass

    import database.music_database as db_mod
    monkeypatch.setattr(db_mod, 'MusicDatabase', _StubDB)

    result = client.set_music_library_by_name('Music')

    assert result is True
    assert client._all_libraries_mode is False
    assert client.music_library is section


def test_is_fully_configured_true_in_all_libraries_mode():
    """Pin: all-libraries mode counts as configured even though
    music_library is None — UI gating + dispatch site checks rely on
    this for the new mode to be functional."""
    server = MagicMock()
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    assert client.is_fully_configured() is True


# ---------------------------------------------------------------------------
# Read methods — single-library mode (regression guard)
# ---------------------------------------------------------------------------


def test_get_all_artists_uses_section_search_in_single_library_mode():
    """Single-library mode: get_all_artists hits self.music_library.searchArtists()
    and never calls server.library.search()."""
    server = MagicMock()
    section = MagicMock()
    section.searchArtists.return_value = [MagicMock(ratingKey=1), MagicMock(ratingKey=2)]
    client = _make_client(server=server, music_library=section, all_libraries_mode=False)

    result = client.get_all_artists()

    section.searchArtists.assert_called_once()
    server.library.search.assert_not_called()
    assert len(result) == 2


def test_get_all_album_ids_uses_section_albums_in_single_library_mode():
    server = MagicMock()
    section = MagicMock()
    section.albums.return_value = [MagicMock(ratingKey=10), MagicMock(ratingKey=20)]
    client = _make_client(server=server, music_library=section, all_libraries_mode=False)

    ids = client.get_all_album_ids()

    section.albums.assert_called_once()
    server.library.search.assert_not_called()
    assert ids == {'10', '20'}


# ---------------------------------------------------------------------------
# Read methods — all-libraries mode (the new path)
# ---------------------------------------------------------------------------


def test_get_all_artists_uses_server_search_in_all_libraries_mode():
    """All-libraries mode: get_all_artists dispatches through
    server.library.search(libtype='artist') — server-wide aggregation
    via Plex's API, no per-section iteration."""
    server = MagicMock()
    server.library.search.return_value = [MagicMock(ratingKey=1), MagicMock(ratingKey=2), MagicMock(ratingKey=3)]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    result = client.get_all_artists()

    server.library.search.assert_called_once_with(libtype='artist')
    assert len(result) == 3


def test_get_all_album_ids_uses_server_search_in_all_libraries_mode():
    server = MagicMock()
    server.library.search.return_value = [
        MagicMock(ratingKey=10), MagicMock(ratingKey=20), MagicMock(ratingKey=30),
    ]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    ids = client.get_all_album_ids()

    server.library.search.assert_called_once_with(libtype='album')
    assert ids == {'10', '20', '30'}


def test_get_library_stats_unions_across_sections_in_all_libraries_mode():
    """All-libraries stats sum totals across every music section via
    server-wide search calls. Artists / albums use distinct names so
    dedup doesn't collapse them — separate test pins the dedup path."""
    server = MagicMock()
    distinct_artists = [_fake_artist(f'Artist {i}', rating_key=str(i), leaf_count=i + 1) for i in range(5)]
    distinct_albums = [_fake_album(f'Album {i}', parent=f'Artist {i}', rating_key=str(100 + i), leaf_count=i + 1) for i in range(12)]
    distinct_tracks = [MagicMock(ratingKey=str(1000 + i)) for i in range(87)]
    server.library.search.side_effect = [
        distinct_artists,
        distinct_albums,
        distinct_tracks,
    ]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    stats = client.get_library_stats()

    assert stats == {'artists': 5, 'albums': 12, 'tracks': 87}
    assert server.library.search.call_count == 3


# ---------------------------------------------------------------------------
# Trigger / scanning — multi-section fan-out
# ---------------------------------------------------------------------------


def test_trigger_library_scan_fans_out_in_all_libraries_mode():
    """Pin: in all-libraries mode, trigger_library_scan calls update()
    on every music section, not just the named one."""
    section_a = MagicMock(type='artist', title='Music A')
    section_b = MagicMock(type='artist', title='Music B')
    section_other = MagicMock(type='movie', title='Movies')
    server = MagicMock()
    server.library.sections.return_value = [section_a, section_b, section_other]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    # Bypass ensure_connection's internal logic.
    client.ensure_connection = lambda: True

    result = client.trigger_library_scan('ignored-name')

    section_a.update.assert_called_once()
    section_b.update.assert_called_once()
    section_other.update.assert_not_called()
    assert result is True


def test_is_library_scanning_returns_true_when_any_section_refreshing():
    """Pin: in all-libraries mode, is_library_scanning returns True
    if any music section has refreshing=True."""
    section_a = MagicMock(type='artist', title='A', refreshing=False)
    section_b = MagicMock(type='artist', title='B', refreshing=True)  # actively scanning
    server = MagicMock()
    server.library.sections.return_value = [section_a, section_b]
    server.activities.return_value = []
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    client.ensure_connection = lambda: True

    assert client.is_library_scanning() is True


# ---------------------------------------------------------------------------
# get_available_music_libraries — synthetic sentinel entry
# ---------------------------------------------------------------------------


def test_available_music_libraries_prepends_sentinel_when_multiple_libraries():
    """Pin: when more than one music library exists, prepend "All
    Libraries (combined)" with the sentinel as its key. Single-library
    users don't get the extra option."""
    server = MagicMock()
    sec_a = MagicMock(type='artist', title='Music', key=1)
    sec_b = MagicMock(type='artist', title='Audiobook Library', key=2)
    server.library.sections.return_value = [sec_a, sec_b]
    client = _make_client(server=server, music_library=sec_a, all_libraries_mode=False)
    client.ensure_connection = lambda: True

    libs = client.get_available_music_libraries()

    # Synthetic entry first; ``value`` carries the sentinel so the
    # frontend has a canonical identifier to POST back to the
    # select-library endpoint.
    assert libs[0]['title'] == 'All Libraries (combined)'
    assert libs[0]['key'] == ALL_LIBRARIES_SENTINEL
    assert libs[0]['value'] == ALL_LIBRARIES_SENTINEL
    assert any(l['title'] == 'Music' and l['value'] == 'Music' for l in libs)
    assert any(l['title'] == 'Audiobook Library' and l['value'] == 'Audiobook Library' for l in libs)


def test_available_music_libraries_omits_sentinel_when_only_one_library():
    """Single-library users don't need the option — keep the dropdown
    clean."""
    server = MagicMock()
    sec_a = MagicMock(type='artist', title='Music', key=1)
    server.library.sections.return_value = [sec_a]
    client = _make_client(server=server, music_library=sec_a, all_libraries_mode=False)
    client.ensure_connection = lambda: True

    libs = client.get_available_music_libraries()

    assert all(l['key'] != ALL_LIBRARIES_SENTINEL for l in libs)
    assert len(libs) == 1


# ---------------------------------------------------------------------------
# Defensive — _can_query / _all_artists / etc gracefully handle no-config
# ---------------------------------------------------------------------------


def test_can_query_false_when_no_section_and_not_all_libraries():
    server = MagicMock()
    client = _make_client(server=server, music_library=None, all_libraries_mode=False)
    assert client._can_query() is False


def test_can_query_false_when_no_server():
    client = _make_client(server=None, music_library=None, all_libraries_mode=True)
    assert client._can_query() is False


def test_all_artists_returns_empty_when_not_configured():
    client = _make_client(server=None, all_libraries_mode=False)
    assert client._all_artists() == []


# ---------------------------------------------------------------------------
# Helper methods for downstream callers (DatabaseUpdateWorker, web_server)
# ---------------------------------------------------------------------------


def test_get_recently_added_albums_unions_across_sections_in_all_libraries_mode():
    """Pin: ``get_recently_added_albums`` iterates every music section
    in all-libraries mode and concats the recentlyAdded list. Pre-fix
    DatabaseUpdateWorker reached ``music_library.recentlyAdded()``
    directly which crashed when music_library is None."""
    section_a = MagicMock(type='artist', title='A')
    section_a.recentlyAdded.return_value = [MagicMock(ratingKey=1), MagicMock(ratingKey=2)]
    section_b = MagicMock(type='artist', title='B')
    section_b.recentlyAdded.return_value = [MagicMock(ratingKey=3)]
    server = MagicMock()
    server.library.sections.return_value = [section_a, section_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    client.ensure_connection = lambda: True

    result = client.get_recently_added_albums(maxresults=100)

    section_a.recentlyAdded.assert_called_once_with(libtype='album', maxresults=100)
    section_b.recentlyAdded.assert_called_once_with(libtype='album', maxresults=100)
    assert len(result) == 3


def test_get_recently_added_albums_uses_section_in_single_library_mode():
    section = MagicMock()
    section.recentlyAdded.return_value = [MagicMock(ratingKey=1)]
    server = MagicMock()
    client = _make_client(server=server, music_library=section, all_libraries_mode=False)
    client.ensure_connection = lambda: True

    result = client.get_recently_added_albums(maxresults=50)

    section.recentlyAdded.assert_called_once_with(libtype='album', maxresults=50)
    assert len(result) == 1


def test_get_recently_added_albums_libtype_none_skips_filter():
    """Pin: ``libtype=None`` skips the libtype kwarg entirely so
    callers can fetch mixed types when album-only returned nothing."""
    section = MagicMock()
    section.recentlyAdded.return_value = []
    server = MagicMock()
    client = _make_client(server=server, music_library=section, all_libraries_mode=False)
    client.ensure_connection = lambda: True

    client.get_recently_added_albums(maxresults=50, libtype=None)

    section.recentlyAdded.assert_called_once_with(maxresults=50)


def test_get_recently_updated_albums_uses_search_dispatch():
    """Pin: routes through ``_search_general`` so single-section vs
    all-libraries mode is handled by the helper."""
    server = MagicMock()
    server.library.search.return_value = [MagicMock(ratingKey=1), MagicMock(ratingKey=2)]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    client.ensure_connection = lambda: True

    result = client.get_recently_updated_albums(limit=200)

    server.library.search.assert_called_once_with(sort='updatedAt:desc', libtype='album', limit=200)
    assert len(result) == 2


def test_get_music_library_locations_unions_across_sections_in_all_libraries_mode():
    """Pin: ``get_music_library_locations`` returns every folder root
    configured across every music section. web_server.py uses this for
    file-path resolution — pre-fix it reached ``music_library.locations``
    which is None in all-libraries mode."""
    section_a = MagicMock(type='artist', title='A')
    section_a.locations = ['/data/userOne/Artists', '/data/userOne/More']
    section_b = MagicMock(type='artist', title='B')
    section_b.locations = ['/data/userTwo/Artists']
    server = MagicMock()
    server.library.sections.return_value = [section_a, section_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    client.ensure_connection = lambda: True

    locations = client.get_music_library_locations()

    assert '/data/userOne/Artists' in locations
    assert '/data/userOne/More' in locations
    assert '/data/userTwo/Artists' in locations
    assert len(locations) == 3


def test_get_music_library_locations_dedupes_overlapping_paths():
    """Pin: same root listed in multiple sections returns once."""
    section_a = MagicMock(type='artist', title='A')
    section_a.locations = ['/data/shared']
    section_b = MagicMock(type='artist', title='B')
    section_b.locations = ['/data/shared', '/data/userTwo']
    server = MagicMock()
    server.library.sections.return_value = [section_a, section_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    client.ensure_connection = lambda: True

    locations = client.get_music_library_locations()

    assert locations.count('/data/shared') == 1
    assert '/data/userTwo' in locations


# ---------------------------------------------------------------------------
# Cross-section dedup (only active in all-libraries mode)
# ---------------------------------------------------------------------------


def _fake_artist(name, rating_key, leaf_count):
    a = MagicMock()
    a.title = name
    a.ratingKey = rating_key
    a.leafCount = leaf_count
    return a


def _fake_album(title, parent, rating_key, leaf_count):
    a = MagicMock()
    a.title = title
    a.parentTitle = parent
    a.ratingKey = rating_key
    a.leafCount = leaf_count
    return a


def test_dedupe_artists_keeps_canonical_with_higher_track_count():
    """Pin: same-name artists across sections collapse to one — the
    one with the higher leafCount wins. Plex Home users with overlapping
    music tastes (both have Drake) shouldn't see "Drake" twice in
    SoulSync's library."""
    server = MagicMock()
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    drake_a = _fake_artist('Drake', rating_key='1', leaf_count=12)
    drake_b = _fake_artist('Drake', rating_key='2', leaf_count=87)  # canonical
    kendrick = _fake_artist('Kendrick Lamar', rating_key='3', leaf_count=40)

    deduped = client._dedupe_artists([drake_a, drake_b, kendrick])

    names = sorted(a.title for a in deduped)
    assert names == ['Drake', 'Kendrick Lamar']
    drake_picked = next(a for a in deduped if a.title == 'Drake')
    assert drake_picked.ratingKey == '2'  # higher leafCount wins


def test_dedupe_artists_case_insensitive_match():
    """Pin: dedup matches lowercased name so "Drake" + "drake" + "DRAKE"
    collapse together."""
    server = MagicMock()
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)
    a1 = _fake_artist('Drake', rating_key='1', leaf_count=10)
    a2 = _fake_artist('drake', rating_key='2', leaf_count=20)
    a3 = _fake_artist('DRAKE', rating_key='3', leaf_count=5)

    deduped = client._dedupe_artists([a1, a2, a3])

    assert len(deduped) == 1
    assert deduped[0].ratingKey == '2'  # canonical = highest count


def test_dedupe_artists_noop_in_single_library_mode():
    """Pin: dedup is bypassed entirely in single-library mode — the
    input list comes back unchanged. Single-section users get zero
    behavior change from the dedup logic."""
    server = MagicMock()
    section = MagicMock()
    client = _make_client(server=server, music_library=section, all_libraries_mode=False)

    drake_a = _fake_artist('Drake', rating_key='1', leaf_count=12)
    drake_b = _fake_artist('Drake', rating_key='2', leaf_count=87)

    result = client._dedupe_artists([drake_a, drake_b])

    assert len(result) == 2
    assert result == [drake_a, drake_b]


def test_dedupe_albums_groups_by_artist_and_title():
    """Pin: album dedup keys on (artist, title) so two artists with
    same album title (e.g. self-titled) stay separate."""
    server = MagicMock()
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    drake_self_a = _fake_album('Drake', parent='Drake', rating_key='1', leaf_count=15)
    drake_self_b = _fake_album('Drake', parent='Drake', rating_key='2', leaf_count=15)
    weeknd_drake = _fake_album('Drake', parent='The Weeknd', rating_key='3', leaf_count=8)  # different artist

    deduped = client._dedupe_albums([drake_self_a, drake_self_b, weeknd_drake])

    assert len(deduped) == 2
    artists = sorted(a.parentTitle for a in deduped)
    assert artists == ['Drake', 'The Weeknd']


def test_get_all_artists_dedupes_in_all_libraries_mode():
    """Pin: ``get_all_artists`` (the public listing) returns deduped
    list in all-libraries mode."""
    server = MagicMock()
    drake_a = _fake_artist('Drake', rating_key='1', leaf_count=12)
    drake_b = _fake_artist('Drake', rating_key='2', leaf_count=87)
    server.library.search.return_value = [drake_a, drake_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    result = client.get_all_artists()

    assert len(result) == 1
    assert result[0].ratingKey == '2'


def test_get_all_artist_ids_does_NOT_dedupe_critical_for_removal_detection():
    """CRITICAL pin: ``get_all_artist_ids`` returns the RAW ratingKey
    set, even in all-libraries mode. Removal detection compares this
    set against DB-linked ratingKeys to decide what's been removed —
    deduping here would falsely report non-canonical ratingKeys as
    "removed" and prune library tracks pointing at them."""
    server = MagicMock()
    drake_a = _fake_artist('Drake', rating_key='1', leaf_count=12)
    drake_b = _fake_artist('Drake', rating_key='2', leaf_count=87)
    server.library.search.return_value = [drake_a, drake_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    ids = client.get_all_artist_ids()

    # Both ratingKeys returned, NOT deduped.
    assert ids == {'1', '2'}


def test_get_all_album_ids_does_NOT_dedupe_critical_for_removal_detection():
    """Same critical pin for albums."""
    server = MagicMock()
    alb_a = _fake_album('Take Care', parent='Drake', rating_key='10', leaf_count=15)
    alb_b = _fake_album('Take Care', parent='Drake', rating_key='20', leaf_count=15)
    server.library.search.return_value = [alb_a, alb_b]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    ids = client.get_all_album_ids()

    assert ids == {'10', '20'}


def test_get_library_stats_uses_deduped_counts_for_artists_albums():
    """Pin: stats reflect the deduped counts users see in the library
    list. Tracks stay raw — same track in two sections is two files."""
    server = MagicMock()
    drake_a = _fake_artist('Drake', rating_key='1', leaf_count=12)
    drake_b = _fake_artist('Drake', rating_key='2', leaf_count=87)
    kendrick = _fake_artist('Kendrick Lamar', rating_key='3', leaf_count=40)
    alb_a = _fake_album('Take Care', parent='Drake', rating_key='10', leaf_count=15)
    alb_b = _fake_album('Take Care', parent='Drake', rating_key='20', leaf_count=15)
    track1 = MagicMock(ratingKey=100)
    track2 = MagicMock(ratingKey=101)
    track3 = MagicMock(ratingKey=102)
    server.library.search.side_effect = [
        [drake_a, drake_b, kendrick],   # artist call → 3 raw → 2 deduped
        [alb_a, alb_b],                 # album call → 2 raw → 1 deduped
        [track1, track2, track3],       # track call → 3 raw, no dedup
    ]
    client = _make_client(server=server, all_libraries_mode=True, music_library=None)

    stats = client.get_library_stats()

    assert stats == {'artists': 2, 'albums': 1, 'tracks': 3}
