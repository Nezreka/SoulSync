"""The forensics fixture is only worth having if it can tell the two failure
modes apart, so prove that it does.

The rotating video-wishlist flake always presents the same way — the write
path says it wrote, the read path sees nothing — and the whole point of
``video_wishlist_forensics`` is to separate "inserted, then something deleted
it" from "never inserted here at all". A diagnostic that quietly reported
neither would be worse than none, because the next red CI run would look
explained.
"""

from database.video_database import VideoDatabase

EP = [{"season_number": 1, "episode_number": 1}]


def _db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_a_delete_is_recorded_and_named(tmp_path, video_wishlist_forensics):
    db = _db(tmp_path)
    video_wishlist_forensics.arm(db)
    db.add_episodes_to_wishlist(42, "Poker Face", EP)

    armed = video_wishlist_forensics(db, "after the insert")
    assert "('episode', 42, 1, 1)" in armed          # the row is there
    assert "NONE" in armed                            # and nothing was deleted

    db.remove_from_wishlist("show", tmdb_id=42)
    after = video_wishlist_forensics(db, "after the delete")
    # Empty wishlist AND a named delete: the row existed and was removed.
    assert "wishlist rows    : EMPTY" in after
    assert "NONE" not in after
    assert "'episode', 42, 1, 1" in after


def test_an_empty_log_means_the_row_never_landed_here(tmp_path, video_wishlist_forensics):
    db = _db(tmp_path)
    video_wishlist_forensics.arm(db)

    report = video_wishlist_forensics(db, "nothing written")
    # The distinction the flake turns on: empty wishlist, empty delete log.
    assert "wishlist rows    : EMPTY" in report
    assert "NONE - nothing was ever deleted from THIS file" in report


def test_an_unarmed_database_says_so_rather_than_lying(tmp_path, video_wishlist_forensics):
    report = video_wishlist_forensics(_db(tmp_path), "never armed")
    # Silence here would read as "no deletes happened", which is not known.
    assert "deletes recorded : (not armed)" in report


def test_the_report_names_both_handles_and_the_live_threads(tmp_path, video_wishlist_forensics):
    import api.video as videoapi

    db = _db(tmp_path)
    videoapi._video_db = db
    try:
        report = video_wishlist_forensics(db, "identity")
        assert "same handle      : True" in report
    finally:
        videoapi._video_db = None
    assert "same handle      : False" in video_wishlist_forensics(db, "identity")
    assert "live threads" in video_wishlist_forensics(db, "threads")
