"""Shared wishlist mirror: candidate selection for the upgrade scan."""

from __future__ import annotations

from core.library2.wishlist_mirror import (
    track_wishlist_payload,
    upgrade_candidate_track_ids,
)
from core.library2.monitor_rules import PROVENANCE_LEGACY, PROVENANCE_USER, record_rule
from core.library2.wanted import recompute_wanted


def _seed(conn, *, policy: str, monitored: int = 1, with_file: bool = True) -> int:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO quality_profiles(name, ranked_targets, upgrade_policy) VALUES(?,?,?)",
        (f"P-{policy}-{monitored}-{with_file}",
         '[{"label":"FLAC","format":"flac"}]', policy))
    profile_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_artists(name) VALUES('X')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Alb')", (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute(
        "INSERT INTO lib2_tracks(album_id, title, monitored, quality_profile_id) "
        "VALUES(?, 'T', ?, ?)", (album_id, monitored, profile_id))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                (track_id, artist_id))
    if with_file:
        cur.execute(
            "INSERT INTO lib2_track_files(track_id, path, format, bitrate) "
            "VALUES(?, ?, 'mp3', 320)", (track_id, f"/m/t-{track_id}.mp3"))
    conn.commit()
    record_rule(
        conn, "track", track_id, bool(monitored), PROVENANCE_LEGACY
    )
    recompute_wanted(conn, track_ids=[track_id])
    return track_id


def test_upgrade_candidates_only_monitored_upgrade_policies_with_files(imported_conn):
    conn = imported_conn
    t_cutoff = _seed(conn, policy="until_cutoff")
    t_top = _seed(conn, policy="until_top")
    t_acceptable = _seed(conn, policy="acceptable")
    t_unmonitored = _seed(conn, policy="until_cutoff", monitored=0)
    t_fileless = _seed(conn, policy="until_cutoff", with_file=False)

    ids = set(upgrade_candidate_track_ids(conn))
    assert t_cutoff in ids
    assert t_top in ids
    assert t_acceptable not in ids
    assert t_unmonitored not in ids
    assert t_fileless not in ids


def test_upgrade_candidates_follow_wanted_projection_not_legacy_flag(imported_conn):
    conn = imported_conn
    projected_wanted = _seed(conn, policy="until_cutoff", monitored=0)
    projected_unwanted = _seed(conn, policy="until_cutoff", monitored=1)
    record_rule(conn, "track", projected_wanted, True, PROVENANCE_USER)
    record_rule(conn, "track", projected_unwanted, False, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[projected_wanted, projected_unwanted])

    ids = set(upgrade_candidate_track_ids(conn))

    assert projected_wanted in ids
    assert projected_unwanted not in ids


def test_upgrade_candidates_respect_active_manual_quality_skip(imported_conn):
    conn = imported_conn
    track_id = _seed(conn, policy="until_cutoff")
    path = conn.execute(
        "SELECT path FROM lib2_track_files WHERE track_id=?", (track_id,)
    ).fetchone()[0]
    conn.execute(
        """INSERT INTO lib2_manual_skips(
               file_path, skipped_checks, profile_id, acknowledged)
           VALUES(?, '["quality"]', 1, 0)""",
        (path,),
    )

    assert track_id not in upgrade_candidate_track_ids(conn)
    conn.execute("UPDATE lib2_manual_skips SET acknowledged=1 WHERE file_path=?", (path,))
    assert track_id in upgrade_candidate_track_ids(conn)


def test_payload_carries_app_wide_profile_id(imported_conn):
    conn = imported_conn
    track_id = _seed(conn, policy="until_cutoff")
    payload = track_wishlist_payload(conn, track_id)
    assert payload is not None
    profile_id = conn.execute(
        "SELECT quality_profile_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()["quality_profile_id"]
    assert payload["quality_profile_id"] == profile_id
    # An MP3 under an until_cutoff FLAC-only profile is an upgrade candidate.
    assert payload["_should_queue"] is True
    assert payload["_source_info"]["quality_profile_id"] == profile_id


def test_unknown_quality_queues_existing_file_for_shared_probe_pipeline(imported_conn):
    track_id = _seed(imported_conn, policy="until_cutoff")
    imported_conn.execute(
        "UPDATE lib2_track_files SET format='unknown' WHERE track_id=?",
        (track_id,),
    )

    payload = track_wishlist_payload(imported_conn, track_id)

    assert payload is not None
    assert payload["_should_queue"] is True
    assert payload["_source_info"]["quality_evaluation"] == "unknown"
