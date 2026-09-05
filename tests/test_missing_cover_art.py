"""Cover Art Filler — the scan's own decisions, against the Library-v2 catalogue.

These tests used to build a hand-rolled ``artists``/``albums``/``tracks`` schema
and monkeypatch ``get_client_for_source``/``get_primary_source`` on the job
module. Both are gone: the scan walks ``active_album_subjects`` and asks
``core.library2.provider_adapters.fetch_artwork_url`` for a URL, and the
per-source client walk it replaced has been deleted from the job along with the
search-result matching helpers it used.

That split is why this file is now shorter. *Which* provider answers, in what
order, and whether a fuzzy search result may be trusted are properties of
``fetch_artwork_url`` and ``core.metadata.art_lookup``, tested in
``tests/library2/test_provider_adapters.py``. What is still this job's own is
the decision to raise a finding at all: DB image, embedded art, and cover.jpg
sidecar, each checked against the path the file actually lives at.
"""

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

from core.library2.provider_adapters import ArtworkProviderResult
from core.repair_jobs import missing_cover_art as mca


def _found(url='https://img/found', source='spotify'):
    return ArtworkProviderResult(
        kind='album', source=source, provider_entity_id='sp-album', url=url)


def _make_db(*, album_image='', file_path=None, album_external_ids='{}'):
    from core.library2.schema import ensure_library_v2_schema

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.execute(
        "INSERT INTO lib2_artists (id, name, sort_name, image_url) "
        "VALUES (1, 'Artist', 'Artist', 'https://artist/thumb')")
    conn.execute(
        "INSERT INTO lib2_albums (id, primary_artist_id, title, image_url, external_ids) "
        "VALUES (1, 1, 'Album', ?, ?)", (album_image, album_external_ids))
    conn.execute(
        "INSERT INTO lib2_tracks (id, album_id, title, track_number) "
        "VALUES (1, 1, 'Track', 1)")
    if file_path:
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, format, is_primary) "
            "VALUES (1, ?, 'flac', 1)", (file_path,))
    conn.commit()
    return conn


def _make_context(conn, **settings):
    values = {'features.library_v2': True, **settings}
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(
            get=lambda key, default=None: values.get(key, default)),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        update_progress=lambda *args, **kwargs: None,
        report_progress=lambda *args, **kwargs: None,
        # Mirror real `_create_finding` contract: True on insert.
        create_finding=lambda **kwargs: (findings.append(kwargs) or True),
        findings=findings,
    )


def _patch_disk(monkeypatch, *, resolved, embedded, sidecar, record=None):
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path',
                        lambda raw, **k: resolved(raw) if callable(resolved) else resolved)
    monkeypatch.setattr('core.metadata.art_apply.file_has_embedded_art',
                        lambda p: (record.append(p) if record is not None else None) or embedded)
    monkeypatch.setattr('core.metadata.art_apply.folder_has_cover_sidecar',
                        lambda d: (record.append(d) if record is not None else None) or sidecar)


def _patch_artwork(monkeypatch, album=None, artist=None, calls=None):
    def fake(kind, **kwargs):
        if calls is not None:
            calls.append((kind, kwargs))
        return album if kind == 'album' else artist

    monkeypatch.setattr('core.library2.provider_adapters.fetch_artwork_url', fake)


# ── which sources the job asks for, and in what order ────────────────────────

def test_prefer_source_leads_the_configured_order(monkeypatch):
    """The job's own ``prefer_source`` setting goes to the FRONT of the order it
    hands the adapter, and does not appear twice."""
    conn = _make_db(file_path='/music/Album/01.flac')
    context = _make_context(
        conn,
        **{'repair.jobs.missing_cover_art.settings': {'prefer_source': 'spotify'},
           'metadata_enhancement.album_art_order': ['itunes', 'spotify', 'deezer']})
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=False)
    calls = []
    _patch_artwork(monkeypatch, album=_found(), calls=calls)

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    album_call = next(kwargs for kind, kwargs in calls if kind == 'album')
    assert album_call['source_order'] == ('spotify', 'itunes', 'deezer')


def test_configured_art_order_is_passed_through_unchanged(monkeypatch):
    """`album_art_order` is the same 'cover art sources' notion the Re-tag job
    and the post-process embed honour."""
    conn = _make_db(file_path='/music/Album/01.flac')
    context = _make_context(
        conn, **{'metadata_enhancement.album_art_order': ['itunes', 'deezer']})
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=False)
    calls = []
    _patch_artwork(monkeypatch, album=_found(url='https://configured/art.jpg'), calls=calls)

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    album_call = next(kwargs for kind, kwargs in calls if kind == 'album')
    assert album_call['source_order'] == ('itunes', 'deezer')
    assert context.findings[0]['details']['found_artwork_url'] == 'https://configured/art.jpg'


def test_artist_art_is_only_offered_when_it_differs(monkeypatch):
    """Pache711: artist art is a separate, independently applyable target — but
    offering the image the artist already has would be a no-op finding."""
    conn = _make_db(file_path='/music/Album/01.flac')
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=False)
    _patch_artwork(
        monkeypatch, album=_found(),
        artist=ArtworkProviderResult(kind='artist', source='spotify',
                                     provider_entity_id='sp-artist',
                                     url='https://artist/thumb'))

    mca.MissingCoverArtJob().scan(context)

    assert context.findings[0]['details']['found_artist_url'] is None


# ── disk-art check must run on the RESOLVED path (flags-every-album bug) ─────

def test_scan_checks_disk_art_on_resolved_path(monkeypatch):
    """The stored path may only resolve via mapping. Checking the raw path
    failed on every path-mapped setup and flagged the whole library, while the
    apply — which resolves — found the art sitting right there."""
    conn = _make_db(album_image='https://has/thumb', file_path='/plex/raw/song.flac')
    context = _make_context(conn)
    checked = []
    _patch_disk(
        monkeypatch,
        resolved=lambda raw: '/resolved/song.flac' if raw == '/plex/raw/song.flac' else None,
        embedded=True, sidecar=True, record=checked)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert checked[0] == '/resolved/song.flac'   # resolved, not raw
    assert result.findings_created == 0          # embedded + cover.jpg → not flagged


def test_unresolvable_path_is_not_claimed_as_missing_art(monkeypatch):
    """An unreachable file cannot be said to lack art — don't false-flag it."""
    conn = _make_db(album_image='https://has/thumb', file_path='/gone/song.flac')
    context = _make_context(conn)
    called = []
    _patch_disk(monkeypatch, resolved=None, embedded=False, sidecar=False, record=called)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 0   # thumb present, disk unknown → not flagged
    assert called == []                   # never checked art on a None path


def test_album_with_art_everywhere_is_not_flagged(monkeypatch):
    """Boulder: don't flag albums that already have art."""
    conn = _make_db(album_image='https://has/thumb', file_path='/music/Album/01.flac')
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=True, sidecar=True)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 0
    assert result.skipped == 1


def test_a_blank_catalogue_image_is_a_gap_of_its_own(monkeypatch):
    """Art on disk does not cover an empty ``lib2_albums.image_url``: that is
    what the library grid draws, so the album renders blank there. The legacy
    scan treated the DB thumb as a cache and stayed quiet; the native one names
    the gap, and ``details`` says which of the three it is."""
    conn = _make_db(album_image='', file_path='/music/Album/01.flac')
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=True, sidecar=True)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    details = context.findings[0]['details']
    assert (details['db_missing'], details['embed_missing'],
            details['sidecar_from_embedded']) == (True, False, False)


def test_embedded_art_but_no_cover_jpg_is_flagged(monkeypatch):
    """Sokhi: files have embedded art but no cover.jpg. Flagged so the filler
    writes the sidecar — even when no provider offers a URL, because the apply
    extracts the embedded art instead."""
    conn = _make_db(album_image='https://has/thumb', file_path='/music/Album/01.flac')
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=True, sidecar=False)
    _patch_artwork(monkeypatch, album=None)   # provider finds nothing

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    assert context.findings[0]['details']['sidecar_from_embedded'] is True
    assert context.findings[0]['details']['artwork_source'] == 'embedded'


def test_files_genuinely_without_art_are_flagged(monkeypatch):
    conn = _make_db(album_image='', file_path='/music/Album/01.flac')
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=False)
    _patch_artwork(monkeypatch, album=_found(url='https://img/x'))

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    assert context.findings[0]['entity_id'] == 'lib2:1'


def test_an_album_with_no_files_is_not_this_job(monkeypatch):
    """``active_album_subjects`` requires an active file. A fileless release is
    a wanted one, and its image is the enrichment workers' job — this scan is
    about art on disk and the catalogue entry beside it."""
    conn = _make_db(album_image='', file_path=None)
    context = _make_context(conn)
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=False, sidecar=False)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert result.scanned == 0
    assert result.findings_created == 0


def test_unresolved_path_falls_back_to_raw_when_the_file_is_really_there(
        tmp_path, monkeypatch):
    """Docker case (Sokhi): the mapping layer returns nothing, but the stored
    path is already a real file inside the container. Use it as-is, or the
    album's folder never gets looked at."""
    track = tmp_path / 'Album' / '01.flac'
    track.parent.mkdir()
    track.write_bytes(b'')
    conn = _make_db(album_image='https://has/thumb', file_path=str(track))
    context = _make_context(conn)
    monkeypatch.setattr('core.library2.paths.resolve_lib2_path', lambda raw, **k: None)
    monkeypatch.setattr('core.metadata.art_apply.file_has_embedded_art', lambda p: True)
    _patch_artwork(monkeypatch, album=None)   # real folder has no cover.jpg

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 1
    assert context.findings[0]['details']['sidecar_from_embedded'] is True


def test_cover_art_download_off_stops_the_sidecar_flag(monkeypatch):
    """With sidecar writing disabled, a missing cover.jpg is not a gap."""
    conn = _make_db(album_image='https://has/thumb', file_path='/music/Album/01.flac')
    context = _make_context(conn, **{'metadata_enhancement.cover_art_download': False})
    _patch_disk(monkeypatch, resolved=lambda raw: raw, embedded=True, sidecar=False)
    _patch_artwork(monkeypatch, album=_found())

    result = mca.MissingCoverArtJob().scan(context)

    assert result.findings_created == 0
    assert result.skipped == 1
