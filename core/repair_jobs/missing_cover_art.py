"""Missing Cover Art Filler Job — finds albums without artwork and locates art from APIs."""

import os
import re

from core.library2.maintenance_subjects import active_album_subjects
from core.library2.maintenance_subjects import subject_details
from core.metadata.art_apply import file_has_embedded_art, folder_has_cover_sidecar
from core.library.path_resolver import resolve_library_file_path
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
        'For each missing cover, it searches for matching artwork using the album name '
        'and artist. If you have configured cover-art sources (Settings > metadata '
        'enhancement art order), those are used first; otherwise it falls back to '
        'Prefer Source (if set) or the primary metadata source.\n\n'
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
        import os

        from core.library2.paths import resolve_lib2_path
        from core.library2.provider_adapters import fetch_artwork_url
        from core.metadata.art_apply import (
            file_has_embedded_art,
            folder_has_cover_sidecar,
        )

        result = JobResult()
        settings = self._get_settings(context)
        configured_order = (
            context.config_manager.get("metadata_enhancement.album_art_order")
            if context.config_manager else None
        )
        source_order = tuple(configured_order or ()) or None
        prefer_source = str(settings.get("prefer_source") or "").strip().lower()
        if prefer_source:
            remaining = tuple(source for source in (source_order or ()) if source != prefer_source)
            source_order = (prefer_source, *remaining)
        sidecar_enabled = bool(
            context.config_manager.get("metadata_enhancement.cover_art_download", True)
            if context.config_manager else True
        )
        albums = active_album_subjects(context.db, context.config_manager)
        total = len(albums)
        for index, subject in enumerate(albums):
            if context.check_stop() or (index % 10 == 0 and context.wait_if_paused()):
                return result
            result.scanned += 1
            raw_path = str(subject.get("rep_path") or "")
            resolved = raw_path if os.path.isfile(raw_path) else resolve_lib2_path(
                raw_path, config_manager=context.config_manager,
            )
            embedded = bool(resolved and file_has_embedded_art(resolved))
            sidecar = bool(
                resolved and folder_has_cover_sidecar(os.path.dirname(resolved))
            )
            db_missing = not str(subject.get("album_image") or "").strip()
            embed_missing = bool(resolved and not embedded)
            sidecar_missing = bool(resolved and sidecar_enabled and not sidecar)
            if not (db_missing or embed_missing or sidecar_missing):
                result.skipped += 1
                continue

            provider_result = fetch_artwork_url(
                "album",
                artist_name=subject.get("artist_name") or "",
                album_title=subject.get("title") or "",
                source_ids=subject.get("album_source_ids") or {},
                source_order=source_order,
            )
            sidecar_from_embedded = sidecar_missing and embedded
            if provider_result is None and not sidecar_from_embedded:
                result.skipped += 1
                continue
            artist_result = fetch_artwork_url(
                "artist",
                artist_name=subject.get("artist_name") or "",
                source_ids=subject.get("artist_source_ids") or {},
            )
            details = {
                "album_id": f"lib2:{subject['album_id']}",
                "album_title": subject.get("title"),
                "artist": subject.get("artist_name"),
                "artist_id": subject.get("artist_id"),
                "found_artwork_url": provider_result.url if provider_result else None,
                "artwork_source": provider_result.source if provider_result else "embedded",
                "artwork_source_id": (
                    provider_result.provider_entity_id if provider_result else None
                ),
                "artist_thumb_url": subject.get("artist_image"),
                "found_artist_url": (
                    artist_result.url
                    if artist_result and artist_result.url != subject.get("artist_image")
                    else None
                ),
                "artist_artwork_source": artist_result.source if artist_result else None,
                "album_folder": os.path.dirname(raw_path) if raw_path else None,
                "db_missing": db_missing,
                "embed_missing": embed_missing,
                "sidecar_from_embedded": sidecar_from_embedded,
                "musicbrainz_release_id": (
                    subject.get("album_source_ids") or {}
                ).get("musicbrainz"),
            }
            details.update(subject_details(subject))
            if context.create_finding:
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="missing_cover_art",
                    severity="info",
                    entity_type="album",
                    entity_id=f"lib2:{subject['album_id']}",
                    file_path=raw_path or None,
                    title=f"Missing artwork: {subject.get('title') or 'Unknown'}",
                    description=(
                        f'Artwork for "{subject.get("title")}" by '
                        f'{subject.get("artist_name") or "Unknown"} can be repaired '
                        f'from {details["artwork_source"]}.'
                    ),
                    details=details,
                )
                if inserted:
                    result.findings_created += 1
                else:
                    result.findings_skipped_dedup += 1
        if context.update_progress:
            context.update_progress(total, total)
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

    def _find_artist_art(self, artist_name, source_priority):
        """Search the configured sources for an artist image, in priority
        order. Returns the first confidently name-matched artist image URL,
        or None. Mirrors _try_source but for artists (Pache711: let the
        Cover Art Filler offer artist art as its own fixable target)."""
        if not artist_name:
            return None
        for source in source_priority:
            client = get_client_for_source(source)
            if not client or not hasattr(client, 'search_artists'):
                continue
            try:
                for res in (client.search_artists(artist_name, limit=5) or []):
                    r_name = getattr(res, 'name', None)
                    if isinstance(res, dict):
                        r_name = res.get('name')
                    # Exact significant-word match — never hang a wrong artist
                    # photo on someone just because the search was fuzzy.
                    if not r_name or _name_tokens(r_name) != _name_tokens(artist_name):
                        continue
                    url = self._extract_artwork_url(res)
                    if url:
                        return url
            except Exception as e:
                logger.debug("%s artist-art lookup failed for '%s': %s",
                             source.capitalize(), artist_name, e)
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
        try:
            return len(active_album_subjects(context.db, context.config_manager))
        except Exception:
            return 0

