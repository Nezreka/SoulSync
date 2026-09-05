"""The BYLT generation, end to end against a real (temporary) database.

the fixture IS the reported failure: a twelve-track Halogen album freshly
ingested into the pool, two seeds that are played but not watchlisted, and a
stale ordinal slot left over from an older run.
"""

import json

import pytest

from core.discovery import bylt_store
from core.discovery.bylt import SCHEMA_VERSION
from core.watchlist_scanner import WatchlistScanner
from database.music_database import MusicDatabase


def _pool(cur, artist, album, n, prefix, when="now"):
    for i in range(n):
        cur.execute(
            "INSERT INTO discovery_pool (deezer_track_id, source, track_name, "
            "artist_name, album_name, duration_ms, track_data_json, added_date, "
            "profile_id) VALUES (?,'deezer',?,?,?,180000,'{}',datetime(?),1)",
            (f"{prefix}{i}", f"{artist} {i}", artist, album, when))


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / "m.db"))
    conn = d._get_connection()
    cur = conn.cursor()
    # two seeds the user plays. NEITHER is on the watchlist - that is the point
    cur.execute("INSERT INTO artists (id, name, deezer_id, genres) "
                "VALUES (1,'Katy Perry','111','[\"pop\"]')")
    cur.execute("INSERT INTO artists (id, name, deezer_id, genres) "
                "VALUES (2,'Ariana Grande','222','[\"pop\"]')")
    for artist, plays in (("Katy Perry", 30), ("Ariana Grande", 20)):
        for _ in range(plays):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES ('x', ?, datetime('now','-1 hours'))", (artist,))
    for rank, name in enumerate(["Halogen", "Drama", "Ruth B.", "Fake Idols"], 1):
        cur.execute("INSERT INTO similar_artists (source_artist_id, source_provider, "
                    "similar_artist_name, similarity_rank, profile_id) "
                    "VALUES ('111','deezer',?,?,1)", (name, rank))
    for rank, name in enumerate(["Scorpixter", "Neon Bloom", "Halogen"], 1):
        cur.execute("INSERT INTO similar_artists (source_artist_id, source_provider, "
                    "similar_artist_name, similarity_rank, profile_id) "
                    "VALUES ('222','deezer',?,?,1)", (name, rank))
    # the album that ate both shelves
    _pool(cur, "Halogen", "Baked", 12, "h")
    _pool(cur, "Drama", "Nine One One", 3, "d")
    _pool(cur, "Ruth B.", "If By Chance", 3, "r")
    _pool(cur, "Scorpixter", "Perfect Girl", 4, "s")
    _pool(cur, "Neon Bloom", "Glass", 4, "n")
    conn.commit()
    conn.close()
    return d


def _scanner(db):
    sc = WatchlistScanner.__new__(WatchlistScanner)
    sc._database = db
    return sc


def _genres():
    return {"katy perry": {"pop"}, "ariana grande": {"pop"}, "halogen": {"pop"}}


def test_unwatchlisted_seeds_still_resolve_their_edges(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    names = [s["seed_name"] for s in gen["sections"]]
    assert names == ["Katy Perry", "Ariana Grande"]
    assert all(s["reason"]["kind"] == "direct" for s in gen["sections"])


def test_seed_sections_are_keyed_by_identity_not_ordinal(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    assert [s["seed_key"] for s in gen["sections"]] == ["deezer:111", "deezer:222"]


def test_one_album_cannot_dominate_either_shelf(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    for section in gen["sections"]:
        albums = [t["album_name"] for t in section["tracks"]]
        assert len(albums) == len(set(albums))
        assert albums.count("Baked") <= 1


def test_the_two_shelves_share_nothing(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    first = {t["track_id"] for t in gen["sections"][0]["tracks"]}
    second = {t["track_id"] for t in gen["sections"][1]["tracks"]}
    assert first & second == set()


def test_the_generation_is_versioned_scoped_and_stamped(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    assert gen["schema"] == SCHEMA_VERSION
    assert gen["profile_id"] == 1
    assert gen["source"] == "deezer"
    assert gen["generation_id"] and gen["generated_at"]
    assert gen["status"] == "ok"


def test_a_second_run_replaces_the_whole_set(db):
    sc = _scanner(db)
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    first = bylt_store.read_generation(db, 1)["generation_id"]
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    second = bylt_store.read_generation(db, 1)
    assert second["generation_id"] != first
    # exactly one stored record, never an old one beside a new one
    raw = db.get_curated_playlist(bylt_store.GENERATION_KEY, profile_id=1)
    assert len(raw) == 1


def test_fewer_seeds_means_fewer_shelves_not_a_stale_third(db):
    sc = _scanner(db)
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    assert len(bylt_store.read_generation(db, 1)["sections"]) == 2
    conn = db._get_connection()
    conn.execute("DELETE FROM listening_history WHERE artist = 'Ariana Grande'")
    conn.commit()
    conn.close()
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    assert [s["seed_name"] for s in gen["sections"]] == ["Katy Perry"]


def test_no_evidence_stores_an_explicit_empty_generation(db):
    conn = db._get_connection()
    conn.execute("DELETE FROM similar_artists")
    conn.execute("DELETE FROM discovery_pool")
    conn.commit()
    conn.close()
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    assert gen["status"] == "ok"
    assert gen["sections"] == []


def test_a_failed_run_keeps_the_last_good_generation(db, monkeypatch):
    sc = _scanner(db)
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    good = bylt_store.read_generation(db, 1)
    assert good["sections"]

    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(db, "get_similar_artist_edges", boom)
    sc._build_because_you_listen_to(1, ["deezer"], _genres())

    still = bylt_store.read_generation(db, 1)
    assert still["generation_id"] == good["generation_id"]
    failure = bylt_store.read_failure(db, 1)
    assert "provider exploded" in failure["message"]


def test_a_later_success_clears_the_failure_marker(db, monkeypatch):
    sc = _scanner(db)
    monkeypatch.setattr(db, "get_similar_artist_edges",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    assert bylt_store.read_failure(db, 1)
    monkeypatch.undo()
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    assert not bylt_store.read_failure(db, 1)


def test_legacy_slots_are_retired_only_for_this_profile(db):
    db.save_curated_playlist("because_you_listen_to_2", ["9884087"], profile_id=1)
    db.save_curated_playlist("because_you_listen_to_2", ["other"], profile_id=2)
    db.set_metadata("bylt_artist_2", "Ariana Grande")
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    assert not db.get_curated_playlist("because_you_listen_to_2", profile_id=1)
    assert db.get_curated_playlist("because_you_listen_to_2", profile_id=2) == ["other"]
    # the heading key is GLOBAL, so retiring must not touch it
    assert db.get_metadata("bylt_artist_2") == "Ariana Grande"


def test_two_profiles_do_not_overwrite_each_others_generations(db):
    sc = _scanner(db)
    sc._build_because_you_listen_to(1, ["deezer"], _genres())
    sc._build_because_you_listen_to(2, ["deezer"], _genres())
    one = bylt_store.read_generation(db, 1)
    two = bylt_store.read_generation(db, 2)
    assert one["profile_id"] == 1 and two["profile_id"] == 2
    assert one["generation_id"] != two["generation_id"]
    # profile 2 has no pool rows of its own, so it honestly has no shelves
    assert two["sections"] == []


def test_edges_from_a_colliding_provider_id_never_cross_seeds(db):
    conn = db._get_connection()
    cur = conn.cursor()
    # an iTunes artist whose id happens to equal Katy Perry's deezer id
    cur.execute("INSERT INTO artists (id, name, itunes_artist_id) "
                "VALUES (3,'Collider','111')")
    cur.execute("INSERT INTO similar_artists (source_artist_id, source_provider, "
                "similar_artist_name, similarity_rank, profile_id) "
                "VALUES ('111','itunes','Wrong Band',1,1)")
    conn.commit()
    conn.close()
    _pool_conn = db._get_connection()
    _pool(_pool_conn.cursor(), "Wrong Band", "Wrong", 4, "w")
    _pool_conn.commit()
    _pool_conn.close()

    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    katy = next(s for s in gen["sections"] if s["seed_name"] == "Katy Perry")
    assert "Wrong Band" not in {t["artist_name"] for t in katy["tracks"]}


def test_stored_rows_carry_full_display_metadata(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    row = gen["sections"][0]["tracks"][0]
    for field in ("track_id", "track_name", "artist_name", "album_name",
                  "duration_ms", "source", "relation", "seed_key"):
        assert field in row
    assert row["duration_ms"] == 180000


def test_diagnostics_record_the_edge_verdicts(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    edges = gen["sections"][0]["diagnostics"]["edges"]
    assert edges["provider"] >= 1
    assert "legacy-ambiguous" in edges


def test_generation_survives_a_json_round_trip(db):
    _scanner(db)._build_because_you_listen_to(1, ["deezer"], _genres())
    gen = bylt_store.read_generation(db, 1)
    assert json.loads(json.dumps(gen)) == gen


# ── B04: exact-id hydration, per supported source ───────────────────────────


@pytest.mark.parametrize("source,column", [
    ("spotify", "spotify_track_id"),
    ("itunes", "itunes_track_id"),
    ("deezer", "deezer_track_id"),
])
def test_saved_ids_hydrate_for_every_supported_source(tmp_path, source, column):
    """Contract test per source, as the review asks for.

    the shelf read used to resolve saved ids against the NEWEST 5,000 pool
    rows. anything older simply vanished, which is how a ten-track shelf could
    render three with no explanation. this asks for the ids themselves.
    """
    d = MusicDatabase(str(tmp_path / f"{source}.db"))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO discovery_pool ({column}, source, track_name, artist_name, "
        f"album_name, duration_ms, track_data_json, added_date, profile_id) "
        f"VALUES ('wanted', ?, 'Old Track', 'A', 'B', 1000, '{{}}', "
        f"datetime('now','-400 days'), 1)", (source,))
    # a wall of newer rows, so a recency window would push the wanted one out
    for i in range(50):
        cur.execute(
            f"INSERT INTO discovery_pool ({column}, source, track_name, artist_name, "
            f"album_name, duration_ms, track_data_json, added_date, profile_id) "
            f"VALUES (?, ?, 'New', 'N', 'M', 1000, '{{}}', datetime('now'), 1)",
            (f"new{i}", source))
    conn.commit()
    conn.close()

    out = d.get_discovery_pool_tracks_by_ids(["wanted", "gone"], source, profile_id=1)
    assert set(out) == {"wanted"}
    assert out["wanted"].track_name == "Old Track"


def test_hydration_is_scoped_to_the_profile_and_the_source(tmp_path):
    d = MusicDatabase(str(tmp_path / "scope.db"))
    conn = d._get_connection()
    conn.execute(
        "INSERT INTO discovery_pool (deezer_track_id, source, track_name, artist_name, "
        "album_name, duration_ms, track_data_json, added_date, profile_id) "
        "VALUES ('x','deezer','T','A','B',1000,'{}',datetime('now'),1)")
    conn.commit()
    conn.close()
    assert d.get_discovery_pool_tracks_by_ids(["x"], "deezer", profile_id=1)
    assert d.get_discovery_pool_tracks_by_ids(["x"], "deezer", profile_id=2) == {}
    # a spotify id and a deezer id are different namespaces, always
    assert d.get_discovery_pool_tracks_by_ids(["x"], "spotify", profile_id=1) == {}


def test_an_unsupported_source_hydrates_nothing_rather_than_guessing(tmp_path):
    d = MusicDatabase(str(tmp_path / "unsup.db"))
    assert d.get_discovery_pool_tracks_by_ids(["x"], "discogs", profile_id=1) == {}
    assert d.get_discovery_pool_tracks_by_ids([], "deezer", profile_id=1) == {}
