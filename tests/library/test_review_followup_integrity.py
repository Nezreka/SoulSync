"""State and file invariants behind the album-review followup fixes."""
import json
import sqlite3

import pytest

from core.reconciliation.fulfillment import (
    record_request, retain_request_context, requested_folder,
)
from core.reconciliation.migration import migrate_album
from core.reconciliation.organization import organization_context
from tests.reconciliation.support.acquisition import Database


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
    from tests.reconciliation.support.audio import _minimal_flac
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


@pytest.mark.parametrize('owner,expected', [(1,'new'),(2,'old')])
def test_legacy_album_and_track_id_changes_require_same_physical_file_and_profile(tmp_path, owner, expected):
    from core.reconciliation.migration import repair_legacy_ids
    db=Database(tmp_path/'legacy.db')
    path=tmp_path/'01 - 100%_Rolling.flac';path.write_bytes(b'audio')
    alias=tmp_path/'alias';alias.symlink_to(tmp_path,target_is_directory=True)
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE albums(id TEXT,owner_profile_id INTEGER)')
        conn.execute('INSERT INTO albums VALUES("new",?)',(owner,))
        conn.execute('CREATE TABLE tracks(id TEXT,album_id TEXT,file_path TEXT)')
        conn.execute('INSERT INTO tracks VALUES("new-track","new",?)',(str(alias/path.name),))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,album_id TEXT,profile_id INTEGER,plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES("album:1:old","old",1,?)',
            (json.dumps({'inventory':{'track_ids':['old-track'],'paths':[str(path)]}}),))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT)')
        conn.execute('INSERT INTO reconcile_album_intent VALUES("old","partial")')
    repair_legacy_ids(db)
    with db._get_connection() as conn:
        assert conn.execute('SELECT album_id FROM reconcile_album_intent').fetchone()[0] == expected
        assert conn.execute('SELECT count(*) FROM reconcile_album_plans').fetchone()[0] == 1


@pytest.mark.parametrize('profile,release,reason,receipt_date,expected',[
    (1,'release','download_complete','2099-01-01','published'),
    (2,'release','download_complete','2099-01-01','requested'),
    (1,'different','download_complete','2099-01-01','requested'),
    (1,'release','removed_by_user','2099-01-01','requested'),
    (1,'release','download_complete','2000-01-01','requested')])
def test_missed_publication_requires_matching_delivery_profile_and_embedded_identity(tmp_path,profile,release,reason,receipt_date,expected):
    from core.reconciliation.fulfillment import recover_published_requests, settle_assignments
    from core.reconciliation.identity import review_case_key
    from tests.reconciliation.support.audio import _minimal_flac
    db=Database(tmp_path/'receipt.db');path=tmp_path/'published.flac'
    _minimal_flac(path,{'musicbrainz_albumid':release,'musicbrainz_trackid':'recording'})
    details={'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'release','replacement_request':True}
    record_request(db,details,{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_removals(spotify_track_id TEXT,reason TEXT,final_path TEXT,profile_ids TEXT,removed_at TEXT)')
        conn.execute('INSERT INTO wishlist_removals VALUES("recording",?,?,?,?)',(reason,str(path),json.dumps([profile]),receipt_date))
        recover_published_requests(conn)
        assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == expected
    if expected=='published':
        # Publication is not completion; only a subsequent supported assignment settles it.
        settle_assignments(db,review_case_key(details,None),[{'source_track_id':'recording','status':'confirmed','file_path':str(path)}],'release','musicbrainz')
        with db._get_connection() as conn:
            assert conn.execute('SELECT state FROM reconcile_fulfillment').fetchone()[0] == 'fulfilled'


@pytest.mark.parametrize('other_request,same_path,expected', [(False,True,'published'),(True,True,'requested'),(False,False,'requested')])
def test_retagged_delivery_requires_unique_request_and_same_file_in_exact_album_plan(tmp_path,other_request,same_path,expected):
    from core.reconciliation.fulfillment import recover_published_requests
    from core.reconciliation.identity import review_case_key
    from tests.reconciliation.support.audio import _minimal_flac
    db=Database(tmp_path/'retagged.db');path=tmp_path/'published.flac'
    _minimal_flac(path,{'musicbrainz_albumid':'another-pressing','musicbrainz_trackid':'recording'})
    details={'album_id':'album','profile_id':1,'source':'musicbrainz','release_id':'requested-release','replacement_request':True}
    record_request(db,details,{'source_track_id':'recording'})
    key=review_case_key(details,None)
    if other_request:
        record_request(db,{**details,'album_id':'another-album','release_id':'another-release'},{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE wishlist_removals(spotify_track_id TEXT,reason TEXT,final_path TEXT,profile_ids TEXT,removed_at TEXT)')
        conn.execute('INSERT INTO wishlist_removals VALUES("recording","download_complete",?,"[1]","2099-01-01")',(str(path),))
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,plan_json TEXT)')
        plan={'source':'musicbrainz','release_id':'requested-release','assignments':[
            {'source_track_id':'recording','file_path':str(path if same_path else tmp_path/'another.flac'),'status':'uncertain'}]}
        conn.execute('INSERT INTO reconcile_album_plans VALUES(?,?)',(key,json.dumps(plan)))
        recover_published_requests(conn)
        assert conn.execute('SELECT state FROM reconcile_fulfillment WHERE case_id=?',(key,)).fetchone()[0] == expected


@pytest.mark.parametrize('new_scope,expected', [(None,'partial'),('complete','complete')])
def test_album_id_migration_retains_inferred_scope_without_overriding_new_choice(tmp_path,new_scope,expected):
    from core.reconciliation.migration import migrate_album
    db=Database(tmp_path/'scope.db')
    with db._get_connection() as conn:
        conn.execute('CREATE TABLE reconcile_album_plans(case_id TEXT PRIMARY KEY,album_id TEXT,plan_json TEXT)')
        conn.execute('INSERT INTO reconcile_album_plans VALUES("album:1:old","old",?)',
            (json.dumps({'source':'musicbrainz','release_id':'release','intent_scope':'partial'}),))
        conn.execute('CREATE TABLE reconcile_album_intent(album_id TEXT PRIMARY KEY,scope TEXT,source TEXT,release_id TEXT)')
        if new_scope:
            conn.execute('INSERT INTO reconcile_album_intent VALUES("new",?,"musicbrainz","release")',(new_scope,))
        migrate_album(conn,'old','new')
        assert conn.execute('SELECT scope FROM reconcile_album_intent WHERE album_id="new"').fetchone()[0]==expected


@pytest.mark.parametrize('profile,indexed_elsewhere,ambiguous,merged',[
    (1,False,False,True),(2,False,False,False),(1,True,False,False),(1,False,True,False)])
def test_delivered_disc_fragment_joins_only_its_requested_album(tmp_path,profile,indexed_elsewhere,ambiguous,merged):
    from core.reconciliation.fulfillment import group_delivered_files
    db=Database(tmp_path/'fragment.db')
    old=str(tmp_path/'Original album'/'01.flac');new=str(tmp_path/'New pressing'/'Disc 2'/'07.flac')
    details={'album_id':'album','profile_id':profile,'source':'musicbrainz','release_id':'release'}
    record_request(db,details,{'source_track_id':'recording'})
    if ambiguous:
        record_request(db,{**details,'album_id':'another'},{'source_track_id':'recording'})
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_fulfillment SET state="published",published_path=?',(new,))
    folders={str(tmp_path/'Original album'):[old],str(tmp_path/'New pressing'/'Disc 2'):[new]}
    tracks={old:{'id':'old','album_id':'album'}}
    if indexed_elsewhere:tracks[new]={'id':'new','album_id':'another'}
    trusted={};albums={'album':{'owner_profile_id':1,'album_title':'Grace (Legacy Edition)'}}
    result=group_delivered_files(db,folders,tracks,albums,trusted)
    assert (new in result[str(tmp_path/'Original album')]) is merged
    assert sum(len(paths)for paths in result.values())==2
    with db._get_connection() as conn:
        assert all(row[0]=='published'for row in conn.execute('SELECT state FROM reconcile_fulfillment'))


@pytest.mark.parametrize('stored,changed', [('The Steve Miller Band',False),('Fred the Godson',True)])
def test_secondary_artist_list_ignores_article_spelling_but_corrects_other_artists(stored,changed):
    from core.library.retag_planner import plan_track
    plan=plan_track({'artist':'Steve Miller Band','artists_list':[stored]},
        {'artists':[{'name':'Steve Miller Band'}]}, {})
    assert ('artists_list' in plan['changes']) is changed
