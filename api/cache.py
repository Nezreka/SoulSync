"""
Cache endpoints — browse MusicBrainz and discovery match caches.
"""

import json
from flask import request
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, parse_pagination, build_pagination


def register_routes(bp):

    # ── MusicBrainz Cache ──────────────────────────────────────

    @bp.route("/cache/musicbrainz", methods=["GET"])
    @require_api_key
    def list_musicbrainz_cache():
        """List cached MusicBrainz lookups.

        Query params:
            entity_type: Filter by type ('artist', 'album', 'track')
            search: Filter by entity_name
            page: Page number
            limit: Items per page
        """
        page, limit = parse_pagination(request)
        entity_type = request.args.get("entity_type")
        search = request.args.get("search", "").strip()

        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            where_parts = []
            params = []

            if entity_type:
                where_parts.append("entity_type = ?")
                params.append(entity_type)
            if search:
                where_parts.append("LOWER(entity_name) LIKE LOWER(?)")
                params.append(f"%{search}%")

            where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

            cursor.execute(f"SELECT COUNT(*) as cnt FROM musicbrainz_cache {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            offset = (page - 1) * limit
            cursor.execute(f"""
                SELECT * FROM musicbrainz_cache
                {where_clause}
                ORDER BY last_updated DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            entries = []
            for row in cursor.fetchall():
                entry = dict(row)
                if entry.get("metadata_json") and isinstance(entry["metadata_json"], str):
                    try:
                        entry["metadata_json"] = json.loads(entry["metadata_json"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                entries.append(entry)

            return api_success(
                {"entries": entries},
                pagination=build_pagination(page, limit, total),
            )
        except Exception as e:
            return api_error("CACHE_ERROR", str(e), 500)

    @bp.route("/cache/musicbrainz/stats", methods=["GET"])
    @require_api_key
    def musicbrainz_cache_stats():
        """Get MusicBrainz cache statistics."""
        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as total FROM musicbrainz_cache")
            total = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT entity_type, COUNT(*) as count
                FROM musicbrainz_cache
                GROUP BY entity_type
                ORDER BY count DESC
            """)
            by_type = {row["entity_type"]: row["count"] for row in cursor.fetchall()}

            cursor.execute("""
                SELECT COUNT(*) as matched FROM musicbrainz_cache
                WHERE musicbrainz_id IS NOT NULL
            """)
            matched = cursor.fetchone()["matched"]

            return api_success({
                "total": total,
                "matched": matched,
                "unmatched": total - matched,
                "by_type": by_type,
            })
        except Exception as e:
            return api_error("CACHE_ERROR", str(e), 500)

    # ── Discovery Match Cache ──────────────────────────────────

    @bp.route("/cache/discovery-matches", methods=["GET"])
    @require_api_key
    def list_discovery_match_cache():
        """List cached discovery provider matches.

        Query params:
            provider: Filter by provider ('spotify', 'itunes', etc.)
            search: Filter by title or artist
            page: Page number
            limit: Items per page
        """
        page, limit = parse_pagination(request)
        provider = request.args.get("provider")
        search = request.args.get("search", "").strip()

        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            where_parts = []
            params = []

            if provider:
                where_parts.append("provider = ?")
                params.append(provider)
            if search:
                where_parts.append("(LOWER(original_title) LIKE LOWER(?) OR LOWER(original_artist) LIKE LOWER(?))")
                params.extend([f"%{search}%", f"%{search}%"])

            where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

            cursor.execute(f"SELECT COUNT(*) as cnt FROM discovery_match_cache {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            offset = (page - 1) * limit
            cursor.execute(f"""
                SELECT * FROM discovery_match_cache
                {where_clause}
                ORDER BY last_used_at DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            entries = []
            for row in cursor.fetchall():
                entry = dict(row)
                if entry.get("matched_data_json") and isinstance(entry["matched_data_json"], str):
                    try:
                        entry["matched_data_json"] = json.loads(entry["matched_data_json"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                entries.append(entry)

            return api_success(
                {"entries": entries},
                pagination=build_pagination(page, limit, total),
            )
        except Exception as e:
            return api_error("CACHE_ERROR", str(e), 500)

    @bp.route("/cache/discovery-matches/stats", methods=["GET"])
    @require_api_key
    def discovery_match_cache_stats():
        """Get discovery match cache statistics."""
        try:
            db = get_database()
            conn = db._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as total FROM discovery_match_cache")
            total = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT provider, COUNT(*) as count
                FROM discovery_match_cache
                GROUP BY provider
                ORDER BY count DESC
            """)
            by_provider = {row["provider"]: row["count"] for row in cursor.fetchall()}

            cursor.execute("SELECT SUM(use_count) as total_uses FROM discovery_match_cache")
            total_uses = cursor.fetchone()["total_uses"] or 0

            cursor.execute("SELECT AVG(match_confidence) as avg_confidence FROM discovery_match_cache")
            avg_confidence = cursor.fetchone()["avg_confidence"]

            return api_success({
                "total": total,
                "total_uses": total_uses,
                "avg_confidence": round(avg_confidence, 3) if avg_confidence else None,
                "by_provider": by_provider,
            })
        except Exception as e:
            return api_error("CACHE_ERROR", str(e), 500)
