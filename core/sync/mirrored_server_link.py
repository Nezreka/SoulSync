"""Read-half of the mirrored-playlist server link (#1289 item 6).

The write-half (``MusicDatabase.link_mirrored_playlist_to_server`` plus the
``/api/mirrored-playlists/<id>/server-link`` endpoint) records which server
playlist a mirrored playlist corresponds to. This module READS that link
during sync:

1. Look up the stored ``server_playlist_id`` for the mirrored playlist — but
   only when its ``server_type`` matches the active server. The columns are
   shared across server types (one row per mirror), so a Navidrome ID must
   never be used against Plex or Jellyfin.
2. Validate the stored ID against the server with a cheap existence check.
   A server-side RENAME keeps the ID valid, so the sync follows it silently —
   Boulder's call: no rename adoption, no prompt.
3. Stored ID missing, for the wrong server type, or gone (deleted on the
   server) → fall back to the existing case-insensitive name-match. On a hit,
   RECORD the discovered server ID via the link function so the next sync
   uses it.
4. No hit either → return None and let the caller run its normal create path
   (which the sync service then links on the post-write pass).

Navidrome-first: the client surface used here is ``get_playlist_by_id`` /
``get_playlists_by_name``, duck-typed so the Plex/Jellyfin clients can follow
in a later pass by growing the same two methods.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.logging_config import get_logger

from core.playlists.source_refs import _MIRRORED_PK_PREFIXES

logger = get_logger("sync.mirrored_server_link")


def mirrored_pk_from_sync_id(playlist_id: object) -> Optional[int]:
    """Strict mirror-PK parse for sync ids.

    Only the synthetic prefixed forms count (auto_mirror_<pk> from the mirror
    auto-sync, youtube_mirrored_<pk> from YouTube discovery, mirrored_<pk>
    from web_server url hashes — see core/playlists/source_refs). A bare
    numeric id is NOT a mirror here: e.g. a Deezer source playlist id must
    never resolve a mirrored_playlists row by coincidence.
    """
    ref = str(playlist_id or "").strip()
    if not ref:
        return None
    for prefix in _MIRRORED_PK_PREFIXES:
        if ref.startswith(prefix):
            tail = ref[len(prefix) :]
            return int(tail) if tail.isdigit() else None
    return None


def resolve_sync_server_playlist_id(
    *,
    playlist_id: object,
    playlist_name: str,
    server_type: str,
    media_client: Any,
    profile_id: Optional[int] = None,
    db: Any = None,
) -> Optional[str]:
    """Sync-service entry point for the read-half.

    Gates on the sync id actually belonging to a mirrored playlist (strict
    prefixed forms only — a bare numeric source id is never a mirror) and on
    the active server type, then delegates to
    :func:`resolve_mirrored_server_playlist_id`. ``db`` is injectable for
    tests; when omitted a ``MusicDatabase`` is constructed. Never raises.
    """
    if server_type != "navidrome" or media_client is None:
        # Navidrome-first: other server types keep the legacy name-match
        # (their clients don't accept a playlist_id kwarg yet).
        return None
    mirror_pk = mirrored_pk_from_sync_id(playlist_id)
    if mirror_pk is None:
        return None
    if db is None:
        try:
            from database.music_database import MusicDatabase

            db = MusicDatabase()
        except Exception:  # noqa: BLE001 - link is best-effort
            logger.debug("server-link db unavailable")
            return None
    return resolve_mirrored_server_playlist_id(
        db,
        media_client,
        server_type=server_type,
        mirrored_playlist_id=mirror_pk,
        playlist_name=playlist_name or "",
        profile_id=profile_id,
    )


def resolve_mirrored_server_playlist_id(
    db: Any,
    client: Any,
    *,
    server_type: str,
    mirrored_playlist_id: int,
    playlist_name: str,
    profile_id: Optional[int] = None,
) -> Optional[str]:
    """Resolve the server playlist ID a mirrored playlist should sync to.

    Returns the server playlist ID to write to, or None when the sync
    should fall through to its normal create path. Never raises — any
    failure degrades to the legacy name-match / create behaviour.
    """
    try:
        mirror = db.get_mirrored_playlist(int(mirrored_playlist_id), profile_id=profile_id)
    except Exception as e:  # noqa: BLE001 - link is best-effort, never break a sync
        logger.debug("server-link read failed for mirror %s: %s", mirrored_playlist_id, e)
        return None
    if not mirror:
        return None

    stored_id = mirror.get("server_playlist_id")
    stored_type = mirror.get("server_type")

    if stored_id and stored_type == server_type:
        # The link is for THIS server type — validate it before trusting it.
        try:
            if client.get_playlist_by_id(str(stored_id)) is not None:
                logger.info(
                    "Mirror %s: following stored %s playlist id %s (server rename safe)",
                    mirrored_playlist_id,
                    server_type,
                    stored_id,
                )
                return str(stored_id)
        except Exception as e:  # noqa: BLE001 - validation failure == unusable link
            logger.debug("server-link validation failed for mirror %s: %s", mirrored_playlist_id, e)
        # Stored ID is gone (deleted on the server) or unreachable — fall
        # through to the name-match below rather than creating a duplicate.
        logger.info(
            "Mirror %s: stored %s playlist id %s no longer on server; falling back to name match",
            mirrored_playlist_id,
            server_type,
            stored_id,
        )
    elif stored_id:
        # A link exists but for a DIFFERENT server type — never use it here.
        logger.debug(
            "Mirror %s: stored server link is for %r, not %r; ignoring",
            mirrored_playlist_id,
            stored_type,
            server_type,
        )

    # Fallback: the legacy case-insensitive name-match, unchanged. On a hit,
    # record the discovered ID so the NEXT sync follows it by ID.
    try:
        matches = client.get_playlists_by_name(playlist_name) or []
    except Exception as e:  # noqa: BLE001 - name-match failure == no match
        logger.debug("server-link name-match failed for mirror %s: %s", mirrored_playlist_id, e)
        return None
    if not matches:
        return None
    found_id = str(matches[0].id)
    try:
        db.link_mirrored_playlist_to_server(int(mirrored_playlist_id), found_id, server_type, profile_id=profile_id)
        logger.info(
            "Mirror %s: name-matched %s playlist %r; recorded server id %s",
            mirrored_playlist_id,
            server_type,
            playlist_name,
            found_id,
        )
    except Exception as e:  # noqa: BLE001 - recording is best-effort
        logger.debug("server-link record failed for mirror %s: %s", mirrored_playlist_id, e)
    return found_id
