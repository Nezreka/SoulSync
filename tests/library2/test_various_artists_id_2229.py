"""#2229: spotify's "Various Artists" id is nobody's.

the reporter synced a playlist with two compilation tracks:
  * "メルト -MIKU EXPO 2014 in INDONESIA Live-" by ryo (supercell), on
    "HATSUNE MIKU EXPO 2014 IN INDONESIA [Live]"
  * "Attack on Tarir" by Lena Chappelle, on "Guild Wars 2: Heart of Thorns
    (Original Soundtrack)"
both albums credit Various Artists, spotify id 0LyfQWJT6nXafLPZqxe9Of. the
wishlist made that the download's artist context, the import's artist guard
renamed "Various Artists" to the singer and kept the id, autolink stored it on
ryo (supercell), and the next compilation's download was found by it:
"16 - ryo (supercell) - Attack on Tarir.flac", ARTISTS=ryo (supercell).
"""

from __future__ import annotations

import json

import pytest

from core.library2 import autolink as A
from core.various_artists_ids import (
    VARIOUS_ARTISTS_IDS,
    is_various_artists_id,
    without_various_artists_ids,
)

VA = "0LyfQWJT6nXafLPZqxe9Of"
VA_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"
RYO_REAL = "5Ud2lekREUXjinNlIGjJIX"
MIKU = "HATSUNE MIKU EXPO 2014 IN INDONESIA [Live]"
GW2 = "Guild Wars 2: Heart of Thorns (Original Soundtrack)"


# --- the shared set ---------------------------------------------------------

def test_the_ids_are_the_providers_various_artists():
    assert VARIOUS_ARTISTS_IDS["spotify"] == VA
    assert VARIOUS_ARTISTS_IDS["musicbrainz"] == VA_MBID
    assert is_various_artists_id(VA)
    assert is_various_artists_id(f"spotify:artist:{VA}")
    assert is_various_artists_id(f"https://open.spotify.com/artist/{VA}")
    assert is_various_artists_id(VA_MBID.upper())
    assert is_various_artists_id("5080", "deezer")
    assert is_various_artists_id("5080", "deezer_artist_id")
    assert is_various_artists_id("194", "discogs")
    assert is_various_artists_id("https://www.discogs.com/artist/194-Various", "discogs")
    assert is_various_artists_id("2935", "tidal")


def test_a_number_is_only_various_artists_for_its_own_provider():
    # deezer's 194 and discogs' 5080 are real artists
    assert not is_various_artists_id("5080")
    assert not is_various_artists_id("194", "deezer")
    assert not is_various_artists_id("5080", "discogs")
    assert not is_various_artists_id(RYO_REAL, "spotify")
    assert not is_various_artists_id("", "spotify")
    assert without_various_artists_ids({"spotify": VA, "deezer": "27"}) == {"deezer": "27"}


# --- the import: the guard and the source ids --------------------------------

def test_the_artist_guard_drops_the_id_of_the_name_it_replaces():
    from core.imports.pipeline import _take_artist_identity
    ctx = {"name": "Various Artists", "id": VA, "genres": []}
    _take_artist_identity(ctx, "Lena Chappelle", {"name": "Lena Chappelle"})
    assert ctx == {"name": "Lena Chappelle", "genres": []}
    ctx = {"name": "Various Artists", "id": VA}
    _take_artist_identity(ctx, "ryo (supercell)", {"name": "ryo (supercell)", "id": RYO_REAL})
    assert ctx == {"name": "ryo (supercell)", "id": RYO_REAL}


def test_provider_source_ids_never_hand_out_various_artists():
    from core.imports.context import get_provider_source_ids
    ids = get_provider_source_ids({"source": "spotify", "artist": {"name": "Lena Chappelle", "id": VA}})
    assert ids["artist_id"] == ""
    ids = get_provider_source_ids({"source": "deezer", "artist": {"name": "x", "id": "5080"}})
    assert ids["artist_id"] == ""
    ids = get_provider_source_ids({"source": "spotify", "artist": {"name": "ryo", "id": RYO_REAL}})
    assert ids["artist_id"] == RYO_REAL


# --- autolink: the writer that stored it --------------------------------------

@pytest.fixture(autouse=True)
def _no_artwork(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_a, **_k: None)


@pytest.fixture
def lib2(monkeypatch, legacy_db, imported_conn):
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return imported_conn


def _wishlist_import(path, title, singer, album, *, context_artist):
    """what the wishlist hands the import for a compilation track: the album
    credit (Various Artists, with its id) is the context's artist"""
    return {
        "_final_processed_path": path,
        "username": "soulseek",
        "source": "spotify",
        "artist": context_artist,
        "album": {"name": album, "id": f"sp-{album[:6]}", "album_type": "compilation",
                  "artists": [{"name": "Various Artists", "id": VA}]},
        "track_info": {
            "name": title,
            "artists": [{"name": singer}],   # no id: the reporter's payload
            "album": {"name": album, "id": f"sp-{album[:6]}", "album_type": "compilation",
                      "artists": [{"name": "Various Artists", "id": VA}]},
            "track_number": 16,
            "provider": "spotify",
            "id": f"sp-{title[:6]}",
        },
    }


def _artist_rows(conn, name):
    return [dict(r) for r in conn.execute(
        "SELECT id, spotify_id, external_ids FROM lib2_artists WHERE name=? ORDER BY id", (name,))]


def _track_artist(conn, file_id):
    return conn.execute(
        "SELECT a.name FROM lib2_track_files tf JOIN lib2_track_artists ta ON ta.track_id=tf.track_id "
        "JOIN lib2_artists a ON a.id=ta.artist_id WHERE tf.id=? AND ta.role='primary'",
        (file_id,)).fetchone()[0]


def _without_the_floor(conn):
    """the writers on their own, without the trigger that strips the id"""
    conn.execute("DROP TRIGGER IF EXISTS trg_lib2_artist_no_various_artists_id_insert")
    conn.execute("DROP TRIGGER IF EXISTS trg_lib2_artist_no_various_artists_id_update")
    conn.commit()


@pytest.mark.parametrize("renamed", [True, False])
def test_two_compilations_keep_their_own_singers(lib2, renamed):
    """``renamed``: the context artist after the import's artist guard (the
    singer, still holding the compilation's id, as before the fix), or as the
    wishlist built it (Various Artists)"""
    _without_the_floor(lib2)

    def ctx_artist(singer):
        return {"name": singer if renamed else "Various Artists", "id": VA}

    melt = A.link_download_into_library_v2(_wishlist_import(
        "/music/VA/miku/03 - ryo (supercell) - Melt.flac", "メルト -MIKU EXPO 2014 in INDONESIA Live-",
        "ryo (supercell)", MIKU, context_artist=ctx_artist("ryo (supercell)")))
    tarir = A.link_download_into_library_v2(_wishlist_import(
        "/music/VA/gw2/16 - Lena Chappelle - Attack on Tarir.flac", "Attack on Tarir",
        "Lena Chappelle", GW2, context_artist=ctx_artist("Lena Chappelle")))
    lib2.commit()

    assert [r["spotify_id"] for r in _artist_rows(lib2, "ryo (supercell)")] == [None]
    assert len(_artist_rows(lib2, "Lena Chappelle")) == 1
    assert _track_artist(lib2, melt) == "ryo (supercell)"
    assert _track_artist(lib2, tarir) == "Lena Chappelle"
    assert lib2.execute("SELECT COUNT(*) FROM lib2_artists WHERE spotify_id=?", (VA,)).fetchone()[0] == 0


def test_autolink_never_stores_it_on_an_artist(lib2):
    _without_the_floor(lib2)
    ryo = A._find_or_create_artist(lib2, "ryo (supercell)", spotify_id=VA, source="spotify")
    lena = A._find_or_create_artist(lib2, "Lena Chappelle", spotify_id=VA, source="spotify")
    deezer = A._find_or_create_artist(lib2, "Someone", spotify_id="5080", source="deezer")
    assert len({ryo, lena, deezer}) == 3
    rows = lib2.execute("SELECT spotify_id, external_ids FROM lib2_artists WHERE id IN (?, ?, ?)",
                        (ryo, lena, deezer)).fetchall()
    assert all(r["spotify_id"] is None and json.loads(r["external_ids"] or "{}") == {} for r in rows)


def test_the_album_artists_id_is_not_a_guests(lib2):
    # the class: any album artist's id, not only Various Artists. a guest's
    # song on someone's album took the album artist's id
    _without_the_floor(lib2)
    ctx = _wishlist_import("/music/T/x.flac", "End Game", "Ed Sheeran", "Reputation",
                           context_artist={"name": "Taylor Swift", "id": "sp-taylor"})
    ctx["album"]["artists"] = ctx["track_info"]["album"]["artists"] = [
        {"name": "Taylor Swift", "id": "sp-taylor"}]
    A.link_download_into_library_v2(ctx)
    assert [r["spotify_id"] for r in _artist_rows(lib2, "Ed Sheeran")] == [None]
    # the same through the context artist's qualified provider ids
    ctx = _wishlist_import("/music/T/y.flac", "Everything Has Changed", "Ed Sheeran 2", "Red",
                           context_artist={"name": "Taylor Swift",
                                           "provider_ids": {"spotify": "sp-taylor"}})
    A.link_download_into_library_v2(ctx)
    assert [r["spotify_id"] for r in _artist_rows(lib2, "Ed Sheeran 2")] == [None]


def test_a_real_id_still_lands(lib2):
    ctx = _wishlist_import("/music/ryo/x.flac", "Melt", "ryo (supercell)", MIKU,
                           context_artist={"name": "ryo (supercell)", "id": RYO_REAL})
    ctx["album"]["artists"] = ctx["track_info"]["album"]["artists"] = [
        {"name": "ryo (supercell)", "id": RYO_REAL}]
    A.link_download_into_library_v2(ctx)
    assert [r["spotify_id"] for r in _artist_rows(lib2, "ryo (supercell)")] == [RYO_REAL]


# --- the floor: no writer can store it ------------------------------------------

def test_no_artist_row_keeps_a_various_artists_id(lib2):
    cur = lib2.execute(
        "INSERT INTO lib2_artists(name, name_key, spotify_id, musicbrainz_id, external_ids) "
        "VALUES('ryo (supercell)', 'ryo (supercell)', ?, ?, ?)",
        (VA, VA_MBID, json.dumps({"deezer": 5080, "discogs": "194", "itunes": "27"})))
    row = lib2.execute("SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?",
                       (cur.lastrowid,)).fetchone()
    assert row["spotify_id"] is None and row["musicbrainz_id"] is None
    assert json.loads(row["external_ids"]) == {"itunes": "27"}

    lib2.execute("UPDATE lib2_artists SET spotify_id=?, external_ids=json_set(external_ids, '$.tidal', '2935') "
                 "WHERE id=?", (VA, cur.lastrowid))
    row = lib2.execute("SELECT spotify_id, external_ids FROM lib2_artists WHERE id=?",
                       (cur.lastrowid,)).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"]) == {"itunes": "27"}
    # a real id is left alone
    lib2.execute("UPDATE lib2_artists SET spotify_id=? WHERE id=?", (RYO_REAL, cur.lastrowid))
    assert lib2.execute("SELECT spotify_id FROM lib2_artists WHERE id=?",
                        (cur.lastrowid,)).fetchone()[0] == RYO_REAL


def test_the_importer_resolver_never_matches_or_stores_it(lib2):
    from core.library2.importer import _ArtistResolver
    _without_the_floor(lib2)
    resolver = _ArtistResolver(lib2.cursor(), 1)
    ryo = resolver.get_or_create_by_name("ryo (supercell)", provider_ids={"spotify": VA})
    lena = resolver.get_or_create_by_name("Lena Chappelle", provider_ids={"spotify": VA})
    assert ryo != lena
    assert lib2.execute("SELECT COUNT(*) FROM lib2_artists WHERE spotify_id=?", (VA,)).fetchone()[0] == 0


# --- the heal ---------------------------------------------------------------------

def _seed_reporters_library(conn):
    """the reporter's fresh database after the sync, as the bug left it"""
    conn.execute("DROP TRIGGER IF EXISTS trg_lib2_artist_no_various_artists_id_insert")
    conn.execute("DROP TRIGGER IF EXISTS trg_lib2_artist_no_various_artists_id_update")
    ids = {}
    for key, name, sp in (("ryo", "ryo (supercell)", VA), ("lena", "Lena Chappelle", None),
                          ("ryo2", "ryo (supercell)", RYO_REAL), ("va", "Various Artists", VA)):
        ids[key] = conn.execute(
            "INSERT INTO lib2_artists(name, name_key, spotify_id) VALUES(?, ?, ?)",
            (name, name.casefold(), sp)).lastrowid
    tracks = {}
    for key, title, credit, path in (
            ("melt", "Melt", "ryo (supercell)", "/music/VA/miku/03 - ryo (supercell) - Melt.flac"),
            ("tarir", "Attack on Tarir", "Lena Chappelle",
             "/music/VA/gw2/16 - ryo (supercell) - Attack on Tarir.flac")):
        album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, ?)",
                             (ids["va"], title + " album")).lastrowid
        track = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_artist) VALUES(?, ?, ?)",
                             (album, title, credit)).lastrowid
        # the compilation id pulled Lena's track in under ryo
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?, ?, 'primary', 0)", (track, ids["ryo"]))
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?, ?)", (track, path))
        tracks[key] = track
    conn.commit()
    return ids, tracks


def test_the_heal_gives_the_tracks_back_and_queues_findings(lib2):
    from core.library2.various_artists_guard import (
        ensure_various_artists_guard, heal_various_artists_ids, take_findings_followups)
    ids, tracks = _seed_reporters_library(lib2)

    stats = heal_various_artists_ids(lib2)
    ensure_various_artists_guard(lib2.cursor())
    lib2.commit()

    assert stats == {"artists": 2, "tracks": 1, "files": 1}
    assert lib2.execute("SELECT COUNT(*) FROM lib2_artists WHERE spotify_id=?", (VA,)).fetchone()[0] == 0
    # the real ryo keeps the real id
    assert lib2.execute("SELECT spotify_id FROM lib2_artists WHERE id=?", (ids["ryo2"],)).fetchone()[0] == RYO_REAL

    def credited(track):
        return [r[0] for r in lib2.execute(
            "SELECT artist_id FROM lib2_track_artists WHERE track_id=?", (track,))]
    assert credited(tracks["melt"]) == [ids["ryo"]]
    assert credited(tracks["tarir"]) == [ids["lena"]]

    waiting = take_findings_followups(lib2)
    assert waiting == {
        "library_retag": ["/music/VA/gw2/16 - ryo (supercell) - Attack on Tarir.flac"],
        "library_reorganize": ["/music/VA/gw2/16 - ryo (supercell) - Attack on Tarir.flac"],
    }
    assert take_findings_followups(lib2) == {}


def test_the_heal_runs_once_from_the_backfills(lib2):
    from core.library2.schema import run_library_v2_backfills
    _seed_reporters_library(lib2)
    first = run_library_v2_backfills(lib2)
    assert first["various_artists_ids"]["tracks"] == 1
    assert "various_artists_ids" not in run_library_v2_backfills(lib2)


def test_the_repair_worker_hands_the_files_over_findings_only(lib2, legacy_db):
    from core.library2.various_artists_guard import queue_findings_followup
    from core.repair_worker import RepairWorker
    _ids, tracks = _seed_reporters_library(lib2)
    queue_findings_followup(lib2, [tracks["tarir"]], reason="test")
    lib2.commit()

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = legacy_db
    worker._held = False
    queued = []
    worker.run_job_now = lambda job_id, scope=None, **_k: queued.append((job_id, scope)) or True
    assert worker._maybe_queue_findings_followups() == 2
    path = "/music/VA/gw2/16 - ryo (supercell) - Attack on Tarir.flac"
    assert sorted(queued) == [
        ("library_reorganize", {"file_paths": [path], "findings_only": True}),
        ("library_retag", {"file_paths": [path], "findings_only": True}),
    ]
    # the next tick is a minute away, and nothing is left
    assert worker._maybe_queue_findings_followups() == 0
    worker._followups_next = 0
    assert worker._maybe_queue_findings_followups() == 0


def test_a_findings_only_run_never_moves_or_writes(monkeypatch):
    from types import SimpleNamespace

    from core.repair_jobs.base import scope_findings_only
    from core.repair_jobs.library_reorganize import LibraryReorganizeJob

    class Config:
        def get(self, key, default=None):
            return {"dry_run": False} if key.endswith(".settings") else default

    live = SimpleNamespace(config_manager=Config(), scope={"file_paths": ["/a.flac"]})
    follow_up = SimpleNamespace(config_manager=Config(),
                                scope={"file_paths": ["/a.flac"], "findings_only": True})
    job = LibraryReorganizeJob()
    assert job._dry_run(live) is False
    assert job._dry_run(follow_up) is True
    assert scope_findings_only(follow_up) and not scope_findings_only(live)


# --- matching ---------------------------------------------------------------------

def test_a_watched_artist_is_never_matched_by_it():
    from core.library2.monitor_sync import _artist_identity_matches
    assert not _artist_identity_matches("Lena Chappelle", {"spotify": VA},
                                        "ryo (supercell)", {"spotify": VA})
    assert _artist_identity_matches("ryo", {"spotify": RYO_REAL}, "ryo (supercell)", {"spotify": RYO_REAL})


def test_provider_credits_skip_it():
    from core.library2.provider_credits import _credited
    artists = [{"name": "Various Artists", "id": VA}, {"name": "Lena Chappelle", "id": "sp-lena"}]
    assert _credited(artists, "spotify") == [("sp-lena", "Lena Chappelle")]
    assert _credited([{"name": "Various Artists", "id": 5080}], "deezer") == []
