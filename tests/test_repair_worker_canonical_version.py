"""Regression: applying a ``canonical_version`` finding did nothing.

The Resolve Canonical Album Versions job (#765) creates ``canonical_version``
findings in its dry run, but ``RepairWorker._execute_fix`` had no handler for
that finding type, so single-fix / Fix All returned
"No fix available for finding type: canonical_version" and pinned nothing.

``_fix_canonical_version`` applies the pin straight from the finding — the
per-finding equivalent of running the job with dry-run OFF. It writes an AUTO
pin (``locked=False``), matching ``resolve_and_store_canonical_for_album`` and
the Reorganizer, and must NOT clobber a manual/locked pin (#758).
"""

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'A', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) "
                     "VALUES (1, 'Beggars Banquet', 1, 'test')")
        conn.commit()
    w = RepairWorker(database=db)
    w._config_manager = None
    return db, w


def _finding_details(source='musicbrainz', release_id='mb-release-123', score=0.97):
    """Mirror the details a real canonical_version finding carries: note the
    resolver's release id lands on the ``album_id`` key (``{'album_id': local,
    **resolved}`` — the resolved release id wins)."""
    return {
        'album_id': release_id,
        'source': source,
        'score': score,
        'album_title': 'Beggars Banquet',
        'artist_name': 'The Rolling Stones',
    }


def test_apply_pins_canonical_as_auto(tmp_path):
    """Applying the finding pins the resolver's release, unlocked, so a later
    resolve can still self-heal it."""
    db, w = _worker(tmp_path)
    res = w._fix_canonical_version('album', '1', None, _finding_details())
    assert res['success'] is True, res

    pin = db.get_album_canonical(1)
    assert pin is not None
    assert pin['source'] == 'musicbrainz'
    assert pin['album_id'] == 'mb-release-123'
    assert round(pin['score'], 2) == 0.97
    assert pin['locked'] is False


def test_dispatch_no_longer_reports_no_fix(tmp_path):
    """The reported bug: _execute_fix used to fall through to 'No fix available
    for finding type: canonical_version'. It now routes to the handler."""
    db, w = _worker(tmp_path)
    res = w._execute_fix('canonical_version', 'album', '1', None, _finding_details())
    assert res['success'] is True, res
    assert 'No fix available' not in (res.get('error') or '')


def test_missing_source_or_release_is_rejected(tmp_path):
    """A malformed finding (no source/release id) is refused, not silently
    written as a null pin."""
    _db, w = _worker(tmp_path)
    res = w._fix_canonical_version('album', '1', None, {'album_title': 'x'})
    assert res['success'] is False
    assert 'canonical source/release id' in res['error']


def test_auto_apply_does_not_clobber_manual_lock(tmp_path):
    """A user's manual edition lock (#758) survives applying an auto finding —
    the finding reports failure instead of overwriting the locked pin."""
    db, w = _worker(tmp_path)
    # User manually pinned+locked the Deezer edition (as /api/library/manual-match does).
    db.set_album_canonical('1', 'deezer', 'dz-999', 1.0, locked=True)

    res = w._fix_canonical_version('album', '1', None, _finding_details())
    assert res['success'] is False
    assert 'locked' in res['error']

    pin = db.get_album_canonical(1)
    assert pin['source'] == 'deezer'
    assert pin['album_id'] == 'dz-999'
    assert pin['locked'] is True
