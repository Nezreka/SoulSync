"""Regression tests for the bulk "Add unwatched library artists to
watchlist" endpoint.

Discord report: bulk add silently skipped library artists that didn't
have an ID for the user's currently active metadata source. A
Spotify-primary user with library artists matched only against iTunes
or Deezer would see them counted as ``skipped_no_id`` and never make
it onto the watchlist — the user perceived this as "Library and
Watchlist not syncing correctly".

These tests pin the new behaviour: try the active source first, then
fall back to any other source ID the artist carries. Drop only when
the artist has zero source IDs.
"""

from core.watchlist.source_picker import pick_artist_id_for_watchlist


def _make_picker(active_source):
    """Tiny adapter so test bodies stay readable as ``pick(artist)``."""
    return lambda artist: pick_artist_id_for_watchlist(artist, active_source)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_active_source_id_takes_priority_when_present() -> None:
    """When the artist has the active source's ID, that one wins —
    other sources don't override it."""
    pick = _make_picker('spotify')
    artist = {
        'spotify_artist_id': 'sp-123',
        'itunes_artist_id': 'it-456',
        'deezer_id': 'dz-789',
    }
    assert pick(artist) == ('sp-123', 'spotify')


def test_falls_back_to_itunes_when_active_spotify_missing() -> None:
    """Spotify-primary user with an iTunes-only library artist must
    still get the artist on the watchlist instead of being silently
    skipped (the Discord-reported regression)."""
    pick = _make_picker('spotify')
    artist = {
        'itunes_artist_id': 'it-456',
        'deezer_id': 'dz-789',
    }
    assert pick(artist) == ('it-456', 'itunes')


def test_falls_back_to_deezer_when_active_and_itunes_missing() -> None:
    """Order matters — iTunes is preferred over Deezer when both
    fallbacks exist, matching the real-world catalogue coverage
    ranking the picker uses."""
    pick = _make_picker('spotify')
    artist = {
        'deezer_id': 'dz-789',
    }
    assert pick(artist) == ('dz-789', 'deezer')


def test_falls_back_to_discogs_as_last_resort() -> None:
    pick = _make_picker('spotify')
    artist = {
        'discogs_id': 'dg-999',
    }
    assert pick(artist) == ('dg-999', 'discogs')


def test_returns_none_when_artist_has_zero_source_ids() -> None:
    """Drop only when the artist has no source IDs at all — that's
    the only legitimate skip reason now."""
    pick = _make_picker('spotify')
    assert pick({'name': 'Some Artist'}) == (None, None)


def test_active_source_itunes_picks_itunes_first() -> None:
    """Active source ordering must work for non-Spotify primary too."""
    pick = _make_picker('itunes')
    artist = {
        'spotify_artist_id': 'sp-123',
        'itunes_artist_id': 'it-456',
    }
    assert pick(artist) == ('it-456', 'itunes')


def test_active_source_deezer_picks_deezer_first() -> None:
    pick = _make_picker('deezer')
    artist = {
        'spotify_artist_id': 'sp-123',
        'deezer_id': 'dz-789',
    }
    assert pick(artist) == ('dz-789', 'deezer')


def test_unrecognized_active_source_still_falls_back() -> None:
    """If active_source is something the picker doesn't know (e.g.
    'hydrabase'), still try every known source — better to add the
    artist with whatever ID exists than reject silently."""
    pick = _make_picker('hydrabase')
    artist = {
        'spotify_artist_id': 'sp-123',
    }
    # First fallback is Spotify per source_id_columns order
    assert pick(artist) == ('sp-123', 'spotify')


def test_empty_string_id_does_not_count_as_present() -> None:
    """SQL NULL surfaces as None; defensive check that empty string
    also falls through to the next source."""
    pick = _make_picker('spotify')
    artist = {
        'spotify_artist_id': '',
        'itunes_artist_id': 'it-456',
    }
    assert pick(artist) == ('it-456', 'itunes')


def test_numeric_id_is_coerced_to_string() -> None:
    """Some sources return numeric IDs from SQLite; the watchlist DB
    stores them as TEXT, so the picker must coerce to string before
    add_artist_to_watchlist sees them."""
    pick = _make_picker('itunes')
    artist = {'itunes_artist_id': 12345}
    artist_id, src = pick(artist)
    assert isinstance(artist_id, str)
    assert artist_id == '12345'
    assert src == 'itunes'
