"""Two-phase manual-skip audit and repair-job consumption."""

from core.library2.manual_skips import (
    active_skip_paths,
    attach_manual_skip_file,
    check_is_skipped,
    record_manual_skip,
)


def test_manual_skip_records_profile_then_attaches_final_path(legacy_db, imported_conn):
    skip_id = record_manual_skip(
        legacy_db,
        content_key="user::remote.flac",
        title="Song",
        artist="Artist",
        skipped_checks=("quality", "acoustid", "quality"),
        profile_id=1,
    )
    assert skip_id
    row = imported_conn.execute(
        """SELECT file_path, profile_id, skipped_checks
             FROM lib2_manual_skips WHERE id=?""",
        (skip_id,),
    ).fetchone()
    assert row["file_path"] is None
    assert row["profile_id"] == 1
    assert row["skipped_checks"] == '["acoustid", "quality"]'

    assert attach_manual_skip_file(
        legacy_db,
        content_key="user::remote.flac",
        file_path="/music/Artist/Song.flac",
    )
    assert active_skip_paths(imported_conn, ("quality",), profile_id=1) == {
        "/music/Artist/Song.flac"
    }
    assert check_is_skipped(
        imported_conn,
        ("/server/path.flac", "/music/Artist/Song.flac"),
        ("acoustid",),
        profile_id=1,
    )


def test_manual_skip_is_profile_scoped_and_acknowledgement_consumes_it(
        legacy_db, imported_conn):
    skip_id = record_manual_skip(
        legacy_db,
        content_key="user::track.flac",
        title="Song",
        artist="Artist",
        skipped_checks=("quality",),
        profile_id=2,
    )
    attach_manual_skip_file(
        legacy_db, content_key="user::track.flac", file_path="/music/track.flac"
    )
    assert not check_is_skipped(
        imported_conn, ("/music/track.flac",), ("quality",), profile_id=1
    )
    assert check_is_skipped(
        imported_conn, ("/music/track.flac",), ("quality",), profile_id=2
    )
    imported_conn.execute(
        "UPDATE lib2_manual_skips SET acknowledged=1 WHERE id=?", (skip_id,)
    )
    assert not check_is_skipped(
        imported_conn, ("/music/track.flac",), ("quality",), profile_id=2
    )
