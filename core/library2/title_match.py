"""Match (title, artist) pairs from plays against the catalogue.

sqlite's LOWER() folds only A-Z, python's lower() folds every letter. keying
one side with each meant a title like 'WÖRLDENDER' never matched its own
row (review 5 R5-H-5 and R5-A-1). every play resolver goes through here so
both sides fold the same way.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                             "abcdefghijklmnopqrstuvwxyz")


def sqlite_lower(text) -> str:
    """what sqlite's LOWER() returns for this text: only A-Z are folded"""
    return str(text or "").translate(_ASCII_LOWER)


def title_fold(text) -> str:
    """the python-side title key every resolver looks plays up by"""
    return str(text or "").strip().casefold()


_PAIR_SQL = """
    SELECT t.title, ar.name_key, t.id
    FROM lib2_tracks t
    JOIN lib2_albums al ON al.id = t.album_id
    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
    WHERE (LOWER(t.title), ar.name_key) IN ({marks})
"""

_ARTIST_SQL = """
    SELECT t.title, ar.name_key, t.id
    FROM lib2_artists ar
    JOIN lib2_albums al ON al.primary_artist_id = ar.id
    JOIN lib2_tracks t ON t.album_id = al.id
    WHERE ar.name_key IN ({marks})
"""


def resolve_title_artist_pairs(cursor, pairs: Iterable[Tuple[str, str]],
                               chunk_size: int = 400) -> Dict[Tuple[str, str], int]:
    """``{(title_fold(title), artist_key): track_id}`` for the pairs found.

    ``pairs`` are (title as the play gave it, catalogue artist name_key). the
    first pass uses the LOWER(title) index with the title folded the way
    sqlite folds it. a title with non-ascii letters in another case than the
    stored one can't match that way, so those few fall back to the artist's
    tracks (name_key index) compared with casefold in python.
    """
    wanted: Dict[Tuple[str, str], str] = {}
    for title, artist_key in pairs:
        title = str(title or "").strip()
        if not title:
            continue
        wanted.setdefault((title_fold(title), artist_key or ""), title)
    result: Dict[Tuple[str, str], int] = {}
    if not wanted:
        return result

    probe = sorted({(sqlite_lower(title), key[1]) for key, title in wanted.items()})
    for i in range(0, len(probe), chunk_size):
        chunk = probe[i:i + chunk_size]
        marks = ",".join(["(?,?)"] * len(chunk))
        cursor.execute(_PAIR_SQL.format(marks=marks),
                       [v for pair in chunk for v in pair])
        for title, artist_key, track_id in cursor.fetchall():
            key = (title_fold(title), artist_key)
            if key in wanted:
                # first match per pair, as the old per-event LIMIT 1 did
                result.setdefault(key, track_id)

    missing_artists = sorted({
        key[1] for key, title in wanted.items()
        if key not in result and not title.isascii()
    })
    for i in range(0, len(missing_artists), chunk_size):
        chunk = missing_artists[i:i + chunk_size]
        marks = ",".join("?" * len(chunk))
        cursor.execute(_ARTIST_SQL.format(marks=marks), chunk)
        for title, artist_key, track_id in cursor.fetchall():
            key = (title_fold(title), artist_key)
            if key in wanted:
                result.setdefault(key, track_id)
    return result


__all__ = ["resolve_title_artist_pairs", "sqlite_lower", "title_fold"]
