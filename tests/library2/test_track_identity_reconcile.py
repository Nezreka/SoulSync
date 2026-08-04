"""Multi-provider identity healing for exact Library-v2 album releases."""

from __future__ import annotations

import json

from core.library2.provider_adapters import (
    TracklistProviderResult,
    TracklistTrack,
)
from core.library2.track_identity_reconcile import (
    reconcile_album_track_provider_ids,
)


def _provider_result(provider, album_id, tracks):
    return TracklistProviderResult(
        provider=provider,
        provider_entity_id=album_id,
        tracks=tuple(
            TracklistTrack(
                title=title,
                track_number=number,
                disc_number=1,
                duration_ms=duration,
                provider=provider,
                provider_id=track_id,
                isrc=isrc,
            )
            for title, number, duration, track_id, isrc in tracks
        ),
    )


def test_reconcile_merges_every_provider_id_and_isrc_without_overwriting(imported_conn):
    album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()["id"]
    imported_conn.execute(
        """UPDATE lib2_albums
              SET spotify_id='sp-views',
                  external_ids='{"deezer":"dz-views","itunes":"it-views"}'
            WHERE id=?""",
        (album_id,),
    )
    one_dance = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND title='One Dance'",
        (album_id,),
    ).fetchone()["id"]
    imported_conn.execute(
        "UPDATE lib2_tracks SET external_ids='{\"deezer\":\"keep-me\"}' WHERE id=?",
        (one_dance,),
    )

    spotify = _provider_result(
        "spotify",
        "sp-views",
        [
            ("One Dance", 1, 200000, "sp-one", "USAAA2600001"),
            # Number differs, but this title is unique on both exact releases.
            ("Hotline Bling", 99, 180000, "sp-hotline", "USAAA2600002"),
        ],
    )
    deezer = _provider_result(
        "deezer",
        "dz-views",
        [
            ("One Dance", 1, 200000, "dz-one", "USAAA2600001"),
            ("Hotline Bling", 2, 180000, "dz-hotline", "USAAA2600002"),
        ],
    )

    stats = reconcile_album_track_provider_ids(
        imported_conn,
        album_id,
        provider_results=(spotify, deezer),
    )

    one = imported_conn.execute(
        """SELECT spotify_id, isrc, external_ids
             FROM lib2_tracks WHERE id=?""",
        (one_dance,),
    ).fetchone()
    hotline = imported_conn.execute(
        """SELECT spotify_id, isrc, external_ids
             FROM lib2_tracks WHERE album_id=? AND title='Hotline Bling'""",
        (album_id,),
    ).fetchone()
    one_ids = json.loads(one["external_ids"])
    hotline_ids = json.loads(hotline["external_ids"])

    assert one["spotify_id"] == "sp-one"
    assert one["isrc"] == "USAAA2600001"
    assert one_ids["deezer"] == "keep-me", "conflicting identity is preserved"
    assert hotline["spotify_id"] == "sp-hotline"
    assert hotline["isrc"] == "USAAA2600002"
    assert hotline_ids["deezer"] == "dz-hotline"
    assert stats == {
        "providers": 2,
        "matched": 4,
        "ids_added": 5,
        "conflicts": 1,
        "skipped": 0,
    }


def test_reconcile_rejects_same_position_when_title_disagrees(imported_conn):
    album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()["id"]
    wrong_edition = _provider_result(
        "deezer",
        "wrong-edition",
        [("Completely Different Track", 1, 200000, "dz-wrong", None)],
    )

    stats = reconcile_album_track_provider_ids(
        imported_conn,
        album_id,
        provider_results=(wrong_edition,),
    )

    assert stats["matched"] == 0
    assert stats["skipped"] == 1
    rows = imported_conn.execute(
        "SELECT external_ids FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchall()
    assert all("dz-wrong" not in row["external_ids"] for row in rows)
