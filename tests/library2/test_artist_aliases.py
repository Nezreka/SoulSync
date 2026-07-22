"""§40 artist-alias registry: soft-link validation + group resolution."""

from __future__ import annotations

import pytest

from core.library2.artist_aliases import (
    AliasLinkError,
    link_artist_alias,
    resolve_alias_group,
    unlink_artist_alias,
)


def _new_artist(conn, name: str) -> int:
    cur = conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (name,))
    return cur.lastrowid


# --- link_artist_alias --------------------------------------------------------

def test_link_sets_canonical_artist_id(imported_conn):
    conn = imported_conn
    romaji = _new_artist(conn, "Hirokyu Samono")
    kanji = _new_artist(conn, "弘求サモノ")

    link_artist_alias(conn, kanji, romaji)

    row = conn.execute(
        "SELECT canonical_artist_id FROM lib2_artists WHERE id=?", (kanji,)
    ).fetchone()
    assert row["canonical_artist_id"] == romaji


def test_link_rejects_self_link(imported_conn):
    conn = imported_conn
    artist = _new_artist(conn, "A")
    with pytest.raises(AliasLinkError) as exc:
        link_artist_alias(conn, artist, artist)
    assert exc.value.status == 400


def test_link_rejects_unknown_canonical(imported_conn):
    conn = imported_conn
    artist = _new_artist(conn, "A")
    with pytest.raises(AliasLinkError) as exc:
        link_artist_alias(conn, artist, 999999)
    assert exc.value.status == 404


def test_link_rejects_unknown_artist(imported_conn):
    conn = imported_conn
    canonical = _new_artist(conn, "A")
    with pytest.raises(AliasLinkError) as exc:
        link_artist_alias(conn, 999999, canonical)
    assert exc.value.status == 404


def test_link_rejects_chain_onto_an_alias(imported_conn):
    conn = imported_conn
    canonical = _new_artist(conn, "A")
    alias = _new_artist(conn, "B")
    third = _new_artist(conn, "C")
    link_artist_alias(conn, alias, canonical)

    with pytest.raises(AliasLinkError) as exc:
        link_artist_alias(conn, third, alias)
    assert exc.value.status == 400
    assert "canonical" in str(exc.value).lower()


def test_link_rejects_group_merge(imported_conn):
    conn = imported_conn
    canonical_a = _new_artist(conn, "A")
    alias_a = _new_artist(conn, "A2")
    canonical_b = _new_artist(conn, "B")
    link_artist_alias(conn, alias_a, canonical_a)

    with pytest.raises(AliasLinkError) as exc:
        link_artist_alias(conn, canonical_a, canonical_b)
    assert exc.value.status == 400
    assert "aliases of its own" in str(exc.value)


def test_link_allows_relinking_an_existing_alias(imported_conn):
    conn = imported_conn
    canonical_a = _new_artist(conn, "A")
    canonical_b = _new_artist(conn, "B")
    alias = _new_artist(conn, "Alias")
    link_artist_alias(conn, alias, canonical_a)

    link_artist_alias(conn, alias, canonical_b)

    row = conn.execute(
        "SELECT canonical_artist_id FROM lib2_artists WHERE id=?", (alias,)
    ).fetchone()
    assert row["canonical_artist_id"] == canonical_b


# --- unlink_artist_alias -------------------------------------------------------

def test_unlink_clears_canonical_artist_id(imported_conn):
    conn = imported_conn
    canonical = _new_artist(conn, "A")
    alias = _new_artist(conn, "B")
    link_artist_alias(conn, alias, canonical)

    unlink_artist_alias(conn, alias)

    row = conn.execute(
        "SELECT canonical_artist_id FROM lib2_artists WHERE id=?", (alias,)
    ).fetchone()
    assert row["canonical_artist_id"] is None


def test_unlink_is_idempotent_for_standalone_artist(imported_conn):
    conn = imported_conn
    artist = _new_artist(conn, "A")
    unlink_artist_alias(conn, artist)  # no error
    row = conn.execute(
        "SELECT canonical_artist_id FROM lib2_artists WHERE id=?", (artist,)
    ).fetchone()
    assert row["canonical_artist_id"] is None


def test_unlink_rejects_unknown_artist(imported_conn):
    conn = imported_conn
    with pytest.raises(AliasLinkError) as exc:
        unlink_artist_alias(conn, 999999)
    assert exc.value.status == 404


def test_unlink_only_detaches_the_one_row(imported_conn):
    conn = imported_conn
    canonical = _new_artist(conn, "A")
    alias_1 = _new_artist(conn, "B")
    alias_2 = _new_artist(conn, "C")
    link_artist_alias(conn, alias_1, canonical)
    link_artist_alias(conn, alias_2, canonical)

    unlink_artist_alias(conn, alias_1)

    assert resolve_alias_group(conn, canonical) == sorted([canonical, alias_2])


# --- resolve_alias_group --------------------------------------------------------

def test_resolve_standalone_artist_returns_itself(imported_conn):
    conn = imported_conn
    artist = _new_artist(conn, "A")
    assert resolve_alias_group(conn, artist) == [artist]


def test_resolve_group_from_either_member_id(imported_conn):
    conn = imported_conn
    canonical = _new_artist(conn, "A")
    alias = _new_artist(conn, "B")
    link_artist_alias(conn, alias, canonical)

    expected = sorted([canonical, alias])
    assert resolve_alias_group(conn, canonical) == expected
    assert resolve_alias_group(conn, alias) == expected


def test_resolve_group_canonical_always_first(imported_conn):
    conn = imported_conn
    # Canonical gets the numerically LARGEST id on purpose, to prove the
    # ordering is "canonical first" and not "smallest id first".
    alias_1 = _new_artist(conn, "B1")
    alias_2 = _new_artist(conn, "B2")
    canonical = _new_artist(conn, "A")
    link_artist_alias(conn, alias_1, canonical)
    link_artist_alias(conn, alias_2, canonical)

    group = resolve_alias_group(conn, alias_1)
    assert group[0] == canonical
    assert group[1:] == sorted([alias_1, alias_2])


def test_resolve_unknown_id_falls_back_to_itself(imported_conn):
    conn = imported_conn
    assert resolve_alias_group(conn, 999999) == [999999]
