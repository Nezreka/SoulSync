import sqlite3
import sys
import types
from types import SimpleNamespace

# Stub optional Spotify dependency so the metadata package can import in tests.
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

if 'core.settings' not in sys.modules:
    config_mod = types.ModuleType('config')
    settings_mod = types.ModuleType('core.settings')

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_mod.settings = settings_mod
    sys.modules['config'] = config_mod
    sys.modules['core.settings'] = settings_mod

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
    """A Library-v2 catalogue with one file, because that is what the scan reads.

    This fixture used to hand-roll ``artists``/``albums``/``tracks``. The job
    stopped reading those when its native scan replaced the legacy projection,
    and an empty ``lib2_track_files`` yields no subjects at all — the tests then
    asserted against a scan that had walked nothing.
    """
    from core.library2.schema import ensure_library_v2_schema

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists (id, name, sort_name) VALUES (1, 'Artist', 'Artist')")
    conn.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Album')")
    conn.execute(
        "INSERT INTO lib2_tracks (id, album_id, title, isrc, external_ids) "
        "VALUES (1, 1, 'Track Title', '', ?)",
        ('{"spotify": "sp-1", "deezer": "dz-1"}',),
    )
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
        "VALUES (1, '/music/Artist/Album/01 - Track Title.flac', 'flac', 1)")
    conn.commit()
    return conn


class _Config:
    """``features.library_v2`` off means ``active_file_subjects`` returns [] —
    the switch every native scan is behind."""

    def get(self, key, default=None):
        return True if key == 'features.library_v2' else default


def _make_context(conn):
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=_Config(),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        update_progress=lambda *args, **kwargs: None,
        report_progress=lambda *args, **kwargs: None,
        sleep_or_stop=lambda seconds: False,
        mb_client=_FakeMBClient(),
        create_finding=lambda **kwargs: (findings.append(kwargs) or True),
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
