"""§40: the periodic discography-refresh sweep must not treat alias-member
rows as their own sweep root — refresh_artist_discography already fans out
across the whole alias group when it processes the CANONICAL row, so an
alias row sweeping independently too would cost N^2 fetches per pass for an
N-member group instead of N."""

from __future__ import annotations

import sqlite3

from core.library2.schema import ensure_library_v2_schema
from core.repair_jobs.lib2_discography_refresh import Lib2DiscographyRefreshJob


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    conn.commit()
    return conn


def _artist(conn, name, *, monitored=1, monitor_new_items="all",
            synced=True, canonical_artist_id=None) -> int:
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, monitored, monitor_new_items, "
        "discography_synced_at, canonical_artist_id) VALUES(?,?,?,?,?)",
        (name, monitored, monitor_new_items,
         "2026-01-01T00:00:00" if synced else None, canonical_artist_id),
    )
    return cur.lastrowid


def test_alias_member_excluded_from_sweep_roots():
    conn = _conn()
    canonical = _artist(conn, "Canonical")
    alias = _artist(conn, "Alias", canonical_artist_id=canonical)
    conn.commit()

    ids = Lib2DiscographyRefreshJob()._artist_ids(conn)

    assert canonical in ids
    assert alias not in ids


def test_standalone_artists_unaffected():
    conn = _conn()
    standalone = _artist(conn, "Standalone")
    conn.commit()

    ids = Lib2DiscographyRefreshJob()._artist_ids(conn)

    assert ids == [standalone]


def test_existing_filters_still_apply_to_canonical_rows():
    conn = _conn()
    unmonitored = _artist(conn, "Unmonitored", monitored=0)
    none_policy = _artist(conn, "NonePolicy", monitor_new_items="none")
    eligible = _artist(conn, "Eligible")
    conn.commit()

    ids = Lib2DiscographyRefreshJob()._artist_ids(conn)

    assert ids == [eligible]
    assert unmonitored not in ids
    assert none_policy not in ids


def test_never_synced_monitored_artist_is_included():
    """A5 (library-overhaul-branch-review): a monitored artist that was only
    ever imported/watchlisted and never had "Update Discography" clicked
    must still get swept — its first fetch landing here instead of a manual
    click changes nothing, since _expand_artist_discography's own
    eligible_reexpansion gate (requires had_discography) already keeps a
    first-ever fetch from auto-monitoring the whole back catalog."""
    conn = _conn()
    never_synced = _artist(conn, "NeverSynced", synced=False)
    conn.commit()

    ids = Lib2DiscographyRefreshJob()._artist_ids(conn)

    assert ids == [never_synced]
