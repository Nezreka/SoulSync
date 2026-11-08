"""hand-tagged releases stay out of the maintenance jobs (Library v2).

"tag it yourself" lets a user type every tag for a bootleg or a live set no
service knows; the file lands in ``manual_metadata_files``. Every job below
would otherwise match the release to the studio album and retag, renumber,
rematch or offer to delete it.

Upstream guards each legacy job twice (a ``metadata_locked`` row filter and a
per-file path check). Library v2 has no lock column, so here the remembered
file is the one guard, applied to the subjects a job walks
(``core.repair_jobs.base.drop_hand_tagged``).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.repair_jobs import acoustid_scanner, library_retag, live_commentary_cleaner
from core.repair_jobs import metadata_gap_filler, missing_cover_art, track_number_repair
from core.repair_jobs.base import drop_hand_tagged, drop_hand_tagged_albums
from core.library2 import duplicate_review
from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track

LIVE = '/music/Pearl Jam/Live at Benaroya Hall/01 - Of the Girl.flac'
STUDIO = '/music/Pearl Jam/Binaural/01 - Of the Girl.flac'


def _db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(str(tmp_path / 'm.db'))


def test_drop_hand_tagged_keeps_everything_else(tmp_path):
    db = _db(tmp_path)
    db.record_manual_metadata_file(LIVE)
    ctx = SimpleNamespace(db=db)
    kept = drop_hand_tagged(ctx, [{'path': LIVE}, {'path': STUDIO}, {'path': None}])
    assert [s['path'] for s in kept] == [STUDIO, None]


def test_drop_hand_tagged_matches_under_another_mount(tmp_path):
    db = _db(tmp_path)
    db.record_manual_metadata_file('/app/Transfer/Pearl Jam/Live at Benaroya Hall/01 - Of the Girl.flac')
    kept = drop_hand_tagged(SimpleNamespace(db=db), [{'rep_path': LIVE}], path_key='rep_path')
    assert kept == []


def test_nothing_hand_tagged_is_a_no_op(tmp_path):
    subjects = [{'path': LIVE}]
    assert drop_hand_tagged(SimpleNamespace(db=_db(tmp_path)), subjects) == subjects
    assert drop_hand_tagged(SimpleNamespace(db=None), subjects) == subjects


def test_library_retag_never_offers_a_hand_tagged_file(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Pearl Jam', album='Live at Benaroya Hall',
                           title='Of the Girl', file_path=LIVE)
        seed_library_track(conn, artist='Pearl Jam', album='Binaural', title='Of the Girl',
                           album_server_id='al2', track_server_id='tr2', file_path=STUDIO)
        conn.commit()
    db.record_manual_metadata_file(LIVE)
    ctx = SimpleNamespace(db=db, config_manager=None, file_scope=None, scope_artist=None,
                          settings={})
    paths = {str(s['path']) for s in library_retag.LibraryRetagJob()._subjects(ctx)}
    assert STUDIO in paths
    assert LIVE not in paths


@pytest.mark.parametrize("module", [
    acoustid_scanner, library_retag, live_commentary_cleaner,
    metadata_gap_filler, missing_cover_art, track_number_repair,
    duplicate_review,  # the native duplicate detector's grouping
])
def test_every_guarded_job_is_wired(module):
    """Pins the wiring: each of these jobs reads the remembered files. A job that
    stops doing so silently starts proposing studio-release fixes again."""
    source = inspect.getsource(module)
    assert ('drop_hand_tagged(' in source or 'drop_hand_tagged_albums(' in source
            or 'is_hand_tagged_path(' in source)


def test_track_number_repair_leaves_an_album_with_one_hand_tagged_file_alone(tmp_path, monkeypatch):
    """3.6.1 skipped the whole album folder: renumbering the other files of a
    release the user typed against the service's tracklist splits it"""
    from core.repair_jobs.base import JobContext
    db = _db(tmp_path)
    db.record_manual_metadata_file(LIVE)
    live_other = LIVE.replace('01 - Of the Girl', '02 - Corduroy')
    subjects = [
        {'album_id': 1, 'path': LIVE, 'artist_name': 'Pearl Jam', 'file_id': 1, 'track_id': 1},
        {'album_id': 1, 'path': live_other, 'artist_name': 'Pearl Jam', 'file_id': 2, 'track_id': 2},
        {'album_id': 2, 'path': STUDIO, 'artist_name': 'Pearl Jam', 'file_id': 3, 'track_id': 3},
    ]
    monkeypatch.setattr(track_number_repair, 'active_file_subjects', lambda *_a, **_k: subjects)
    asked = []
    monkeypatch.setattr('core.library2.completeness.resolve_tracklist',
                        lambda _cfg, _conn, album_id: asked.append(album_id))
    ctx = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=None)

    track_number_repair.TrackNumberRepairJob().scan(ctx)

    assert asked == [2]


def test_one_hand_tagged_file_locks_its_album(tmp_path):
    """3.6.1 locked the album when one of its files was hand-tagged, so a
    retag, the live/commentary cleaner or a bpm backfill left the rest of it
    alone too. a scoped run judges the album from the unscoped files."""
    db = _db(tmp_path)
    db.record_manual_metadata_file(LIVE)
    live_other = LIVE.replace('01 - Of the Girl', '02 - Corduroy')
    everything = [{'album_id': 1, 'path': LIVE}, {'album_id': 1, 'path': live_other},
                  {'album_id': 2, 'path': STUDIO}]
    ctx = SimpleNamespace(db=db)
    assert [s['path'] for s in drop_hand_tagged_albums(ctx, everything)] == [STUDIO]
    # an artist-scoped run that only holds the other live file still skips it
    scoped = [{'album_id': 1, 'path': live_other}]
    assert drop_hand_tagged_albums(ctx, scoped, everything) == []


def test_library_retag_leaves_an_album_with_one_hand_tagged_file_alone(tmp_path):
    db = _db(tmp_path)
    live_other = LIVE.replace('01 - Of the Girl', '02 - Corduroy')
    with db._get_connection() as conn:
        seed_library_track(conn, artist='Pearl Jam', album='Live at Benaroya Hall',
                           title='Of the Girl', file_path=LIVE)
        seed_library_track(conn, artist='Pearl Jam', album='Live at Benaroya Hall',
                           title='Corduroy', track_server_id='tr3', file_path=live_other)
        seed_library_track(conn, artist='Pearl Jam', album='Binaural', title='Of the Girl',
                           album_server_id='al2', track_server_id='tr2', file_path=STUDIO)
        conn.commit()
    db.record_manual_metadata_file(LIVE)
    ctx = SimpleNamespace(db=db, config_manager=None, file_scope=None, scope_artist=None,
                          settings={})
    paths = {str(s['path']) for s in library_retag.LibraryRetagJob()._subjects(ctx)}
    assert paths == {STUDIO}
