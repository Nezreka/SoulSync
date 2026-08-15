"""Attach images and IDs to cached stats result sets.

Lifted out of ``ListeningStatsWorker`` so the Year in Listening surface can use
the SAME enrichment. The year is cached by the worker but computed live on a
cache miss, and an enrichment that only existed on the worker meant the live
path returned rows with no artwork — a page that looks broken on exactly the
install (fresh, or just restarted) least able to explain why.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("stats_enrich")

# Result-set keys holding ARTIST rows. `discoveries` is the year's
# newly-found artists; it wants the same image + id treatment as top_artists,
# because the surface makes those rows clickable through to artist detail.
ARTIST_LIST_KEYS = ('top_artists', 'discoveries')


def _name_key(name) -> str:
    """Return the same indexed, Unicode-aware artist key as Library v2."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ''))


def enrich_stats_items(db, cache):
    """Add image URLs, IDs, and Last.fm data to cached stats items.

    Previously ran one SELECT per artist / album / track entry. Now each
    of the three lists is resolved with a single batched IN query so
    cache rebuilds scale with the number of result sets, not with the
    number of items in them.
    """
    # Every artist-shaped list in one pass, so a discovery and a top artist
    # can never resolve to different images for the same name.
    top_artists = [row for key in ARTIST_LIST_KEYS for row in (cache.get(key) or [])]
    top_albums = cache.get('top_albums') or []
    top_tracks = cache.get('top_tracks') or []

    if not (top_artists or top_albums or top_tracks):
        return

    # Normalize image URLs HERE, at cache-build time, not on every /api/stats/cached
    # read. normalize_image_url registers each URL in the image cache (a SQLite write
    # under a lock) — doing that per-request made the "instant" stats endpoint take ~20s
    # on HDD-backed installs (#935). Done once per background rebuild it's off the hot path,
    # and the read just returns the already-browser-safe URLs.
    from core.metadata import normalize_image_url as _fix_image

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        # ---- artist-shaped rows: match by Library v2's normalized key ----
        if top_artists:
            unique_names = {_name_key(a.get('name')) for a in top_artists
                            if a.get('name')}
            artist_rows = {}
            if unique_names:
                name_list = list(unique_names)
                chunk = 500
                for i in range(0, len(name_list), chunk):
                    sub = name_list[i:i + chunk]
                    placeholders = ','.join(['?'] * len(sub))
                    cursor.execute(
                        f"""
                        SELECT name_key, image_url,
                               COALESCE(canonical_artist_id, id),
                               json_extract(enrichment, '$.lastfm.listeners'),
                               json_extract(enrichment, '$.lastfm.playcount'),
                               soul_id
                        FROM lib2_artists
                        WHERE name_key IN ({placeholders})
                        ORDER BY (canonical_artist_id IS NOT NULL), id
                        """,
                        sub,
                    )
                    for row in cursor.fetchall():
                        # Keep first match per lowered name (LIMIT 1 equiv).
                        artist_rows.setdefault(row[0], row)

            for artist in top_artists:
                key = _name_key(artist.get('name'))
                r = artist_rows.get(key)
                if r:
                    artist['image_url'] = _fix_image(r[1]) or None
                    artist['id'] = r[2]
                    artist['global_listeners'] = r[3]
                    artist['global_playcount'] = r[4]
                    artist['soul_id'] = r[5]

        # ---- top_albums: match by LOWER(title) ----
        if top_albums:
            titles = [a.get('name') or '' for a in top_albums]
            unique_titles = {t.lower() for t in titles if t}
            album_rows = {}
            if unique_titles:
                title_list = list(unique_titles)
                chunk = 500
                for i in range(0, len(title_list), chunk):
                    sub = title_list[i:i + chunk]
                    placeholders = ','.join(['?'] * len(sub))
                    cursor.execute(
                        f"""
                        SELECT LOWER(al.title), al.image_url, al.id,
                               COALESCE(ar.canonical_artist_id, ar.id)
                        FROM lib2_albums al
                        JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                        WHERE LOWER(al.title) IN ({placeholders})
                        """,
                        sub,
                    )
                    for row in cursor.fetchall():
                        album_rows.setdefault(row[0], row)

            for album in top_albums:
                key = (album.get('name') or '').lower()
                r = album_rows.get(key)
                if r:
                    album['image_url'] = _fix_image(r[1]) or None
                    album['id'] = r[2]
                    album['artist_id'] = r[3]

        # ---- top_tracks: match by (LOWER(title), LOWER(artist name)) ----
        if top_tracks:
            pairs = set()
            for t in top_tracks:
                name = (t.get('name') or '').lower()
                artist = _name_key(t.get('artist'))
                if name:
                    pairs.add((name, artist))
            track_rows = {}
            if pairs:
                pair_list = list(pairs)
                chunk = 500
                for i in range(0, len(pair_list), chunk):
                    sub = pair_list[i:i + chunk]
                    placeholders = ','.join(['(?,?)'] * len(sub))
                    flat = [v for pair in sub for v in pair]
                    cursor.execute(
                        f"""
                        SELECT LOWER(t.title), ar.name_key,
                               al.image_url, t.id,
                               COALESCE(ar.canonical_artist_id, ar.id)
                        FROM lib2_tracks t
                        JOIN lib2_albums al ON al.id = t.album_id
                        JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                        WHERE (LOWER(t.title), ar.name_key) IN ({placeholders})
                        """,
                        flat,
                    )
                    for row in cursor.fetchall():
                        track_rows.setdefault((row[0], row[1]), row)

            for track in top_tracks:
                key = ((track.get('name') or '').lower(),
                       _name_key(track.get('artist')))
                r = track_rows.get(key)
                if r:
                    track['image_url'] = _fix_image(r[2]) or None
                    track['id'] = r[3]
                    track['artist_id'] = r[4]
    except Exception as e:
        logger.error(f"Error enriching stats items: {e}")
    finally:
        if conn:
            conn.close()
