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

from core.repair_jobs import metadata_gap_filler as mgf


class _FakeTrackClient:
    def __init__(self, source_name, isrc=None):
        self.source_name = source_name
        self.isrc = isrc
        self.calls = []

    def get_track_details(self, track_id):
        self.calls.append(track_id)
        if self.isrc is None:
            return None
        return {
            'id': track_id,
            'external_ids': {'isrc': self.isrc},
        }


class _FakeMBClient:
    def __init__(self):
        self.calls = []

    def search_recording(self, title, artist_name=None, limit=1):
        self.calls.append((title, artist_name, limit))
        return [{'id': 'mb-recording'}]


def _make_db():
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
            thumb_url TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            album_id INTEGER,
            spotify_track_id TEXT,
            itunes_track_id TEXT,
            deezer_track_id TEXT,
            isrc TEXT,
            musicbrainz_recording_id TEXT
        )
        """
    )
    cursor.execute("INSERT INTO artists (id, name, thumb_url) VALUES (1, 'Artist', '')")
    cursor.execute("INSERT INTO albums (id, title, thumb_url) VALUES (1, 'Album', '')")
    cursor.execute(
        """
        INSERT INTO tracks
            (id, title, artist_id, album_id, spotify_track_id, itunes_track_id, deezer_track_id, isrc, musicbrainz_recording_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 'Track Title', 1, 1, 'sp-1', None, 'dz-1', '', ''),
    )
    conn.commit()
    return conn


def _make_context(conn):
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(get=lambda key, default=None: default),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        update_progress=lambda *args, **kwargs: None,
        report_progress=lambda *args, **kwargs: None,
        sleep_or_stop=lambda seconds: False,
        mb_client=_FakeMBClient(),
        create_finding=lambda **kwargs: findings.append(kwargs),
        findings=findings,
    )


def test_metadata_gap_filler_prefers_primary_track_source(monkeypatch):
    conn = _make_db()
    context = _make_context(conn)

    spotify_client = _FakeTrackClient('spotify', isrc='SP-ISRC')
    deezer_client = _FakeTrackClient('deezer', isrc='DZ-ISRC')
    itunes_client = _FakeTrackClient('itunes', isrc=None)

    monkeypatch.setattr(mgf, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(
        mgf,
        'get_client_for_source',
        lambda source: {'spotify': spotify_client, 'deezer': deezer_client, 'itunes': itunes_client}.get(source),
    )

    result = mgf.MetadataGapFillerJob().scan(context)

    assert result.findings_created == 1
    assert deezer_client.calls == ['dz-1']
    assert spotify_client.calls == []
    assert context.findings[0]['details']['found_fields']['isrc'] == 'DZ-ISRC'
    assert context.findings[0]['details']['resolved_source'] == 'deezer'
    assert context.findings[0]['details']['resolved_track_id'] == 'dz-1'


def test_metadata_gap_filler_skips_track_detail_lookup_when_isrc_disabled(monkeypatch):
    conn = _make_db()
    context = _make_context(conn)

    spotify_client = _FakeTrackClient('spotify', isrc='SP-ISRC')
    deezer_client = _FakeTrackClient('deezer', isrc='DZ-ISRC')

    monkeypatch.setattr(mgf, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(
        mgf,
        'get_client_for_source',
        lambda source: {'spotify': spotify_client, 'deezer': deezer_client}.get(source),
    )

    job = mgf.MetadataGapFillerJob()
    monkeypatch.setattr(job, '_get_settings', lambda context: {'fill_isrc': False, 'fill_musicbrainz_id': True})

    result = job.scan(context)

    assert result.findings_created == 1
    assert spotify_client.calls == []
    assert deezer_client.calls == []
    assert context.findings[0]['details']['found_fields']['musicbrainz_recording_id'] == 'mb-recording'
