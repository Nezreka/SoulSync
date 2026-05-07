"""Liked-artist multi-source matching — lifted from web_server.py.

Both function bodies are byte-identical to the originals. The
``spotify_client`` proxy + ``_get_*_client`` shims let the bodies resolve
their original names without any modification.
"""
import logging
import time

from config.settings import config_manager
from core.metadata.registry import (
    get_deezer_client,
    get_discogs_client,
    get_itunes_client,
    get_spotify_client,
)

logger = logging.getLogger(__name__)


def _get_itunes_client():
    """Mirror of web_server._get_itunes_client — delegates to registry."""
    return get_itunes_client()


def _get_deezer_client():
    """Mirror of web_server._get_deezer_client — delegates to registry."""
    return get_deezer_client()


def _get_discogs_client(token=None):
    """Mirror of web_server._get_discogs_client — delegates to registry."""
    return get_discogs_client(token)


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily so a Spotify re-auth that
    rebinds the cached client in core.metadata.registry is visible to the
    lifted bodies."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


spotify_client = _SpotifyClientProxy()


def _match_liked_artists_to_all_sources(database, profile_id: int):
    """Match pending liked artists to ALL metadata sources (Spotify, iTunes, Deezer, Discogs).
    Uses the same matching pattern as the watchlist scanner: DB-first, then API search
    with fuzzy name matching. Stores all resolved IDs so source switching works instantly."""
    pending = database.get_liked_artists_pending_match(profile_id, limit=200)
    if not pending:
        return

    # Source → column mapping
    source_cols = {
        'spotify': 'spotify_artist_id',
        'itunes': 'itunes_artist_id',
        'deezer': 'deezer_artist_id',
        'discogs': 'discogs_artist_id',
    }
    id_cols = list(source_cols.values())

    # Reject known placeholder images and local server paths
    _placeholder_hashes = {'2a96cbd8b46e442fc41c2b86b821562f'}
    def _valid_image(url):
        if not url or not url.strip():
            return None
        if any(ph in url for ph in _placeholder_hashes):
            return None
        # Reject local media server paths (Plex/Jellyfin) — not loadable in browser
        if url.startswith('/') or url.startswith('\\'):
            return None
        if not url.startswith('http'):
            return None
        return url

    # Build search clients for each source
    from core.deezer_client import DeezerClient
    search_clients = {}
    if spotify_client and spotify_client.is_spotify_authenticated():
        search_clients['spotify'] = spotify_client
    try:
        search_clients['itunes'] = _get_itunes_client()
    except Exception as e:
        logger.debug("itunes client init failed: %s", e)
    try:
        search_clients['deezer'] = _get_deezer_client()
    except Exception as e:
        logger.debug("deezer client init failed: %s", e)
    try:
        dc = _get_discogs_client()
        # Only use Discogs if token is configured
        from config.settings import config_manager as _cm
        if _cm.get('discogs.token', ''):
            search_clients['discogs'] = dc
    except Exception as e:
        logger.debug("discogs client init failed: %s", e)

    # Reuse watchlist scanner's fuzzy matching logic
    from core.watchlist_scanner import WatchlistScanner
    _normalize = WatchlistScanner._normalize_artist_name

    def _best_match(results, artist_name):
        """Pick best match from search results using name similarity (same as watchlist scanner)."""
        if not results:
            return None
        # Exact normalized match
        for r in results:
            if _normalize(r.name) == _normalize(artist_name):
                return r
        # Fuzzy scoring
        best = None
        best_sim = 0
        for r in results:
            # Simple normalized comparison
            n1 = _normalize(artist_name)
            n2 = _normalize(r.name)
            if n1 == n2:
                return r
            # Levenshtein-style similarity
            max_len = max(len(n1), len(n2))
            if max_len == 0:
                continue
            distance = sum(1 for a, b in zip(n1, n2, strict=False) if a != b) + abs(len(n1) - len(n2))
            sim = (max_len - distance) / max_len
            if sim > best_sim:
                best_sim = sim
                best = r
        if best and best_sim >= 0.85:
            return best
        return None

    api_calls = 0
    matched = 0

    for entry in pending:
        name = entry['artist_name']
        pool_id = entry['id']
        harvested_ids = {}
        best_image = None

        # Pre-load existing IDs from the entry itself
        for col in id_cols:
            if entry.get(col):
                harvested_ids[col] = entry[col]

        # --- DB STRATEGIES (free, no API calls) ---

        # 1. Library artists table
        try:
            conn = database._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM artists WHERE name = ? COLLATE NOCASE LIMIT 1", (name,))
            row = cursor.fetchone()
            if row:
                r = dict(row)
                for col in id_cols:
                    if r.get(col) and col not in harvested_ids:
                        harvested_ids[col] = str(r[col])
                if _valid_image(r.get('thumb_url')):
                    best_image = r['thumb_url']
        except Exception as e:
            logger.debug("library artist lookup failed: %s", e)

        # 2. Watchlist artists
        try:
            conn = database._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM watchlist_artists WHERE artist_name = ? COLLATE NOCASE AND profile_id = ? LIMIT 1",
                (name, profile_id)
            )
            row = cursor.fetchone()
            if row:
                wl = dict(row)
                for col in id_cols:
                    if wl.get(col) and col not in harvested_ids:
                        harvested_ids[col] = str(wl[col])
                if _valid_image(wl.get('image_url')) and not best_image:
                    best_image = wl['image_url']
        except Exception as e:
            logger.debug("watchlist artist lookup failed: %s", e)

        # 3. Metadata cache (all sources)
        try:
            conn = database._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT entity_id, source, image_url FROM metadata_cache_entities WHERE entity_type = 'artist' AND name = ? COLLATE NOCASE",
                (name,)
            )
            for row in cursor.fetchall():
                col = source_cols.get(row['source'])
                if col and col not in harvested_ids:
                    harvested_ids[col] = row['entity_id']
                if _valid_image(row['image_url']) and not best_image:
                    best_image = row['image_url']
        except Exception as e:
            logger.debug("metadata cache lookup failed: %s", e)

        # --- API STRATEGIES (search each missing source) ---
        # Same pattern as watchlist scanner's _backfill_missing_ids
        for source, col in source_cols.items():
            if col in harvested_ids:
                continue  # Already have this source's ID
            client = search_clients.get(source)
            if not client:
                continue
            if api_calls >= 200:  # Hard cap per refresh cycle
                break
            try:
                results = client.search_artists(name, limit=5)
                best = _best_match(results, name)
                if best:
                    harvested_ids[col] = best.id
                    if hasattr(best, 'image_url') and _valid_image(best.image_url) and not best_image:
                        best_image = best.image_url
                api_calls += 1
                time.sleep(0.4)  # Rate limit breathing room
            except Exception as e:
                logger.debug(f"[Your Artists] {source} search failed for '{name}': {e}")
                api_calls += 1

        # Save all harvested IDs
        if harvested_ids:
            # Determine best active source/ID — prefer Spotify, then iTunes, Deezer, Discogs
            resolved_source = None
            resolved_id = None
            for src in ('spotify', 'itunes', 'deezer', 'discogs'):
                col = source_cols[src]
                if col in harvested_ids:
                    resolved_source = src
                    resolved_id = harvested_ids[col]
                    break

            database.update_liked_artist_match(
                pool_id, active_source=resolved_source, active_source_id=resolved_id,
                image_url=best_image, all_ids=harvested_ids
            )
            matched += 1

    database.sync_liked_artists_watchlist_flags(profile_id)
    logger.info(f"[Your Artists] Matched {matched}/{len(pending)} artists to {len(search_clients)} sources ({api_calls} API calls)")

    # Image backfill: fetch images for matched artists that have IDs but no image
    _backfill_liked_artist_images(database, profile_id, search_clients)


def _backfill_liked_artist_images(database, profile_id: int, search_clients: dict):
    """Fetch images for matched artists missing artwork using their stored source IDs."""
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id
            FROM liked_artists_pool
            WHERE profile_id = ? AND match_status = 'matched'
              AND (image_url IS NULL OR image_url = ''
                   OR image_url LIKE '%2a96cbd8b46e442fc41c2b86b821562f%'
                   OR image_url NOT LIKE 'http%')
            LIMIT 100
        """, (profile_id,))
        rows = cursor.fetchall()
        if not rows:
            return

        logger.info(f"[Your Artists] Backfilling images for {len(rows)} artists...")
        filled = 0

        for row in rows:
            r = dict(row)
            image_url = None

            # Try Spotify artist lookup (has best images)
            if r.get('spotify_artist_id') and 'spotify' in search_clients:
                try:
                    sp = search_clients['spotify']
                    if hasattr(sp, 'sp') and sp.sp:
                        artist_data = sp.sp.artist(r['spotify_artist_id'])
                        if artist_data and artist_data.get('images'):
                            image_url = artist_data['images'][0]['url']
                except Exception as e:
                    logger.debug("spotify artist image fetch failed: %s", e)

            # Try Deezer (direct image URL from ID)
            if not image_url and r.get('deezer_artist_id'):
                image_url = f"https://api.deezer.com/artist/{r['deezer_artist_id']}/image?size=big"

            if image_url:
                try:
                    cursor2 = conn.cursor()
                    cursor2.execute(
                        "UPDATE liked_artists_pool SET image_url = ? WHERE id = ?",
                        (image_url, r['id'])
                    )
                    filled += 1
                except Exception as e:
                    logger.debug("liked artist image update failed: %s", e)
                time.sleep(0.3)

        conn.commit()
        if filled:
            logger.info(f"[Your Artists] Backfilled {filled}/{len(rows)} artist images")
    except Exception as e:
        logger.debug(f"[Your Artists] Image backfill error: {e}")
