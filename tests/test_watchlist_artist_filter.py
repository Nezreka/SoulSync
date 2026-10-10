"""Regression tests for the watchlist scanner artist filter.

_should_include_track used to wishlist every track on any release associated
with the watched artist — tribute albums, soundtracks, compilations — without
ever checking the track's artist. Now the watched artist must be credited on
the track (unless include_other_artists is on).
"""
import sys
import types
from datetime import datetime

if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

if "core.matching_engine" not in sys.modules:
    matching_engine_mod = types.ModuleType("core.matching_engine")

    class _DummyMatchingEngine:
        def clean_title(self, title):
            return title

    matching_engine_mod.MusicMatchingEngine = _DummyMatchingEngine
    sys.modules["core.matching_engine"] = matching_engine_mod

from unittest.mock import MagicMock

from database.music_database import WatchlistArtist
from core.watchlist_scanner import WatchlistScanner


def _artist(name, **kwargs):
    a = WatchlistArtist(
        id=1, spotify_artist_id='x', artist_name=name,
        date_added=datetime.now(),
    )
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _scanner():
    return WatchlistScanner(spotify_client=MagicMock())


def _track(name, artists):
    return {'name': name, 'artists': [{'name': a} for a in artists]}


METALLICA = 'Metallica'
TRIBUTE_ALBUM = {'name': 'The Metallica Blacklist'}


def test_tribute_album_track_by_other_artist_excluded():
    s = _scanner()
    track = _track('Sad But True', ['Royal Blood'])
    assert s._should_include_track(track, TRIBUTE_ALBUM, _artist(METALLICA)) is False


def test_soundtrack_track_by_other_artist_excluded():
    s = _scanner()
    track = _track('Some Song', ['Mckenna Grace'])
    assert s._should_include_track(track, {'name': 'Nimrods Original Soundtrack'},
                                   _artist('Green Day')) is False


def test_own_track_included():
    s = _scanner()
    track = _track('Enter Sandman', ['Metallica'])
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True


def test_feat_credit_included():
    s = _scanner()
    track = _track('The Unforgiven', ['Metallica', 'Someone Else'])
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True


def test_monitored_artist_as_guest_included():
    # The watched artist appears as a guest on someone else's track.
    s = _scanner()
    track = _track('Collab Song', ['Some Band', 'Metallica'])
    assert s._should_include_track(track, {'name': 'Some Album'}, _artist(METALLICA)) is True


def test_tribute_band_name_does_not_match():
    # Substring must not count: "Metallica" is inside "Metallica Tribute Band".
    s = _scanner()
    track = _track('Enter Sandman', ['Metallica Tribute Band'])
    assert s._should_include_track(track, {'name': 'Tribute Night'}, _artist(METALLICA)) is False


def test_various_artists_compilation_excluded():
    s = _scanner()
    track = _track('Random Song', ['Various Artists'])
    assert s._should_include_track(track, {'name': 'Best of Metal'}, _artist(METALLICA)) is False


def test_no_artist_data_fails_open():
    # Thin providers: no credits at all must not drop the track.
    s = _scanner()
    track = {'name': 'Mystery Track'}
    assert s._should_include_track(track, {'name': 'Some Album'}, _artist(METALLICA)) is True


def test_album_artist_fallback():
    # Track carries no artists but the album does and matches.
    s = _scanner()
    track = {'name': 'Mystery Track'}
    album = {'name': 'Metallica', 'artists': [{'name': 'Metallica'}]}
    assert s._should_include_track(track, album, _artist(METALLICA)) is True


def test_album_artist_fallback_mismatch_excluded():
    s = _scanner()
    track = {'name': 'Mystery Track'}
    album = {'name': 'Tribute Album', 'artists': [{'name': 'Royal Blood'}]}
    assert s._should_include_track(track, album, _artist(METALLICA)) is False


def test_include_other_artists_flag_restores_old_behavior():
    s = _scanner()
    track = _track('Sad But True', ['Royal Blood'])
    artist = _artist(METALLICA, include_other_artists=True)
    assert s._should_include_track(track, TRIBUTE_ALBUM, artist) is True


def test_string_artist_entries():
    # Some upstreams pass plain strings instead of dicts.
    s = _scanner()
    track = {'name': 'Enter Sandman', 'artists': ['Metallica']}
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True
    track2 = {'name': 'Sad But True', 'artists': ['Royal Blood']}
    assert s._should_include_track(track2, TRIBUTE_ALBUM, _artist(METALLICA)) is False


def test_case_insensitive_match():
    s = _scanner()
    track = _track('Enter Sandman', ['metallica'])
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True


def test_curly_apostrophe_matches_straight():
    # MusicBrainz canonical "Guns N’ Roses" vs Spotify/Deezer "Guns N' Roses".
    s = _scanner()
    track = _track('Sweet Child O Mine', ["Guns N' Roses"])
    assert s._should_include_track(track, {'name': 'Appetite'}, _artist('Guns N’ Roses')) is True


def test_diacritics_stripped():
    s = _scanner()
    track = _track('Svefn-g-englar', ['Sigur Ros'])
    assert s._should_include_track(track, {'name': 'Ágætis byrjun'}, _artist('Sigur Rós')) is True
    track2 = _track('Human Behaviour', ['Bjork'])
    assert s._should_include_track(track2, {'name': 'Debut'}, _artist('Björk')) is True


def test_leading_the_ignored():
    s = _scanner()
    track = _track('Come Together', ['Beatles'])
    assert s._should_include_track(track, {'name': 'Abbey Road'}, _artist('The Beatles')) is True


def test_trailing_punctuation_ignored():
    s = _scanner()
    track = _track('Enter Sandman', ['Metallica.'])
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True


def test_bare_string_artists_shape():
    # Some shapes pass a single string instead of a list — must not iterate chars.
    s = _scanner()
    track = {'name': 'Enter Sandman', 'artists': 'Metallica'}
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True
    track2 = {'name': 'Sad But True', 'artists': 'Royal Blood'}
    assert s._should_include_track(track2, TRIBUTE_ALBUM, _artist(METALLICA)) is False


def test_dict_artists_shape():
    # A single artist object instead of a list — must not iterate its keys.
    s = _scanner()
    track = {'name': 'Enter Sandman', 'artists': {'name': 'Metallica'}}
    assert s._should_include_track(track, {'name': 'Metallica'}, _artist(METALLICA)) is True


def test_ligature_variants_match():
    # NFKD does not decompose ligatures — explicit map handles them.
    s = _scanner()
    track = _track('Comme des enfants', ['Cœur de pirate'])
    assert s._should_include_track(track, {'name': 'Coeur de pirate'}, _artist('Coeur de pirate')) is True
    track2 = _track('Feuer frei', ['Rammstein'])
    assert s._should_include_track(track2, {'name': 'Mutter'}, _artist('Rammstein')) is True


def test_internal_periods_ignored():
    s = _scanner()
    track = _track('Get It On', ['T. Rex'])
    assert s._should_include_track(track, {'name': 'Electric Warrior'}, _artist('T Rex')) is True
    track2 = _track('Cheerleader', ['St. Vincent'])
    assert s._should_include_track(track2, {'name': 'Masseduction'}, _artist('St Vincent')) is True
