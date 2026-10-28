"""Unified Library-v2 artist settings backed by the existing watchlist row.

Library v2 deliberately does not own a second copy of release filters,
lookback, automatic-download or preferred-provider settings.  This module
resolves a lib2 artist to the admin profile's existing ``watchlist_artists``
row and reads/writes that row in place.  ``monitor_new_items`` remains on the
lib2 artist because it controls lib2's own discography re-expansion policy.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional


WATCHLIST_BOOLEAN_FIELDS = (
    "include_albums",
    "include_eps",
    "include_singles",
    "include_live",
    "include_remixes",
    "include_acoustic",
    "include_compilations",
    "include_instrumentals",
    "include_other_artists",
    "auto_download",
)
WATCHLIST_PROVIDER_FIELDS = {
    "spotify": "spotify_artist_id",
    "itunes": "itunes_artist_id",
    "deezer": "deezer_artist_id",
    "discogs": "discogs_artist_id",
    "amazon": "amazon_artist_id",
    "musicbrainz": "musicbrainz_artist_id",
}
MONITOR_NEW_ITEMS = {"all", "new", "none"}


class ArtistSettingsError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _columns(conn, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _external_ids(raw: Any) -> Dict[str, str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            raw = {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip().lower(): str(value).strip()
        for key, value in raw.items()
        if value is not None and str(value).strip()
    }


def _artist_provider_ids(artist) -> Dict[str, str]:
    external = _external_ids(artist["external_ids"])
    values = {
        "spotify": artist["spotify_id"],
        "musicbrainz": artist["musicbrainz_id"],
        "itunes": external.get("itunes"),
        "deezer": external.get("deezer"),
        "discogs": external.get("discogs"),
        "amazon": external.get("amazon"),
    }
    return {
        provider: str(value).strip()
        for provider, value in values.items()
        if value is not None and str(value).strip()
    }


def _watchlist_row(conn, artist, profile_id: int):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_artists'"
    ).fetchone():
        raise ArtistSettingsError("Watchlist storage is unavailable", 409)
    columns = _columns(conn, "watchlist_artists")
    profile_clause = " WHERE profile_id=?" if "profile_id" in columns else ""
    params: tuple[Any, ...] = (int(profile_id),) if profile_clause else ()
    rows = conn.execute(
        f"SELECT * FROM watchlist_artists{profile_clause} ORDER BY id", params
    ).fetchall()
    provider_ids = _artist_provider_ids(artist)

    # Stable provider identity always wins.  A case-insensitive name match is
    # the same compatibility fallback the importer uses for old watchlist rows.
    for row in rows:
        for provider, column in WATCHLIST_PROVIDER_FIELDS.items():
            if column not in columns or provider not in provider_ids:
                continue
            value = row[column]
            if value is not None and str(value) == provider_ids[provider]:
                return row, columns
    name = str(artist["name"] or "").casefold()
    for row in rows:
        if str(row["artist_name"] or "").casefold() == name:
            return row, columns
    raise ArtistSettingsError("Artist is not on the admin watchlist", 409)


def _artist_row(conn, artist_id: int):
    artist = conn.execute(
        """SELECT id, name, spotify_id, musicbrainz_id, external_ids,
                  monitor_new_items
             FROM lib2_artists WHERE id=?""",
        (int(artist_id),),
    ).fetchone()
    if not artist:
        raise ArtistSettingsError("Artist not found", 404)
    return artist


def _bool_value(row, columns: Iterable[str], field: str, default: bool) -> bool:
    return bool(row[field]) if field in columns and row[field] is not None else default


def _release_rules(watchlist, columns: Iterable[str]) -> tuple[Dict[str, bool], bool]:
    """The nine release rules a scan applies, and whether they are the
    watchlist defaults the artist follows (#1822) rather than its own."""
    from core.settings import config_manager
    from core.watchlist_defaults import effective_rules, follows_defaults, legacy_override_on

    row = {column: watchlist[column] for column in columns}
    override = legacy_override_on(config_manager)
    return effective_rules(row, config_manager, override), follows_defaults(row, override)


def get_artist_settings(conn, artist_id: int, *, profile_id: int = 1) -> Dict[str, Any]:
    """Return lib2 + watchlist settings for one monitored artist."""
    artist = _artist_row(conn, artist_id)
    watchlist, columns = _watchlist_row(conn, artist, profile_id)
    rules, following = _release_rules(watchlist, columns)
    provider_ids = {
        provider: (str(watchlist[column]) if watchlist[column] not in (None, "") else None)
        for provider, column in WATCHLIST_PROVIDER_FIELDS.items()
        if column in columns
    }
    return {
        "artist_id": int(artist["id"]),
        "watchlist_row_id": int(watchlist["id"]),
        "watchlist_name": watchlist["artist_name"],
        "watchlist_image_url": (
            watchlist["image_url"] if "image_url" in columns else None
        ),
        "provider_ids": provider_ids,
        "monitor_new_items": artist["monitor_new_items"] or "all",
        **rules,
        "follow_defaults": following,
        "auto_download": _bool_value(watchlist, columns, "auto_download", True),
        "lookback_days": watchlist["lookback_days"] if "lookback_days" in columns else None,
        "preferred_metadata_source": (
            watchlist["preferred_metadata_source"]
            if "preferred_metadata_source" in columns
            else None
        ),
    }


def update_artist_settings(
    conn,
    artist_id: int,
    updates: Dict[str, Any],
    *,
    profile_id: int = 1,
    allowed_metadata_sources: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Validate and update the existing watchlist row in one transaction."""
    if not isinstance(updates, dict):
        raise ArtistSettingsError("JSON body must be an object")
    artist = _artist_row(conn, artist_id)
    watchlist, columns = _watchlist_row(conn, artist, profile_id)

    values: Dict[str, Any] = {}
    for field in (*WATCHLIST_BOOLEAN_FIELDS, "follow_defaults"):
        if field in updates:
            if not isinstance(updates[field], bool):
                raise ArtistSettingsError(f"{field} must be a boolean")
            if field in columns:
                values[field] = int(updates[field])

    # #1822: a follower given rules of its own stops following, starting from
    # the defaults it was getting rather than the stale columns
    rules, following = _release_rules(watchlist, columns)
    if following and "follow_defaults" not in updates and set(values) & set(rules):
        values = {**{k: int(v) for k, v in rules.items() if k in columns},
                  **values, "follow_defaults": 0}
    if not values.get("follow_defaults", following) and not any(
        values.get(field, rules[field])
        for field in ("include_albums", "include_eps", "include_singles")
    ):
        raise ArtistSettingsError("At least one of Albums, EPs or Singles must be enabled")

    if "lookback_days" in updates:
        lookback = updates["lookback_days"]
        if lookback in (None, ""):
            values["lookback_days"] = None
        elif isinstance(lookback, bool):
            raise ArtistSettingsError("lookback_days must be a non-negative integer or null")
        else:
            try:
                lookback = int(lookback)
            except (TypeError, ValueError) as exc:
                raise ArtistSettingsError(
                    "lookback_days must be a non-negative integer or null"
                ) from exc
            if lookback < 0 or lookback > 36500:
                raise ArtistSettingsError("lookback_days must be between 0 and 36500")
            values["lookback_days"] = lookback

    if "preferred_metadata_source" in updates:
        preferred = updates["preferred_metadata_source"]
        preferred = str(preferred).strip().lower() if preferred not in (None, "") else None
        allowed = {str(source).lower() for source in (allowed_metadata_sources or ())}
        if preferred is not None and preferred not in allowed:
            raise ArtistSettingsError("preferred_metadata_source is not available")
        values["preferred_metadata_source"] = preferred

    monitor_new = updates.get("monitor_new_items")
    if monitor_new is not None:
        monitor_new = str(monitor_new).strip().lower()
        if monitor_new not in MONITOR_NEW_ITEMS:
            raise ArtistSettingsError("monitor_new_items must be all|new|none")
        conn.execute(
            """UPDATE lib2_artists
                  SET monitor_new_items=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (monitor_new, int(artist_id)),
        )

    if values:
        previous_lookback = (
            watchlist["lookback_days"] if "lookback_days" in columns else None
        )
        assignments = [f"{field}=?" for field in values if field in columns]
        params = [values[field] for field in values if field in columns]
        if (
            "lookback_days" in values
            and "last_scan_timestamp" in columns
            and values["lookback_days"] != previous_lookback
        ):
            assignments.append("last_scan_timestamp=NULL")
        if "updated_at" in columns:
            assignments.append("updated_at=CURRENT_TIMESTAMP")
        if assignments:
            conn.execute(
                f"UPDATE watchlist_artists SET {', '.join(assignments)} WHERE id=?",
                (*params, int(watchlist["id"])),
            )

    return get_artist_settings(conn, artist_id, profile_id=profile_id)


__all__ = [
    "ArtistSettingsError",
    "MONITOR_NEW_ITEMS",
    "WATCHLIST_BOOLEAN_FIELDS",
    "get_artist_settings",
    "update_artist_settings",
]
