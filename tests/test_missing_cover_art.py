import sqlite3
import sys
import types
from types import SimpleNamespace

# Stub optional Spotify dependency so metadata_service can import in tests.
if 'spotipy' not in sys.modules:
    spotipy = types.ModuleType('spotipy')
    oauth2 = types.ModuleType('spotipy.oauth2')

    class _DummySpotify:
        pass

    class _DummyOAuth:
        pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules['spotipy'] = spotipy
    sys.modules['spotipy.oauth2'] = oauth2

if 'config.settings' not in sys.modules:
    config_mod = types.ModuleType('config')
    settings_mod = types.ModuleType('config.settings')

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

    settings_mod.config_manager = _DummyConfigManager()
    config_mod.settings = settings_mod
    sys.modules['config'] = config_mod
    sys.modules['config.settings'] = settings_mod

from core.repair_jobs import missing_cover_art as mca


class _FakeClient:
    def __init__(self, album_image=None, search_image=None):
        self.album_image = album_image
        self.search_image = search_image
        self.get_album_calls = []
        self.search_calls = []

    def get_album(self, album_id, include_tracks=False):
        self.get_album_calls.append((album_id, include_tracks))
        if self.album_image is None:
            return None
        if isinstance(self.album_image, str):
            return {'images': [{'url': self.album_image}]}
        return self.album_image

    def search_albums(self, query, limit=1):
        self.search_calls.append((query, limit))
        if self.search_image is None:
            return []
        return [SimpleNamespace(id='search-album', image_url=self.search_image)]


def _make_db(album_row):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE artists (
            id INTEGER PRIMARY KEY,
            name TEXT,
            thumb_url TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            thumb_url TEXT,
            spotify_album_id TEXT,
            itunes_album_id TEXT,
            deezer_id TEXT,
            discogs_id TEXT,
            soul_id TEXT
        )
        """
    )
    cursor.execute(
        "INSERT INTO artists (id, name, thumb_url) VALUES (?, ?, ?)",
        (1, 'Artist', 'https://artist/thumb'),
    )
    cursor.execute(
        """
        INSERT INTO albums
            (id, title, artist_id, thumb_url, spotify_album_id, itunes_album_id, deezer_id, discogs_id, soul_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        album_row,
    )
    conn.commit()
    return conn


def _make_context(conn, prefer_source=None):
    job_settings = {}
    if prefer_source is not None:
        job_settings['prefer_source'] = prefer_source
    settings = {'repair.jobs.missing_cover_art.settings': job_settings}
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(get=lambda key, default=None: settings.get(key, default)),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        update_progress=lambda *args, **kwargs: None,
        report_progress=lambda *args, **kwargs: None,
        create_finding=lambda **kwargs: findings.append(kwargs),
        findings=findings,
    )


def test_missing_cover_art_prefers_explicit_source_over_primary(monkeypatch):
    conn = _make_db((1, 'Album', 1, '', 'sp-album', 'it-album', 'dz-album', 'dg-album', 'hy-album'))
    context = _make_context(conn, prefer_source='spotify')

    deezer_client = _FakeClient(album_image='https://img/deezer-direct')
    spotify_client = _FakeClient(album_image='https://img/spotify-direct')

    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(
        mca,
        'get_client_for_source',
        lambda source: {'deezer': deezer_client, 'spotify': spotify_client}.get(source),
    )

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    assert spotify_client.get_album_calls == [('sp-album', False)]
    assert deezer_client.get_album_calls == []
    assert context.findings[0]['details']['found_artwork_url'] == 'https://img/spotify-direct'


def test_missing_cover_art_uses_primary_when_prefer_unset(monkeypatch):
    conn = _make_db((1, 'Album', 1, '', None, None, None, None, None))
    context = _make_context(conn)

    discogs_client = _FakeClient(search_image='https://img/discogs-search')
    spotify_client = _FakeClient(search_image='https://img/spotify-search')
    itunes_client = _FakeClient(search_image='https://img/itunes-search')

    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'discogs')
    monkeypatch.setattr(
        mca,
        'get_client_for_source',
        lambda source: {'discogs': discogs_client, 'spotify': spotify_client, 'itunes': itunes_client}.get(source),
    )

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    assert discogs_client.search_calls == [('Artist Album', 1)]
    assert spotify_client.search_calls == []
    assert itunes_client.search_calls == []
    assert context.findings[0]['details']['found_artwork_url'] == 'https://img/discogs-search'
