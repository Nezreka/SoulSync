"""Discography Backfill Job — finds missing albums/tracks for library artists."""

from core.metadata_service import (
    get_album_tracks_for_source,
    get_artist_discography,
    get_primary_source,
    MetadataLookupOptions,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from core.watchlist_scanner import (
    is_acoustic_version,
    is_compilation_album,
    is_instrumental_version,
    is_live_version,
    is_remix_version,
)
from utils.logging_config import get_logger

logger = get_logger("repair_job.discography_backfill")


@register_job
class DiscographyBackfillJob(RepairJob):
    job_id = 'discography_backfill'
    display_name = 'Discography Backfill'
    description = 'Finds missing albums and tracks for artists in your library'
    help_text = (
        'Scans each artist in your library, fetches their full discography from '
        'the configured metadata source, and creates findings for any tracks '
        'you don\'t already own. Click Fix on a finding to add it to the '
        'wishlist for automatic download.\n\n'
        'Respects content filters: live versions, remixes, acoustic versions, '
        'instrumentals, and compilations are excluded by default.\n\n'
        'Settings:\n'
        '- Max Artists Per Run: Limit how many artists to process per scan (default: 50)\n'
        '- Auto Add To Wishlist: When on, missing tracks are pushed to the wishlist during the scan as well as logged as findings\n'
        '- Include Albums / EPs / Singles: Which release types to check\n'
        '- Include Live / Remixes / Acoustic / Compilations / Instrumentals: Content type filters'
    )
    icon = 'repair-icon-backfill'
    default_enabled = False
    default_interval_hours = 24  # Daily — the scan is rate-limited at 50 artists per run
    # Order matters: the UI renders these in dict-insertion order. Keys beginning
    # with `_section_` are rendered as group headers (not settings rows) and are
    # stripped from the saved config.
    default_settings = {
        '_section_core': 'Core',
        'max_artists_per_run': 50,
        # When on, missing tracks are added to the wishlist during the scan in
        # addition to creating findings. When off (default), only findings are
        # created; the user reviews them and decides per-track in the repair UI.
        'auto_add_to_wishlist': False,
        '_section_release_types': 'Release Types',
        'include_albums': True,
        'include_eps': True,
        'include_singles': True,
        '_section_content_filters': 'Content Filters',
        'include_live': False,
        'include_remixes': False,
        'include_acoustic': False,
        'include_compilations': False,
        'include_instrumentals': False,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)

        max_artists = settings.get('max_artists_per_run', 50)

        # Fetch all library artists with their metadata source IDs
        artists = self._get_library_artists(context)
        if not artists:
            logger.info("No artists in library to scan")
            return result

        total = min(len(artists), max_artists)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Scanning discography for {total} artists...',
                total=total,
            )

        logger.info("Discography backfill: scanning %d artists (of %d total)", total, len(artists))
        primary_source = get_primary_source()

        for i, artist in enumerate(artists[:max_artists]):
            if context.check_stop():
                return result
            if i % 5 == 0 and context.wait_if_paused():
                return result

            artist_id = artist['id']
            artist_name = artist['name']

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Scanning {i + 1} / {total}',
                    log_line=f'Fetching discography: {artist_name}',
                    log_type='info',
                )

            logger.info("[%d/%d] Scanning %s", i + 1, total, artist_name)
            try:
                missing_count = self._scan_artist(context, artist, settings, primary_source, result)
                if missing_count > 0:
                    logger.info("[%d/%d] Found %d missing tracks for %s", i + 1, total, missing_count, artist_name)
                else:
                    logger.info("[%d/%d] %s — no missing tracks", i + 1, total, artist_name)
            except Exception as e:
                logger.warning("[%d/%d] Error scanning discography for %s: %s", i + 1, total, artist_name, e)
                result.errors += 1

            if context.update_progress and (i + 1) % 3 == 0:
                context.update_progress(i + 1, total)

            # Rate limit between artists
            if context.sleep_or_stop(1.0):
                return result

        if context.update_progress:
            context.update_progress(total, total)

        logger.info(
            "Discography backfill complete: %d artists scanned, %d missing tracks found, %d errors",
            result.scanned, result.findings_created, result.errors,
        )
        return result

    def _scan_artist(self, context, artist, settings, primary_source, result):
        """Scan one artist's discography and create findings for missing tracks.

        Uses the same batched in-memory matching the Library and Artists pages
        use (get_candidate_albums_for_artist + get_candidate_tracks_for_albums)
        so one artist with a big library doesn't trigger thousands of per-track
        SQL queries.
        """
        artist_name = artist['name']
        result.scanned += 1

        # Build source ID map for more accurate lookups. Primary fallback
        # relies on artist-name search when a source ID is missing.
        source_ids = {}
        if artist.get('spotify_artist_id'):
            source_ids['spotify'] = artist['spotify_artist_id']
        if artist.get('itunes_artist_id'):
            source_ids['itunes'] = artist['itunes_artist_id']
        if artist.get('deezer_id'):
            source_ids['deezer'] = artist['deezer_id']

        # Fetch full discography
        discography = get_artist_discography(
            artist_id=str(artist['id']),
            artist_name=artist_name,
            options=MetadataLookupOptions(
                allow_fallback=True,
                skip_cache=False,
                artist_source_ids=source_ids if source_ids else None,
            ),
        )

        if not discography:
            result.skipped += 1
            return 0

        source = discography.get('source', primary_source)
        albums = discography.get('albums', [])
        singles = discography.get('singles', [])
        missing_count = 0
        active_server = None
        if context.config_manager:
            active_server = context.config_manager.get_active_media_server()

        auto_add = settings.get('auto_add_to_wishlist', False)

        # Pre-fetch the artist's library albums + tracks ONCE per artist for
        # fast in-memory matching (same pattern as the Library/Artists page
        # completion check). Avoids thousands of per-track SQL calls.
        candidate_tracks = None
        try:
            cand_albums = context.db.get_candidate_albums_for_artist(
                artist_name, server_source=active_server
            )
            if cand_albums:
                candidate_tracks = context.db.get_candidate_tracks_for_albums(
                    [a.id for a in cand_albums]
                )
        except Exception as exc:
            logger.debug("Could not pre-fetch candidates for %s: %s", artist_name, exc)
            candidate_tracks = None

        # Process albums and singles
        for release in albums + singles:
            if context.check_stop():
                return missing_count

            release_name = release.get('name', '')
            release_id = release.get('id', '')
            total_tracks = release.get('total_tracks', 0) or 0
            album_type = release.get('album_type', 'album')
            release_image = release.get('image_url', '') or ''
            release_date = release.get('release_date', '') or ''

            # Filter by release type
            if not self._should_include_release(total_tracks, album_type, settings):
                continue

            # Filter compilation albums
            if not settings.get('include_compilations', False):
                if is_compilation_album(release_name):
                    continue

            # Fetch tracks for this release
            try:
                tracks_data = get_album_tracks_for_source(source, str(release_id))
            except Exception:
                tracks_data = None

            if not tracks_data:
                continue

            # Extract track items
            items = []
            if isinstance(tracks_data, dict):
                items = tracks_data.get('items', [])
            elif isinstance(tracks_data, list):
                items = tracks_data

            if not items:
                continue

            # Build the full album context once per release so every finding
            # created for this release carries the same wishlist-ready dict.
            # Matches the shape add_to_wishlist / download pipeline expects.
            album_context = {
                'id': str(release_id),
                'name': release_name,
                'album_type': album_type,
                'release_date': release_date,
                'images': [{'url': release_image}] if release_image else [],
                'image_url': release_image,
                'artists': [{'name': artist_name}],
                'total_tracks': total_tracks,
            }

            for track_item in items:
                if context.check_stop():
                    return missing_count

                track_name = track_item.get('name', '')
                if not track_name:
                    continue

                # Extract artist name from track (fall back to the discography artist)
                track_artists = track_item.get('artists', [])
                if track_artists:
                    first_artist = track_artists[0]
                    if isinstance(first_artist, dict):
                        track_artist = first_artist.get('name', artist_name)
                    else:
                        track_artist = str(first_artist)
                else:
                    track_artist = artist_name

                # Content type filters
                if not settings.get('include_live', False):
                    if is_live_version(track_name, release_name):
                        continue
                if not settings.get('include_remixes', False):
                    if is_remix_version(track_name, release_name):
                        continue
                if not settings.get('include_acoustic', False):
                    if is_acoustic_version(track_name, release_name):
                        continue
                if not settings.get('include_instrumentals', False):
                    if is_instrumental_version(track_name, release_name):
                        continue

                # Check if track already exists in library — batched in-memory
                # match when candidates were pre-fetched (fast path). Falls back
                # to the legacy SQL path if pre-fetch failed.
                db_track, confidence = context.db.check_track_exists(
                    track_name, track_artist,
                    confidence_threshold=0.7,
                    server_source=active_server,
                    album=release_name,
                    candidate_tracks=candidate_tracks,
                )
                if db_track and confidence >= 0.7:
                    continue  # Already owned

                # Check if already in wishlist
                try:
                    track_id = track_item.get('id', '')
                    if track_id and self._is_in_wishlist(context.db, track_id):
                        continue
                except Exception:
                    pass

                # Build wishlist-ready track data. album is a dict (required by
                # add_to_wishlist and by the download pipeline's cover-art
                # extraction). Every finding carries enough context that the
                # fix handler can hand it straight to the wishlist.
                track_data = {
                    'id': track_item.get('id', f'backfill_{hash(f"{track_artist}_{track_name}") % 100000}'),
                    'name': track_name,
                    'artists': [{'name': track_artist}],
                    'album': dict(album_context),  # copy so per-track mutations don't bleed
                    'duration_ms': track_item.get('duration_ms', 0),
                    'track_number': track_item.get('track_number', 0),
                    'disc_number': track_item.get('disc_number', 1),
                    'image_url': release_image,
                }

                # Create finding
                if context.create_finding:
                    try:
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='missing_discography_track',
                            severity='info',
                            entity_type='track',
                            entity_id=str(track_data['id']),
                            file_path=None,
                            title=f'Missing: {track_name}',
                            description=(
                                f'"{track_name}" by {track_artist} from '
                                f'"{release_name}" is not in your library.'
                            ),
                            details={
                                'track_data': track_data,
                                'artist_name': artist_name,
                                'album_name': release_name,
                                'album_image_url': release_image,
                                'source': source,
                            },
                        )
                        if inserted:
                            result.findings_created += 1
                            missing_count += 1
                        else:
                            result.findings_skipped_dedup += 1

                        # Auto-wishlist mode: also push to wishlist now. The
                        # finding still gets created so the user has a log of
                        # what the backfill picked up. Only fire on a NEW
                        # finding — skip if dedup-suppressed (already on the
                        # wishlist or already auto-added in a prior scan).
                        if auto_add and inserted:
                            try:
                                context.db.add_to_wishlist(
                                    spotify_track_data=track_data,
                                    failure_reason='Discography backfill — missing from library (auto-added)',
                                    source_type='repair',
                                    source_info={
                                        'job': 'discography_backfill',
                                        'artist': artist_name,
                                        'auto_added': True,
                                    },
                                )
                            except Exception as wl_err:
                                logger.debug("Auto-add to wishlist failed for '%s': %s", track_name, wl_err)
                    except Exception as e:
                        logger.debug("Error creating finding for %s: %s", track_name, e)
                        result.errors += 1

        return missing_count

    @staticmethod
    def _should_include_release(total_tracks, album_type, settings):
        """Check if a release should be included based on type settings.

        Spotify lumps both EPs and true singles under album_type='single', so
        only an explicit 'album' / 'ep' / 'compilation' is trusted outright.
        Anything else (including 'single' or missing type) falls through to a
        track-count disambiguation matching the download pipeline:
          - 1-3 tracks -> true single
          - 4-6 tracks -> EP
          - 7+ tracks -> album
        """
        normalized = (album_type or '').lower()
        if normalized == 'compilation':
            return settings.get('include_compilations', False)
        if normalized == 'album':
            return settings.get('include_albums', True)
        if normalized == 'ep':
            return settings.get('include_eps', True)
        # 'single' or missing: disambiguate by track count
        if total_tracks >= 7:
            return settings.get('include_albums', True)
        if total_tracks >= 4:
            return settings.get('include_eps', True)
        if total_tracks >= 1:
            return settings.get('include_singles', False)
        return settings.get('include_albums', True)

    @staticmethod
    def _is_in_wishlist(db, track_id):
        """Check if a track ID is already in the wishlist."""
        conn = db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM wishlist_tracks WHERE spotify_track_id = ?",
                (str(track_id),),
            )
            return cursor.fetchone()[0] > 0
        finally:
            conn.close()

    def _get_library_artists(self, context):
        """Get all artists from the library database with source IDs."""
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Check which columns exist
            cursor.execute("PRAGMA table_info(artists)")
            columns = {col[1] for col in cursor.fetchall()}

            select = ["id", "name"]
            if 'spotify_artist_id' in columns:
                select.append("spotify_artist_id")
            if 'itunes_artist_id' in columns:
                select.append("itunes_artist_id")
            if 'deezer_id' in columns:
                select.append("deezer_id")

            cursor.execute(f"""
                SELECT {', '.join(select)}
                FROM artists
                WHERE name IS NOT NULL AND name != '' AND name != 'Unknown Artist'
                ORDER BY name
            """)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error("Error fetching library artists: %s", e, exc_info=True)
            return []
        finally:
            if conn:
                conn.close()

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
                SELECT COUNT(*) FROM artists
                WHERE name IS NOT NULL AND name != '' AND name != 'Unknown Artist'
            """)
            row = cursor.fetchone()
            settings = self._get_settings(context)
            max_artists = settings.get('max_artists_per_run', 50)
            return min(row[0] if row else 0, max_artists)
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
