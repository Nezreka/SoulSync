"""Upstream 33da6be49 on the Library V2 page: the A-Z sort skips leading
punctuation, so '"Weird Al" Yankovic' files under W and '*NSYNC' under N
instead of floating above 2Pac. Digits still sort as numbers; articles stay.

Upstream fixed the legacy library query; the Library V2 artist list and the
artist page's album order are this branch's equivalents."""

from __future__ import annotations

from core.library2 import queries as Q
from tests.lib2_seed import album, artist


def _sorted_names(conn, names):
    rows, _total = Q.list_artists(conn, sort="name", limit=500, include_size=False)
    return [row["name"] for row in rows if row["name"] in names]


def test_leading_punctuation_does_not_lead_the_artist_list(imported_conn):
    names = ['"Weird Al" Yankovic', "*NSYNC", "2Pac", "ABBA", "The Notwist", "Zappa"]
    for name in names:  # monitored: the list shows artists with a file or intent
        artist(imported_conn, name, sort_name=name, monitored=1)

    assert _sorted_names(imported_conn, names) == [
        "2Pac", "ABBA", "*NSYNC", "The Notwist", '"Weird Al" Yankovic', "Zappa",
    ]


def test_an_artist_without_a_sort_name_sorts_by_its_name(imported_conn):
    names = ["Aaron", "Mogwai", "Zz Top"]
    for name in names:
        artist(imported_conn, name, sort_name=name, monitored=1)
    artist(imported_conn, "Mogwai", sort_name=None, monitored=1)

    assert _sorted_names(imported_conn, names) == names


def test_albums_of_one_year_sort_by_title_without_their_punctuation(imported_conn):
    owner = artist(imported_conn, "Tiebreak Artist", sort_name="Tiebreak Artist")
    for title in ['"Heroes"', "Blackstar", "...And Out Come the Wolves"]:
        album_id = album(imported_conn, "Tiebreak Artist", title, year=2001)
        imported_conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?, ?, 'primary')",
            (album_id, owner))
    imported_conn.commit()

    detail = Q.get_artist(imported_conn, owner)
    titles = [entry["title"] for entry in detail["albums"]]
    assert titles == ["...And Out Come the Wolves", "Blackstar", '"Heroes"']
