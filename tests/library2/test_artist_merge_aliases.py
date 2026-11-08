"""the duplicate-artist repair never leaves an artist pointing at itself.

found in the second #1062 review: the survivor of two artists sharing a
provider id was picked by how many ids it held before whether it was an
alias. an alias with more ids than its own canonical survived, and the
alias re-parenting then pointed it at itself: canonical_artist_id = its own
id, so it (and every album it now held) left the Library artist list, and
linking to it was refused ("target is itself an alias").
"""

from __future__ import annotations

import json

from core.library2.dedup_repair import repair_duplicate_artists


def _artist(conn, name, *, canonical=None, spotify=None, deezer=None):
    ext = {k: v for k, v in (("spotify", spotify), ("deezer", deezer)) if v}
    return conn.execute(
        "INSERT INTO lib2_artists(name, name_key, canonical_artist_id, spotify_id, external_ids)"
        " VALUES(?,?,?,?,?)",
        (name, name.casefold(), canonical, spotify, json.dumps(ext))).lastrowid


def _album(conn, artist_id, title):
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                         (artist_id, title)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)", (album, artist_id))
    return album


def _rows(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT id, canonical_artist_id FROM lib2_artists")}


def test_an_alias_with_more_ids_never_survives_its_own_canonical(imported_conn, legacy_db):
    conn = imported_conn
    canonical = _artist(conn, "Hikaru Utada", spotify="sp-utada")
    alias = _artist(conn, "宇多田ヒカル", canonical=canonical, spotify="sp-utada", deezer="dz-utada")
    fan_alias = _artist(conn, "Utada", canonical=alias)
    album = _album(conn, alias, "First Love")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    rows = _rows(conn)
    assert all(parent != aid for aid, parent in rows.items())
    assert canonical in rows and rows[canonical] is None and alias not in rows
    assert rows.get(fan_alias) == canonical  # re-parented onto the root, not a chain
    assert conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?",
                        (album,)).fetchone()[0] == canonical
    ids = json.loads(conn.execute("SELECT external_ids FROM lib2_artists WHERE id=?",
                                  (canonical,)).fetchone()[0])
    assert ids.get("deezer") == "dz-utada"


def test_a_survivor_that_was_an_alias_of_the_duplicate_is_freed(imported_conn, legacy_db):
    """two aliases of one root share an id: whichever survives, nothing ends
    up pointing at itself or at the row that was merged away"""
    conn = imported_conn
    root = _artist(conn, "Root Artist")
    first = _artist(conn, "Alias One", canonical=root, spotify="sp-x", deezer="dz-x")
    second = _artist(conn, "Alias Two", canonical=root, spotify="sp-x")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    rows = _rows(conn)
    survivors = [a for a in (first, second) if a in rows]
    assert len(survivors) == 1
    assert rows[survivors[0]] == root
    assert all(parent != aid for aid, parent in rows.items())


def test_a_hand_made_monitor_decision_survives_the_merge(imported_conn, legacy_db):
    conn = imported_conn
    keep = _artist(conn, "Odetari", spotify="sp-odetari")
    fragment = _artist(conn, "Odetari w", spotify="sp-odetari")
    conn.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id, monitored,"
                 " provenance) VALUES('artist', ?, 1, 0, 'legacy_import')", (keep,))
    conn.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id, monitored,"
                 " provenance) VALUES('artist', ?, 1, 1, 'user_explicit')", (fragment,))
    conn.commit()

    repair_duplicate_artists(legacy_db)

    survivor = next(a for a in (keep, fragment) if a in _rows(conn))
    assert tuple(conn.execute("SELECT monitored, provenance FROM lib2_monitor_rules WHERE"
                              " entity_type='artist' AND entity_id=? AND profile_id=1",
                              (survivor,)).fetchone()) == (1, "user_explicit")


def test_merging_rules_keeps_the_stronger_choice_and_its_value(imported_conn):
    """the release merge used to keep the survivor's value and only copy the
    stronger provenance: an explicit "monitor" merged over a legacy
    "unmonitored" read as an explicit unmonitor"""
    from core.library2.monitor_rules import merge_rules_into
    conn = imported_conn
    rules = [("album", 10, 1, 0, "legacy_import"), ("album", 20, 1, 1, "user_explicit"),
             ("album", 10, 2, 1, "user_explicit"), ("album", 20, 2, 0, "cascade"),
             ("album", 20, 3, 1, "cascade")]
    conn.executemany("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id,"
                     " monitored, provenance) VALUES(?,?,?,?,?)", rules)
    merge_rules_into(conn, "album", 20, 10)
    got = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT profile_id, monitored, provenance FROM lib2_monitor_rules WHERE entity_id=10")}
    assert got == {1: (1, "user_explicit"), 2: (1, "user_explicit"), 3: (1, "cascade")}
    assert conn.execute("SELECT COUNT(*) FROM lib2_monitor_rules WHERE entity_id=20").fetchone()[0] == 0
