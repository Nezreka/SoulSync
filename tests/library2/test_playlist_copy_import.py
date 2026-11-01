"""Legacy playlist originals become retained file versions, never lost bytes."""

from pathlib import Path

import pytest

from core.library2.importer import import_legacy_library
from tests.library2.test_two_libraries import lib
from tests.library2.test_two_libraries_import import _legacy, _q


@pytest.mark.parametrize('own', [False, True])
def test_two_playlist_originals_are_versions_of_one_recording(lib, own):
    owner = lib.kim if own else None
    root = Path(lib.kim_root if own else lib.shared)
    paths = [root / name / 'A - Intro.flac' for name in ('Mix X', 'Mix Y')]
    for i, path in enumerate(paths):
        path.parent.mkdir(parents=True)
        path.write_bytes(f'original-{i}'.encode())
    _legacy(lib.db,
        artists=[('1', 'A', owner, 'a'*22), ('2', 'A', owner, 'a'*22)],
        albums=[('10','1','Record',2,'b'*22,owner,2020), ('20','2','Record',2,'b'*22,owner,2020)],
        tracks=[('100','10','1','Intro',1,str(paths[0]),'c'*22,0,owner,1),
                ('200','20','2','Intro',1,str(paths[1]),'c'*22,0,owner,1)])
    with lib.db._get_connection() as conn:
        for i, name in enumerate(('Mix X', 'Mix Y'), 1):
            playlist_id = conn.execute(
                "INSERT INTO mirrored_playlists(source,source_playlist_id,name,profile_id) "
                "VALUES('spotify',?,?,?)", (f'playlist-{i}',name,owner or 1)).lastrowid
            conn.execute("INSERT INTO mirrored_playlist_tracks(playlist_id,position,track_name,artist_name,source_track_id) "
                         "VALUES(?,1,'Intro','A',?)", (playlist_id,'c'*22))
        conn.commit()
    import_legacy_library(lib.db)
    assert _q(lib.db,"SELECT COUNT(*) FROM lib2_albums WHERE title='Record'") == [(1,)]
    assert _q(lib.db,"SELECT COUNT(*) FROM lib2_tracks WHERE title='Intro'") == [(1,)]
    files = _q(lib.db,'SELECT id,track_id,path FROM lib2_track_files ORDER BY path')
    assert {row[2] for row in files} == {str(p) for p in paths}
    assert len({row[1] for row in files}) == 1
    origins = _q(lib.db,'SELECT playlist_name FROM lib2_file_origins WHERE playlist_name IS NOT NULL ORDER BY playlist_name')
    assert origins == [('Mix X',),('Mix Y',)]
    before = files
    import_legacy_library(lib.db)
    assert _q(lib.db,'SELECT id,track_id,path FROM lib2_track_files ORDER BY path') == before
    assert [p.read_bytes() for p in paths] == [b'original-0',b'original-1']


def test_shared_physical_file_keeps_source_observations_without_extra_owners(lib):
    from tests.library2.test_upgrade_safety import _seed_pair
    _seed_pair(lib, same_path=True)
    import_legacy_library(lib.db)
    assert _q(lib.db,'SELECT COUNT(*) FROM lib2_track_files') == [(1,)]
    assert _q(lib.db,'SELECT DISTINCT owner_profile_id FROM lib2_track_files') == [(None,)]
    assert {r[0] for r in _q(lib.db,'SELECT owner_profile_id FROM lib2_file_origins')} == {None,lib.kim}
