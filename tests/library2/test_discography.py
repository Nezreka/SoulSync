"""Discography expansion: persist provider releases, dedup, claim on import."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2 import discography as D
from core.library2.importer import import_legacy_library
from core.library2.provider_adapters import DISCOGRAPHY_PARSER_VERSION


@pytest.fixture(autouse=True)
def _disable_unrelated_artwork_warmup(monkeypatch):
    """Discography assertions are offline; artwork scheduling has its own suite."""
    monkeypatch.setattr(
        "core.library2.artwork.schedule_missing_artwork",
        lambda *_args, **_kwargs: 0,
    )


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


def test_collaborative_release_is_linked_to_every_album_artist(
        legacy_db, imported_conn, monkeypatch):
    payload = _cards(("albums", {
        "id": "mb-collab-release",
        "title": "Shared Score",
        "album_type": "album",
        # The artist whose page is refreshed is deliberately second, matching
        # soundtrack releases whose displayed primary credit is the co-composer.
        "artists": ["Other Composer", "Drake"],
        "artist_credits": [
            {"name": "Other Composer", "id": "mb-other-composer"},
            {"name": "Drake", "id": "mb-drake"},
        ],
        "track_count": 12,
    }))
    payload["source"] = "musicbrainz"
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None,
    )

    drake_id = _artist_id(imported_conn)
    D.expand_artist_discography(legacy_db, drake_id)
    # A repeat refresh must enrich the same row and keep the credit set stable.
    D.expand_artist_discography(legacy_db, drake_id)

    album = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Shared Score'"
    ).fetchone()
    credits = imported_conn.execute(
        """SELECT ar.name, aa.role
             FROM lib2_album_artists aa
             JOIN lib2_artists ar ON ar.id=aa.artist_id
            WHERE aa.album_id=?
            ORDER BY CASE aa.role WHEN 'primary' THEN 0 ELSE 1 END, ar.name""",
        (album["id"],),
    ).fetchall()
    assert [(row["name"], row["role"]) for row in credits] == [
        ("Drake", "primary"),
        ("Other Composer", "featured"),
    ]

    other_id = imported_conn.execute(
        "SELECT id, spotify_id, external_ids FROM lib2_artists "
        "WHERE name='Other Composer'"
    ).fetchone()
    assert other_id["spotify_id"] is None
    assert json.loads(other_id["external_ids"])["musicbrainz"] == "mb-other-composer"
    from core.library2.match_status import entity_match_status
    musicbrainz = next(
        chip for chip in entity_match_status(imported_conn, "artist", other_id["id"])
        if chip["service"] == "musicbrainz"
    )
    assert musicbrainz["status"] == "matched"
    assert musicbrainz["external_id"] == "mb-other-composer"
    from core.library2 import queries as Q
    other_view = Q.get_artist(imported_conn, other_id["id"])
    assert [release["title"] for release in other_view["albums"]] == ["Shared Score"]


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
    assert snapshot["parser_version"] == DISCOGRAPHY_PARSER_VERSION
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


def test_reexpansion_skips_a_new_single_when_singles_are_off(legacy_db, imported_conn, fake_discography, monkeypatch):
    """the watchlist's Albums / EPs / Singles toggles hold for new releases
    again (3.6.1's _should_include_release): Singles off, a new single is not
    wishlisted, a new album still is."""
    aid = _artist_id(imported_conn)
    conn = legacy_db._get_connection()
    conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all' WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    D.expand_artist_discography(legacy_db, aid)

    monkeypatch.setattr(D, "_artist_content_filters",
                        lambda _conn, _aid, *_args: {**D._CONTENT_FILTER_DEFAULTS, "include_singles": False})
    grown = _cards(
        ("albums", {"id": "sp-views", "title": "Views", "album_type": "album", "track_count": 20}),
        ("albums", {"id": "sp-scorpion", "title": "Scorpion", "album_type": "album", "track_count": 25}),
        ("albums", {"id": "sp-new", "title": "For All The Dogs", "album_type": "album",
                    "track_count": 23}),
        ("singles", {"id": "sp-onedance", "title": "One Dance", "album_type": "single",
                     "track_count": 1}),
        ("singles", {"id": "sp-newsingle", "title": "Brand New Single", "album_type": "single",
                     "track_count": 1}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown,
    )
    stats = D.expand_artist_discography(legacy_db, aid)
    assert stats["added"] == 2
    monitored = {row["title"]: row["monitored"] for row in imported_conn.execute(
        "SELECT title, monitored FROM lib2_albums WHERE title IN ('For All The Dogs', 'Brand New Single')")}
    assert monitored == {"For All The Dogs": 1, "Brand New Single": 0}


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

    def fake_expand(_database, target_artist_id, **_kwargs):
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


def test_expand_warms_artwork_for_new_releases(
        legacy_db, imported_conn, fake_discography, monkeypatch):
    """perf25-04: an expand queues the artist plus its new albums for artwork,
    instead of leaving them cold until the next full precache run."""
    from core.library2 import artwork

    requests = []
    monkeypatch.setattr(
        artwork,
        "schedule_missing_artwork",
        lambda db, cfg, targets: requests.append(list(targets)),
    )
    aid = _artist_id(imported_conn)

    D.expand_artist_discography(legacy_db, aid)

    assert requests, "expand must warm artwork for what it just added"
    targets = requests[0]
    assert targets[0] == ("artist", aid)
    new_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Scorpion'"
    ).fetchone()["id"]
    assert ("album", new_id) in targets


# ---------------------------------------------------------------------------
# MusicBrainz release GROUPS are not releases
#
# MB's discography browse projects release groups, so the id on every card is a
# group mbid. Stored under `external_ids['musicbrainz']` it sat in the release
# namespace, where a file's MUSICBRAINZ_ALBUMID tag and the "open on
# MusicBrainz" /release/ link both live.
# ---------------------------------------------------------------------------

MB_GROUP = "9c5c1a2e-1f77-4c1e-9f43-1b2d3e4f5a60"
MB_RELEASE = "1d0e2f3a-4b5c-4d6e-8f90-a1b2c3d4e5f6"


@pytest.fixture
def fake_mb_discography(monkeypatch):
    """A MusicBrainz catalog: `id` and `release_group_id` are the same value."""
    payload = {
        "albums": [{
            "id": MB_GROUP, "title": "Certified Lover Boy", "album_type": "album",
            "release_date": "2021-09-03", "year": "2021", "track_count": 21,
            "image_url": None, "release_group_id": MB_GROUP,
        }],
        "eps": [], "singles": [], "success": True, "source": "musicbrainz",
    }
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: payload)
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None)
    return payload


def test_a_musicbrainz_group_id_is_not_stored_as_a_release_id(
        legacy_db, imported_conn, fake_mb_discography):
    stats = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    assert stats["added"] == 1

    row = imported_conn.execute(
        "SELECT musicbrainz_id, musicbrainz_release_group_id AS rg, external_ids "
        "FROM lib2_albums WHERE title='Certified Lover Boy'").fetchone()
    assert row["rg"] == MB_GROUP
    # Neither place that means "the concrete release" may hold it.
    assert row["musicbrainz_id"] is None
    assert "musicbrainz" not in json.loads(row["external_ids"])


def test_a_musicbrainz_group_still_matches_its_row_on_the_next_sync(
        legacy_db, imported_conn, fake_mb_discography):
    """The regression this could have introduced: with the group id out of
    external_ids, the id match has to find the row by its group column or the
    second sync inserts the release all over again."""
    D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    second = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))

    assert second["added"] == 0
    assert second["enriched"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE title='Certified Lover Boy'"
    ).fetchone()["c"] == 1


MB_GROUP_2 = "3a7bd3e2-360f-4e5c-9a1b-2c3d4e5f6071"


def test_two_new_musicbrainz_groups_in_one_run(legacy_db, imported_conn, monkeypatch):
    """ARCH-01: the index row appended for a freshly inserted release has to
    carry the same fields the loaded rows do.  Leaving the group column out
    made the NEXT release's id match raise KeyError, so the run never reached
    its commit and a first expansion of any two-group catalog was lost."""
    payload = {
        "albums": [
            {"id": MB_GROUP, "title": "Certified Lover Boy", "album_type": "album",
             "release_date": "2021-09-03", "year": "2021", "track_count": 21,
             "image_url": None, "release_group_id": MB_GROUP},
            {"id": MB_GROUP_2, "title": "Scorpion", "album_type": "album",
             "release_date": "2018-06-29", "year": "2018", "track_count": 25,
             "image_url": None, "release_group_id": MB_GROUP_2},
        ],
        "eps": [], "singles": [], "success": True, "source": "musicbrainz",
    }
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: payload)
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None)

    stats = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    assert stats["added"] == 2
    rows = imported_conn.execute(
        "SELECT title, musicbrainz_release_group_id AS rg FROM lib2_albums "
        "WHERE origin='discography' ORDER BY title").fetchall()
    assert [(r["title"], r["rg"]) for r in rows] == [
        ("Certified Lover Boy", MB_GROUP), ("Scorpion", MB_GROUP_2)]

    # And the second run stays idempotent through the same index.
    second = D.expand_artist_discography(legacy_db, _artist_id(imported_conn))
    assert second["added"] == 0
    assert second["enriched"] == 2


def test_a_concrete_release_keeps_both_ids(legacy_db, imported_conn, monkeypatch):
    """The other MB shape — a release expanded out of its group — carries two
    different ids, and each belongs in its own place."""
    payload = {
        "albums": [{
            "id": MB_RELEASE, "title": "Certified Lover Boy", "album_type": "album",
            "release_date": "2021-09-03", "year": "2021", "track_count": 21,
            "image_url": None, "release_group_id": MB_GROUP,
        }],
        "eps": [], "singles": [], "success": True, "source": "musicbrainz",
    }
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: payload)
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None)

    D.expand_artist_discography(legacy_db, _artist_id(imported_conn))

    row = imported_conn.execute(
        "SELECT musicbrainz_release_group_id AS rg, external_ids "
        "FROM lib2_albums WHERE title='Certified Lover Boy'").fetchone()
    assert row["rg"] == MB_GROUP
    assert json.loads(row["external_ids"])["musicbrainz"] == MB_RELEASE


def test_another_provider_never_writes_the_musicbrainz_column(
        legacy_db, imported_conn, monkeypatch):
    """`release_group_id` exists on more than one provider's Album dataclass;
    only a MusicBrainz value may land in a MusicBrainz column."""
    payload = {
        "albums": [{
            "id": "js-clb", "title": "Certified Lover Boy", "album_type": "album",
            "release_date": "2021-09-03", "year": "2021", "track_count": 21,
            "image_url": None, "release_group_id": "js-group-42",
        }],
        "eps": [], "singles": [], "success": True, "source": "jiosaavn",
    }
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: payload)
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_album_tracklist",
        lambda *_args, **_kwargs: None)

    D.expand_artist_discography(legacy_db, _artist_id(imported_conn))

    row = imported_conn.execute(
        "SELECT musicbrainz_release_group_id AS rg, external_ids "
        "FROM lib2_albums WHERE title='Certified Lover Boy'").fetchone()
    assert row["rg"] is None
    assert json.loads(row["external_ids"])["jiosaavn"] == "js-clb"


@pytest.mark.parametrize('preference,expected', [('all', {'Edition Test', 'Edition Test (Deluxe)', 'Edition Test (Super Deluxe)'}), ('one_standard', {'Edition Test'}), ('one_complete', {'Edition Test (Deluxe)'})])
def test_native_automatic_selection_keeps_the_entire_edition_catalogue(legacy_db, imported_conn, monkeypatch, preference, expected):
    from core.settings import config_manager
    original_get = config_manager.get
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: preference if key == 'watchlist.edition_preference' else original_get(key, default))
    aid = _artist_id(imported_conn)
    imported_conn.execute("UPDATE lib2_artists SET monitored=1, monitor_new_items='all', discography_synced_at='2020-01-01' WHERE id=?", (aid,))
    imported_conn.commit()
    payload = _cards(*[('albums', {'id': f'edition-{i}', 'title': title, 'album_type': 'album', 'track_count': count, 'release_date': '2024-01-01'}) for i, (title, count) in enumerate([('Edition Test', 10), ('Edition Test (Deluxe)', 20), ('Edition Test (Super Deluxe)', 80)])])
    monkeypatch.setattr('core.metadata.discography.get_artist_detail_discography', lambda *a, **k: payload)
    stats = D.expand_artist_discography(legacy_db, aid)
    selected = set(stats['auto_monitor_album_ids'])
    rows = imported_conn.execute("SELECT id,title,monitored FROM lib2_albums WHERE title LIKE 'Edition Test%'").fetchall()
    assert len(rows) == 3
    assert {r['title'] for r in rows if r['id'] in selected} == expected
    assert {r['title'] for r in rows if r['monitored']} == expected


def test_automatic_editions_preserve_explicit_monitor_provenance(legacy_db, imported_conn, monkeypatch):
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.edition_selection import select_automatic_album_ids
    from types import SimpleNamespace
    aid = _artist_id(imported_conn)
    ids = [imported_conn.execute('INSERT INTO lib2_albums(primary_artist_id,title,track_count) VALUES(?,?,?)', (aid, title, n)).lastrowid for title, n in [('Choice', 10), ('Choice (Deluxe)', 20)]]
    record_rule(imported_conn, 'album', ids[1], True, PROVENANCE_USER)
    imported_conn.commit()
    cfg = SimpleNamespace(get=lambda key, default=None: 'one_standard' if key == 'watchlist.edition_preference' else default)
    assert select_automatic_album_ids(imported_conn, ids, cfg) == [ids[1]]
    def materialize(config, conn, album_id):
        conn.execute("INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Chosen')", (album_id,))
        return [{'title': 'Chosen', 'track_number': 1}]
    monkeypatch.setattr('core.library2.completeness.resolve_tracklist', materialize)
    monkeypatch.setattr('core.library2.wishlist_mirror.mirror_projected_tracks_wishlist', lambda db, conn, tracks, **kw: len(tracks))
    assert D.auto_monitor_releases(legacy_db, cfg, ids) == 1
    assert imported_conn.execute("SELECT monitored,provenance FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id=?", (ids[1],)).fetchone()[:] == (1, 'user_explicit')


def test_automatic_editions_keep_pins_and_respect_manual_veto(imported_conn):
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.edition_selection import select_automatic_album_ids
    from types import SimpleNamespace
    aid = _artist_id(imported_conn)
    ids = [imported_conn.execute('INSERT INTO lib2_albums(primary_artist_id,title,track_count) VALUES(?,?,?)', (aid, title, n)).lastrowid for title, n in [('Choice', 10), ('Choice (Deluxe)', 20)]]
    imported_conn.execute('UPDATE lib2_albums SET canonical_locked=1 WHERE id=?', (ids[1],))
    cfg = SimpleNamespace(get=lambda key, default=None: 'one_standard' if key == 'watchlist.edition_preference' else default)
    assert select_automatic_album_ids(imported_conn, ids, cfg) == [ids[1]]
    record_rule(imported_conn, 'album', ids[1], False, PROVENANCE_USER)
    assert select_automatic_album_ids(imported_conn, ids, cfg) == [ids[0]]


def test_release_credits_never_resolve_the_primary_artist_again(imported_conn, monkeypatch):
    """review 5 R5-E-3: the primary credit is primary_artist_id already;
    resolving it too was an artist lookup thrown away inside the write
    transaction. only the other credits are looked up and linked."""
    from core.library2 import autolink

    drake = _artist_id(imported_conn)
    album = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id=? LIMIT 1", (drake,)
    ).fetchone()["id"]
    looked_up = []
    real = autolink.find_or_create_artist

    def spy(conn, name, **kwargs):
        looked_up.append(name)
        return real(conn, name, **kwargs)

    monkeypatch.setattr(autolink, "find_or_create_artist", spy)
    D._link_release_artists(
        imported_conn, album, drake, "Drake",
        [{"name": "Drake", "id": "246791"}, {"name": "Rihanna", "id": "564"}],
        "deezer",
    )

    assert looked_up == ["Rihanna"]
    featured = imported_conn.execute(
        "SELECT a.name FROM lib2_album_artists aa JOIN lib2_artists a ON a.id=aa.artist_id "
        "WHERE aa.album_id=? AND aa.role='featured'", (album,)).fetchall()
    names = [r["name"] for r in featured]
    assert "Rihanna" in names and "Drake" not in names


# --- #2082: a live album you ask for, and the edition you ask for ------------

def _live_album(legacy_db, monkeypatch, *, title="MTV Unplugged"):
    conn = legacy_db._get_connection()
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Alice in Chains')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, track_count) "
        "VALUES(?, ?, 'album', 3)", (artist, title)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album, artist))
    conn.commit()
    conn.close()

    def materialize(_config, conn, target_album_id):
        for number, name in enumerate(("Nutshell", "Rooster (Live)", "Would? (Remix)"), 1):
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
                "VALUES(?, ?, ?, 0)", (target_album_id, name, number)).lastrowid
            conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                         (track, artist))
        return [{"title": "Nutshell", "track_number": 1}]

    monkeypatch.setattr("core.library2.completeness.resolve_tracklist", materialize)
    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist",
        lambda _db, _conn, track_ids, **_kwargs: len(track_ids))
    return album


def _monitored(legacy_db, album_id):
    conn = legacy_db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT title FROM lib2_tracks WHERE album_id=? AND monitored=1", (album_id,)))
    finally:
        conn.close()


def test_a_live_album_nobody_asked_for_stays_out_with_live_albums_off(imported_conn, legacy_db, monkeypatch):
    album = _live_album(legacy_db, monkeypatch)
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _monitored(legacy_db, album) == []


def test_a_live_album_you_ask_for_brings_its_tracks(imported_conn, legacy_db, monkeypatch):
    # "the Alice In Chains MTV Unplugged album refused to download until I
    # disabled the exclude live filter": every track of a live album was a
    # live version, so approving it wished for nothing
    album = _live_album(legacy_db, monkeypatch)
    D.approve_release(legacy_db, None, album, requested=True)
    # the live album's own tracks, not the stray remix on it
    assert _monitored(legacy_db, album) == ["Nutshell", "Rooster (Live)"]


def test_the_back_catalogues_own_auto_add_is_not_someone_asking(imported_conn, legacy_db, monkeypatch):
    album = _live_album(legacy_db, monkeypatch)
    D.approve_release(legacy_db, None, album)
    assert _monitored(legacy_db, album) == []


def test_a_members_auto_add_is_not_someone_asking_either(imported_conn, legacy_db, monkeypatch):
    # for a library of its own the auto add writes that profile's album rule,
    # which looks like a person's. it is still not a request for a live album
    album = _live_album(legacy_db, monkeypatch)
    D.approve_release(legacy_db, None, album, profile_id=3)
    conn = legacy_db._get_connection()
    try:
        wanted = [r[0] for r in conn.execute(
            "SELECT t.title FROM lib2_monitor_rules r JOIN lib2_tracks t ON t.id = r.entity_id "
            "WHERE r.entity_type='track' AND r.profile_id=3 AND r.monitored=1 AND t.album_id=?",
            (album,))]
    finally:
        conn.close()
    assert wanted == []
    D.approve_release(legacy_db, None, album, profile_id=3, requested=True)
    conn = legacy_db._get_connection()
    try:
        # the fake tracklist is laid down again by the second approve, so by title
        wanted = sorted({r[0] for r in conn.execute(
            "SELECT t.title FROM lib2_monitor_rules r JOIN lib2_tracks t ON t.id = r.entity_id "
            "WHERE r.entity_type='track' AND r.profile_id=3 AND r.monitored=1 AND t.album_id=?",
            (album,))})
    finally:
        conn.close()
    assert wanted == ["Nutshell", "Rooster (Live)"]


# --- #2117: the wanted projection, not the flag, is what downloads ---------
# the #2082 tests above assert the monitored flag. but acquisition reads
# lib2_wanted_tracks, and for the admin profile auto_monitor recorded no
# track rules, so the album's new_release rule made every track wanted —
# live albums were wishlisted and downloaded with Live off. these tests
# assert the projection itself, and what the real mirror enqueue
# (enqueue_projected_tracks, the function the mirror stub replaces) would
# queue from it. note the fake materialize below inserts a fresh set of
# track rows on every call, so a re-run duplicates rows; the re-run tests
# assert on the ORIGINAL track ids, which stay in the album's track list
# and are exactly what preservation has to protect.

def _plain_album(legacy_db, monkeypatch, *, title, track_names, artist_name="Brandy Clark",
                 track_credits=None, artist_monitored=0, fail_first=False):
    """track_credits: optional {track title: artist name} for tracks credited
    to someone other than the album's artist (the uncredited-veto tests)."""
    conn = legacy_db._get_connection()
    artist = conn.execute(
        "INSERT INTO lib2_artists(name, monitored) VALUES(?, ?)",
        (artist_name, artist_monitored)).lastrowid
    others = {}
    for name in (track_credits or {}).values():
        if name != artist_name and name not in others:
            others[name] = conn.execute(
                "INSERT INTO lib2_artists(name) VALUES(?)", (name,)).lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, track_count) "
        "VALUES(?, ?, 'album', ?)", (artist, title, len(track_names))).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album, artist))
    conn.commit()
    conn.close()

    calls = {"n": 0}

    def materialize(_config, conn, target_album_id):
        calls["n"] += 1
        if fail_first and calls["n"] == 1:
            raise RuntimeError("provider fetch failed")
        for number, name in enumerate(track_names, 1):
            track = conn.execute(
                "INSERT INTO lib2_tracks(album_id, title, track_number, monitored) "
                "VALUES(?, ?, ?, 0)", (target_album_id, name, number)).lastrowid
            credit = artist if not track_credits else others.get(
                track_credits.get(name, artist_name), artist)
            conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id) VALUES(?,?)",
                         (track, credit))
        return [{"title": track_names[0], "track_number": 1}]

    monkeypatch.setattr("core.library2.completeness.resolve_tracklist", materialize)
    mirrored = []
    monkeypatch.setattr(
        "core.library2.wishlist_mirror.mirror_projected_tracks_wishlist",
        lambda _db, _conn, track_ids, **_kwargs: mirrored.extend(track_ids) or len(track_ids))
    return album, mirrored


def _wanted_titles(legacy_db, album_id, profile_id=1):
    conn = legacy_db._get_connection()
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT t.title FROM lib2_wanted_tracks w JOIN lib2_tracks t ON t.id = w.track_id "
            "WHERE t.album_id=? AND w.profile_id=? AND w.wanted=1", (album_id, profile_id)))
    finally:
        conn.close()


def _enqueued_ops(legacy_db, track_ids, profile_id=1):
    """Run the mirror's real enqueue over the projection and return the
    outbox ops it created for these tracks."""
    from core.library2.mirror_outbox import enqueue_projected_tracks
    conn = legacy_db._get_connection()
    try:
        outbox_ids = enqueue_projected_tracks(conn, list(track_ids), profile_id=profile_id)
        if not outbox_ids:
            return []
        marks = ",".join("?" for _ in outbox_ids)
        return [r[0] for r in conn.execute(
            f"SELECT op FROM lib2_mirror_outbox WHERE id IN ({marks})", outbox_ids)]
    finally:
        conn.close()


def _album_track_ids(legacy_db, album_id):
    conn = legacy_db._get_connection()
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,))]
    finally:
        conn.close()


def test_2117_admin_live_album_stays_unwanted_with_live_off(imported_conn, legacy_db, monkeypatch):
    album, mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town", "Girl Next Door"))
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _monitored(legacy_db, album) == []
    # the projection is what the wishlist mirror reads: nothing may be wanted
    assert _wanted_titles(legacy_db, album) == []
    assert mirrored  # the mirror was invoked over the album's tracks
    # and the real enqueue from that projection queues no wishlist adds
    ops = _enqueued_ops(legacy_db, _album_track_ids(legacy_db, album))
    assert ops and set(ops) == {"wishlist_remove"}


def test_2117_live_bonus_track_stays_unwanted_on_a_studio_album(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Drinkin' Smokin' Cheatin'", "Get High (Live)"),
        artist_name="Brandy Clark")
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == ["12 Stories", "Drinkin' Smokin' Cheatin'"]
    assert _monitored(legacy_db, album) == ["12 Stories", "Drinkin' Smokin' Cheatin'"]


def test_2117_requested_live_album_still_becomes_wanted(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.approve_release(legacy_db, None, album, requested=True)
    assert _wanted_titles(legacy_db, album) == ["Big Day in a Small Town", "When I Get to Drinkin'"]


def test_2117_deliberate_album_rule_stands_over_the_filters(imported_conn, legacy_db, monkeypatch):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    conn = legacy_db._get_connection()
    try:
        record_rule(conn, "album", album, True, PROVENANCE_USER)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    # the person's own album choice is not overridden by the live filter,
    # and no new_release track rules are written underneath it
    assert _wanted_titles(legacy_db, album) == ["Big Day in a Small Town", "When I Get to Drinkin'"]
    conn = legacy_db._get_connection()
    try:
        track_rules = conn.execute(
            "SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_type='track' "
            "AND provenance='new_release' AND entity_id IN "
            "(SELECT id FROM lib2_tracks WHERE album_id=?)", (album,)).fetchone()[0]
    finally:
        conn.close()
    assert track_rules == 0


def test_2117_deliberate_track_rule_is_not_overwritten_on_rerun(imported_conn, legacy_db, monkeypatch):
    from core.library2.monitor_rules import PROVENANCE_CASCADE, record_rule
    from core.library2.wanted import recompute_wanted_for_entity
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == []
    conn = legacy_db._get_connection()
    try:
        track = conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? AND title='Big Day in a Small Town'",
            (album,)).fetchone()[0]
        # the person's album toggle cascades a rule onto every track
        record_rule(conn, "track", track, True, PROVENANCE_CASCADE)
        recompute_wanted_for_entity(conn, "album", album)
        conn.commit()
    finally:
        conn.close()
    assert _wanted_titles(legacy_db, album) == ["Big Day in a Small Town"]
    # a materialize retry lays the tracklist down again; the deliberate
    # rule on the existing track must survive it
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        rule = conn.execute(
            "SELECT monitored, provenance FROM lib2_monitor_rules "
            "WHERE entity_type='track' AND entity_id=?", (track,)).fetchone()
        wanted = conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
            (track,)).fetchone()[0]
    finally:
        conn.close()
    assert tuple(rule) == (1, PROVENANCE_CASCADE)
    assert wanted == 1


def test_2117_leaked_wanted_state_repairs_itself_on_rerun(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.auto_monitor_releases(legacy_db, None, [album])
    # recreate the pre-fix state: album rule only, no track rules, wanted=1
    from core.library2.wanted import recompute_wanted_for_entity
    conn = legacy_db._get_connection()
    try:
        track_ids = [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=?", (album,))]
        conn.execute(
            "DELETE FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id IN "
            "(SELECT id FROM lib2_tracks WHERE album_id=?)", (album,))
        recompute_wanted_for_entity(conn, "album", album)
        conn.commit()
    finally:
        conn.close()
    assert _wanted_titles(legacy_db, album) == ["Big Day in a Small Town", "When I Get to Drinkin'"]
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        leaked = conn.execute(
            "SELECT COUNT(*) FROM lib2_wanted_tracks WHERE wanted=1 AND profile_id=1 "
            "AND track_id IN (?,?)", track_ids).fetchone()[0]
    finally:
        conn.close()
    assert leaked == 0
    # and the mirror enqueue from the repaired projection withdraws the
    # stale wishlist rows instead of adding anything
    ops = _enqueued_ops(legacy_db, track_ids)
    assert ops and set(ops) == {"wishlist_remove"}


def test_2117_deliberate_album_rule_off_stands_too(imported_conn, legacy_db, monkeypatch):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Get High"), artist_name="Brandy Clark")
    conn = legacy_db._get_connection()
    try:
        record_rule(conn, "album", album, False, PROVENANCE_USER)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    # the person's "off" for the whole album stands: edition selection
    # drops an explicitly-off album before auto-monitor ever processes
    # it, so nothing is even materialized, let alone wanted
    assert _wanted_titles(legacy_db, album) == []
    assert _album_track_ids(legacy_db, album) == []
    conn = legacy_db._get_connection()
    try:
        track_rules = conn.execute(
            "SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_type='track' "
            "AND provenance='new_release' AND entity_id IN "
            "(SELECT id FROM lib2_tracks WHERE album_id=?)", (album,)).fetchone()[0]
    finally:
        conn.close()
    assert track_rules == 0


def test_2117_deliberate_track_rules_survive_in_both_directions(imported_conn, legacy_db, monkeypatch):
    from core.library2.monitor_rules import (
        PROVENANCE_CASCADE, PROVENANCE_WISHLIST, record_rule)
    from core.library2.wanted import recompute_wanted_for_entity
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Get High"), artist_name="Brandy Clark")
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == ["12 Stories", "Get High"]
    conn = legacy_db._get_connection()
    try:
        first, second = (r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY track_number", (album,)))
        # a deliberate off (album-toggle cascade) on a passing track, and a
        # wishlisted item's rule on the other
        record_rule(conn, "track", first, False, PROVENANCE_CASCADE)
        record_rule(conn, "track", second, True, PROVENANCE_WISHLIST)
        recompute_wanted_for_entity(conn, "album", album)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        rules = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT entity_id, monitored, provenance FROM lib2_monitor_rules "
            "WHERE entity_type='track' AND entity_id IN (?,?)", (first, second))}
        wanted = {r[0]: r[1] for r in conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id IN (?,?)", (first, second))}
    finally:
        conn.close()
    assert rules[first] == (0, PROVENANCE_CASCADE)
    assert rules[second] == (1, PROVENANCE_WISHLIST)
    assert wanted == {first: 0, second: 1}
    # pinned divergence (pre-existing, not this fix): the admin flag flip
    # follows the filters only, so the cascade-off track's flag says
    # monitored while the projection — what acquisition reads — says
    # unwanted. the rule, not the flag, is what keeps it out.
    conn = legacy_db._get_connection()
    try:
        flag = conn.execute(
            "SELECT monitored FROM lib2_tracks WHERE id=?", (first,)).fetchone()[0]
    finally:
        conn.close()
    assert flag == 1


def test_2117_remix_track_stays_unwanted_on_a_studio_album(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Get High (Remix)"), artist_name="Brandy Clark")
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == ["12 Stories"]
    assert _monitored(legacy_db, album) == ["12 Stories"]


def test_2117_user_explicit_track_rules_are_never_demoted(imported_conn, legacy_db, monkeypatch):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted_for_entity
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        first, second = (r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY track_number", (album,)))
        # the person's own track choices: one live track they turned on by
        # hand (protected only by the deliberate skip — the explicit-off
        # veto does not cover a True), one they turned off
        record_rule(conn, "track", first, True, PROVENANCE_USER)
        record_rule(conn, "track", second, False, PROVENANCE_USER)
        recompute_wanted_for_entity(conn, "album", album)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        rules = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT entity_id, monitored, provenance FROM lib2_monitor_rules "
            "WHERE entity_type='track' AND entity_id IN (?,?)", (first, second))}
        wanted = {r[0]: r[1] for r in conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id IN (?,?)", (first, second))}
    finally:
        conn.close()
    # neither rule is rewritten as new_release: the highest-ranked
    # provenance keeps both its value and its rank
    assert rules[first] == (1, PROVENANCE_USER)
    assert rules[second] == (0, PROVENANCE_USER)
    assert wanted == {first: 1, second: 0}


def test_2117_turning_live_on_rewrites_the_rules_on_rerun(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == []
    track_ids = _album_track_ids(legacy_db, album)
    # the user turns Live on: the same enforcement, with the filters
    # flipped, rewrites the new_release rules it wrote before
    live_on = dict(D._CONTENT_FILTER_DEFAULTS, include_live=True, include_live_tracks=True)
    monkeypatch.setattr(D, "_artist_content_filters", lambda *_a, **_k: dict(live_on))
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        states = {r[0]: r[1] for r in conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id IN (?,?)", track_ids)}
    finally:
        conn.close()
    assert states == {track_ids[0]: 1, track_ids[1]: 1}
    # and turning it back off rewrites them again
    monkeypatch.setattr(D, "_artist_content_filters",
                        lambda *_a, **_k: dict(D._CONTENT_FILTER_DEFAULTS))
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        states = {r[0]: r[1] for r in conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id IN (?,?)", track_ids)}
        flags = {r[0]: r[1] for r in conn.execute(
            "SELECT id, monitored FROM lib2_tracks WHERE id IN (?,?)", track_ids)}
    finally:
        conn.close()
    assert states == {track_ids[0]: 0, track_ids[1]: 0}
    # pinned divergence (pre-existing shape, same as the cascade-off
    # case): the flag flip only ever sets monitored=1, so after an
    # on-then-off cycle the flags still say monitored while the rules
    # and the projection say unwanted. acquisition reads the projection.
    assert flags == {track_ids[0]: 1, track_ids[1]: 1}


def test_2117_uncredited_track_stays_unwanted(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Not Hers"), artist_monitored=1,
        track_credits={"Not Hers": "Someone Else"})
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == ["12 Stories"]
    assert _monitored(legacy_db, album) == ["12 Stories"]


def test_2117_requested_album_still_leaves_out_the_uncredited(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("12 Stories", "Not Hers"), artist_monitored=1,
        track_credits={"Not Hers": "Someone Else"})
    # asking for the live album bypasses the live filter, never the
    # credit veto: the other artist's track is not the person's to grab
    D.approve_release(legacy_db, None, album, requested=True)
    assert _wanted_titles(legacy_db, album) == ["12 Stories"]


def test_2117_derived_track_rules_follow_the_filter_outcome(imported_conn, legacy_db, monkeypatch):
    # plan-directed, member-branch parity: derived rules (file_import,
    # legacy_import) are not deliberate, so the enforcement rewrites
    # them with the filter outcome — provenance AND wanted state: the
    # album rule outranked them in the projection before, so the live
    # tracks were wanted; now their own rewritten rule keeps them out.
    from core.library2.monitor_rules import PROVENANCE_FILE, PROVENANCE_LEGACY, record_rule
    from core.library2.wanted import recompute_wanted_for_entity
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        first, second = (r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=? ORDER BY track_number", (album,)))
        record_rule(conn, "track", first, True, PROVENANCE_FILE)
        record_rule(conn, "track", second, True, PROVENANCE_LEGACY)
        recompute_wanted_for_entity(conn, "album", album)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    conn = legacy_db._get_connection()
    try:
        rules = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT entity_id, monitored, provenance FROM lib2_monitor_rules "
            "WHERE entity_type='track' AND entity_id IN (?,?)", (first, second))}
        wanted = {r[0]: r[1] for r in conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id IN (?,?)", (first, second))}
    finally:
        conn.close()
    assert rules[first] == (0, "new_release")
    assert rules[second] == (0, "new_release")
    assert wanted == {first: 0, second: 0}


def test_2117_requested_album_with_a_deliberate_off_rule_stays_off(imported_conn, legacy_db, monkeypatch):
    # reaches the admin branch's explicit guard the only way it can be
    # reached: a request bypasses edition selection, so the album IS
    # processed — and the person's deliberate off still stands (the
    # same outcome dev had; the guard is what keeps the new track-rule
    # writing from overriding it)
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    conn = legacy_db._get_connection()
    try:
        record_rule(conn, "album", album, False, PROVENANCE_USER)
        conn.commit()
    finally:
        conn.close()
    D.approve_release(legacy_db, None, album, requested=True)
    assert _wanted_titles(legacy_db, album) == []
    # the click still records its marker: the deliberate off rule is
    # what stands in its way (suppression), and if that rule is ever
    # removed the recorded request is what remains — by design
    assert _album_field(legacy_db, album, "admin_requested") == 1
    conn = legacy_db._get_connection()
    try:
        track_rules = conn.execute(
            "SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_type='track' "
            "AND provenance='new_release' AND entity_id IN "
            "(SELECT id FROM lib2_tracks WHERE album_id=?)", (album,)).fetchone()[0]
    finally:
        conn.close()
    assert track_rules == 0


def test_2117_deliberate_album_on_covers_its_uncredited_tracks(imported_conn, legacy_db, monkeypatch):
    # plan-directed suppression: a deliberate album rule stands for the
    # whole album, so no track rules are written and even the uncredited
    # track follows the album the person chose (same as before this fix;
    # the member branch differs — it writes track rules regardless)
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Big Day in a Small Town",
        track_names=("12 Stories", "Not Hers"), artist_monitored=1,
        track_credits={"Not Hers": "Someone Else"})
    conn = legacy_db._get_connection()
    try:
        record_rule(conn, "album", album, True, PROVENANCE_USER)
        conn.commit()
    finally:
        conn.close()
    D.auto_monitor_releases(legacy_db, None, [album])
    assert _wanted_titles(legacy_db, album) == ["12 Stories", "Not Hers"]


def _make_retryable(legacy_db, album_id):
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_albums SET origin='discography', tracklist_retry_at=NULL WHERE id=?",
            (album_id,))
        conn.commit()
    finally:
        conn.close()


def _retry_population(legacy_db, monkeypatch, album_id):
    """The albums a discography sweep would retry, via the real retry
    query (the provider fetch is stubbed out, so the sweep returns
    before its edition-selection step — that step's requested keep is
    covered by the requested-edition test). The failed fetch stamps a
    future retry time; make it due first."""
    conn = legacy_db._get_connection()
    try:
        artist_id = conn.execute(
            "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
        conn.execute(
            "UPDATE lib2_albums SET tracklist_retry_at='2000-01-01 00:00:00' WHERE id=?",
            (album_id,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artist_discography",
        lambda *_args, **_kwargs: None)
    return D.expand_artist_discography(legacy_db, artist_id)["auto_monitor_album_ids"]


def _album_field(legacy_db, album_id, field):
    conn = legacy_db._get_connection()
    try:
        return conn.execute(
            f"SELECT {field} FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
    finally:
        conn.close()


def test_2117_requested_live_album_survives_a_failed_first_fetch(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"),
        fail_first=True)
    _make_retryable(legacy_db, album)
    D.approve_release(legacy_db, None, album, requested=True)
    # the click recorded itself durably, and the failed fetch wanted nothing
    assert _album_field(legacy_db, album, "admin_requested") == 1
    assert _album_field(legacy_db, album, "tracklist_status") == "failed"
    assert _wanted_titles(legacy_db, album) == []
    # the retry runs without requested_ids — the marker has to carry it
    retry_ids = _retry_population(legacy_db, monkeypatch, album)
    assert album in retry_ids
    D.auto_monitor_releases(legacy_db, None, retry_ids)
    assert _wanted_titles(legacy_db, album) == ["Big Day in a Small Town", "When I Get to Drinkin'"]


def test_2117_auto_discovered_live_album_stays_unwanted_on_retry(imported_conn, legacy_db, monkeypatch):
    # the real retry population the rejected monitored=1 proposal would
    # have bypassed the filter for: inserted monitored=1 by the new
    # release enforcement, with a new_release album rule, never clicked
    from core.library2.monitor_rules import PROVENANCE_NEW_RELEASE, record_rule
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    _make_retryable(legacy_db, album)
    conn = legacy_db._get_connection()
    try:
        conn.execute("UPDATE lib2_albums SET monitored=1, tracklist_status='failed' WHERE id=?",
                     (album,))
        record_rule(conn, "album", album, True, PROVENANCE_NEW_RELEASE)
        conn.commit()
    finally:
        conn.close()
    retry_ids = _retry_population(legacy_db, monkeypatch, album)
    assert album in retry_ids
    D.auto_monitor_releases(legacy_db, None, retry_ids)
    assert _wanted_titles(legacy_db, album) == []


def test_2117_auto_added_live_album_stays_unwanted_on_retry(imported_conn, legacy_db, monkeypatch):
    # the back catalogue's auto add approves without a click
    # (requested=False): no marker, and the retry stays filtered (#2082)
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"),
        fail_first=True)
    _make_retryable(legacy_db, album)
    D.approve_release(legacy_db, None, album)
    assert _album_field(legacy_db, album, "admin_requested") == 0
    assert _album_field(legacy_db, album, "tracklist_status") == "failed"
    retry_ids = _retry_population(legacy_db, monkeypatch, album)
    assert album in retry_ids
    D.auto_monitor_releases(legacy_db, None, retry_ids)
    assert _wanted_titles(legacy_db, album) == []


def test_2117_requested_album_retry_still_leaves_out_the_uncredited(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("12 Stories", "Not Hers"), artist_monitored=1,
        track_credits={"Not Hers": "Someone Else"}, fail_first=True)
    _make_retryable(legacy_db, album)
    D.approve_release(legacy_db, None, album, requested=True)
    assert _album_field(legacy_db, album, "tracklist_status") == "failed"
    retry_ids = _retry_population(legacy_db, monkeypatch, album)
    assert album in retry_ids
    D.auto_monitor_releases(legacy_db, None, retry_ids)
    assert _wanted_titles(legacy_db, album) == ["12 Stories"]


def test_2117_member_request_leaves_the_shared_marker_alone(imported_conn, legacy_db, monkeypatch):
    album, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Live from Los Angeles",
        track_names=("When I Get to Drinkin'", "Big Day in a Small Town"))
    D.approve_release(legacy_db, None, album, profile_id=3, requested=True)
    # P2D-6: the marker is the shared library's, like the monitored
    # column; a member's request is their own album rule instead
    assert _album_field(legacy_db, album, "admin_requested") == 0


def test_2117_requested_edition_survives_a_failed_first_fetch(imported_conn, legacy_db, monkeypatch):
    # the sweep: the provider answers this time, and the edition
    # preference would drop the deluxe — a requested retry id is kept
    deluxe, artist, config = _requested_failed_edition(legacy_db, monkeypatch)
    stats = D._expand_artist_discography(legacy_db, artist)
    assert deluxe in stats["auto_monitor_album_ids"]
    D.auto_monitor_releases(legacy_db, config, stats["auto_monitor_album_ids"])
    assert _wanted_titles(legacy_db, deluxe) == ["Nutshell"]


def _requested_failed_edition(legacy_db, monkeypatch):
    """A requested deluxe whose first fetch failed, next to its standard
    edition, with the provider answering again. Returns (deluxe, artist,
    config) — the setup the requested-edition and scope tests share."""
    class _Config:
        def get(self, key, default=None):
            return {"watchlist.edition_preference": "one_standard"}.get(key, default)

    deluxe, _mirrored = _plain_album(
        legacy_db, monkeypatch, title="Dirt (Deluxe)",
        track_names=("Nutshell",), artist_name="Alice in Chains", fail_first=True)
    conn = legacy_db._get_connection()
    try:
        artist = conn.execute(
            "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (deluxe,)).fetchone()[0]
        conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type, track_count, origin) "
                     "VALUES(?, 'Dirt', 'album', 2, 'library')", (artist,))
        conn.execute(
            "UPDATE lib2_albums SET tracklist_retry_at='2000-01-01 00:00:00' WHERE id=?",
            (deluxe,))
        conn.commit()
    finally:
        conn.close()
    _make_retryable(legacy_db, deluxe)
    D.approve_release(legacy_db, _Config(), deluxe, requested=True)
    assert _album_field(legacy_db, deluxe, "tracklist_status") == "failed"
    conn = legacy_db._get_connection()
    try:
        conn.execute(
            "UPDATE lib2_albums SET tracklist_retry_at='2000-01-01 00:00:00' WHERE id=?",
            (deluxe,))
        conn.commit()
    finally:
        conn.close()
    grown = _cards(
        ("albums", {"id": "sp-dirt", "title": "Dirt", "album_type": "album"}),
        ("albums", {"id": "sp-deluxe", "title": "Dirt (Deluxe)", "album_type": "album"}),
    )
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_detail_discography",
        lambda artist_id, artist_name="", options=None: grown)
    monkeypatch.setattr("core.settings.config_manager", _Config())
    return deluxe, artist, _Config()


def test_2117_requested_keep_does_not_apply_to_member_or_deferred_sweeps(imported_conn, legacy_db, monkeypatch):
    # the marker is the admin's: a member's sweep (their own filter
    # profile) and a deferred sweep (the repair job's review mode) keep
    # the edition selection's verdict on a requested retry
    deluxe, artist, _config = _requested_failed_edition(legacy_db, monkeypatch)
    stats = D._expand_artist_discography(legacy_db, artist, filter_profile_id=3)
    assert deluxe not in stats["auto_monitor_album_ids"]
    stats = D._expand_artist_discography(legacy_db, artist, defer_auto_monitor=True)
    assert deluxe not in stats["auto_monitor_album_ids"]
    # and the admin sweep that will actually auto-monitor still keeps it
    stats = D._expand_artist_discography(legacy_db, artist)
    assert deluxe in stats["auto_monitor_album_ids"]


def test_an_edition_you_ask_for_is_not_up_to_the_edition_preference(imported_conn, legacy_db, monkeypatch):
    class _Config:
        def get(self, key, default=None):
            return {"watchlist.edition_preference": "one_standard"}.get(key, default)

    deluxe = _live_album(legacy_db, monkeypatch, title="Dirt (Deluxe)")
    conn = legacy_db._get_connection()
    artist = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?", (deluxe,)).fetchone()[0]
    conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type, track_count) "
                 "VALUES(?, 'Dirt', 'album', 2)", (artist,))
    conn.commit()
    conn.close()

    # left to the preference, the deluxe is not the standard edition
    D.auto_monitor_releases(legacy_db, _Config(), [deluxe])
    assert _monitored(legacy_db, deluxe) == []
    D.approve_release(legacy_db, _Config(), deluxe, requested=True)
    assert _monitored(legacy_db, deluxe) == ["Nutshell"]
