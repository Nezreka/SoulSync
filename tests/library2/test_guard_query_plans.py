"""Guard: no request scans a big catalogue table.

A third of review 5's findings were queries that read every row of a table
holding 300k rows on a real library (an 18 s wishlist removal, a 100 s Keep
Best, 4 s scan pages). Test databases hold a few dozen rows, so nothing looks
slow here. SQLite's plan doesn't need the rows, though: without statistics it
plans for a big table either way, so the plan of each query is the check.

This runs every Library v2 GET route as the admin, a household member and an
own library, records each SELECT it runs, asks SQLite for its plan, and fails
on a full scan of a big table that isn't in the baseline below. The baseline
is a ratchet: when a scan goes, take it out; a new one needs a fix (an index,
or a narrower query), not a new entry.
"""

from __future__ import annotations

import re
import threading
from collections import Counter

import pytest

from tests.library2.test_two_libraries_api import world  # noqa: F401  (fixture)

BIG_TABLES = {
    "lib2_tracks", "lib2_track_files", "lib2_albums", "lib2_artists",
    "lib2_release_tracks", "lib2_recordings", "lib2_release_editions",
    "lib2_monitor_rules", "lib2_wanted_tracks", "lib2_track_artists",
    "lib2_album_artists", "lib2_media_server_mappings", "lib2_provider_attempts",
    "track_downloads", "listening_history", "repair_findings",
}

# (route rule, table) -> how many full scans that route makes today. each is a
# known cost to take down, never a pattern to copy. filled from the first run
# on the review 5 fix branch.
BASELINE: dict = {
    # whole-library passes that are cached until the library changes (the
    # member-version counter); a request pays them once per change, not per
    # call (review 5 perf-2)
    ("/api/library/v2/albums", "lib2_albums"): 4,
    ("/api/library/v2/artists", "lib2_artists"): 1,
    ("/api/library/v2/wanted-projection/status", "lib2_tracks"): 3,
    # the library picker's per-library file counts, one pass
    ("/api/library/v2/scopes", "lib2_track_files"): 1,
    # track_downloads is the download log: 5k rows on a 300k-track library,
    # 0.03 s for the whole scan (review 5 F, measured on the live DB)
    ("/api/library/v2/artists/<int:artist_id>/history", "track_downloads"): 2,
    ("/api/library/v2/tracks/<int:track_id>", "track_downloads"): 2,
    ("/api/library/v2/tracks/<int:track_id>/source-info", "track_downloads"): 2,
    ("/api/library/v2/tracks/<int:track_id>/versions", "track_downloads"): 2,
}

_FROM = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)(?:\s+(?:AS\s+)?([A-Za-z_]\w*))?", re.I)
_SQL_WORDS = {"on", "where", "join", "left", "inner", "cross", "group", "order", "limit",
              "using", "natural", "union", "and", "or", "set", "values", "as", "outer"}
_SKIP_ARGS = {"job_id", "kind", "token", "filename", "path", "service", "source"}


def _aliases(sql: str) -> dict:
    out = {}
    for table, alias in _FROM.findall(sql):
        out.setdefault(table.lower(), table.lower())
        if alias and alias.lower() not in _SQL_WORDS:
            out[alias.lower()] = table.lower()
    return out


def _big_scans(conn, sql: str):
    """the big tables this statement reads in full"""
    try:
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    except Exception:  # noqa: BLE001 - a statement that only parses in context
        return []
    names = _aliases(sql)
    scans = []
    for row in plan:
        detail = str(row[-1])
        m = re.match(r"SCAN (\w+)", detail)
        # "SCAN x USING [COVERING] INDEX" walks an index (a sorted page
        # walk stops after its LIMIT); a bare "SCAN x" reads every row
        if not m or "USING" in detail:
            continue
        table = names.get(m.group(1).lower(), m.group(1).lower())
        if table in BIG_TABLES:
            scans.append(table)
    return scans


def _route_urls(app, ids):
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if "GET" not in rule.methods or not rule.rule.startswith("/api/library/v2"):
            continue
        if set(rule.arguments) & _SKIP_ARGS:
            continue
        url = rule.rule
        for arg in rule.arguments:
            value = ids.get(arg)
            if value is None:
                break
            url = re.sub(r"<(?:[a-z]+:)?%s>" % arg, str(value), url)
        else:
            yield rule.rule, url


def test_no_library_request_scans_a_big_table(world, monkeypatch):
    db = world.db
    with db._get_connection() as conn:
        ids = {
            "artist_id": world.ids["h"],
            "album_id": conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                                     (world.ids["h"],)).fetchone()[0],
            "track_id": conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0],
            "file_id": conn.execute("SELECT id FROM lib2_track_files LIMIT 1").fetchone()[0],
            "profile_id": 1,
        }
    ids["entity_id"] = ids["artist_id"]
    ids["id"] = ids["artist_id"]

    statements: list = []
    real_get = type(db)._get_connection
    # the test client serves a request on this thread. a background worker an
    # earlier test left running (the enrichment backlog walks, say) opens its
    # own connections on its own thread; its queries are no request's cost.
    request_thread = threading.get_ident()

    def traced(self, *a, **k):
        conn = real_get(self, *a, **k)
        if threading.get_ident() == request_thread:
            try:
                conn.set_trace_callback(statements.append)
            except Exception:  # noqa: BLE001
                pass
        return conn

    monkeypatch.setattr(type(db), "_get_connection", traced)
    # the art pickers ask cover art archive, deezer and itunes; the sweep
    # checks queries, never the network
    import core.metadata.art_lookup as art_lookup
    import core.metadata.artist_image as artist_image
    monkeypatch.setattr(art_lookup, "gather_album_art_candidates", lambda *a, **k: [])
    monkeypatch.setattr(artist_image, "gather_artist_image_candidates", lambda *a, **k: [])
    app = world.client.application
    found: Counter = Counter()
    for profile in (1, world.sam, world.kim):
        world.as_profile(profile)
        for rule, url in _route_urls(app, ids):
            statements.clear()
            world.client.get(url)
            seen = list(statements)
            with real_get(db) as plan_conn:
                for sql in seen:
                    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
                    if head not in ("SELECT", "WITH"):
                        continue
                    for table in _big_scans(plan_conn, sql):
                        found[(rule, table)] += 1
    over = {key: (n, BASELINE.get(key, 0)) for key, n in found.items()
            if n > BASELINE.get(key, 0)}
    assert not over, (
        "full scans of a big table on a request path (route, table): (now, "
        "baseline). each reads every row of a 300k-row table on a real "
        "library; give it an index or a narrower query:\n"
        + "\n".join(f"{k}: {v}" for k, v in sorted(over.items())))


def test_the_plan_check_sees_a_scan(tmp_path):
    """negative control"""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "x.db"))
    conn.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, title TEXT)")
    assert _big_scans(conn, "SELECT id FROM lib2_tracks t WHERE lower(t.title)='x'") == ["lib2_tracks"]
    assert _big_scans(conn, "SELECT id FROM lib2_tracks WHERE id=1") == []


def test_lock_reads_do_not_scan_a_big_table(world, monkeypatch):
    """#2046: the padlock's reads with a lock in place (the sweep above runs
    with nothing locked, so the review and drift queries never fire there),
    and the reviewed filters on the artist list."""
    from core.library2 import locks

    db = world.db
    locks.reset_for_tests()
    with db._get_connection() as conn:
        artist_id = world.ids["h"]
        album_id = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id=?",
                                (artist_id,)).fetchone()[0]
        locks.set_lock(conn, "album", album_id)
        locks.set_lock(conn, "artist", artist_id)
        conn.commit()

    statements: list = []
    real_get = type(db)._get_connection
    request_thread = threading.get_ident()

    def traced(self, *a, **k):
        conn = real_get(self, *a, **k)
        if threading.get_ident() == request_thread:
            try:
                conn.set_trace_callback(statements.append)
            except Exception:  # noqa: BLE001
                pass
        return conn

    monkeypatch.setattr(type(db), "_get_connection", traced)
    world.as_profile(1)
    urls = [
        f"/api/library/v2/albums/{album_id}/lock",
        f"/api/library/v2/artists/{artist_id}/lock",
        f"/api/library/v2/artists/{artist_id}/review?after={album_id}",
        "/api/library/v2/locks",
        "/api/library/v2/artists?review=reviewed",
        "/api/library/v2/artists?review=unreviewed",
    ]
    found: Counter = Counter()
    try:
        for url in urls:
            statements.clear()
            assert world.client.get(url).status_code == 200, url
            seen = list(statements)
            with real_get(db) as plan_conn:
                for sql in seen:
                    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
                    if head in ("SELECT", "WITH"):
                        for table in _big_scans(plan_conn, sql):
                            found[(url.split("?")[0] + ("?review" if "review=" in url else ""),
                                   table)] += 1
        # the automation side: the files of locked albums, and the enrichment queue
        with real_get(db) as plan_conn:
            for sql in (
                "SELECT f.path, t.album_id FROM lib2_entity_locks lk "
                "JOIN lib2_tracks t ON t.album_id=lk.entity_id "
                "JOIN lib2_track_files f ON f.track_id=t.id "
                "WHERE lk.entity_type='album' AND f.path IS NOT NULL AND f.path<>''",
                f"SELECT e.id FROM lib2_tracks e WHERE e.id=1 AND "
                f"{locks.unlocked_sql('track', 'e', 'metadata')}",
            ):
                for table in _big_scans(plan_conn, sql):
                    found[("automation", table)] += 1
    finally:
        locks.reset_for_tests()
    # the plain artist list reads lib2_artists once already (BASELINE above)
    allowed = {("/api/library/v2/artists?review", "lib2_artists"): 2}
    over = {key: n for key, n in found.items() if n > allowed.get(key, 0)}
    assert not over, over
