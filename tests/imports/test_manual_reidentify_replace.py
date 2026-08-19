"""Re-identify's "replace the original" must work on a MANUAL import too.

Reported by Urethra Franklin: ticked "Replace the original file", the file was
staged as expected, but importing it by hand from the Import page left the old
file and its library row in place.

The rematch hint was consumed in exactly ONE place — the auto-import worker.
Import the staged file yourself and nothing ever looked for the hint, so the
checkbox silently did nothing. They were not doing anything wrong.

Only the REPLACE half of the hint matters on this path: manual import means
the user picked the release themselves in the UI, so the identification half
is already handled.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from core.imports.routes import finalize_manual_rematch_replace


@pytest.fixture()
def db(monkeypatch, tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE rematch_hints (
        id INTEGER PRIMARY KEY, staged_path TEXT, content_hash TEXT, source TEXT,
        isrc TEXT, track_id TEXT, album_id TEXT, artist_id TEXT, track_title TEXT,
        album_name TEXT, artist_name TEXT, album_type TEXT, track_number INTEGER,
        disc_number INTEGER, replace_track_id INTEGER, exempt_dedup INTEGER,
        status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consumed_at TIMESTAMP)""")
    conn.commit()

    class _KeepOpen:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass

    monkeypatch.setattr('database.music_database.get_database',
                        lambda *a, **k: SimpleNamespace(_get_connection=lambda: _KeepOpen(conn)))
    return conn


@pytest.fixture()
def runtime():
    logged = []
    return SimpleNamespace(
        logger=SimpleNamespace(
            info=lambda *a, **k: logged.append(a),
            debug=lambda *a, **k: None,
            warning=lambda *a, **k: logged.append(a),
        ),
        _logged=logged,
    )


def _hint(db, staged_path, replace_track_id=99):
    db.execute(
        "INSERT INTO rematch_hints (staged_path, source, replace_track_id, exempt_dedup) "
        "VALUES (?, 'spotify', ?, 1)", (staged_path, replace_track_id))
    db.commit()


def _pending(db):
    return db.execute("SELECT COUNT(*) FROM rematch_hints WHERE status='pending'").fetchone()[0]


def test_a_manual_import_now_deletes_the_replaced_track(db, runtime, monkeypatch, tmp_path):
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    deleted = []
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: deleted.append(track_id))

    finalize_manual_rematch_replace(runtime, staged, {'_final_path': '/library/new/Song.flac'})

    assert deleted == [99], 'the replaced library track was never deleted'


def test_the_hint_is_consumed_so_it_is_single_use(db, runtime, monkeypatch, tmp_path):
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda *a, **k: None)

    finalize_manual_rematch_replace(runtime, staged, {})

    assert _pending(db) == 0


def test_the_new_path_is_passed_so_the_same_home_guard_works(db, runtime, monkeypatch, tmp_path):
    """Never delete a file the import just wrote at the old location."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    seen = {}
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: seen.update(paths=new_paths))

    finalize_manual_rematch_replace(runtime, staged, {'_final_path': '/library/new/Song.flac'})

    assert seen['paths'] == ['/library/new/Song.flac']


def test_the_canonical_processed_path_wins(db, runtime, monkeypatch, tmp_path):
    """`_final_processed_path` is what side_effects.py and the auto-import
    worker both read FIRST — post-processing can move a file after
    `_final_path` was recorded, so the later key is the true landing site."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    seen = {}
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: seen.update(paths=new_paths))

    finalize_manual_rematch_replace(runtime, staged, {
        '_final_path': '/library/stale/Song.flac',
        '_final_processed_path': '/library/real/Song.flac',
    })

    assert seen['paths'] == ['/library/real/Song.flac']


def test_it_refuses_to_delete_when_it_cannot_tell_where_the_file_landed(db, runtime, monkeypatch, tmp_path):
    """The same-home guard needs the landing path. Without it, a re-identify
    onto a release that resolves to the SAME path would delete the file that IS
    the re-imported track. Leaving a duplicate is recoverable; deleting the
    only copy is not."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    deleted = []
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: deleted.append(track_id))

    finalize_manual_rematch_replace(runtime, staged, {})

    assert deleted == [], 'deleted the original without knowing where the new file landed'
    assert _pending(db) == 0, 'the hint must still be consumed so it cannot fire on a stale path'


def test_a_moved_staged_file_still_resolves_its_hint(db, runtime, monkeypatch, tmp_path):
    """The import MOVES the file out of staging before this runs, so the file
    is gone by the time we look. Path matching must not depend on it existing —
    otherwise the fix is a no-op in production while every test passes."""
    staged = str(tmp_path / 'Song.flac')
    _hint(db, staged)          # note: file deliberately never created

    deleted = []
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: deleted.append(track_id))

    finalize_manual_rematch_replace(runtime, staged, {'_final_processed_path': '/library/new/Song.flac'})

    assert deleted == [99]


def test_a_hint_without_replace_only_consumes(db, runtime, monkeypatch, tmp_path):
    """"Replace" unticked means keep the original — deleting it would be data
    loss the user explicitly declined."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged, replace_track_id=None)

    deleted = []
    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track',
                        lambda cursor, track_id, new_paths=None, **kw: deleted.append(track_id))

    finalize_manual_rematch_replace(runtime, staged, {})

    assert deleted == []
    assert _pending(db) == 0


def test_an_ordinary_staging_file_is_untouched(db, runtime, tmp_path):
    """The overwhelmingly common case: no hint, silent no-op, no errors."""
    staged = str(tmp_path / 'Ordinary.flac')
    open(staged, 'w').close()

    finalize_manual_rematch_replace(runtime, staged, {})   # must not raise


def test_a_cleanup_failure_never_fails_the_import(db, runtime, monkeypatch, tmp_path):
    """The import already succeeded. A cleanup problem is logged, not raised —
    otherwise a successful import reports as a failure."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    def _boom(*a, **k):
        raise RuntimeError('disk on fire')

    monkeypatch.setattr('core.imports.rematch_hints.delete_replaced_track', _boom)

    finalize_manual_rematch_replace(runtime, staged, {})   # must not raise


# ── the call site ────────────────────────────────────────────────────────────
#
# The tests above exercise the finaliser directly. This one proves the manual
# import path actually CALLS it — deleting the call site passed every test
# above, which is the whole failure mode being fixed here: a correct helper
# nothing invokes.

def test_a_manual_single_import_invokes_the_replace_finaliser(tmp_path, monkeypatch):
    from core.imports import routes as import_routes
    from core.imports.routes import ImportRouteRuntime, process_single_import_file

    audio_file = tmp_path / 'Artist - Song.flac'
    audio_file.write_bytes(b'')

    called = {}
    monkeypatch.setattr(
        import_routes, 'finalize_manual_rematch_replace',
        lambda runtime, path, context: called.update(path=path, context=context))

    runtime = ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(tmp_path)],
        parse_filename_metadata=lambda filename: {'title': 'Song', 'artist': 'Artist'},
        get_single_track_import_context=lambda title, artist, **kwargs: {
            'source': 'deezer',
            'context': {'track': {'name': title}, 'artist': {'name': artist}},
        },
        normalize_import_context=lambda context: context,
        get_import_context_artist=lambda context: context['artist'],
        get_import_track_info=lambda context: context['track'],
        post_process_matched_download=lambda key, context, path: None,
        logger=type('L', (), {'info': lambda *a, **k: None,
                              'debug': lambda *a, **k: None,
                              'warning': lambda *a, **k: None,
                              'error': lambda *a, **k: None})(),
    )

    outcome = process_single_import_file(
        runtime, {'full_path': str(audio_file), 'filename': audio_file.name})

    assert outcome[0] == 'ok'
    assert called.get('path') == str(audio_file), 'the finaliser was never called'


def test_a_rejected_import_does_not_delete_the_original(tmp_path, monkeypatch):
    """Quarantine/rejection returns early. Deleting the original there would
    destroy the only copy — the replacement never landed."""
    from core.imports import routes as import_routes
    from core.imports.routes import ImportRouteRuntime, process_single_import_file

    audio_file = tmp_path / 'Artist - Song.flac'
    audio_file.write_bytes(b'')

    called = {}
    monkeypatch.setattr(import_routes, 'finalize_manual_rematch_replace',
                        lambda *a, **k: called.update(ran=True))
    monkeypatch.setattr(import_routes, 'import_rejection_reason',
                        lambda context: 'failed the silence check')

    runtime = ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(tmp_path)],
        parse_filename_metadata=lambda filename: {'title': 'Song', 'artist': 'Artist'},
        get_single_track_import_context=lambda title, artist, **kwargs: {
            'source': 'deezer',
            'context': {'track': {'name': title}, 'artist': {'name': artist}},
        },
        normalize_import_context=lambda context: context,
        get_import_context_artist=lambda context: context['artist'],
        get_import_track_info=lambda context: context['track'],
        post_process_matched_download=lambda key, context, path: None,
        logger=type('L', (), {'info': lambda *a, **k: None,
                              'debug': lambda *a, **k: None,
                              'warning': lambda *a, **k: None,
                              'error': lambda *a, **k: None})(),
    )

    outcome = process_single_import_file(
        runtime, {'full_path': str(audio_file), 'filename': audio_file.name})

    assert outcome[0] == 'error'
    assert 'ran' not in called


def test_the_stored_path_is_resolved_before_unlinking(db, runtime, monkeypatch, tmp_path):
    """The DB stores the media-server's view of a path, which this process may
    not be able to open literally (Docker). The auto-import worker passes a
    resolve_fn for exactly this; without it the row is deleted and the FILE is
    orphaned."""
    staged = str(tmp_path / 'Song.flac')
    open(staged, 'w').close()
    _hint(db, staged)

    seen = {}
    monkeypatch.setattr(
        'core.imports.rematch_hints.delete_replaced_track',
        lambda cursor, track_id, resolve_fn=None, new_paths=None, **kw: seen.update(
            resolve_fn=resolve_fn))

    finalize_manual_rematch_replace(runtime, staged, {'_final_processed_path': '/library/new.flac'})

    assert callable(seen.get('resolve_fn')), 'no path resolver was passed'
