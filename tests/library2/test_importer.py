"""Importer: credit splitting, multi-artist, single-vs-album, idempotency."""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.importer import (
    featured_from_title,
    import_legacy_library,
    split_artist_credits,
)
from core.library2 import queries as Q


# --- credit splitter ---------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Drake feat. Rihanna", ["Drake", "Rihanna"]),
    ("A ft. B", ["A", "B"]),
    ("A featuring B", ["A", "B"]),
    ("Odetari w/ 9lives", ["Odetari", "9lives"]),
    ("A, B & C", ["A", "B", "C"]),
    ("Calvin Harris x Dua Lipa", ["Calvin Harris", "Dua Lipa"]),
    ("Drake feat. Wizkid & Kyla", ["Drake", "Wizkid", "Kyla"]),
    ("", []),
])
def test_split_artist_credits(raw, expected):
    assert split_artist_credits(raw) == expected


def test_split_dedupes_case_insensitive():
    assert split_artist_credits("Drake, drake & DRAKE") == ["Drake"]


def test_featured_from_title():
    assert featured_from_title("One Dance (feat. Wizkid & Kyla)") == ["Wizkid", "Kyla"]
    assert featured_from_title("I LOVE YOU HOE (w/ Trippie Redd & 9lives)") == [
        "Trippie Redd", "9lives",
    ]
    assert featured_from_title("Plain Title") == []


# --- full import -------------------------------------------------------------

def _q(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def test_import_counts(legacy_db):
    stats = import_legacy_library(legacy_db)
    assert stats["artists"] == 1          # only legacy artists (Drake)
    assert stats["albums"] == 2
    assert stats["tracks"] == 3
    assert stats["files"] == 2            # track 101 has no file_path
    assert stats["linked_duplicates"] == 1


def test_import_reports_start_and_completion_for_every_row_stage(legacy_db):
    events = []

    import_legacy_library(
        legacy_db,
        progress=lambda stage, current, total: events.append((stage, current, total)),
    )

    for stage, total in (("artists", 1), ("albums", 2), ("tracks", 3)):
        stage_events = [event for event in events if event[0] == stage]
        assert stage_events[0] == (stage, 0, total)
        assert stage_events[-1] == (stage, total, total)


def test_import_preloads_row_lookup_maps_instead_of_n_plus_one_selects(legacy_db):
    """The large-library contract: entity writes remain ordered, but lookup
    SELECTs must not scale once per legacy album/track/wishlist row."""
    conn = sqlite3.connect(legacy_db.path)
    for index in range(20, 50):
        conn.execute(
            "INSERT INTO albums VALUES(?,?,?,2024,NULL,NULL,1,NULL)",
            (index, 1, f"Scale Album {index}"),
        )
        conn.execute(
            "INSERT INTO tracks VALUES(?,?,1,?,1,180000,?,1000,5000,NULL)",
            (index + 1000, index, f"Scale Track {index}", f"/m/{index}.flac"),
        )
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for index in range(30):
        payload = {
            "id": f"wishlist-track-{index}",
            "name": f"Wishlist Track {index}",
            "artists": [{"id": "wishlist-artist", "name": "Wishlist Artist"}],
            "album": {
                "id": f"wishlist-album-{index}",
                "name": f"Wishlist Album {index}",
                "album_type": "single",
                "total_tracks": 1,
                "artists": [{"id": "wishlist-artist", "name": "Wishlist Artist"}],
            },
        }
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data) VALUES(?,?)",
            (payload["id"], json.dumps(payload)),
        )
    conn.commit()
    conn.close()

    statements = []
    original_get_connection = legacy_db._get_connection

    def traced_connection():
        traced = original_get_connection()
        traced.set_trace_callback(statements.append)
        return traced

    legacy_db._get_connection = traced_connection
    stats = import_legacy_library(legacy_db)
    normalized = [" ".join(statement.upper().split()) for statement in statements]

    assert stats["albums"] == 32
    assert stats["tracks"] == 33
    assert stats["wishlist_tracks"] == 30
    banned_per_row_reads = (
        "SELECT COUNT(*) AS C FROM TRACKS WHERE ALBUM_ID=",
        "SELECT ID FROM LIB2_TRACK_FILES WHERE TRACK_ID=",  # paired with AND PATH below
        "SELECT ID FROM LIB2_ALBUMS WHERE SPOTIFY_ID=",
        "SELECT ID FROM LIB2_TRACKS WHERE ALBUM_ID=",
    )
    for banned in banned_per_row_reads:
        matches = [statement for statement in normalized if banned in statement]
        if "LIB2_TRACK_FILES" in banned:
            matches = [statement for statement in matches if "AND PATH=" in statement]
        assert not matches, banned


def test_album_type_detection(imported_conn):
    rows = {r["title"]: r["album_type"] for r in
            _q(imported_conn, "SELECT title, album_type FROM lib2_albums")}
    assert rows["Views"] == "album"
    assert rows["One Dance"] == "single"   # single-track legacy album


def test_partial_album_is_not_blanket_monitored_on_import(legacy_db):
    """§16.2: an album whose known tracks aren't all present must NOT default to
    monitored=1 — otherwise every un-owned track of a partially-downloaded album
    is projected wanted and auto-grabbed, even though the user only wanted some.

    'Views' (track_count=2) has only one track with a file ('One Dance'); its
    other known track ('Hotline Bling') has none → partial → unmonitored. The
    'One Dance' single is fully present → stays monitored (owned → upgradeable).
    """
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    try:
        monitored = {
            r["title"]: r["monitored"] for r in conn.execute(
                "SELECT title, monitored FROM lib2_albums")
        }
        assert monitored["Views"] == 0
        assert monitored["One Dance"] == 1

        # The un-owned, un-wishlisted track of the partial album is NOT wanted.
        hotline_id = conn.execute(
            "SELECT id FROM lib2_tracks WHERE legacy_track_id=101"
        ).fetchone()[0]
        wanted = conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
            (hotline_id,),
        ).fetchone()
        assert wanted is not None and wanted["wanted"] == 0

        # Concrete file ownership is track-level monitoring even when the
        # parent release is incomplete.  Only the absent, non-Wishlist sibling
        # stays unmonitored.
        def _monitored(legacy_track_id: int) -> int:
            return conn.execute(
                "SELECT monitored FROM lib2_tracks WHERE legacy_track_id=?",
                (legacy_track_id,),
            ).fetchone()["monitored"]

        assert _monitored(101) == 0  # Hotline Bling, Views (partial album)
        assert _monitored(100) == 1  # One Dance, Views (file present)
        assert _monitored(102) == 1  # One Dance, single (fully-owned album)
        present_wanted = conn.execute(
            "SELECT wanted, reason FROM lib2_wanted_tracks WHERE track_id=("
            "SELECT id FROM lib2_tracks WHERE legacy_track_id=100) AND profile_id=1"
        ).fetchone()
        assert (present_wanted["wanted"], present_wanted["reason"]) == (
            1, "track_rule:file_import"
        )
    finally:
        conn.close()


def _fresh_resolver(legacy_db):
    from core.library2.schema import ensure_library_v2_schema
    from core.library2.importer import _ArtistResolver
    from core.library2.profile_lookup import default_quality_profile_id
    conn = legacy_db._get_connection()
    ensure_library_v2_schema(conn)
    resolver = _ArtistResolver(conn.cursor(), default_quality_profile_id(conn))
    resolver.seed_existing()
    return conn, resolver


def test_artist_resolver_disambiguates_same_name_by_provider_id(legacy_db):
    """§16.3(b): two different artists that share a name must NOT collapse into
    one lib2 entity when their provider ids differ — otherwise an album gets
    hung on the wrong artist and its real tracklist can never be fetched."""
    conn, resolver = _fresh_resolver(legacy_db)
    try:
        a1 = resolver.get_or_create_by_name("Nova", spotify_id="sp_1")
        a2 = resolver.get_or_create_by_name("Nova", spotify_id="sp_2")
        a1_again = resolver.get_or_create_by_name("Nova", spotify_id="sp_1")

        assert a1 != a2          # different spotify id → distinct artists
        assert a1 == a1_again    # same spotify id → reused despite name clash
        # Provider id beats the name key: a different display name but the same
        # id resolves back to the id's artist.
        assert resolver.get_or_create_by_name("Nova (Deluxe)", spotify_id="sp_1") == a1
        # MusicBrainz id disambiguates the same way.
        m1 = resolver.get_or_create_by_name("Orion", musicbrainz_id="mb_a")
        m2 = resolver.get_or_create_by_name("Orion", musicbrainz_id="mb_b")
        assert m1 != m2
    finally:
        conn.close()


def test_artist_resolver_name_only_and_id_adoption(legacy_db):
    """Backwards compat: a name-only lookup still reuses by name; adding an id
    to a same-named artist that had none is adoption, not a conflict."""
    conn, resolver = _fresh_resolver(legacy_db)
    try:
        x1 = resolver.get_or_create_by_name("Solo")
        x2 = resolver.get_or_create_by_name("Solo")
        assert x1 == x2

        y1 = resolver.get_or_create_by_name("Adopt")                       # no id
        y2 = resolver.get_or_create_by_name("Adopt", spotify_id="sp_adopt")  # adopt id
        assert y1 == y2
        # The adopted id is now a resolution key.
        assert resolver.get_or_create_by_name("Adopt", spotify_id="sp_adopt") == y1
    finally:
        conn.close()


def test_artist_resolver_matches_across_providers_via_external_ids(legacy_db):
    """§16.3(b), provider-neutral: identity keys on ANY provider id (Deezer is
    the DEFAULT source), not Spotify. The same Deezer id resolves to the same
    artist even under a different display name; a different id for the same name
    is distinct; the source→id map persists in external_ids."""
    import json
    conn, resolver = _fresh_resolver(legacy_db)
    try:
        a = resolver.get_or_create_by_name("Nova", provider_ids={"deezer": "dz1"})
        # Same Deezer id, different display name → same artist (id beats name).
        assert resolver.get_or_create_by_name(
            "Nova (Live)", provider_ids={"deezer": "dz1"}) == a
        # Different Deezer id, same name → distinct artist.
        b = resolver.get_or_create_by_name("Nova", provider_ids={"deezer": "dz2"})
        assert a != b
        # Cross-source: an artist known by Deezer id, later referenced by a
        # MusicBrainz id, is adopted (not duplicated) when the name also matches.
        ext = json.loads(
            conn.execute("SELECT external_ids FROM lib2_artists WHERE id=?", (a,))
            .fetchone()["external_ids"] or "{}")
        assert ext.get("deezer") == "dz1"
    finally:
        conn.close()


def test_import_captures_all_provider_ids_into_external_ids(legacy_db):
    """§16.3(b) / import-parity: the main legacy import must capture EVERY
    provider id the legacy row has (Deezer + MusicBrainz + Spotify), not only
    Spotify — otherwise a Deezer-primary user loses all provider identity in
    lib2 and disambiguation / tracklist fetches can't use it."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE artists ADD COLUMN deezer_artist_id TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN musicbrainz_artist_id TEXT")
    conn.execute(
        "UPDATE artists SET deezer_artist_id='dz_drake', "
        "musicbrainz_artist_id='mb_drake' WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists "
            "WHERE name='Drake'").fetchone()
        ext = json.loads(row["external_ids"] or "{}")
        assert ext.get("deezer") == "dz_drake"
        assert ext.get("spotify") == "sp1"          # conftest spotify_artist_id
        assert ext.get("musicbrainz") == "mb_drake"
        assert row["musicbrainz_id"] == "mb_drake"  # well-known column also filled
    finally:
        conn.close()


def test_import_captures_album_provider_ids_into_external_ids(legacy_db):
    """Import-parity for albums: capture Deezer/Spotify/MusicBrainz album ids
    into external_ids so completeness.resolve_tracklist can fetch the EXACT
    provider release (Deezer users, §16.3(b)) instead of only a name search."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN deezer_album_id TEXT")
    conn.execute("ALTER TABLE albums ADD COLUMN spotify_album_id TEXT")
    conn.execute(
        "UPDATE albums SET deezer_album_id='dz_views', spotify_album_id='sp_views' "
        "WHERE id=10")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        ext = json.loads(
            conn.execute("SELECT external_ids FROM lib2_albums WHERE title='Views'")
            .fetchone()["external_ids"] or "{}")
        assert ext.get("deezer") == "dz_views"
        assert ext.get("spotify") == "sp_views"
    finally:
        conn.close()


def test_import_captures_album_long_tail_provider_ids_into_external_ids(legacy_db):
    """§17.7 step 3: albums must also capture the long-tail providers beyond
    Spotify/Deezer/MusicBrainz/Tidal/Qobuz — iTunes/AudioDB/Discogs/Amazon/
    JioSaavn/Bandcamp ids existed on the legacy row but had no importer path."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    for col in ("itunes_album_id", "audiodb_id", "discogs_id", "amazon_id",
                "jiosaavn_id", "bandcamp_url"):
        conn.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
    conn.execute(
        "UPDATE albums SET itunes_album_id='it_views', audiodb_id='adb_views', "
        "discogs_id='dg_views', amazon_id='am_views', jiosaavn_id='js_views', "
        "bandcamp_url='https://drake.bandcamp.com/album/views' WHERE id=10")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        ext = json.loads(
            conn.execute("SELECT external_ids FROM lib2_albums WHERE title='Views'")
            .fetchone()["external_ids"] or "{}")
        assert ext.get("itunes") == "it_views"
        assert ext.get("audiodb") == "adb_views"
        assert ext.get("discogs") == "dg_views"
        assert ext.get("amazon") == "am_views"
        assert ext.get("jiosaavn") == "js_views"
        assert ext.get("bandcamp") == "https://drake.bandcamp.com/album/views"
    finally:
        conn.close()


def test_import_captures_track_provider_ids_into_external_ids(legacy_db):
    """§17.7 step 1: ``lib2_tracks`` had no ``external_ids`` column at all, so
    every provider id beyond isrc/musicbrainz/spotify (which keep dedicated
    columns) was silently dropped on import."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    for col in ("deezer_id", "tidal_id", "qobuz_id", "itunes_track_id",
                "audiodb_id", "genius_id", "amazon_id", "jiosaavn_id",
                "bandcamp_url", "lastfm_url"):
        conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} TEXT")
    conn.execute(
        "UPDATE tracks SET deezer_id='dz_t100', tidal_id='td_t100', "
        "qobuz_id='qb_t100', itunes_track_id='it_t100', audiodb_id='adb_t100', "
        "genius_id='gn_t100', amazon_id='am_t100', jiosaavn_id='js_t100', "
        "bandcamp_url='https://x.bandcamp.com/track/one-dance', "
        "lastfm_url='https://last.fm/one-dance' WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        ext = json.loads(
            conn.execute(
                "SELECT external_ids FROM lib2_tracks WHERE title='One Dance' "
                "AND legacy_track_id=100"
            ).fetchone()["external_ids"] or "{}")
        assert ext.get("deezer") == "dz_t100"
        assert ext.get("tidal") == "td_t100"
        assert ext.get("qobuz") == "qb_t100"
        assert ext.get("itunes") == "it_t100"
        assert ext.get("audiodb") == "adb_t100"
        assert ext.get("genius") == "gn_t100"
        assert ext.get("amazon") == "am_t100"
        assert ext.get("jiosaavn") == "js_t100"
        assert ext.get("bandcamp") == "https://x.bandcamp.com/track/one-dance"
        assert ext.get("lastfm") == "https://last.fm/one-dance"
    finally:
        conn.close()


def test_import_captures_track_bpm_and_explicit(legacy_db):
    """§17.7 step 2: ``bpm``/``explicit`` exist on the legacy ``tracks`` row but
    had no lib2 destination column, so they were permanently lost on import."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
    conn.execute("ALTER TABLE tracks ADD COLUMN explicit INTEGER")
    conn.execute("UPDATE tracks SET bpm=104.5, explicit=1 WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT bpm, explicit FROM lib2_tracks WHERE title='One Dance' "
            "AND legacy_track_id=100"
        ).fetchone()
        assert row["bpm"] == 104.5
        assert row["explicit"] == 1
    finally:
        conn.close()


def test_import_captures_track_style_mood(legacy_db):
    """§48: ``style``/``mood`` exist on the legacy ``tracks`` row (like
    bpm/explicit already handled) but had no lib2 destination column."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE tracks ADD COLUMN style TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN mood TEXT")
    conn.execute("UPDATE tracks SET style='Pop Rap', mood='Chill' WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT style, mood FROM lib2_tracks WHERE title='One Dance' "
            "AND legacy_track_id=100"
        ).fetchone()
        assert row["style"] == "Pop Rap"
        assert row["mood"] == "Chill"
    finally:
        conn.close()


def test_import_captures_album_explicit_label_upc(legacy_db):
    """§17.7 step 2: ``explicit``/``label``/``upc`` (barcode) exist on the
    legacy ``albums`` row but had no lib2 destination column."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN explicit INTEGER")
    conn.execute("ALTER TABLE albums ADD COLUMN label TEXT")
    conn.execute("ALTER TABLE albums ADD COLUMN upc TEXT")
    conn.execute(
        "UPDATE albums SET explicit=1, label='OVO Sound', upc='00602557546317' "
        "WHERE id=10")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT explicit, label, upc FROM lib2_albums WHERE title='Views'"
        ).fetchone()
        assert row["explicit"] == 1
        assert row["label"] == "OVO Sound"
        assert row["upc"] == "00602557546317"
    finally:
        conn.close()


def test_import_captures_album_style_mood(legacy_db):
    """§48: ``style``/``mood`` exist on the legacy ``albums`` row (like the
    artist row already handled) but had no lib2 destination column."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN style TEXT")
    conn.execute("ALTER TABLE albums ADD COLUMN mood TEXT")
    conn.execute("UPDATE albums SET style='Hip Hop', mood='Moody' WHERE id=10")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT style, mood FROM lib2_albums WHERE title='Views'"
        ).fetchone()
        assert row["style"] == "Hip Hop"
        assert row["mood"] == "Moody"
    finally:
        conn.close()


def test_import_captures_artist_style_mood_label_aliases_banner(legacy_db):
    """§17.7 remainder: AudioDB-sourced style/mood/label/banner_url and the
    MusicBrainz aliases list existed on the legacy artist row but had no lib2
    destination column."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE artists ADD COLUMN style TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN mood TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN label TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN aliases TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN banner_url TEXT")
    conn.execute(
        "UPDATE artists SET style='Hip Hop', mood='Energetic', label='OVO Sound', "
        "aliases='[\"Drizzy\", \"Champagne Papi\"]', "
        "banner_url='http://img/banner.jpg' WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT style, mood, label, aliases, banner_url FROM lib2_artists "
            "WHERE name='Drake'"
        ).fetchone()
        assert row["style"] == "Hip Hop"
        assert row["mood"] == "Energetic"
        assert row["label"] == "OVO Sound"
        assert json.loads(row["aliases"]) == ["Drizzy", "Champagne Papi"]
        assert row["banner_url"] == "http://img/banner.jpg"
    finally:
        conn.close()


def test_import_reimport_keeps_artist_flat_fields_when_legacy_column_missing(legacy_db):
    """A re-import from a legacy DB copy that lacks the style/mood/label/
    banner_url migration columns entirely must not null out values a prior
    import already captured — same COALESCE-on-UPDATE contract as bpm/explicit."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE artists ADD COLUMN style TEXT")
    conn.execute("UPDATE artists SET style='Hip Hop' WHERE id=1")
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db, reset=True)

    # Second import run against the SAME db (style column still present, but
    # simulate a legacy row where the field went blank/unset again).
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("UPDATE artists SET style=NULL WHERE id=1")
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db, reset=False)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute("SELECT style FROM lib2_artists WHERE name='Drake'").fetchone()
        assert row["style"] == "Hip Hop"
    finally:
        conn.close()


def test_import_captures_artist_lastfm_genius_discogs_enrichment(legacy_db):
    """§17.7 remainder: Last.fm/Genius/Discogs bio/listeners/similar/tags
    exist on the legacy artist row but had no lib2 destination at all."""
    conn = sqlite3.connect(legacy_db.path)
    for col in ("lastfm_bio", "lastfm_tags", "lastfm_similar",
                "lastfm_url", "genius_description", "genius_alt_names", "genius_url",
                "discogs_bio", "discogs_members", "discogs_urls"):
        conn.execute(f"ALTER TABLE artists ADD COLUMN {col} TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN lastfm_listeners INTEGER")
    conn.execute(
        "UPDATE artists SET lastfm_bio='A rapper from Toronto.', "
        "lastfm_listeners=5000000, lastfm_tags='[\"rap\", \"canadian\"]', "
        "lastfm_similar='[\"Future\", \"21 Savage\"]', "
        "lastfm_url='https://last.fm/drake', "
        "genius_description='Aubrey Drake Graham.', "
        "genius_alt_names='[\"Drizzy\"]', genius_url='https://genius.com/drake', "
        "discogs_bio='Canadian rapper.', discogs_members='[\"Drake\"]', "
        "discogs_urls='[\"https://discogs.com/artist/drake\"]' WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        enrichment = json.loads(
            conn.execute(
                "SELECT enrichment FROM lib2_artists WHERE name='Drake'"
            ).fetchone()["enrichment"])
        assert enrichment["lastfm"]["bio"] == "A rapper from Toronto."
        assert enrichment["lastfm"]["listeners"] == 5000000
        assert enrichment["lastfm"]["tags"] == ["rap", "canadian"]
        assert enrichment["lastfm"]["similar"] == ["Future", "21 Savage"]
        assert enrichment["lastfm"]["url"] == "https://last.fm/drake"
        assert enrichment["genius"]["description"] == "Aubrey Drake Graham."
        assert enrichment["genius"]["alt_names"] == ["Drizzy"]
        assert enrichment["discogs"]["bio"] == "Canadian rapper."
        assert enrichment["discogs"]["members"] == ["Drake"]
    finally:
        conn.close()


def test_import_enrichment_merge_never_overwrites_richer_existing_data(legacy_db):
    """Re-importing from a source with a thinner (or missing) bio must not
    erase a bio a previous import already captured — same never-overwrite
    contract as ``_merge_external_ids``."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE artists ADD COLUMN lastfm_bio TEXT")
    conn.execute("UPDATE artists SET lastfm_bio='A rapper from Toronto.' WHERE id=1")
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.execute("UPDATE artists SET lastfm_bio='' WHERE id=1")
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db, reset=False)

    conn = legacy_db._get_connection()
    try:
        enrichment = json.loads(
            conn.execute(
                "SELECT enrichment FROM lib2_artists WHERE name='Drake'"
            ).fetchone()["enrichment"])
        assert enrichment["lastfm"]["bio"] == "A rapper from Toronto."
    finally:
        conn.close()


def test_import_captures_track_genius_lyrics_copyright_play_count_last_played(legacy_db):
    """§17.7 remainder: genius_lyrics/copyright/play_count/last_played exist
    on the legacy tracks row but had no lib2 destination column."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE tracks ADD COLUMN genius_lyrics TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN copyright TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN play_count INTEGER")
    conn.execute("ALTER TABLE tracks ADD COLUMN last_played TIMESTAMP")
    conn.execute(
        "UPDATE tracks SET genius_lyrics=?, "
        "copyright='(C) 2016 OVO Sound', play_count=42, "
        "last_played='2026-07-01 12:00:00' WHERE id=100",
        ("[Verse 1]\nlyrics here",))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT genius_lyrics, copyright, play_count, last_played "
            "FROM lib2_tracks WHERE title='One Dance' AND legacy_track_id=100"
        ).fetchone()
        assert row["genius_lyrics"] == "[Verse 1]\nlyrics here"
        assert row["copyright"] == "(C) 2016 OVO Sound"
        assert row["play_count"] == 42
        assert row["last_played"] == "2026-07-01 12:00:00"
    finally:
        conn.close()


def test_import_track_play_count_defaults_to_zero_without_legacy_column(legacy_db):
    """A legacy DB predating the play_count migration must not crash the
    track INSERT — the NOT NULL DEFAULT 0 column needs an explicit 0, not a
    NULL (the schema default only applies when the column is omitted)."""
    stats = import_legacy_library(legacy_db, reset=True)
    assert stats["tracks"] > 0

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT play_count FROM lib2_tracks WHERE title='One Dance' "
            "AND legacy_track_id=100"
        ).fetchone()
        assert row["play_count"] == 0
    finally:
        conn.close()


def test_import_new_track_uses_legacy_quality_profile_id_when_valid(legacy_db):
    """§17.7 remainder: a legacy track's own quality_profile_id was never
    read; new tracks always got the run-wide default profile instead."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "CREATE TABLE quality_profiles(id INTEGER PRIMARY KEY, is_default INTEGER)")
    conn.execute("INSERT INTO quality_profiles VALUES(1, 1)")
    conn.execute("INSERT INTO quality_profiles VALUES(7, 0)")
    conn.execute("ALTER TABLE tracks ADD COLUMN quality_profile_id INTEGER")
    conn.execute("UPDATE tracks SET quality_profile_id=7 WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT quality_profile_id FROM lib2_tracks WHERE title='One Dance' "
            "AND legacy_track_id=100"
        ).fetchone()
        assert row["quality_profile_id"] == 7
        # A sibling track with no legacy profile still gets the default.
        other = conn.execute(
            "SELECT quality_profile_id FROM lib2_tracks WHERE legacy_track_id=101"
        ).fetchone()
        assert other["quality_profile_id"] == 1
    finally:
        conn.close()


def test_import_new_track_falls_back_to_default_for_dangling_legacy_profile_id(legacy_db):
    """A legacy quality_profile_id pointing at a profile that no longer
    exists (deleted since) must not be copied verbatim — falls back to the
    run-wide default instead of leaving a dangling reference."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "CREATE TABLE quality_profiles(id INTEGER PRIMARY KEY, is_default INTEGER)")
    conn.execute("INSERT INTO quality_profiles VALUES(1, 1)")
    conn.execute("ALTER TABLE tracks ADD COLUMN quality_profile_id INTEGER")
    conn.execute("UPDATE tracks SET quality_profile_id=999 WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        row = conn.execute(
            "SELECT quality_profile_id FROM lib2_tracks WHERE title='One Dance' "
            "AND legacy_track_id=100"
        ).fetchone()
        assert row["quality_profile_id"] == 1
    finally:
        conn.close()


def test_import_captures_real_schema_artist_provider_ids(legacy_db):
    """#38 root cause: the REAL legacy schema names the artist provider ids
    ``deezer_id``/``tidal_id``/``qobuz_id`` — NOT ``deezer_artist_id`` etc. The
    importer must read the real columns into external_ids, otherwise a
    Deezer-primary artist (Deezer is SoulSync's default source) lands with
    external_ids='{}' and ``expand_artist_discography`` has no provider id to
    fetch the catalog with → "Update Discography finds only singles"."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    for col in ("deezer_id", "tidal_id", "qobuz_id"):
        conn.execute(f"ALTER TABLE artists ADD COLUMN {col} TEXT")
    conn.execute(
        "UPDATE artists SET deezer_id='259', tidal_id='tid1', qobuz_id='qob1' WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        ext = json.loads(conn.execute(
            "SELECT external_ids FROM lib2_artists WHERE name='Drake'"
        ).fetchone()["external_ids"] or "{}")
        assert ext.get("deezer") == "259"
        assert ext.get("tidal") == "tid1"
        assert ext.get("qobuz") == "qob1"
    finally:
        conn.close()


def test_reimport_matches_artist_with_text_legacy_id_no_duplicate(tmp_path):
    """#38/#40: an artist whose legacy ``artists.id`` is TEXT (soulsync/Deezer-
    generated, e.g. '476516869' — the common case for Deezer-primary libraries)
    must MATCH its existing lib2 row on re-import. ``_by_legacy`` was keyed by the
    INTEGER ``legacy_artist_id`` on re-seed but looked up with the TEXT legacy id
    → str-vs-int miss → a duplicate artist row every re-import, the original
    orphaned (legacy link nulled, external_ids left empty). Coercing the key to
    str fixes it. Directly compounds #38: the orphaned original keeps
    external_ids='{}' so the discography sync never gets a provider id."""
    import sqlite3, json
    path = str(tmp_path / "legacy_text_id.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, thumb_url TEXT,
            genres TEXT, summary TEXT, spotify_artist_id TEXT, musicbrainz_id TEXT,
            deezer_id TEXT);
        CREATE TABLE albums(id INTEGER PRIMARY KEY, artist_id TEXT, title TEXT,
            year INTEGER, thumb_url TEXT, genres TEXT, track_count INTEGER,
            release_date TEXT);
        CREATE TABLE tracks(id INTEGER PRIMARY KEY, album_id INTEGER, artist_id TEXT,
            title TEXT, track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT);
    """)
    conn.execute("INSERT INTO artists VALUES('476516869','Michael Jackson',NULL,NULL,"
                 "NULL,NULL,'f27ec8db','259')")
    conn.execute("INSERT INTO albums VALUES(10,'476516869','Thriller',1982,NULL,NULL,9,NULL)")
    conn.execute("INSERT INTO tracks VALUES(100,10,'476516869','Beat It',1,258000,"
                 "'/m/beatit.flac',1000,5000,NULL)")
    conn.commit()
    conn.close()

    class _Shim:
        def __init__(self, p): self.path = p
        def _get_connection(self):
            c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON"); return c
    db = _Shim(path)

    import_legacy_library(db)   # first import creates the lib2 artist row
    import_legacy_library(db)   # re-import must MATCH the existing row, not duplicate

    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, legacy_artist_id, external_ids FROM lib2_artists "
        "WHERE name='Michael Jackson'").fetchall()
    conn.close()

    assert len(rows) == 1, f"re-import duplicated the artist ({len(rows)} rows)"
    assert rows[0]["legacy_artist_id"] is not None, "original row was orphaned"
    ext = json.loads(rows[0]["external_ids"] or "{}")
    assert ext.get("deezer") == "259", f"external_ids not populated: {ext}"


def test_reimport_keeps_stable_album_and_track_ids_with_text_legacy_ids(tmp_path):
    """#38/#40 (albums/EPs/singles): the same TEXT-vs-INTEGER legacy-id mismatch
    that duplicated artists also duplicated albums and tracks. ``album_map`` and
    ``track_map`` were seeded by the INTEGER ``legacy_*_id`` but looked up with the
    TEXT legacy id, so a re-import NEVER matched an existing album/track — it
    re-inserted a fresh row (its id churns) and ``_reconcile_legacy_snapshot`` then
    detached the orphaned original into an ``origin='discography'`` twin (visible
    "Thriller 40 twice under Michael Jackson") when it had a provider identity, or
    deleted it otherwise. The invariant the importer promises is idempotency: a
    re-import must reconcile the SAME row, so its lib2 id stays stable and no twin
    appears. The album carries a provider id (deezer) to exercise the detach path."""
    import sqlite3
    path = str(tmp_path / "legacy_text_ids.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, thumb_url TEXT,
            genres TEXT, summary TEXT, spotify_artist_id TEXT, musicbrainz_id TEXT,
            deezer_id TEXT);
        CREATE TABLE albums(id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
            year INTEGER, thumb_url TEXT, genres TEXT, track_count INTEGER,
            release_date TEXT, deezer_id TEXT);
        CREATE TABLE tracks(id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT,
            title TEXT, track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT);
    """)
    conn.execute("INSERT INTO artists VALUES('476516869','Michael Jackson',NULL,NULL,"
                 "NULL,NULL,'f27ec8db','259')")
    conn.execute("INSERT INTO albums VALUES('630009860','476516869','Thriller 40',2022,"
                 "NULL,NULL,1,NULL,'375513297')")
    conn.execute("INSERT INTO tracks VALUES('700','630009860','476516869','Thriller',1,"
                 "357000,'/m/thriller.flac',1000,5000,NULL)")
    conn.commit()
    conn.close()

    class _Shim:
        def __init__(self, p): self.path = p
        def _get_connection(self):
            c = sqlite3.connect(self.path); c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON"); return c
    db = _Shim(path)

    import_legacy_library(db)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Thriller 40'").fetchone()["id"]
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='Thriller'").fetchone()["id"]
    conn.close()

    import_legacy_library(db)   # re-import must reconcile the SAME rows, not re-create

    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    album_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Thriller 40'")]
    track_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='Thriller'")]
    # no discography twin was spawned by the detach path
    twins = conn.execute(
        "SELECT COUNT(*) FROM lib2_albums WHERE title='Thriller 40' "
        "AND origin='discography'").fetchone()[0]
    conn.close()

    assert album_ids == [album_id], f"album id churned/duplicated on re-import: {album_ids} != [{album_id}]"
    assert track_ids == [track_id], f"track id churned/duplicated on re-import: {track_ids} != [{track_id}]"
    assert twins == 0, "re-import spawned an origin='discography' twin album"


def test_import_accepts_base62_legacy_album_ids(tmp_path):
    """Legacy media-server IDs are TEXT and may be Spotify-shaped base62.

    The ownership counters used to coerce ``tracks.album_id`` to ``int`` even
    though the rest of the importer deliberately normalizes legacy IDs with
    ``_legacy_key``.  A real Spotify-shaped album primary key therefore aborted
    the import before the first album could be materialized.
    """
    import sqlite3

    album_legacy_id = "01MoTj8w4VkVtgdPOijUUE"
    path = str(tmp_path / "legacy_base62_album_id.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists(id TEXT PRIMARY KEY, name TEXT, thumb_url TEXT,
            genres TEXT, summary TEXT, spotify_artist_id TEXT, musicbrainz_id TEXT);
        CREATE TABLE albums(id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
            year INTEGER, thumb_url TEXT, genres TEXT, track_count INTEGER,
            release_date TEXT);
        CREATE TABLE tracks(id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT,
            title TEXT, track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT);
    """)
    conn.execute(
        "INSERT INTO artists VALUES(?, 'Base62 Artist', NULL, NULL, NULL, NULL, NULL)",
        ("artist-provider-key",),
    )
    conn.execute(
        "INSERT INTO albums VALUES(?, ?, 'Base62 Album', 2026, NULL, NULL, 1, NULL)",
        (album_legacy_id, "artist-provider-key"),
    )
    conn.execute(
        "INSERT INTO tracks VALUES(?, ?, ?, 'Base62 Track', 1, 180000, "
        "'/m/base62.flac', 1000, 5000, NULL)",
        ("track-provider-key", album_legacy_id, "artist-provider-key"),
    )
    conn.commit()
    conn.close()

    class _Shim:
        def _get_connection(self):
            db_conn = sqlite3.connect(path)
            db_conn.row_factory = sqlite3.Row
            db_conn.execute("PRAGMA foreign_keys=ON")
            return db_conn

    db = _Shim()
    stats = import_legacy_library(db)

    assert stats["albums"] == 1
    assert stats["tracks"] == 1
    assert stats["files"] == 1
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    imported = conn.execute(
        """SELECT al.id, al.legacy_album_id, al.spotify_id, al.external_ids,
                  al.monitored, t.legacy_track_id, f.path
             FROM lib2_albums al
             JOIN lib2_tracks t ON t.album_id=al.id
             JOIN lib2_track_files f ON f.track_id=t.id
            WHERE al.title='Base62 Album'"""
    ).fetchone()
    conn.close()

    assert imported is not None
    first_lib2_album_id = imported["id"]
    assert imported["legacy_album_id"] == album_legacy_id
    # A provider-shaped legacy PK is still only a legacy relationship.  It must
    # not leak into §63's provider namespaces without an explicit legacy
    # spotify_album_id/source marker.
    assert imported["spotify_id"] is None
    assert imported["external_ids"] == "{}"
    assert imported["legacy_track_id"] == "track-provider-key"
    assert imported["monitored"] == 1
    assert imported["path"] == "/m/base62.flac"

    # The §38/§40 idempotency contract applies to alphanumeric ids too: a
    # re-import reconciles the same release instead of creating a discography
    # twin for the §63 duplicate repair to clean up later.
    import_legacy_library(db)
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT id FROM lib2_albums WHERE legacy_album_id=?", (album_legacy_id,)
    ).fetchall()
    conn.close()
    assert rows == [(first_lib2_album_id,)]


def test_import_captures_real_schema_album_provider_ids(legacy_db):
    """Album counterpart of the artist fix: real legacy albums carry
    ``deezer_id``/``tidal_id``/``qobuz_id`` (not ``*_album_id``)."""
    import json
    conn = sqlite3.connect(legacy_db.path)
    for col in ("deezer_id", "tidal_id", "qobuz_id"):
        conn.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
    conn.execute(
        "UPDATE albums SET deezer_id='dz10', tidal_id='td10', qobuz_id='qb10' WHERE id=10")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        ext = json.loads(conn.execute(
            "SELECT external_ids FROM lib2_albums WHERE title='Views'"
        ).fetchone()["external_ids"] or "{}")
        assert ext.get("deezer") == "dz10"
        assert ext.get("tidal") == "td10"
        assert ext.get("qobuz") == "qb10"
    finally:
        conn.close()


def test_import_prefers_explicit_album_type_over_one_track_heuristic(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE albums ADD COLUMN album_type TEXT")
    conn.execute("UPDATE albums SET album_type='album' WHERE id=11")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    row = conn.execute(
        "SELECT album_type FROM lib2_albums WHERE legacy_album_id=11"
    ).fetchone()
    conn.close()
    assert row[0] == "album"


def test_multi_artist_split(imported_conn):
    # Track 100 ("One Dance" on Views) credits Drake + featured Wizkid.
    names = [r["name"] for r in _q(
        imported_conn,
        """SELECT ar.name FROM lib2_track_artists ta
           JOIN lib2_artists ar ON ar.id = ta.artist_id
           JOIN lib2_tracks t ON t.id = ta.track_id
           WHERE t.legacy_track_id = 100 ORDER BY ta.position""",
    )]
    assert names == ["Drake", "Wizkid"]
    # Wizkid was created as a new artist (not a legacy mirror row).
    wiz = _q(imported_conn, "SELECT legacy_artist_id FROM lib2_artists WHERE name='Wizkid'")
    assert wiz and wiz[0]["legacy_artist_id"] is None
    appearance = imported_conn.execute(
        """SELECT aa.role FROM lib2_album_artists aa
             JOIN lib2_artists ar ON ar.id=aa.artist_id
             JOIN lib2_albums al ON al.id=aa.album_id
            WHERE ar.name='Wizkid' AND al.title='Views'"""
    ).fetchone()
    assert appearance is not None and appearance["role"] == "featured"


def test_single_album_linkage(imported_conn):
    # The single's track points its canonical_track_id at the album track.
    single = _q(imported_conn, "SELECT id, canonical_track_id FROM lib2_tracks WHERE legacy_track_id = 102")[0]
    album_track = _q(imported_conn, "SELECT id FROM lib2_tracks WHERE legacy_track_id = 100")[0]
    assert single["canonical_track_id"] == album_track["id"]


def test_single_album_linkage_survives_feat_suffix_on_album_cut(legacy_db):
    """#39: a genuine single↔album duplicate must still link when the album cut
    spells out the guests in its title (``(feat. …)``) and the single does not.
    Otherwise ``link_single_album_duplicates`` groups them apart and the Manage
    Tracks modal wrongly reports "No duplicates found"."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "UPDATE tracks SET title='One Dance (feat. Wizkid & Kyla)' WHERE id=100")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    single = conn.execute(
        "SELECT id, canonical_track_id FROM lib2_tracks WHERE legacy_track_id=102"
    ).fetchone()
    album_track = conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()
    conn.close()
    assert single["canonical_track_id"] == album_track["id"]


def test_single_album_linkage_survives_feat_suffix_on_single(legacy_db):
    """Mirror of the above: the annotation may sit on the single instead."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "UPDATE tracks SET title='One Dance (feat. Wizkid & Kyla)' WHERE id=102")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    single = conn.execute(
        "SELECT id, canonical_track_id FROM lib2_tracks WHERE legacy_track_id=102"
    ).fetchone()
    album_track = conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100").fetchone()
    conn.close()
    assert single["canonical_track_id"] == album_track["id"]


def test_dedup_title_key_strips_only_featured_annotations():
    """The dedup key drops featured-artist tails but preserves version qualifiers
    (Remix/Live/Remastered are distinct recordings and must not be collapsed)."""
    from core.library2.importer import dedup_title_key

    assert dedup_title_key("One Dance (feat. Wizkid & Kyla)") == dedup_title_key("One Dance")
    assert dedup_title_key("One Dance [ft. Wizkid]") == dedup_title_key("One Dance")
    assert dedup_title_key("One Dance feat. Wizkid") == dedup_title_key("One Dance")
    # Not a credit annotation — must stay distinct:
    assert dedup_title_key("One Dance - Live") != dedup_title_key("One Dance")
    assert dedup_title_key("One Dance (Remix)") != dedup_title_key("One Dance")
    assert dedup_title_key("Feature Presentation") == "feature presentation"


def test_idempotent_rerun(legacy_db):
    first = import_legacy_library(legacy_db)
    second = import_legacy_library(legacy_db)
    # Re-run must not duplicate rows.
    conn = sqlite3.connect(legacy_db.path)
    for table, expected in (("lib2_artists", 2), ("lib2_albums", 2), ("lib2_tracks", 3),
                            ("lib2_track_files", 2)):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == expected, f"{table} duplicated on re-run: {count}"
    conn.close()
    assert second["files"] == 0   # nothing new to insert the second time


def test_rerun_reconciles_legacy_file_path_without_touching_secondary_file(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, source) VALUES(?, ?, ?)",
        (track_id, "/m/manual-secondary.flac", "manual"),
    )
    conn.execute("UPDATE tracks SET file_path='/m/renamed.flac' WHERE id=100")
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    files = conn.execute(
        """SELECT path, legacy_track_id, legacy_import_run_id
             FROM lib2_track_files WHERE track_id=? ORDER BY path""",
        (track_id,),
    ).fetchall()
    conn.close()
    assert [row["path"] for row in files] == [
        "/m/manual-secondary.flac",
        "/m/renamed.flac",
    ]
    assert files[0]["legacy_track_id"] is None
    assert files[0]["legacy_import_run_id"] is None
    assert files[1]["legacy_track_id"] == 100
    assert files[1]["legacy_import_run_id"]
    assert stats["files"] == 1
    assert stats["reconciled_files"] == 1


def test_rerun_removes_deleted_legacy_snapshot_rows(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    conn.execute("DELETE FROM tracks WHERE album_id=11")
    conn.execute("DELETE FROM albums WHERE id=11")
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    assert conn.execute(
        "SELECT 1 FROM lib2_tracks WHERE legacy_track_id=102"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM lib2_albums WHERE legacy_album_id=11"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE path='/m/single.flac'"
    ).fetchone() is None
    conn.close()
    assert stats["reconciled_files"] == 1
    assert stats["reconciled_tracks"] == 1
    assert stats["reconciled_albums"] == 1


def test_reconcile_detaches_provider_identity_instead_of_deleting_it(legacy_db):
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    conn.execute(
        "UPDATE lib2_albums SET spotify_id='provider-album' WHERE legacy_album_id=11"
    )
    conn.execute(
        "UPDATE lib2_tracks SET spotify_id='provider-track' WHERE legacy_track_id=102"
    )
    conn.execute("DELETE FROM tracks WHERE album_id=11")
    conn.execute("DELETE FROM albums WHERE id=11")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = legacy_db._get_connection()
    album = conn.execute(
        "SELECT legacy_album_id, origin FROM lib2_albums WHERE spotify_id='provider-album'"
    ).fetchone()
    track = conn.execute(
        "SELECT legacy_track_id FROM lib2_tracks WHERE spotify_id='provider-track'"
    ).fetchone()
    file_row = conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE path='/m/single.flac'"
    ).fetchone()
    conn.close()
    assert dict(album) == {"legacy_album_id": None, "origin": "discography"}
    assert track["legacy_track_id"] is None
    assert file_row is None


def test_reset_rebuilds(legacy_db):
    import_legacy_library(legacy_db)
    stats = import_legacy_library(legacy_db, reset=True)
    assert stats["tracks"] == 3
    conn = sqlite3.connect(legacy_db.path)
    assert conn.execute("SELECT COUNT(*) FROM lib2_tracks").fetchone()[0] == 3
    conn.close()


def test_album_monitor_intent_reprojects_and_survives_reset(legacy_db):
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    record_rule(conn, "album", album_id, False, PROVENANCE_USER)
    # Simulate compatibility-column drift: the rule remains authoritative.
    conn.execute("UPDATE lib2_albums SET monitored=1 WHERE id=?", (album_id,))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    row = conn.execute(
        "SELECT monitored FROM lib2_albums WHERE title='Views'"
    ).fetchone()
    assert row["monitored"] == 0
    conn.close()

    stats = import_legacy_library(legacy_db, reset=True)
    conn = legacy_db._get_connection()
    row = conn.execute(
        """SELECT al.monitored, r.monitored AS rule_monitored, r.provenance
             FROM lib2_albums al
             JOIN lib2_monitor_rules r
               ON r.entity_type='album' AND r.entity_id=al.id AND r.profile_id=1
            WHERE al.title='Views'"""
    ).fetchone()
    conn.close()

    assert stats["album_monitor_intent_restored"] == 1
    assert dict(row) == {
        "monitored": 0,
        "rule_monitored": 0,
        "provenance": "user_explicit",
    }


def test_import_uses_live_default_after_profile_one_is_deleted(legacy_db):
    from core.library2.schema import ensure_library_v2_schema

    conn = legacy_db._get_connection()
    ensure_library_v2_schema(conn)
    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
    conn.execute("DELETE FROM quality_profiles WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    try:
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            profile_ids = {
                row[0] for row in conn.execute(
                    f"SELECT DISTINCT quality_profile_id FROM {table}")
            }
            assert profile_ids == {2}, table
    finally:
        conn.close()


def test_wishlist_only_track_seeds_missing_monitored_library_rows(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp_track_1",
        "name": "Only Wanted Song",
        "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        "album": {
            "id": "sp_album_1",
            "name": "Wishlist Album",
            "album_type": "single",
            "total_tracks": 3,
            "release_date": "2026-01-01",
            "images": [{"url": "http://cover"}],
            "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        },
        "track_number": 1,
        "disc_number": 1,
        "duration_ms": 123000,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_1", json.dumps(payload), "manual"),
    )
    conn.commit()
    conn.close()

    stats = import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    artist = conn.execute("SELECT * FROM lib2_artists WHERE name='Wishlist Artist'").fetchone()
    album = conn.execute("SELECT * FROM lib2_albums WHERE title='Wishlist Album'").fetchone()
    track = conn.execute("SELECT * FROM lib2_tracks WHERE title='Only Wanted Song'").fetchone()
    file_count = conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?", (track["id"],)
    ).fetchone()[0]
    conn.close()

    assert stats["wishlist_tracks"] == 1
    assert artist["monitored"] == 0
    assert album["monitored"] == 0
    # Keep the provider's canonical size.  The detail view can immediately
    # show missing slots and the critical tracklist precache can resolve their
    # real titles instead of considering this a complete one-track release.
    assert album["track_count"] == 3
    assert album["expected_track_count"] == 3
    assert track["monitored"] == 1
    assert file_count == 0

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    detail = Q.get_album(conn, album["id"])
    conn.close()

    assert detail["track_count"] == 3
    assert detail["tracks_missing"] == 3
    assert [t["title"] for t in detail["tracks"]] == [
        "Only Wanted Song", None, None,
    ]
    assert detail["monitored"] is False
    assert detail["tracks"][0]["monitored"] is True

    # A later non-destructive re-import must not replace an explicit Library-v2
    # override with the still-present legacy Wishlist row.
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    conn = legacy_db._get_connection()
    record_rule(conn, "track", track["id"], False, PROVENANCE_USER)
    conn.execute("UPDATE lib2_tracks SET monitored=0 WHERE id=?", (track["id"],))
    conn.commit()
    conn.close()
    import_legacy_library(legacy_db)
    conn = legacy_db._get_connection()
    rule = conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type='track' AND entity_id=? AND profile_id=1",
        (track["id"],),
    ).fetchone()
    projected = conn.execute(
        "SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
        (track["id"],),
    ).fetchone()
    conn.close()
    assert (rule["monitored"], rule["provenance"]) == (0, PROVENANCE_USER)
    assert projected["wanted"] == 0


def test_album_parent_is_monitored_when_files_and_wishlist_cover_every_track(legacy_db):
    """A release-level monitor is safe when every canonical slot is covered,
    even when that coverage is a mix of downloaded and Wishlist tracks."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("ALTER TABLE tracks ADD COLUMN spotify_track_id TEXT")
    conn.execute("UPDATE tracks SET spotify_track_id='sp-hotline' WHERE id=101")
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp-hotline",
        "name": "Hotline Bling",
        "artists": [{"id": "sp1", "name": "Drake"}],
        "album": {
            "name": "Views", "album_type": "album", "total_tracks": 2,
            "artists": [{"id": "sp1", "name": "Drake"}],
        },
        "track_number": 2,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data) VALUES(?,?)",
        ("sp-hotline", json.dumps(payload)),
    )
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = legacy_db._get_connection()
    views = conn.execute(
        "SELECT id, monitored FROM lib2_albums WHERE title='Views'"
    ).fetchone()
    tracks = conn.execute(
        "SELECT title, monitored FROM lib2_tracks WHERE album_id=? ORDER BY track_number",
        (views["id"],),
    ).fetchall()
    wanted = conn.execute(
        "SELECT COUNT(*) FROM lib2_wanted_tracks w JOIN lib2_tracks t ON t.id=w.track_id "
        "WHERE t.album_id=? AND w.profile_id=1 AND w.wanted=1",
        (views["id"],),
    ).fetchone()[0]
    conn.close()

    assert views["monitored"] == 1
    assert [(row["title"], row["monitored"]) for row in tracks] == [
        ("One Dance", 1), ("Hotline Bling", 1),
    ]
    assert wanted == 2


def test_wishlist_seed_preserves_valid_track_profile_only(legacy_db, caplog):
    from core.quality.schema import ensure_quality_profiles_schema

    conn = sqlite3.connect(legacy_db.path)
    ensure_quality_profiles_schema(conn)
    conn.execute("INSERT INTO quality_profiles(id, name) VALUES(7, 'Wishlist Hi-Res')")
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            profile_id INTEGER DEFAULT 1,
            quality_profile_id INTEGER
        )
    """)

    def _payload(track_id, title):
        return {
            "id": track_id,
            "name": title,
            "artists": [{"id": "sp_artist_q", "name": "Profiled Artist"}],
            "album": {
                "id": "sp_album_q",
                "name": "Profiled Album",
                "album_type": "album",
                "total_tracks": 2,
                "artists": [{"id": "sp_artist_q", "name": "Profiled Artist"}],
            },
        }

    conn.executemany(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, quality_profile_id) "
        "VALUES(?,?,?)",
        [
            ("sp_profiled", json.dumps(_payload("sp_profiled", "Profiled")), 7),
            ("sp_dangling", json.dumps(_payload("sp_dangling", "Dangling")), 999),
        ],
    )
    conn.commit()
    conn.close()

    caplog.set_level("WARNING")
    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    default_id = conn.execute(
        "SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1"
    ).fetchone()[0]
    track_profiles = {
        row["title"]: row["quality_profile_id"]
        for row in conn.execute(
            "SELECT title, quality_profile_id FROM lib2_tracks "
            "WHERE spotify_id IN ('sp_profiled', 'sp_dangling')")
    }
    album_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE spotify_id='sp_album_q'"
    ).fetchone()[0]
    artist_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE spotify_id='sp_artist_q'"
    ).fetchone()[0]
    conn.close()

    assert track_profiles == {"Profiled": 7, "Dangling": default_id}
    assert album_profile == default_id
    assert artist_profile == default_id
    assert "invalid quality profile 999" in caplog.text


def test_wishlist_profile_conflict_is_visible_and_latest_row_wins(legacy_db, caplog):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            profile_id INTEGER DEFAULT 1,
            quality_profile_id INTEGER
        )
    """)
    payload = {
        "id": "sp_conflict",
        "name": "Conflicted",
        "artists": [{"id": "sp_conflict_artist", "name": "Conflict Artist"}],
        "album": {
            "id": "sp_conflict_album",
            "name": "Conflict Album",
            "artists": [{"id": "sp_conflict_artist", "name": "Conflict Artist"}],
        },
    }
    conn.executemany(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, quality_profile_id) "
        "VALUES(?,?,?)",
        [
            ("sp_conflict::first", json.dumps(payload), 1),
            ("sp_conflict::second", json.dumps(payload), 2),
        ],
    )
    conn.commit()
    conn.close()

    caplog.set_level("WARNING")
    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    profile_id = conn.execute(
        "SELECT quality_profile_id FROM lib2_tracks WHERE spotify_id='sp_conflict'"
    ).fetchone()[0]
    conn.close()
    assert profile_id == 2
    assert "assign different quality profiles" in caplog.text


def test_wishlist_seed_does_not_clamp_discography_expected_count(legacy_db):
    """A wishlist track that lands on a provider-only (discography) release must
    not shrink the release's expected_track_count to the wishlisted rows — the
    later tracklist materialization trims to expected, so a clamp would
    truncate the whole release to one track."""
    import_legacy_library(legacy_db)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, spotify_id) "
        "VALUES('Wishlist Artist','Wishlist Artist','sp_artist_1')")
    artist_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id, "
        "origin, monitored, track_count, expected_track_count) "
        "VALUES(?, 'Big Release', 'album', 'sp_album_1', 'discography', 0, 12, 12)",
        (artist_id,))
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                 (album_id, artist_id))
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp_track_9",
        "name": "Wanted Album Cut",
        "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        "album": {
            "id": "sp_album_1",
            "name": "Big Release",
            "album_type": "album",
            "total_tracks": 12,
            "artists": [{"id": "sp_artist_1", "name": "Wishlist Artist"}],
        },
        "track_number": 3,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_9", json.dumps(payload), "manual"))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    album = conn.execute("SELECT * FROM lib2_albums WHERE spotify_id='sp_album_1'").fetchone()
    conn.close()
    assert album["expected_track_count"] == 12
    assert album["origin"] == "discography"


def test_full_band_name_credit_is_not_split_into_ghost_artists(legacy_db):
    """'Simon & Garfunkel' as a track credit must reuse the existing artist row,
    not be split at '&' into two invented artists."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "INSERT INTO artists VALUES(2,'Simon & Garfunkel',NULL,NULL,NULL,NULL,NULL)")
    conn.execute(
        "INSERT INTO albums VALUES(20,2,'Bookends',1968,NULL,NULL,1,NULL)")
    conn.execute(
        "INSERT INTO tracks VALUES(200,20,2,'Mrs. Robinson',1,240000,'/m/mrs.flac',900,4000,"
        "'Simon & Garfunkel')")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    names = {r["name"] for r in conn.execute("SELECT name FROM lib2_artists")}
    conn.close()
    assert "Simon & Garfunkel" in names
    assert "Simon" not in names
    assert "Garfunkel" not in names


@pytest.mark.parametrize(
    ("credit", "ghost_names"),
    [
        ("Earth, Wind & Fire", {"Earth", "Wind", "Fire"}),
        ("Florence and the Machine", {"Florence", "the Machine"}),
        ("Hall & Oates", {"Hall", "Oates"}),
    ],
)
def test_unknown_full_band_credit_is_preserved_without_ghost_artists(
    legacy_db, credit, ghost_names
):
    """P2-24: an ambiguous, providerless credit is not evidence that every
    comma/conjunction-delimited token is a separate artist.

    The old M1 guard only worked when the full band already had its own legacy
    artist row.  Guest bands can appear solely in ``track_artist``; preserve
    that raw credit as one artist instead of inventing several identities.
    """
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("UPDATE tracks SET track_artist=? WHERE id=101", (credit,))
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    names = {r["name"] for r in conn.execute("SELECT name FROM lib2_artists")}
    credited = {
        r["name"]
        for r in conn.execute(
            """SELECT a.name
                 FROM lib2_track_artists ta
                 JOIN lib2_artists a ON a.id=ta.artist_id
                 JOIN lib2_tracks t ON t.id=ta.track_id
                WHERE t.legacy_track_id=101"""
        )
    }
    conn.close()

    assert credit in names
    assert credit in credited
    assert names.isdisjoint(ghost_names)


def test_unknown_band_in_title_feature_credit_is_preserved(legacy_db):
    """The same P2-24 guard applies to credits extracted from a title."""
    credit = "Earth, Wind & Fire"
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "UPDATE tracks SET title=?, track_artist=NULL WHERE id=101",
        (f"September (feat. {credit})",),
    )
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    names = {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}
    conn.close()
    assert credit in names
    assert names.isdisjoint({"Earth", "Wind", "Fire"})


def test_ambiguous_credit_splits_when_every_artist_is_already_known(legacy_db):
    """Conservative P2-24 parsing must not discard corroborated junctions."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "INSERT INTO artists VALUES(2,'Rihanna',NULL,NULL,NULL,NULL,NULL)"
    )
    conn.execute("UPDATE tracks SET track_artist='Drake & Rihanna' WHERE id=101")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    credited = {
        r[0]
        for r in conn.execute(
            """SELECT a.name
                 FROM lib2_track_artists ta
                 JOIN lib2_artists a ON a.id=ta.artist_id
                 JOIN lib2_tracks t ON t.id=ta.track_id
                WHERE t.legacy_track_id=101"""
        )
    }
    names = {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}
    conn.close()
    assert {"Drake", "Rihanna"} <= credited
    assert "Drake & Rihanna" not in names


def test_title_feature_list_splits_when_track_credit_supplies_a_known_anchor(legacy_db):
    """P2-24 stays multi-artist aware when the flat legacy fields corroborate
    one member of an otherwise ambiguous title-credit list."""
    conn = sqlite3.connect(legacy_db.path)
    conn.execute(
        "UPDATE tracks SET title=?, track_artist=? WHERE id=100",
        ("One Dance (feat. Wizkid & Kyla)", "Drake feat. Wizkid"),
    )
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    names = {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}
    conn.close()
    assert {"Drake", "Wizkid", "Kyla"} <= names
    assert "Wizkid & Kyla" not in names


def test_watchlist_artist_monitoring_is_independent_from_wishlist_tracks(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.execute("""
        CREATE TABLE watchlist_artists(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_name TEXT NOT NULL,
            spotify_artist_id TEXT,
            musicbrainz_artist_id TEXT
        )
    """)
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id) VALUES(?, ?)",
        ("Drake", "sp1"),
    )
    conn.execute("""
        CREATE TABLE wishlist_tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_track_id TEXT NOT NULL,
            spotify_data TEXT NOT NULL,
            source_type TEXT DEFAULT 'manual',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    payload = {
        "id": "sp_track_2",
        "name": "Wishlist Only",
        "artists": [{"id": "sp_artist_2", "name": "Other Wishlist Artist"}],
        "album": {
            "id": "sp_album_2",
            "name": "Wishlist Only Single",
            "album_type": "single",
            "total_tracks": 1,
            "artists": [{"id": "sp_artist_2", "name": "Other Wishlist Artist"}],
        },
        "track_number": 1,
    }
    conn.execute(
        "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type) VALUES(?,?,?)",
        ("sp_track_2", json.dumps(payload), "manual"),
    )
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db, reset=True)

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    drake = conn.execute("SELECT monitored FROM lib2_artists WHERE name='Drake'").fetchone()
    wishlist_artist = conn.execute(
        "SELECT monitored FROM lib2_artists WHERE name='Other Wishlist Artist'"
    ).fetchone()
    wishlist_track = conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE title='Wishlist Only'"
    ).fetchone()
    conn.close()

    assert drake["monitored"] == 1
    assert wishlist_artist["monitored"] == 0
    assert wishlist_track["monitored"] == 1


# ---------------------------------------------------------------------------
# §62.5/§62.6 Stufe 4: the legacy import must not duplicate a same-named artist
# ---------------------------------------------------------------------------

def _conn(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    return conn


def _pre_seed_artist(legacy_db, name, **cols):
    """Create the lib2 schema up front and plant an artist row — the state a
    wishlist-materialize (autolink) leaves behind BEFORE the first legacy
    import runs (§62.1 timeline steps 1→3)."""
    import sqlite3
    from core.library2.schema import ensure_library_v2_schema

    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    keys = ", ".join(["name", "sort_name", *cols.keys()])
    marks = ", ".join("?" for _ in range(2 + len(cols)))
    cur = conn.execute(
        f"INSERT INTO lib2_artists({keys}) VALUES({marks})",
        (name, name, *cols.values()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def test_legacy_import_adopts_existing_same_named_idless_artist(legacy_db):
    pre_id = _pre_seed_artist(legacy_db, "Drake")

    import_legacy_library(legacy_db)

    conn = _conn(legacy_db)
    rows = conn.execute(
        "SELECT id, legacy_artist_id, spotify_id FROM lib2_artists "
        "WHERE name='Drake'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == pre_id
    assert rows[0]["legacy_artist_id"] == 1
    assert rows[0]["spotify_id"] == "sp1"     # legacy ids adopted onto the row
    conn.close()


def test_legacy_import_adopts_same_named_artist_matched_by_provider_id(legacy_db):
    pre_id = _pre_seed_artist(legacy_db, "drake  ", spotify_id="sp1")

    import_legacy_library(legacy_db)

    conn = _conn(legacy_db)
    rows = conn.execute(
        "SELECT id, legacy_artist_id FROM lib2_artists WHERE name='Drake'").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == pre_id
    assert rows[0]["legacy_artist_id"] == 1
    conn.close()


def test_legacy_import_keeps_conflicting_same_named_artist_separate(legacy_db):
    """Same display name but a DIFFERENT id of the same source = a genuinely
    different artist (§16.3(b)) — the import must NOT fold them."""
    _pre_seed_artist(legacy_db, "Drake", spotify_id="sp-other")

    import_legacy_library(legacy_db)

    conn = _conn(legacy_db)
    rows = conn.execute(
        "SELECT id, spotify_id, legacy_artist_id FROM lib2_artists "
        "WHERE name='Drake' ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["spotify_id"] == "sp-other"
    assert rows[0]["legacy_artist_id"] is None
    assert rows[1]["spotify_id"] == "sp1"
    assert rows[1]["legacy_artist_id"] == 1
    conn.close()


def test_import_alias_links_leftover_conflicting_name_twins(legacy_db):
    """§62.6 Stufe 4 wiring: the import's closing repair pass soft-links
    same-name artists it must not merge (conflicting ids), so discography
    fan-out sees them as one group."""
    _pre_seed_artist(legacy_db, "Drake", spotify_id="sp-other")

    import_legacy_library(legacy_db)

    conn = _conn(legacy_db)
    rows = conn.execute(
        "SELECT id, spotify_id, canonical_artist_id FROM lib2_artists "
        "WHERE name='Drake' ORDER BY id").fetchall()
    assert len(rows) == 2
    linked = [r for r in rows if r["canonical_artist_id"] is not None]
    assert len(linked) == 1
    conn.close()


def test_artist_resolver_provider_map_is_namespace_aware(legacy_db):
    """§62.6 Stufe 5: a Deezer id numerically equal to ANOTHER artist's
    iTunes id must not resolve to that artist — the provider map keys on
    (source, value), not on the bare value."""
    conn, resolver = _fresh_resolver(legacy_db)
    try:
        a1 = resolver.get_or_create_by_name(
            "Nova", provider_ids={"itunes": "1315147"})
        a2 = resolver.get_or_create_by_name(
            "Totally Different", provider_ids={"deezer": "1315147"})
        assert a1 != a2
        # Same (source, value) still resolves to the same artist.
        assert resolver.get_or_create_by_name(
            "Nova Alias", provider_ids={"itunes": "1315147"}) == a1
    finally:
        conn.close()


def test_import_never_maps_foreign_shaped_ids_into_spotify_namespace(legacy_db):
    """§62.4: legacy enrichment wrote iTunes/Deezer ids into spotify_*
    columns ("Column name is spotify_album_id but stores iTunes ID too").
    The importer must not carry that poison into lib2's spotify namespace."""
    conn = _conn(legacy_db)
    conn.execute("ALTER TABLE albums ADD COLUMN spotify_album_id TEXT")
    conn.execute("ALTER TABLE albums ADD COLUMN itunes_album_id TEXT")
    conn.execute("ALTER TABLE artists ADD COLUMN itunes_artist_id TEXT")
    # The Sawano row shape: identical numeric value in both columns.
    conn.execute(
        "UPDATE albums SET spotify_album_id='1239706770', "
        "itunes_album_id='1239706770' WHERE id=10")
    conn.execute("UPDATE artists SET spotify_artist_id='1315147', "
                 "itunes_artist_id='1315147' WHERE id=1")
    conn.commit()
    conn.close()

    import_legacy_library(legacy_db)

    conn = _conn(legacy_db)
    album = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE title='Views'"
    ).fetchone()
    assert album["spotify_id"] is None
    album_ids = json.loads(album["external_ids"])
    assert album_ids.get("itunes") == "1239706770"
    assert "spotify" not in album_ids
    artist = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_artists WHERE name='Drake'"
    ).fetchone()
    assert artist["spotify_id"] is None
    artist_ids = json.loads(artist["external_ids"])
    assert artist_ids.get("itunes") == "1315147"
    assert "spotify" not in artist_ids
    conn.close()
