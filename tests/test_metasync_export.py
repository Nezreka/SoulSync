"""MetaSync export endpoint — /api/v1/metasync/export.

MetaSync is a peer-to-peer metadata sidecar: it walks this export, packages
rows into signed claims and trades them with peers. Two properties it depends
on, both pinned here:

* the walk is COMPLETE — keyset pagination over a library that enrichment
  workers are mutating must visit every row exactly once, with no gaps and no
  duplicates, and must terminate;
* the payload is an ALLOWLIST — nothing install-local (media-server ids, file
  paths, listening behaviour) may leave the box, and a column added to
  ``tracks`` next year must not silently join the export. The excluded-field
  assertions below are the important ones: they are what stops that.
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from urllib.parse import urlencode

import pytest
from flask import Flask

from api import create_api_blueprint, limiter
from database.music_database import MusicDatabase

RAW_KEY = "sk_metasync_test_key"


# ── fixtures ──────────────────────────────────────────────────────────────

class _Config:
    def __init__(self):
        self._keys = [{
            "id": "k1",
            "label": "test",
            "key_hash": hashlib.sha256(RAW_KEY.encode()).hexdigest(),
            "key_prefix": RAW_KEY[:11],
        }]

    def get(self, key, default=None):
        return self._keys if key == "api_keys" else default

    def set(self, *a, **kw):
        return None

    def save_config(self, *a, **kw):
        return None


@pytest.fixture
def db(tmp_path):
    """A real, throwaway MusicDatabase — schema from the live initializer, so
    these tests break if a migration changes shape under them."""
    return MusicDatabase(str(tmp_path / "metasync.db"))


def _seed(db, artists=1, albums_per_artist=1, tracks_per_album=3, soul_prefix="soul_"):
    """Insert a small library directly. Returns (artist_ids, album_ids, track_ids)."""
    conn = db._get_connection()
    cur = conn.cursor()
    a_ids, al_ids, t_ids = [], [], []
    for a in range(artists):
        aid = f"ar{a:03d}"
        cur.execute(
            "INSERT INTO artists (id, name, soul_id, soul_id_path, spotify_artist_id, "
            "spotify_match_status, updated_at) VALUES (?,?,?,?,?,?,?)",
            (aid, f"Artist {a}", f"{soul_prefix}artist_{a}", "canonical",
             f"sp_ar_{a}", "matched", "2026-08-01T00:00:00"),
        )
        a_ids.append(aid)
        for b in range(albums_per_artist):
            alid = f"al{a:03d}{b:03d}"
            cur.execute(
                "INSERT INTO albums (id, artist_id, title, soul_id, year, "
                "spotify_album_id, spotify_match_status, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (alid, aid, f"Album {a}-{b}", f"{soul_prefix}album_{a}_{b}", 2020,
                 f"sp_al_{a}_{b}", "matched", "2026-08-01T00:00:00"),
            )
            al_ids.append(alid)
            for t in range(tracks_per_album):
                tid = f"tr{a:03d}{b:03d}{t:03d}"
                cur.execute(
                    "INSERT INTO tracks (id, album_id, artist_id, title, soul_id, "
                    "album_soul_id, track_number, file_path, play_count, "
                    "genius_lyrics, spotify_track_id, spotify_match_status, "
                    "track_artist, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, alid, aid, f"Track {t}", f"{soul_prefix}track_{a}_{b}_{t}",
                     f"{soul_prefix}album_{a}_{b}", t + 1,
                     "/music/secret/path.flac", 42, "all the lyrics",
                     f"sp_tr_{a}_{b}_{t}", "matched", "TAG ARTIST NAME",
                     "2026-08-01T00:00:00"),
                )
                t_ids.append(tid)
    conn.commit()
    return a_ids, al_ids, t_ids


@pytest.fixture
def client(db, monkeypatch):
    """Flask test client with the real blueprint mounted."""
    import database.music_database as mdb
    monkeypatch.setattr(mdb, "get_database", lambda: db)
    import api.metasync as ms
    monkeypatch.setattr(ms, "get_database", lambda: db)

    app = Flask(__name__)
    app.config["RATELIMIT_ENABLED"] = False
    limiter.init_app(app)
    app.register_blueprint(create_api_blueprint(), url_prefix="/api/v1")
    app.soulsync = {"config_manager": _Config()}
    return app.test_client()


def _get(client, **params):
    # urlencode, not string concatenation: a '+' in a raw query string is a
    # SPACE, which silently mangles both an offset like +02:00 and a base64
    # cursor before the route ever sees them.
    qs = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return client.get(f"/api/v1/metasync/export?{qs}",
                      headers={"Authorization": f"Bearer {RAW_KEY}"})


# ── the walk is complete ──────────────────────────────────────────────────

@pytest.mark.parametrize("entity,count", [("artist", 5), ("album", 5), ("track", 15)])
def test_pagination_visits_every_row_exactly_once(db, client, entity, count):
    """No duplicates, no gaps, and it terminates. Walked at limit=2 so the
    seeded library takes many pages."""
    _seed(db, artists=5, albums_per_artist=1, tracks_per_album=3)

    seen, cursor, pages = [], "", 0
    while True:
        resp = _get(client, entity=entity, limit=2, cursor=cursor)
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        seen.extend(item["soul_id"] for item in data["items"])
        pages += 1
        assert pages < 50, "walk failed to terminate"
        if not data["has_more"]:
            break
        cursor = data["next_cursor"]

    assert len(seen) == count, "gap or short walk"
    assert len(set(seen)) == count, "duplicate rows across pages"


def test_has_more_is_false_on_the_last_partial_page(db, client):
    _seed(db, artists=3, albums_per_artist=1, tracks_per_album=1)
    resp = _get(client, entity="artist", limit=500)
    data = resp.get_json()["data"]
    assert len(data["items"]) == 3
    assert data["has_more"] is False


def test_cursor_is_opaque_base64_of_the_last_id(db, client):
    a_ids, _, _ = _seed(db, artists=2, albums_per_artist=0, tracks_per_album=0)
    data = _get(client, entity="artist", limit=1).get_json()["data"]
    decoded = base64.urlsafe_b64decode(data["next_cursor"].encode()).decode()
    assert decoded == a_ids[0]


# ── since ─────────────────────────────────────────────────────────────────

def test_since_filters_to_changed_rows(db, client):
    _seed(db, artists=2, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET updated_at = ? WHERE id = ?",
                 ("2026-08-18T12:00:00", "ar001"))
    conn.commit()

    items = _get(client, entity="artist", since="2026-08-10T00:00:00").get_json()["data"]["items"]
    assert [i["soul_id"] for i in items] == ["soul_artist_1"]

    everything = _get(client, entity="artist").get_json()["data"]["items"]
    assert len(everything) == 2


def test_a_same_day_change_is_not_lost_to_a_text_comparison(db, client):
    """L2-010: SQLite writes CURRENT_TIMESTAMP as 'YYYY-MM-DD HH:MM:SS' while
    the API takes ISO-8601 with a 'T'. Compared as text, 'T' > ' ', so asking
    for everything since midnight silently returned nothing from that same
    day — a permanent hole in every consumer's incremental feed."""
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET updated_at = ? WHERE id = ?",
                 ("2026-08-21 12:00:00", "ar000"))
    conn.commit()

    for since in ("2026-08-21T00:00:00", "2026-08-21 00:00:00",
                  "2026-08-21T00:00:00Z"):
        items = _get(client, entity="artist", since=since).get_json()["data"]["items"]
        assert [i["soul_id"] for i in items] == ["soul_artist_0"], since


def test_offsets_are_compared_as_instants_not_as_text(db, client):
    """13:00+02:00 is 11:00 UTC, so a row stamped 11:30 UTC is after it — but
    ranked as text the offset string decided the answer instead."""
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET updated_at = ? WHERE id = ?",
                 ("2026-08-21 11:30:00", "ar000"))
    conn.commit()

    included = _get(client, entity="artist",
                    since="2026-08-21T13:00:00+02:00").get_json()["data"]["items"]
    assert [i["soul_id"] for i in included] == ["soul_artist_0"]

    excluded = _get(client, entity="artist",
                    since="2026-08-21T14:00:00+02:00").get_json()["data"]["items"]
    assert excluded == []


def test_the_boundary_itself_is_included(db, client):
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET updated_at = ? WHERE id = ?",
                 ("2026-08-21 12:00:00", "ar000"))
    conn.commit()

    items = _get(client, entity="artist",
                 since="2026-08-21T12:00:00").get_json()["data"]["items"]
    assert [i["soul_id"] for i in items] == ["soul_artist_0"]


# ── L2-011: the change feed has to cover the whole payload ────────────────

def _touch(db, table, row_id, when):
    conn = db._get_connection()
    conn.execute(f"UPDATE {table} SET updated_at = ? WHERE id = ?", (when, row_id))
    conn.commit()


def _incremental(client, entity, since):
    return [i["soul_id"] for i in
            _get(client, entity=entity, since=since).get_json()["data"]["items"]]


def test_an_artist_rename_invalidates_its_albums_and_tracks(db, client):
    """The album payload carries the artist's NAME and the track payload carries
    the artist name plus the album title. Renaming an artist rewrites what the
    export says about every one of its children while touching none of their
    timestamps, so a consumer that had already walked them was never told."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET name = 'Renamed', updated_at = ? WHERE id = 'ar000'",
                 ("2026-08-21 12:00:00",))
    conn.commit()

    since = "2026-08-21T00:00:00"
    assert _incremental(client, "album", since) == ["soul_album_0_0"]
    assert _incremental(client, "track", since) == ["soul_track_0_0_0"]


def test_an_album_retitle_invalidates_its_tracks(db, client):
    """A track's payload carries its album's title and year."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)
    _touch(db, "albums", "al000000", "2026-08-21 12:00:00")

    assert _incremental(client, "track", "2026-08-21T00:00:00") == ["soul_track_0_0_0"]


def test_an_untouched_child_is_still_left_out(db, client):
    """The widened predicate must not turn the incremental export into a full
    one: nothing changed anywhere, so nothing comes back."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)

    assert _incremental(client, "album", "2026-08-21T00:00:00") == []
    assert _incremental(client, "track", "2026-08-21T00:00:00") == []


def test_minting_a_soul_id_makes_the_row_appear_in_the_incremental(db, client):
    """A row with no soul_id is filtered OUT of the export entirely, so minting
    one is the moment it starts existing for consumers. The SoulID worker used
    to write it without touching updated_at, so a full walk that ran before the
    worker meant the row never appeared in any later incremental either."""
    from core.soulid_worker import SoulIDWorker  # noqa: F401  (import guard only)

    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    conn.execute("UPDATE artists SET soul_id = NULL WHERE id = 'ar000'")
    conn.commit()
    assert _incremental(client, "artist", "2026-08-01T00:00:00") == []

    # What the worker's write now does, verbatim.
    conn = db._get_connection()
    conn.execute("UPDATE artists SET soul_id = ?, soul_id_path = ?, "
                 "updated_at = CURRENT_TIMESTAMP "
                 "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                 ("soul_artist_0", "canonical", "ar000"))
    conn.commit()

    assert _incremental(client, "artist", "2026-08-01T00:00:00") == ["soul_artist_0"]


def test_a_canonical_claim_shows_up_in_the_incremental(db, client):
    """canonical_source/canonical_album_id are exported fields."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=0)
    assert _incremental(client, "album", "2026-08-21T00:00:00") == []

    assert db.set_album_canonical("al000000", "musicbrainz", "mb-1", 0.9) is True

    assert _incremental(client, "album", "2026-08-21T00:00:00") == ["soul_album_0_0"]


# ── unpublishable rows are never served ───────────────────────────────────

def test_rows_without_a_usable_soul_id_are_omitted(db, client):
    """NULL, empty, and the soul_unnamed_ fallback — the last one embeds the
    library primary key (a Plex ratingKey / Jellyfin GUID) and is install-local."""
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    for i, soul in enumerate((None, "", "soul_unnamed_997"), start=90):
        conn.execute("INSERT INTO artists (id, name, soul_id) VALUES (?,?,?)",
                     (f"ar{i}", f"Bad {i}", soul))
    conn.commit()

    items = _get(client, entity="artist").get_json()["data"]["items"]
    assert [i["soul_id"] for i in items] == ["soul_artist_0"]


def test_unnamed_rows_do_not_shorten_a_page_and_end_the_walk(db, client):
    """Regression: filtering AFTER the SQL LIMIT returns a short page, and the
    route reads len(items) == limit to decide whether to keep going — so an
    unnamed row mid-library used to end the export early and silently drop
    everything after it."""
    conn = db._get_connection()
    rows = [("ar001", "soul_a"), ("ar002", "soul_unnamed_2"), ("ar003", "soul_b")]
    for rid, soul in rows:
        conn.execute("INSERT INTO artists (id, name, soul_id) VALUES (?,?,?)",
                     (rid, rid, soul))
    conn.commit()

    seen, cursor = [], ""
    while True:
        data = _get(client, entity="artist", limit=1, cursor=cursor).get_json()["data"]
        seen.extend(i["soul_id"] for i in data["items"])
        if not data["has_more"]:
            break
        cursor = data["next_cursor"]
    assert seen == ["soul_a", "soul_b"], "the walk stopped at the unnamed row"


# ── the allowlist — the assertions that matter ────────────────────────────

EXCLUDED = (
    "id", "artist_id", "album_id",
    "file_path", "file_size", "bitrate",
    "thumb_url", "banner_url",
    "genius_lyrics", "genius_description", "summary", "discogs_bio",
    "lastfm_wiki", "alt_names", "aliases",
    "play_count", "last_played",
    "server_source", "verification_status", "repair_status",
    "repair_last_checked", "quality_profile_id",
)


@pytest.mark.parametrize("entity", ["artist", "album", "track"])
def test_excluded_fields_never_appear(db, client, entity):
    """Key by key. This is what stops a future column from leaking."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)
    items = _get(client, entity=entity).get_json()["data"]["items"]
    assert items
    for item in items:
        for forbidden in EXCLUDED:
            assert forbidden not in item, f"{forbidden} leaked into the {entity} export"


def test_track_export_carries_identity_and_provider_status(db, client):
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)
    item = _get(client, entity="track").get_json()["data"]["items"][0]
    assert item["soul_id"] and item["album_soul_id"]
    assert item["spotify_track_id"] == "sp_tr_0_0_0"
    # Load-bearing: MetaSync only publishes ids whose status is 'matched'.
    assert item["spotify_match_status"] == "matched"


def test_track_artist_name_comes_from_the_joined_artist_not_the_tag(db, client):
    """soulid_worker._process_tracks computes the track soul_id from the JOINED
    artists.name, so exporting tracks.track_artist would make an importer
    normalize a different string and derive a different key."""
    _seed(db, artists=1, albums_per_artist=1, tracks_per_album=1)
    item = _get(client, entity="track").get_json()["data"]["items"][0]
    assert item["artist_name"] == "Artist 0"
    assert item["album_title"] == "Album 0-0"


def test_artist_export_reports_its_derivation_path(db, client):
    """Only 'canonical' is reproducible on another install, so a consumer has
    to be able to tell which derivation produced the key."""
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    item = _get(client, entity="artist").get_json()["data"]["items"][0]
    assert item["soul_id_path"] == "canonical"


# ── schema drift ──────────────────────────────────────────────────────────

def test_a_missing_column_yields_none_instead_of_raising(db, client, tmp_path):
    """An upgraded database can lag the code by a migration. Naming a missing
    column in the SQL would fail the whole export with 'no such column'."""
    _seed(db, artists=1, albums_per_artist=0, tracks_per_album=0)
    conn = db._get_connection()
    # Rebuild `artists` without soul_id_path, simulating a pre-migration DB.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(artists)")]
    keep = [c for c in cols if c != "soul_id_path"]
    collist = ", ".join(keep)
    conn.execute(f"CREATE TABLE artists_old AS SELECT {collist} FROM artists")
    conn.execute("DROP TABLE artists")
    conn.execute("ALTER TABLE artists_old RENAME TO artists")
    conn.commit()

    resp = _get(client, entity="artist")
    assert resp.status_code == 200, resp.get_json()
    item = resp.get_json()["data"]["items"][0]
    assert item["soul_id_path"] is None


# ── request validation ────────────────────────────────────────────────────

def test_bogus_entity_is_a_400(client):
    assert _get(client, entity="bogus").status_code == 400


def test_missing_entity_is_a_400(client):
    assert _get(client).status_code == 400


def test_malformed_since_is_a_400(db, client):
    assert _get(client, entity="artist", since="last-tuesday").status_code == 400


def test_malformed_cursor_is_a_400(db, client):
    assert _get(client, entity="artist", cursor="!!!not-base64!!!").status_code == 400


def test_missing_api_key_is_a_401(client):
    assert client.get("/api/v1/metasync/export?entity=artist").status_code == 401


def test_limit_is_clamped(db, client):
    _seed(db, artists=3, albums_per_artist=0, tracks_per_album=0)
    assert _get(client, entity="artist", limit=99999).status_code == 200
    assert _get(client, entity="artist", limit=0).status_code == 200
    assert _get(client, entity="artist", limit="abc").status_code == 200


def test_export_is_read_only(db, client):
    """No INSERT/UPDATE/DELETE anywhere in the module."""
    with open("api/metasync.py", encoding="utf-8") as fh:
        src = fh.read().upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert verb not in src
