"""Tests for core/maintenance/dedupe_source_ids.py — the one-off repair for
source ids that enrichment wrongly shared across multiple artists.

Corruption = one source id on artists with DIFFERENT names. Legit duplicates =
the SAME artist on two media servers, same name — must be left alone.
"""

from __future__ import annotations

import pytest

from core.maintenance import dedupe_source_ids as dd
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, *, artist_id, name, **extra):
    import json
    external = {source: extra[f'{source}_id'] for source in dd.SOURCES
                if source not in ('spotify', 'musicbrainz') and extra.get(f'{source}_id')}
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists(id,name,name_key,server_source,spotify_id,"
            "musicbrainz_id,external_ids) VALUES(?,?,?,?,?,?,?)",
            (artist_id, name, name.casefold(), extra.get('server_source', 'plex'),
             extra.get('spotify_artist_id'), extra.get('musicbrainz_id'),
             json.dumps(external)),
        )
        for source in dd.SOURCES:
            if extra.get(f'{source}_match_status'):
                conn.execute(
                    "INSERT INTO lib2_provider_attempts(entity_type,entity_id,service,status) "
                    "VALUES('artist',?,?,?)",
                    (artist_id, source, extra[f'{source}_match_status']))
        conn.commit()


def _get(db, artist_id, col):
    source = col.removesuffix('_match_status')
    with db._get_connection() as conn:
        if col.endswith('_match_status'):
            row = conn.execute(
                "SELECT status FROM lib2_provider_attempts WHERE entity_type='artist' "
                "AND entity_id=? AND service=?", (artist_id, source)).fetchone()
            return row[0] if row else None
        from core.library2.provider_ids import provider_id_sql
        provider = {'spotify_artist_id': 'spotify'}.get(col, col.removesuffix('_id'))
        return conn.execute(
            f"SELECT {provider_id_sql(provider)} FROM lib2_artists WHERE id=?",
            (artist_id,)).fetchone()[0]


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
        conn.execute("UPDATE lib2_artists SET server_source='jellyfin' WHERE id='4'")
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
# Post-import repair
# ---------------------------------------------------------------------------

def test_post_import_repair_clears_shared_source_ids(db):
    _insert(db, artist_id="1", name="Kendrick", deezer_id="525046")
    _insert(db, artist_id="2", name="Jorja", deezer_id="525046")
    _insert(db, artist_id="3", name="Radiohead", spotify_artist_id="rh")
    _insert(db, artist_id="4", name="Radiohead", spotify_artist_id="rh")

    dd.repair_imported_state(db)

    assert _get(db, "1", "deezer_id") is None
    assert _get(db, "2", "deezer_id") is None
    assert _get(db, "3", "spotify_artist_id") == "rh"
    assert _get(db, "4", "spotify_artist_id") == "rh"


def test_post_import_repair_resets_old_genius_and_artist_soul_ids_once(db):
    _insert(db, artist_id="1", name="Artist", genius_id="bad")
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_artists SET soul_id='old', enrichment=? WHERE id=1",
                     ('{"genius":{"bio":"bad"}}',))
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(1,'Album')"
        ).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,external_ids,enrichment) VALUES(?,'Track',?,?)",
            (album_id, '{"genius":"bad"}', '{"genius":{"text":"bad"}}'),
        ).lastrowid
        conn.execute("INSERT INTO lib2_provider_attempts VALUES('track',?,'genius','matched',1,CURRENT_TIMESTAMP,NULL)",
                     (track_id,))
        conn.commit()

    report = dd.repair_imported_state(db)
    assert report['genius_reset'] is True
    assert report['soul_ids_cleared'] == 1
    with db._get_connection() as conn:
        artist = conn.execute(
            "SELECT soul_id,json_extract(external_ids,'$.genius') FROM lib2_artists WHERE id=1"
        ).fetchone()
        track = conn.execute(
            "SELECT json_extract(external_ids,'$.genius') FROM lib2_tracks WHERE id=?", (track_id,)
        ).fetchone()
        assert tuple(artist) == (None, None)
        assert track[0] is None
        assert conn.execute("SELECT 1 FROM lib2_provider_attempts WHERE service='genius'").fetchone() is None
        conn.execute("UPDATE lib2_artists SET external_ids=? WHERE id=1", ('{"genius":"new"}',))
        conn.commit()

    dd.repair_imported_state(db)
    assert _get(db, "1", "genius_id") == "new"
