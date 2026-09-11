from unittest.mock import MagicMock, patch
import pytest
from core.audiobook_database import AudiobookDatabase
from core.audiobook_library_matching import score_candidate, match_one, match_library
from core.audiobook_library_scan import scan, MIN_BOOK_BYTES

BOOK = {'asin':'B000000001','title':'The Long Journey','author_names':['Alice Writer'],
        'narrator_names':['Bob Reader'],'runtime_minutes':600,'language':'english',
        'format_type':'unabridged','source':'audible'}
LOCAL = {'title':'The Long Journey','title_source':'metadata','author':'Alice Writer',
         'narrator':'Bob Reader','runtime_minutes':600,'language':'eng','format_type':'unabridged'}

@pytest.fixture
def db(tmp_path):
    instance = AudiobookDatabase(str(tmp_path/'books.db'))
    yield instance
    instance.close()

def local_row(db):
    db.add_to_library({'asin':'local:one','title':LOCAL['title']}, '/library/book', source='scan', metadata_json=LOCAL, scan_signature='v1')
    return db.get_library_entry('local:one')

@pytest.mark.parametrize('change', [
    {'narrator_names':['Someone Else']}, {'author_names':['Someone Else']},
    {'runtime_minutes':300}, {'language':'german'}, {'format_type':'abridged'},
    {'title':'The Long Journey 2'},
])
def test_conflicting_editions_cannot_auto_match(change):
    result = score_candidate(LOCAL,{**BOOK,**change})
    assert result['conflicts'] and not result['automatic_eligible']

@pytest.mark.parametrize('field',['author','narrator','runtime_minutes'])
def test_missing_evidence_requires_review(field):
    result=score_candidate({**LOCAL,field:''},BOOK)
    assert not result['automatic_eligible']

def test_unique_strong_match_sets_ownership_without_changing_local_id(db):
    row=local_row(db); client=MagicMock(); client.search.return_value=[BOOK]
    assert match_one(db,row,client)=='automatic'
    assert db.is_owned(BOOK['asin']) and db.get_library_entry('local:one')
    assert db.get_library_entry('local:one')['origin']=='disk'

def test_two_plausible_editions_require_review(db):
    row=local_row(db); client=MagicMock(); client.search.return_value=[BOOK,{**BOOK,'asin':'B000000002'}]
    assert match_one(db,row,client)=='suggested'
    assert db.owned_asins()==set()

def test_bad_title_does_not_get_high_confidence_from_other_fields(db):
    assert not score_candidate({**LOCAL,'title':'Other Book'},BOOK)['automatic_eligible']

def test_filename_only_title_cannot_auto_match():
    assert not score_candidate({**LOCAL,'title_source':'filename'},BOOK)['automatic_eligible']

def test_manual_choice_wins_over_inflight_automatic_result(db):
    row=local_row(db)
    assert db.apply_library_match(row['asin'],signature='v1',revision=0,status='confirmed',catalog_asin='B000000002',manual=True)
    client=MagicMock();client.search.return_value=[BOOK]
    assert match_one(db,row,client)=='unchanged'
    assert db.is_owned('B000000002') and not db.is_owned(BOOK['asin'])

def test_ignore_is_persistent_and_drops_ownership(db):
    row=local_row(db)
    assert db.apply_library_match(row['asin'],signature='v1',revision=0,status='ignored',manual=True)
    client=MagicMock()
    assert match_library(db,client)['match_checked']==0
    client.search.assert_not_called()

def test_catalogue_failure_preserves_inventory_and_uses_short_retry(db):
    local_row(db); client=MagicMock();client.search.side_effect=RuntimeError('Catalogue offline')
    result=match_library(db,client)
    assert result['match_errors']==1 and len(db.get_library())==1
    assert db.get_library()[0]['match_status']=='error'
    assert match_library(db,client)['match_checked']==0

def test_matching_batch_is_bounded(db):
    for i in range(5):
        db.add_to_library({'asin':f'local:{i}','title':'Book'},f'/books/{i}',source='scan',metadata_json=LOCAL)
    client=MagicMock();client.search.return_value=[]
    result=match_library(db,client,limit=2)
    assert result['match_checked']==2 and result['match_pending']==3
    assert client.search.call_count==2

def test_scanned_duplicate_copies_share_catalogue_identity(db,tmp_path):
    from core.audiobook_post_processor import build_opf
    for name in ['one','two']:
        folder=tmp_path/name;folder.mkdir()
        (folder/'01.mp3').write_bytes(b'a'*(MIN_BOOK_BYTES+1))
        (folder/'metadata.opf').write_text(build_opf(BOOK))
    scan(str(tmp_path),db)
    assert len(db.get_library())==2
    assert {r['catalog_asin'] for r in db.get_library()}=={BOOK['asin']}
    assert db.owned_asins()=={BOOK['asin']}

def test_moving_unmatched_book_preserves_manual_choice_and_origin(db,tmp_path):
    folder=tmp_path/'Old';folder.mkdir();(folder/'01.mp3').write_bytes(b'a'*(MIN_BOOK_BYTES+1))
    scan(str(tmp_path),db);row=db.get_library()[0]
    db.apply_library_match(row['asin'],signature=row['scan_signature'],revision=row['match_revision'],status='confirmed',catalog_asin=BOOK['asin'],manual=True)
    folder.rename(tmp_path/'New')
    result=scan(str(tmp_path),db)
    updated=db.get_library()[0]
    assert result['moved']==1 and updated['asin']==row['asin']
    assert updated['match_status']=='confirmed' and updated['catalog_asin']==BOOK['asin']
    assert updated['origin']=='disk'

def test_flat_tagged_chapters_group_without_owning_root(db,tmp_path):
    for i in range(2): (tmp_path/f'{i}.mp3').write_bytes(b'a'*(MIN_BOOK_BYTES+1))
    audio=MagicMock();audio.info.length=1800;audio.tags={'album':['Book'],'artist':['Author']}
    with patch('mutagen.File',return_value=audio): scan(str(tmp_path),db)
    row=db.get_library()[0]
    assert len(db.get_library())==1 and len(row['file_paths'])==2
    assert row['file_scope']=='files' and row['path']!=str(tmp_path)

def test_different_tagged_books_in_same_folder_stay_separate(db,tmp_path):
    folder=tmp_path/'Mixed';folder.mkdir()
    for name in ['one','two']: (folder/f'{name}.mp3').write_bytes(b'a'*(MIN_BOOK_BYTES+1))
    def audio_for(file):
        audio=MagicMock();audio.info.length=1800;audio.tags={'album':[file.stem],'artist':['Author']};return audio
    with patch('mutagen.File',side_effect=audio_for): scan(str(tmp_path),db)
    assert len(db.get_library())==2
    assert all(r['file_scope']=='files' for r in db.get_library())

def test_legacy_source_default_is_not_download_proof(db):
    db.add_to_library(BOOK,'/books/old')
    assert db.get_library()[0]['origin']=='unknown'

def test_explicit_download_import_records_provenance(db):
    db.add_to_library(BOOK,'/books/imported',download_id='torrent-id',origin='soulsync')
    row=db.get_library()[0]
    assert row['origin']=='soulsync' and row['download_id']=='torrent-id'

def test_release_marker_links_completed_history(db,tmp_path):
    from core.audiobook_post_processor import build_opf
    folder=tmp_path/'Book';folder.mkdir();(folder/'01.mp3').write_bytes(b'a'*(MIN_BOOK_BYTES+1))
    (folder/'metadata.opf').write_text(build_opf(BOOK));(folder/'.soulsync-release').write_text('Release.One')
    db.record_download('dl1',BOOK['asin'],BOOK['title'],'torrent',release_title='Release.One')
    db.update_download('dl1',status='completed')
    scan(str(tmp_path),db)
    row=db.get_library()[0]
    assert row['origin']=='soulsync' and row['download_id']=='dl1'

def test_replaced_recording_does_not_inherit_old_identifier(db, tmp_path):
    from core.audiobook_post_processor import build_opf
    folder = tmp_path / 'Book'
    folder.mkdir()
    audio = folder / '01.mp3'
    audio.write_bytes(b'a' * (MIN_BOOK_BYTES + 1))
    sidecar = folder / 'metadata.opf'
    sidecar.write_text(build_opf(BOOK))
    scan(str(tmp_path), db)
    assert db.is_owned(BOOK['asin'])
    audio.write_bytes(b'b' * (MIN_BOOK_BYTES + 2))
    sidecar.unlink()
    scan(str(tmp_path), db)
    assert not db.is_owned(BOOK['asin'])
    assert db.get_library()[0]['match_status'] == 'unmatched'
