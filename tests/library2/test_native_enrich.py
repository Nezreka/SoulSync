"""Resolve + enrich Library-v2 native artists (no legacy back-reference).

Artists born inside lib2 (featured credits, wishlist, discography) have
``legacy_artist_id`` NULL, so the legacy-row-based enrichment pipeline can
never reach them: Enrich rejects them and manual match records an id but pulls
no artwork. ``core.library2.native_enrich`` resolves the provider identity by
name and writes id + artwork/genres straight onto the lib2 row.
"""

from __future__ import annotations

import json

import pytest

from core.library2 import match_status as MS
from core.library2 import native_enrich as NE


def _insert_native_artist(conn, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES(?, ?)", (name, name)
    )
    return int(cur.lastrowid)


def test_resolve_stores_spotify_id_image_genres_and_flips_chip(imported_conn):
    aid = _insert_native_artist(imported_conn, "Afrojack")

    def resolver(name):
        assert name == "Afrojack"
        return {
            "source": "spotify",
            "artist_id": "SP_AFRO",
            "name": "Afrojack",
            "image_url": "http://img/afro.jpg",
            "genres": ["big room", "edm"],
        }

    result = NE.resolve_and_enrich_native_artist(imported_conn, aid, resolver=resolver)

    assert result["success"] is True
    row = imported_conn.execute(
        "SELECT spotify_id, image_url, genres FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] == "SP_AFRO"
    assert row["image_url"] == "http://img/afro.jpg"
    assert json.loads(row["genres"]) == ["big room", "edm"]

    chips = {c["service"]: c for c in MS.entity_match_status(imported_conn, "artist", aid)}
    assert chips["spotify"]["status"] == "matched"
    assert chips["spotify"]["external_id"] == "SP_AFRO"


def test_resolve_non_spotify_writes_external_ids_and_flips_chip(imported_conn):
    aid = _insert_native_artist(imported_conn, "Some DJ")
    resolver = lambda name: {  # noqa: E731
        "source": "deezer", "artist_id": "DZ123", "name": name,
        "image_url": None, "genres": [],
    }

    NE.resolve_and_enrich_native_artist(imported_conn, aid, resolver=resolver)

    ext = json.loads(
        imported_conn.execute(
            "SELECT external_ids FROM lib2_artists WHERE id=?", (aid,)
        ).fetchone()["external_ids"] or "{}"
    )
    assert ext.get("deezer") == "DZ123"
    chips = {c["service"]: c for c in MS.entity_match_status(imported_conn, "artist", aid)}
    assert chips["deezer"]["status"] == "matched"
    assert chips["deezer"]["external_id"] == "DZ123"


def test_resolve_does_not_clobber_other_provider_external_ids(imported_conn):
    aid = _insert_native_artist(imported_conn, "Multi")
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids=? WHERE id=?",
        (json.dumps({"itunes": "IT9"}), aid),
    )
    resolver = lambda name: {"source": "deezer", "artist_id": "DZ1", "name": name}  # noqa: E731

    NE.resolve_and_enrich_native_artist(imported_conn, aid, resolver=resolver)

    ext = json.loads(
        imported_conn.execute(
            "SELECT external_ids FROM lib2_artists WHERE id=?", (aid,)
        ).fetchone()["external_ids"]
    )
    assert ext == {"itunes": "IT9", "deezer": "DZ1"}


def test_resolve_no_match_returns_attempted_and_leaves_row_untouched(imported_conn):
    aid = _insert_native_artist(imported_conn, "Big Sean and BabyTron")
    resolver = lambda name: None  # noqa: E731

    result = NE.resolve_and_enrich_native_artist(imported_conn, aid, resolver=resolver)

    assert result["success"] is False
    assert result["attempted"] is True
    row = imported_conn.execute(
        "SELECT spotify_id, image_url FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] is None
    assert row["image_url"] is None


def test_legacy_backed_artist_is_rejected(imported_conn):
    drake = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()["id"]

    with pytest.raises(ValueError):
        NE.resolve_and_enrich_native_artist(
            imported_conn, drake,
            resolver=lambda n: {"source": "spotify", "artist_id": "x"},
        )


def test_enrich_native_artwork_writes_image_from_stored_ids(imported_conn):
    aid = _insert_native_artist(imported_conn, "Afrojack")
    imported_conn.execute("UPDATE lib2_artists SET spotify_id='SP1' WHERE id=?", (aid,))
    captured = {}

    def fetcher(name, source_ids):
        captured["name"] = name
        captured["ids"] = dict(source_ids)
        return "http://cover/afro.jpg"

    ok = NE.enrich_native_artist_artwork(imported_conn, aid, artwork_fetcher=fetcher)

    assert ok is True
    assert captured["name"] == "Afrojack"
    assert captured["ids"] == {"spotify": "SP1"}
    img = imported_conn.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()["image_url"]
    assert img == "http://cover/afro.jpg"


def test_enrich_native_artwork_noop_when_no_provider_ids(imported_conn):
    aid = _insert_native_artist(imported_conn, "Nobody")
    called = []

    ok = NE.enrich_native_artist_artwork(
        imported_conn, aid,
        artwork_fetcher=lambda n, s: called.append(1) or "x",
    )

    assert ok is False
    assert called == []


def _artist_id_by_name(conn, name):
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE name=?", (name,)
    ).fetchone()
    return row["id"] if row else None


def _make_collab_release(conn, combined_id):
    """A collab single owned by the combined artist as PRIMARY, with a track."""
    cur = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
        "VALUES(?, 'Runaway (U & I)', 'single')",
        (combined_id,),
    )
    album_id = int(cur.lastrowid)
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?, ?, 'primary')",
        (album_id, combined_id),
    )
    cur = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, 'Runaway', 1)",
        (album_id,),
    )
    track_id = int(cur.lastrowid)
    conn.execute(
        "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?, ?, 'primary', 0)",
        (track_id, combined_id),
    )
    return album_id, track_id


def _component_resolver(name):
    return {"source": "spotify", "artist_id": "SP_" + name.replace(" ", "_"),
            "name": name, "image_url": "http://img/" + name, "genres": []}


def test_smart_split_rehomes_primary_album_and_deletes_ghost(imported_conn):
    combined = _insert_native_artist(imported_conn, "Ian Asher & Galantis")
    imported_conn.execute("UPDATE lib2_artists SET monitored=0 WHERE id=?", (combined,))
    album_id, track_id = _make_collab_release(imported_conn, combined)

    result = NE.smart_split_combined_artist(
        imported_conn, combined, resolver=_component_resolver
    )

    assert result is not None
    # Ghost is gone; both real components now exist and are matched.
    assert _artist_id_by_name(imported_conn, "Ian Asher & Galantis") is None
    ian = _artist_id_by_name(imported_conn, "Ian Asher")
    gal = _artist_id_by_name(imported_conn, "Galantis")
    assert ian is not None and gal is not None
    
    # Assert that components inherited the unmonitored status of the ghost artist
    for cid in (ian, gal):
        row = imported_conn.execute(
            "SELECT monitored FROM lib2_artists WHERE id=?", (cid,)
        ).fetchone()
        assert row["monitored"] == 0
    assert imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (ian,)
    ).fetchone()["spotify_id"] == "SP_Ian_Asher"

    # Album survived the ghost delete (cascade safety) and re-homed to a component.
    album = imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    assert album is not None
    assert album["primary_artist_id"] == ian

    # Both components are credited on album + track; the ghost is off them.
    alb_artists = {r["artist_id"] for r in imported_conn.execute(
        "SELECT artist_id FROM lib2_album_artists WHERE album_id=?", (album_id,))}
    assert alb_artists == {ian, gal}
    trk_artists = {r["artist_id"] for r in imported_conn.execute(
        "SELECT artist_id FROM lib2_track_artists WHERE track_id=?", (track_id,))}
    assert trk_artists == {ian, gal}
    assert imported_conn.execute(
        "SELECT 1 FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone() is not None


def test_smart_split_aborts_when_a_component_does_not_resolve(imported_conn):
    combined = _insert_native_artist(imported_conn, "Foo & Bar")
    album_id, _track = _make_collab_release(imported_conn, combined)

    def resolver(name):
        return _component_resolver(name) if name == "Foo" else None

    result = NE.smart_split_combined_artist(imported_conn, combined, resolver=resolver)

    assert result is None
    # Nothing changed: ghost intact, no phantom component created.
    assert _artist_id_by_name(imported_conn, "Foo & Bar") == combined
    assert _artist_id_by_name(imported_conn, "Foo") is None
    assert imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()["primary_artist_id"] == combined


def test_smart_split_skips_a_non_combined_name(imported_conn):
    solo = _insert_native_artist(imported_conn, "Solo Artist")
    result = NE.smart_split_combined_artist(
        imported_conn, solo, resolver=_component_resolver
    )
    assert result is None
    assert _artist_id_by_name(imported_conn, "Solo Artist") == solo


def test_smart_split_reuses_existing_component_artist(imported_conn):
    # "Galantis" already exists (e.g. imported separately); split must reuse it,
    # not create a duplicate.
    existing_gal = _insert_native_artist(imported_conn, "Galantis")
    combined = _insert_native_artist(imported_conn, "Ian Asher & Galantis")
    _make_collab_release(imported_conn, combined)

    NE.smart_split_combined_artist(imported_conn, combined, resolver=_component_resolver)

    gal_rows = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Galantis'"
    ).fetchall()
    assert len(gal_rows) == 1
    assert gal_rows[0]["id"] == existing_gal


def test_reconcile_splits_unmatched_combined_and_counts_it(imported_conn):
    combined = _insert_native_artist(imported_conn, "Ian Asher & Galantis")
    _make_collab_release(imported_conn, combined)

    def resolver(name):
        # The combined name matches nothing; each component resolves.
        if name == "Ian Asher & Galantis":
            return None
        return _component_resolver(name)

    stats = NE.reconcile_unmapped_native_artists(imported_conn, resolver=resolver)

    assert stats["split"] >= 1
    assert _artist_id_by_name(imported_conn, "Ian Asher & Galantis") is None


def test_reconcile_matches_pending_native_skips_matched_and_legacy(imported_conn):
    pending = _insert_native_artist(imported_conn, "Afrojack")
    already = _insert_native_artist(imported_conn, "Already Matched")
    imported_conn.execute(
        "UPDATE lib2_artists SET spotify_id='PRE' WHERE id=?", (already,)
    )
    calls = []

    def resolver(name):
        calls.append(name)
        return {"source": "spotify", "artist_id": "SP_" + name, "name": name}

    stats = NE.reconcile_unmapped_native_artists(imported_conn, resolver=resolver)

    # Only pending native artists are scanned: our Afrojack + the fixture's
    # featured "Wizkid"; never the already-matched native or legacy-backed Drake.
    assert "Afrojack" in calls
    assert "Already Matched" not in calls
    assert "Drake" not in calls
    assert stats["scanned"] == len(calls)
    assert stats["matched"] == len(calls)
    assert stats["unmatched"] == 0
    assert (
        imported_conn.execute(
            "SELECT spotify_id FROM lib2_artists WHERE id=?", (pending,)
        ).fetchone()["spotify_id"]
        == "SP_Afrojack"
    )


def test_smart_split_legacy_backed_artist_becomes_alias(imported_conn):
    # Insert a combined legacy-backed artist
    combined = _insert_native_artist(imported_conn, "A & B")
    imported_conn.execute(
        "UPDATE lib2_artists SET legacy_artist_id=9999 WHERE id=?", (combined,)
    )
    album_id, track_id = _make_collab_release(imported_conn, combined)

    result = NE.smart_split_combined_artist(
        imported_conn, combined, resolver=_component_resolver
    )

    assert result is not None
    # Ghost is not deleted (legacy ID preserved) but becomes an alias
    row = imported_conn.execute(
        "SELECT id, legacy_artist_id, canonical_artist_id FROM lib2_artists WHERE id=?",
        (combined,),
    ).fetchone()
    assert row is not None
    assert row["legacy_artist_id"] == 9999
    
    # Resolves to components
    a_id = _artist_id_by_name(imported_conn, "A")
    b_id = _artist_id_by_name(imported_conn, "B")
    assert a_id is not None and b_id is not None
    assert row["canonical_artist_id"] == a_id

    # Album survived and re-homed to A
    alb = imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    assert alb["primary_artist_id"] == a_id


def test_lastfm_only_artist_is_considered_pending(imported_conn):
    # Insert an artist who only has lastfm in external_ids
    aid = _insert_native_artist(imported_conn, "LastFM Artist")
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids='{\"lastfm\":\"https://last.fm/music/x\"}' WHERE id=?",
        (aid,),
    )
    
    # Run _pending_unmapped_artists
    pending = NE._pending_unmapped_artists(imported_conn, limit=None)
    pending_ids = [p["id"] for p in pending]
    
    assert aid in pending_ids

    # If they get a catalog ID (e.g. deezer), they should no longer be pending
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids='{\"lastfm\":\"https://last.fm/music/x\",\"deezer\":\"123\"}' WHERE id=?",
        (aid,),
    )
    pending = NE._pending_unmapped_artists(imported_conn, limit=None)
    pending_ids = [p["id"] for p in pending]
    assert aid not in pending_ids


