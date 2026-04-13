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


_backfill_done = False

def get_metadata_cache():
    """Get or create the singleton MetadataCache instance."""
    global _cache_instance, _backfill_done
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = MetadataCache()
    # One-time backfill of Deezer album genres from stored raw_json
    if not _backfill_done:
        _backfill_done = True
        try:
            import threading
            threading.Thread(target=_cache_instance.backfill_deezer_album_genres, daemon=True).start()
        except Exception:
            pass
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

    # Names that indicate junk/placeholder data — should not be cached
    _JUNK_NAMES = frozenset({
        '', 'unknown', 'unknown artist', 'unknown album', 'unknown track',
        'untitled', 'none', 'n/a', 'null',
    })

    def _is_junk_entity(self, fields: dict) -> bool:
        """Check if extracted fields represent junk/placeholder data."""
        name = (fields.get('name') or '').strip().lower()
        if name in self._JUNK_NAMES:
            return True
        # For tracks: reject if artist_name is junk (prevents caching "Song by Unknown Artist")
        artist_name = (fields.get('artist_name') or '').strip().lower()
        if artist_name and artist_name in self._JUNK_NAMES:
            return True
        return False

    def store_entity(self, source: str, entity_type: str, entity_id: str, raw_data: dict) -> None:
        """Store an entity in the cache. Extracts structured fields from raw_data."""
        if not entity_id or not raw_data:
            return
        try:
            fields = self._extract_fields(source, entity_type, raw_data)
            # Skip validation for synthetic cache entries (_features, _tracks suffixes)
            if not entity_id.endswith('_features') and not entity_id.endswith('_tracks') and self._is_junk_entity(fields):
                logger.debug(f"Rejecting junk entity ({source}/{entity_type}/{entity_id}): name='{fields.get('name')}'")
                return
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
                    if self._is_junk_entity(fields):
                        continue
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

                # Don't select raw_json — it's huge and only needed for detail view
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
                    'artists': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
                    'albums': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
                    'tracks': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
                    'searches': 0,
                    'total_entries': 0,
                    'total_hits': 0,
                    'oldest': None,
                    'newest': None,
                }

                # Count by type and source — fast via idx_mce_stats covering index
                cursor.execute("""
                    SELECT entity_type, source, COUNT(*) as cnt, SUM(access_count) as hits
                    FROM metadata_cache_entities
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

                # MusicBrainz cache stats
                try:
                    cursor.execute("SELECT COUNT(*) as cnt FROM musicbrainz_cache")
                    stats['musicbrainz_total'] = cursor.fetchone()['cnt']
                    cursor.execute("SELECT COUNT(*) as cnt FROM musicbrainz_cache WHERE musicbrainz_id IS NULL")
                    stats['musicbrainz_failed'] = cursor.fetchone()['cnt']
                except Exception:
                    stats['musicbrainz_total'] = 0
                    stats['musicbrainz_failed'] = 0

                return stats
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache stats error: {e}")
            return {
                'artists': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
                'albums': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
                'tracks': {'spotify': 0, 'itunes': 0, 'deezer': 0, 'discogs': 0},
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

    def clean_junk_entities(self) -> int:
        """Delete cached entities with empty/placeholder names."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                junk_names = "', '".join(self._JUNK_NAMES - {''})  # exclude empty, handled separately
                cursor.execute(f"""
                    DELETE FROM metadata_cache_entities
                    WHERE (name IS NULL
                       OR TRIM(name) = ''
                       OR LOWER(TRIM(name)) IN ('{junk_names}'))
                      AND entity_id NOT LIKE '%\\_features' ESCAPE '\\'
                      AND entity_id NOT LIKE '%\\_tracks' ESCAPE '\\'
                """)
                count = cursor.rowcount
                conn.commit()
                if count > 0:
                    logger.info(f"Cleaned {count} junk entities from cache")
                return count
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Junk cleanup error: {e}")
            return 0

    def clean_orphaned_searches(self) -> int:
        """Delete search results where <50% of referenced entities still exist."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, source, search_type, result_ids FROM metadata_cache_searches")
                rows = cursor.fetchall()

                dead_ids = []
                for row in rows:
                    try:
                        result_ids = json.loads(row['result_ids'] or '[]')
                    except (json.JSONDecodeError, TypeError):
                        dead_ids.append(row['id'])
                        continue

                    if not result_ids:
                        dead_ids.append(row['id'])
                        continue

                    # Check how many referenced entities still exist
                    placeholders = ','.join('?' * len(result_ids))
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM metadata_cache_entities
                        WHERE source = ? AND entity_type = ? AND entity_id IN ({placeholders})
                    """, [row['source'], row['search_type']] + list(result_ids))
                    found = cursor.fetchone()[0]

                    if found < len(result_ids) * 0.5:
                        dead_ids.append(row['id'])

                if dead_ids:
                    # Delete in chunks to stay under SQLite variable limit
                    for i in range(0, len(dead_ids), 400):
                        chunk = dead_ids[i:i + 400]
                        placeholders = ','.join('?' * len(chunk))
                        cursor.execute(f"DELETE FROM metadata_cache_searches WHERE id IN ({placeholders})", chunk)
                    conn.commit()

                count = len(dead_ids)
                if count > 0:
                    logger.info(f"Cleaned {count} orphaned search results from cache")
                return count
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Orphan search cleanup error: {e}")
            return 0

    def clean_stale_musicbrainz_nulls(self, max_age_days: int = 30) -> int:
        """Delete MusicBrainz cache entries where lookup found nothing (null MBID) and age > max_age_days."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM musicbrainz_cache
                    WHERE musicbrainz_id IS NULL
                      AND julianday('now') - julianday(last_updated) > ?
                """, (max_age_days,))
                count = cursor.rowcount
                conn.commit()
                if count > 0:
                    logger.info(f"Cleaned {count} stale MusicBrainz null entries (>{max_age_days} days)")
                return count
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"MusicBrainz null cleanup error: {e}")
            return 0

    def get_health_stats(self) -> dict:
        """Return cache health statistics for the repair dashboard.
        Consolidated into fewer queries for faster modal open."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                stats = {}

                # Query 1: Main entity stats in one pass (was 7 separate queries)
                junk_names = "', '".join(self._JUNK_NAMES - {''})
                cursor.execute(f"""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN (name IS NULL OR TRIM(name) = '' OR LOWER(TRIM(name)) IN ('{junk_names}'))
                             AND entity_id NOT LIKE '%\\_features' ESCAPE '\\'
                             AND entity_id NOT LIKE '%\\_tracks' ESCAPE '\\'
                             THEN 1 ELSE 0 END) as junk,
                        SUM(CASE WHEN julianday('now') - julianday(updated_at) > ttl_days - 1 THEN 1 ELSE 0 END) as exp_24h,
                        SUM(CASE WHEN julianday('now') - julianday(updated_at) > ttl_days - 7 THEN 1 ELSE 0 END) as exp_7d,
                        ROUND(AVG(julianday('now') - julianday(updated_at)), 1) as avg_age,
                        COALESCE(SUM(access_count), 0) as total_hits
                    FROM metadata_cache_entities
                """)
                row = cursor.fetchone()
                stats['total_entities'] = row[0] or 0
                stats['junk_entities'] = row[1] or 0
                stats['expiring_24h'] = row[2] or 0
                stats['expiring_7d'] = row[3] or 0
                stats['avg_age_days'] = row[4] or 0
                stats['total_access_hits'] = row[5] or 0

                # Query 2: Group counts (source + type in one pass)
                cursor.execute("""
                    SELECT source, entity_type, COUNT(*) as cnt
                    FROM metadata_cache_entities GROUP BY source, entity_type
                """)
                by_source = {}
                by_type = {}
                for r in cursor.fetchall():
                    by_source[r[0]] = by_source.get(r[0], 0) + r[2]
                    by_type[r[1]] = by_type.get(r[1], 0) + r[2]
                stats['by_source'] = by_source
                stats['by_type'] = by_type

                # Query 3: Search count
                cursor.execute("SELECT COUNT(*) FROM metadata_cache_searches")
                stats['total_searches'] = cursor.fetchone()[0]

                # Query 4: MusicBrainz stats (separate table)
                try:
                    cursor.execute("""
                        SELECT COUNT(*) as total,
                               SUM(CASE WHEN musicbrainz_id IS NULL THEN 1 ELSE 0 END) as failed
                        FROM musicbrainz_cache
                    """)
                    mb = cursor.fetchone()
                    stats['total_musicbrainz'] = mb[0] or 0
                    stats['stale_mb_nulls'] = mb[1] or 0
                except Exception:
                    stats['total_musicbrainz'] = 0
                    stats['stale_mb_nulls'] = 0

                return stats
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Cache health stats error: {e}")
            return {}

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

    def clear_musicbrainz(self, failed_only: bool = False) -> int:
        """Clear MusicBrainz cache entries. If failed_only=True, only clears entries with NULL musicbrainz_id."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                if failed_only:
                    cursor.execute("DELETE FROM musicbrainz_cache WHERE musicbrainz_id IS NULL")
                else:
                    cursor.execute("DELETE FROM musicbrainz_cache")
                count = cursor.rowcount
                conn.commit()
                logger.info(f"Cleared {count} MusicBrainz cache entries (failed_only={failed_only})")
                return count
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"MusicBrainz cache clear error: {e}")
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
        elif source == 'discogs':
            return self._extract_discogs_fields(entity_type, raw_data)
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

    # Deezer genre_id → name mapping (from https://api.deezer.com/genre)
    _DEEZER_GENRE_MAP = {
        132: 'Pop', 116: 'Rap/Hip Hop', 122: 'Reggaeton', 152: 'Rock', 113: 'Dance',
        165: 'R&B', 85: 'Alternative', 186: 'Christian', 106: 'Electro', 466: 'Folk',
        144: 'Reggae', 129: 'Jazz', 84: 'Country', 67: 'Salsa', 173: 'Films/Games',
        98: 'Classical', 169: 'Soul & Funk', 2: 'African Music', 16: 'Asian Music',
        153: 'Blues', 75: 'Brazilian Music', 81: 'Indian Music', 95: 'Kids',
        197: 'Latin Music', 73: 'Metal', 464: 'Rap', 174: 'Musicals',
    }

    def _extract_deezer_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from Deezer API response."""
        fields = {}

        if entity_type == 'artist':
            fields['name'] = data.get('name', '')
            # Deezer artists don't have genres directly, but may have genre_id from search context
            genre_id = data.get('genre_id')
            if genre_id and genre_id in self._DEEZER_GENRE_MAP:
                fields['genres'] = json.dumps([self._DEEZER_GENRE_MAP[genre_id]])
            else:
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
            # Deezer full album response: genres in data.genres.data[].name
            # Deezer search response: genre_id (numeric) — map to name
            dz_genres = data.get('genres', {})
            if isinstance(dz_genres, dict):
                dz_genres = dz_genres.get('data', [])
            if isinstance(dz_genres, list) and dz_genres:
                fields['genres'] = json.dumps([g.get('name', '') for g in dz_genres if isinstance(g, dict) and g.get('name')])
            else:
                genre_id = data.get('genre_id')
                if genre_id and genre_id in self._DEEZER_GENRE_MAP:
                    fields['genres'] = json.dumps([self._DEEZER_GENRE_MAP[genre_id]])
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

    def _extract_discogs_fields(self, entity_type: str, data: dict) -> dict:
        """Extract fields from Discogs API response."""
        fields = {}

        # Discogs uses 'name' for artists, 'title' for releases/masters
        if entity_type == 'artist':
            fields['name'] = data.get('name', data.get('title', ''))
            fields['genres'] = json.dumps([])
            fields['popularity'] = 0
            fields['followers'] = 0
            # Images array — prefer primary type
            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                fields['image_url'] = (primary or images[0]).get('uri')
            # Search results use cover_image/thumb — upscale from 150px to 600px
            if not fields.get('image_url'):
                img = data.get('cover_image') or data.get('thumb')
                if img and 'spacer.gif' not in img:
                    fields['image_url'] = img

        elif entity_type == 'album':
            # Handle "Artist - Title" format from search results
            raw_title = data.get('title', '')
            if data.get('artists'):
                fields['name'] = raw_title
                fields['artist_name'] = data['artists'][0].get('name', '')
            elif ' - ' in raw_title:
                parts = raw_title.split(' - ', 1)
                fields['artist_name'] = parts[0].strip()
                fields['name'] = parts[1].strip()
            else:
                fields['name'] = raw_title

            fields['release_date'] = str(data.get('year', '')) if data.get('year') else ''
            tracklist = data.get('tracklist', [])
            fields['total_tracks'] = len(tracklist) if tracklist else 0
            fields['genres'] = json.dumps(data.get('genres', []))

            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                fields['image_url'] = (primary or images[0]).get('uri')
            if not fields.get('image_url'):
                img = data.get('cover_image') or data.get('thumb')
                if img and 'spacer.gif' not in img:
                    fields['image_url'] = img

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

    def get_genre_new_releases(self, user_genres, source=None, sources=None, limit=20):
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
                if sources:
                    placeholders = ','.join(['?'] * len(sources))
                    source_filter = f"AND source IN ({placeholders})"
                    params.extend(sources)
                elif source:
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

    def get_genre_deep_dive(self, genre, source=None, sources=None, artist_limit=12, album_limit=20, track_limit=15):
        """Get artists, albums, and tracks for a genre. Albums don't have genres in Spotify,
        so we find artists with matching genres then fetch their cached albums and tracks."""
        if not genre:
            return {'artists': [], 'albums': [], 'tracks': []}
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()

                # Build source filter for allowed sources
                source_filter = ""
                source_params = []
                if sources:
                    placeholders = ','.join(['?'] * len(sources))
                    source_filter = f"AND source IN ({placeholders})"
                    source_params = list(sources)
                elif source:
                    source_filter = "AND source = ?"
                    source_params = [source]
                params = [f'%{genre}%'] + source_params
                # Fetch extra to allow dedup across sources
                cursor.execute(f"""
                    SELECT name, image_url, popularity, followers, entity_id, source, genres
                    FROM metadata_cache_entities
                    WHERE entity_type = 'artist'
                      AND genres LIKE ?
                      {source_filter}
                    ORDER BY COALESCE(followers, 0) DESC, COALESCE(popularity, 0) DESC
                    LIMIT ?
                """, params + [artist_limit * 3])
                # Deduplicate by name — prefer entry with image, then most followers
                seen_artists = {}
                for r in cursor.fetchall():
                    r = dict(r)
                    key = r['name'].lower().strip()
                    existing = seen_artists.get(key)
                    if not existing:
                        seen_artists[key] = r
                    elif not existing.get('image_url') and r.get('image_url'):
                        seen_artists[key] = r
                    elif (existing.get('followers') or 0) < (r.get('followers') or 0):
                        seen_artists[key] = r
                artists = list(seen_artists.values())[:artist_limit]

                # If not enough artists found (e.g. Deezer), find artists via album genres
                # Two-step: get artist names from albums, then look up artist entities
                if len(artists) < artist_limit:
                    existing_names = {a['name'].lower() for a in artists}
                    album_params = [f'%{genre}%'] + source_params
                    # Step 1b: Get distinct artist names from genre-matching albums
                    cursor.execute(f"""
                        SELECT DISTINCT artist_name FROM metadata_cache_entities
                        WHERE entity_type = 'album' AND genres LIKE ?
                          {source_filter}
                        LIMIT 50
                    """, album_params)
                    album_artist_names = [r['artist_name'] for r in cursor.fetchall()
                                          if r['artist_name'] and r['artist_name'].lower() not in existing_names]

                    # Step 1c: Look up those artists by name (deduplicate across sources)
                    if album_artist_names:
                        name_ph = ','.join(['?'] * len(album_artist_names))
                        art_params = list(album_artist_names) + source_params
                        cursor.execute(f"""
                            SELECT name, image_url, popularity, followers, entity_id, source, genres
                            FROM metadata_cache_entities
                            WHERE entity_type = 'artist'
                              AND name COLLATE NOCASE IN ({name_ph})
                              {source_filter}
                            ORDER BY COALESCE(followers, 0) DESC
                            LIMIT ?
                        """, art_params + [(artist_limit - len(artists)) * 3])
                        for row in cursor.fetchall():
                            r = dict(row)
                            key = r['name'].lower()
                            if key not in existing_names:
                                existing_names.add(key)
                                artists.append(r)
                                if len(artists) >= artist_limit:
                                    break

                albums = []
                tracks = []
                original_names = [a['name'] for a in artists if a.get('name')]

                if original_names:
                    name_placeholders = ','.join(['?'] * len(original_names))

                    # Step 2: Find albums by those artists (COLLATE NOCASE on column leverages index)
                    album_params = list(original_names) + source_params
                    cursor.execute(f"""
                        SELECT name, artist_name, image_url, popularity, release_date, label,
                               source, entity_id, album_type, total_tracks, genres
                        FROM metadata_cache_entities
                        WHERE entity_type = 'album'
                          AND artist_name COLLATE NOCASE IN ({name_placeholders})
                          {source_filter}
                        ORDER BY COALESCE(popularity, 0) DESC, RANDOM()
                        LIMIT ?
                    """, album_params + [album_limit])
                    albums = [dict(r) for r in cursor.fetchall()]

                    # Step 3: Find tracks by those artists
                    track_params = list(original_names) + source_params
                    cursor.execute(f"""
                        SELECT name, artist_name, image_url, popularity, album_name,
                               source, entity_id, duration_ms, album_id
                        FROM metadata_cache_entities
                        WHERE entity_type = 'track'
                          AND artist_name COLLATE NOCASE IN ({name_placeholders})
                          {source_filter}
                        ORDER BY COALESCE(popularity, 0) DESC, RANDOM()
                        LIMIT ?
                    """, track_params + [track_limit])
                    tracks = [dict(r) for r in cursor.fetchall()]

                # Step 4: Find related genres from artist genres + ALL albums by these artists
                related_genres = {}
                genre_lower = genre.lower()

                # From artist genre data (Spotify/iTunes — multiple genres per artist)
                for artist in artists:
                    try:
                        artist_genres = json.loads(artist.get('genres', '[]'))
                        if isinstance(artist_genres, list):
                            for g in artist_genres:
                                g_lower = g.strip().lower()
                                if g_lower and g_lower != genre_lower:
                                    related_genres[g_lower] = related_genres.get(g_lower, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass

                # From ALL albums by these artists (not just the 20 we fetched)
                # This finds cross-genre artists (e.g., artist has Pop AND R&B albums)
                if original_names:
                    cursor.execute(f"""
                        SELECT DISTINCT genres FROM metadata_cache_entities
                        WHERE entity_type = 'album'
                          AND artist_name COLLATE NOCASE IN ({name_placeholders})
                          AND genres IS NOT NULL AND genres != '[]'
                          {source_filter}
                    """, list(original_names) + source_params)
                    for row in cursor.fetchall():
                        try:
                            parsed = json.loads(row['genres'])
                            if isinstance(parsed, list):
                                for g in parsed:
                                    g_lower = g.strip().lower()
                                    if g_lower and g_lower != genre_lower:
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

                # Step 6: Resolve library artist IDs for navigation (batched)
                if artists:
                    lib_name_placeholders = ','.join(['LOWER(?)'] * len(artists))
                    lib_name_params = [a['name'] for a in artists]
                    try:
                        cursor.execute(f"""
                            SELECT id, LOWER(name) as lname FROM artists
                            WHERE LOWER(name) IN ({lib_name_placeholders})
                        """, lib_name_params)
                        lib_id_map = {r['lname']: r['id'] for r in cursor.fetchall()}
                    except Exception:
                        lib_id_map = {}
                    for artist in artists:
                        artist['library_id'] = lib_id_map.get(artist['name'].lower())

                return {'artists': artists, 'albums': albums, 'tracks': tracks, 'related_genres': related}
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Genre deep dive error: {e}")
            return {'artists': [], 'albums': [], 'tracks': []}

    _genre_explorer_cache = {}  # {source: (timestamp, results)}
    _GENRE_EXPLORER_TTL = 86400  # 24 hours

    def get_genre_explorer(self, user_genres_set, source=None, sources=None):
        """Aggregate genres from cached artists and albums, highlight unexplored ones."""
        import time
        cache_key = ','.join(sorted(sources)) if sources else (source or '_all')
        cached = self._genre_explorer_cache.get(cache_key)
        if cached:
            ts, raw_results = cached
            if time.time() - ts < self._GENRE_EXPLORER_TTL:
                user_lower = {g.lower() for g in user_genres_set} if user_genres_set else set()
                for r in raw_results:
                    r['explored'] = r['genre'].lower() in user_lower
                return raw_results
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                params = []
                source_filter = ""
                if sources:
                    placeholders = ','.join(['?'] * len(sources))
                    source_filter = f"AND source IN ({placeholders})"
                    params.extend(sources)
                elif source:
                    source_filter = "AND source = ?"
                    params.append(source)

                # Count unique artists per genre from both artist and album entities
                # Artists have genres directly; albums have genre_id-mapped genres + artist_name
                genre_artists = {}  # {genre_lower: set(artist_names)}

                # From artist entities
                cursor.execute(f"""
                    SELECT name, genres FROM metadata_cache_entities
                    WHERE entity_type = 'artist'
                      AND genres IS NOT NULL AND genres != '' AND genres != '[]'
                      {source_filter}
                """, params)
                for row in cursor.fetchall():
                    try:
                        parsed = json.loads(row['genres'])
                        if isinstance(parsed, list):
                            artist_key = (row['name'] or '').lower()
                            for g in parsed:
                                g_lower = g.strip().lower()
                                if g_lower:
                                    genre_artists.setdefault(g_lower, set()).add(artist_key)
                    except (json.JSONDecodeError, TypeError):
                        pass

                # From album entities (for Deezer where artists lack genres)
                cursor.execute(f"""
                    SELECT artist_name, genres FROM metadata_cache_entities
                    WHERE entity_type = 'album'
                      AND genres IS NOT NULL AND genres != '' AND genres != '[]'
                      {source_filter}
                """, params)
                for row in cursor.fetchall():
                    try:
                        parsed = json.loads(row['genres'])
                        if isinstance(parsed, list):
                            artist_key = (row['artist_name'] or '').lower()
                            if artist_key:
                                for g in parsed:
                                    g_lower = g.strip().lower()
                                    if g_lower:
                                        genre_artists.setdefault(g_lower, set()).add(artist_key)
                    except (json.JSONDecodeError, TypeError):
                        pass

                user_lower = {g.lower() for g in user_genres_set} if user_genres_set else set()
                results = []
                for genre, artists_set in sorted(genre_artists.items(), key=lambda x: len(x[1]), reverse=True)[:50]:
                    if len(artists_set) < 2:
                        continue  # Skip genres with only 1 artist
                    results.append({
                        'genre': genre.title(),
                        'artist_count': len(artists_set),
                        'explored': genre in user_lower,
                    })
                # Cache for subsequent requests
                self._genre_explorer_cache[cache_key] = (time.time(), results)
                return results
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Genre explorer error: {e}")
            return []

    def backfill_deezer_album_genres(self):
        """One-time backfill: extract genres from raw_json for Deezer albums that have genres: '[]'.
        Deezer album API responses include genres in data.genres.data[].name but this wasn't
        extracted in earlier versions. This parses the stored raw_json and updates the genres field."""
        try:
            db = self._get_db()
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, raw_json FROM metadata_cache_entities
                    WHERE source = 'deezer' AND entity_type = 'album'
                      AND (genres IS NULL OR genres = '' OR genres = '[]')
                      AND raw_json IS NOT NULL
                    LIMIT 50000
                """)
                updated = 0
                for row in cursor.fetchall():
                    try:
                        raw = json.loads(row['raw_json'])
                        genre_names = []

                        # Try full genres object (from get_album responses)
                        dz_genres = raw.get('genres', {})
                        if isinstance(dz_genres, dict):
                            dz_genres = dz_genres.get('data', [])
                        if isinstance(dz_genres, list) and dz_genres:
                            genre_names = [g.get('name', '') for g in dz_genres if isinstance(g, dict) and g.get('name')]

                        # Fallback: genre_id from search responses
                        if not genre_names:
                            genre_id = raw.get('genre_id')
                            if genre_id and genre_id in self._DEEZER_GENRE_MAP:
                                genre_names = [self._DEEZER_GENRE_MAP[genre_id]]

                        if genre_names:
                            cursor.execute(
                                "UPDATE metadata_cache_entities SET genres = ? WHERE id = ?",
                                (json.dumps(genre_names), row['id'])
                            )
                            updated += 1
                    except Exception:
                        continue
                conn.commit()
                logger.info(f"Deezer album genre backfill: updated {updated} albums")

                # Phase 2: Propagate album genres to Deezer artist entities
                # Match by artist_name or artist_id from albums that have genres
                artist_updated = 0
                cursor.execute("""
                    SELECT DISTINCT artist_name, artist_id, genres
                    FROM metadata_cache_entities
                    WHERE source = 'deezer' AND entity_type = 'album'
                      AND genres IS NOT NULL AND genres != '' AND genres != '[]'
                      AND (artist_name != '' OR artist_id != '')
                """)
                album_artists = {}  # {artist_identifier: set(genres)}
                for row in cursor.fetchall():
                    try:
                        names = json.loads(row['genres'])
                        if not isinstance(names, list):
                            continue
                        # Key by artist_name or artist_id
                        key_name = row['artist_name'] or ''
                        key_id = row['artist_id'] or ''
                        for key in [k for k in [key_name, key_id] if k]:
                            album_artists.setdefault(key, set()).update(names)
                    except Exception:
                        continue

                # Update artist entities that have empty genres
                cursor.execute("""
                    SELECT id, name, entity_id FROM metadata_cache_entities
                    WHERE source = 'deezer' AND entity_type = 'artist'
                      AND (genres IS NULL OR genres = '' OR genres = '[]')
                """)
                for row in cursor.fetchall():
                    genres = album_artists.get(row['name']) or album_artists.get(row['entity_id']) or set()
                    if genres:
                        cursor.execute(
                            "UPDATE metadata_cache_entities SET genres = ? WHERE id = ?",
                            (json.dumps(list(genres)), row['id'])
                        )
                        artist_updated += 1
                conn.commit()
                logger.info(f"Deezer artist genre backfill: updated {artist_updated} artists from album genres")

                return updated + artist_updated
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Deezer genre backfill error: {e}")
            return 0
