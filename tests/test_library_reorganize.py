"""Tests for the rewritten Library Reorganize repair job.

Issue #500: pre-rewrite the job had its own tag-reading + transfer-
folder-grouping + template-application implementation. The
``is_album = group_size > 1`` heuristic misclassified album tracks
as singles when only one track of an album sat in the transfer folder
or when album tags varied across tracks.

Post-rewrite the job delegates to the per-album planner
(``core.library_reorganize.preview_album_reorganize`` /
``reorganize_queue``) — no second move/template implementation. These
tests pin the delegation contract so future drift fails here instead
of at runtime against a real library.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ── stubs (same shape used elsewhere in the suite) ──────────────────────
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


from core.repair_jobs.library_reorganize import LibraryReorganizeJob
from core.repair_jobs.base import JobContext


# ── fixtures ──────────────────────────────────────────────────────────


class _FakeConfigManager:
    """Minimal config manager. Reads via ``get(key, default)``."""

    def __init__(self, settings: dict | None = None, file_org_enabled: bool = True,
                 active_server: str = 'plex'):
        self._settings = settings or {}
        self._file_org_enabled = file_org_enabled
        self._active_server = active_server

    def get(self, key, default=None):
        if key == 'file_organization.enabled':
            return self._file_org_enabled
        return self._settings.get(key, default)

    def get_active_media_server(self):
        return self._active_server


class _FakeDB:
    """Stand-in for MusicDatabase. Returns the canned album list from
    ``_load_albums``'s SELECT.

    Supports per-server filtering: pass ``rows_by_server`` as a dict to
    return different album sets depending on the SQL parameters. The
    helper inspects the SQL string for ``server_source = ?`` and returns
    the matching slice. Falls back to ``album_rows`` when ``rows_by_server``
    isn't set (back-compat with single-server tests)."""

    def __init__(self, album_rows: list = None, rows_by_server: dict = None):
        self._album_rows = album_rows or []
        self._rows_by_server = rows_by_server or {}

    def _get_connection(self):
        cursor = MagicMock()
        rows_by_server = self._rows_by_server
        default_rows = self._album_rows

        def _execute(sql, params=()):
            if rows_by_server and 'server_source = ?' in sql and params:
                cursor._captured_rows = rows_by_server.get(params[0], [])
            else:
                cursor._captured_rows = default_rows
            cursor.fetchall.return_value = cursor._captured_rows
            cursor.fetchone.return_value = (len(cursor._captured_rows),)

        cursor.execute.side_effect = _execute
        cursor.fetchall.return_value = default_rows
        cursor.fetchone.return_value = (len(default_rows),)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn


@pytest.fixture
def make_context():
    """Build a JobContext with optional finding-collector."""

    def _make(*, db, cm=None, dry_run=True, transfer='/tmp/transfer'):
        findings = []

        def _create_finding(**kwargs):
            findings.append(kwargs)
            return True  # 'inserted' return value

        ctx = JobContext(
            db=db,
            transfer_folder=transfer,
            config_manager=cm or _FakeConfigManager(
                settings={
                    f'repair.jobs.library_reorganize.settings.dry_run': dry_run,
                },
            ),
            create_finding=_create_finding,
        )
        # Attach so tests can inspect.
        ctx._captured_findings = findings  # type: ignore[attr-defined]
        return ctx

    return _make


def _make_album_row(*, id_, title='Test Album', artist_id=10, artist_name='Test Artist'):
    """Match the row shape ``_load_albums`` returns."""
    return {
        'id': id_,
        'title': title,
        'artist_id': artist_id,
        'artist_name': artist_name,
    }


def _stub_preview(monkeypatch, response_by_album_id: dict):
    """Patch ``preview_album_reorganize`` import inside scan() so it
    returns a canned response per album_id."""
    from core import library_reorganize as core_lr

    def _fake_preview(*, album_id, **kwargs):
        return response_by_album_id.get(album_id) or {
            'success': False, 'status': 'no_album', 'tracks': [],
        }
    monkeypatch.setattr(core_lr, 'preview_album_reorganize', _fake_preview)


# ── core delegation contract ─────────────────────────────────────────


def test_scan_skips_when_file_organization_disabled(make_context):
    """Pin: file_organization.enabled=False → scan returns immediately
    with empty result, no DB iteration, no preview calls."""
    db = _FakeDB([])
    cm = _FakeConfigManager(file_org_enabled=False)
    ctx = make_context(db=db, cm=cm)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.scanned == 0
    assert result.findings_created == 0
    assert ctx._captured_findings == []  # type: ignore[attr-defined]


def test_scan_returns_empty_when_no_albums(make_context):
    """Pin: empty DB → empty result, no errors."""
    db = _FakeDB([])
    ctx = make_context(db=db)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.scanned == 0
    assert result.findings_created == 0
    assert result.errors == 0


def test_scan_emits_path_mismatch_finding_for_each_changed_track(make_context, monkeypatch):
    """Pin: dry-run mode emits one finding per matched-but-not-unchanged
    track returned by the planner. Album with two tracks, both
    mismatched → two findings."""
    db = _FakeDB([_make_album_row(id_='A1')])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'Album One',
            'artist': 'Artist One',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'Track One', 'track_number': 1,
                    'current_path': 'old/path/01 - Track One.flac',
                    'new_path': 'new/path/01 - Track One.flac',
                    'matched': True, 'unchanged': False, 'file_exists': True,
                },
                {
                    'track_id': 't2', 'title': 'Track Two', 'track_number': 2,
                    'current_path': 'old/path/02 - Track Two.flac',
                    'new_path': 'new/path/02 - Track Two.flac',
                    'matched': True, 'unchanged': False, 'file_exists': True,
                },
            ],
        },
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.findings_created == 2
    assert result.scanned == 2
    findings = ctx._captured_findings  # type: ignore[attr-defined]
    titles = {f['title'] for f in findings}
    assert any('Track One' in t for t in titles)
    assert any('Track Two' in t for t in titles)


def test_scan_skips_unchanged_tracks(make_context, monkeypatch):
    """Pin: tracks with unchanged=True don't produce findings — they're
    already at the right path."""
    db = _FakeDB([_make_album_row(id_='A1')])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'Album One', 'artist': 'Artist One',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'Track One',
                    'current_path': 'right/path/01 - Track One.flac',
                    'new_path': 'right/path/01 - Track One.flac',
                    'matched': True, 'unchanged': True, 'file_exists': True,
                },
            ],
        },
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.findings_created == 0
    assert result.scanned == 1


def test_scan_skips_unmatched_tracks_within_planned_album(make_context, monkeypatch):
    """Pin: tracks the planner couldn't match (matched=False) are
    skipped — no path was computed for them, can't reorganize."""
    db = _FakeDB([_make_album_row(id_='A1')])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'Album One', 'artist': 'Artist One',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'Bonus Track',
                    'current_path': 'old/path/12 - Bonus.flac',
                    'new_path': '',
                    'matched': False, 'unchanged': False, 'file_exists': True,
                    'reason': 'No matching track in spotify tracklist',
                },
            ],
        },
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.findings_created == 0
    assert result.scanned == 1


def test_scan_skips_tracks_with_missing_files(make_context, monkeypatch):
    """Pin: file_exists=False → not eligible for move (handled by the
    Dead File Cleaner job instead). No path_mismatch finding emitted."""
    db = _FakeDB([_make_album_row(id_='A1')])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'Album One', 'artist': 'Artist One',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'Missing Track',
                    'current_path': 'gone/01 - Missing.flac',
                    'new_path': 'new/01 - Missing.flac',
                    'matched': True, 'unchanged': False, 'file_exists': False,
                },
            ],
        },
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.findings_created == 0


def test_scan_emits_album_needs_enrichment_when_planner_returns_no_source_id(make_context, monkeypatch):
    """Pin: planner returns status='no_source_id' → emit ONE
    album-level finding ('needs enrichment') instead of N per-track
    'no source' findings (which would clutter the UI)."""
    db = _FakeDB([_make_album_row(id_='A1', title='Unenriched Album')])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': False, 'status': 'no_source_id',
            'source': None,
            'album': 'Unenriched Album', 'artist': 'Some Artist',
            'tracks': [
                {'track_id': 't1', 'title': 'Track 1', 'matched': False, 'reason': '...'},
                {'track_id': 't2', 'title': 'Track 2', 'matched': False, 'reason': '...'},
            ],
        },
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    findings = ctx._captured_findings  # type: ignore[attr-defined]
    assert result.findings_created == 1
    assert findings[0]['finding_type'] == 'album_needs_enrichment'
    assert 'Unenriched Album' in findings[0]['title']


def test_scan_skips_albums_planner_reports_as_no_album(make_context, monkeypatch):
    """Pin: planner returns 'no_album' (race: album deleted between
    SELECT and preview) → silently skipped, no finding, no error."""
    db = _FakeDB([_make_album_row(id_='A1')])
    _stub_preview(monkeypatch, {
        'A1': {'success': False, 'status': 'no_album', 'tracks': []},
    })
    ctx = make_context(db=db, dry_run=True)

    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.findings_created == 0
    assert result.errors == 0
    assert result.skipped == 1


def test_scan_handles_preview_exceptions_gracefully(make_context, monkeypatch):
    """Pin: preview raising for one album doesn't abort the whole
    scan — counts as one error, continues to next album."""
    db = _FakeDB([
        _make_album_row(id_='A1'),
        _make_album_row(id_='A2', title='Good Album'),
    ])
    from core import library_reorganize as core_lr

    def _flaky(*, album_id, **kwargs):
        if album_id == 'A1':
            raise RuntimeError("preview boom")
        return {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'Good Album', 'artist': 'Artist',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'Track',
                    'current_path': 'old/01 - Track.flac',
                    'new_path': 'new/01 - Track.flac',
                    'matched': True, 'unchanged': False, 'file_exists': True,
                },
            ],
        }
    monkeypatch.setattr(core_lr, 'preview_album_reorganize', _flaky)

    ctx = make_context(db=db, dry_run=True)
    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert result.errors == 1                 # A1 failed
    assert result.findings_created == 1       # A2 succeeded


def test_scan_apply_mode_enqueues_albums_via_reorganize_queue(make_context, monkeypatch):
    """Pin: dry_run=False → mismatched albums are bulk-enqueued via
    ``core.reorganize_queue.get_queue().enqueue_many(...)``. Repair
    job does NOT do file moves itself — delegates to the queue worker
    which uses the same code path the per-album modal does."""
    db = _FakeDB([
        _make_album_row(id_='A1', title='First Album', artist_id=10, artist_name='A'),
        _make_album_row(id_='A2', title='Second Album', artist_id=20, artist_name='B'),
    ])
    _stub_preview(monkeypatch, {
        'A1': {
            'success': True, 'status': 'planned',
            'source': 'spotify',
            'album': 'First Album', 'artist': 'A',
            'tracks': [
                {
                    'track_id': 't1', 'title': 'X',
                    'current_path': 'old/01 - X.flac', 'new_path': 'new/01 - X.flac',
                    'matched': True, 'unchanged': False, 'file_exists': True,
                },
            ],
        },
        'A2': {
            'success': True, 'status': 'planned',
            'source': 'deezer',
            'album': 'Second Album', 'artist': 'B',
            'tracks': [
                {
                    'track_id': 't2', 'title': 'Y',
                    'current_path': 'old/01 - Y.flac', 'new_path': 'new/01 - Y.flac',
                    'matched': True, 'unchanged': False, 'file_exists': True,
                },
            ],
        },
    })

    enqueue_calls = []

    class _StubQueue:
        def enqueue_many(self, items):
            enqueue_calls.append(items)
            # Match the real queue's return shape:
            # {'enqueued': N, 'already_queued': M, 'total': K}
            return {'enqueued': len(items), 'already_queued': 0, 'total': len(items)}

    import core.reorganize_queue as queue_mod
    monkeypatch.setattr(queue_mod, 'get_queue', lambda: _StubQueue())

    ctx = make_context(db=db, dry_run=False)
    job = LibraryReorganizeJob()
    result = job.scan(ctx)

    assert len(enqueue_calls) == 1
    queued = enqueue_calls[0]
    assert {q['album_id'] for q in queued} == {'A1', 'A2'}
    assert {q['source'] for q in queued} == {'spotify', 'deezer'}
    assert result.auto_fixed == 2
    # Apply mode does NOT emit findings — it enqueues for actual move.
    assert result.findings_created == 0


def test_scan_only_iterates_albums_for_active_server(make_context, monkeypatch):
    """Pin: multi-server users (Plex + Jellyfin etc) — the job only
    iterates albums on the ACTIVE server. Inactive server's rows are
    skipped so we don't move files at paths the user can't see in
    the artist-detail UI."""
    db = _FakeDB(rows_by_server={
        'plex': [_make_album_row(id_='plex_album_1', title='Plex-only Album')],
        'jellyfin': [
            _make_album_row(id_='jelly_album_1', title='Jelly Album 1'),
            _make_album_row(id_='jelly_album_2', title='Jelly Album 2'),
        ],
    })
    cm = _FakeConfigManager(active_server='jellyfin')
    ctx = make_context(db=db, cm=cm)

    seen_album_ids = []

    from core import library_reorganize as core_lr

    def _track_calls(*, album_id, **kwargs):
        seen_album_ids.append(album_id)
        return {'success': False, 'status': 'no_album', 'tracks': []}
    monkeypatch.setattr(core_lr, 'preview_album_reorganize', _track_calls)

    job = LibraryReorganizeJob()
    job.scan(ctx)

    # Only Jellyfin's two albums were processed; the Plex album was
    # filtered out by the SQL active-server clause.
    assert sorted(seen_album_ids) == ['jelly_album_1', 'jelly_album_2']


def test_estimate_scope_returns_album_count(monkeypatch):
    """Pin: ``estimate_scope`` returns the DB album count (matches
    what scan iterates over)."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    db = MagicMock()
    db._get_connection.return_value = conn

    cm = _FakeConfigManager()
    ctx = JobContext(
        db=db, transfer_folder='/tmp', config_manager=cm,
    )

    job = LibraryReorganizeJob()
    assert job.estimate_scope(ctx) == 42
