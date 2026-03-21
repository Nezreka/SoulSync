"""
SoulID Worker — generates deterministic soul IDs for artists, albums, and tracks.

Runs as a background worker that processes library entries without soul IDs,
computes a deterministic hash from normalized metadata, and stores the result.

Hash inputs (all lowercased, stripped of accents/punctuation, collapsed):
  - Artist:      normalize(artist_name) + normalize(debut_year) if known
                 Debut year is sourced from iTunes + Deezer APIs (not local DB)
                 to ensure deterministic results across all SoulSync nodes.
  - Album:       normalize(artist_name) + normalize(album_name)
  - Track (song): normalize(artist_name) + normalize(track_name)
  - Track (album): normalize(artist_name) + normalize(album_name) + normalize(track_name)

The "song" soul ID links different versions of the same song (single vs album).
The "album track" soul ID is specific to a track on a particular release.
"""

import hashlib
import re
import threading
import time
import unicodedata
from typing import Dict, Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("soulid_worker")


def normalize_for_soul_id(text: str) -> str:
    """Aggressively normalize a string for deterministic hashing.

    - Lowercase
    - Strip accents/diacritics (Beyoncé → beyonce)
    - Remove parentheticals: (feat. X), (Deluxe), (Remastered), [Live], etc.
    - Remove all non-alphanumeric characters
    - Collapse whitespace
    """
    if not text:
        return ''
    s = text.lower()
    # Decompose unicode and strip combining marks (accents)
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    # Remove parenthetical/bracket suffixes
    s = re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', s)
    # Remove all non-alphanumeric
    s = re.sub(r'[^a-z0-9]', '', s)
    return s


def generate_soul_id(*parts: str) -> str:
    """Generate a soul ID from normalized parts.

    Returns a 'soul_' prefixed hex string (first 16 chars of SHA-256).
    """
    combined = ''.join(normalize_for_soul_id(p) for p in parts if p)
    if not combined:
        return ''
    digest = hashlib.sha256(combined.encode('utf-8')).hexdigest()[:16]
    return f'soul_{digest}'


class SoulIDWorker:
    """Background worker that generates soul IDs for all library entities.

    Artists are processed one at a time (API calls to iTunes/Deezer for
    deterministic debut year). Albums and tracks are processed in batches
    (local DB only, no API calls).
    """

    def __init__(self, database):
        self.db = database

        # Worker state
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread = None
        self.current_item = None

        # API clients (lazy-initialized)
        self._itunes_client = None
        self._deezer_client = None
        self._matching_engine = None

        # Statistics
        self.stats = {
            'artists_processed': 0,
            'albums_processed': 0,
            'tracks_processed': 0,
            'errors': 0,
            'pending': 0,
        }

        # Processing config
        self.batch_size = 100         # For albums/tracks (no API calls)
        self.artist_sleep = 3.0       # Between artist API lookups (rate limit courtesy)
        self.inter_batch_sleep = 0.5  # Between album/track batches
        self.idle_sleep = 30
        self.album_match_threshold = 0.80

        logger.info("SoulID worker initialized")

    def _get_itunes_client(self):
        if self._itunes_client is None:
            try:
                from core.itunes_client import iTunesClient
                self._itunes_client = iTunesClient()
            except Exception as e:
                logger.error(f"Failed to init iTunes client: {e}")
        return self._itunes_client

    def _get_deezer_client(self):
        if self._deezer_client is None:
            try:
                from core.deezer_client import DeezerClient
                self._deezer_client = DeezerClient()
            except Exception as e:
                logger.error(f"Failed to init Deezer client: {e}")
        return self._deezer_client

    def _get_matching_engine(self):
        if self._matching_engine is None:
            try:
                from core.matching_engine import MusicMatchingEngine
                self._matching_engine = MusicMatchingEngine()
            except Exception as e:
                logger.error(f"Failed to init matching engine: {e}")
        return self._matching_engine

    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("SoulID worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("SoulID worker stopped")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def get_stats(self) -> Dict[str, Any]:
        self.stats['pending'] = self._count_pending()
        is_running = self.running and self.thread is not None and self.thread.is_alive()
        is_idle = is_running and not self.paused and self.stats['pending'] == 0
        return {
            'enabled': True,
            'running': is_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
        }

    # ── Main loop ──

    def _run(self):
        logger.info("SoulID worker thread started")

        # One-time migration: reset artist soul IDs when algorithm changes
        self._migrate_artist_soul_ids()

        while not self.should_stop:
            try:
                if self.paused:
                    time.sleep(1)
                    continue

                processed = 0
                processed += self._process_next_artist()
                processed += self._process_albums()
                processed += self._process_tracks()

                if processed == 0:
                    self.current_item = None
                    time.sleep(self.idle_sleep)
                else:
                    # Albums/tracks get inter_batch_sleep, artists get their
                    # own sleep inside _process_next_artist
                    time.sleep(self.inter_batch_sleep)

            except Exception as e:
                logger.error(f"Error in SoulID worker loop: {e}", exc_info=True)
                self.stats['errors'] += 1
                time.sleep(5)

        self.current_item = None
        logger.info("SoulID worker thread finished")

    # ── Artist processing (one at a time, API-based) ──

    def _process_next_artist(self) -> int:
        """Process a single artist — queries iTunes + Deezer for debut year."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name FROM artists
                WHERE (soul_id IS NULL OR soul_id = '')
                  AND name IS NOT NULL AND name != ''
                  AND id IS NOT NULL
                LIMIT 1
            """)
            row = cursor.fetchone()
            if not row:
                return 0

            artist_id, name = row
            self.current_item = f"Artist: {name}"

            # Get this artist's album names from our DB for cross-referencing
            cursor.execute("""
                SELECT title FROM albums
                WHERE artist_id = ? AND title IS NOT NULL AND title != ''
            """, (artist_id,))
            db_album_names = [r[0] for r in cursor.fetchall()]

            # Look up debut year from iTunes + Deezer APIs
            debut_year = self._lookup_debut_year(name, db_album_names)

            if debut_year:
                soul_id = generate_soul_id(name, debut_year)
                self.current_item = f"Artist: {name} ({debut_year})"
            else:
                soul_id = generate_soul_id(name)

            if not soul_id:
                soul_id = f'soul_unnamed_{artist_id}'

            cursor.execute(
                "UPDATE artists SET soul_id = ? WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                (soul_id, artist_id)
            )
            conn.commit()
            self.stats['artists_processed'] += 1
            logger.info(f"Generated soul ID for artist: {name}" + (f" (debut {debut_year})" if debut_year else ""))

            # Rate limit courtesy for API calls
            time.sleep(self.artist_sleep)
            return 1

        except Exception as e:
            logger.error(f"Error processing artist: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return 0
        finally:
            if conn:
                conn.close()

    def _lookup_debut_year(self, artist_name: str, db_album_names: List[str]) -> Optional[str]:
        """Look up an artist's debut year from iTunes and Deezer.

        Searches both sources for the artist, verifies the match by comparing
        their discography against our DB albums, then pools all album years
        from both matched sources and returns the earliest.

        Args:
            artist_name: Artist name to search for
            db_album_names: Album names from our DB for this artist

        Returns:
            Earliest release year as string (e.g. '2011'), or None
        """
        if not db_album_names:
            # No albums to cross-reference — can't verify which artist is correct
            return None

        matching = self._get_matching_engine()
        if not matching:
            return None

        # Search both sources
        itunes = self._get_itunes_client()
        deezer = self._get_deezer_client()

        itunes_results = []
        deezer_results = []

        try:
            if itunes:
                itunes_results = itunes.search_artists(artist_name, limit=5) or []
        except Exception as e:
            logger.debug(f"iTunes artist search failed for '{artist_name}': {e}")

        try:
            if deezer:
                deezer_results = deezer.search_artists(artist_name, limit=5) or []
        except Exception as e:
            logger.debug(f"Deezer artist search failed for '{artist_name}': {e}")

        # Each source independently steps through its results to find a match
        itunes_discog = self._find_matching_discography(itunes, itunes_results, db_album_names, matching, 'iTunes')
        deezer_discog = self._find_matching_discography(deezer, deezer_results, db_album_names, matching, 'Deezer')

        # Pool all albums from both matched sources
        all_years = []
        for discog in (itunes_discog, deezer_discog):
            if discog:
                for album in discog:
                    year = self._extract_year(album)
                    if year:
                        all_years.append(year)

        if all_years:
            return min(all_years)

        return None

    def _find_matching_discography(self, client, artist_results, db_album_names: List[str],
                                    matching, source_name: str) -> Optional[list]:
        """Step through artist search results, return the discography of the first
        one whose albums overlap with our DB albums.

        Args:
            client: iTunes or Deezer client
            artist_results: List of Artist dataclass objects from search
            db_album_names: Our DB album names for comparison
            matching: MatchingEngine instance
            source_name: 'iTunes' or 'Deezer' for logging

        Returns:
            List of Album objects from the matched artist's discography, or None
        """
        if not client or not artist_results:
            return None

        for artist in artist_results:
            try:
                discog = client.get_artist_albums(artist.id, album_type='album,single', limit=50)
                if not discog:
                    continue

                # Check if any discography album matches any DB album
                for api_album in discog:
                    api_name = api_album.name if hasattr(api_album, 'name') else str(api_album)
                    for db_name in db_album_names:
                        score = matching.similarity_score(
                            matching.normalize_string(api_name),
                            matching.normalize_string(db_name)
                        )
                        if score >= self.album_match_threshold:
                            logger.debug(f"  {source_name}: matched '{artist.name}' via album '{api_name}' ↔ '{db_name}' (score={score:.2f})")
                            return discog

            except Exception as e:
                logger.debug(f"  {source_name}: discography fetch failed for '{artist.name}': {e}")
                continue

        return None

    @staticmethod
    def _extract_year(album) -> Optional[str]:
        """Extract a 4-digit year from an Album object's release_date."""
        release_date = getattr(album, 'release_date', '') or ''
        release_date = str(release_date)
        if len(release_date) >= 4:
            year = release_date[:4]
            if year.isdigit() and int(year) > 1900:
                return year
        return None

    # ── Album processing (batch, local DB only) ──

    def _process_albums(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT al.id, al.title, ar.name as artist_name
                FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.soul_id IS NULL OR al.soul_id = '')
                  AND al.title IS NOT NULL AND al.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND al.id IS NOT NULL
                LIMIT ?
            """, (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for album_id, title, artist_name in rows:
                if self.should_stop:
                    break
                soul_id = generate_soul_id(artist_name, title)
                if not soul_id:
                    soul_id = f'soul_unnamed_{album_id}'
                cursor.execute(
                    "UPDATE albums SET soul_id = ? WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                    (soul_id, album_id)
                )
                count += 1
                self.current_item = f"Album: {artist_name} - {title}"

            if count > 0:
                conn.commit()
                self.stats['albums_processed'] += count
                logger.info(f"Generated soul IDs for {count} albums")
            return count
        except Exception as e:
            logger.error(f"Error processing albums: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return 0
        finally:
            if conn:
                conn.close()

    # ── Track processing (batch, local DB only) ──

    def _process_tracks(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name as artist_name, al.title as album_title
                FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE (t.soul_id IS NULL OR t.soul_id = '')
                  AND t.title IS NOT NULL AND t.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND t.id IS NOT NULL
                LIMIT ?
            """, (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for track_id, title, artist_name, album_title in rows:
                if self.should_stop:
                    break

                # Song soul ID: artist + track (links singles to album versions)
                song_soul_id = generate_soul_id(artist_name, title)

                # Album track soul ID: artist + album + track (specific to this release)
                album_soul_id = ''
                if album_title:
                    album_soul_id = generate_soul_id(artist_name, album_title, title)

                if not song_soul_id:
                    song_soul_id = f'soul_unnamed_{track_id}'
                cursor.execute(
                    "UPDATE tracks SET soul_id = ?, album_soul_id = ? WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                    (song_soul_id, album_soul_id or None, track_id)
                )
                count += 1
                self.current_item = f"Track: {artist_name} - {title}"

            if count > 0:
                conn.commit()
                self.stats['tracks_processed'] += count
                logger.info(f"Generated soul IDs for {count} tracks")
            return count
        except Exception as e:
            logger.error(f"Error processing tracks: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return 0
        finally:
            if conn:
                conn.close()

    # ── Migrations ──

    def _migrate_artist_soul_ids(self):
        """One-time reset: clear all artist soul IDs when algorithm changes.
        Uses a versioned metadata flag to run only once per algorithm version."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT value FROM metadata WHERE key = 'soulid_artist_version' LIMIT 1")
            row = cursor.fetchone()
            if row and row[0] == 'debut_year_api_v2':
                return  # Already on latest version

            # Reset all artist soul IDs for regeneration
            cursor.execute("UPDATE artists SET soul_id = NULL WHERE soul_id IS NOT NULL")
            reset_count = cursor.rowcount

            # Mark current version
            cursor.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('soulid_artist_version', 'debut_year_api_v2')")
            # Clean up old flags
            cursor.execute("DELETE FROM metadata WHERE key IN ('soulid_artist_v2', 'soulid_artist_v3')")
            conn.commit()

            if reset_count > 0:
                logger.info(f"SoulID migration: reset {reset_count} artist soul IDs for API-based debut year regeneration")
        except Exception as e:
            logger.error(f"SoulID migration failed: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn:
                conn.close()

    # ── Helpers ──

    def _count_pending(self) -> int:
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            total = 0

            # Must match the WHERE clauses in _process_* methods exactly
            cursor.execute("""
                SELECT COUNT(*) FROM artists
                WHERE (soul_id IS NULL OR soul_id = '')
                  AND name IS NOT NULL AND name != ''
                  AND id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            cursor.execute("""
                SELECT COUNT(*) FROM albums al
                JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.soul_id IS NULL OR al.soul_id = '')
                  AND al.title IS NOT NULL AND al.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND al.id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            cursor.execute("""
                SELECT COUNT(*) FROM tracks t
                JOIN artists ar ON ar.id = t.artist_id
                WHERE (t.soul_id IS NULL OR t.soul_id = '')
                  AND t.title IS NOT NULL AND t.title != ''
                  AND ar.name IS NOT NULL AND ar.name != ''
                  AND t.id IS NOT NULL
            """)
            total += (cursor.fetchone() or [0])[0]

            return total
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
