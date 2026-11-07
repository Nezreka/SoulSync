"""X1: a household member's watchlist is their own watch state in Library v2.

3.6.1 gave every profile its own watchlist. v2 kept that page per profile
but the Library read and wrote only the admin's state, so a member saw the
admin's watched artists on every card and could not watch from it.
"""

from __future__ import annotations

from database.music_database import MusicDatabase
from core.library2 import monitor_sync


def _db(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    sam = db.create_profile("Sam")
    with db._get_connection() as conn:
        artist = conn.execute("INSERT INTO lib2_artists(name, name_key, spotify_id) "
                              "VALUES('Portishead','portishead','sp-porti')").lastrowid
        conn.commit()
    return db, sam, artist


def _rule(db, artist, profile):
    with db._get_connection() as conn:
        row = conn.execute("SELECT monitored, provenance FROM lib2_monitor_rules WHERE entity_type='artist'"
                           " AND entity_id=? AND profile_id=?", (artist, profile)).fetchone()
        return tuple(row) if row else None


def _column(db, artist):
    with db._get_connection() as conn:
        return conn.execute("SELECT monitored FROM lib2_artists WHERE id=?", (artist,)).fetchone()[0]


def test_a_member_adding_from_the_watchlist_page_watches_on_their_own(tmp_path):
    db, sam, artist = _db(tmp_path)
    assert monitor_sync.sync_watchlist_addition(db, name="Portishead", provider_id="sp-porti",
                                                source="spotify", profile_id=sam) == artist
    assert _rule(db, artist, sam)[0] == 1
    assert _rule(db, artist, 1) is None
    assert not _column(db, artist), "the shared column is the admin's"


def test_a_member_never_adds_catalogue_artists(tmp_path):
    db, sam, _artist = _db(tmp_path)
    assert monitor_sync.sync_watchlist_addition(db, name="Somebody New", profile_id=sam) is None
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE name='Somebody New'").fetchone()[0] == 0


def test_a_member_removing_drops_their_rule_and_leaves_the_admins(tmp_path):
    db, sam, artist = _db(tmp_path)
    monitor_sync.sync_watchlist_addition(db, name="Portishead", provider_id="sp-porti",
                                         source="spotify", profile_id=1)
    monitor_sync.sync_watchlist_addition(db, name="Portishead", provider_id="sp-porti",
                                         source="spotify", profile_id=sam)
    monitor_sync.sync_watchlist_removal(db, None, {"name": "Portishead",
                                                   "provider_ids": {"spotify": "sp-porti"}}, profile_id=sam)
    assert _rule(db, artist, sam)[0] == 0
    assert _rule(db, artist, 1)[0] == 1
    assert _column(db, artist) == 1


def test_household_watchlists_carry_over_once(tmp_path):
    db, sam, artist = _db(tmp_path)
    db.add_artist_to_watchlist("sp-porti", "Portishead", sam, "spotify")
    with db._get_connection() as conn:
        stats = monitor_sync.carry_member_watchlists(conn)
    assert stats == {"profiles": 1, "artists": 1}
    assert _rule(db, artist, sam) == (1, "legacy_import")
    assert _rule(db, artist, 1) is None
    with db._get_connection() as conn:
        assert monitor_sync.carry_member_watchlists(conn) == {"profiles": 0, "artists": 0}
