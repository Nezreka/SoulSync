"""Offline acceptance: shared snapshots, findings, edition references and writes."""

import json
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mutagen.flac import FLAC

from core.library2.schema import ensure_library_v2_schema
from core.library2.tag_cache import persist_tag_cache, read_tag_snapshot
from core.library2.validation import finding_summary, finding_verified
from tests.lib2_seed import RowDb


@pytest.fixture
def library(tmp_path, monkeypatch):
    path = tmp_path / '07 - Track 7.flac'
    path.write_bytes(b'fLaC\x80\x00\x00\x22\x00\x10\x00\x10' + b'\x00' * 6 + b'\x0a\xc4\x42\xf0' + b'\x00' * 20)
    audio = FLAC(path)
    for key, value in {'title': 'Track 7', 'artist': 'Artist', 'albumartist': 'Artist', 'album': 'Album',
                       'tracknumber': '7', 'tracktotal': '13', 'discnumber': '1', 'disctotal': '1', 'date': '2026', 'genre': 'Rock'}.items():
        audio[key] = [value]
    audio.save()
    config = SimpleNamespace(get=lambda key, default=None: {'metadata_enhancement.embed_album_art': False,
                                                          'metadata_enhancement.cover_art_download': False}.get(key, default))
    monkeypatch.setattr('core.metadata.common.get_config_manager', lambda: config)
    db = RowDb(str(tmp_path / 'library.db'))
    with closing(db._get_connection()) as conn, conn:
        ensure_library_v2_schema(conn)
        conn.execute("INSERT INTO lib2_artists(id,name) VALUES(1,'Artist')")
        conn.execute("INSERT INTO lib2_albums(id,primary_artist_id,title,year,genres,image_url,expected_track_count) VALUES(1,1,'Album',2026,'[\"Rock\"]','https://example.test/cover',13)")
        conn.executemany("INSERT INTO lib2_tracks(id,album_id,title,track_number,disc_number) VALUES(?,1,?, ?,1)", [(i, f'Track {i}', i) for i in range(1, 14)])
        conn.execute("INSERT INTO lib2_track_files(id,track_id,path,format) VALUES(1,7,?,'flac')", (str(path),))
        conn.execute("INSERT INTO lib2_release_editions(id,release_group_id,track_count,disc_count) VALUES(1,1,13,1)")
        conn.executemany('INSERT INTO lib2_recordings(id,title) VALUES(?,?)', [(i, f'Track {i}') for i in range(1, 14)])
        conn.executemany('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) VALUES(1,?,?,?,1)', [(i, i, i) for i in range(1, 14)])
        conn.execute("CREATE TABLE repair_findings(id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, severity TEXT, status TEXT DEFAULT 'pending', entity_type TEXT, entity_id TEXT, file_path TEXT, title TEXT, description TEXT, details_json TEXT, user_action TEXT, resolved_at TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP, last_error TEXT, fix_claimed_at TEXT)")
    return db, path, config


def observe(library, tags=None):
    db, path, cfg = library
    with closing(db._get_connection()) as conn, conn:
        persist_tag_cache(conn, 1, tags if tags is not None else read_tag_snapshot(str(path)), cfg)
        return dict(conn.execute('SELECT * FROM lib2_track_files WHERE id=1').fetchone())


def test_refresh_persists_mismatches_without_replacing_reference(library):
    db, path, _ = library
    audio = FLAC(path)
    audio['tracknumber'], audio['tracktotal'] = ['3'], ['1']
    audio.save()
    row = observe(library)
    assert {'track_number', 'total_tracks'} <= set(json.loads(row['metadata_gaps_json']))
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT track_number FROM lib2_tracks WHERE id=7').fetchone()[0] == 7
        assert len(finding_summary(conn, row)) == 1


def test_external_correction_resolves_only_after_positive_read(library):
    db, path, _ = library
    tags = read_tag_snapshot(str(path))
    observe(library, {**tags, 'track_number': 3})
    observe(library, {'error': 'Unreadable'})
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'pending'
    assert json.loads(observe(library)['metadata_gaps_json']) == []
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'resolved'


def test_settings_disable_cover_requirements(library):
    row = observe(library)
    validation = json.loads(row['tags_json'])['_validation']
    assert validation['status'] == 'correct'
    assert validation['checks']['cover'] == validation['checks']['artwork_sidecar'] == 'not_required'


def test_cached_art_is_not_a_database_image(library, monkeypatch):
    db, _, _ = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute("UPDATE lib2_albums SET image_url=NULL")
    monkeypatch.setattr('core.library2.retag._album_cover_source', lambda *args: '/cached/cover.jpg')
    assert json.loads(observe(library)['tags_json'])['_validation']['checks']['artwork_database'] == 'missing'


def test_import_uses_native_edition_and_reads_final_file(library, monkeypatch):
    from core.library2.validation import import_reference, refresh_imported_metadata, metadata_states
    db, path, _ = library
    monkeypatch.setattr('database.music_database.get_database', lambda: db)
    reference = import_reference({'lib2_entity': {'track_id': 7}})
    assert (reference['track_number'], reference['track_count'], reference['total_discs']) == (7, 13, 1)
    assert refresh_imported_metadata(db, [str(path)]) == 'correct'
    assert metadata_states(db, [str(path)]) == {str(path): 'correct'}
    with closing(db._get_connection()) as conn:
        assert json.loads(conn.execute('SELECT tags_json FROM lib2_track_files').fetchone()[0])['_validation']['source'] == 'import'


@pytest.mark.parametrize('readable', [True, False])
def test_cover_tool_retires_only_positively_verified_findings(library, monkeypatch, readable):
    from core.repair_jobs.base import JobContext
    from core.repair_jobs.missing_cover_art import MissingCoverArtJob
    db, path, cfg = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute("INSERT INTO repair_findings(job_id,finding_type,entity_type,entity_id,file_path,title,details_json) VALUES('missing_cover_art','missing_cover_art','album','lib2:1',?,'Missing art','{}')", (str(path),))
    if not readable:
        monkeypatch.setattr('core.library2.tag_cache.read_tag_snapshot', lambda p: {'error': 'Unreadable'})
    result = MissingCoverArtJob().scan(JobContext(db=db, config_manager=cfg, transfer_folder=str(path.parent)))
    assert result.errors == int(not readable)
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == ('resolved' if readable else 'pending')


def test_ambiguous_edition_preserves_pending_finding(library):
    db, path, _ = library
    observe(library, {**read_tag_snapshot(str(path)), 'track_number': 3})
    with closing(db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO lib2_release_editions(id,release_group_id) VALUES(2,1)')
        conn.execute('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number) VALUES(2,7,7,3)')
    row = observe(library)
    assert json.loads(row['tags_json'])['_validation']['checks']['edition'] == 'unknown'
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'pending'


def test_single_edition_overrides_group_position(library):
    db, _, _ = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute('UPDATE lib2_tracks SET track_number=3 WHERE id=7')
    assert json.loads(observe(library)['tags_json'])['_validation']['checks']['track_number'] == 'correct'


@pytest.mark.parametrize('manual', [False, True])
def test_retag_and_organize_previews_share_edition_and_manual_reference(library, manual):
    from core.library2.metadata_overrides import set_field_override
    from core.library2.reorganize_plan import plan_album_reorganize
    from core.library2.retag import tag_preview, track_contexts
    db, path, _ = library
    before, seen = path.read_bytes(), []
    with closing(db._get_connection()) as conn, conn:
        conn.execute('UPDATE lib2_tracks SET track_number=3 WHERE id=7')
        conn.execute("UPDATE lib2_release_editions SET title='Album Deluxe'")
        if manual:
            set_field_override(conn, entity_type='track', entity_id=7, field_name='track_number', value=9)
        preview = tag_preview(track_contexts(conn, [7]))[0]
        def build(context, artist, album, extension, **kwargs):
            seen.append(context)
            return str(path.parent / f"{context['track_info']['track_number']:02d} - Song{extension}"), True
        plan = plan_album_reorganize(conn, 1, build_final_path_fn=build, transfer_dir=str(path.parent), resolve_file_path_fn=lambda p: p)
    assert preview['track_number'] == plan['tracks'][0]['track_number'] == (9 if manual else 7)
    assert preview['album_title'] == seen[0]['spotify_album']['name'] == 'Album Deluxe'
    assert path.read_bytes() == before


def test_organize_does_not_guess_an_ambiguous_edition_position(library):
    from core.library2.reorganize_plan import plan_album_reorganize
    db, path, _ = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO lib2_release_editions(id,release_group_id) VALUES(2,1)')
        conn.execute('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number) VALUES(2,7,7,3)')
        def fail(*args, **kwargs):
            raise AssertionError('Ambiguous reference must not reach the path builder')
        plan = plan_album_reorganize(conn, 1, build_final_path_fn=fail, transfer_dir=str(path.parent), resolve_file_path_fn=lambda p: p)
    assert not plan['tracks'][0]['matched'] and not plan['tracks'][0]['new_path_abs']


def test_unknown_reference_total_does_not_retire_an_old_total_finding(library):
    db, path, _ = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute('UPDATE lib2_release_editions SET track_count=NULL')
        conn.execute("INSERT INTO repair_findings(job_id,finding_type,entity_type,entity_id,file_path,title,details_json) VALUES('track_number_repair','track_number_mismatch','track','lib2:7',?,'Wrong total','{\"total_tracks\":13}')", (str(path),))
    observe(library)
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'pending'


def test_retag_fixes_separate_total_and_verifies_it(library):
    from core.library2.retag import write_tags
    db, path, _ = library
    audio = FLAC(path)
    audio['tracktotal'] = ['1']
    audio.save()
    observe(library)
    assert write_tags(db, [7], embed_cover=False)['written'] == 1
    assert FLAC(path)['tracktotal'] == ['13']
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'resolved'


def test_findings_are_visible_only_in_their_own_library(library):
    db, _, _ = library
    row = observe(library)
    with closing(db._get_connection()) as conn, conn:
        conn.execute("INSERT INTO repair_findings(job_id,finding_type,entity_type,entity_id,title,details_json) VALUES('acoustid_scanner','acoustid_mismatch','track','lib2:7','Wrong recording','{\"library_owner_id\":2}')")
        assert finding_summary(conn, row) == []
        conn.execute("UPDATE repair_findings SET details_json='{\"library_owner_id\":1}'")
        assert finding_summary(conn, row)[0]['category'] == 'check'


def test_failed_atomic_write_and_verification_report_failure(library):
    from core.metadata import enrichment
    _, path, cfg = library
    metadata = {'title': 'New Title', 'artist': 'Artist', 'album': 'Album', 'album_artist': 'Artist', 'track_number': 7, 'total_tracks': 13}
    with patch.object(enrichment, 'get_config_manager', return_value=cfg), patch.object(enrichment, 'extract_source_metadata', return_value=metadata), patch.object(enrichment, 'embed_source_ids'), patch.object(enrichment, 'save_audio_file', return_value=False):
        assert not enrichment.enhance_file_metadata(str(path), {}, {}, {})
    assert FLAC(path)['title'] == ['Track 7']
    with patch.object(enrichment, 'get_config_manager', return_value=cfg), patch.object(enrichment, 'extract_source_metadata', return_value=metadata), patch.object(enrichment, 'embed_source_ids'), patch.object(enrichment, 'verify_metadata_written', return_value=False):
        assert not enrichment.enhance_file_metadata(str(path), {}, {}, {})


def test_unknown_total_and_owning_edition(library):
    from core.metadata import source
    from core.repair_jobs.track_number_repair import _api_tracks_for_subject
    _, _, cfg = library
    with patch.object(source, 'get_config_manager', return_value=cfg):
        metadata = source.extract_source_metadata({'source': 'spotify', 'album': {'name': 'Album'}, 'original_search_result': {'title': 'Track 7', 'artist': 'Artist'}}, {'name': 'Artist'}, {'is_album': True, 'album_name': 'Album', 'track_number': 7})
    assert metadata['total_tracks'] is None
    edition = [{'track_number': 7}]
    assert _api_tracks_for_subject({'track_id': 7}, [{'track_number': 3}], {1: edition}, {7: [1]}) == edition


def test_id3_and_opus_read_separate_totals():
    from core.tag_writer import read_number_pair
    from mutagen.id3 import ID3, TRCK
    id3 = ID3()
    id3.add(TRCK(encoding=3, text=['7/13']))
    assert read_number_pair(SimpleNamespace(tags=id3)) == (7, 13)
    audio = type('OggOpus', (dict,), {})({'tracknumber': ['7'], 'tracktotal': ['13']})
    audio.tags = audio
    assert read_number_pair(audio) == (7, 13)


@pytest.mark.parametrize('write', [False, True])
def test_worker_resolves_only_after_the_file_actually_changes(library, write, monkeypatch):
    from core.repair_worker import RepairWorker
    db, path, cfg = library
    audio = FLAC(path)
    audio['tracknumber'] = ['3']
    audio.save()
    observe(library)
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = db, cfg, str(path.parent)
    if not write:
        monkeypatch.setattr(worker, '_execute_fix', lambda *args: {'success': True})
    with closing(db._get_connection()) as conn:
        fid = conn.execute('SELECT id FROM repair_findings').fetchone()[0]
    result = worker.fix_finding(fid)
    assert result['success'] is write
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings WHERE id=?', (fid,)).fetchone()[0] == ('resolved' if write else 'pending')


def test_metadata_click_dispatches_persisted_finding_to_the_worker(library, monkeypatch):
    from flask import Flask
    from api.library_v2 import register_library_v2_routes
    db, path, cfg = library
    observe(library, {**read_tag_snapshot(str(path)), 'track_number': 3})
    fixed = []
    worker = SimpleNamespace(fix_finding=lambda fid: fixed.append(fid) or {'success': True})
    app = Flask(__name__)
    register_library_v2_routes(app, get_database=lambda: db, config_get=cfg.get, config_manager=cfg, repair_worker_getter=lambda: worker)
    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr('threading.Thread', InlineThread)
    response = app.test_client().post('/api/library/v2/tracks/7/fill-tag-gaps')
    assert response.status_code == 200
    assert fixed == [1]


def test_retag_tool_persists_and_rechecks_without_writing_files(library):
    from core.repair_jobs.base import JobContext
    from core.repair_jobs.library_retag import LibraryRetagJob
    db, path, cfg = library
    audio = FLAC(path)
    audio['tracknumber'] = ['3']
    audio.save()
    before, findings = path.read_bytes(), []
    context = JobContext(db=db, config_manager=cfg, transfer_folder=str(path.parent), create_finding=lambda **f: findings.append(f) or True)
    assert not LibraryRetagJob().scan(context).errors
    assert path.read_bytes() == before
    assert findings[0]['details']['validation']['checks']['track_number'] == 'mismatch'
    audio['tracknumber'] = ['7']
    audio.save()
    assert not LibraryRetagJob().scan(context).errors
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings').fetchone()[0] == 'resolved'


def test_shared_position_across_editions_is_not_ambiguous(library):
    from core.library2.reorganize_plan import plan_album_reorganize
    from core.library2.retag import track_contexts
    db, path, _ = library
    with closing(db._get_connection()) as conn, conn:
        conn.execute('INSERT INTO lib2_release_editions(id,release_group_id,track_count,disc_count) VALUES(2,1,16,1)')
        conn.execute('INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) VALUES(2,7,7,7,1)')
        reference = track_contexts(conn, [7])[0]['db_data']
        plan = plan_album_reorganize(conn, 1, build_final_path_fn=lambda c, *a, **k: (str(path), True),
                                     transfer_dir=str(path.parent), resolve_file_path_fn=lambda p: p)
    assert (reference['track_number'], reference['disc_number'], reference['edition_status']) == (7, 1, 'correct')
    assert reference['track_count'] is None
    assert plan['tracks'][0]['matched']
    assert json.loads(observe(library)['tags_json'])['_validation']['checks']['track_number'] == 'correct'


def test_revalidation_keeps_retag_finding_details(library):
    db, path, _ = library
    observe(library, {**read_tag_snapshot(str(path)), 'track_number': 3})
    with closing(db._get_connection()) as conn, conn:
        details = json.loads(conn.execute('SELECT details_json FROM repair_findings').fetchone()[0])
        details.update(has_manual_conflict=True, manual_fields=['Title'], diff=[{'file_key': 'title'}], library_owner_id=1)
        conn.execute('UPDATE repair_findings SET details_json=?', (json.dumps(details),))
    observe(library, {**read_tag_snapshot(str(path)), 'track_number': 4})
    with closing(db._get_connection()) as conn:
        details = json.loads(conn.execute('SELECT details_json FROM repair_findings').fetchone()[0])
    assert details['has_manual_conflict'] and details['manual_fields'] == ['Title'] and details['library_owner_id'] == 1
    assert details['diff'] == [{'file_key': 'title'}] and 'track_number' in details['required_fields']
