"""_fix_mbid_mismatch: stripping the bad MBID off the file must also
clear the catalogue's recording MBID (lib2_tracks.musicbrainz_id) when it still
holds that SAME bad value — otherwise
the export MBID waterfall's DB rung (core/exports/export_sources.py) keeps resolving the
wrong recording straight out of the DB even after the file itself was cleaned up.
"""
import os

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase

BAD_MBID = "11111111-2222-3333-4444-555555555555"
OTHER_MBID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
# A letter-bearing MBID so upper/lower actually differ — BAD_MBID above is all digits.
MIXED_CASE_MBID = "aaaa1111-bbbb-2222-cccc-3333dddd4444"


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
    res = w._fix_mbid_mismatch('track', 'lib2:10', str(src), details)

    assert res['success'] is True
    assert fake_db.calls == [(10, BAD_MBID)]
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
    res = w._fix_mbid_mismatch('track', 'lib2:10', str(src), details)

    assert res['success'] is True
    assert fake_db.calls == [(10, BAD_MBID)]   # asked, but guard in the DB layer refused
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
    res = w._fix_mbid_mismatch('track', 'lib2:10', str(src), details)

    assert res['success'] is False
    assert fake_db.calls == []
    assert fake_db.current_mbid == BAD_MBID


# ── side_effects.py lowercases the MBID on import, but a
# finding's details['mbid'] carries the raw file-tag case — the clear must not compare
# with a case-sensitive `=` or it silently matches 0 rows ──

def _seed_track(db, track_id, mbid):
    from tests.support.catalogue_seed import seed_album, seed_artist
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id=f"a{track_id}", name="A", server_source="test")
        album_id = seed_album(conn, server_id=f"al{track_id}", title="Alb",
                              artist_id=artist_id, server_source="test")
        conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title, musicbrainz_id, server_source)"
            " VALUES (?, ?, 'T', ?, 'test')", (track_id, album_id, mbid))
        conn.commit()


def _mbid(db, track_id):
    with db._get_connection() as conn:
        return conn.execute("SELECT musicbrainz_id FROM lib2_tracks WHERE id=?",
                            (track_id,)).fetchone()[0]


def test_clear_track_recording_mbid_is_case_insensitive(tmp_path):
    """DB holds the lowercased MBID (as import writes it); the finding's mbid is
    uppercase (as the file tag itself was) -> must still match and clear."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed_track(db, 30, MIXED_CASE_MBID.lower())

    cleared = db.clear_track_recording_mbid_if_matches(30, MIXED_CASE_MBID.upper())

    assert cleared is True
    assert _mbid(db, 30) is None


def test_clear_track_recording_mbid_case_insensitive_still_guards_different_value(tmp_path):
    """Case-insensitivity must not turn into "clear whatever's there" — a genuinely
    different MBID (not just different case) is still left alone."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed_track(db, 31, OTHER_MBID)

    cleared = db.clear_track_recording_mbid_if_matches(31, MIXED_CASE_MBID.upper())

    assert cleared is False
    assert _mbid(db, 31) == OTHER_MBID


def test_fix_mbid_mismatch_end_to_end_clears_case_mismatched_db_value(tmp_path, monkeypatch):
    """Full path through RepairWorker._fix_mbid_mismatch with a REAL MusicDatabase: the
    finding's (uppercase) mbid must still clear the (lowercase, as-imported) DB column."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed_track(db, 32, MIXED_CASE_MBID.lower())

    src = tmp_path / "Transfer" / "Artist" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"audio")

    w = RepairWorker.__new__(RepairWorker)
    w.db = db
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    monkeypatch.setattr(
        "core.repair_jobs.mbid_mismatch_detector._remove_mbid_from_file", lambda path: True,
    )

    details = {'mbid': MIXED_CASE_MBID.upper(), 'mb_title': 'Wrong Song', 'title': 'Right Song'}
    res = w._fix_mbid_mismatch('track', 'lib2:32', str(src), details)

    assert res['success'] is True
    assert _mbid(db, 32) is None
