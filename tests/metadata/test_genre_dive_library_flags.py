"""The genre dive asks Library v2 what the user already owns (docs §50.4.4.16).

Two of the deep dive's six steps are catalogue questions rather than cache ones:
which of the discovered albums are already owned, and which discovered artists
have a library page to link to. Both were reading the legacy tables, and both
carried the same defect — ``LOWER()`` in SQLite is ASCII-only, so a non-Latin
name never matched and the answer came back "you do not own this" / "no page".

There were no tests over this path at all; these are the first, which is also
why they lean on a real schema rather than a stubbed cursor. The failure mode
being guarded is precisely the one a stub cannot see: a join or column that does
not exist, silently answering "nothing owned" for the whole page.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

# Same import guards as the other cache tests: importing core.metadata.cache
# must not drag in spotipy or the real config.
if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2
if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyCM:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyCM()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

from core.metadata.cache import MetadataCache  # noqa: E402


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from core.library2.importer import normalize_name
    from database.music_database import MusicDatabase

    # A real database: the cache entities and the lib2 catalogue live in the
    # same file, and the dive reads both in one connection.
    database = MusicDatabase(str(tmp_path / "music.db"))
    conn = database._get_connection()

    def _artist(name, legacy_id):
        return conn.execute(
            "INSERT INTO lib2_artists(name, name_key, sort_name, legacy_artist_id) "
            "VALUES(?,?,?,?)",
            (name, normalize_name(name), name, legacy_id)).lastrowid

    def _album(artist_row, title, origin='library'):
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, origin) VALUES(?,?,?)",
            (artist_row, title, origin)).lastrowid
        if origin == 'library':
            track_id = conn.execute(
                "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (album_id, title)).lastrowid
            conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                         (track_id, f'/music/{title}.flac'))

    owned = _artist('Björk', 4242)
    _album(owned, 'Homogenic')
    _album(owned, 'Vespertine', origin='discography')
    _artist('Native Only', None)

    # The discovered set the dive works from — cached provider entities.
    for name in ('Björk', 'Native Only', 'Unknown Artist'):
        conn.execute(
            "INSERT INTO metadata_cache_entities(entity_type, entity_id, source, "
            "name, genres, followers, raw_json) "
            "VALUES('artist', ?, 'deezer', ?, ?, 10, '{}')",
            (f"e-{name}", name, json.dumps(['art pop'])))
    for title, artist in (('Homogenic', 'Björk'), ('Vespertine', 'Björk'),
                          ('Phantom', 'Unknown Artist')):
        conn.execute(
            "INSERT INTO metadata_cache_entities(entity_type, entity_id, source, "
            "name, artist_name, genres, raw_json) "
            "VALUES('album', ?, 'deezer', ?, ?, ?, '{}')",
            (f"a-{title}", title, artist, json.dumps(['art pop'])))
    conn.commit()
    conn.close()

    instance = MetadataCache()
    monkeypatch.setattr(instance, "_get_db", lambda: database)
    return instance


def _dive(cache):
    return cache.get_genre_deep_dive('art pop', source='deezer')


def _album_flag(result, title):
    return next(a['in_library'] for a in result['albums'] if a['name'] == title)


def _artist_link(result, name):
    return next(a['library_id'] for a in result['artists'] if a['name'] == name)


class TestOwnership:
    def test_an_owned_release_is_flagged_despite_a_non_ascii_artist(self, cache):
        """SQLite's ``LOWER()`` left ``Björk`` capitalized on the catalogue side,
        so this album came back unowned and offered the user a download."""
        assert _album_flag(_dive(cache), 'Homogenic') is True

    def test_a_provider_only_release_is_not_owned(self, cache):
        """``origin='discography'`` is a release we know of, not one we have."""
        assert _album_flag(_dive(cache), 'Vespertine') is False

    def test_an_album_the_catalogue_never_saw_is_not_owned(self, cache):
        assert _album_flag(_dive(cache), 'Phantom') is False


class TestTheArtistLink:
    def test_a_linked_artist_gets_its_legacy_id(self, cache):
        """The id goes to the artist-detail route, which resolves a bare numeric
        id against the legacy table by contract — so it must be that id."""
        assert _artist_link(_dive(cache), 'Björk') == 4242

    def test_a_native_artist_gets_no_link_rather_than_a_dead_one(self, cache):
        assert _artist_link(_dive(cache), 'Native Only') is None

    def test_an_unknown_artist_gets_no_link(self, cache):
        assert _artist_link(_dive(cache), 'Unknown Artist') is None
