"""Tests for core/maintenance/dedupe_source_ids.py — the one-off repair for
source ids that enrichment wrongly shared across multiple artists.

Corruption = one source id on artists with DIFFERENT names. Legit duplicates =
the SAME artist on two media servers, same name — must be left alone.
"""

from __future__ import annotations

import pytest

import database.music_database as mdb_mod
from core.maintenance import dedupe_source_ids as dd
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, *, artist_id, name, **extra):
    cols = ["id", "name", "server_source"] + list(extra.keys())
    vals = [artist_id, name, "plex"] + list(extra.values())
    placeholders = ",".join("?" for _ in cols)
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO artists ({','.join(cols)}) VALUES ({placeholders})", vals
        )
        conn.commit()


def _get(db, artist_id, col):
    with db._get_connection() as conn:
        return conn.execute(f"SELECT {col} FROM artists WHERE id=?", (artist_id,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detects_different_name_cluster_as_corrupt(db):
    _insert(db, artist_id="1", name="Kendrick Lamar", deezer_id="525046")
    _insert(db, artist_id="2", name="Jorja Smith", deezer_id="525046")
    _insert(db, artist_id="3", name="Vince Staples", deezer_id="525046")

    clusters = dd.find_corrupt_clusters(db)
    assert len(clusters) == 1
    c = clusters[0]
    assert c['source'] == 'deezer'
    assert c['source_id'] == '525046'
    assert {n for _, n in c['members']} == {"Kendrick Lamar", "Jorja Smith", "Vince Staples"}


def test_same_name_duplicate_is_not_corrupt(db):
    # Same artist on two servers — legit shared id, must be ignored.
    _insert(db, artist_id="10", name="Radiohead", deezer_id="999")
    _insert(db, artist_id="11", name="radiohead", deezer_id="999")  # case-insensitive
    assert dd.find_corrupt_clusters(db) == []


def test_unique_ids_are_not_corrupt(db):
    _insert(db, artist_id="20", name="A", deezer_id="1")
    _insert(db, artist_id="21", name="B", deezer_id="2")
    assert dd.find_corrupt_clusters(db) == []


def test_detects_corruption_across_multiple_sources(db):
    _insert(db, artist_id="1", name="Kendrick", deezer_id="525046", spotify_artist_id="sp-x")
    _insert(db, artist_id="2", name="Jorja", deezer_id="525046")
    _insert(db, artist_id="3", name="Someone", spotify_artist_id="sp-x")
    sources = {c['source'] for c in dd.find_corrupt_clusters(db)}
    assert sources == {'deezer', 'spotify'}


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(db):
    _insert(db, artist_id="1", name="Kendrick", deezer_id="525046", deezer_match_status="matched")
    _insert(db, artist_id="2", name="Jorja", deezer_id="525046", deezer_match_status="matched")

    report = dd.clear_corrupt_source_ids(db, dry_run=True)
    assert report['dry_run'] is True
    assert report['cluster_count'] == 1
    assert report['artist_count'] == 2
    assert report['by_source'] == {'deezer': 2}
    # Nothing changed.
    assert _get(db, "1", "deezer_id") == "525046"
    assert _get(db, "2", "deezer_id") == "525046"


def test_apply_clears_id_and_status_for_corrupt_rows(db):
    _insert(db, artist_id="1", name="Kendrick", deezer_id="525046", deezer_match_status="matched")
    _insert(db, artist_id="2", name="Jorja", deezer_id="525046", deezer_match_status="matched")

    report = dd.clear_corrupt_source_ids(db, dry_run=False)
    assert report['dry_run'] is False
    assert report['artist_count'] == 2
    # Both cleared so the (now name-checked) worker re-derives them.
    for aid in ("1", "2"):
        assert _get(db, aid, "deezer_id") is None
        assert _get(db, aid, "deezer_match_status") is None


def test_apply_leaves_legit_duplicates_untouched(db):
    # Corrupt deezer cluster + a legit same-name spotify duplicate.
    _insert(db, artist_id="1", name="Kendrick", deezer_id="525046")
    _insert(db, artist_id="2", name="Jorja", deezer_id="525046")
    _insert(db, artist_id="3", name="Radiohead", spotify_artist_id="rh", server_source="plex")
    _insert(db, artist_id="4", name="Radiohead", spotify_artist_id="rh", server_source="jellyfin")

    with db._get_connection() as conn:
        conn.execute("UPDATE artists SET server_source='jellyfin' WHERE id='4'")
        conn.commit()

    dd.clear_corrupt_source_ids(db, dry_run=False)
    # Corrupt deezer ids cleared…
    assert _get(db, "1", "deezer_id") is None
    assert _get(db, "2", "deezer_id") is None
    # …legit same-name spotify duplicate preserved.
    assert _get(db, "3", "spotify_artist_id") == "rh"
    assert _get(db, "4", "spotify_artist_id") == "rh"


def test_clean_library_is_a_noop(db):
    _insert(db, artist_id="1", name="A", deezer_id="1")
    _insert(db, artist_id="2", name="B", deezer_id="2")
    report = dd.clear_corrupt_source_ids(db, dry_run=False)
    assert report['cluster_count'] == 0
    assert report['artist_count'] == 0


# ---------------------------------------------------------------------------
# The one-time startup migration (auto-repair for users who pull the fix)
# ---------------------------------------------------------------------------

def test_startup_migration_clears_shared_source_ids(tmp_path):
    """The _source_id_dedupe_v1 migration in MusicDatabase init must clear
    differently-named shared ids and leave same-name cross-server dups."""
    path = str(tmp_path / "music.db")
    db = MusicDatabase(path)  # first init creates the marker on an empty db

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id,name,server_source,deezer_id,deezer_match_status) "
                     "VALUES ('1','Kendrick Lamar','plex','525046','matched')")
        conn.execute("INSERT INTO artists (id,name,server_source,deezer_id,deezer_match_status) "
                     "VALUES ('2','Jorja Smith','plex','525046','matched')")
        # Legit same-name dup across two servers — must survive.
        conn.execute("INSERT INTO artists (id,name,server_source,spotify_artist_id) "
                     "VALUES ('3','Radiohead','plex','rh')")
        conn.execute("INSERT INTO artists (id,name,server_source,spotify_artist_id) "
                     "VALUES ('4','Radiohead','jellyfin','rh')")
        conn.execute("DROP TABLE _source_id_dedupe_v1")
        conn.commit()

    # Force the one-time migration to run again.
    mdb_mod._database_initialized_paths.clear()
    MusicDatabase(path)

    with db._get_connection() as conn:
        k = conn.execute("SELECT deezer_id, deezer_match_status FROM artists WHERE id='1'").fetchone()
        assert tuple(k) == (None, None)
        assert conn.execute("SELECT deezer_id FROM artists WHERE id='2'").fetchone()[0] is None
        rh = conn.execute("SELECT spotify_artist_id FROM artists WHERE id IN ('3','4')").fetchall()
        assert all(r[0] == 'rh' for r in rh)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='_source_id_dedupe_v1'").fetchone()
