"""the Completed tail must not contain the acoustid scanner's synthetic rows.

user report (aug 25): hundreds of songs that existed before soulsync showed
up under Downloads > Completed, and the real downloads fell out of the
capped tail behind them. the scanner writes review rows with
event_type='download' / download_source='acoustid_scan' - review entries,
not downloads.
"""

from database.music_database import MusicDatabase


def _db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _seed(db):
    db.add_library_history_entry(
        event_type='download', title='Real Download', artist_name='A',
        download_source='soulseek')
    db.add_library_history_entry(
        event_type='download', title='Scan Row', artist_name='B',
        download_source='acoustid_scan')
    db.add_library_history_entry(
        event_type='import', title='Server Import', artist_name='C')


def test_exclusion_drops_scan_rows_and_counts_honestly(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    entries, total = db.get_library_history(
        event_type='download', exclude_download_sources=('acoustid_scan',))
    assert [e['title'] for e in entries] == ['Real Download']
    assert total == 1


def test_default_behavior_unchanged_without_the_filter(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    entries, total = db.get_library_history(event_type='download')
    assert {e['title'] for e in entries} == {'Real Download', 'Scan Row'}
    assert total == 2


def test_rows_with_null_download_source_survive_the_filter(tmp_path):
    """COALESCE matters: legacy download rows carry NULL download_source and
    must not be swept out with the scan rows."""
    db = _db(tmp_path)
    db.add_library_history_entry(event_type='download', title='Legacy Row')
    entries, total = db.get_library_history(
        event_type='download', exclude_download_sources=('acoustid_scan',))
    assert [e['title'] for e in entries] == ['Legacy Row']
    assert total == 1
