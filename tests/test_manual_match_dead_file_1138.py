"""#1138 — a manual match to a deleted file swallowed the track.

carlosjfcasero's steps: process playlist A containing song B, manually add B to
Library Match, delete B from disk, reprocess A. Song B is then "not processed
nor added to wishlist" — it disappears from every subsequent run, leaving only
a WARNING in the log.

The earlier pass at this fixed the SYNC COMPARE PREVIEW's reporting
(``dead_match_source_ids`` / ``manual_match_stale``). That was real, but it was
not the reported failure: ``build_bulk_override_lookup`` is called from exactly
one place — the playlist-tracks preview endpoint — and the reprocess damage
happens in the download analysis, which reads the match table directly and
never went near that code.

The actual mechanism is ``core/downloads/master.py``: a manual match row was
treated as authoritative on EXISTENCE alone. The analysis marked the track
found at confidence 1.0, called
``check_and_remove_track_from_wishlist_by_metadata`` — actively pulling it OFF
the wishlist — and ``continue``d past the download. Nothing ever asked whether
the matched file was still there.

The reporter's other ask (being able to edit the list) is the second half:
``delete_manual_library_match`` returned True whether or not it deleted
anything, so a delete that matched no row reported success and the entry came
back on the next load.
"""

from __future__ import annotations

from core.library import manual_library_match as mlm


class _DB:
    """Minimal library DB: known track ids, and a path→id index."""

    def __init__(self, ids=(), by_path=None, raises=False):
        self.ids = {str(i) for i in ids}
        self.by_path = dict(by_path or {})
        self.raises = raises
        self.path_lookups = []

    def api_get_tracks_by_ids(self, ids):
        if self.raises:
            raise RuntimeError("db is having a moment")
        return [{"id": str(i)} for i in ids if str(i) in self.ids]

    def find_track_id_by_file_path(self, path):
        self.path_lookups.append(path)
        return self.by_path.get(path)


# ── the validator ────────────────────────────────────────────────────────────

def test_a_match_whose_library_row_still_exists_is_live():
    db = _DB(ids=["17549816"])
    assert mlm.match_is_live(db, {"library_track_id": "17549816"}) is True


def test_a_match_to_a_deleted_file_is_dead():
    # The exact shape from the report: the library id is gone and the stored
    # path resolves to nothing.
    db = _DB(ids=[], by_path={})
    match = {
        "library_track_id": "17549816",
        "library_file_path": "/music/library/La La Love You/El Fin del Mundo.mp3",
    }
    assert mlm.match_is_live(db, match) is False


def test_a_rescan_that_only_re_keyed_the_row_is_still_live():
    """The id went stale but the file is right where it was — that is a
    re-keyed library, not a deleted track, and must NOT be called dead."""
    db = _DB(ids=["99"], by_path={"/music/song.mp3": "99"})
    match = {"library_track_id": "17549816", "library_file_path": "/music/song.mp3"}
    assert mlm.match_is_live(db, match) is True


def test_liveness_is_judged_against_the_database_not_the_filesystem():
    """``library_file_path`` is the path as the MEDIA SERVER sees it. A
    split-container install may mount it elsewhere or not at all, so an
    os.path.exists check would call every live match dead and trigger a storm
    of re-downloads. The path is only ever used as a DB key."""
    db = _DB(ids=[], by_path={"/music/library/only-the-server-can-see-this.mp3": "7"})
    match = {"library_file_path": "/music/library/only-the-server-can-see-this.mp3"}
    assert mlm.match_is_live(db, match) is True
    assert db.path_lookups == ["/music/library/only-the-server-can-see-this.mp3"]


def test_a_database_error_is_not_read_as_deleted():
    """Failing open matters more than failing closed here: a transient DB error
    read as "deleted" would re-download tracks the user already owns."""
    db = _DB(ids=[], raises=True)
    assert mlm.match_is_live(db, {"library_track_id": "17549816"}) is True


def test_a_db_without_the_readers_is_unverifiable_not_dead():
    """The failure mode this guard must never cause is the opposite one. A db
    object that cannot answer the question — a stub, a narrowed facade — would
    otherwise have EVERY saved match called dead, and the analysis would
    re-download the user's whole library. Caught by
    tests/downloads/test_downloads_master.py, whose _FakeDB implements the
    match getters and neither reader."""

    class _NoReaders:
        pass

    match = {"library_track_id": "17549816", "library_file_path": "/music/x.mp3"}
    assert mlm.match_is_live(_NoReaders(), match) is True


def test_a_missing_path_resolver_does_not_condemn_a_match_either():
    """Half a check is still not a complete one: the id said "gone", but with
    no way to try the stored path we have not finished asking."""

    class _IdOnly:
        def api_get_tracks_by_ids(self, ids):
            return []

    match = {"library_track_id": "17549816", "library_file_path": "/music/x.mp3"}
    assert mlm.match_is_live(_IdOnly(), match) is True


def test_a_match_with_nothing_to_check_is_dead():
    # No id and no path is not evidence of anything.
    assert mlm.match_is_live(_DB(), {}) is False
    assert mlm.match_is_live(_DB(), None) is False


# ── the analysis branch that consumed it ─────────────────────────────────────

def test_the_download_analysis_drops_a_stale_match_before_trusting_it():
    """Pins the guard's SHAPE at the call site: the analysis must consult
    match_is_live between fetching the match and acting on it. A rewrite that
    reorders those two lines re-opens #1138 exactly."""
    import re
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "core" / "downloads" / "master.py").read_text(encoding="utf-8")
    fetch = source.index("_mlm.get_match_for_track(")
    guard = source.index("_mlm.match_is_live(")
    act = source.index("'manual_library_match'")
    assert fetch < guard < act, (
        "the staleness guard must sit between fetching the manual match and "
        "acting on it — #1138 is exactly what happens when it does not"
    )
    # And the guard must clear the match rather than merely log about it.
    between = source[guard:act]
    assert re.search(r"_manual_match\s*=\s*None", between), (
        "a warning alone still skips the download — the match must be dropped"
    )


# ── being able to remove one (the reporter's other ask) ──────────────────────

class _DeleteDB:
    """Stands in for the sqlite layer: reports how many rows a DELETE hit."""

    def __init__(self, rows):
        self.rows = list(rows)          # (id, profile_id) pairs

    def delete_manual_library_match(self, match_id, profile_id):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r != (match_id, profile_id)]
        return len(self.rows) != before


def test_deleting_a_match_that_is_not_there_reports_failure():
    db = _DeleteDB([(1, 1)])
    assert mlm.delete_match(db, 1, 1) is True      # the row it owns
    assert mlm.delete_match(db, 1, 1) is False     # already gone
    assert mlm.delete_match(db, 99, 1) is False    # never existed


def test_a_match_saved_under_another_profile_does_not_report_success():
    """The delete is profile-scoped. Answering "success" to a no-op is what
    leaves the user clicking the same button on the same row forever."""
    db = _DeleteDB([(1, 2)])                       # belongs to profile 2
    assert mlm.delete_match(db, 1, 1) is False


def test_the_real_delete_returns_rowcount_not_true():
    """Guards the sqlite implementation itself — the bug was that it returned
    True whenever the statement did not raise."""
    import inspect

    from database.music_database import MusicDatabase

    source = inspect.getsource(MusicDatabase.delete_manual_library_match)
    assert "rowcount" in source, (
        "delete_manual_library_match must report whether a row was actually "
        "removed; returning a bare True is #1138's 'I couldn't remove it'"
    )
