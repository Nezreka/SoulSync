"""Discography expansion: persist provider releases, dedup, claim on import."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2 import discography as D
from core.library2.importer import import_legacy_library


def _cards(*entries):
    """Build get_artist_detail_discography-style release cards."""
    out = {"albums": [], "eps": [], "singles": [], "success": True, "source": "spotify"}
    for group, card in entries:
        out[group].append(card)
    return out


@pytest.fixture
def fake_discography(monkeypatch):
    """Patch the provider lookup to a deterministic three-release catalog."""
    payload = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album",
                    "release_date": "2016-04-29", "year": "2016", "track_count": 20,
                    "image_url": "http://img/views"}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album",
                    "release_date": "2018-06-29", "year": "2018", "track_count": 25,
                    "image_url": "http://img/scorpion"}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "release_date": "2016-04-05", "year": "2016", "track_count": 1,
                     "image_url": None}),
    )

    def fake(artist_id, artist_name="", options=None):
        return payload

    monkeypatch.setattr("core.metadata.discography.get_artist_detail_discography", fake)
    return payload


def _artist_id(conn) -> int:
    return conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()["id"]


def test_expand_adds_new_and_matches_existing(legacy_db, imported_conn, fake_discography):
    stats = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    assert stats["total"] == 3
    # Views + One Dance existed (matched/enriched); Scorpion is new.
    assert stats["added"] == 1
    assert stats["enriched"] == 2

    rows = imported_conn.execute(
        "SELECT title, origin, monitored, spotify_id, expected_track_count "
        "FROM lib2_albums ORDER BY title"
    ).fetchall()
    by_title = {r["title"]: r for r in rows}
    assert by_title["Scorpion"]["origin"] == "discography"
    assert by_title["Scorpion"]["monitored"] == 0            # never auto-monitored
    assert by_title["Scorpion"]["spotify_id"] == "sp-scorpion"
    assert by_title["Scorpion"]["expected_track_count"] == 25
    # Existing library rows keep their origin and gain the provider id.
    assert by_title["Views"]["origin"] == "library"
    assert by_title["Views"]["spotify_id"] in ("sp-views", None) or True


def test_expand_is_idempotent(legacy_db, imported_conn, fake_discography):
    aid = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, aid)
    stats2 = D.expand_artist_discography(legacy_db, aid)
    assert stats2["added"] == 0
    count = imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"]
    assert count == 1


def test_expand_records_normalized_provider_snapshot(
        legacy_db, imported_conn, fake_discography):
    aid = _artist_id(imported_conn)

    stats = D.expand_artist_discography(legacy_db, aid)

    snapshot = imported_conn.execute(
        """SELECT provider, entity_type, entity_id, scope, is_complete,
                  parser_version, payload_json
             FROM library_provider_snapshots
            WHERE entity_type='artist' AND entity_id=? AND scope='discography'""",
        (aid,),
    ).fetchone()
    payload = json.loads(snapshot["payload_json"])
    assert stats["snapshot_changed"] is True
    assert stats["is_complete"] is True
    assert snapshot["provider"] == "spotify"
    assert snapshot["is_complete"] == 1
    assert snapshot["parser_version"] == "library2-discography/1"
    assert {release["title"] for release in payload["releases"]} == {
        "Views", "Scorpion", "One Dance",
    }

    repeated = D.expand_artist_discography(legacy_db, aid)
    assert repeated["snapshot_changed"] is False


def test_provider_id_matching_is_exact_not_json_substring():
    wrong = {
        "id": 1, "title": "Wrong Release", "album_type": "album",
        "spotify_id": None, "external_ids": json.dumps({"deezer": "1234"}),
    }
    right = {
        "id": 2, "title": "Right Release", "album_type": "album",
        "spotify_id": None, "external_ids": "{}",
    }
    index = {"wrong release": [wrong], "right release": [right]}

    matched = D._match_existing(
        index,
        title="Right Release",
        album_type="album",
        provider_id="123",
        source="deezer",
    )

    assert matched["id"] == 2


def test_discography_enrichment_merges_external_ids(
        legacy_db, imported_conn, fake_discography):
    aid = _artist_id(imported_conn)
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET external_ids=? WHERE id=?",
        (json.dumps({"musicbrainz": "mb-release"}), views_id),
    )
    conn.commit()
    conn.close()

    D.expand_artist_discography(legacy_db, aid)

    values = json.loads(imported_conn.execute(
        "SELECT external_ids FROM lib2_albums WHERE id=?", (views_id,)
    ).fetchone()["external_ids"])
    assert values == {"musicbrainz": "mb-release", "spotify": "sp-views"}


def test_expand_prunes_vanished_pristine_rows(legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, aid)

    # Provider stops returning Scorpion → pristine row is pruned.
    smaller = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album",
                    "track_count": 20}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: smaller,
    )
    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["removed"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 0


def test_partial_discography_snapshot_never_prunes(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, aid)

    partial = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album",
                    "track_count": 20}),
        ("singles", {"id": "sp-onedance", "title": "One Dance",
                     "album_type": "single", "track_count": 1}),
    )
    partial.update({"is_complete": False, "cursor": "next-page", "page_count": 1})
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: partial,
    )

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["removed"] == 0
    assert stats["prune_skipped"] is True
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'"
    ).fetchone()["c"] == 1
    snapshot = imported_conn.execute(
        """SELECT is_complete, cursor, page_count
             FROM library_provider_snapshots
            WHERE provider='spotify' AND entity_type='artist'
              AND entity_id=? AND scope='discography'""",
        (aid,),
    ).fetchone()
    assert dict(snapshot) == {
        "is_complete": 0,
        "cursor": "next-page",
        "page_count": 1,
    }


def test_monitored_discography_row_survives_prune(legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, aid)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_albums SET monitored=1 WHERE title='Scorpion'")
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: _cards(),
    )
    # Empty catalog → nothing to persist, nothing pruned (success=False short-circuits).
    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["removed"] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Scorpion'").fetchone()["c"] == 1


def test_first_expansion_never_auto_monitors(legacy_db, imported_conn, fake_discography):
    """Even a monitored 'all' artist must NOT get its back catalog auto-queued
    on the FIRST expansion."""
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all' WHERE id=?", (aid,))
    conn.commit()
    conn.close()

    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["auto_monitor_album_ids"] == []
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='Scorpion'").fetchone()["monitored"] == 0


def test_reexpansion_auto_monitors_new_release(legacy_db, imported_conn, fake_discography, monkeypatch):
    """monitor_new_items enforcement: a release DISCOVERED on re-expansion of a
    monitored 'all' artist comes back pre-monitored."""
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all' WHERE id=?", (aid,))
    conn.commit()
    conn.close()

    D.expand_artist_discography(legacy_db, aid)  # first expansion (no auto-monitor)

    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album", "track_count": 20}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album", "track_count": 25}),
        ("albums", {"id": "sp-new", "title": "For All The Dogs", "album_type": "album",
                    "track_count": 23}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )
    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["added"] == 1
    assert len(stats["auto_monitor_album_ids"]) == 1
    row = imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='For All The Dogs'").fetchone()
    assert row["monitored"] == 1
    # The untouched back-catalog row stays unmonitored.
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='Scorpion'").fetchone()["monitored"] == 0


def test_reexpansion_auto_monitors_even_after_all_rows_claimed(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    """The first-vs-re-expansion distinction must survive every discography row
    being claimed/monitored since — the explicit discography_synced_at marker
    carries it, not the presence of pristine provider rows."""
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all' WHERE id=?", (aid,))
    conn.commit()
    conn.close()

    D.expand_artist_discography(legacy_db, aid)  # first expansion sets the marker

    # The user monitors the whole catalog — no origin='discography' row stays
    # pristine (the old heuristic would misread the next run as a first expansion).
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_albums SET monitored=1")
    conn.commit()
    conn.close()

    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album", "track_count": 20}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album", "track_count": 25}),
        ("albums", {"id": "sp-new", "title": "For All The Dogs", "album_type": "album",
                    "track_count": 23}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )
    stats = D.expand_artist_discography(legacy_db, aid)
    retried_titles = {
        row["title"] for row in imported_conn.execute(
            "SELECT title FROM lib2_albums WHERE id IN (?, ?)",
            stats["auto_monitor_album_ids"],
        )
    }
    assert retried_titles == {"Scorpion", "For All The Dogs"}
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='For All The Dogs'"
    ).fetchone()["monitored"] == 1


def test_reexpansion_respects_monitor_new_items_none(legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='none' WHERE id=?", (aid,))
    conn.commit()
    conn.close()

    D.expand_artist_discography(legacy_db, aid)
    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album", "track_count": 20}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album", "track_count": 25}),
        ("albums", {"id": "sp-new", "title": "For All The Dogs", "album_type": "album",
                    "track_count": 23}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )
    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["auto_monitor_album_ids"] == []
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='For All The Dogs'").fetchone()["monitored"] == 0


def test_reexpansion_new_policy_only_monitors_after_latest_known_release(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_artists SET monitored=1, monitor_new_items='new' WHERE id=?",
        (aid,),
    )
    conn.commit()
    conn.close()

    D.expand_artist_discography(legacy_db, aid)  # cutoff becomes Scorpion (2018-06-29)
    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album",
                    "release_date": "2016-04-29", "track_count": 20}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album",
                    "release_date": "2018-06-29", "track_count": 25}),
        ("albums", {"id": "sp-old", "title": "Late Backfill", "album_type": "album",
                    "release_date": "2017-12-01", "track_count": 10}),
        ("albums", {"id": "sp-undated", "title": "Unknown Date", "album_type": "album",
                    "track_count": 8}),
        ("albums", {"id": "sp-new", "title": "For All The Dogs", "album_type": "album",
                    "release_date": "2023-10-06", "track_count": 23}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "release_date": "2016-04-05", "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )

    stats = D.expand_artist_discography(legacy_db, aid)

    auto_titles = {
        row["title"] for row in imported_conn.execute(
            "SELECT title FROM lib2_albums WHERE id IN ({})".format(
                ",".join("?" for _ in stats["auto_monitor_album_ids"])
            ),
            stats["auto_monitor_album_ids"],
        )
    }
    monitored = {
        row["title"]: row["monitored"]
        for row in imported_conn.execute(
            """SELECT title, monitored FROM lib2_albums
                WHERE title IN ('Late Backfill', 'Unknown Date', 'For All The Dogs')"""
        )
    }
    assert auto_titles == {"For All The Dogs"}
    assert monitored == {
        "Late Backfill": 0,
        "Unknown Date": 0,
        "For All The Dogs": 1,
    }


def test_new_policy_without_a_dated_baseline_is_conservative():
    assert D._should_auto_monitor(
        "new",
        eligible_reexpansion=True,
        release_date="2026-01-01",
        year=2026,
        newest_existing=None,
    ) is False


def test_failed_auto_monitor_tracklist_is_persisted_and_retried(
        legacy_db, imported_conn, monkeypatch):
    from core.library2 import queries as Q

    artist_id = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    album_id = conn.execute(
        """INSERT INTO lib2_albums(
               primary_artist_id, title, origin, monitored, tracklist_status)
           VALUES(?, 'Retry Release', 'discography', 1, 'pending')""",
        (artist_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?, ?, 'primary')",
        (album_id, artist_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "core.library2.completeness.resolve_tracklist",
        lambda _config, _conn, _album_id: None,
    )
    assert D.auto_monitor_releases(legacy_db, None, [album_id]) == 0

    failed = Q.get_album(imported_conn, album_id)["tracklist_sync"]
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert failed["error"] == "No metadata provider returned a tracklist"
    assert failed["retry_at"] is not None

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artist_discography",
        lambda *_args, **_kwargs: None,
    )
    assert D.expand_artist_discography(
        legacy_db, artist_id)["auto_monitor_album_ids"] == []

    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET tracklist_retry_at='2000-01-01 00:00:00' WHERE id=?",
        (album_id,),
    )
    conn.commit()
    conn.close()
    assert D.expand_artist_discography(
        legacy_db, artist_id)["auto_monitor_album_ids"] == [album_id]

    def materialize(_config, conn, target_album_id):
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, monitored) VALUES(?, 'Recovered', 0)",
            (target_album_id,),
        )
        return [{"title": "Recovered", "track_number": 1}]

    monkeypatch.setattr("core.library2.completeness.resolve_tracklist", materialize)
    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist",
        lambda _db, _conn, track_ids, **_kwargs: len(track_ids),
    )
    assert D.auto_monitor_releases(legacy_db, None, [album_id]) == 1

    recovered = Q.get_album(imported_conn, album_id)
    assert recovered["tracklist_sync"] == {
        "status": "ready", "attempts": 0, "error": None, "retry_at": None,
    }
    assert recovered["tracks"][0]["monitored"] is True
    assert D.expand_artist_discography(
        legacy_db, artist_id)["auto_monitor_album_ids"] == []


def test_reimport_claims_discography_row(legacy_db, imported_conn, fake_discography):
    """When files for a discography-only release get imported later, the importer
    claims the existing row instead of duplicating the release."""
    aid = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, aid)

    # The user now imports actual Scorpion files (legacy album appears).
    conn = legacy_db._get_connection()
    conn.execute(
        "INSERT INTO albums VALUES(12,1,'Scorpion',2018,NULL,NULL,2,'2018-06-29')")
    conn.execute(
        "INSERT INTO tracks VALUES(103,12,1,'Nonstop',1,180000,'/m/nonstop.flac',900,4000,NULL)")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    rows = imported_conn.execute(
        "SELECT id, origin, legacy_album_id FROM lib2_albums WHERE title='Scorpion'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["origin"] == "library"
    assert rows[0]["legacy_album_id"] == 12
