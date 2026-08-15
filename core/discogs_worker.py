"""
Discogs background enrichment worker.

Enriches library artists and albums with Discogs metadata:
- Artists: discogs_id, bio, members, genres, styles, URLs, images
- Albums: discogs_id, genres, styles, label, catalog number, country, community rating

Follows the exact same pattern as AudioDBWorker.
"""

import json
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from utils.logging_config import get_logger
from database.music_database import MusicDatabase
from core.discogs_client import DiscogsClient, _discogs_album_kind, _tag_discogs_album_id
from core.worker_utils import interruptible_sleep

logger = get_logger("discogs_worker")

# Discogs exposes artists and releases, not recordings — there is no track
# endpoint to call.
_ENTITY_TYPES = ('artist', 'album')


def count_discogs_real_tracks(tracklist) -> int:
    """Count actual songs in a Discogs tracklist response.

    Discogs tracklists interleave real tracks with section headings
    (``type_=='heading'``), index markers (``type_=='index'``),
    and sub-tracks (``type_=='sub_track'``) that aren't themselves
    songs. We count anything that's explicitly typed as ``'track'`` OR
    has an empty/missing ``type_`` field — matching exactly what
    :meth:`core.discogs_client.DiscogsClient.get_album_tracks` itself
    treats as a real track (`type_ in ('track', '')`). Counting any
    narrower set silently disagrees with the repair job's fallback
    `_get_expected_total` path, which calls `get_album_tracks_for_source`
    under the hood and therefore uses the client's count.

    Reported by kettui on PR #374 — original filter only counted
    ``type_=='track'`` and undercounted releases where the discogs
    response left ``type_`` empty for some real tracks.
    """
    if not tracklist:
        return 0
    return sum(1 for t in tracklist if (t.get('type_') or '') in ('track', ''))


class DiscogsWorker:
    """Background worker for enriching library artists and albums with Discogs metadata."""

    def __init__(self, database: MusicDatabase):
        self.db = database
        self.client = DiscogsClient()

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self._stop_event = threading.Event()

        # Current item being processed (for UI tooltip)
        self.current_item = None

        # Statistics
        self.stats = {
            'matched': 0,
            'not_found': 0,
            'pending': 0,
            'errors': 0
        }

        # Retry configuration
        self.retry_days = 30

        # Name matching threshold
        self.name_similarity_threshold = 0.80

        logger.info(f"Discogs background worker initialized (authenticated: {self.client.is_authenticated()})")

    def start(self):
        """Start the background worker."""
        if self.running:
            logger.warning("Discogs worker already running")
            return

        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Discogs background worker started")

    def stop(self):
        """Stop the background worker."""
        if not self.running:
            return
        logger.info("Stopping Discogs worker...")
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        logger.info("Discogs worker stopped")

    def pause(self):
        """Pause the worker."""
        if not self.running:
            return
        self.paused = True
        logger.info("Discogs worker paused")

    def resume(self):
        """Resume the worker."""
        if not self.running:
            return
        self.paused = False
        logger.info("Discogs worker resumed")

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        self.stats['pending'] = self._count_pending_items()
        is_actually_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_actually_running and not self.paused and self.stats['pending'] == 0 and self.current_item is None

        return {
            'enabled': True,
            'running': is_actually_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
        }

    def _run(self):
        """Main worker loop."""
        logger.info("Discogs worker thread started")

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                item = self._get_next_item()

                if not item:
                    interruptible_sleep(self._stop_event, 10)
                    continue

                self.current_item = item.get('name', '')

                # Guard: skip items with None/NULL IDs
                item_id = item.get('id')
                if item_id is None:
                    logger.warning(f"Skipping {item.get('type', 'unknown')} with NULL id")
                    continue

                self._process_item(item)
                interruptible_sleep(self._stop_event, 2)

            except Exception as e:
                logger.error(f"Error in Discogs worker loop: {e}")
                interruptible_sleep(self._stop_event, 5)

        logger.info("Discogs worker thread finished")

    def _get_next_item(self) -> Optional[Dict[str, Any]]:
        """Get next item to process from the Library-v2 catalogue.

        Priority, retry window and the pinned-group override all live in
        ``core.library2.worker_queue`` — the same rules every enrichment worker
        uses (docs §32.3.1 stage 2). Discogs has no track endpoint, so tracks are
        never offered: attempting them would mark every one ``not_found`` and
        count that as progress.
        """
        conn = None
        try:
            from core.library2.worker_queue import next_pending
            from core.worker_utils import read_enrichment_priority

            conn = self.db._get_connection()
            _prio = read_enrichment_priority('discogs')
            return next_pending(
                conn, 'discogs',
                retry_after_days=self.retry_days,
                pinned=_prio if _prio in _ENTITY_TYPES else None,
                entity_types=_ENTITY_TYPES,
            )
        except Exception as e:
            logger.error(f"Error getting next Discogs item: {e}")
            return None
        finally:
            if conn:
                conn.close()

    def _count_pending_items(self) -> int:
        """Count items still needing Discogs enrichment."""
        conn = None
        try:
            from core.library2.worker_queue import pending_count

            conn = self.db._get_connection()
            return pending_count(conn, 'discogs', retry_after_days=self.retry_days,
                                 entity_types=_ENTITY_TYPES)
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()

    def _normalize_name(self, name: str) -> str:
        """Normalize name for comparison."""
        name = name.lower().strip()
        name = re.sub(r'\s+[-–—]\s+.*$', '', name)
        name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
        name = re.sub(r'[^\w\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _name_matches(self, query_name: str, result_name: str) -> bool:
        """Check if Discogs result name matches our query with fuzzy matching."""
        norm_query = self._normalize_name(query_name)
        norm_result = self._normalize_name(result_name)
        if not norm_query or not norm_result:
            raw_query = (query_name or '').strip().lower()
            raw_result = (result_name or '').strip().lower()
            return bool(raw_query) and raw_query == raw_result
        similarity = SequenceMatcher(None, norm_query, norm_result).ratio()
        return similarity >= self.name_similarity_threshold

    def _process_item(self, item: Dict[str, Any]):
        """Process a single artist or album."""
        try:
            item_type = item['type']
            item_id = item['id']
            item_name = item['name']

            logger.debug(f"Processing {item_type} #{item_id}: {item_name}")

            # Check for existing discogs_id (manual match) — use direct lookup
            existing_id = self._get_existing_id(item_type, item_id)
            if existing_id:
                try:
                    if item_type == 'artist':
                        data = self.client._fetch_and_cache_artist(existing_id)
                        if data:
                            self._update_artist(item_id, data)
                            self.stats['matched'] += 1
                            logger.info(f"Enriched artist '{item_name}' from existing Discogs ID: {existing_id}")
                            return
                    elif item_type == 'album':
                        data = self.client._fetch_and_cache_album(existing_id)
                        if data:
                            self._update_album(item_id, data)
                            self.stats['matched'] += 1
                            logger.info(f"Enriched album '{item_name}' from existing Discogs ID: {existing_id}")
                            return
                except Exception as e:
                    logger.warning(f"Direct Discogs lookup failed for ID {existing_id}: {e}")
                return  # Preserve manual match, don't search

            if item_type == 'artist':
                self._search_and_match_artist(item_id, item_name)
            elif item_type == 'album':
                self._search_and_match_album(item_id, item_name, item.get('artist', ''))

        except Exception as e:
            logger.error(f"Error processing {item.get('type')} #{item.get('id')}: {e}")
            self.stats['errors'] += 1
            try:
                self._mark_status(item['type'], item['id'], 'error')
            except Exception as e:
                logger.debug("mark item status error failed: %s", e)

    def _get_existing_id(self, entity_type: str, entity_id) -> Optional[str]:
        """The Discogs id already stored for this entity, if any.

        Set by a manual match or an earlier run; honoring it is what keeps a
        manual match from being searched over (issue #501).
        """
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id

            conn = self.db._get_connection()
            return stored_provider_id(conn, entity_type, entity_id, 'discogs')
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _search_and_match_artist(self, artist_id, artist_name: str):
        """Search Discogs for an artist and store metadata if matched."""
        results = self.client.search_artists(artist_name, limit=5)
        if not results:
            self._mark_status('artist', artist_id, 'not_found')
            self.stats['not_found'] += 1
            return

        # Find best match by name similarity (skipping ids already claimed by
        # a differently-named artist, so we don't create a shared/duplicate id).
        from core.library2.worker_support import accept_artist_match

        conn = self.db._get_connection()
        try:
            gate = [
                (result, *accept_artist_match(
                    conn, 'discogs', result.id, artist_id, artist_name, result.name))
                for result in results
            ]
        finally:
            conn.close()
        for result, ok, _reason in gate:
            if ok:
                # Fetch full artist detail (uses cache)
                data = self.client._fetch_and_cache_artist(result.id)
                if data:
                    self._update_artist(artist_id, data)
                    self.stats['matched'] += 1
                    logger.info(f"Matched artist '{artist_name}' -> Discogs ID: {result.id}")
                    return

        self._mark_status('artist', artist_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"No confident match for artist '{artist_name}'")

    def _search_and_match_album(self, album_id, album_name: str, artist_name: str):
        """Search Discogs for an album and store metadata if matched."""
        # Search with artist + album for better precision
        query = f"{artist_name} {album_name}" if artist_name else album_name
        results = self.client.search_albums(query, limit=5)
        if not results:
            self._mark_status('album', album_id, 'not_found')
            self.stats['not_found'] += 1
            return

        for result in results:
            if self._name_matches(album_name, result.name):
                # Fetch full release detail (uses cache)
                data = self.client._fetch_and_cache_album(result.id)
                if data:
                    self._update_album(album_id, data)
                    self.stats['matched'] += 1
                    logger.info(f"Matched album '{album_name}' -> Discogs ID: {result.id}")
                    return

        self._mark_status('album', album_id, 'not_found')
        self.stats['not_found'] += 1
        logger.debug(f"No confident match for album '{album_name}'")

    def _update_artist(self, artist_id, data: Dict[str, Any]):
        """Store Discogs metadata for an artist."""
        conn = None
        try:
            conn = self.db._get_connection()

            discogs_id = str(data.get('id', ''))
            bio = data.get('profile', '')
            members = [m.get('name', '') for m in data.get('members', [])] or None
            urls = data.get('urls') or None

            # Get image
            image_url = None
            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                image_url = (primary or images[0]).get('uri')

            # Library v2 is the catalogue (docs §32.3.1 stage 2). The payload keys
            # are the mirror's own declaration for Discogs, so a natively written
            # row is indistinguishable from a mirrored one.
            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment

            backfill = {}
            if bio:
                backfill['summary'] = bio
            if image_url:
                backfill['image_url'] = image_url
            write_provider_enrichment(
                conn, entity_type='artist', entity_id=artist_id, service='discogs',
                payload={'bio': bio or None, 'members': members, 'urls': urls},
                provider_id=discogs_id,
                backfill=backfill or None,
            )
            record_attempt(conn, entity_type='artist', entity_id=artist_id,
                           service='discogs', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(f"Error updating artist #{artist_id} with Discogs data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _update_album(self, album_id, data: Dict[str, Any]):
        """Store Discogs metadata for an album."""
        conn = None
        try:
            conn = self.db._get_connection()

            # Tag the ID with its Discogs type so later re-fetches hit the right
            # endpoint (master vs release share one numeric space).
            discogs_id = _tag_discogs_album_id(data.get('id', ''), _discogs_album_kind(data))
            genres = data.get('genres') or None
            styles = data.get('styles') or None
            labels = data.get('labels', [])
            label = labels[0].get('name', '') if labels else ''
            catno = labels[0].get('catno', '') if labels else ''
            country = data.get('country', '')

            # Community rating
            community = data.get('community', {})
            rating = community.get('rating', {})
            rating_avg = rating.get('average', 0)
            rating_count = rating.get('count', 0)

            # Image
            image_url = None
            images = data.get('images', [])
            if images:
                primary = next((img for img in images if img.get('type') == 'primary'), None)
                image_url = (primary or images[0]).get('uri')

            from core.library2.provider_attempts import record_attempt
            from core.library2.provider_writes import write_provider_enrichment
            from core.library2.worker_support import set_expected_track_count

            backfill = {}
            if image_url:
                backfill['image_url'] = image_url
            if genres:
                from core.genre_filter import filter_genres
                from core.settings import config_manager as _cfg
                _filtered = filter_genres(list(genres), _cfg)
                if _filtered:
                    backfill['genres'] = json.dumps(_filtered)
            write_provider_enrichment(
                conn, entity_type='album', entity_id=album_id, service='discogs',
                payload={
                    'genres': genres, 'styles': styles,
                    'label': label or None, 'catno': catno or None,
                    'country': country or None,
                    'rating': rating_avg or None,
                    'rating_count': rating_count or None,
                },
                provider_id=discogs_id,
                backfill=backfill or None,
            )

            # Cache the authoritative expected track count for the Album
            # Completeness repair job. See `count_discogs_real_tracks`
            # for why we accept both `type_ == 'track'` and empty `type_`
            # (kettui's PR #374 review — narrower filter undercounted).
            set_expected_track_count(
                conn, album_id, count_discogs_real_tracks(data.get('tracklist')))

            record_attempt(conn, entity_type='album', entity_id=album_id,
                           service='discogs', status='matched')
            conn.commit()

        except Exception as e:
            logger.error(f"Error updating album #{album_id} with Discogs data: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _mark_status(self, entity_type: str, entity_id, status: str):
        """Record the outcome of an attempt in the provider ledger.

        Replaces the legacy `discogs_match_status`/`_last_attempted` column pair.
        Both `not_found` and `error` become due again after the retry
        window; a source-wide outage is handled by the worker's own backoff
        before an attempt is ever recorded, so it cannot become a tight loop.
        """
        conn = None
        try:
            from core.library2.provider_attempts import record_attempt

            conn = self.db._get_connection()
            record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                           service='discogs', status=status)
            conn.commit()
        except Exception as e:
            logger.error(f"Error marking {entity_type} #{entity_id} status: {e}")
        finally:
            if conn:
                conn.close()
