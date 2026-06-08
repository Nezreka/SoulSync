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

        def get_active_media_server(self):
            return "plex"

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
        # Real source clients return album results carrying title + artist;
        # the filler now validates those before trusting the artwork.
        return [SimpleNamespace(id='search-album', image_url=self.search_image,
                                title='Album', artist='Artist')]


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
    # The scan now joins a representative track path to check art on disk.
    cursor.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            album_id INTEGER,
            file_path TEXT,
            disc_number INTEGER,
            track_number INTEGER
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
        # Mirror real `_create_finding` contract: True on insert.
        create_finding=lambda **kwargs: (findings.append(kwargs) or True),
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


def test_missing_cover_art_uses_configured_art_sources(monkeypatch):
    """When cover-art sources are configured (album_art_order), the Filler pulls
    art from them and skips the metadata source-priority loop — same 'cover art
    sources' notion the Re-tag job and post-process embed honor."""
    conn = _make_db((1, 'Album', 1, '', 'sp-album', 'it-album', 'dz-album', 'dg-album', 'hy-album'))
    settings = {
        'repair.jobs.missing_cover_art.settings': {},
        'metadata_enhancement.album_art_order': ['itunes', 'deezer'],
    }
    findings = []
    context = SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(get=lambda key, default=None: settings.get(key, default)),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        findings=findings,
    )
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'spotify')
    consulted = []
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: consulted.append(s) or _FakeClient())
    monkeypatch.setattr('core.metadata.art_lookup.select_preferred_art_url',
                        lambda artist, album, meta, order, **k: 'https://configured/art.jpg')

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    # ALBUM art came from the configured order — the album source-priority loop
    # was skipped (if it had run, the URL would be the fake client's art).
    assert findings[0]['details']['found_artwork_url'] == 'https://configured/art.jpg'
    # Artist-art search (Pache711) is a SEPARATE lookup that does consult the
    # sources; the fake client has no search_artists, so it finds nothing and
    # no artist target is offered.
    assert findings[0]['details']['found_artist_url'] is None


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
    assert discogs_client.search_calls == [('Artist Album', 5)]
    assert spotify_client.search_calls == []
    assert itunes_client.search_calls == []
    assert context.findings[0]['details']['found_artwork_url'] == 'https://img/discogs-search'


# ── Stricter matching (issue: new sources returning WRONG cover art) ──

class _SearchClient:
    """search_albums returns whatever results it's given (title/artist/image)."""
    def __init__(self, results):
        self._results = results
        self.search_calls = []

    def get_album(self, album_id, include_tracks=False):
        return None

    def search_albums(self, query, limit=1):
        self.search_calls.append((query, limit))
        return list(self._results)


def test_search_rejects_wrong_artist_result(monkeypatch):
    """A result with the right-ish title but a DIFFERENT artist must be rejected
    (this is what produced wrong covers from the new sources)."""
    conn = _make_db((1, 'Album', 1, '', None, None, None, None, None))
    context = _make_context(conn)
    client = _SearchClient([SimpleNamespace(id='x', image_url='https://img/wrong',
                                            title='Album', artist='Different Artist')])
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'discogs')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: client if s == 'discogs' else None)

    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 0          # wrong-artist art not accepted
    assert context.findings == []


def test_search_skips_wrong_result_and_takes_matching_one(monkeypatch):
    """Given several results, take the first that actually matches title+artist."""
    conn = _make_db((1, 'Album', 1, '', None, None, None, None, None))
    context = _make_context(conn)
    client = _SearchClient([
        SimpleNamespace(id='a', image_url='https://img/wrong', title='Other Record', artist='Someone'),
        SimpleNamespace(id='b', image_url='https://img/right', title='Album', artist='Artist'),
    ])
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'discogs')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: client if s == 'discogs' else None)

    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 1
    assert context.findings[0]['details']['found_artwork_url'] == 'https://img/right'


def test_result_matches_unit():
    m = mca.MissingCoverArtJob._result_matches
    # exact + deluxe variant + featuring all accepted when artist matches
    assert m({'title': 'Album', 'artist': 'Artist'}, 'Album', 'Artist')
    assert m({'title': 'Album (Deluxe Edition)', 'artist': 'Artist'}, 'Album', 'Artist')
    assert m({'title': 'Album', 'artist': 'The Artist'}, 'Album', 'Artist')   # stopword 'the'
    # wrong artist / wrong title rejected
    assert not m({'title': 'Album', 'artist': 'Nope'}, 'Album', 'Artist')
    assert not m({'title': 'Totally Other', 'artist': 'Artist'}, 'Album', 'Artist')
    # no artist on result → require exact title
    assert m({'title': 'Album'}, 'Album', 'Artist')
    assert not m({'title': 'Album Deluxe'}, 'Album', 'Artist')


# ── disk-art check must run on the RESOLVED path (flags-every-album bug) ──

def _add_track(conn, path):
    conn.execute(
        "INSERT INTO tracks (id, album_id, file_path, disc_number, track_number) "
        "VALUES (1, 1, ?, 1, 1)", (path,))
    conn.commit()


def test_scan_checks_disk_art_on_resolved_path(monkeypatch):
    # Album already has a DB thumb (db not missing) and a track whose DB path
    # only resolves via mapping. The disk-art check must run on the RESOLVED
    # path — checking the raw path would fail on path-mapped setups and flag
    # the whole library while the apply (which resolves) finds art present.
    conn = _make_db((1, 'Album', 1, 'https://has/thumb', None, None, None, None, None))
    _add_track(conn, '/plex/raw/song.flac')
    context = _make_context(conn)
    checked = {}
    monkeypatch.setattr(mca, 'resolve_library_file_path',
                        lambda raw, **k: '/resolved/song.flac' if raw == '/plex/raw/song.flac' else None)
    monkeypatch.setattr(mca, 'file_has_embedded_art',
                        lambda p: checked.update(path=p) or True)
    monkeypatch.setattr(mca, 'folder_has_cover_sidecar', lambda d: True)  # has cover.jpg too

    result = mca.MissingCoverArtJob().scan(context)

    assert checked.get('path') == '/resolved/song.flac'   # resolved, not raw
    assert result.findings_created == 0                    # embedded + cover.jpg → not flagged


def test_scan_unresolvable_path_not_flagged_disk_missing(monkeypatch):
    # An unreachable file (resolve → None) must NOT be claimed as "missing disk
    # art" — we can't know, so don't false-flag. (Album has a thumb already.)
    conn = _make_db((1, 'Album', 1, 'https://has/thumb', None, None, None, None, None))
    _add_track(conn, '/gone/song.flac')
    context = _make_context(conn)
    monkeypatch.setattr(mca, 'resolve_library_file_path', lambda raw, **k: None)
    called = []
    monkeypatch.setattr(mca, 'file_has_embedded_art', lambda p: called.append(p) or False)
    monkeypatch.setattr(mca, 'folder_has_cover_sidecar', lambda d: called.append(d) or False)

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 0   # thumb present, disk unknown → not flagged
    assert called == []                   # never checked art on a None path


def test_local_album_with_embedded_and_sidecar_not_flagged(monkeypatch):
    # Has BOTH embedded art AND a cover.jpg — nothing missing, even with an
    # empty DB thumb cache. (Boulder: don't flag albums that already have art.)
    conn = _make_db((1, 'Album', 1, '', None, None, None, None, None))  # empty thumb
    _add_track(conn, '/music/Album/01.flac')
    context = _make_context(conn)
    monkeypatch.setattr(mca, 'resolve_library_file_path', lambda raw, **k: raw)
    monkeypatch.setattr(mca, 'file_has_embedded_art', lambda p: True)
    monkeypatch.setattr(mca, 'folder_has_cover_sidecar', lambda d: True)

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 0   # has both → not "missing"
    assert result.skipped == 1


def test_embedded_art_but_no_cover_jpg_is_flagged(monkeypatch):
    # Sokhi: files HAVE embedded art but no cover.jpg sidecar. With cover.jpg
    # enabled (default), it's flagged so the filler writes the sidecar — even
    # when the API finds NO art (the apply extracts the embedded art).
    conn = _make_db((1, 'Album', 1, 'https://has/thumb', None, None, None, None, None))
    _add_track(conn, '/music/Album/01.flac')
    context = _make_context(conn)
    monkeypatch.setattr(mca, 'resolve_library_file_path', lambda raw, **k: raw)
    monkeypatch.setattr(mca, 'file_has_embedded_art', lambda p: True)      # embedded present
    monkeypatch.setattr(mca, 'folder_has_cover_sidecar', lambda d: False)  # but no cover.jpg
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: _FakeClient())  # API finds nothing

    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 1   # flagged for the missing sidecar
    assert context.findings[0]['details']['sidecar_from_embedded'] is True


def test_local_album_without_file_art_still_flagged(monkeypatch):
    # Local album whose files genuinely lack art → still flagged (real case).
    # Give it a source id + findable art so a finding is created when flagged.
    conn = _make_db((1, 'Album', 1, '', 'sp-album', None, None, None, None))
    _add_track(conn, '/music/Album/01.flac')
    context = _make_context(conn)
    monkeypatch.setattr(mca, 'resolve_library_file_path', lambda raw, **k: raw)
    monkeypatch.setattr(mca, 'file_has_embedded_art', lambda p: False)     # no embedded art
    monkeypatch.setattr(mca, 'folder_has_cover_sidecar', lambda d: False)
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: _FakeClient(album_image='https://img/x'))

    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 1   # files lack art → flagged


def test_media_server_only_album_empty_thumb_still_flagged(monkeypatch):
    # No local files (media-server-only) + empty thumb → DB thumb is the only
    # art, so still flag it.
    conn = _make_db((1, 'Album', 1, '', 'sp-album', None, None, None, None))  # no track added
    context = _make_context(conn)
    monkeypatch.setattr(mca, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(mca, 'get_client_for_source', lambda s: _FakeClient(album_image='https://img/x'))

    result = mca.MissingCoverArtJob().scan(context)
    assert result.findings_created == 1   # media-server-only + empty thumb → flagged
