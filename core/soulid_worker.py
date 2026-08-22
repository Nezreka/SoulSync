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
from core.worker_utils import interruptible_sleep

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


# ── The Library-v2 pending sets (docs §50.4.4.12) ────────────────────────────
#
# Two rules hold all of these together, and both are about keeping the hash
# inputs identical to what every other SoulSync node feeds them.
#
# **A track hashes its ALBUM's artist.** Legacy joined ``tracks.artist_id``,
# which the media-server scan fills with the album artist; the featured credit
# lives in the separate ``track_artist`` column and never reached the hash. lib2
# keeps that credit in ``lib2_track_artists``, so preferring it here — as the
# file-subject query rightly does elsewhere, where the question is "who made
# this recording" — would hand ``Drake feat. Wizkid`` to the hash and re-key the
# track away from every other node.
#
# **Only physically owned releases are in the id space.** Catalogue provenance
# is not ownership; an active file with a real path is the evidence.
_PENDING = "(%(alias)ssoul_id IS NULL OR %(alias)ssoul_id = '')"

PENDING_ALBUMS_SQL = f"""
    SELECT al.id, al.title, ar.name AS artist_name
    FROM lib2_albums al
    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
    WHERE {_PENDING % {'alias': 'al.'}}
      AND al.title IS NOT NULL AND al.title != ''
      AND ar.name IS NOT NULL AND ar.name != ''
      AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f
                  ON f.track_id=t.id WHERE t.album_id=al.id
                  AND f.file_state='active' AND TRIM(f.path)<>'')
"""

PENDING_TRACKS_SQL = f"""
    SELECT t.id, t.title, ar.name AS artist_name, al.title AS album_title
    FROM lib2_tracks t
    JOIN lib2_albums al ON al.id = t.album_id
    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
    WHERE {_PENDING % {'alias': 't.'}}
      AND t.title IS NOT NULL AND t.title != ''
      AND ar.name IS NOT NULL AND ar.name != ''
      AND EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id
                  AND f.file_state='active' AND TRIM(f.path)<>'')
"""

PENDING_ARTISTS_SQL = f"""
    SELECT id, name
    FROM lib2_artists
    WHERE {_PENDING % {'alias': ''}}
      AND name IS NOT NULL AND name != ''
      AND EXISTS (
          SELECT 1 FROM lib2_tracks t
          JOIN lib2_track_files f ON f.track_id=t.id
          JOIN lib2_albums al ON al.id=t.album_id
          LEFT JOIN lib2_track_artists ta ON ta.track_id=t.id AND ta.artist_id=lib2_artists.id
          WHERE f.file_state='active' AND TRIM(f.path)<>''
            AND (al.primary_artist_id=lib2_artists.id OR ta.artist_id IS NOT NULL)
      )
"""

# The two disambiguators the artist hash reaches for, in legacy's order: the
# alphabetically first owned track title verifies the provider lookup, and the
# alphabetically first owned album title is the fallback when no provider
# recognizes the artist.
OWNED_TRACK_TITLE_SQL = """
    SELECT t.title
    FROM lib2_tracks t
    JOIN lib2_albums al ON al.id = t.album_id
    WHERE al.primary_artist_id = ?
      AND EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id
                  AND f.file_state='active' AND TRIM(f.path)<>'')
      AND t.title IS NOT NULL AND t.title != ''
    ORDER BY t.title ASC
    LIMIT 1
"""

OWNED_ALBUM_TITLE_SQL = """
    SELECT al.title
    FROM lib2_albums al
    WHERE al.primary_artist_id = ?
      AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f
                  ON f.track_id=t.id WHERE t.album_id=al.id
                  AND f.file_state='active' AND TRIM(f.path)<>'')
      AND title IS NOT NULL AND title != ''
    ORDER BY title ASC
    LIMIT 1
"""


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
        self._stop_event = threading.Event()

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
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("SoulID worker started")

    def stop(self):
        if not self.running:
            return
        self.should_stop = True
        self.running = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
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
        self._migrate_artist_soul_id_paths()

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                processed = 0
                processed += self._process_next_artist()
                processed += self._process_albums()
                processed += self._process_tracks()

                if processed == 0:
                    self.current_item = None
                    interruptible_sleep(self._stop_event, self.idle_sleep)
                else:
                    # Albums/tracks get inter_batch_sleep, artists get their
                    # own sleep inside _process_next_artist
                    interruptible_sleep(self._stop_event, self.inter_batch_sleep)

            except Exception as e:
                logger.error(f"Error in SoulID worker loop: {e}", exc_info=True)
                self.stats['errors'] += 1
                interruptible_sleep(self._stop_event, 5)

        self.current_item = None
        logger.info("SoulID worker thread finished")

    # ── Artist processing (one at a time, API-based) ──

    def _process_next_artist(self) -> int:
        """Process a single artist — uses track-verified API lookup for canonical ID."""
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(PENDING_ARTISTS_SQL + " LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return 0

            artist_id, name = row[0], row[1]
            self.current_item = f"Artist: {name}"

            # Get a track title from this artist for verification lookup
            cursor.execute(OWNED_TRACK_TITLE_SQL, (artist_id,))
            track_row = cursor.fetchone()
            verify_track = track_row[0] if track_row else None

            # Look up canonical artist ID from Deezer + iTunes using track verification
            canonical_id = self._lookup_canonical_artist_id(name, verify_track)

            # Which of the three derivations produced this id. Only 'canonical'
            # is reproducible on another install; the album fallback depends on
            # what THIS library happens to own, so two nodes holding the same
            # artist compute different ids. Recorded so a consumer can tell how
            # much to trust the key instead of guessing from the outside.
            soul_id_path = None
            if canonical_id:
                soul_id = generate_soul_id(name, str(canonical_id))
                soul_id_path = 'canonical'
                self.current_item = f"Artist: {name} (id:{canonical_id})"
            else:
                # Fallback: use name + first album title alphabetically
                cursor.execute(OWNED_ALBUM_TITLE_SQL, (artist_id,))
                album_row = cursor.fetchone()
                if album_row:
                    soul_id = generate_soul_id(name, album_row[0])
                    soul_id_path = 'album'
                    self.current_item = f"Artist: {name} (album fallback)"
                else:
                    soul_id = generate_soul_id(name)
                    soul_id_path = 'name'

            if not soul_id:
                soul_id = f'soul_unnamed_{artist_id}'
                soul_id_path = None

            cursor.execute(
                # updated_at moves with every soul_id write (L2-011): a row
                # with no soul_id is filtered OUT of the MetaSync export
                # entirely, so minting one is the moment that row starts
                # existing for consumers. Without the touch, a full walk that
                # ran before this worker meant the row never appeared in any
                # later incremental either.
                "UPDATE lib2_artists SET soul_id = ?, soul_id_path = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
                (soul_id, soul_id_path, artist_id)
            )
            conn.commit()
            self.stats['artists_processed'] += 1
            logger.info(f"Generated soul ID for artist: {name}" + (f" (canonical id: {canonical_id})" if canonical_id else ""))

            # Rate limit courtesy for API calls
            interruptible_sleep(self._stop_event, self.artist_sleep)
            return 1

        except Exception as e:
            logger.error(f"Error processing artist: {e}")
            self.stats['errors'] += 1
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return 0
        finally:
            if conn:
                conn.close()

    def _lookup_canonical_artist_id(self, artist_name: str, verify_track: Optional[str]) -> Optional[int]:
        """Look up a canonical artist ID from Deezer and iTunes using track verification.

        Searches both services for 'artist_name track_title' to find the exact artist,
        then returns max(deezer_id, itunes_id) as a deterministic canonical identifier.
        Any SoulSync instance with the same artist and at least one matching track
        will arrive at the same canonical ID.

        Args:
            artist_name: Artist name to search for
            verify_track: A track title from the artist's library for verification

        Returns:
            max(deezer_id, itunes_id) as int, or the single available ID, or None
        """
        if not verify_track:
            return None

        matching = self._get_matching_engine()
        norm_artist = matching.normalize_string(artist_name) if matching else artist_name.lower().strip()

        deezer_artist_id = None
        itunes_artist_id = None

        # Search Deezer by "artist track" to find the exact artist
        deezer = self._get_deezer_client()
        if deezer:
            try:
                import requests as req
                query = f"{artist_name} {verify_track}"
                # shared deezer budget — this call used to bypass it entirely
                from core.deezer_throttle import wait_for_slot
                wait_for_slot()
                resp = req.get('https://api.deezer.com/search', params={'q': query, 'limit': 5}, timeout=10)
                if resp.ok:
                    for item in resp.json().get('data', []):
                        result_artist = item.get('artist', {}).get('name', '')
                        norm_result = matching.normalize_string(result_artist) if matching else result_artist.lower().strip()
                        if norm_result == norm_artist or (matching and matching.similarity_score(norm_artist, norm_result) >= 0.85):
                            raw_id = item.get('artist', {}).get('id')
                            if raw_id:
                                deezer_artist_id = int(raw_id)
                                logger.debug(f"Deezer artist ID for '{artist_name}': {deezer_artist_id}")
                            break
                interruptible_sleep(self._stop_event, 0.3)
            except Exception as e:
                logger.debug(f"Deezer track search failed for '{artist_name}': {e}")

        # Search iTunes by "artist track" to find the exact artist
        itunes = self._get_itunes_client()
        if itunes:
            try:
                query = f"{artist_name} {verify_track}"
                raw_results = itunes._search(query, entity='song', limit=5)
                if raw_results:
                    for item in raw_results:
                        result_artist = item.get('artistName', '')
                        norm_result = matching.normalize_string(result_artist) if matching else result_artist.lower().strip()
                        if norm_result == norm_artist or (matching and matching.similarity_score(norm_artist, norm_result) >= 0.85):
                            raw_id = item.get('artistId')
                            if raw_id:
                                itunes_artist_id = int(raw_id)
                                logger.debug(f"iTunes artist ID for '{artist_name}': {itunes_artist_id}")
                            break
                interruptible_sleep(self._stop_event, 0.3)
            except Exception as e:
                logger.debug(f"iTunes track search failed for '{artist_name}': {e}")

        # Return max of both IDs (deterministic regardless of which source each instance has)
        if deezer_artist_id and itunes_artist_id:
            canonical = max(deezer_artist_id, itunes_artist_id)
            logger.debug(f"Canonical ID for '{artist_name}': {canonical} (deezer={deezer_artist_id}, itunes={itunes_artist_id})")
            return canonical
        elif deezer_artist_id:
            return deezer_artist_id
        elif itunes_artist_id:
            return itunes_artist_id
        return None

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
                            logger.debug(
                                "%s matched artist=%r via album api=%r db=%r score=%.2f",
                                source_name,
                                artist.name,
                                api_name,
                                db_name,
                                score,
                            )
                            return discog

            except Exception as e:
                logger.debug(
                    "%s discography fetch failed for artist=%r: %s",
                    source_name,
                    artist.name,
                    e,
                )
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
            cursor.execute(PENDING_ALBUMS_SQL + " LIMIT ?", (self.batch_size,))
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
                    "UPDATE lib2_albums SET soul_id = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
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
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
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
            cursor.execute(PENDING_TRACKS_SQL + " LIMIT ?", (self.batch_size,))
            rows = cursor.fetchall()
            if not rows:
                return 0

            count = 0
            for track_id, title, artist_name, album_title in rows:
                if self.should_stop:
                    break

                # Song soul ID: artist + track (links singles to album versions)
                song_soul_id = generate_soul_id(artist_name, title)

                # updated_at moves with every soul_id write (L2-011): a row with
                # no soul_id is filtered OUT of the MetaSync export entirely, so
                # minting one is the moment that row starts existing for
                # consumers. Without the touch, a full walk that ran before this
                # worker meant the row never appeared in any later incremental.
                # Album track soul ID: artist + album + track (this release)
                album_soul_id = ''
                if album_title:
                    album_soul_id = generate_soul_id(artist_name, album_title, title)

                if not song_soul_id:
                    song_soul_id = f'soul_unnamed_{track_id}'
                cursor.execute(
                    "UPDATE lib2_tracks SET soul_id = ?, album_soul_id = ?, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND (soul_id IS NULL OR soul_id = '')",
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
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
            return 0
        finally:
            if conn:
                conn.close()

    # ── Migrations ──

    # How a stored id was derived, when the row predates the column. Kept
    # separate from the id-algorithm marker so an install that is already on the
    # current algorithm still gets its paths (L2-014).
    PATH_MIGRATION_KEY = 'soulid_artist_path_version'
    PATH_MIGRATION_VERSION = 'lib2_v1'

    def _migrate_artist_soul_id_paths(self):
        """Fill ``lib2_artists.soul_id_path`` for ids that predate the column.

        The column is additive, so existing rows start NULL — and nothing ever
        fills them: the worker only looks at artists with NO soul_id, and the
        id-algorithm migration returns early on an install already reading the
        current version. MetaSync/Hydrabase could therefore never tell a
        provider-canonical, reproducible artist key from a library-dependent
        album/name fallback (L2-014).

        Regenerating the ids to find out is not on the table: a soul_id is the
        shared content key peers have already traded claims about. Each path is
        PROVEN locally instead by recomputing it — a name-only or album-derived
        id reproduces exactly — and anything that reproduces neither is recorded
        as ``unknown`` rather than assumed canonical. An album deleted since the
        id was minted looks identical from here, and guessing would upgrade that
        key's trustworthiness on no evidence.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            columns = {r[1] for r in cursor.execute("PRAGMA table_info(lib2_artists)")}
            if 'soul_id_path' not in columns:
                return

            cursor.execute("SELECT value FROM metadata WHERE key = ? LIMIT 1",
                           (self.PATH_MIGRATION_KEY,))
            row = cursor.fetchone()
            if row and row[0] == self.PATH_MIGRATION_VERSION:
                return

            cursor.execute(r"""
                SELECT id, soul_id, name FROM lib2_artists
                 WHERE soul_id IS NOT NULL AND soul_id != ''
                   AND soul_id NOT LIKE 'soul\_unnamed\_%' ESCAPE '\'
                   AND soul_id_path IS NULL
                   AND name IS NOT NULL AND name != ''
            """)
            pending = cursor.fetchall()

            resolved = {'name': 0, 'album': 0, 'unknown': 0}
            for artist_id, stored, name in pending:
                path = 'unknown'
                if stored and stored == generate_soul_id(name):
                    path = 'name'
                else:
                    # Every owned album, not just today's alphabetically-first
                    # one: the library may have gained or lost releases since
                    # the id was minted, and only an exact reproduction proves
                    # the path.
                    cursor.execute("""
                        SELECT al.title FROM lib2_albums al
                         WHERE al.primary_artist_id = ?
                           AND al.title IS NOT NULL AND al.title != ''
                    """, (artist_id,))
                    for (title,) in cursor.fetchall():
                        if stored == generate_soul_id(name, title):
                            path = 'album'
                            break
                resolved[path] += 1
                cursor.execute(
                    "UPDATE lib2_artists SET soul_id_path = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (path, artist_id))

            cursor.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                (self.PATH_MIGRATION_KEY, self.PATH_MIGRATION_VERSION))
            conn.commit()
            if pending:
                logger.info(
                    "SoulID path migration: %d artist(s) — %d name, %d album, "
                    "%d unproven", len(pending), resolved['name'],
                    resolved['album'], resolved['unknown'])
        except Exception as e:
            logger.error(f"SoulID path migration failed: {e}")
            if conn:
                try:
                    conn.rollback()
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
        finally:
            if conn:
                conn.close()

    def _migrate_artist_soul_ids(self):
        """One-time reset: clear all artist soul IDs when algorithm changes.
        Uses a versioned metadata flag to run only once per algorithm version.

        One thing to know before bumping the version string while the legacy
        tables still exist: the mirror carries ``artists.soul_id`` into an empty
        ``lib2_artists.soul_id`` (docs §50.4.4.12), so a reset here would be
        refilled from the legacy value the *previous* algorithm produced. That is
        exactly right today — the flag has read ``debut_year_api_v2`` on every
        install since before the move, so the reset never fires and the mirror's
        one job is to carry those ids across. A genuinely new algorithm belongs
        after the legacy tables are gone (stage 3), or has to clear both sides.
        """
        conn = None
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT value FROM metadata WHERE key = 'soulid_artist_version' LIMIT 1")
            row = cursor.fetchone()
            if row and row[0] == 'debut_year_api_v2':
                return  # Already on latest version

            # Reset all artist soul IDs for regeneration
            cursor.execute("UPDATE lib2_artists SET soul_id = NULL, "
                           "updated_at = CURRENT_TIMESTAMP "
                           "WHERE soul_id IS NOT NULL")
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
                except Exception as _e:
                    logger.debug("rollback failed: %s", _e)
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

            # Counted through the very statements the batches take, rather than a
            # second copy of each WHERE clause kept identical by a comment. A
            # progress figure that disagrees with the work is how a worker looks
            # stuck long after it has finished.
            for sql in (PENDING_ARTISTS_SQL, PENDING_ALBUMS_SQL, PENDING_TRACKS_SQL):
                cursor.execute(f"SELECT COUNT(*) FROM ({sql})")
                total += (cursor.fetchone() or [0])[0]

            return total
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
