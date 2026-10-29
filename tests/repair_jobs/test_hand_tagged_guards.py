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
from core.repair_jobs.base import drop_hand_tagged
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
])
def test_every_guarded_job_is_wired(module):
    """Pins the wiring: each of these jobs reads the remembered files. A job that
    stops doing so silently starts proposing studio-release fixes again."""
    source = inspect.getsource(module)
    assert 'drop_hand_tagged(' in source or 'is_hand_tagged_path(' in source
