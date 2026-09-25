"""The artists page's watchlist filter stays scoped to the calling profile.

Guide §2.6: Library v2 owns a single global, admin-controlled monitoring intent,
while other household profiles keep their own legacy watchlist. The legacy
reader honoured that — ``get_library_artists`` matches each artist against
``watchlist_artists`` rows *for the given profile_id*, by Spotify id, iTunes id
or lowercased name.

When iss32-E03 moved the endpoint onto lib2, the filter became
``a.monitored``: the admin's global flag, for every caller. Nobody passed a
profile any more. That is a silent change of meaning for every non-admin
profile, so it is pinned here from both sides — the filter and the reported
``is_watched`` — before ``api/library.py`` is moved onto the same function.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.queries import legacy_api_artists_page
from core.library2.schema import ensure_library_v2_schema

ADMIN, GUEST = 1, 2


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.execute(
        """
        CREATE TABLE watchlist_artists(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spotify_artist_id TEXT, itunes_artist_id TEXT,
            artist_name TEXT NOT NULL, profile_id INTEGER DEFAULT 1
        )
        """
    )
    yield c
    c.close()


def _artist(conn, name, *, legacy_id, monitored=0, **columns):
    columns.setdefault("sort_name", name)
    cols = ", ".join(["name", "legacy_artist_id", "monitored", *columns])
    holes = ", ".join("?" for _ in range(len(columns) + 3))
    return conn.execute(
        f"INSERT INTO lib2_artists({cols}) VALUES({holes})",
        (name, legacy_id, monitored, *columns.values()),
    ).lastrowid


def _watch(conn, name, *, profile_id, spotify=None, itunes=None):
    conn.execute(
        "INSERT INTO watchlist_artists(artist_name, spotify_artist_id, "
        "itunes_artist_id, profile_id) VALUES(?,?,?,?)",
        (name, spotify, itunes, profile_id),
    )


def _names(page):
    return [artist["name"] for artist in page["artists"]]


def test_watched_is_filtered_per_profile(conn):
    _artist(conn, "Massive Attack", legacy_id=10)
    _artist(conn, "Portishead", legacy_id=11)
    _watch(conn, "Massive Attack", profile_id=ADMIN)
    _watch(conn, "Portishead", profile_id=GUEST)
    conn.commit()

    admin = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=ADMIN)
    guest = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=GUEST)

    assert _names(admin) == ["Massive Attack"]
    assert _names(guest) == ["Portishead"]


def test_is_watched_follows_the_profile_not_the_global_monitor_flag(conn):
    """The admin's lib2 ``monitored`` flag is not the guest's watchlist. Serving
    it as ``is_watched`` tells a guest their library is monitored when it is the
    admin's intent they are looking at."""
    _artist(conn, "Massive Attack", legacy_id=10, monitored=1)
    conn.commit()

    guest = legacy_api_artists_page(conn, profile_id=GUEST)["artists"][0]
    assert guest["is_watched"] is False

    _watch(conn, "Massive Attack", profile_id=GUEST)
    conn.commit()
    guest = legacy_api_artists_page(conn, profile_id=GUEST)["artists"][0]
    assert guest["is_watched"] is True


def test_a_watchlist_row_matches_by_spotify_id(conn):
    _artist(conn, "Renamed Since", legacy_id=10, spotify_id="sp-1")
    _watch(conn, "Massive Attack", profile_id=ADMIN, spotify="sp-1")
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=ADMIN)

    assert _names(page) == ["Renamed Since"]


def test_a_watchlist_row_matches_a_provider_id_held_only_in_external_ids(conn):
    """lib2 promotes Spotify and MusicBrainz to columns; every other provider
    id, iTunes included, lives in ``external_ids`` only."""
    _artist(conn, "Portishead", legacy_id=11,
            external_ids=json.dumps({"itunes": "it-9"}))
    _watch(conn, "Something Else", profile_id=ADMIN, itunes="it-9")
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=ADMIN)

    assert _names(page) == ["Portishead"]


def test_the_name_match_ignores_case(conn):
    _artist(conn, "Massive Attack", legacy_id=10)
    _watch(conn, "massive attack", profile_id=ADMIN)
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=ADMIN)

    assert _names(page) == ["Massive Attack"]


def test_unwatched_is_the_exact_complement(conn):
    _artist(conn, "Massive Attack", legacy_id=10)
    _artist(conn, "Portishead", legacy_id=11)
    _watch(conn, "Massive Attack", profile_id=ADMIN)
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="unwatched", profile_id=ADMIN)

    assert _names(page) == ["Portishead"]


def test_an_empty_watchlist_can_match_nothing(conn):
    _artist(conn, "Massive Attack", legacy_id=10, monitored=1)
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=GUEST)

    assert page["artists"] == []
    assert page["pagination"]["total_count"] == 0


def test_the_filter_and_the_reported_flag_cannot_disagree(conn):
    """One page, both readings: everything a ``watched`` page returns must
    report ``is_watched``. The filter runs in SQL for pagination and the flag is
    evaluated per row, so this is the invariant that keeps them one rule."""
    _artist(conn, "By Name", legacy_id=10)
    _artist(conn, "By Spotify", legacy_id=11, spotify_id="sp-2")
    _artist(conn, "By iTunes", legacy_id=12,
            external_ids=json.dumps({"itunes": "it-3"}))
    _artist(conn, "Not Watched", legacy_id=13, monitored=1)
    _watch(conn, "By Name", profile_id=ADMIN)
    _watch(conn, "x", profile_id=ADMIN, spotify="sp-2")
    _watch(conn, "y", profile_id=ADMIN, itunes="it-3")
    conn.commit()

    page = legacy_api_artists_page(conn, watchlist_filter="watched", profile_id=ADMIN)

    assert len(page["artists"]) == 3
    assert all(artist["is_watched"] for artist in page["artists"])


def test_a_missing_watchlist_table_means_nothing_is_watched(conn):
    """A fresh install can read the catalogue before the legacy watchlist table
    exists. Falling back to ``monitored`` there is what produced the regression
    this module exists for, so the honest reading is an empty watchlist."""
    conn.execute("DROP TABLE watchlist_artists")
    _artist(conn, "Massive Attack", legacy_id=10, monitored=1)
    conn.commit()

    page = legacy_api_artists_page(conn, profile_id=ADMIN)

    assert page["artists"][0]["is_watched"] is False
