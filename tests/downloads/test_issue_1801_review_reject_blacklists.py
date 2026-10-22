"""#1801 (kevin2xk): a file deleted in review came straight back.

six songs probably aren't on any of kevin's sources. each sync downloaded
one, kevin listened, said "not it", deleted it in the review queue, and the
next sync downloaded the exact same file from the exact same source.

the download worker has skipped blacklisted (username, filename) pairs for a
long time. the review delete just never put anything there. it now does,
with the pair the next search's candidate will carry: the peer from the
provenance row for that file, or the service for a streaming source.
"""

from __future__ import annotations

import pytest

from core.downloads import candidates as dc
from core.downloads.review_reject import rejected_source
from core.runtime_state import download_tasks, matched_downloads_context
from database.music_database import MusicDatabase
from tests.downloads.test_downloads_candidates import (
    _build_deps,
    _Candidate,
    _seed_task,
    _Track,
)

SLSK_FILE = "@@music\\Sami Kaur\\Seville.mp3"
YT_FILE = "ZCuXBTm4LRY||Seville"


# --- which source a rejected row names --------------------------------------


def test_a_soulseek_reject_names_the_peer_from_provenance():
    history = {'source_filename': SLSK_FILE, 'download_source': 'Soulseek'}
    prov = {'source_username': 'peer42', 'source_filename': SLSK_FILE}
    assert rejected_source(history, prov) == ('peer42', SLSK_FILE)


def test_a_streaming_reject_works_off_the_label_alone():
    history = {'source_filename': YT_FILE, 'download_source': 'YouTube'}
    assert rejected_source(history, None) == ('youtube', YT_FILE)


def test_a_soulseek_reject_without_provenance_names_nothing():
    # "Soulseek" is a label, not a peer: guessing would blacklist nothing real
    history = {'source_filename': SLSK_FILE, 'download_source': 'Soulseek'}
    assert rejected_source(history, None) is None


def test_provenance_for_some_other_file_is_not_trusted():
    history = {'source_filename': SLSK_FILE, 'download_source': 'Soulseek'}
    prov = {'source_username': 'peer42', 'source_filename': '@@other\\Seville.mp3'}
    assert rejected_source(history, prov) is None


@pytest.mark.parametrize('label', ['Staging', 'Auto-Import', 'Torrent'])
def test_files_that_were_not_a_search_pick_name_nothing(label):
    assert rejected_source({'source_filename': 'x.mp3', 'download_source': label}, None) is None


def test_a_row_with_no_source_filename_names_nothing():
    assert rejected_source({'download_source': 'YouTube'}, None) is None


# --- the review delete, end to end ---------------------------------------------


@pytest.fixture(autouse=True)
def _reset_tasks():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'lib.db'))


def _review_delete(db, tmp_path, monkeypatch, *, source, filename, peer=None):
    from flask import Flask

    import api.verification as ver
    f = tmp_path / 'Seville.mp3'
    f.write_bytes(b'x')
    history_id = db.add_library_history_entry(
        event_type='download', title='Seville', artist_name='Sami Kaur',
        file_path=str(f), download_source=source, source_filename=filename,
        verification_status='unverified')
    if peer:
        db.record_track_download(str(f), 'soulseek', peer, filename,
                                 track_title='Seville', track_artist='Sami Kaur')
    monkeypatch.setattr(ver, 'get_database', lambda: db)
    monkeypatch.setattr(ver, '_resolve_history_audio_path', lambda row: str(f))
    with Flask(__name__).app_context():
        resp = ver.delete_verification_item.__wrapped__(history_id)
    assert not f.exists()
    return resp.get_json()


def _next_search_downloads(db, candidates):
    deps = _build_deps(db=db)
    _seed_task('t1801')
    dc.attempt_download_with_candidates('t1801', candidates, _Track(), batch_id=None, deps=deps)
    return [(u, fn) for u, fn, _size in deps.download_orchestrator.download_calls]


def test_a_rejected_soulseek_file_is_skipped_by_the_next_search(db, tmp_path, monkeypatch):
    out = _review_delete(db, tmp_path, monkeypatch, source='Soulseek',
                         filename=SLSK_FILE, peer='peer42')
    assert out['blacklisted'] is True
    assert db.is_blacklisted('peer42', SLSK_FILE)

    # the same file comes back first in the next search: it's passed over
    got = _next_search_downloads(db, [
        _Candidate(username='peer42', filename=SLSK_FILE, confidence=0.99),
        _Candidate(username='peer7', filename='@@m\\Seville (other).mp3', confidence=0.8),
    ])
    assert got == [('peer7', '@@m\\Seville (other).mp3')]


def test_a_rejected_youtube_file_is_skipped_by_the_next_search(db, tmp_path, monkeypatch):
    out = _review_delete(db, tmp_path, monkeypatch, source='YouTube', filename=YT_FILE)
    assert out['blacklisted'] is True
    got = _next_search_downloads(db, [
        _Candidate(username='youtube', filename=YT_FILE, confidence=0.99),
        _Candidate(username='youtube', filename='abc123||Seville', confidence=0.9),
    ])
    assert got == [('youtube', 'abc123||Seville')]


def test_when_nothing_is_left_nothing_downloads(db, tmp_path, monkeypatch):
    # kevin's six: the rejected file was the only hit. better to stay on
    # the wishlist than pull the same wrong file again
    _review_delete(db, tmp_path, monkeypatch, source='YouTube', filename=YT_FILE)
    assert _next_search_downloads(db, [
        _Candidate(username='youtube', filename=YT_FILE, confidence=0.99)]) == []


def test_a_failed_blacklist_never_stops_the_delete(db, tmp_path, monkeypatch):
    def boom(**kw):
        raise RuntimeError('db locked')
    monkeypatch.setattr(db, 'add_to_blacklist', boom)
    out = _review_delete(db, tmp_path, monkeypatch, source='YouTube', filename=YT_FILE)
    assert out['success'] is True and out['file_deleted'] is True
    assert out['blacklisted'] is False
