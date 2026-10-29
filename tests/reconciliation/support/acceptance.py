"""Real scan fixtures shared by contracts and lifecycle tests.

Only external catalogue/audio responses are controlled. The scanner, database,
decision policy, tag files and queue projection are production implementations.
"""
from copy import deepcopy
from types import SimpleNamespace

from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC

from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.audio import _Acoustid, _minimal_flac

RELEASE = '11111111-1111-4111-8111-111111111111'


OTHER_RELEASE = '22222222-2222-4222-8222-222222222222'


ARTIST = 'Acceptance Artist'


ALBUM = 'Northbound' 


def recording_id(index):
    return f'00000000-0000-4000-8000-{index:012d}'


def build_album(tmp_path, monkeypatch, scenario):
    import core.repair_jobs.library_reconciliation as job

    root = tmp_path / 'music'
    folder = root / ARTIST / ALBUM
    folder.mkdir(parents=True)
    slots = [
        {'id': recording_id(i), 'name': title, 'artists': [{'name': ARTIST}],
         'track_number': i, 'disc_number': 1, 'duration_ms': 190000 + i * 10000}
        for i, title in enumerate(('Home', 'Open Water'), 1)
    ]
    local_titles = ['Home', 'Open Water']
    if scenario in ('compatible_qualifier', 'different_performance'):
        local_titles[0] = 'Home (live at Wembley, 12 July 1986)'
        slots[0]['name'] = ('Home (live at Wembley, London, 1986-07-12)'
                            if scenario == 'compatible_qualifier'
                            else 'Home (live at Wembley, 1986-07-13)')
    if scenario == 'repeated_recording':
        slots.append({**slots[0], 'track_number': 3})
        local_titles.append(local_titles[0])
    if scenario == 'partial':
        slots.append({**slots[1], 'id': recording_id(3), 'name': 'Afterglow',
                      'track_number': 3, 'duration_ms': 220000})
        local_titles = local_titles[:1]

    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists(id,name,server_source) VALUES(1,?,'test')", (ARTIST,))
        conn.execute("""INSERT INTO albums(id,title,artist_id,server_source,
            canonical_source,canonical_album_id) VALUES(10,?,1,'test','musicbrainz',?)""",
                     (ALBUM, RELEASE))
    db.set_album_canonical(10, 'musicbrainz', RELEASE, score=1, locked=True)
    paths, matches, durations = [], {}, {}
    for i, title in enumerate(local_titles, 1):
        path = folder / f'{i:02d} - {title}.flac'
        tags = {'title': title, 'artist': ARTIST, 'albumartist': ARTIST,
                'album': ALBUM, 'tracknumber': str(i), 'discnumber': '1',
                'musicbrainz_albumid': RELEASE}
        if scenario == 'metadata_only':
            tags['musicbrainz_trackid'] = slots[i - 1]['id']
            if i == 2:
                tags.pop('album')
        if scenario == 'missing_metadata' and i == 2:
            tags.pop('album')
            tags.pop('musicbrainz_albumid')
        if scenario in ('contradictory_metadata', 'numbering_metadata_conflict') and i == 2:
            tags.update(album='Northbound (Deluxe)', musicbrainz_albumid=OTHER_RELEASE)
            if scenario == 'numbering_metadata_conflict':
                tags['tracknumber'] = '7'
        _minimal_flac(path, tags)
        paths.append(path)
        slot = slots[i - 1]
        matches[path.name] = {'mbid': slot['id'], 'title': title, 'artist': ARTIST, 'score': .99}
        durations[str(path)] = slot['duration_ms']
        if scenario == 'metadata_only':
            matches[path.name] = []
        if scenario == 'recording_conflict' and i == 1:
            matches[path.name] = {'mbid': recording_id(9), 'title': 'Unrelated Song',
                                  'artist': 'Another Artist', 'score': .99}
        with db._get_connection() as conn:
            conn.execute("""INSERT INTO tracks(id,album_id,artist_id,title,track_number,
                disc_number,duration,file_path) VALUES(?,10,1,?,?,1,?,?)""",
                         (i, title, i, slot['duration_ms'], str(path)))
    if scenario in ('duplicate', 'companion', 'unmatched_companion'):
        path = paths[0].with_suffix('.mp3' if 'companion' in scenario else '.copy.flac')
        if 'companion' in scenario:
            # Parseable MPEG headers plus real ID3 tags; fingerprinting is
            # controlled, as it is for the frame-less FLAC fixtures.
            path.write_bytes((b'\xff\xfb\x90\x00' + bytes(413)) * 4)
            tags = EasyID3()
            for key, value in FLAC(str(paths[0])).items():
                tags[key] = value
            tags.save(str(path))
        else:
            path.write_bytes(paths[0].read_bytes())
        matches[path.name] = deepcopy(matches[paths[0].name])
        durations[str(path)] = durations[str(paths[0])]
        paths.append(path)
        with db._get_connection() as conn:
            conn.execute("""INSERT INTO tracks(id,album_id,artist_id,title,track_number,
                disc_number,duration,file_path) VALUES(3,10,1,'Home',1,1,200000,?)""", (str(path),))
    if scenario == 'unmatched_companion':
        slots[0]['name'] = 'Different Song'
        slots[0]['id'] = recording_id(8)

    client = _Acoustid(matches)
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, '_schedule_album_refresh', lambda *a, **k: True)
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda self: client))
    monkeypatch.setattr(job, 'all_library_roots', lambda context: [str(root)])
    reader = job._read_file_tags
    monkeypatch.setattr(job, '_read_file_tags',
                        lambda path: {**reader(path), 'duration_ms': durations[str(path)]})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **k: deepcopy(slots))
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *a, **k: {
        'name': ALBUM, 'artists': [{'name': ARTIST}], 'musicbrainz_release_id': RELEASE,
        'country': 'GB', 'date': '2001-01-01', 'media': [{'format': 'Digital Media'}],
    })
    monkeypatch.setattr(job, '_search_release_candidates', lambda *a, **k: [])
    monkeypatch.setattr('core.reconciliation.selection._release_group_candidates', lambda *a, **k: [])
    config = SimpleNamespace(get=lambda key, default=None:
        True if key == 'lossy_copy.enabled' and 'companion' in scenario else default)
    context = JobContext(db=db, transfer_folder=str(root), config_manager=config,
                         acoustid_client=client, create_finding=worker._create_finding)
    return SimpleNamespace(db=db, worker=worker, context=context, paths=paths,
                           folder=folder, root=root, slots=slots, durations=durations)


def scan_case(album):
    result = LibraryReconciliationJob().scan(album.context)
    assert not result.errors
    with album.db._get_connection() as conn:
        case_id = conn.execute(
            "SELECT case_id FROM reconcile_album_plans WHERE state!='obsolete'").fetchone()[0]
    case = album.worker.get_review_case(case_id)
    assert case is not None
    return case
