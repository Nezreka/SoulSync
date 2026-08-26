"""Metadata-cache endpoints, lifted out of web_server.py.

The cache browser: stats, listing, invalidation, and the quality-tier helper
the library page shares (imported back by web_server)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request
import os

from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("api.metadata_cache")

bp = Blueprint("metadata_cache", __name__)

# Injected by configure() at boot.
get_database = None
get_metadata_cache = None
docker_resolve_path = None



def configure(*, get_database, get_metadata_cache, docker_resolve_path):
    globals()['get_database'] = get_database
    globals()['get_metadata_cache'] = get_metadata_cache
    globals()['docker_resolve_path'] = docker_resolve_path


def create_blueprint():
    return bp


@bp.route('/api/metadata-cache/stats', methods=['GET'])
def metadata_cache_stats():
    """Get metadata cache statistics."""
    try:
        cache = get_metadata_cache()
        stats = cache.get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting metadata cache stats: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/metadata-cache/browse', methods=['GET'])
def metadata_cache_browse():
    """Browse cached metadata entities with filtering, search, sorting, and pagination."""
    try:
        cache = get_metadata_cache()
        entity_type = request.args.get('type', 'artist')
        source = request.args.get('source')
        search = request.args.get('search')
        sort = request.args.get('sort', 'last_accessed_at')
        sort_dir = request.args.get('sort_dir', 'desc')
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 48))

        result = cache.browse(
            entity_type=entity_type,
            source=source if source else None,
            search=search if search else None,
            sort=sort,
            sort_dir=sort_dir,
            offset=offset,
            limit=limit
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error browsing metadata cache: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/metadata-cache/entity/<source>/<entity_type>/<path:entity_id>', methods=['GET'])
def metadata_cache_entity_detail(source, entity_type, entity_id):
    """Get detailed view of a single cached entity."""
    try:
        cache = get_metadata_cache()
        detail = cache.get_entity_detail(source, entity_type, entity_id)
        if detail is None:
            return jsonify({"error": "Entity not found"}), 404
        return jsonify(detail)
    except Exception as e:
        logger.error(f"Error getting metadata cache entity: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/metadata-cache/browse-musicbrainz', methods=['GET'])
def metadata_cache_browse_musicbrainz():
    """Browse MusicBrainz cache entries in the same format as metadata cache browse."""
    try:
        entity_type = request.args.get('entity_type', 'artist')
        search = request.args.get('search', '').strip()
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 48))
        offset = (page - 1) * limit

        database = get_database()
        conn = database._get_connection()
        try:
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

            cursor.execute(f"SELECT COUNT(*) FROM musicbrainz_cache {where_clause}", params)
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT * FROM musicbrainz_cache
                {where_clause}
                ORDER BY last_updated DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            items = []
            for row in cursor.fetchall():
                r = dict(row)
                matched = r.get('musicbrainz_id') is not None
                items.append({
                    'entity_id': r.get('musicbrainz_id') or f"mb-{r.get('entity_type','')}-{r.get('entity_name','')}",
                    'source': 'musicbrainz',
                    'name': r.get('entity_name', ''),
                    'artist_name': r.get('artist_name', ''),
                    'image_url': None,
                    'popularity': int((r.get('match_confidence') or 0) * 100),
                    'access_count': 1,
                    'last_accessed_at': r.get('last_updated', ''),
                    'created_at': r.get('last_updated', ''),
                    '_mb_matched': matched,
                    '_mb_id': r.get('musicbrainz_id', ''),
                })

            return jsonify({'items': items, 'total': total, 'offset': offset})
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error browsing MusicBrainz cache: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/metadata-cache/clear', methods=['DELETE'])
@admin_only
def metadata_cache_clear():
    """Clear cached metadata. Optional query params: source, type."""
    try:
        cache = get_metadata_cache()
        source = request.args.get('source')
        entity_type = request.args.get('type')
        cleared = cache.clear(
            source=source if source else None,
            entity_type=entity_type if entity_type else None
        )
        return jsonify({"success": True, "cleared": cleared})
    except Exception as e:
        logger.error(f"Error clearing metadata cache: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/metadata-cache/evict', methods=['POST'])
@admin_only
def metadata_cache_evict():
    """Evict expired entries from the metadata cache."""
    try:
        cache = get_metadata_cache()
        evicted = cache.evict_expired()
        return jsonify({"success": True, "evicted": evicted})
    except Exception as e:
        logger.error(f"Error evicting metadata cache: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/metadata-cache/clear-musicbrainz', methods=['DELETE'])
@admin_only
def metadata_cache_clear_musicbrainz():
    """Clear MusicBrainz cache entries. Optional query param: failed_only=true."""
    try:
        cache = get_metadata_cache()
        failed_only = request.args.get('failed_only', '').lower() == 'true'
        cleared = cache.clear_musicbrainz(failed_only=failed_only)
        return jsonify({"success": True, "cleared": cleared})
    except Exception as e:
        logger.error(f"Error clearing MusicBrainz cache: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/metadata-cache/failed-mb-lookups', methods=['GET'])
def metadata_cache_failed_mb_lookups():
    """Get all failed MusicBrainz lookups with pagination and filtering."""
    try:
        entity_type = request.args.get('entity_type', '')
        search = request.args.get('search', '').strip()
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        offset = (page - 1) * limit
        # Only fetch type_counts on first load (page 1, no filters) — frontend caches them
        include_counts = request.args.get('counts', '').lower() == 'true'

        database = get_database()
        conn = database._get_connection()
        try:
            cursor = conn.cursor()
            where_parts = ["musicbrainz_id IS NULL"]
            params = []
            if entity_type:
                where_parts.append("entity_type = ?")
                params.append(entity_type)
            if search:
                where_parts.append("(entity_name LIKE ? COLLATE NOCASE OR artist_name LIKE ? COLLATE NOCASE)")
                params.extend([f"%{search}%", f"%{search}%"])

            where_clause = f"WHERE {' AND '.join(where_parts)}"

            # Single query: fetch items + use SQL window for total count
            cursor.execute(f"""
                SELECT id, entity_type, entity_name, artist_name, match_confidence, last_updated,
                       COUNT(*) OVER() as _total
                FROM musicbrainz_cache {where_clause}
                ORDER BY last_updated DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])

            rows = cursor.fetchall()
            total = rows[0]['_total'] if rows else 0
            items = [{
                'id': r['id'],
                'entity_type': r['entity_type'],
                'entity_name': r['entity_name'],
                'artist_name': r['artist_name'] or '',
                'confidence': r['match_confidence'] or 0,
                'last_updated': r['last_updated'] or '',
            } for r in rows]

            result = {'items': items, 'total': total, 'page': page}

            # Type counts only when requested (avoids full table scan on every tab switch)
            if include_counts:
                cursor.execute("""
                    SELECT entity_type, COUNT(*) as cnt
                    FROM musicbrainz_cache WHERE musicbrainz_id IS NULL
                    GROUP BY entity_type
                """)
                result['type_counts'] = {row['entity_type']: row['cnt'] for row in cursor.fetchall()}

            return jsonify(result)
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error getting failed MB lookups: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/metadata-cache/mb-entry/<int:entry_id>', methods=['DELETE'])
def metadata_cache_delete_mb_entry(entry_id):
    """Delete a single MusicBrainz cache entry by ID."""
    try:
        database = get_database()
        conn = database._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM musicbrainz_cache WHERE id = ?", (entry_id,))
            conn.commit()
            return jsonify({"success": True, "deleted": cursor.rowcount})
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error deleting MB cache entry: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/metadata-cache/mb-match', methods=['POST'])
def metadata_cache_save_mb_match():
    """Save a manual MusicBrainz match for a failed lookup."""
    try:
        data = request.get_json()
        entry_id = data.get('entry_id')
        mbid = data.get('mbid', '').strip()
        mb_name = data.get('mb_name', '').strip()

        if not entry_id or not mbid:
            return jsonify({"success": False, "error": "Missing entry_id or mbid"}), 400

        database = get_database()
        conn = database._get_connection()
        try:
            cursor = conn.cursor()
            # Update the failed entry with the user-selected MBID
            cursor.execute("""
                UPDATE musicbrainz_cache
                SET musicbrainz_id = ?, match_confidence = 100, metadata_json = ?, last_updated = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (mbid, json.dumps({'name': mb_name, 'manual_match': True}), entry_id))
            conn.commit()

            if cursor.rowcount == 0:
                return jsonify({"success": False, "error": "Entry not found"}), 404

            logger.info(f"Manual MB match: entry {entry_id} → {mbid} ({mb_name})")
            return jsonify({"success": True})
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error saving MB match: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


