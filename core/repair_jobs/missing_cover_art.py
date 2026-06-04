"""Missing Cover Art Filler Job — finds albums without artwork and locates art from APIs."""

import re

from core.metadata_service import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cover_art")

# Stopwords dropped before comparing album/artist names so trivial words
# ("the", "and") don't make two different names look like a match.
_NAME_STOPWORDS = {'the', 'a', 'an', 'and', 'of', 'feat', 'ft', 'featuring'}


def _norm_name(value) -> str:
    """Lowercase, strip bracketed qualifiers (Deluxe/Remaster/feat.) and
    punctuation so names can be compared on their significant words."""
    s = (value or '').lower()
    s = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', s)          # drop (...) [...] qualifiers
    s = re.sub(r'\b(?:feat|ft|featuring)\b.*', ' ', s)  # drop trailing "feat. X"
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


def _name_tokens(value) -> set:
    return set(_norm_name(value).split()) - _NAME_STOPWORDS


def _names_match(a, b) -> bool:
    """True when two names share all the significant words of the shorter one
    (so "Album" matches "Album (Deluxe)", but unrelated titles don't)."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


@register_job
class MissingCoverArtJob(RepairJob):
    job_id = 'missing_cover_art'
    display_name = 'Cover Art Filler'
    description = 'Finds albums missing artwork and locates art from metadata sources'
    help_text = (
        'Scans your library for albums that have no cover art stored in the database. '
        'For each missing cover, it searches the configured metadata sources using the '
        'album name and artist to find matching artwork. If Prefer Source is set, that '
        'source is tried first; otherwise the primary metadata source is used.\n\n'
        'When artwork is found, a finding is created with the image URL so you can review '
        'and apply it. The job does not download or embed artwork automatically.\n\n'
        'Settings:\n'
        '- Prefer Source: Optional source to try first; otherwise the primary metadata source is used'
    )
    icon = 'repair-icon-coverart'
    default_enabled = True
    default_interval_hours = 48
    default_settings = {}
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        primary_source = get_primary_source()
        source_priority = get_source_priority(primary_source)
        prefer_source = settings.get('prefer_source')
        if prefer_source and prefer_source != primary_source and prefer_source in source_priority:
            source_priority.remove(prefer_source)
            source_priority.insert(0, prefer_source)
            if primary_source in source_priority:
                source_priority.remove(primary_source)
                source_priority.insert(1, primary_source)

        # Fetch albums with missing artwork
        albums = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(albums)")
            album_columns = {column[1] for column in cursor.fetchall()}

            select_cols = [
                "al.id",
                "al.title",
                "ar.name",
                "al.spotify_album_id",
                "al.thumb_url",
                "ar.thumb_url",
            ]
            column_map = [
                ("itunes_album_id", "al.itunes_album_id"),
                ("deezer_album_id", "al.deezer_id"),
                ("discogs_album_id", "al.discogs_id"),
                ("hydrabase_album_id", "al.soul_id"),
            ]
            column_index = {}
            for alias, column in column_map:
                if column.split('.', 1)[1] in album_columns:
                    column_index[alias] = len(select_cols)
                    select_cols.append(f"{column} AS {alias}")

            cursor.execute(f"""
                SELECT {', '.join(select_cols)}
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                WHERE (al.thumb_url IS NULL OR al.thumb_url = '')
                  AND al.title IS NOT NULL AND al.title != ''
            """)
            albums = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching albums without artwork: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Found %d albums missing cover art", total)

        if context.report_progress:
            context.report_progress(phase=f'Searching artwork for {total} albums...', total=total)

        for i, row in enumerate(albums):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            album_id, title, artist_name, spotify_album_id, _, artist_thumb = row[:6]
            source_album_ids = {
                'spotify': spotify_album_id,
                'itunes': row[column_index['itunes_album_id']] if 'itunes_album_id' in column_index else None,
                'deezer': row[column_index['deezer_album_id']] if 'deezer_album_id' in column_index else None,
                'discogs': row[column_index['discogs_album_id']] if 'discogs_album_id' in column_index else None,
                'hydrabase': row[column_index['hydrabase_album_id']] if 'hydrabase_album_id' in column_index else None,
            }
            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Searching {i + 1} / {total}',
                    log_line=f'Searching: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            artwork_url = None

            # Try source-specific IDs first, then title/artist search, in priority order.
            for source in source_priority:
                artwork_url = self._try_source(source, source_album_ids.get(source), title, artist_name)
                if artwork_url:
                    break

            if artwork_url:
                if context.report_progress:
                    context.report_progress(
                        log_line=f'Found art: {title or "Unknown"}',
                        log_type='success'
                    )
                # Create finding for user to approve
                if context.create_finding:
                    try:
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='missing_cover_art',
                            severity='info',
                            entity_type='album',
                            entity_id=str(album_id),
                            file_path=None,
                            title=f'Missing artwork: {title or "Unknown"}',
                            description=f'Album "{title}" by {artist_name or "Unknown"} has no cover art. Found artwork from API.',
                            details={
                                'album_id': album_id,
                                'album_title': title,
                                'artist': artist_name,
                                'found_artwork_url': artwork_url,
                                'spotify_album_id': spotify_album_id,
                                'artist_thumb_url': artist_thumb or None,
                            }
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1
                    except Exception as e:
                        logger.debug("Error creating cover art finding for album %s: %s", album_id, e)
                        result.errors += 1
            else:
                result.skipped += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("Cover art scan: %d albums checked, %d found art, %d skipped",
                     result.scanned, result.findings_created, result.skipped)
        return result

    def _try_source(self, source, source_album_id, title, artist_name):
        """Try to get album art from a specific metadata source."""
        client = get_client_for_source(source)
        if not client:
            return None

        query = f"{artist_name} {title}" if artist_name else title

        try:
            if source_album_id:
                album_data = self._get_album_for_source(source, client, source_album_id)
                artwork_url = self._extract_artwork_url(album_data)
                if artwork_url:
                    return artwork_url

            if query and hasattr(client, 'search_albums'):
                # Pull a few results and only accept one whose title AND artist
                # actually match this album. The old code grabbed results[0]'s
                # artwork unconditionally, so a loose full-text search returning
                # the wrong album gave the wrong cover.
                results = client.search_albums(query, limit=5) or []
                for res in results:
                    if not self._result_matches(res, title, artist_name):
                        continue
                    artwork_url = self._extract_artwork_url(res)
                    if artwork_url:
                        return artwork_url
                    candidate_id = self._extract_album_id(res)
                    if candidate_id:
                        album_data = self._get_album_for_source(source, client, candidate_id)
                        artwork_url = self._extract_artwork_url(album_data)
                        if artwork_url:
                            return artwork_url
        except Exception as e:
            logger.debug("%s art lookup failed for '%s': %s", source.capitalize(), title, e)
        return None

    @staticmethod
    def _result_title_artist(item):
        """Pull (title, artist) from a search result that may be a dict or an
        Album-like object, across the various source clients."""
        if item is None:
            return '', ''
        if isinstance(item, dict):
            title = item.get('title') or item.get('name') or item.get('album') or ''
            artist = item.get('artist') or item.get('artist_name') or ''
            if not artist:
                artists = item.get('artists') or []
                if isinstance(artists, list) and artists:
                    a0 = artists[0]
                    artist = a0.get('name', '') if isinstance(a0, dict) else str(a0)
        else:
            title = getattr(item, 'title', None) or getattr(item, 'name', None) or getattr(item, 'album', None) or ''
            artist = getattr(item, 'artist', None) or getattr(item, 'artist_name', None) or ''
            if not artist:
                arts = getattr(item, 'artists', None) or []
                if isinstance(arts, list) and arts:
                    a0 = arts[0]
                    artist = a0.get('name', '') if isinstance(a0, dict) else str(a0)
        return str(title or ''), str(artist or '')

    @classmethod
    def _result_matches(cls, result, album_title, album_artist) -> bool:
        """Reject a search result unless it confidently matches the album.

        Title must match; if both the result and the album carry an artist, the
        artist must match too (the strongest guard against wrong covers). When
        the result has no artist to compare, require an exact title match.
        """
        r_title, r_artist = cls._result_title_artist(result)
        # Title may carry extra qualifiers (Deluxe/Remaster) → allow subset.
        if not _names_match(r_title, album_title):
            return False
        # Artist is the strong guard, so require its significant words to match
        # EXACTLY (not subset) — "Different Artist" must NOT match "Artist".
        if r_artist and album_artist:
            return _name_tokens(r_artist) == _name_tokens(album_artist)
        # No artist on the result → require an exact title match instead.
        return _norm_name(r_title) == _norm_name(album_title)

    @staticmethod
    def _get_album_for_source(source, client, album_id):
        if source == 'spotify':
            return client.get_album(album_id)
        return client.get_album(album_id, include_tracks=False)

    @staticmethod
    def _extract_album_id(item):
        if hasattr(item, 'id'):
            return getattr(item, 'id', None)
        if isinstance(item, dict):
            return item.get('id')
        return None

    @staticmethod
    def _extract_artwork_url(item):
        if not item:
            return None
        if hasattr(item, 'image_url') and getattr(item, 'image_url', None):
            return item.image_url
        if isinstance(item, dict):
            if item.get('image_url'):
                return item['image_url']
            images = item.get('images') or []
            if images and isinstance(images, list):
                first = images[0]
                if isinstance(first, dict):
                    return first.get('url')
        return None

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM albums
                WHERE (thumb_url IS NULL OR thumb_url = '')
                  AND title IS NOT NULL AND title != ''
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
