"""
MetaSync export — a read-only walk of the library's resolved metadata.

MetaSync is a peer-to-peer metadata network running as a SoulSync sidecar. It
reads over this endpoint, packages rows into signed claims and trades them with
peers, so that entity resolution one install paid thousands of API calls for
does not have to be redone by every other install.

Two things make this different from the /library endpoints, which are
search-shaped (title/artist filters, offset pagination, 200 cap):

* it walks EVERY row once and then only what changed, so pagination is keyset
  (``id > cursor``) — offset both skips and duplicates rows when the enrichment
  workers shift them mid-walk;
* it serves ``soul_id``, the cross-install identity the whole network keys on,
  and nothing that is meaningful only on this box (see the allowlist
  serializers in api/serializers.py).

Read-only: no INSERT/UPDATE/DELETE anywhere in this module.
"""

import base64
import binascii
from datetime import datetime

from flask import request

from database.music_database import get_database

from .auth import require_api_key
from .helpers import api_error, api_success
from .serializers import (
    serialize_metasync_album,
    serialize_metasync_artist,
    serialize_metasync_track,
)

_SERIALIZERS = {
    "artist": serialize_metasync_artist,
    "album": serialize_metasync_album,
    "track": serialize_metasync_track,
}

_DEFAULT_LIMIT = 500
_MAX_LIMIT = 1000


def _encode_cursor(value) -> str:
    """Opaque cursor for the caller. It is the last row's primary key, base64'd
    so nobody builds a dependency on its shape — it stays between MetaSync and
    SoulSync on localhost and is never published to peers."""
    return base64.urlsafe_b64encode(str(value).encode("utf-8")).decode("ascii")


def _decode_cursor(raw: str) -> str:
    """Inverse of _encode_cursor. Raises ValueError on anything malformed."""
    if not raw:
        return ""
    try:
        return base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("cursor is not a valid base64 token") from exc


def register_routes(bp):

    @bp.route("/metasync/export", methods=["GET"])
    @require_api_key
    def metasync_export():
        """Page through artists/albums/tracks for the MetaSync sidecar."""
        entity = (request.args.get("entity") or "").strip().lower()
        if entity not in _SERIALIZERS:
            return api_error(
                "BAD_REQUEST",
                "entity is required and must be one of: artist, album, track.",
                400,
            )

        try:
            cursor = _decode_cursor(request.args.get("cursor", "").strip())
        except ValueError as e:
            return api_error("BAD_REQUEST", str(e), 400)

        since = (request.args.get("since") or "").strip()
        if since:
            # Validated here rather than passed through: an unparseable string
            # would silently compare as text in SQLite and quietly return the
            # wrong slice instead of telling the caller it was wrong.
            try:
                datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                return api_error(
                    "BAD_REQUEST",
                    "since must be an ISO-8601 timestamp (e.g. 2026-08-19T00:00:00).",
                    400,
                )

        try:
            limit = int(request.args.get("limit", _DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT
        limit = max(1, min(_MAX_LIMIT, limit))

        try:
            db = get_database()
            rows = db.api_export_entities(entity, cursor=cursor, since=since, limit=limit)
        except ValueError as e:
            return api_error("BAD_REQUEST", str(e), 400)
        except Exception as e:
            return api_error("EXPORT_ERROR", str(e), 500)

        serializer = _SERIALIZERS[entity]
        # The row dicts carry `id` so the cursor can be built; the allowlist
        # serializer drops it, so it never reaches the response.
        items = [serializer(row) for row in rows]
        next_cursor = _encode_cursor(rows[-1].get("id")) if rows else ""

        return api_success({
            "entity": entity,
            "items": items,
            "next_cursor": next_cursor,
            # The soul_unnamed_ filter is applied in SQL before the LIMIT, so a
            # full page really does mean "there may be more".
            "has_more": len(rows) == limit,
        })
