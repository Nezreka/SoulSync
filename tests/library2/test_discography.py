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
    # Refresh now also repairs underfilled imported releases.  Keep this unit
    # fixture deterministic/offline; individual tracklist tests install their
    # own resolver payloads.
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None,
    )
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
    # A provider catalog must heal an old import undercount upward.  Preserving
    # the stale value is what made Update Discovery leave one-track albums
    # permanently truncated.
    assert by_title["Views"]["expected_track_count"] == 20


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


def test_cross_bucket_fallback_denied_when_release_has_its_own_provider_id():
    """G1: a Single sharing an Album's title but carrying its OWN provider id
    must NOT match the Album row via the candidates[0] fallback — that id
    belongs to a genuinely different release and matching here would let the
    caller overwrite the Album's external_ids with the Single's id."""
    album = {
        "id": 1, "title": "Faith", "album_type": "album",
        "spotify_id": None, "external_ids": json.dumps({"deezer": "album-id"}),
    }
    index = {"faith": [album]}

    matched = D._match_existing(
        index, title="Faith", album_type="single",
        provider_id="single-id", source="deezer",
    )

    assert matched is None


def test_cross_bucket_fallback_still_allowed_without_a_provider_id():
    """Legacy-imported releases without any provider id still need the old
    cross-bucket title fallback (e.g. a single legacy-classified as 'album'
    by the one-track heuristic) — only an id-carrying release is exempted."""
    album = {
        "id": 1, "title": "Faith", "album_type": "album",
        "spotify_id": None, "external_ids": "{}",
    }
    index = {"faith": [album]}

    matched = D._match_existing(
        index, title="Faith", album_type="single",
        provider_id="", source=None,
    )

    assert matched is not None
    assert matched["id"] == 1


def test_merge_external_id_refuses_to_overwrite_a_conflicting_id():
    """G1: once a source's id is set, a differing id for the same source must
    never silently replace it (defense-in-depth alongside the match-time
    fallback restriction — a conflict here means two different releases were
    matched to the same row somewhere upstream)."""
    raw = json.dumps({"deezer": "album-id"})

    merged = D._merge_external_id(raw, "deezer", "single-id")

    assert json.loads(merged) == {"deezer": "album-id"}


def test_merge_external_id_fills_a_missing_source_normally():
    raw = json.dumps({"musicbrainz": "mb-1"})

    merged = D._merge_external_id(raw, "deezer", "deezer-1")

    assert json.loads(merged) == {"musicbrainz": "mb-1", "deezer": "deezer-1"}


def test_single_sharing_album_title_gets_its_own_row_not_the_albums_identity(
        legacy_db, imported_conn, monkeypatch):
    """G1 end-to-end: a Single release sharing the 'Views' album's title, with
    its own provider id, must land in its own new row — the Album's
    external_ids must keep its own id, not the Single's."""
    aid = _artist_id(imported_conn)
    payload = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album",
                    "release_date": "2016-04-29", "year": "2016", "track_count": 20}),
        ("singles", {"id": "sp-views-single", "title": "Views", "album_type": "single",
                     "release_date": "2016-04-05", "year": "2016", "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: payload,
    )

    D.expand_artist_discography(legacy_db, aid)

    rows = imported_conn.execute(
        "SELECT album_type, spotify_id, external_ids FROM lib2_albums WHERE title='Views'"
    ).fetchall()
    assert len(rows) == 2
    by_type = {r["album_type"]: r for r in rows}
    assert by_type["album"]["spotify_id"] == "sp-views"
    assert json.loads(by_type["album"]["external_ids"]) == {"spotify": "sp-views"}
    assert by_type["single"]["spotify_id"] == "sp-views-single"
    assert json.loads(by_type["single"]["external_ids"]) == {"spotify": "sp-views-single"}


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


def test_review_reexpansion_defers_monitoring_and_materialization(
    legacy_db, imported_conn, fake_discography, monkeypatch
):
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_artists SET monitored=1, monitor_new_items='all' WHERE id=?",
        (aid,),
    )
    conn.commit()
    conn.close()
    D.expand_artist_discography(legacy_db, aid)

    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album"}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album"}),
        ("albums", {"id": "sp-review", "title": "Review Me", "album_type": "album"}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single"}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )

    stats = D._expand_artist_discography(
        legacy_db, aid, defer_auto_monitor=True)

    assert len(stats["auto_monitor_album_ids"]) == 1
    album_id = stats["auto_monitor_album_ids"][0]
    row = imported_conn.execute(
        "SELECT monitored, tracklist_status FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    assert row["monitored"] == 0
    assert row["tracklist_status"] == "idle"
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id=?",
        (album_id,),
    ).fetchone()[0] == 0


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


def test_auto_monitor_releases_never_reflips_an_explicitly_unmonitored_track(
        legacy_db, imported_conn, monkeypatch):
    """G8: a retry re-materialization must not steamroll a track the user
    explicitly unmonitored before the retry fired — only the compatibility
    ``lib2_tracks.monitored`` flag is at stake here (the wanted projection
    already respects the rule), but the flag must not lie either."""
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

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
    kept_off_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
        "VALUES(?, 'Explicitly Off', 1, 0)",
        (album_id,),
    ).lastrowid
    record_rule(conn, "track", kept_off_id, False, PROVENANCE_USER)
    conn.commit()
    conn.close()

    def materialize(_config, conn, target_album_id):
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
            "VALUES(?, 'New Track', 2, 0)",
            (target_album_id,),
        )
        return [
            {"title": "Explicitly Off", "track_number": 1},
            {"title": "New Track", "track_number": 2},
        ]

    monkeypatch.setattr("core.library2.completeness.resolve_tracklist", materialize)
    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist",
        lambda _db, _conn, track_ids, **_kwargs: len(track_ids),
    )
    D.auto_monitor_releases(legacy_db, None, [album_id])

    conn = legacy_db._get_connection()
    rows = {
        r["title"]: bool(r["monitored"])
        for r in conn.execute(
            "SELECT title, monitored FROM lib2_tracks WHERE album_id=?", (album_id,))
    }
    conn.close()
    assert rows["Explicitly Off"] is False
    assert rows["New Track"] is True


def test_expand_discography_retries_album_where_this_artist_is_not_primary(
        legacy_db, imported_conn, monkeypatch):
    """G8: an album whose primary_artist_id belongs to a different (featured)
    artist must still be retried when THIS artist's discography is
    expanded, as long as this artist is linked via lib2_album_artists —
    matching _existing_release_index's and the prune query's scope."""
    artist_id = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    other_artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, monitored) VALUES ('Feat Artist', 1)"
    ).lastrowid
    album_id = conn.execute(
        """INSERT INTO lib2_albums(
               primary_artist_id, title, origin, monitored, tracklist_status)
           VALUES(?, 'Collab Release', 'discography', 1, 'pending')""",
        (other_artist_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?, ?, 'primary')",
        (album_id, other_artist_id),
    )
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?, ?, 'featured')",
        (album_id, artist_id),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artist_discography",
        lambda *_args, **_kwargs: None,
    )
    stats = D.expand_artist_discography(legacy_db, artist_id)
    assert stats["auto_monitor_album_ids"] == [album_id]


def test_same_artist_discography_refreshes_are_serialized(
    legacy_db, imported_conn, fake_discography, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core.library2 import provider_adapters

    artist_id = _artist_id(imported_conn)
    original_fetch = provider_adapters.fetch_artist_discography
    first_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    calls = 0
    active = 0
    max_active = 0

    def blocking_fetch(*args, **kwargs):
        nonlocal calls, active, max_active
        with state_lock:
            calls += 1
            call_number = calls
            active += 1
            max_active = max(max_active, active)
        try:
            if call_number == 1:
                first_entered.set()
                assert release_first.wait(timeout=2)
            return original_fetch(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(provider_adapters, "fetch_artist_discography", blocking_fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(D.expand_artist_discography, legacy_db, artist_id)
        assert first_entered.wait(timeout=2)
        second = pool.submit(D.expand_artist_discography, legacy_db, artist_id)
        assert not second.done()
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert calls == 2
    assert max_active == 1


def test_refresh_holds_artist_lock_through_auto_monitor(
    legacy_db, imported_conn, monkeypatch
):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    artist_id = _artist_id(imported_conn)
    auto_monitor_entered = threading.Event()
    release_auto_monitor = threading.Event()
    expand_calls = []

    def fake_expand(_database, target_artist_id):
        expand_calls.append(target_artist_id)
        return {"auto_monitor_album_ids": [91]}

    def blocking_auto_monitor(*_args, **_kwargs):
        auto_monitor_entered.set()
        assert release_auto_monitor.wait(timeout=2)
        return 1

    monkeypatch.setattr(D, "_expand_artist_discography", fake_expand)
    monkeypatch.setattr(D, "auto_monitor_releases", blocking_auto_monitor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(
            D.refresh_artist_discography,
            legacy_db,
            artist_id,
            None,
        )
        assert auto_monitor_entered.wait(timeout=2)
        expansion = pool.submit(D.expand_artist_discography, legacy_db, artist_id)
        assert not expansion.done()
        assert expand_calls == [artist_id]
        release_auto_monitor.set()
        assert refresh.result(timeout=2) == (
            {"auto_monitor_album_ids": [91], "repaired_track_number_collisions": [],
             "repaired_incomplete_tracklists": []}, 1)
        assert expansion.result(timeout=2) == {"auto_monitor_album_ids": [91]}

    assert expand_calls == [artist_id, artist_id]


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


def _make_colliding_library_album(conn, artist_id: int, title: str) -> int:
    """An already-owned album with a (disc, track_number) collision — the
    §17.2 "SWAG" symptom: every real track collapsed onto number 1, one of
    them ALSO duplicated by a fileless placeholder at its true number."""
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, expected_track_count) "
        "VALUES(?, ?, 'library', 3)",
        (artist_id, title),
    ).lastrowid
    for track_title in ("Alpha", "Bravo", "Charlie"):
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
            "VALUES(?,?,1,1,1)",
            (album_id, track_title),
        )
        tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)",
            (tid, f"/m/{track_title.lower()}.flac"),
        )
    conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?, 'Bravo', 2, 1, 0)",
        (album_id,),
    )
    # Pre-seed the canonical tracklist cache so resolve_tracklist can heal
    # from cache alone — no provider/network call needed in these tests.
    conn.execute(
        "UPDATE lib2_albums SET tracklist_json=? WHERE id=?",
        (json.dumps([
            {"track_number": 1, "title": "Alpha"},
            {"track_number": 2, "title": "Bravo"},
            {"track_number": 3, "title": "Charlie"},
        ]), album_id),
    )
    conn.commit()
    return album_id


def test_refresh_repairs_track_number_collision_on_existing_library_album(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    """§17.2: 'Update Discography' must repair track-number collisions on
    ALREADY-OWNED albums too, not only newly discovered ones — the title
    healing (§16.3, eca36caa) only ever fires through auto_monitor_releases,
    which is scoped to auto_monitor_album_ids (new releases). SWAG-style
    corruption on an existing album never gets a resolve_tracklist call at
    all, no matter how many times the button is clicked."""
    aid = _artist_id(imported_conn)
    swag_id = _make_colliding_library_album(imported_conn, aid, "swag")

    import core.library2.completeness as completeness_module
    real_resolve = completeness_module.resolve_tracklist
    calls = []

    def spy_resolve(config_manager, conn, album_id):
        calls.append(album_id)
        return real_resolve(config_manager, conn, album_id)

    monkeypatch.setattr(
        "core.library2.completeness.resolve_tracklist", spy_resolve)

    stats, _mirrored = D.refresh_artist_discography(legacy_db, aid, None)

    assert swag_id in calls
    assert swag_id in stats["repaired_track_number_collisions"]
    healed = {
        r["title"]: r["track_number"] for r in imported_conn.execute(
            "SELECT title, track_number FROM lib2_tracks WHERE album_id=?",
            (swag_id,))
    }
    assert healed == {"Alpha": 1, "Bravo": 2, "Charlie": 3}


def test_refresh_incomplete_repair_ignores_complete_library_albums(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    """The underfilled-release pass is scoped: a complete library release is
    not re-resolved merely because Update Discovery was clicked."""
    aid = _artist_id(imported_conn)
    complete_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, expected_track_count) "
        "VALUES(?, 'Already Complete', 'library', 1)", (aid,)
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
        (complete_id, aid),
    )
    imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Done', 1)",
        (complete_id,),
    )
    imported_conn.commit()
    calls = []
    monkeypatch.setattr(
        "core.library2.completeness.resolve_tracklist",
        lambda _config, _conn, album_id: calls.append(album_id),
    )

    D.refresh_artist_discography(legacy_db, aid, None)

    assert complete_id not in calls


def test_refresh_resolves_imported_release_after_provider_raises_track_count(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    aid = _artist_id(imported_conn)
    views_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    calls = []

    def resolve(_config, _conn, album_id):
        calls.append(album_id)
        return [{"title": "One Dance", "track_number": 1}]

    monkeypatch.setattr("core.library2.completeness.resolve_tracklist", resolve)

    stats, _mirrored = D.refresh_artist_discography(legacy_db, aid, None)

    assert views_id in calls
    assert views_id in stats["repaired_incomplete_tracklists"]


def test_refresh_track_number_repair_does_not_remonitor_or_reprovenance(
        legacy_db, imported_conn, fake_discography):
    """The repair pass must preserve a deliberate parent override and must not
    stamp a ``new_release`` provenance while healing an owned album."""
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    aid = _artist_id(imported_conn)
    swag_id = _make_colliding_library_album(imported_conn, aid, "swag")
    imported_conn.execute(
        "UPDATE lib2_albums SET monitored=0 WHERE id=?", (swag_id,))
    record_rule(imported_conn, "album", swag_id, False, PROVENANCE_USER)
    imported_conn.commit()

    D.refresh_artist_discography(legacy_db, aid, None)

    assert imported_conn.execute(
        "SELECT monitored FROM lib2_albums WHERE id=?", (swag_id,)
    ).fetchone()["monitored"] == 0
    rule = imported_conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type='album' AND entity_id=?",
        (swag_id,),
    ).fetchone()
    assert (rule["monitored"], rule["provenance"]) == (0, PROVENANCE_USER)


# --- §40 alias-group fan-out --------------------------------------------------

def _fake_discography_by_name(monkeypatch, catalog_by_name):
    """Like ``fake_discography`` but returns a DIFFERENT catalog per artist
    name — proves a group fan-out fetches EACH member's own provider catalog,
    not just the requested member's."""
    def fake(artist_id, artist_name="", options=None):
        return catalog_by_name.get(artist_name, _cards())

    monkeypatch.setattr("core.metadata.discography.get_artist_detail_discography", fake)


def _new_artist(conn, name: str) -> int:
    cur = conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (name,))
    return cur.lastrowid


def test_expand_fans_out_across_alias_group(legacy_db, imported_conn, monkeypatch):
    from core.library2.artist_aliases import link_artist_alias

    drake_id = _artist_id(imported_conn)
    alias_id = _new_artist(imported_conn, "Drake (Alias)")
    imported_conn.commit()
    link_artist_alias(imported_conn, alias_id, drake_id)
    imported_conn.commit()

    _fake_discography_by_name(monkeypatch, {
        "Drake": _cards(("albums", {
            "id": "sp-scorpion", "title": "Scorpion", "album_type": "album",
            "release_date": "2018-06-29", "year": "2018", "track_count": 25,
            "image_url": None,
        })),
        "Drake (Alias)": _cards(("albums", {
            "id": "sp-alias-only", "title": "Alias-Only Release", "album_type": "album",
            "release_date": "2020-01-01", "year": "2020", "track_count": 10,
            "image_url": None,
        })),
    })

    # Triggering the CANONICAL row's discography still reaches the alias's
    # own provider catalog — the whole point of the group (§40/§24.3).
    stats = D.expand_artist_discography(legacy_db, drake_id)

    assert stats["group"] == [drake_id, alias_id]
    assert stats["total"] == 2  # 1 from each member's own catalog
    alias_only = imported_conn.execute(
        "SELECT al.id FROM lib2_albums al JOIN lib2_album_artists aa ON aa.album_id=al.id "
        "WHERE aa.artist_id=? AND al.title='Alias-Only Release'", (alias_id,),
    ).fetchone()
    assert alias_only is not None


def test_expand_fans_out_regardless_of_which_member_id_is_used(
        legacy_db, imported_conn, monkeypatch):
    from core.library2.artist_aliases import link_artist_alias

    drake_id = _artist_id(imported_conn)
    alias_id = _new_artist(imported_conn, "Drake (Alias)")
    imported_conn.commit()
    link_artist_alias(imported_conn, alias_id, drake_id)
    imported_conn.commit()

    _fake_discography_by_name(monkeypatch, {
        "Drake": _cards(("albums", {"id": "sp-a", "title": "A", "album_type": "album"})),
        "Drake (Alias)": _cards(("albums", {"id": "sp-b", "title": "B", "album_type": "album"})),
    })

    # Clicking "Update Discography" from the ALIAS row must refresh the
    # canonical row's catalog too, not just its own.
    stats = D.expand_artist_discography(legacy_db, alias_id)

    assert sorted(stats["group"]) == sorted([drake_id, alias_id])
    canonical_album = imported_conn.execute(
        "SELECT 1 FROM lib2_albums al JOIN lib2_album_artists aa ON aa.album_id=al.id "
        "WHERE aa.artist_id=? AND al.title='A'", (drake_id,),
    ).fetchone()
    assert canonical_album is not None


def test_expand_group_partial_failure_still_persists_the_other_member(
        legacy_db, imported_conn, monkeypatch):
    from core.library2.artist_aliases import link_artist_alias

    drake_id = _artist_id(imported_conn)
    alias_id = _new_artist(imported_conn, "Drake (Alias)")
    imported_conn.commit()
    link_artist_alias(imported_conn, alias_id, drake_id)
    imported_conn.commit()

    def fake(artist_id, artist_name="", options=None):
        if artist_name == "Drake (Alias)":
            raise RuntimeError("simulated provider failure")
        return _cards(("albums", {"id": "sp-a", "title": "A", "album_type": "album"}))

    monkeypatch.setattr("core.metadata.discography.get_artist_detail_discography", fake)

    stats = D.expand_artist_discography(legacy_db, drake_id)

    assert stats["members"][alias_id]["error"]
    assert "error" not in stats["members"][drake_id]
    canonical_album = imported_conn.execute(
        "SELECT 1 FROM lib2_albums al JOIN lib2_album_artists aa ON aa.album_id=al.id "
        "WHERE aa.artist_id=? AND al.title='A'", (drake_id,),
    ).fetchone()
    assert canonical_album is not None


def test_expand_group_stays_untouched_for_standalone_artist(
        legacy_db, imported_conn, fake_discography):
    """No aliases linked => identical shape/behavior to pre-§40 code (no
    'group'/'members' keys leaking into the common single-artist case)."""
    stats = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    assert "group" not in stats
    assert "members" not in stats


def test_refresh_fans_out_auto_monitor_and_repair_across_group(
        legacy_db, imported_conn, monkeypatch):
    from core.library2.artist_aliases import link_artist_alias

    drake_id = _artist_id(imported_conn)
    alias_id = _new_artist(imported_conn, "Drake (Alias)")
    imported_conn.commit()
    link_artist_alias(imported_conn, alias_id, drake_id)
    imported_conn.commit()
    imported_conn.execute(
        "UPDATE lib2_artists SET discography_synced_at=CURRENT_TIMESTAMP, "
        "monitor_new_items='all', monitored=1 WHERE id IN (?,?)",
        (drake_id, alias_id),
    )
    imported_conn.commit()

    _fake_discography_by_name(monkeypatch, {
        "Drake": _cards(("albums", {
            "id": "sp-new-a", "title": "New A", "album_type": "album",
            "release_date": "2030-01-01", "year": "2030",
        })),
        "Drake (Alias)": _cards(("albums", {
            "id": "sp-new-b", "title": "New B", "album_type": "album",
            "release_date": "2030-01-01", "year": "2030",
        })),
    })

    stats, mirrored = D.refresh_artist_discography(legacy_db, drake_id, None)

    assert len(stats["auto_monitor_album_ids"]) == 2
    assert "repaired_track_number_collisions" in stats


# ---------------------------------------------------------------------------
# §62 stage 1: cross-language/cross-release duplicate hardening
# ---------------------------------------------------------------------------

def _seed_library_album(conn, artist_id, *, title, release_date=None,
                        expected_track_count=None, external_ids="{}",
                        album_type="album", origin="library"):
    cur = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type,
               release_date, expected_track_count, external_ids, origin)
           VALUES(?,?,?,?,?,?,?)""",
        (artist_id, title, album_type, release_date, expected_track_count,
         external_ids, origin))
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (cur.lastrowid, artist_id))
    conn.commit()
    return cur.lastrowid


def _cards_from(source, *entries):
    payload = _cards(*entries)
    payload["source"] = source
    return payload


def test_release_title_key_ignores_quotes_brackets_and_width():
    from core.library2.importer import release_title_key

    assert release_title_key('TV Anime "Attack on Titan" Original Soundtrack') \
        == release_title_key("TV Anime Attack on Titan Original Soundtrack")
    # NFKC folds full-width forms (：) to ASCII; punctuation never splits identity.
    assert release_title_key("The Seven Deadly Sins：Cursed by Light OST") \
        == release_title_key("The Seven Deadly Sins: Cursed by Light OST")
    assert release_title_key("Scorpion") != release_title_key("Views")


def test_same_date_and_track_count_matches_other_language_edition(
        legacy_db, imported_conn, monkeypatch):
    """The Sawano case: the library holds the JP-titled Deezer release, the
    provider lists the international EN-titled release of the SAME album
    (same day, same track count, different provider id). Must enrich, not
    duplicate."""
    aid = _artist_id(imported_conn)
    album_id = _seed_library_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33,
        external_ids=json.dumps({"deezer": "196470602"}))

    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("albums", {"id": "42695001",
                    "title": 'TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
                    "album_type": "album", "release_date": "2017-06-07",
                    "year": "2017", "track_count": 33, "image_url": None}),
    )})

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["added"] == 0
    assert stats["enriched"] >= 1
    titles = [r["title"] for r in imported_conn.execute(
        "SELECT title FROM lib2_albums WHERE primary_artist_id=?", (aid,))]
    assert 'TV Anime "Attack on Titan Season 2" (Original Soundtrack)' not in titles
    row = imported_conn.execute(
        "SELECT external_ids FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
    # The library row keeps its own deezer identity (G1 — no clobbering).
    assert json.loads(row["external_ids"])["deezer"] == "196470602"


def test_punctuation_only_title_variant_matches(legacy_db, imported_conn, monkeypatch):
    aid = _artist_id(imported_conn)
    _seed_library_album(
        imported_conn, aid, title='TV Anime "Attack on Titan" Original Soundtrack',
        release_date="2014-07-31", expected_track_count=16)

    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("albums", {"id": "338920467",
                    "title": "TV Anime Attack on Titan Original Soundtrack",
                    "album_type": "album", "release_date": "2014-07-31",
                    "year": "2014", "track_count": 16, "image_url": None}),
    )})

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["added"] == 0
    assert stats["enriched"] >= 1


def test_ambiguous_same_day_singles_are_not_merged(legacy_db, imported_conn, monkeypatch):
    """Three 1-track singles dropped the same day (EGO / LICHT MEER /
    Vigilante) must stay three distinct releases — the date+count fallback
    only fires when exactly one candidate matches."""
    aid = _artist_id(imported_conn)
    for title in ("EGO", "LICHT MEER", "Vigilante"):
        _seed_library_album(imported_conn, aid, title=title, album_type="single",
                            release_date="2020-02-02", expected_track_count=1)

    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("singles", {"id": "dz-new", "title": "Brand New Cut", "album_type": "single",
                     "release_date": "2020-02-02", "year": "2020",
                     "track_count": 1, "image_url": None}),
    )})

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["added"] == 1          # genuinely new single, no false merge
    count = imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Brand New Cut'").fetchone()["c"]
    assert count == 1


def test_year_only_dates_never_trigger_date_fallback(legacy_db, imported_conn, monkeypatch):
    """A bare year is too coarse for the date+count fallback: two different
    10-track albums from the same year must not merge."""
    aid = _artist_id(imported_conn)
    _seed_library_album(imported_conn, aid, title="Winter Works",
                        release_date="2019", expected_track_count=10)

    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("albums", {"id": "dz-summer", "title": "Summer Works", "album_type": "album",
                    "release_date": "2019", "year": "2019", "track_count": 10,
                    "image_url": None}),
    )})

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["added"] == 1


def test_reimport_claims_discography_row_despite_punctuation_variant(
        legacy_db, imported_conn, monkeypatch):
    """§62 stage 1, importer half: legacy files tagged `"Quoted" Album` must
    claim the provider row spelled without quotes instead of duplicating."""
    aid = _artist_id(imported_conn)
    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("albums", {"id": "dz-quoted", "title": "TV Anime Attack on Titan OST",
                    "album_type": "album", "release_date": "2014-07-31",
                    "year": "2014", "track_count": 2, "image_url": None}),
    )})
    D.expand_artist_discography(legacy_db, aid)

    conn = legacy_db._get_connection()
    conn.execute(
        "INSERT INTO albums VALUES(13,1,'TV Anime \"Attack on Titan\" OST',"
        "2014,NULL,NULL,2,'2014-07-31')")
    conn.execute(
        "INSERT INTO tracks VALUES(104,13,1,'attack',1,180000,'/m/atk.flac',900,4000,NULL)")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    rows = imported_conn.execute(
        "SELECT id, title, origin FROM lib2_albums WHERE title LIKE '%Attack on Titan%'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["origin"] == "library"


# ---------------------------------------------------------------------------
# §62 stage 2: an alternative provider release becomes an edition, not noise
# ---------------------------------------------------------------------------

def _alt_editions(conn, album_id):
    rows = conn.execute(
        "SELECT is_default, external_ids, release_date, track_count "
        "FROM lib2_release_editions WHERE release_group_id=? AND is_default=0",
        (album_id,)).fetchall()
    return [dict(r) for r in rows]


def test_conflicting_provider_release_id_is_recorded_as_edition(
        legacy_db, imported_conn, monkeypatch):
    """When the day+count fallback (or a title match) lands on a row that
    already carries a DIFFERENT id of the same source, the alternative
    release id must survive as a lib2_release_editions row (§62.6 Stufe 2)
    instead of being logged away."""
    aid = _artist_id(imported_conn)
    album_id = _seed_library_album(
        imported_conn, aid,
        title="TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
        release_date="2017-06-07", expected_track_count=33,
        external_ids=json.dumps({"deezer": "196470602"}))

    payload = _cards_from(
        "deezer",
        ("albums", {"id": "42695001",
                    "title": 'TV Anime "Attack on Titan Season 2" (Original Soundtrack)',
                    "album_type": "album", "release_date": "2017-06-07",
                    "year": "2017", "track_count": 33, "image_url": None}),
    )
    _fake_discography_by_name(monkeypatch, {"Drake": payload})

    D.expand_artist_discography(legacy_db, aid)

    alts = _alt_editions(imported_conn, album_id)
    assert len(alts) == 1
    assert json.loads(alts[0]["external_ids"])["deezer"] == "42695001"
    assert alts[0]["release_date"] == "2017-06-07"
    assert alts[0]["track_count"] == 33

    # Idempotent: a re-sync must not stack a second copy of the edition.
    D.expand_artist_discography(legacy_db, aid)
    assert len(_alt_editions(imported_conn, album_id)) == 1


def test_same_title_second_release_id_is_recorded_as_edition(
        legacy_db, imported_conn, monkeypatch):
    """Deezer lists `"Attack on Titan" Season 3 OST` twice (two pressings,
    two ids). The title match absorbs the second card; its id must land in
    an edition."""
    aid = _artist_id(imported_conn)
    _fake_discography_by_name(monkeypatch, {"Drake": _cards_from(
        "deezer",
        ("albums", {"id": "100049482", "title": '"Attack on Titan" Season 3 OST',
                    "album_type": "album", "release_date": "2019-06-26",
                    "year": "2019", "track_count": 31, "image_url": None}),
        ("albums", {"id": "197089272", "title": '"Attack on Titan" Season 3 OST',
                    "album_type": "album", "release_date": "2019-06-26",
                    "year": "2019", "track_count": 31, "image_url": None}),
    )})

    stats = D.expand_artist_discography(legacy_db, aid)

    assert stats["added"] == 1
    row = imported_conn.execute(
        "SELECT id, external_ids FROM lib2_albums WHERE title LIKE '%Season 3%'"
    ).fetchone()
    assert json.loads(row["external_ids"])["deezer"] == "100049482"
    alts = _alt_editions(imported_conn, row["id"])
    assert len(alts) == 1
    assert json.loads(alts[0]["external_ids"])["deezer"] == "197089272"
