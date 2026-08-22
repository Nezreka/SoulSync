"""Browser-renderable artwork for Wishlist rows, resolved through Library v2.

A wishlist row that came from Library v2 used to carry no album art at all:
``track_wishlist_payload`` never selected one, so ``spotify_data.album.images``
was absent and the UI (which reads ``images[0].url``) rendered an empty tile for
every Library-v2 item. Artist photos had the opposite problem — they *were*
resolved, but by exact name match against ``lib2_artists.image_url``, which on a
media-server install is a Plex/Jellyfin/Navidrome path. Those get rewritten into
authenticated media-server URLs and cached, so when the media server is offline
or unreachable from the browser every single artist photo 404s.

This module is the one place that answers "what image URL do I hand the browser
for this Library-v2 entity?", and it answers in the SAME order the Library v2
pages use (``api/library_v2._apply_artwork_urls``):

1. Library v2's own media-server-independent artwork endpoint
   (``/api/library/v2/artwork/<kind>/<id>``, see ``core.library2.artwork``),
   which resolves from embedded file art and the metadata providers and serves
   the already-cached JPEG straight off disk.
2. the provider CDN url the catalogue stores, when a browser anywhere can
   actually load it (``usable_remote_image_url`` — public host, not a known
   provider placeholder). This is the stand-in for the wait: a cold entity 404s
   while the endpoint builds it in the background, so the client paints this
   instead of a placeholder and picks the local copy up on the next render.

Consumers that cannot use a relative URL — the import pipeline fetches
``album.images`` to write cover.jpg and embed tag art — must take the first
entry whose URL is absolute, not simply the first entry.

Both are stable identities — nothing here mints a per-request URL, so the image
cache cannot be filled with one key per render.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.metadata.artwork import usable_remote_image_url

ARTWORK_ROUTE = "/api/library/v2/artwork"

# What we claim for the artwork endpoint's images. The endpoint serves whatever
# the source gave us, so these are nominal — they exist because the Spotify
# payload shape the wishlist UI consumes expects width/height alongside url, and
# a missing dimension makes some consumers skip the entry entirely.
_NOMINAL_EDGE = 500


def artwork_endpoint_url(kind: str, entity_id: Any, *, version: Any = None) -> str:
    """The stable Library-v2 artwork URL for one entity.

    ``version`` is the cache-buster (the artwork file's mtime, from
    ``core.library2.artwork.artwork_version``). It is optional because the
    wishlist WRITE path has only a bare connection — it stores the plain,
    stable URL, and the read path adds the version when it can.
    """
    base = f"{ARTWORK_ROUTE}/{kind}/{int(entity_id)}"
    return f"{base}?v={version}" if version else base


def _artwork_version(database: Any, kind: str, entity_id: int) -> Any:
    if database is None:
        return None
    try:
        from core.library2.artwork import artwork_version

        return artwork_version(database, kind, int(entity_id)) or None
    except Exception:  # noqa: BLE001 — art is cosmetic, never fail the caller
        return None


def _image_entry(url: str, edge: int = _NOMINAL_EDGE) -> Dict[str, Any]:
    return {"url": url, "height": edge, "width": edge}


def album_images(
    conn: Any,
    album_id: Any,
    *,
    database: Any = None,
    stored_image_url: Any = None,
) -> List[Dict[str, Any]]:
    """The ``album.images`` list for a Library-v2 album, best entry first.

    The local artwork endpoint leads — it is the long-term truth (a manual
    cover pick, an embedded cover, a NAS with no internet) and it is already
    on disk for anything the user has browsed. A usable provider CDN cover
    follows as the stand-in while a cold local build runs.

    Returns ``[]`` only when there is no album to point at.
    """
    if album_id is None:
        return []
    try:
        album_id = int(album_id)
    except (TypeError, ValueError):
        return []

    remote = usable_remote_image_url(stored_image_url)
    if remote is None and conn is not None:
        try:
            row = conn.execute(
                "SELECT image_url FROM lib2_albums WHERE id=?", (album_id,)
            ).fetchone()
        except Exception:  # noqa: BLE001
            row = None
        if row is not None:
            remote = usable_remote_image_url(row["image_url"])

    entries: List[Dict[str, Any]] = [
        _image_entry(
            artwork_endpoint_url(
                "album", album_id, version=_artwork_version(database, "album", album_id)
            )
        )
    ]
    if remote:
        entries.append(_image_entry(remote, 640))
    return entries


def first_fetchable_image_url(images: Any) -> Optional[str]:
    """The first entry a server-side fetcher can actually retrieve.

    ``album.images[0]`` is now SoulSync's own relative artwork URL, which a
    browser resolves happily and ``urllib`` cannot resolve at all. Anything
    that downloads the cover (cover.jpg, embedded tag art) asks for this
    instead of indexing the list.
    """
    if not isinstance(images, (list, tuple)):
        return None
    for entry in images:
        url = entry.get("url") if isinstance(entry, dict) else entry
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def artist_image_url(
    conn: Any,
    artist_id: Any,
    *,
    database: Any = None,
    stored_image_url: Any = None,
) -> Optional[str]:
    """The primary photo URL for a Library-v2 artist — the local endpoint.

    Media-server independent by construction. :func:`artist_remote_image_url`
    supplies the CDN stand-in a client paints while a cold build runs; neither
    ever returns a media-server path or a provider placeholder, which are the
    two things that render as a broken tile.
    """
    if artist_id is None:
        return None
    try:
        artist_id = int(artist_id)
    except (TypeError, ValueError):
        return None

    return artwork_endpoint_url(
        "artist", artist_id, version=_artwork_version(database, "artist", artist_id)
    )


def artist_remote_image_url(
    conn: Any, artist_id: Any, *, stored_image_url: Any = None,
) -> Optional[str]:
    """The CDN photo a client paints while the local build is still cold."""
    remote = usable_remote_image_url(stored_image_url)
    if remote is not None or conn is None or artist_id is None:
        return remote
    try:
        row = conn.execute(
            "SELECT image_url FROM lib2_artists WHERE id=?", (int(artist_id),)
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return usable_remote_image_url(row["image_url"]) if row is not None else None


__all__ = [
    "ARTWORK_ROUTE",
    "album_images",
    "artist_image_url",
    "artist_remote_image_url",
    "artwork_endpoint_url",
    "first_fetchable_image_url",
]
