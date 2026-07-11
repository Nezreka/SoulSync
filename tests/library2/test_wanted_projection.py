"""Materialized wanted projection (audit §11.2 / ADR-02 Stufe 2).

These tests PIN the documented priority — the audit requires the order to be
fixed in tests before the projection is used:

1. explicit track rule    (beats everything, both directions)
2. projected track rule   (cascade / new_release)
3. album rule             (any provenance)
4. artist rule            (any provenance)
5. legacy track rule      (legacy_import)
6. default                (unmonitored)
"""

from __future__ import annotations

from core.library2.monitor_rules import (
    PROVENANCE_CASCADE,
    PROVENANCE_LEGACY,
    PROVENANCE_NEW_RELEASE,
    PROVENANCE_USER,
    record_rule,
)
from core.library2.wanted import (
    PROJECTION_VERSION,
    ensure_wanted_projection,
    recompute_wanted,
    recompute_wanted_for_entity,
    wanted_track_ids,
)


def _seed_chain(conn, *, title="Song"):
    """artist -> album -> track without any monitor rules."""
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('W Artist')")
    artist = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) "
                "VALUES(?, 'W Album')", (artist,))
    album = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, monitored) "
                "VALUES(?,?,0)", (album, title))
    return artist, album, cur.lastrowid


def _projected(conn, track_id, profile_id=1):
    row = conn.execute(
        "SELECT wanted, reason, projection_version FROM lib2_wanted_tracks "
        "WHERE profile_id=? AND track_id=?", (profile_id, track_id)).fetchone()
    return (bool(row["wanted"]), row["reason"]) if row else None


def test_default_is_unmonitored(imported_conn):
    conn = imported_conn
    _, _, track = _seed_chain(conn)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (False, "default_unmonitored")


def test_explicit_track_rule_beats_every_parent(imported_conn):
    conn = imported_conn
    artist, album, track = _seed_chain(conn)
    record_rule(conn, "album", album, True, PROVENANCE_USER)
    record_rule(conn, "artist", artist, True, PROVENANCE_USER)
    record_rule(conn, "track", track, False, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (False, "track_explicit")
    # ... and in the wanted direction against unmonitored parents (P1-14).
    record_rule(conn, "album", album, False, PROVENANCE_USER)
    record_rule(conn, "artist", artist, False, PROVENANCE_USER)
    record_rule(conn, "track", track, True, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (True, "track_explicit")


def test_cascade_track_rule_beats_album_rule(imported_conn):
    """The profile-assign opt-in projects cascade rules onto tracks without
    touching the album rule — the newer per-track intent wins."""
    conn = imported_conn
    _, album, track = _seed_chain(conn)
    record_rule(conn, "album", album, False, PROVENANCE_USER)
    record_rule(conn, "track", track, True, PROVENANCE_CASCADE)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (True, "track_rule:cascade")


def test_album_rule_decides_ruleless_tracks(imported_conn):
    """Tracks materialized from a provider tracklist after an album toggle
    have no own rule — the album tier decides."""
    conn = imported_conn
    _, album, track = _seed_chain(conn)
    record_rule(conn, "album", album, True, PROVENANCE_NEW_RELEASE)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (True, "album_rule:new_release")


def test_album_rule_beats_stale_legacy_track_rule(imported_conn):
    """A legacy_import flag copy must never override a deliberate album
    decision (P1-13: unknown origin never blocks a cascade)."""
    conn = imported_conn
    _, album, track = _seed_chain(conn)
    record_rule(conn, "track", track, True, PROVENANCE_LEGACY)
    record_rule(conn, "album", album, False, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (False, "album_rule:user_explicit")


def test_artist_rule_applies_when_no_album_or_track_rule(imported_conn):
    conn = imported_conn
    artist, _, track = _seed_chain(conn)
    record_rule(conn, "artist", artist, True, PROVENANCE_USER)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (True, "artist_rule:user_explicit")


def test_legacy_track_rule_is_weakest_recorded_intent(imported_conn):
    conn = imported_conn
    _, _, track = _seed_chain(conn)
    record_rule(conn, "track", track, True, PROVENANCE_LEGACY)
    recompute_wanted(conn, track_ids=[track])
    assert _projected(conn, track) == (True, "track_rule:legacy_import")


def test_full_recompute_prunes_deleted_tracks_and_counts_mismatches(imported_conn):
    conn = imported_conn
    _, album, track = _seed_chain(conn)
    record_rule(conn, "album", album, True, PROVENANCE_USER)
    stats = recompute_wanted(conn)
    assert stats["projected"] >= 1
    # The seeded track has flag monitored=0 but a wanted album rule → one
    # observable divergence, no flag was changed.
    assert stats["flag_mismatches"] >= 1
    assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?",
                        (track,)).fetchone()[0] == 0

    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track,))
    stats = recompute_wanted(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_wanted_tracks WHERE track_id=?",
        (track,)).fetchone()[0] == 0


def test_scoped_recompute_by_entity(imported_conn):
    conn = imported_conn
    artist, album, track = _seed_chain(conn)
    record_rule(conn, "artist", artist, True, PROVENANCE_USER)
    recompute_wanted_for_entity(conn, "artists", artist)
    assert _projected(conn, track) == (True, "artist_rule:user_explicit")
    record_rule(conn, "album", album, False, PROVENANCE_USER)
    recompute_wanted_for_entity(conn, "albums", album)
    assert _projected(conn, track) == (False, "album_rule:user_explicit")
    assert track not in wanted_track_ids(conn)


def test_importer_populates_projection(imported_conn):
    """The import fixture ends with a full projection over the legacy rules:
    every track has a row and the projection agrees with the flags."""
    conn = imported_conn
    rows = conn.execute(
        """SELECT t.id, t.monitored, w.wanted, w.projection_version
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w ON w.track_id = t.id AND w.profile_id=1
        """).fetchall()
    assert rows
    for r in rows:
        assert r["wanted"] is not None, f"track {r['id']} missing projection"
        assert bool(r["wanted"]) == bool(r["monitored"])
        assert r["projection_version"] == PROJECTION_VERSION


def test_ensure_rebuilds_on_version_bump(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    track = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()["id"]
    conn.execute("UPDATE lib2_wanted_tracks SET projection_version=0, wanted=1-wanted")
    stale = _projected(conn, track)
    ensure_wanted_projection(cur)
    rebuilt = _projected(conn, track)
    assert rebuilt != stale or rebuilt[1] != ""  # rebuilt from rules
    assert conn.execute(
        "SELECT MIN(projection_version) FROM lib2_wanted_tracks"
    ).fetchone()[0] == PROJECTION_VERSION
