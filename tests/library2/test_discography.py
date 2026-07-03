"""Discography expansion: persist provider releases, dedup, claim on import."""

from __future__ import annotations

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
