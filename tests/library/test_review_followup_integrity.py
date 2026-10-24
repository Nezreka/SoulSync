"""State and file invariants behind the album-review followup fixes."""
import json
import sqlite3

import pytest

from core.library.reconciliation_fulfillment import (
    record_request, retain_request_context, requested_folder,
)
from core.library.reconciliation_migration import migrate_album
from core.library.reconciliation_organization import organization_context
from tests.library.test_reconciliation_fulfillment import Database


@pytest.mark.parametrize('case', ['album:1:old', 'folder:1:old:abcdef', 'legacy-stable-key'])
def test_album_id_migration_preserves_all_review_state(tmp_path, case):
    db = Database(tmp_path / 'migration.db')
    details = {'album_id': 'old', 'profile_id': 1, 'source': 'musicbrainz', 'release_id': 'release'}
    record_request(db, details, {'source_track_id': 'recording', 'disc_number': 1, 'track_number': 2})
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_fulfillment SET case_id=?, state="published", published_path="/new.flac"', (case,))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY, album_id TEXT, plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?,?)', (case, 'old', json.dumps({'album_id':'old','case_id':case,'inventory':{'track_ids':['t1']}})))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT)')
        conn.execute('INSERT INTO reconcile_album_intent VALUES("old","partial")')
        conn.execute('CREATE TABLE reconcile_review_choices(finding_id INTEGER PRIMARY KEY,case_id TEXT,evidence_json TEXT)')
        conn.execute('INSERT INTO reconcile_review_choices VALUES(1,?,?)',(case,json.dumps(details)))
        migrate_album(conn, 'old', 'new')
        assert conn.execute('SELECT album_id,scope FROM reconcile_album_intent').fetchone() == ('new','partial')
        assert conn.execute('SELECT album_id,state,published_path FROM reconcile_fulfillment').fetchone() == ('new','published','/new.flac')
        updated, raw = conn.execute('SELECT case_id,plan_json FROM reconcile_album_plans').fetchone()
        assert updated == case.replace(':old', ':new')
        assert json.loads(raw)['album_id'] == 'new'
        assert conn.execute('SELECT album_id FROM reconcile_wanted_slots').fetchone()[0] == 'new'
        assert json.loads(conn.execute('SELECT evidence_json FROM reconcile_review_choices').fetchone()[0])['album_id'] == 'new'
        migrate_album(conn, 'old', 'new')  # a replay cannot delete the migrated records
        assert conn.execute('SELECT count(*) FROM reconcile_fulfillment').fetchone()[0] == 1


def test_retry_keeps_review_release_and_profile_boundary(tmp_path):
    db = Database(tmp_path / 'retry.db')
    record_request(db, {'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'exact',
                       'replacement_request':True}, {'source_track_id':'recording'})
    payload = {'id':'recording','album':{'id':'exact','name':'Album'}}
    with db._get_connection() as conn:
        upgraded, info = retain_request_context(conn,payload,{'reason':'download_failed','attempt':2},1)
        assert upgraded['album']['musicbrainz_release_id'] == 'exact'
        assert upgraded['provider'] == 'musicbrainz'
        assert info['reason'] == 'library_reconciliation_wrong_audio'
        assert info['attempt'] == 2
        assert retain_request_context(conn,payload,{'reason':'download_failed'},2) == (payload,{'reason':'download_failed'})
        assert 'musicbrainz_release_id' not in payload['album']


def test_requested_copy_returns_to_reviewed_disc_and_never_crosses_root(tmp_path):
    db = Database(tmp_path / 'routing.db')
    root=tmp_path/'library'; root.mkdir()
    first=root/'Compilations'/'Album'/'Disc 1'; first.mkdir(parents=True)
    second=first.parent/'Disc 2'; second.mkdir()
    one=first/'01.flac'; one.write_bytes(b'audio1')
    two=second/'01.flac'; two.write_bytes(b'audio2')
    record_request(db, {'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'exact','album_folder':str(first.parent)}, {'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT,disc_number INTEGER)')
        conn.executemany('INSERT INTO tracks VALUES(?,?,?,?)',[('1','album',str(one),1),('2','album',str(two),2)])
    args=dict(profile_id=1,source='musicbrainz',release_id='exact',track_id='recording',disc_number=2,root=str(root))
    assert requested_folder(db,**args) == str(second)
    assert requested_folder(db,**{**args,'profile_id':2}) is None
    assert requested_folder(db,**{**args,'release_id':'different-edition'}) is None
    assert requested_folder(db,**{**args,'root':str(first)}) is None


def test_organization_context_preserves_unedited_names_and_approved_title(tmp_path):
    db = Database(tmp_path/'organization.db')
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE albums(id TEXT,title TEXT)')
        conn.execute('INSERT INTO albums VALUES("album","Old deluxe, pressing information")')
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT)')
        conn.executemany('INSERT INTO tracks VALUES(?,?,?)',[('1','album','/old/01 - First.flac'),('2','album','/old/02 - Legacy Title.flac')])
    context=organization_context(db,'album',[{'db_album_title':'New deluxe'}, {'track_id':'1','changes':{'filename':{'new':'01 - Approved.flac'}}}])
    assert context == {'album_title':'New deluxe','filenames':{'1':'01 - Approved.flac','2':'02 - Legacy Title.flac'}}
    with pytest.raises(ValueError):
        organization_context(db,'album',[{'track_id':'1','changes':{'filename':{'new':'../escape.flac'}}}])


def test_artist_rewrite_removes_stale_separate_list(tmp_path):
    from mutagen.flac import FLAC
    from core.tag_writer import write_tags_to_file
    from core.library.release_identity import read_release_evidence
    from tests.library.test_reconciliation_end_to_end import _minimal_flac
    path=tmp_path/'song.flac'; _minimal_flac(path, {})
    tags=FLAC(path);tags['artist']='No Doubt';tags['artists']=['Fred the Godson','KIDD'];tags.save()
    assert read_release_evidence(str(path))['artists_list'] == ['Fred the Godson','KIDD']
    assert write_tags_to_file(str(path),{'track_artist':'No Doubt'},embed_cover=False)['success']
    tags=FLAC(path)
    assert tags['artist'] == ['No Doubt']
    assert not tags.get('artists')


def test_review_organization_preview_uses_final_folder_and_preserves_filenames(tmp_path, monkeypatch):
    from core.library_reorganize import preview_album_reorganize
    import core.library_reorganize as reorganize
    path=tmp_path/'old'/'01 - Old Name.flac';path.parent.mkdir();path.write_bytes(b'audio')
    track={'id':'1','file_path':str(path),'title':'Old Name','track_number':1,'disc_number':1}
    release={'name':'Old Album','disambiguation':'Old pressing','artists':[{'name':'Artist'}]}
    monkeypatch.setattr(reorganize,'load_album_and_tracks',lambda *a: ({'title':'Old Album','artist_name':'Artist'},[track]))
    monkeypatch.setattr(reorganize,'plan_album_reorganize',lambda *a,**kw: {'status':'planned','source':'tags','api_album':release,'total_discs':1,'items':[{'matched':True,'track':track,'api_track':{'name':'Old Name','track_number':1},'api_album':release}]})
    contexts=[]
    def build(context,artist,album,extension,**kwargs):
        contexts.append(context)
        return str(tmp_path/context['spotify_album']['name']/('generated'+extension)),True
    result=preview_album_reorganize(album_id='a',db=None,transfer_dir=str(tmp_path),resolve_file_path_fn=lambda p:p,
        build_final_path_fn=build,review_context={'album_title':'Approved Album','filenames':{'1':'01 - Approved.flac'}})
    assert result['success'],result
    assert result['tracks'][0]['new_path_abs'] == str(tmp_path/'Approved Album'/'01 - Approved.flac')
    assert contexts[0]['spotify_album']['disambiguation'] == ''
    assert path.read_bytes() == b'audio'
