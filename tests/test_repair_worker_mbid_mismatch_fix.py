"""_fix_mbid_mismatch (#903 follow-up): stripping the bad MBID off the file must also
clear tracks.musicbrainz_recording_id when it still holds that SAME bad value — otherwise
the export MBID waterfall's DB rung (core/exports/export_sources.py) keeps resolving the
wrong recording straight out of the DB even after the file itself was cleaned up.
"""
import os

from core.repair_worker import RepairWorker

BAD_MBID = "11111111-2222-3333-4444-555555555555"
OTHER_MBID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class _FakeDB:
    """Records what the fix asked to clear, without touching a real database."""
    def __init__(self, current_mbid):
        self.current_mbid = current_mbid
        self.calls = []

    def clear_track_recording_mbid_if_matches(self, track_id, expected_mbid):
        self.calls.append((track_id, expected_mbid))
        if self.current_mbid == expected_mbid:
            self.current_mbid = None
            return True
        return False


def _worker(tmp_path, fake_db, remove_mbid_result=True, monkeypatch=None):
    w = RepairWorker.__new__(RepairWorker)   # no __init__: no threads, no live services
    w.db = fake_db
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    if monkeypatch is not None:
        monkeypatch.setattr(
            "core.repair_jobs.mbid_mismatch_detector._remove_mbid_from_file",
            lambda path: remove_mbid_result,
        )
    return w


def test_clears_db_column_when_it_matches_the_bad_mbid(tmp_path, monkeypatch):
    src = tmp_path / "Transfer" / "Artist" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")

    fake_db = _FakeDB(current_mbid=BAD_MBID)
    w = _worker(tmp_path, fake_db, monkeypatch=monkeypatch)

    details = {'mbid': BAD_MBID, 'mb_title': 'Wrong Song', 'title': 'Right Song'}
    res = w._fix_mbid_mismatch('track', '10', str(src), details)

    assert res['success'] is True
    assert fake_db.calls == [('10', BAD_MBID)]
    assert fake_db.current_mbid is None


def test_leaves_db_column_alone_when_it_no_longer_matches(tmp_path, monkeypatch):
    """The DB value was already corrected to something else (by enrichment, or a prior
    fix) in between — the equality guard means this fix must not touch it."""
    src = tmp_path / "Transfer" / "Artist" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")

    fake_db = _FakeDB(current_mbid=OTHER_MBID)
    w = _worker(tmp_path, fake_db, monkeypatch=monkeypatch)

    details = {'mbid': BAD_MBID, 'mb_title': 'Wrong Song', 'title': 'Right Song'}
    res = w._fix_mbid_mismatch('track', '10', str(src), details)

    assert res['success'] is True
    assert fake_db.calls == [('10', BAD_MBID)]   # asked, but guard in the DB layer refused
    assert fake_db.current_mbid == OTHER_MBID    # untouched


def test_does_not_touch_db_when_tag_removal_finds_nothing(tmp_path, monkeypatch):
    """If the file tag was already gone (nothing to remove), the fix reports failure and
    never even asks the DB to clear the column."""
    src = tmp_path / "Transfer" / "Artist" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")

    fake_db = _FakeDB(current_mbid=BAD_MBID)
    w = _worker(tmp_path, fake_db, remove_mbid_result=False, monkeypatch=monkeypatch)

    details = {'mbid': BAD_MBID, 'mb_title': 'Wrong Song', 'title': 'Right Song'}
    res = w._fix_mbid_mismatch('track', '10', str(src), details)

    assert res['success'] is False
    assert fake_db.calls == []
    assert fake_db.current_mbid == BAD_MBID
