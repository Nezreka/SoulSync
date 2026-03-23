"""
Universal Metadata Cache — caches all Spotify and iTunes API responses.

Stores full JSON responses alongside structured queryable fields for browsing.
Transparent to callers: check cache before API call, store after success.
"""

import json
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

# Singleton
_cache_instance = None
_cache_lock = threading.Lock()


def get_metadata_cache():
    """Get or create the singleton MetadataCache instance."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = MetadataCache()
    return _cache_instance


class MetadataCache:
    """Caches Spotify and iTunes API responses with structured fields + raw JSON."""

    def __init__(self):
        # Tables are created by MusicDatabase migration — we just use get_database()
        pass

    def _get_db(self):
        from database.music_database import get_database
        return get_database()

    # ─── Entity Methods ───────────────────────────────────────────────

    def get_entity(self, source: str, entity_type: str, entity_id: str) -> Optional[dict]:
        """Look up a cached entity. Returns parsed raw_json dict on hit, None on miss."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, raw_json, updated_at, ttl_days FROM metadata_cache_entities
                    WHERE source = ? AND entity_type = ? AND entity_id = ?
                """, (source, entity_type, entity_id))
                row = cursor.fetchone()
                if row:
                    # Inline TTL check — don't serve stale data
                    try:
                        updated = datetime.fromisoformat(row['updated_at'])
                        age_days = (datetime.now() - updated).days
                        if age_days > (row['ttl_days'] or 30):
                            cursor.execute("DELETE FROM metadata_cache_entities WHERE id = ?", (row['id'],))
                            conn.commit()
                            return None
                    except (ValueError, TypeError):
                        pass

                    # Touch: update access stats
                    cursor.execute("""
                        UPDATE metadata_cache_entities
                        SET last_accessed_at = CURRENT_TIMESTAMP, access_count = access_count + 1
                        WHERE id = ?
                    """, (row['id'],))
                    conn.commit()
                    return json.loads(row['raw_json'])
                return None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Cache lookup error ({source}/{entity_type}/{entity_id}): {e}")
            return None

    def store_entity(self, source: str, entity_type: str, entity_id: str, raw_data: dict) -> None:
        """Store an entity in the cache. Extracts structured fields from raw_data."""
        if not entity_id or not raw_data:
            return
        try:
            fields = self._extract_fields(source, entity_type, raw_data)
            raw_json = json.dumps(raw_data, default=str)
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO metadata_cache_entities
                    (source, entity_type, entity_id, name, image_url, external_urls,
                     genres, popularity, followers,
                     artist_name, artist_id, release_date, total_tracks, album_type, label,
                     album_name, album_id, duration_ms, track_number, disc_number, explicit, isrc, preview_url,
                     raw_json, updated_at, last_accessed_at, access_count)
                    VALUES (?, ?, ?, ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                            COALESCE((SELECT access_count FROM metadata_cache_entities
                                      WHERE source = ? AND entity_type = ? AND entity_id = ?), 0) + 1)
                """, (
                    source, entity_type, entity_id,
                    fields.get('name', ''),
                    fields.get('image_url'),
                    fields.get('external_urls'),
                    fields.get('genres'),
                    fields.get('popularity'),
                    fields.get('followers'),
                    fields.get('artist_name'),
                    fields.get('artist_id'),
                    fields.get('release_date'),
                    fields.get('total_tracks'),
                    fields.get('album_type'),
                    fields.get('label'),
                    fields.get('album_name'),
                    fields.get('album_id'),
                    fields.get('duration_ms'),
                    fields.get('track_number'),
                    fields.get('disc_number'),
                    fields.get('explicit'),
                    fields.get('isrc'),
                    fields.get('preview_url'),
                    raw_json,
                    source, entity_type, entity_id,
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Cache store error ({source}/{entity_type}/{entity_id}): {e}")

    def store_entities_bulk(self, source: str, entity_type: str, items: List[Tuple[str, dict]],
                            skip_if_exists: bool = False) -> None:
        """Store multiple entities at once. items = [(entity_id, raw_data), ...]

        Args:
            skip_if_exists: If True, don't overwrite existing entries. Use this for
                opportunistic caching of simplified data (e.g. from list endpoints)
                to avoid replacing richer data from detail endpoints.
        """
        if not items:
            return
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                for entity_id, raw_data in items:
                    if not entity_id or not raw_data:
                        continue

                    if skip_if_exists:
                        cursor.execute("""
                            SELECT 1 FROM metadata_cache_entities
                            WHERE source = ? AND entity_type = ? AND entity_id = ?
                        """, (source, entity_type, entity_id))
                        if cursor.fetchone():
                            continue

                    fields = self._extract_fields(source, entity_type, raw_data)
                    raw_json = json.dumps(raw_data, default=str)
                    cursor.execute("""
                        INSERT OR REPLACE INTO metadata_cache_entities
                        (source, entity_type, entity_id, name, image_url, external_urls,
                         genres, popularity, followers,
                         artist_name, artist_id, release_date, total_tracks, album_type, label,
                         album_name, album_id, duration_ms, track_number, disc_number, explicit, isrc, preview_url,
                         raw_json, updated_at, last_accessed_at, access_count)
                        VALUES (?, ?, ?, ?, ?, ?,
                                ?, ?, ?,
                                ?, ?, ?, ?, ?, ?,
                                ?, ?, ?, ?, ?, ?, ?, ?,
                                ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                                COALESCE((SELECT access_count FROM metadata_cache_entities
                                          WHERE source = ? AND entity_type = ? AND entity_id = ?), 0) + 1)
                    """, (
                        source, entity_type, entity_id,
                        fields.get('name', ''),
                        fields.get('image_url'),
                        fields.get('external_urls'),
                        fields.get('genres'),
                        fields.get('popularity'),
                        fields.get('followers'),
                        fields.get('artist_name'),
                        fields.get('artist_id'),
                        fields.get('release_date'),
                        fields.get('total_tracks'),
                        fields.get('album_type'),
                        fields.get('label'),
                        fields.get('album_name'),
                        fields.get('album_id'),
                        fields.get('duration_ms'),
                        fields.get('track_number'),
                        fields.get('disc_number'),
                        fields.get('explicit'),
                        fields.get('isrc'),
                        fields.get('preview_url'),
                        raw_json,
                        source, entity_type, entity_id,
                    ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Cache bulk store error ({source}/{entity_type}): {e}")

    def get_entities_batch(self, source: str, entity_type: str,
                           entity_ids: List[str]) -> Tuple[Dict[str, dict], List[str]]:
        """Batch cache lookup. Returns (found_dict, missing_ids)."""
        found = {}
        missing = []
        if not entity_ids:
            return found, missing
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                # Batch query in chunks of 500 to avoid SQLite variable limit
                for i in range(0, len(entity_ids), 500):
                    chunk = entity_ids[i:i + 500]
                    placeholders = ','.join('?' * len(chunk))
                    cursor.execute(f"""
                        SELECT entity_id, raw_json FROM metadata_cache_entities
                        WHERE source = ? AND entity_type = ? AND entity_id IN ({placeholders})
                    """, [source, entity_type] + chunk)
                    for row in cursor.fetchall():
                        found[row['entity_id']] = json.loads(row['raw_json'])
                    # Touch all found entries
                    if found:
                        found_in_chunk = [eid for eid in chunk if eid in found]
                        if found_in_chunk:
                            ph2 = ','.join('?' * len(found_in_chunk))
                            cursor.execute(f"""
                                UPDATE metadata_cache_entities
                                SET last_accessed_at = CURRENT_TIMESTAMP, access_count = access_count + 1
                                WHERE source = ? AND entity_type = ? AND entity_id IN ({ph2})
                            """, [source, entity_type] + found_in_chunk)
                conn.commit()
            finally:
                conn.close()
            missing = [eid for eid in entity_ids if eid not in found]
        except Exception as e:
            logger.debug(f"Cache batch lookup error: {e}")
            missing = entity_ids
        return found, missing

    # ─── Search Cache Methods ─────────────────────────────────────────

    def get_search_results(self, source: str, search_type: str,
                           query: str, limit: int) -> Optional[List[dict]]:
        """Look up cached search results. Returns list of raw_json dicts or None."""
        normalized = query.strip().lower()
        if not normalized:
            return None
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, result_ids, created_at FROM metadata_cache_searches
                    WHERE source = ? AND search_type = ? AND query_normalized = ? AND search_limit = ?
                """, (source, search_type, normalized, limit))
                row = cursor.fetchone()
                if not row:
                    return None

                # Check TTL (7 days for searches)
                try:
                    created = datetime.fromisoformat(row['created_at'])
                    age_days = (datetime.now() - created).days
                    if age_days > 7:
                        # Expired — delete and return miss
                        cursor.execute("DELETE FROM metadata_cache_searches WHERE id = ?", (row['id'],))
                        conn.commit()
                        return None
                except (ValueError, TypeError):
                    pass

                # Touch search entry
                cursor.execute("""
                    UPDATE metadata_cache_searches
                    SET last_accessed_at = CURRENT_TIMESTAMP, access_count = access_count + 1
                    WHERE id = ?
                """, (row['id'],))
                conn.commit()

                # Resolve entity IDs to full data
                result_ids = json.loads(row['result_ids'])
                if not result_ids:
                    return []

                results = []
                for eid in result_ids:
                    cursor.execute("""
                        SELECT raw_json FROM metadata_cache_entities
                        WHERE source = ? AND entity_type = ? AND entity_id = ?
                    """, (source, search_type, eid))
                    erow = cursor.fetchone()
                    if erow:
                        results.append(json.loads(erow['raw_json']))

                # Only return if we found all (or most) entries — partial results are unreliable
                if len(results) >= len(result_ids) * 0.8:
                    return results
                return None
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Search cache lookup error ({source}/{search_type}/{query}): {e}")
            return None

    def store_search_results(self, source: str, search_type: str, query: str,
                             limit: int, entity_ids: List[str]) -> None:
        """Store search result mapping. Individual entities should already be stored."""
        normalized = query.strip().lower()
        if not normalized or not entity_ids:
            return
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO metadata_cache_searches
                    (source, search_type, query_normalized, query_original, result_ids,
                     result_count, search_limit, last_accessed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    source, search_type, normalized, query.strip(),
                    json.dumps(entity_ids), len(entity_ids), limit,
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Search cache store error ({source}/{search_type}/{query}): {e}")

    # ─── Browsing (for UI) ────────────────────────────────────────────

    def browse(self, entity_type: str, source: str = None, search: str = None,
               sort: str = 'last_accessed_at', sort_dir: str = 'desc',
               offset: int = 0, limit: int = 48) -> dict:
        """Paginated browse of cached entities for the UI."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                where_clauses = ['entity_type = ?']
                params = [entity_type]

                # Exclude pseudo-entities like album_id_tracks and track_id_features
                where_clauses.append(r"entity_id NOT LIKE '%\_tracks' ESCAPE '\'")
                where_clauses.append(r"entity_id NOT LIKE '%\_features' ESCAPE '\'")

                if source:
                    where_clauses.append('source = ?')
                    params.append(source)

                if search:
                    search_term = f'%{search}%'
                    where_clauses.append('(name LIKE ? OR artist_name LIKE ? OR album_name LIKE ?)')
                    params.extend([search_term, search_term, search_term])

                where_sql = ' AND '.join(where_clauses)

                # Count total
                cursor.execute(f"SELECT COUNT(*) as cnt FROM metadata_cache_entities WHERE {where_sql}", params)
                total = cursor.fetchone()['cnt']

                # Validate sort column
                valid_sorts = {'last_accessed_at', 'created_at', 'access_count', 'name', 'popularity', 'updated_at'}
                if sort not in valid_sorts:
                    sort = 'last_accessed_at'
                direction = 'ASC' if sort_dir == 'asc' else 'DESC'

                cursor.execute(f"""
                    SELECT id, source, entity_type, entity_id, name, image_url,
                           genres, popularity, followers,
                           artist_name, artist_id, release_date, total_tracks, album_type, label,
                           album_name, album_id, duration_ms, track_number, disc_number, explicit,
                           isrc, preview_url, external_urls,
                           created_at, updated_at, last_accessed_at, access_count
                    FROM metadata_cache_entities
                    WHERE {where_sql}
                    ORDER BY {sort} {direction}
                    LIMIT ? OFFSET ?
                """, params + [limit, offset])

                items = []
                for row in cursor.fetchall():
                    item = dict(row)
                    # Parse JSON fields for the UI
                    for json_field in ('genres', 'external_urls'):
                        if item.get(json_field):
                            try:
                                item[json_field] = json.loads(item[json_field])
                            except (json.JSONDecodeError, TypeError):
                                pass
                    items.append(item)

                return {'items': items, 'total': total, 'offset': offset, 'limit': limit}
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache browse error: {e}")
            return {'items': [], 'total': 0, 'offset': offset, 'limit': limit}

    def get_entity_detail(self, source: str, entity_type: str, entity_id: str) -> Optional[dict]:
        """Get full entity detail including parsed raw_json for the detail modal."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM metadata_cache_entities
                    WHERE source = ? AND entity_type = ? AND entity_id = ?
                """, (source, entity_type, entity_id))
                row = cursor.fetchone()
                if not row:
                    return None

                # Touch
                cursor.execute("""
                    UPDATE metadata_cache_entities
                    SET last_accessed_at = CURRENT_TIMESTAMP, access_count = access_count + 1
                    WHERE id = ?
                """, (row['id'],))
                conn.commit()

                item = dict(row)
                # Parse JSON fields
                for json_field in ('genres', 'external_urls', 'raw_json'):
                    if item.get(json_field):
                        try:
                            item[json_field] = json.loads(item[json_field])
                        except (json.JSONDecodeError, TypeError):
                            pass
                return item
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache detail error: {e}")
            return None

    # ─── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get cache statistics for the dashboard tool card and modal stats bar."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                stats = {
                    'artists': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                    'albums': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                    'tracks': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                    'searches': 0,
                    'total_entries': 0,
                    'total_hits': 0,
                    'oldest': None,
                    'newest': None,
                }

                # Count by type and source (exclude pseudo-entities like _tracks, _features)
                cursor.execute(r"""
                    SELECT entity_type, source, COUNT(*) as cnt, SUM(access_count) as hits
                    FROM metadata_cache_entities
                    WHERE entity_id NOT LIKE '%\_tracks' ESCAPE '\'
                      AND entity_id NOT LIKE '%\_features' ESCAPE '\'
                    GROUP BY entity_type, source
                """)
                type_key_map = {'artist': 'artists', 'album': 'albums', 'track': 'tracks'}
                for row in cursor.fetchall():
                    et = type_key_map.get(row['entity_type'])
                    src = row['source']
                    if et and et in stats and src in stats[et]:
                        stats[et][src] = row['cnt']
                    stats['total_entries'] += row['cnt']
                    stats['total_hits'] += (row['hits'] or 0)

                # Search count
                cursor.execute("SELECT COUNT(*) as cnt FROM metadata_cache_searches")
                stats['searches'] = cursor.fetchone()['cnt']

                # Oldest and newest
                cursor.execute("SELECT MIN(created_at) as oldest, MAX(created_at) as newest FROM metadata_cache_entities")
                row = cursor.fetchone()
                if row:
                    stats['oldest'] = row['oldest']
                    stats['newest'] = row['newest']

                return stats
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache stats error: {e}")
            return {
                'artists': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                'albums': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                'tracks': {'spotify': 0, 'itunes': 0, 'deezer': 0},
                'searches': 0, 'total_entries': 0, 'total_hits': 0,
                'oldest': None, 'newest': None,
            }

    # ─── Maintenance ──────────────────────────────────────────────────

    def evict_expired(self) -> int:
        """Delete entries that have exceeded their TTL. Returns count of evicted entries."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                # Entities
                cursor.execute("""
                    DELETE FROM metadata_cache_entities
                    WHERE julianday('now') - julianday(updated_at) > ttl_days
                """)
                entity_count = cursor.rowcount

                # Searches
                cursor.execute("""
                    DELETE FROM metadata_cache_searches
                    WHERE julianday('now') - julianday(created_at) > ttl_days
                """)
                search_count = cursor.rowcount

                conn.commit()
                total = entity_count + search_count
                if total > 0:
                    logger.info(f"Evicted {total} expired cache entries ({entity_count} entities, {search_count} searches)")
                return total
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache eviction error: {e}")
            return 0

    def clear(self, source: str = None, entity_type: str = None) -> int:
        """Clear cache entries. Optional filters by source and/or entity_type."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                # Clear entities
                where_parts = []
                params = []
                if source:
                    where_parts.append('source = ?')
                    params.append(source)
                if entity_type:
                    where_parts.append('entity_type = ?')
                    params.append(entity_type)

                if where_parts:
                    where_sql = ' AND '.join(where_parts)
                    cursor.execute(f"DELETE FROM metadata_cache_entities WHERE {where_sql}", params)
                else:
                    cursor.execute("DELETE FROM metadata_cache_entities")
                entity_count = cursor.rowcount

                # Clear searches (match source and entity_type → search_type)
                search_where = []
                search_params = []
                if source:
                    search_where.append('source = ?')
                    search_params.append(source)
                if entity_type:
                    search_where.append('search_type = ?')
                    search_params.append(entity_type)

                if search_where:
                    cursor.execute(f"DELETE FROM metadata_cache_searches WHERE {' AND '.join(search_where)}", search_params)
                else:
                    cursor.execute("DELETE FROM metadata_cache_searches")
                search_count = cursor.rowcount

                conn.commit()
                total = entity_count + search_count
                logger.info(f"Cleared {total} cache entries (source={source}, type={entity_type})")
                return total
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return 0

    # ─── Field Extraction ─────────────────────────────────────────────

    def _extract_fields(self, source: str, entity_type: str, raw_data: dict) -> dict:
        """Extract structured queryable fields from a raw API response."""
        if source == 'spotify':
            return self._extract_spotify_fields(entity_type, raw_data)
        elif source == 'itunes':
            return self._extract_itunes_fields(entity_type, raw_data)
        elif source == 'deezer':
            return self._extract_deezer_fields(entity_type, raw_data)
        elif source == 'beatport':
            return self._extract_beatport_fields(entity_type, raw_data)
        return {'name': str(raw_data.get('name', raw_data.get('trackName', '')))}

    def _extract_spotify_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from Spotify API response."""
        fields = {}

        if entity_type == 'artist':
            fields['name'] = data.get('name', '')
            fields['genres'] = json.dumps(data.get('genres', []))
            fields['popularity'] = data.get('popularity', 0)
            followers = data.get('followers')
            fields['followers'] = followers.get('total', 0) if isinstance(followers, dict) else 0
            images = data.get('images', [])
            fields['image_url'] = images[0]['url'] if images else None
            fields['external_urls'] = json.dumps(data.get('external_urls', {}))

        elif entity_type == 'album':
            fields['name'] = data.get('name', '')
            artists = data.get('artists', [])
            if artists:
                fields['artist_name'] = artists[0].get('name', '')
                fields['artist_id'] = artists[0].get('id', '')
            fields['release_date'] = data.get('release_date', '')
            fields['total_tracks'] = data.get('total_tracks', 0)
            fields['album_type'] = data.get('album_type', 'album')
            fields['label'] = data.get('label', '')
            images = data.get('images', [])
            fields['image_url'] = images[0]['url'] if images else None
            fields['genres'] = json.dumps(data.get('genres', []))
            fields['external_urls'] = json.dumps(data.get('external_urls', {}))

        elif entity_type == 'track':
            fields['name'] = data.get('name', '')
            artists = data.get('artists', [])
            if artists:
                fields['artist_name'] = artists[0].get('name', '')
                fields['artist_id'] = artists[0].get('id', '')
            album = data.get('album', {})
            fields['album_name'] = album.get('name', '')
            fields['album_id'] = album.get('id', '')
            album_images = album.get('images', [])
            fields['image_url'] = album_images[0]['url'] if album_images else None
            fields['duration_ms'] = data.get('duration_ms', 0)
            fields['track_number'] = data.get('track_number')
            fields['disc_number'] = data.get('disc_number', 1)
            fields['explicit'] = 1 if data.get('explicit') else 0
            fields['popularity'] = data.get('popularity', 0)
            ext_ids = data.get('external_ids', {})
            fields['isrc'] = ext_ids.get('isrc') if isinstance(ext_ids, dict) else None
            fields['preview_url'] = data.get('preview_url')
            fields['external_urls'] = json.dumps(data.get('external_urls', {}))

        return fields

    def _extract_deezer_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from Deezer API response."""
        fields = {}

        if entity_type == 'artist':
            fields['name'] = data.get('name', '')
            fields['genres'] = '[]'
            fields['popularity'] = 0
            fields['followers'] = data.get('nb_fan', 0)
            fields['image_url'] = data.get('picture_xl') or data.get('picture_big') or data.get('picture_medium')
            urls = {}
            if data.get('link'):
                urls['deezer'] = data['link']
            fields['external_urls'] = json.dumps(urls)

        elif entity_type == 'album':
            fields['name'] = data.get('title', '')
            artist = data.get('artist', {})
            fields['artist_name'] = artist.get('name', '') if isinstance(artist, dict) else ''
            fields['artist_id'] = str(artist.get('id', '')) if isinstance(artist, dict) else ''
            fields['release_date'] = data.get('release_date', '')
            fields['total_tracks'] = data.get('nb_tracks', 0)
            record_type = data.get('record_type', 'album')
            fields['album_type'] = record_type if record_type in ('single', 'ep', 'album') else 'album'
            fields['label'] = data.get('label', '')
            fields['image_url'] = data.get('cover_xl') or data.get('cover_big') or data.get('cover_medium')
            urls = {}
            if data.get('link'):
                urls['deezer'] = data['link']
            fields['external_urls'] = json.dumps(urls)

        elif entity_type == 'track':
            fields['name'] = data.get('title', '')
            artist = data.get('artist', {})
            fields['artist_name'] = artist.get('name', '') if isinstance(artist, dict) else ''
            fields['artist_id'] = str(artist.get('id', '')) if isinstance(artist, dict) else ''
            album = data.get('album', {})
            fields['album_name'] = album.get('title', '') if isinstance(album, dict) else ''
            fields['album_id'] = str(album.get('id', '')) if isinstance(album, dict) else ''
            fields['image_url'] = (album.get('cover_xl') or album.get('cover_big') or album.get('cover_medium')) if isinstance(album, dict) else None
            fields['duration_ms'] = data.get('duration', 0) * 1000
            fields['track_number'] = data.get('track_position')
            fields['disc_number'] = data.get('disk_number', 1)
            fields['explicit'] = 1 if data.get('explicit_lyrics') else 0
            fields['popularity'] = data.get('rank', 0)
            fields['isrc'] = data.get('isrc')
            fields['preview_url'] = data.get('preview')
            urls = {}
            if data.get('link'):
                urls['deezer'] = data['link']
            fields['external_urls'] = json.dumps(urls)

        return fields

    def _extract_itunes_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from iTunes API response."""
        fields = {}

        def _upscale_artwork(url):
            """Convert iTunes 100x100 artwork to 600x600."""
            if url and '100x100' in url:
                return url.replace('100x100', '600x600')
            return url

        if entity_type == 'artist':
            fields['name'] = data.get('artistName', '')
            genre = data.get('primaryGenreName', '')
            fields['genres'] = json.dumps([genre] if genre else [])
            fields['popularity'] = 0
            fields['followers'] = 0
            fields['image_url'] = _upscale_artwork(data.get('artworkUrl100'))
            urls = {}
            if data.get('artistViewUrl'):
                urls['itunes'] = data['artistViewUrl']
            fields['external_urls'] = json.dumps(urls)

        elif entity_type == 'album':
            fields['name'] = data.get('collectionName', '')
            fields['artist_name'] = data.get('artistName', '')
            fields['artist_id'] = str(data.get('artistId', ''))
            fields['release_date'] = data.get('releaseDate', '')[:10] if data.get('releaseDate') else ''
            fields['total_tracks'] = data.get('trackCount', 0)
            # Infer album type from track count
            tc = data.get('trackCount', 0)
            if tc <= 3:
                fields['album_type'] = 'single'
            elif tc <= 6:
                fields['album_type'] = 'ep'
            else:
                fields['album_type'] = 'album'
            fields['image_url'] = _upscale_artwork(data.get('artworkUrl100'))
            urls = {}
            if data.get('collectionViewUrl'):
                urls['itunes'] = data['collectionViewUrl']
            fields['external_urls'] = json.dumps(urls)

        elif entity_type == 'track':
            fields['name'] = data.get('trackName', '')
            fields['artist_name'] = data.get('artistName', '')
            fields['artist_id'] = str(data.get('artistId', ''))
            fields['album_name'] = data.get('collectionName', '')
            fields['album_id'] = str(data.get('collectionId', ''))
            fields['image_url'] = _upscale_artwork(data.get('artworkUrl100'))
            fields['duration_ms'] = data.get('trackTimeMillis', 0)
            fields['track_number'] = data.get('trackNumber')
            fields['disc_number'] = data.get('discNumber', 1)
            fields['explicit'] = 1 if data.get('trackExplicitness') == 'explicit' else 0
            fields['preview_url'] = data.get('previewUrl')
            urls = {}
            if data.get('trackViewUrl'):
                urls['itunes'] = data['trackViewUrl']
            fields['external_urls'] = json.dumps(urls)

        return fields

    def _extract_beatport_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from Beatport enriched track data."""
        fields = {}

        if entity_type == 'track':
            fields['name'] = data.get('title', '')
            fields['artist_name'] = data.get('artist', '')
            fields['album_name'] = data.get('release_name', '')
            fields['album_id'] = data.get('release_id', '')
            fields['image_url'] = data.get('release_image', '')
            fields['label'] = data.get('label', '')
            fields['release_date'] = data.get('release_date', '')
            # Beatport duration is in seconds, convert to ms
            duration = data.get('duration', 0)
            fields['duration_ms'] = int(duration) * 1000 if duration else 0
            fields['track_number'] = data.get('position')
            fields['genres'] = json.dumps([data['genre']]) if data.get('genre') else '[]'
            urls = {}
            if data.get('url'):
                urls['beatport'] = data['url']
            fields['external_urls'] = json.dumps(urls)

        return fields

    # ─── Discovery Methods (mine cache for recommendations) ──────

    def get_undiscovered_albums(self, top_artist_names, library_album_keys, source=None, limit=20):
        """Find popular cached albums by user's top artists that aren't in their library."""
        if not top_artist_names:
            return []
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                placeholders = ','.join(['?'] * len(top_artist_names))
                params = [a.lower() for a in top_artist_names]
                source_filter = "AND source = ?" if source else ""
                if source:
                    params.append(source)
                cursor.execute(f"""
                    SELECT name, artist_name, image_url, popularity, release_date, label,
                           source, entity_id, album_type, total_tracks
                    FROM metadata_cache_entities
                    WHERE entity_type = 'album'
                      AND LOWER(artist_name) IN ({placeholders})
                      {source_filter}
                    ORDER BY COALESCE(popularity, 0) DESC, access_count DESC
                    LIMIT 200
                """, params)
                results = []
                for row in cursor.fetchall():
                    key = (row['name'].lower().strip(), row['artist_name'].lower().strip())
                    if key not in library_album_keys:
                        results.append(dict(row))
                        if len(results) >= limit:
                            break
                return results
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Undiscovered albums error: {e}")
            return []

    def get_genre_new_releases(self, user_genres, source=None, limit=20):
        """Find recently released cached albums matching user's genres."""
        if not user_genres:
            return []
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                genre_clauses = ' OR '.join(['genres LIKE ?' for _ in user_genres])
                params = [f'%{g}%' for g in user_genres]
                source_filter = ""
                if source:
                    source_filter = "AND source = ?"
                    params.append(source)
                cursor.execute(f"""
                    SELECT name, artist_name, image_url, popularity, release_date, genres,
                           source, entity_id, album_type, total_tracks
                    FROM metadata_cache_entities
                    WHERE entity_type = 'album'
                      AND release_date != '' AND release_date IS NOT NULL
                      AND release_date >= date('now', '-180 days')
                      AND ({genre_clauses})
                      {source_filter}
                    ORDER BY release_date DESC, COALESCE(popularity, 0) DESC
                    LIMIT ?
                """, params + [limit])
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Genre new releases error: {e}")
            return []

    def get_label_explorer(self, user_labels, source=None, limit=20):
        """Find popular cached albums from labels the user already has."""
        if not user_labels:
            return []
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                placeholders = ','.join(['?'] * len(user_labels))
                params = list(user_labels)
                source_filter = ""
                if source:
                    source_filter = "AND source = ?"
                    params.append(source)
                cursor.execute(f"""
                    SELECT name, artist_name, image_url, popularity, release_date, label,
                           source, entity_id, album_type, total_tracks
                    FROM metadata_cache_entities
                    WHERE entity_type = 'album'
                      AND label IN ({placeholders})
                      {source_filter}
                    ORDER BY COALESCE(popularity, 0) DESC, access_count DESC
                    LIMIT ?
                """, params + [limit])
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Label explorer error: {e}")
            return []

    def get_deep_cuts(self, artist_names, source=None, popularity_cap=30, limit=20):
        """Find low-popularity tracks from artists the user listens to."""
        if not artist_names:
            return []
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                placeholders = ','.join(['?'] * len(artist_names))
                params = [a.lower() for a in artist_names]
                source_filter = ""
                if source:
                    source_filter = "AND source = ?"
                    params.append(source)
                cursor.execute(f"""
                    SELECT name, artist_name, image_url, popularity, album_name,
                           source, entity_id, duration_ms, album_id
                    FROM metadata_cache_entities
                    WHERE entity_type = 'track'
                      AND LOWER(artist_name) IN ({placeholders})
                      AND (popularity IS NULL OR popularity <= ?)
                      {source_filter}
                    ORDER BY COALESCE(popularity, 50) ASC, access_count DESC
                    LIMIT ?
                """, params + [popularity_cap, limit])
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Deep cuts error: {e}")
            return []

    def get_genre_deep_dive(self, genre, source=None, artist_limit=12, album_limit=20, track_limit=15):
        """Get artists, albums, and tracks for a genre. Albums don't have genres in Spotify,
        so we find artists with matching genres then fetch their cached albums and tracks."""
        if not genre:
            return {'artists': [], 'albums': [], 'tracks': []}
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                # Step 1: Find artists with this genre
                params = [f'%{genre}%']
                source_filter = "AND source = ?" if source else ""
                if source:
                    params.append(source)
                cursor.execute(f"""
                    SELECT name, image_url, popularity, followers, entity_id, source, genres
                    FROM metadata_cache_entities
                    WHERE entity_type = 'artist'
                      AND genres LIKE ?
                      {source_filter}
                    ORDER BY COALESCE(followers, 0) DESC, COALESCE(popularity, 0) DESC
                    LIMIT ?
                """, params + [artist_limit])
                artists = [dict(r) for r in cursor.fetchall()]

                artist_names = [a['name'].lower() for a in artists if a.get('name')]
                albums = []
                tracks = []

                if artist_names:
                    name_placeholders = ','.join(['?'] * len(artist_names))
                    src_filter = "AND source = ?" if source else ""

                    # Step 2: Find albums by those artists
                    album_params = list(artist_names)
                    if source:
                        album_params.append(source)
                    cursor.execute(f"""
                        SELECT name, artist_name, image_url, popularity, release_date, label,
                               source, entity_id, album_type, total_tracks
                        FROM metadata_cache_entities
                        WHERE entity_type = 'album'
                          AND LOWER(artist_name) IN ({name_placeholders})
                          {src_filter}
                        ORDER BY COALESCE(popularity, 0) DESC, access_count DESC
                        LIMIT ?
                    """, album_params + [album_limit])
                    albums = [dict(r) for r in cursor.fetchall()]

                    # Step 3: Find tracks by those artists
                    track_params = list(artist_names)
                    if source:
                        track_params.append(source)
                    cursor.execute(f"""
                        SELECT name, artist_name, image_url, popularity, album_name,
                               source, entity_id, duration_ms, album_id
                        FROM metadata_cache_entities
                        WHERE entity_type = 'track'
                          AND LOWER(artist_name) IN ({name_placeholders})
                          {src_filter}
                        ORDER BY COALESCE(popularity, 0) DESC, access_count DESC
                        LIMIT ?
                    """, track_params + [track_limit])
                    tracks = [dict(r) for r in cursor.fetchall()]

                # Step 4: Find related genres (co-occurring on same artists)
                related_genres = {}
                for artist in artists:
                    try:
                        artist_genres = json.loads(artist.get('genres', '[]'))
                        if isinstance(artist_genres, list):
                            for g in artist_genres:
                                g_lower = g.strip().lower()
                                if g_lower and g_lower != genre.lower():
                                    related_genres[g_lower] = related_genres.get(g_lower, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass
                related = sorted(
                    [{'genre': g.title(), 'count': c} for g, c in related_genres.items()],
                    key=lambda x: x['count'], reverse=True
                )[:12]

                # Step 5: Check which albums are in the library
                if albums:
                    album_keys = [(a['name'].lower().strip(), a['artist_name'].lower().strip()) for a in albums]
                    or_clauses = ' OR '.join(['(LOWER(al.title) = ? AND LOWER(ar.name) = ?)' for _ in album_keys])
                    lib_params = []
                    for k in album_keys:
                        lib_params.extend(k)
                    cursor.execute(f"""
                        SELECT LOWER(al.title), LOWER(ar.name) FROM albums al
                        JOIN artists ar ON ar.id = al.artist_id
                        WHERE {or_clauses}
                    """, lib_params)
                    lib_set = {(r[0].strip(), r[1].strip()) for r in cursor.fetchall()}
                    for album in albums:
                        album['in_library'] = (album['name'].lower().strip(), album['artist_name'].lower().strip()) in lib_set

                # Step 6: Resolve library artist IDs for navigation
                for artist in artists:
                    try:
                        cursor.execute("""
                            SELECT id FROM artists WHERE LOWER(name) = LOWER(?) LIMIT 1
                        """, (artist['name'],))
                        row = cursor.fetchone()
                        artist['library_id'] = row['id'] if row else None
                    except Exception:
                        artist['library_id'] = None

                return {'artists': artists, 'albums': albums, 'tracks': tracks, 'related_genres': related}
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Genre deep dive error: {e}")
            return {'artists': [], 'albums': [], 'tracks': []}

    def get_genre_explorer(self, user_genres_set, source=None):
        """Aggregate genres from cached artists, highlight unexplored ones."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                params = []
                source_filter = ""
                if source:
                    source_filter = "AND source = ?"
                    params.append(source)
                cursor.execute(f"""
                    SELECT genres FROM metadata_cache_entities
                    WHERE entity_type = 'artist'
                      AND genres IS NOT NULL AND genres != '' AND genres != '[]'
                      {source_filter}
                """, params)
                genre_counts = {}
                for row in cursor.fetchall():
                    try:
                        parsed = json.loads(row['genres'])
                        if isinstance(parsed, list):
                            for g in parsed:
                                g_lower = g.strip().lower()
                                if g_lower:
                                    genre_counts[g_lower] = genre_counts.get(g_lower, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass

                user_lower = {g.lower() for g in user_genres_set} if user_genres_set else set()
                results = []
                for genre, count in sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:50]:
                    results.append({
                        'genre': genre.title(),
                        'artist_count': count,
                        'explored': genre in user_lower,
                    })
                return results
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Genre explorer error: {e}")
            return []
