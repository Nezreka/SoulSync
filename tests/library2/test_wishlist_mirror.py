"""Shared wishlist mirror: candidate selection for the upgrade scan."""

from __future__ import annotations

import json

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
        "INSERT INTO lib2_tracks(album_id, title, monitored, quality_profile_id, "
        "quality_profile_explicit) VALUES(?, 'T', ?, ?, 1)",
        (album_id, monitored, profile_id))
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
    t_none = _seed(conn, policy="none")
    t_unmonitored = _seed(conn, policy="until_cutoff", monitored=0)
    t_fileless = _seed(conn, policy="until_cutoff", with_file=False)

    ids = set(upgrade_candidate_track_ids(conn))
    assert t_cutoff in ids
    assert t_top in ids
    assert t_acceptable in ids
    assert t_none not in ids
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


def test_payload_uses_namespaced_non_spotify_identity_end_to_end(imported_conn):
    track_id = _seed(
        imported_conn, policy="acceptable", with_file=False,
    )
    row = imported_conn.execute(
        """SELECT t.album_id, al.primary_artist_id
             FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.id=?""",
        (track_id,),
    ).fetchone()
    imported_conn.execute(
        "UPDATE lib2_tracks SET external_ids=? WHERE id=?",
        (json.dumps({"deezer": "DZ-TRACK", "itunes": "IT-TRACK"}), track_id),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET external_ids=? WHERE id=?",
        (json.dumps({"deezer": "DZ-ALBUM"}), row["album_id"]),
    )
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids=? WHERE id=?",
        (json.dumps({"deezer": "DZ-ARTIST"}), row["primary_artist_id"]),
    )

    payload = track_wishlist_payload(imported_conn, track_id)

    assert payload["provider"] == "deezer"
    assert payload["source"] == "deezer"
    assert payload["id"] == "DZ-TRACK"
    assert payload["provider_ids"] == {
        "deezer": "DZ-TRACK", "itunes": "IT-TRACK",
    }
    assert payload["album"]["id"] == "DZ-ALBUM"
    assert payload["album"]["provider_ids"] == {"deezer": "DZ-ALBUM"}
    assert payload["artists"][0]["provider_ids"] == {"deezer": "DZ-ARTIST"}


def test_artist_mirror_uses_non_spotify_provider_identity(imported_conn):
    from core.library2 import mirror_outbox

    artist_id = imported_conn.execute(
        "INSERT INTO lib2_artists(name, external_ids) VALUES(?, ?)",
        ("Deezer Native", json.dumps({"deezer": "DZ-ARTIST"})),
    ).lastrowid

    outbox_ids = mirror_outbox.enqueue_artist_watchlist(
        imported_conn, artist_id, True,
    )

    assert len(outbox_ids) == 1
    data = json.loads(imported_conn.execute(
        "SELECT payload FROM lib2_mirror_outbox WHERE id=?", (outbox_ids[0],)
    ).fetchone()["payload"])
    assert data == {
        "ext": "DZ-ARTIST",
        "name": "Deezer Native",
        "source": "deezer",
        "quality_profile_id": 1,
    }


def test_upgrade_payload_never_sets_the_legacy_enhance_flag(imported_conn):
    """#1109 guard: the legacy Artist-Enhance flag makes the import pipeline
    write the upgrade back to ``original_file_path``'s folder. That path is the
    MEDIA SERVER's view of the library, which this process usually cannot
    reach. The V2 upgrade tool must keep using the normal path template, so it
    must never set ``enhance`` — even though it records the original path for
    retirement/quality comparison."""
    track_id = _seed(imported_conn, policy="until_cutoff")

    payload = track_wishlist_payload(imported_conn, track_id)

    assert payload is not None
    source_info = payload["_source_info"]
    assert source_info["upgrade_check"] is True
    assert source_info.get("original_file_path")
    assert "enhance" not in source_info


def test_payload_album_carries_images(imported_conn):
    """Library-v2 wishlist rows used to carry no `album.images` at all, so the
    UI (which reads `images[0].url`) drew a blank tile for every one of them —
    373 of 611 rows in the 2026-08-22 production report — and the import
    pipeline, which reads the same slot for cover.jpg, had nothing to work
    from either."""
    conn = imported_conn
    track_id = _seed(conn, policy="acceptable", with_file=False)
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]

    payload = track_wishlist_payload(conn, track_id)
    images = payload["album"]["images"]
    assert images, "a Library-v2 wishlist payload must never be image-less"
    assert images[0]["url"] == f"/api/library/v2/artwork/album/{album_id}"


def test_payload_album_images_lead_with_the_local_endpoint(imported_conn):
    """Same precedence as the Library v2 pages: the locally cached copy is the
    primary url, the provider CDN cover only stands in while a cold build runs.
    The import pipeline reads the same list and must take the CDN entry, which
    is why it asks for the first FETCHABLE url rather than images[0]."""
    conn = imported_conn
    track_id = _seed(conn, policy="acceptable", with_file=False)
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
    conn.execute("UPDATE lib2_albums SET image_url=? WHERE id=?",
                 ("https://i.scdn.co/image/cover", album_id))
    conn.commit()

    images = track_wishlist_payload(conn, track_id)["album"]["images"]
    assert [i["url"] for i in images] == [
        f"/api/library/v2/artwork/album/{album_id}", "https://i.scdn.co/image/cover",
    ]

    from core.library2.wishlist_art import first_fetchable_image_url
    assert first_fetchable_image_url(images) == "https://i.scdn.co/image/cover"


def test_payload_album_images_reject_a_media_server_cover(imported_conn):
    """A `/rest/..` cover only loads if the browser can reach Navidrome itself;
    it must never be the primary image."""
    conn = imported_conn
    track_id = _seed(conn, policy="acceptable", with_file=False)
    album_id = conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)).fetchone()[0]
    conn.execute("UPDATE lib2_albums SET image_url=? WHERE id=?",
                 ("/rest/getCoverArt.view?id=al-1", album_id))
    conn.commit()

    images = track_wishlist_payload(conn, track_id)["album"]["images"]
    assert images[0]["url"] == f"/api/library/v2/artwork/album/{album_id}"
