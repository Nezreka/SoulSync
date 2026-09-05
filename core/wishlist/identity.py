"""One derivation of the wishlist row key, for every writer and reader.

A wishlist row identifies a *release* of a recording, not the recording. With
``wishlist.allow_duplicate_tracks`` on — the default — the same provider track
wanted from two different albums is two rows, keyed ``<track>::<album>``.

Three places used to derive that key independently, and two of them got it
wrong (SYNC-02/SYNC-03):

- the insert in ``add_to_wishlist_detailed`` builds the composite key,
- the mirror's "is it still on the wishlist?" probe asked for the bare track
  id, never found the composite row, and so skipped withdrawing a wish the
  library had already satisfied,
- the mirror's remove passed the bare track id to ``remove_from_wishlist``,
  whose documented job is to clear *every* ``<track>::%`` row — correct for
  "this recording was downloaded", wrong for "the user unmonitored one
  release", where it also deleted a sibling release that was still wanted,
- and the outbox's supersession keyed on the bare track id too, so two wanted
  releases of one recording in the same drain looked like one intent
  superseding the other, and only the newer add survived.

Deriving it once here is what makes those four agree.
"""

from __future__ import annotations

from typing import Any, Optional

_SEPARATOR = "::"


def allow_duplicate_wishlist_rows(config_get: Optional[Any] = None) -> bool:
    """Whether one recording may hold a wishlist row per release."""
    if config_get is None:
        from core.settings import config_manager

        config_get = config_manager.get
    return bool(config_get("wishlist.allow_duplicate_tracks", True))


def wishlist_row_key(track_id: Any, album_id: Any = None, *,
                     allow_duplicates: Optional[bool] = None,
                     config_get: Optional[Any] = None) -> str:
    """The key the wishlist row for one release of a track is stored under.

    Falls back to the bare track id when duplicates are off or the release has
    no identity of its own — the same shape the row already has in that case.
    """
    track = str(track_id or "").strip()
    album = str(album_id or "").strip()
    if not track:
        return ""
    if allow_duplicates is None:
        allow_duplicates = allow_duplicate_wishlist_rows(config_get)
    if allow_duplicates and album:
        return f"{track}{_SEPARATOR}{album}"
    return track


def wishlist_key_from_payload(payload: Any, *,
                              allow_duplicates: Optional[bool] = None,
                              config_get: Optional[Any] = None) -> str:
    """``wishlist_row_key`` for a track payload's own ``album.id``."""
    if not isinstance(payload, dict):
        return ""
    album = payload.get("album")
    album_id = album.get("id") if isinstance(album, dict) else None
    return wishlist_row_key(payload.get("id"), album_id,
                            allow_duplicates=allow_duplicates,
                            config_get=config_get)


__all__ = [
    "allow_duplicate_wishlist_rows",
    "wishlist_key_from_payload",
    "wishlist_row_key",
]
