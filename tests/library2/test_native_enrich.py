"""Resolve and enrich Library-v2 entities through provider-qualified IDs."""

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


def test_legacy_backed_artist_is_enriched_natively_in_p3(imported_conn):
    drake = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()["id"]

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, drake,
        resolver=lambda n: {"source": "deezer", "artist_id": "dz-drake"},
    )

    assert result["success"] is True
    row = imported_conn.execute(
        "SELECT external_ids FROM lib2_artists WHERE id=?", (drake,)
    ).fetchone()
    assert json.loads(row["external_ids"])["deezer"] == "dz-drake"


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


def test_service_enrichment_persists_actual_fallback_provider(imported_conn):
    """A Spotify request returning Deezer data must not create a Spotify ID."""
    aid = _insert_native_artist(imported_conn, "Fallback Artist")
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids=? WHERE id=?",
        (json.dumps({"itunes": "IT-OLD"}), aid),
    )

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "artist",
        aid,
        "spotify",
        searcher=lambda service, entity, query: [{
            "id": "DZ-ARTIST",
            "name": "Fallback Artist",
            "provider": "deezer",
            "image": "https://img.example/deezer.jpg",
        }],
    )

    assert result["requested_source"] == "spotify"
    assert result["source"] == "deezer"
    row = imported_conn.execute(
        "SELECT spotify_id, external_ids, image_url FROM lib2_artists WHERE id=?",
        (aid,),
    ).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"]) == {
        "deezer": "DZ-ARTIST",
        "itunes": "IT-OLD",
    }
    assert row["image_url"] == "https://img.example/deezer.jpg"


def test_artist_service_enrichment_rejects_near_miss_below_dedicated_threshold(
    imported_conn,
):
    """A12: artist fuzzy-matching must use the same dedicated 0.85 gate
    (core.worker_utils.artist_name_matches) as every other worker match —
    "Blanke" vs "Blance" scores ~0.83, which the old local 0.72 threshold
    would have wrongly accepted as the same artist."""
    aid = _insert_native_artist(imported_conn, "Blanke")

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "artist",
        aid,
        "spotify",
        searcher=lambda service, entity, query: [{
            "id": "SP-WRONG",
            "name": "Blance",
            "provider": "spotify",
        }],
    )

    assert result["success"] is False
    assert result["reason"] == "not_found"
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] is None


def test_artist_service_enrichment_rejects_unrelated_cjk_names(imported_conn):
    """A12: the old ASCII-only [^a-z0-9]+ normalizer collapsed any CJK name
    to '', so two completely unrelated CJK artists both normalized to ''
    and SequenceMatcher('', '').ratio() == 1.0 always accepted the first
    candidate. The dedicated gate's Unicode-aware normalizer must actually
    compare the names and reject a real mismatch."""
    aid = _insert_native_artist(imported_conn, "さよなら")

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "artist",
        aid,
        "spotify",
        searcher=lambda service, entity, query: [{
            "id": "SP-WRONG-CJK",
            "name": "こんにちは",
            "provider": "spotify",
        }],
    )

    assert result["success"] is False
    assert result["reason"] == "not_found"
    row = imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] is None


def test_album_service_enrichment_preserves_non_latin_title(imported_conn):
    artist_id = _insert_native_artist(imported_conn, "宇多田ヒカル")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, '初恋')",
        (artist_id,),
    ).lastrowid)

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "album",
        album_id,
        "spotify",
        searcher=lambda service, entity, query: [{
            "id": "SP-CJK-ALBUM",
            "name": "初恋",
            "provider": "spotify",
            "image": "https://img.example/cjk.jpg",
            "extra": "宇多田ヒカル · 2018",
        }],
    )

    assert result["success"] is True
    assert imported_conn.execute(
        "SELECT spotify_id FROM lib2_albums WHERE id=?", (album_id,),
    ).fetchone()["spotify_id"] == "SP-CJK-ALBUM"


def test_album_enrich_requires_matching_artist_context(imported_conn):
    artist_id = _insert_native_artist(imported_conn, "Right Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Home')",
        (artist_id,),
    ).lastrowid)

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "album",
        album_id,
        "spotify",
        searcher=lambda *_args: [
            {"id": "WRONG", "name": "Home", "extra": "Other Artist · 2024",
             "image": "https://img.example/wrong.jpg"},
            {"id": "RIGHT", "name": "Home", "extra": "Right Artist · 2020",
             "image": "https://img.example/right.jpg"},
        ],
    )

    assert result["provider_id"] == "RIGHT"


def test_track_enrich_requires_album_context(imported_conn, monkeypatch):
    artist_id = _insert_native_artist(imported_conn, "Right Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Right Album')",
        (artist_id,),
    ).lastrowid)
    track_id = int(imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Intro')",
        (album_id,),
    ).lastrowid)
    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_descriptive_metadata",
        lambda *_args, **_kwargs: None,
    )

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "track",
        track_id,
        "spotify",
        searcher=lambda *_args: [
            {"id": "WRONG", "name": "Intro", "extra": "Right Artist · Wrong Album"},
            {"id": "RIGHT", "name": "Intro", "extra": "Right Artist · Right Album"},
        ],
    )

    assert result["provider_id"] == "RIGHT"


def test_enrich_rejects_tied_identity_candidates(imported_conn):
    artist_id = _insert_native_artist(imported_conn, "Right Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Home')",
        (artist_id,),
    ).lastrowid)

    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "album",
        album_id,
        "spotify",
        searcher=lambda *_args: [
            {"id": "ONE", "name": "Home", "extra": "Right Artist · 2020"},
            {"id": "TWO", "name": "Home", "extra": "Right Artist · 2021"},
        ],
    )

    assert result["success"] is False
    assert result["reason"] == "ambiguous"
    assert imported_conn.execute(
        "SELECT spotify_id FROM lib2_albums WHERE id=?", (album_id,),
    ).fetchone()["spotify_id"] is None


def test_track_service_enrichment_passes_actual_provider_id_to_metadata(
    imported_conn, monkeypatch
):
    artist_id = _insert_native_artist(imported_conn, "Track Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Record')",
        (artist_id,),
    ).lastrowid)
    track_id = int(imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Exact Song')",
        (album_id,),
    ).lastrowid)
    captured = {}

    from core.library2 import provider_adapters

    def fake_track_metadata(entity_type, source_ids, *, source_order=None):
        captured["entity_type"] = entity_type
        captured["ids"] = dict(source_ids)
        captured["order"] = tuple(source_order or ())
        return provider_adapters.DescriptiveMetadataProviderResult(
            provider="itunes",
            provider_entity_id="IT-TRACK",
            duration_ms=234000,
            image_url="https://img.example/album.jpg",
        )

    monkeypatch.setattr(
        provider_adapters, "fetch_descriptive_metadata", fake_track_metadata
    )
    result = NE.enrich_native_entity_for_service(
        imported_conn,
        "track",
        track_id,
        "spotify",
        searcher=lambda service, entity, query: [{
            "id": "IT-TRACK",
            "name": "Exact Song",
            "provider": "itunes",
            "extra": "Track Artist · Record",
        }],
    )

    assert result["source"] == "itunes"
    assert captured == {
        "entity_type": "track",
        "ids": {"itunes": "IT-TRACK"},
        "order": ("itunes",),
    }
    row = imported_conn.execute(
        "SELECT spotify_id, external_ids, duration FROM lib2_tracks WHERE id=?",
        (track_id,),
    ).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"])["itunes"] == "IT-TRACK"
    assert row["duration"] == 234000
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()["image_url"] == "https://img.example/album.jpg"


def test_enrich_updates_descriptive_fields_for_already_matched_artist(
    imported_conn, monkeypatch,
):
    from core.library2 import provider_adapters

    artist_id = _insert_native_artist(imported_conn, "Matched Artist")
    imported_conn.execute(
        "UPDATE lib2_artists SET spotify_id='SP-ARTIST' WHERE id=?", (artist_id,),
    )
    monkeypatch.setattr(
        provider_adapters,
        "fetch_descriptive_metadata",
        lambda *_args, **_kwargs: provider_adapters.DescriptiveMetadataProviderResult(
            provider="spotify", provider_entity_id="SP-ARTIST",
            image_url="https://img.example/artist.jpg", genres=("ambient", "idm"),
            summary="Provider biography", style="Electronic", mood="Dreamy",
            label="Artist Label", banner_url="https://img.example/banner.jpg",
        ),
    )

    result = NE.enrich_native_entity_for_service(
        imported_conn, "artist", artist_id, "spotify",
        searcher=lambda *_args: (_ for _ in ()).throw(
            AssertionError("matched entities must not search")
        ),
    )

    assert result["success"] is True
    row = imported_conn.execute(
        "SELECT image_url, genres, summary, style, mood, label, banner_url "
        "FROM lib2_artists WHERE id=?", (artist_id,),
    ).fetchone()
    assert dict(row) == {
        "image_url": "https://img.example/artist.jpg",
        "genres": '["ambient", "idm"]',
        "summary": "Provider biography",
        "style": "Electronic",
        "mood": "Dreamy",
        "label": "Artist Label",
        "banner_url": "https://img.example/banner.jpg",
    }


def test_enrich_updates_descriptive_fields_for_already_matched_album(
    imported_conn, monkeypatch,
):
    from core.library2 import provider_adapters

    artist_id = _insert_native_artist(imported_conn, "Matched Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, spotify_id) "
        "VALUES(?, 'Matched Album', 'SP-ALBUM')", (artist_id,),
    ).lastrowid)
    monkeypatch.setattr(
        provider_adapters,
        "fetch_descriptive_metadata",
        lambda *_args, **_kwargs: provider_adapters.DescriptiveMetadataProviderResult(
            provider="spotify", provider_entity_id="SP-ALBUM",
            image_url="https://img.example/album.jpg", genres=("house",),
            year=2026, release_date="2026-07-22", label="Album Label",
            upc="123456789", style="Club", mood="Energetic", explicit=False,
        ),
    )

    NE.enrich_native_entity_for_service(
        imported_conn, "album", album_id, "spotify",
    )

    row = imported_conn.execute(
        "SELECT image_url, genres, year, release_date, label, upc, style, mood, explicit "
        "FROM lib2_albums WHERE id=?", (album_id,),
    ).fetchone()
    assert dict(row) == {
        "image_url": "https://img.example/album.jpg", "genres": '["house"]',
        "year": 2026, "release_date": "2026-07-22", "label": "Album Label",
        "upc": "123456789", "style": "Club", "mood": "Energetic",
        "explicit": 0,
    }


def test_enrich_updates_descriptive_fields_for_already_matched_track(
    imported_conn, monkeypatch,
):
    from core.library2 import provider_adapters

    artist_id = _insert_native_artist(imported_conn, "Matched Artist")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
        (artist_id,),
    ).lastrowid)
    track_id = int(imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, spotify_id) "
        "VALUES(?, 'Track', 'SP-TRACK')", (album_id,),
    ).lastrowid)
    monkeypatch.setattr(
        provider_adapters,
        "fetch_descriptive_metadata",
        lambda *_args, **_kwargs: provider_adapters.DescriptiveMetadataProviderResult(
            provider="spotify", provider_entity_id="SP-TRACK",
            image_url="https://img.example/track-album.jpg", duration_ms=201000,
            bpm=128.5, explicit=True, lyrics="Lyrics", copyright="Copyright",
            style="Dance", mood="Upbeat",
        ),
    )

    NE.enrich_native_entity_for_service(
        imported_conn, "track", track_id, "spotify",
    )

    row = imported_conn.execute(
        "SELECT duration, bpm, explicit, genius_lyrics, copyright, style, mood "
        "FROM lib2_tracks WHERE id=?", (track_id,),
    ).fetchone()
    assert dict(row) == {
        "duration": 201000, "bpm": 128.5, "explicit": 1,
        "genius_lyrics": "Lyrics", "copyright": "Copyright",
        "style": "Dance", "mood": "Upbeat",
    }
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_albums WHERE id=?", (album_id,),
    ).fetchone()["image_url"] == "https://img.example/track-album.jpg"


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


def _insert_album(conn, artist_id, title, *, spotify_id=None, musicbrainz_id=None,
                   external_ids=None, featured_artist_id=None):
    cur = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, spotify_id, "
        "musicbrainz_id, external_ids) VALUES(?, ?, ?, ?, ?)",
        (artist_id, title, spotify_id, musicbrainz_id,
         json.dumps(external_ids or {})),
    )
    album_id = int(cur.lastrowid)
    if featured_artist_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(?, ?, 'featured')",
            (album_id, featured_artist_id),
        )
    return album_id


def _insert_track(conn, album_id, title, *, spotify_id=None, musicbrainz_id=None,
                   external_ids=None, featured_artist_id=None):
    cur = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, spotify_id, musicbrainz_id, "
        "external_ids) VALUES(?, ?, ?, ?, ?)",
        (album_id, title, spotify_id, musicbrainz_id, json.dumps(external_ids or {})),
    )
    track_id = int(cur.lastrowid)
    if featured_artist_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
            "VALUES(?, ?, 'featured', 1)",
            (track_id, featured_artist_id),
        )
    return track_id


def _refusing_resolver(name):
    raise AssertionError(
        f"name-based resolver must not run when a strong catalog anchor exists (got {name!r})"
    )


def test_resolve_uses_album_anchor_instead_of_name_search(imported_conn):
    aid = _insert_native_artist(imported_conn, "Afrojack")
    _insert_album(imported_conn, aid, "Ten Feet Tall", spotify_id="SP-ALBUM-1")

    def anchor_resolver(source, kind, provider_id):
        assert (source, kind, provider_id) == ("spotify", "album", "SP-ALBUM-1")
        return {"source": "spotify", "artist_id": "SP-ANCHORED-AFRO",
                "name": "Afrojack", "image_url": "http://img/afro.jpg", "genres": ["edm"]}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid, resolver=_refusing_resolver, anchor_resolver=anchor_resolver,
    )

    assert result["success"] is True
    assert result["source"] == "spotify"
    assert result["provider_id"] == "SP-ANCHORED-AFRO"
    row = imported_conn.execute(
        "SELECT spotify_id, image_url FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] == "SP-ANCHORED-AFRO"
    assert row["image_url"] == "http://img/afro.jpg"


def test_resolve_collects_every_anchored_source_not_just_the_first(imported_conn):
    aid = _insert_native_artist(imported_conn, "Multi Source")
    _insert_album(
        imported_conn, aid, "One Release",
        spotify_id="SP-A", musicbrainz_id="MB-A",
        external_ids={"deezer": "DZ-A"},
    )

    def anchor_resolver(source, kind, provider_id):
        return {"source": source, "artist_id": f"{source.upper()}-ARTIST", "name": "Multi Source"}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid, resolver=_refusing_resolver, anchor_resolver=anchor_resolver,
    )

    assert result["success"] is True
    assert set(result["anchor_sources"]) == {"spotify", "musicbrainz", "deezer"}
    row = imported_conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] == "SPOTIFY-ARTIST"
    assert row["musicbrainz_id"] == "MUSICBRAINZ-ARTIST"
    assert json.loads(row["external_ids"])["deezer"] == "DEEZER-ARTIST"


def test_resolve_uses_track_anchor_when_no_album_anchor_exists(imported_conn):
    aid = _insert_native_artist(imported_conn, "Track Only")
    album_id = _insert_album(imported_conn, aid, "Untagged Album")
    _insert_track(imported_conn, album_id, "Some Song", spotify_id="SP-TRACK-1")

    def anchor_resolver(source, kind, provider_id):
        assert (source, kind, provider_id) == ("spotify", "track", "SP-TRACK-1")
        return {"source": "spotify", "artist_id": "SP-FROM-TRACK", "name": "Track Only"}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid, resolver=_refusing_resolver, anchor_resolver=anchor_resolver,
    )

    assert result["success"] is True
    assert result["provider_id"] == "SP-FROM-TRACK"


def test_resolve_ignores_anchors_of_releases_the_artist_is_only_featured_on(
    imported_conn,
):
    """A featured credit is NOT an anchor.

    ``get_album_artist_identity_for_source`` answers with the album's PRIMARY
    artist — that is its whole contract. Anchoring a guest on a release they
    merely appear on therefore resolves to somebody else every single time: on
    the production library twelve guests of one Major Lazer release ended up
    holding Major Lazer's Spotify id, artwork and discography, and ten more
    held Sawano Hiroyuki's. Only a primary credit may anchor.
    """
    primary = _insert_native_artist(imported_conn, "Main Act")
    featured = _insert_native_artist(imported_conn, "Guest Verse")
    album_id = _insert_album(
        imported_conn, primary, "Collab Album",
        spotify_id="SP-COLLAB", featured_artist_id=featured,
    )
    _insert_track(
        imported_conn, album_id, "Collab Song",
        spotify_id="SP-COLLAB-TRACK", featured_artist_id=featured,
    )

    # The anchor loop swallows exceptions on purpose (one bad source must not
    # abort the rest), so record the calls instead of raising inside it.
    anchor_calls = []

    def anchor_resolver(source, kind, provider_id):
        anchor_calls.append((source, kind, provider_id))
        return {"source": "spotify", "artist_id": "SP-MAIN-ACT", "name": "Main Act"}

    def name_resolver(name):
        assert name == "Guest Verse"
        return {"source": "spotify", "artist_id": "SP-REAL-GUEST", "name": name}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, featured, resolver=name_resolver, anchor_resolver=anchor_resolver,
    )

    assert anchor_calls == []
    assert result["success"] is True
    assert result["provider_id"] == "SP-REAL-GUEST"


def test_resolve_refuses_an_identity_another_artist_already_holds(imported_conn):
    """Two catalogue rows are two artists. Whatever a resolver claims, the same
    provider identity may not be stamped onto both — that fan-out is precisely
    what put one artist's photo and discography on a dozen unrelated pages."""
    owner = _insert_native_artist(imported_conn, "Major Lazer")
    imported_conn.execute(
        "UPDATE lib2_artists SET spotify_id='SP-TAKEN' WHERE id=?", (owner,))
    other = _insert_native_artist(imported_conn, "DJ Snake")

    def name_resolver(name):
        return {"source": "spotify", "artist_id": "SP-TAKEN", "name": name}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, other, resolver=name_resolver,
        anchor_resolver=lambda *a: None,
    )

    assert result["success"] is False
    assert imported_conn.execute(
        "SELECT spotify_id FROM lib2_artists WHERE id=?", (other,)
    ).fetchone()["spotify_id"] is None


def test_resolve_falls_back_to_name_search_without_any_anchor(imported_conn):
    aid = _insert_native_artist(imported_conn, "No Catalog Yet")
    anchor_calls = []

    def anchor_resolver(source, kind, provider_id):
        anchor_calls.append((source, kind, provider_id))
        return None

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid,
        resolver=lambda name: {"source": "spotify", "artist_id": "SP-NAMED", "name": name},
        anchor_resolver=anchor_resolver,
    )

    assert anchor_calls == []
    assert result["success"] is True
    assert result["provider_id"] == "SP-NAMED"
    assert "anchor_sources" not in result


def test_resolve_falls_back_to_name_search_when_anchor_lookup_fails(imported_conn):
    aid = _insert_native_artist(imported_conn, "Stale Anchor")
    _insert_album(imported_conn, aid, "Delisted Album", spotify_id="SP-GONE")

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid,
        resolver=lambda name: {"source": "deezer", "artist_id": "DZ-FALLBACK", "name": name},
        anchor_resolver=lambda source, kind, provider_id: None,
    )

    assert result["success"] is True
    assert result["source"] == "deezer"
    assert result["provider_id"] == "DZ-FALLBACK"


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


# --- issues.md §16 Finding 2: cooldown for permanently unmatched artists ----


def _attempt_marker(conn, artist_id):
    return conn.execute(
        "SELECT unmapped_last_attempted_at FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()["unmapped_last_attempted_at"]


def test_reconcile_marks_the_attempt_on_an_unmatched_artist(imported_conn):
    aid = _insert_native_artist(imported_conn, "Nobody Knows This Name")

    NE.reconcile_unmapped_native_artists(imported_conn, resolver=lambda name: None)

    assert _attempt_marker(imported_conn, aid) is not None


def test_reconcile_marks_the_attempt_even_when_the_resolver_raises(imported_conn):
    aid = _insert_native_artist(imported_conn, "Exploding Resolver")
    imported_conn.commit()  # the pass rolls back on error; the row predates it

    def resolver(name):
        raise RuntimeError("provider down")

    stats = NE.reconcile_unmapped_native_artists(imported_conn, resolver=resolver)

    assert stats["errors"] >= 1
    # The rollback must not take the backoff marker with it, otherwise a
    # permanently failing source is re-hammered on every automated trigger.
    assert _attempt_marker(imported_conn, aid) is not None


def test_automated_reconcile_skips_an_artist_inside_the_cooldown_window(imported_conn):
    aid = _insert_native_artist(imported_conn, "Recently Tried")
    imported_conn.execute(
        "UPDATE lib2_artists SET unmapped_last_attempted_at=CURRENT_TIMESTAMP WHERE id=?",
        (aid,),
    )
    calls = []

    NE.reconcile_unmapped_native_artists(
        imported_conn,
        resolver=lambda name: calls.append(name) or None,
        cooldown_hours=168,
    )

    assert "Recently Tried" not in calls


def test_automated_reconcile_retries_once_the_cooldown_window_passed(imported_conn):
    aid = _insert_native_artist(imported_conn, "Tried Long Ago")
    imported_conn.execute(
        "UPDATE lib2_artists SET unmapped_last_attempted_at=datetime('now','-200 hours') "
        "WHERE id=?",
        (aid,),
    )
    calls = []

    NE.reconcile_unmapped_native_artists(
        imported_conn,
        resolver=lambda name: calls.append(name) or None,
        cooldown_hours=168,
    )

    assert "Tried Long Ago" in calls


def test_manual_reconcile_ignores_the_cooldown(imported_conn):
    _insert_native_artist(imported_conn, "Recently Tried")
    imported_conn.execute(
        "UPDATE lib2_artists SET unmapped_last_attempted_at=CURRENT_TIMESTAMP "
        "WHERE name='Recently Tried'"
    )
    calls = []

    # No cooldown_hours: the user pressed the button, they get the full backlog.
    NE.reconcile_unmapped_native_artists(
        imported_conn, resolver=lambda name: calls.append(name) or None,
    )

    assert "Recently Tried" in calls


def test_enrich_all_services_walks_every_supported_provider(monkeypatch):
    """One provider id is enough to exist and not enough to work. Each service
    is independent: one being down or having no match must not cost the rest."""
    from core.library2 import native_enrich

    calls = []

    def fake_one(_conn, entity_type, entity_id, service):
        calls.append((entity_type, entity_id, service))
        if service == "musicbrainz":
            raise RuntimeError("provider down")
        if service == "spotify":
            # iss29-D06: the real `enrich_native_entity_for_service` returns
            # `provider_id`; it has never returned `external_id`. This stub said
            # `external_id`, so the aggregate returned {} in production while
            # this test stayed green — the defect and its guard cancelled out.
            return {"success": True, "source": "spotify", "provider_id": "sp-1"}
        return {"success": False}

    monkeypatch.setattr(native_enrich, "enrich_native_entity_for_service", fake_one)

    resolved = native_enrich.enrich_native_entity_all_services(None, "album", 7)

    assert resolved == {"spotify": "sp-1"}
    services = [c[2] for c in calls]
    # A failing provider does not end the walk.
    assert "musicbrainz" in services and len(services) > 2
    assert all(c[:2] == ("album", 7) for c in calls)


def test_enrich_all_services_skips_providers_that_do_not_support_the_entity(monkeypatch):
    from core.library2 import native_enrich

    calls = []
    monkeypatch.setattr(
        native_enrich, "enrich_native_entity_for_service",
        lambda _c, _t, _i, service: calls.append(service) or {"success": False})

    native_enrich.enrich_native_entity_all_services(None, "artist", 1)

    # Bandcamp has no artist-level column in the SERVICES matrix.
    assert "bandcamp" not in calls
    assert "spotify" in calls


def test_enrich_all_services_releases_the_write_lock_between_providers(monkeypatch):
    """Each service does a blocking provider call and then writes on this
    connection. Holding the write transaction open across the NEXT service's
    network call kept SQLite's single writer lock for the whole walk, and
    every other request — including the monitor POST that started the walk —
    died on "database is locked" after the 30s busy timeout."""
    from core.library2 import native_enrich

    events = []

    class FakeConn:
        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    def fake_one(_conn, _entity_type, _entity_id, service):
        events.append(f"call:{service}")
        if service == "musicbrainz":
            raise RuntimeError("provider down")
        return {"success": False}

    monkeypatch.setattr(native_enrich, "enrich_native_entity_for_service", fake_one)

    native_enrich.enrich_native_entity_all_services(FakeConn(), "album", 7, commit=True)

    # Never two provider calls in a row without releasing the transaction.
    calls = [i for i, e in enumerate(events) if e.startswith("call:")]
    for first, second in zip(calls, calls[1:]):
        assert any(e in ("commit", "rollback") for e in events[first + 1:second])
    # A failing provider releases too, instead of leaving the walk holding it.
    failed = events.index("call:musicbrainz")
    assert "rollback" in events[failed + 1:failed + 3]


def test_enrich_all_services_does_not_touch_the_connection_without_commit(monkeypatch):
    """The synchronous caller keeps owning its own transaction boundary."""
    from core.library2 import native_enrich

    class Boom:
        def commit(self):
            raise AssertionError("must not commit the caller's transaction")

        def rollback(self):
            raise AssertionError("must not roll back the caller's transaction")

    monkeypatch.setattr(
        native_enrich, "enrich_native_entity_for_service",
        lambda *_a, **_k: {"success": False})

    assert native_enrich.enrich_native_entity_all_services(Boom(), "album", 7) == {}


def test_enrich_all_services_skips_unconfigured_providers(monkeypatch):
    """Tidal's client starts an interactive login, so walking a provider the
    instance never configured opened an OAuth tab in the user's browser
    seconds after a background enrich started."""
    from core.library2 import native_enrich

    calls = []
    monkeypatch.setattr(
        native_enrich, "enrich_native_entity_for_service",
        lambda _c, _t, _i, service: calls.append(service) or {"success": False})

    native_enrich.enrich_native_entity_all_services(
        None, "album", 7, services={"spotify", "deezer"})

    assert set(calls) == {"spotify", "deezer"}


def test_enrichment_releases_the_write_lock_before_calling_a_provider(
    legacy_db, monkeypatch,
):
    """The provider clients cache into the SAME database on their own
    connection, so holding a write transaction across a provider call
    deadlocked the enrichment thread against itself: its second connection
    waited out the full busy timeout while every other writer in the process
    waited behind it. That is the production "database is locked" storm.

    This reproduces the shape exactly — the fake provider call does what the
    real Spotify client does, a write through an independent connection — and
    fails on the previous code with 'database is locked'.
    """
    from core.library2.importer import import_legacy_library
    from core.library2.schema import ensure_library_v2_schema

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    ensure_library_v2_schema(conn)
    try:
        artist_id = _insert_native_artist(conn, "Deadlock Artist")
        conn.commit()

        observed = {}

        def _fake_provider(*_args, **_kwargs):
            # Exactly what a provider client does after answering: cache the
            # response through its own connection to the same database.
            other = legacy_db._get_connection()
            try:
                other.execute("PRAGMA busy_timeout = 1500")
                other.execute(
                    "UPDATE lib2_artists SET summary='cached' WHERE id=?",
                    (artist_id,),
                )
                other.commit()
                observed["cached"] = True
            finally:
                other.close()
            return None

        monkeypatch.setattr(
            "core.library2.provider_adapters.fetch_descriptive_metadata",
            _fake_provider,
        )

        result = NE.enrich_native_entity_for_service(
            conn, "artist", artist_id, "spotify",
            searcher=lambda service, entity, query: [{
                "id": "SP-DEADLOCK",
                "name": "Deadlock Artist",
                "provider": "spotify",
                "image": "https://img.example/a.jpg",
            }],
        )

        assert result["success"] is True
        assert observed.get("cached") is True, (
            "the provider's own cache write could not get the write lock — "
            "the enrichment transaction was still open across the call"
        )
    finally:
        conn.close()


# --- iss29-D01: the writer must be released across provider calls ---------


def test_anchor_walk_never_holds_a_write_transaction_across_a_provider_call(imported_conn):
    """The anchor loop must not keep an open write transaction over network I/O.

    This is the deadlock class this project already took a production outage on,
    and which this very file documents 300 lines further down: the provider
    clients cache their responses in the SAME SQLite database through their own
    connection. A writer held across a provider call therefore waits on itself —
    it blocks the cache write, waits out the full 30 s busy timeout, and every
    other writer in the process queues behind it.

    ``_persist_identity`` is a bare UPDATE and ``isolation_level=""`` opens an
    implicit transaction on DML, so persisting anchor *n* and then resolving
    anchor *n+1* is exactly that pattern. The window is
    (anchors − 1) × (provider latency + up to 30 s busy timeout), reachable both
    from the maintenance button and from the automatic post-import trigger.

    Asserting on ``in_transaction`` rather than on call ORDER is deliberate: a
    future refactor that reintroduces an interleaved write would still be caught,
    however the loop is spelled.
    """
    aid = _insert_native_artist(imported_conn, "Multi Anchor")
    _insert_album(
        imported_conn, aid, "One Release",
        spotify_id="SP-A", musicbrainz_id="MB-A",
        external_ids={"deezer": "DZ-A"},
    )
    imported_conn.commit()

    transaction_states = []

    def anchor_resolver(source, kind, provider_id):
        # Stand-in for the blocking provider roundtrip.
        transaction_states.append((source, imported_conn.in_transaction))
        return {"source": source, "artist_id": f"{source.upper()}-ARTIST",
                "name": "Multi Anchor"}

    result = NE.resolve_and_enrich_native_artist(
        imported_conn, aid, resolver=_refusing_resolver, anchor_resolver=anchor_resolver,
    )

    assert result["success"] is True
    assert len(transaction_states) == 3, transaction_states
    held = [source for source, in_txn in transaction_states if in_txn]
    assert held == [], f"write transaction still open during provider calls for {held}"

    # ...and the writes themselves must still all land.
    row = imported_conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?", (aid,)
    ).fetchone()
    assert row["spotify_id"] == "SPOTIFY-ARTIST"
    assert row["musicbrainz_id"] == "MUSICBRAINZ-ARTIST"
    assert json.loads(row["external_ids"])["deezer"] == "DEEZER-ARTIST"


# --- MusicBrainz decides its own identity ----------------------------------


def test_musicbrainz_artist_enrichment_asks_match_artist_not_the_name_gate(
    imported_conn, monkeypatch,
):
    """The Enrich button could not reach a cross-script artist at all.

    This path ranks a provider's search results by normalised-name similarity,
    and across scripts that is 0.0 by construction — so `澤野弘之`, the entity
    MusicBrainz itself returns first at score 100, was discarded, while
    `SawanoHiroyuki[nZk]` (a different MusicBrainz entity) normalises to 0.88
    against "Sawano Hiroyuki" and clears the 0.85 gate. Nothing about that is
    specific to the button: the provider-gap backfill drives the same function
    across the whole library, so it could write that wrong id onto every
    cross-script artist it touched — and the AcoustID alias bridge reads exactly
    that id.
    """
    aid = _insert_native_artist(imported_conn, "Sawano Hiroyuki")
    asked = {}

    class _Service:
        def match_artist(self, name, owned_titles=None):
            asked["name"] = name
            asked["owned_titles"] = list(owned_titles or [])
            return {"mbid": "mbid-sawano", "name": "澤野弘之", "confidence": 80}

    monkeypatch.setattr("core.musicbrainz_service.get_musicbrainz_service",
                        lambda: _Service())

    result = NE.enrich_native_entity_for_service(
        imported_conn, "artist", aid, "musicbrainz",
        searcher=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("the name-similarity path must not be used")),
    )

    assert result["success"] is True
    assert result["provider_id"] == "mbid-sawano"
    assert asked["name"] == "Sawano Hiroyuki"
    row = imported_conn.execute(
        "SELECT musicbrainz_id FROM lib2_artists WHERE id=?", (aid,)).fetchone()
    assert row["musicbrainz_id"] == "mbid-sawano"


def test_musicbrainz_artist_enrichment_falls_through_when_no_match(
    imported_conn, monkeypatch,
):
    """No answer from `match_artist` is not a reason to skip the generic path."""
    aid = _insert_native_artist(imported_conn, "Some Artist")

    class _Service:
        def match_artist(self, name, owned_titles=None):
            return None

    monkeypatch.setattr("core.musicbrainz_service.get_musicbrainz_service",
                        lambda: _Service())

    result = NE.enrich_native_entity_for_service(
        imported_conn, "artist", aid, "musicbrainz",
        searcher=lambda *_a, **_k: [{
            "id": "MB-FROM-SEARCH", "name": "Some Artist",
            "provider": "musicbrainz"}],
    )

    assert result["provider_id"] == "MB-FROM-SEARCH"


def test_musicbrainz_album_enrichment_is_untouched(imported_conn, monkeypatch):
    """Only artists have a `match_artist`; albums keep the generic path."""
    monkeypatch.setattr(
        "core.musicbrainz_service.get_musicbrainz_service",
        lambda: (_ for _ in ()).throw(AssertionError("albums must not ask")))
    aid = _insert_native_artist(imported_conn, "Album Artist")
    cur = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, ?)",
        (aid, "Some Album"))
    album_id = int(cur.lastrowid)

    result = NE.enrich_native_entity_for_service(
        imported_conn, "album", album_id, "musicbrainz",
        searcher=lambda *_a, **_k: [{
            "id": "MB-RELEASE", "name": "Some Album", "provider": "musicbrainz",
            "artist_name": "Album Artist"}],
    )

    assert result["provider_id"] == "MB-RELEASE"


def test_release_borrowed_identities_clears_guests_and_keeps_the_owner(imported_conn):
    """Backlog healer for rows that already hold somebody else's identity."""
    owner = _insert_native_artist(imported_conn, "Major Lazer")
    imported_conn.execute(
        "UPDATE lib2_artists SET spotify_id='SP-ML', image_url='http://img/ml.jpg', "
        "genres='[\"moombahton\"]' WHERE id=?",
        (owner,),
    )
    _insert_album(imported_conn, owner, "Peace Is The Mission", spotify_id="SP-PITM")
    guests = []
    for name in ("DJ Snake", "MØ"):
        gid = _insert_native_artist(imported_conn, name)
        imported_conn.execute(
            "UPDATE lib2_artists SET spotify_id='SP-ML', image_url='http://img/ml.jpg', "
            "genres='[\"moombahton\"]' WHERE id=?",
            (gid,),
        )
        guests.append(gid)

    stats = NE.release_borrowed_artist_identities(imported_conn)

    assert stats["released"] == 2
    assert sorted(stats["artist_ids"]) == sorted(guests)
    assert imported_conn.execute(
        "SELECT spotify_id, image_url, genres FROM lib2_artists WHERE id=?", (owner,)
    ).fetchone()["spotify_id"] == "SP-ML"
    for gid in guests:
        row = imported_conn.execute(
            "SELECT spotify_id, image_url, genres FROM lib2_artists WHERE id=?", (gid,)
        ).fetchone()
        assert row["spotify_id"] is None
        # the artwork and genres arrived with the borrowed identity, byte for
        # byte — they go with it.
        assert row["image_url"] is None
        assert row["genres"] == "[]"


def test_release_borrowed_identities_leaves_ambiguous_groups_alone(imported_conn):
    """Two co-composers of one soundtrack, neither the primary of any release:
    nothing in the catalogue says which one owns the id, so neither is touched
    and the group is reported instead."""
    first = _insert_native_artist(imported_conn, "Marcin Przybyłowicz")
    second = _insert_native_artist(imported_conn, "Paul Leonard-Morgan")
    for aid in (first, second):
        imported_conn.execute(
            "UPDATE lib2_artists SET spotify_id='SP-WITCHER' WHERE id=?", (aid,))

    stats = NE.release_borrowed_artist_identities(imported_conn)

    assert stats["released"] == 0
    assert stats["ambiguous"] == 1
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE spotify_id='SP-WITCHER'"
    ).fetchone()[0] == 2


def _browse_only_artist(conn, name):
    """An artist that exists only because a browse-only release credited it."""
    aid = _insert_native_artist(conn, name)
    owner = _insert_native_artist(conn, f"{name} Host")
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, ?, 'discography', 0)",
        (owner, f"{name} Host Record"),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'featured')",
        (album_id, aid),
    )
    return aid, album_id


def test_prune_removes_artists_that_only_browse_only_releases_credited(imported_conn):
    """82 of 380 artists on the production library existed for this reason
    alone — listed as library artists with a necessarily empty My Library."""
    ghost, _album = _browse_only_artist(imported_conn, "Ghost Guest")

    stats = NE.prune_browse_only_artists(imported_conn)

    assert stats["pruned"] == 1
    assert stats["artist_ids"] == [ghost]
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE id=?", (ghost,)
    ).fetchone()[0] == 0


def test_prune_keeps_a_guest_on_a_release_the_user_actually_has(imported_conn):
    guest, album_id = _browse_only_artist(imported_conn, "Real Guest")
    imported_conn.execute(
        "UPDATE lib2_albums SET origin='library' WHERE id=?", (album_id,))

    assert NE.prune_browse_only_artists(imported_conn)["pruned"] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE id=?", (guest,)
    ).fetchone()[0] == 1


def test_prune_keeps_a_guest_on_a_release_the_user_wants(imported_conn):
    guest, album_id = _browse_only_artist(imported_conn, "Wanted Guest")
    imported_conn.execute(
        "UPDATE lib2_albums SET monitored=1 WHERE id=?", (album_id,))

    assert NE.prune_browse_only_artists(imported_conn)["pruned"] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE id=?", (guest,)
    ).fetchone()[0] == 1


def test_prune_keeps_monitored_art_locked_and_user_touched_rows(imported_conn):
    monitored, _ = _browse_only_artist(imported_conn, "Monitored Guest")
    imported_conn.execute(
        "UPDATE lib2_artists SET monitored=1 WHERE id=?", (monitored,))
    locked, _ = _browse_only_artist(imported_conn, "Art Locked Guest")
    imported_conn.execute(
        "UPDATE lib2_artists SET art_locked=1 WHERE id=?", (locked,))

    assert NE.prune_browse_only_artists(imported_conn)["pruned"] == 0


def test_prune_keeps_an_artist_that_fronts_anything_and_one_with_a_file(imported_conn):
    fronts = _insert_native_artist(imported_conn, "Fronts A Record")
    imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, 'Their Own Record', 'discography', 0)",
        (fronts,),
    )
    owning, album_id = _browse_only_artist(imported_conn, "Owns A File")
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Guest Spot')",
        (album_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?,'featured',1)", (track_id, owning))
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?, '/m/guest.flac')",
        (track_id,))

    assert NE.prune_browse_only_artists(imported_conn)["pruned"] == 0


def test_prune_never_touches_an_artist_with_no_credits_at_all(imported_conn):
    """A watchlisted or freshly created artist has an empty page too, but it is
    there because somebody asked for it — not because a browse-only tracklist
    mentioned it."""
    lonely = _insert_native_artist(imported_conn, "Nothing Yet")

    assert NE.prune_browse_only_artists(imported_conn)["pruned"] == 0
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_artists WHERE id=?", (lonely,)
    ).fetchone()[0] == 1


def test_prune_removes_the_lead_of_a_browse_only_single(imported_conn):
    """Position 0 of any credit list is stored as ``role='primary'``, so
    fronting a browse-only TRACK cannot be a reason to keep a row: the lead of
    a single that only surfaced through a guest's discography is the same
    ghost as the guest."""
    host = _insert_native_artist(imported_conn, "Featured Host")
    lead = _insert_native_artist(imported_conn, "Single Lead")
    album_id = imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, monitored) "
        "VALUES(?, 'Their Single (feat. Featured Host)', 'discography', 0)",
        (host,),
    ).lastrowid
    track_id = imported_conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, monitored) VALUES(?, 'Their Single', 0)",
        (album_id,),
    ).lastrowid
    imported_conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,'featured')",
        (album_id, lead))
    imported_conn.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?,'primary',0)", (track_id, lead))

    stats = NE.prune_browse_only_artists(imported_conn)

    assert lead in stats["artist_ids"]
