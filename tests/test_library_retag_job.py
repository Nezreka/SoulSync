"""Seam tests for the Library Re-tag job scan + the apply handler.

Injected fakes only — no metadata APIs, no real tag writes. A temp sqlite db
+ real (empty) track files exercise the orchestration: scan -> detailed
finding, and finding -> per-track write payload.
"""

import sqlite3
import sys
import types
from types import SimpleNamespace

# Stub optional deps so the modules import in the test env.
if 'spotipy' not in sys.modules:
    sp = types.ModuleType('spotipy'); oa = types.ModuleType('spotipy.oauth2')
    sp.Spotify = type('S', (), {}); oa.SpotifyOAuth = oa.SpotifyClientCredentials = type('O', (), {})
    sp.oauth2 = oa; sys.modules['spotipy'] = sp; sys.modules['spotipy.oauth2'] = oa
if 'core.settings' not in sys.modules:
    cm = types.ModuleType('config'); sm = types.ModuleType('core.settings')

    class _Cfg:
        def get(self, k, d=None): return d
        def get_active_media_server(self): return 'plex'
    sm.config_manager = _Cfg(); cm.settings = sm
    sys.modules['config'] = cm; sys.modules['core.settings'] = sm

from core.repair_jobs import library_retag as lr


def _db_with_album(path, track_file, current_title='Old Title'):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("""CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER,
                 spotify_album_id TEXT, itunes_album_id TEXT, deezer_id TEXT, musicbrainz_release_id TEXT)""")
    c.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, album_id INTEGER, title TEXT,
                 track_number INTEGER, disc_number INTEGER, file_path TEXT)""")
    c.execute("INSERT INTO artists (id, name) VALUES (1, 'Real Artist')")
    c.execute("INSERT INTO albums (id, title, artist_id, spotify_album_id) VALUES (1, 'Real Album', 1, 'sp_alb')")
    c.execute("INSERT INTO tracks (id, album_id, title, track_number, disc_number, file_path) VALUES (1, 1, ?, 1, 1, ?)",
              (current_title, track_file))
    conn.commit()
    return conn


def _context(conn, settings):
    findings = []
    return SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: conn),
        config_manager=SimpleNamespace(get=lambda k, d=None: {'repair.jobs.library_retag.settings': settings}.get(k, d)),
        check_stop=lambda: False, wait_if_paused=lambda: False,
        update_progress=lambda *a, **k: None, report_progress=lambda *a, **k: None,
        create_finding=lambda **kw: (findings.append(kw) or True),
        findings=findings,
    )


_ALBUM_META = {'name': 'Real Album', 'artists': [{'name': 'Real Artist'}],
               'year': '2021', 'genres': ['Rock'], 'total_tracks': 1,
               'image_url': 'http://art/cover.jpg'}
_SRC_TRACKS = [{'name': 'Real Title', 'track_number': 1, 'disc_number': 1, 'id': 'sp_trk'}]


def _patch_source(monkeypatch, current_tags):
    monkeypatch.setattr(lr, 'get_album_for_source', lambda s, i: _ALBUM_META)
    monkeypatch.setattr(lr, 'get_album_tracks_for_source', lambda s, i: list(_SRC_TRACKS))
    monkeypatch.setattr(lr, '_read_current_tags', lambda p: dict(current_tags))


def test_scan_creates_detailed_finding(tmp_path, monkeypatch):
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert ctx.findings[0]['finding_type'] == 'library_retag'
    assert d['source'] == 'spotify'
    assert d['cover_action'] == 'replace'
    tp = d['tracks'][0]
    assert tp['changes']['title'] == {'old': 'Old Title', 'new': 'Real Title'}
    # source ids stamped onto the write payload
    assert tp['db_data']['spotify_album_id'] == 'sp_alb'
    assert tp['db_data']['spotify_track_id'] == 'sp_trk'
    assert tp['db_data']['title'] == 'Real Title'


def test_scan_dry_run_off_auto_applies_no_finding(tmp_path, monkeypatch):
    """dry_run=False: scan applies in place (auto_fixed) and creates NO finding."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify', 'dry_run': False})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    writes = []
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: writes.append(db_data) or {'success': True})

    result = lr.LibraryRetagJob().scan(ctx)

    assert ctx.findings == []                 # no finding in apply mode
    assert result.auto_fixed == 1
    assert writes and writes[0]['title'] == 'Real Title'   # actually wrote


def test_scan_prefers_configured_cover_art_source(tmp_path, monkeypatch):
    """Sokhi's request: when cover-art sources are configured, the re-tag pulls
    art from them (select_preferred_art_url) instead of the matched source's
    album image — so changing sources + 'replace' re-downloads fresh covers."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.metadata.art_lookup.select_preferred_art_url',
                        lambda artist, album, meta, order, **k: 'http://itunes/big-cover.jpg')

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert d['cover_url'] == 'http://itunes/big-cover.jpg'   # configured source won
    assert d['cover_action'] == 'replace'


def test_scan_falls_back_to_source_image_when_no_configured_art(tmp_path, monkeypatch):
    """Non-breaking: with no configured cover-art order, keep using the matched
    source's album image (select_preferred_art_url returns None)."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.metadata.art_lookup.select_preferred_art_url',
                        lambda *a, **k: None)

    result = lr.LibraryRetagJob().scan(ctx)
    d = ctx.findings[0]['details']
    assert d['cover_url'] == 'http://art/cover.jpg'   # _ALBUM_META['image_url']


def test_scan_full_depth_attaches_full_meta_to_finding(tmp_path, monkeypatch):
    """depth=full: each track plan carries a full_meta dict (title/album/artist +
    source ids) for the enrichment cascade, and details record the depth."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify', 'depth': 'full'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert d['depth'] == 'full'
    fm = d['tracks'][0]['full_meta']
    assert fm['title'] == 'Real Title'
    assert fm['album'] == 'Real Album'
    assert fm['album_artist'] == 'Real Artist'
    assert fm['spotify_album_id'] == 'sp_alb'
    assert fm['spotify_track_id'] == 'sp_trk'


def test_scan_full_depth_auto_apply_runs_enrich(tmp_path, monkeypatch):
    """depth=full + dry_run off: after the light write, the full enrichment
    cascade runs once per written track."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify',
                          'depth': 'full', 'dry_run': False})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: {'success': True})
    enriched = []
    monkeypatch.setattr(lr, '_run_full_enrich',
                        lambda fp, meta, **k: enriched.append((fp, meta, k.get('runtime'))) or True)

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.auto_fixed == 1
    assert len(enriched) == 1
    assert enriched[0][1]['spotify_track_id'] == 'sp_trk'
    # the scan must thread a REAL (non-None) runtime through apply -> enrich,
    # otherwise depth=full silently stays the old no-op (#1511.1)
    assert enriched[0][2] is not None


def test_scan_light_depth_does_not_run_enrich(tmp_path, monkeypatch):
    """depth=light (default): no full_meta, enrichment cascade never invoked."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify',
                          'dry_run': False})  # depth defaults to light
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: {'success': True})
    enriched = []
    monkeypatch.setattr(lr, '_run_full_enrich',
                        lambda fp, meta, **k: enriched.append(fp) or True)

    lr.LibraryRetagJob().scan(ctx)
    assert enriched == []


def test_scan_skips_album_already_correct(tmp_path, monkeypatch):
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Real Title')
    # cover skipped + tags already match → nothing to do
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'skip', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)
    assert result.findings_created == 0
    assert ctx.findings == []
    assert result.skipped >= 1


def test_add_source_ids_maps_per_source():
    db = {}
    lr._add_source_ids(db, 'spotify', 'AL', {'id': 'TR'})
    assert db == {'spotify_album_id': 'AL', 'spotify_track_id': 'TR'}
    db2 = {}
    lr._add_source_ids(db2, 'musicbrainz', 'REL', {'id': 'REC'})
    assert db2 == {'musicbrainz_release_id': 'REL', 'musicbrainz_recording_id': 'REC'}


def test_scan_full_depth_fill_missing_includes_unchanged_tracks(tmp_path, monkeypatch):
    """Issue #1511.3: at depth=full, fill_missing tracks with complete tags and
    no changes still get the enrichment — the scan creates a finding with
    full_meta instead of skipping them."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Real Title')
    ctx = _context(conn, {'mode': 'fill_missing', 'cover_art': 'skip', 'source': 'spotify',
                          'depth': 'full'})
    _patch_source(monkeypatch, {
        'title': 'Real Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    d = ctx.findings[0]['details']
    assert d['tracks'][0]['changes'] == {}
    assert d['tracks'][0]['full_meta']['spotify_track_id'] == 'sp_trk'
    assert '(enrichment)' in ctx.findings[0]['title']


def test_build_retag_enrichment_runtime_wires_source_clients(monkeypatch):
    """Issue #1511.1: the runtime handed to embed_source_ids() carries real
    client objects in the worker-shaped attrs the source processors read
    (mb_worker.mb_service, *_worker.client)."""
    import sys, types
    made = {}

    def _fake_client_module(name, cls_name):
        mod = types.ModuleType(name)

        def _init(self, *a, **k):
            made.setdefault(cls_name, self)

        cls = type(cls_name, (), {'__init__': _init})
        setattr(mod, cls_name, cls)
        return mod

    monkeypatch.setitem(sys.modules, 'core.deezer_client',
                        _fake_client_module('core.deezer_client', 'DeezerClient'))
    monkeypatch.setitem(sys.modules, 'core.audiodb_client',
                        _fake_client_module('core.audiodb_client', 'AudioDBClient'))
    monkeypatch.setitem(sys.modules, 'core.lastfm_client',
                        _fake_client_module('core.lastfm_client', 'LastFMClient'))
    mb_mod = types.ModuleType('core.musicbrainz_service')

    def _mb_init(self, *a, **k):
        made.setdefault('MusicBrainzService', self)

    mb_mod.MusicBrainzService = type('MusicBrainzService', (), {'__init__': _mb_init})
    monkeypatch.setitem(sys.modules, 'core.musicbrainz_service', mb_mod)

    rt = lr.build_retag_enrichment_runtime(
        config_manager=SimpleNamespace(get=lambda k, d=None: 'KEY' if k == 'lastfm.api_key' else d),
        db=object())

    assert rt.deezer_worker.client is made['DeezerClient']
    assert rt.audiodb_worker.client is made['AudioDBClient']
    assert rt.lastfm_worker.client is made['LastFMClient']
    assert rt.mb_worker.mb_service is made['MusicBrainzService']


def test_run_full_enrich_passes_file_date_to_enrichment(monkeypatch):
    """Issue #1511.2: the file's full date is handed to embed_source_ids() in
    the metadata (blocking the writer's unconditional release-year fallback).
    Pins the handover, not the write itself — embed_source_ids is mocked."""
    import sys, types
    seen = {}
    src_mod = types.ModuleType('core.metadata.source')
    src_mod.embed_source_ids = lambda audio, meta, context=None, runtime=None: seen.update(
        meta=dict(meta), runtime=runtime)
    monkeypatch.setitem(sys.modules, 'core.metadata.source', src_mod)
    common_mod = types.ModuleType('core.metadata.common')
    audio = SimpleNamespace(tags=object(), save=lambda: seen.setdefault('saved', True))
    common_mod.get_mutagen_symbols = lambda: SimpleNamespace(File=lambda p, **k: audio)
    monkeypatch.setitem(sys.modules, 'core.metadata.common', common_mod)
    monkeypatch.setattr(lr, '_read_embedded_date', lambda fp, symbols: '2016-01-19')

    full_meta = {'title': 'T', 'spotify_track_id': 'x'}
    rt = SimpleNamespace(deezer_worker=SimpleNamespace(client=object()))
    assert lr._run_full_enrich('/tmp/does-not-matter.flac', full_meta, runtime=rt) is True

    assert seen['meta']['date'] == '2016-01-19'
    assert 'date' not in full_meta            # caller dict untouched
    assert seen['runtime'] is rt               # real runtime actually passed
    assert seen['saved'] is True


def test_apply_enrichment_only_plan_runs_enrich(tmp_path, monkeypatch):
    """Issue #1511.2 (review): a full-depth plan with empty db_data (e.g. a
    deezer-matched track — deezer ids are never stamped) must still reach
    _run_full_enrich instead of being skipped, and count as written."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    enriched = []
    monkeypatch.setattr(lr, '_run_full_enrich',
                        lambda fp, meta, **k: enriched.append((fp, meta)) or True)
    plans = [{'file_path': str(track), 'db_data': {},
              'full_meta': {'title': 'T', 'artist': 'A'}}]

    res = lr.apply_track_plans(plans, full=True, enrich_runtime=SimpleNamespace())

    assert enriched and enriched[0][0] == str(track)
    assert res['written'] == 1
    assert res['skipped'] == 0
    assert res['failed'] == 0


def test_apply_enrichment_only_plan_counts_enrich_failure(tmp_path, monkeypatch):
    """The mirror: enrichment-only plan whose enrichment fails counts failed."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    monkeypatch.setattr(lr, '_run_full_enrich', lambda fp, meta, **k: False)
    plans = [{'file_path': str(track), 'db_data': {},
              'full_meta': {'title': 'T', 'artist': 'A'}}]

    res = lr.apply_track_plans(plans, full=True, enrich_runtime=SimpleNamespace())

    assert res['written'] == 0
    assert res['failed'] == 1


# ── apply handler ──

def test_fix_library_retag_passes_enrichment_runtime(tmp_path, monkeypatch):
    """_fix_library_retag must hand apply_track_plans a real enrichment runtime
    at full depth (and None at light depth) — the #1511.1 wiring seam."""
    import core.repair_worker as rw
    track = tmp_path / 'a.flac'; track.write_bytes(b'')

    worker = rw.RepairWorker.__new__(rw.RepairWorker)
    worker.db = SimpleNamespace()
    worker._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    worker.transfer_folder = str(tmp_path)
    monkeypatch.setattr(rw, '_resolve_file_path', lambda p, *a, **k: p)

    calls = []
    monkeypatch.setattr('core.repair_jobs.library_retag.apply_track_plans',
                        lambda plans, *a, **k: calls.append(k) or {'written': 1, 'failed': 0,
                                                                 'skipped': 0, 'cover_written': False})
    sentinel = SimpleNamespace()
    monkeypatch.setattr('core.repair_jobs.library_retag.build_retag_enrichment_runtime',
                        lambda cm, db: sentinel)

    base = {'tracks': [{'file_path': str(track), 'db_data': {'title': 'x'},
                        'full_meta': {'title': 'x'}}],
            'cover_action': None, 'cover_url': None}

    res = worker._fix_library_retag('album', '1', None, {**base, 'depth': 'full'})
    assert res['success'] is True
    assert calls[-1]['full'] is True
    assert calls[-1]['enrich_runtime'] is sentinel

    calls.clear()
    res = worker._fix_library_retag('album', '1', None, {**base, 'depth': 'light'})
    assert res['success'] is True
    assert calls[-1]['full'] is False
    assert calls[-1]['enrich_runtime'] is None


def test_fix_library_retag_writes_each_track(tmp_path, monkeypatch):
    import core.repair_worker as rw
    track = tmp_path / 'a.flac'; track.write_bytes(b'')

    worker = rw.RepairWorker.__new__(rw.RepairWorker)
    worker.db = SimpleNamespace()
    worker._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    worker.transfer_folder = str(tmp_path)

    monkeypatch.setattr(rw, '_resolve_file_path', lambda p, *a, **k: p)
    writes = []
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: writes.append((fp, db_data)) or {'success': True})

    details = {
        'tracks': [{'file_path': str(track), 'db_data': {'title': 'Real Title', 'spotify_track_id': 'sp_trk'}}],
        'cover_action': None, 'cover_url': None,
    }
    res = worker._fix_library_retag('album', '1', None, details)
    assert res['success'] is True
    assert res['written'] == 1
    assert writes[0][1]['title'] == 'Real Title'
    assert writes[0][1]['spotify_track_id'] == 'sp_trk'


def test_fix_library_retag_counts_unreachable(tmp_path, monkeypatch):
    import core.repair_worker as rw
    worker = rw.RepairWorker.__new__(rw.RepairWorker)
    worker.db = SimpleNamespace()
    worker._config_manager = SimpleNamespace(get=lambda k, d=None: d)
    worker.transfer_folder = str(tmp_path)
    monkeypatch.setattr(rw, '_resolve_file_path', lambda p, *a, **k: p)
    monkeypatch.setattr('core.tag_writer.write_tags_to_file', lambda *a, **k: {'success': True})

    details = {'tracks': [{'file_path': str(tmp_path / 'missing.flac'), 'db_data': {'title': 'x'}}],
               'cover_action': None, 'cover_url': None}
    res = worker._fix_library_retag('album', '1', None, details)
    assert res['success'] is False        # nothing written (file missing)


# ---------------------------------------------------------------------------
# Cover-art scans on path-mapped setups (the "(0 track(s))" / "No tracks to
# re-tag in finding" report): the scan must resolve DB paths the same way the
# apply handler does, never emit an empty finding, and give unmatched tracks
# the album art.
# ---------------------------------------------------------------------------

def test_scan_resolves_mapped_paths_instead_of_skipping(tmp_path, monkeypatch):
    """DB stores a container path the scan process can't see directly; the
    resolver maps it to the real file. Before the fix the bare isfile() check
    dropped every track and cover-mode scans produced unappliable 0-track
    findings."""
    real = tmp_path / 'track.flac'; real.write_bytes(b'')
    raw = '/container/music/track.flac'   # not a real path here
    conn = _db_with_album(str(tmp_path / 'm.db'), raw, current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {
        'title': 'Old Title', 'album_artist': 'Real Artist', 'album': 'Real Album',
        'year': '2021', 'genre': 'Rock', 'track_number': 1, 'disc_number': 1,
    })
    monkeypatch.setattr(lr, 'resolve_library_file_path',
                        lambda p, **k: str(real) if p == raw else None)

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    tracks = ctx.findings[0]['details']['tracks']
    assert len(tracks) == 1
    assert tracks[0]['file_path'] == str(real)   # plan carries the RESOLVED path


def test_cover_scan_with_no_reachable_tracks_creates_no_finding(tmp_path, monkeypatch):
    """Cover action set but no track resolvable: skip the album entirely.
    The old behavior created a '(0 track(s))' finding whose apply always
    failed with 'No tracks to re-tag in finding'."""
    conn = _db_with_album(str(tmp_path / 'm.db'), '/container/music/gone.flac')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {'title': 'Old Title'})
    monkeypatch.setattr(lr, 'resolve_library_file_path', lambda p, **k: None)

    result = lr.LibraryRetagJob().scan(ctx)

    assert ctx.findings == []
    assert result.findings_created == 0
    assert result.skipped == 1


def test_cover_scan_includes_unmatched_tracks_as_art_only(tmp_path, monkeypatch):
    """A track with no source match can't be re-tagged, but album cover art
    still applies to it — cover-mode scans include an art-only plan (empty
    db_data) and the finding title says 'cover art', not '(0 track(s))'."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    conn = _db_with_album(str(tmp_path / 'm.db'), str(track), current_title='Old Title')
    ctx = _context(conn, {'mode': 'overwrite', 'cover_art': 'replace', 'source': 'spotify'})
    _patch_source(monkeypatch, {'title': 'Old Title'})
    # Source tracklist that matches NOTHING in the library.
    monkeypatch.setattr(lr, 'get_album_tracks_for_source',
                        lambda s, i: [{'name': 'Zzz Unrelated Song', 'track_number': 9,
                                       'disc_number': 9, 'id': 'zz'}])

    result = lr.LibraryRetagJob().scan(ctx)

    assert result.findings_created == 1
    f = ctx.findings[0]
    assert 'cover art' in f['title']
    tracks = f['details']['tracks']
    assert len(tracks) == 1
    assert not tracks[0]['changes'] and tracks[0]['db_data'] == {}  # art-only plan


def test_apply_art_only_plan_embeds_cover(tmp_path, monkeypatch):
    """The art-only plans the cover-mode scan now emits (empty db_data) must go
    through apply_track_plans as a WRITE (cover embed), not a skip/failure."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    calls = []
    monkeypatch.setattr('core.tag_writer.download_cover_art',
                        lambda url: (b'img-bytes', 'image/jpeg'))
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: calls.append((fp, db_data, k)) or {'success': True})

    res = lr.apply_track_plans(
        [{'file_path': str(track), 'db_data': {}}],
        cover_action='replace', cover_url='http://art/cover.jpg',
    )

    assert res['written'] == 1 and res['failed'] == 0
    fp, db_data, kwargs = calls[0]
    assert db_data == {} and kwargs['embed_cover'] is True
    assert kwargs['cover_data'] == (b'img-bytes', 'image/jpeg')
    assert res['cover_written'] is True  # cover.jpg written next to the track


def test_apply_art_only_plan_skips_when_cover_download_fails(tmp_path, monkeypatch):
    """If the cover can't be downloaded there's nothing to write for an
    art-only plan — it must count as skipped, never failed."""
    track = tmp_path / 'track.flac'; track.write_bytes(b'')
    monkeypatch.setattr('core.tag_writer.download_cover_art',
                        lambda url: (_ for _ in ()).throw(RuntimeError('net down')))
    monkeypatch.setattr('core.tag_writer.write_tags_to_file',
                        lambda fp, db_data, **k: {'success': True})

    res = lr.apply_track_plans(
        [{'file_path': str(track), 'db_data': {}}],
        cover_action='replace', cover_url='http://art/cover.jpg',
    )

    assert res == {'written': 0, 'failed': 0, 'skipped': 1, 'cover_written': False,
                   'lyrics_written': 0}
