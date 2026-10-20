"""Discography Backfill Job — finds missing albums/tracks for library artists."""

from core.metadata_service import (
    get_album_tracks_for_source,
    get_artist_discography,
    get_primary_source,
    MetadataLookupOptions,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, watchlist_artist_names
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
        'Runs rotate through your library: each run picks up where the last '
        'one left off, so a per-run limit spreads the whole collection over '
        'successive runs instead of rescanning the same first artists. A run '
        'stopped halfway resumes where it left off (rescanning at most a '
        'few artists it had just finished).\n\n'
        'Settings:\n'
        '- Max Artists Per Run: Limit how many artists to process per scan (default: 50)\n'
        '- Min Album Tracks Owned: Only backfill artists you own at least this many tracks of '
        'on one album, so a few tracks from a playlist don\'t pull in a whole discography. '
        'Watchlist artists always count. 0 checks every artist (default: 3)\n'
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
        # one or two playlist grabs are not "this artist is in my library" (#1572)
        'min_album_tracks_owned': 3,
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
        artists = self._get_library_artists(
            context, min_album_tracks=int(settings.get('min_album_tracks_owned', 3) or 0))
        if not artists:
            logger.info("No artists in library to scan")
            return result

        if self._coerce_limit(max_artists) <= 0:
            logger.warning(
                "Discography backfill: max_artists_per_run is %r — scanning no artists",
                max_artists)
            return result

        # Rotate through the library across runs (#1709): the per-run limit
        # used to slice artists[:max_artists] off the same name-ordered list
        # every time, so artists past the limit were never scanned at all.
        # The window starts after the last artist a previous run finished
        # and wraps to the top, so successive runs cover the whole library
        # and then start a fresh pass.
        window = self._rotation_window(context, artists, max_artists)

        total = len(window)
        # say where this run starts so the user can see the rotation working
        start_name = window[0].get('name') if window else '-'
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Scanning discography for {total} of {len(artists)} artists, starting at {start_name}...',
                total=total,
            )

        logger.info("Discography backfill: scanning %d artists (of %d total), starting at %s",
                    total, len(artists), start_name)
        primary_source = get_primary_source()

        last_completed = None
        last_saved_id = None

        def save_progress():
            # Every config save rewrites the whole config, so persist in
            # batches (like the AcoustID scanner's checkpoint) and never
            # write the same checkpoint twice in a row.
            nonlocal last_saved_id
            if last_completed is None or str(last_completed.get('id')) == last_saved_id:
                return
            self._save_checkpoint(context, last_completed)
            last_saved_id = str(last_completed.get('id'))

        for i, artist in enumerate(window):
            if context.check_stop():
                save_progress()
                return result
            if i % 5 == 0 and context.wait_if_paused():
                save_progress()
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
            except Exception as e:
                logger.warning("[%d/%d] Error scanning discography for %s: %s", i + 1, total, artist_name, e)
                result.errors += 1
                missing_count = 0

            if missing_count is None:
                # Nothing could be scanned for this artist (no discography
                # came back, or every release's track list failed to
                # fetch). The fetch layer cannot tell a provider outage
                # from an artist with genuinely nothing on file — except
                # when the primary source is Spotify and it is under a
                # rate-limit ban, where the empty result is the ban
                # talking. Then stop here WITHOUT moving the checkpoint,
                # so the ban does not burn this window of artists until
                # the next pass. (Probed outside the artist try/except: a
                # raising probe must never be read as an artist error.)
                try:
                    rate_limited = (primary_source == 'spotify'
                                    and context.is_spotify_rate_limited())
                except Exception:  # noqa: BLE001 — a broken probe is not a ban
                    rate_limited = False
                if rate_limited:
                    result.stopped_early = (
                        'Spotify rate limit hit; discography fetches are '
                        'returning nothing. Stopped so the remaining '
                        'artists are not marked scanned.')
                    logger.warning("Discography backfill stopping early: %s",
                                   result.stopped_early)
                    save_progress()
                    return result
                logger.info("[%d/%d] %s — no discography found", i + 1, total, artist_name)
            elif missing_count > 0:
                logger.info("[%d/%d] Found %d missing tracks for %s", i + 1, total, missing_count, artist_name)
            else:
                logger.info("[%d/%d] %s — no missing tracks", i + 1, total, artist_name)

            # The artist was attempted, so the rotation moves past it —
            # including on error, or one artist that always fails would
            # block every artist behind it forever. (A release whose track
            # list would not fetch, when others did, is likewise an
            # attempt: the artist is revisited on the next pass, not
            # retried inside this one.) But a stop requested while this
            # artist was mid-scan means it did NOT finish: leave the
            # checkpoint behind it so the next run resumes with it.
            if context.check_stop():
                save_progress()
                return result
            last_completed = artist
            if (i + 1) % 5 == 0:
                save_progress()

            if context.update_progress and (i + 1) % 3 == 0:
                context.update_progress(i + 1, total)

            # Rate limit between artists
            if context.sleep_or_stop(1.0):
                save_progress()
                return result

        save_progress()

        if context.update_progress:
            context.update_progress(total, total)

        logger.info(
            "Discography backfill complete: %d artists scanned, %d missing tracks found, %d errors",
            result.scanned, result.findings_created, result.errors,
        )
        return result

    # -- Cross-run rotation (#1709) -------------------------------------
    # Same persistence the AcoustID scanner uses for its checkpoint: a
    # value on the config manager under this job's own key, saved in
    # batches as artists finish. It is never cleared — rotation is a
    # cycle, not a pass with an end state.

    @staticmethod
    def _coerce_limit(value) -> int:
        """``max_artists_per_run`` as an int; the job default (50) for a
        stored value that is not a usable number."""
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return 50

    def _get_checkpoint(self, context):
        """The {'id', 'name'} of the last artist a run finished, or None."""
        if not context.config_manager:
            return None
        try:
            saved = context.config_manager.get(
                f'repair.jobs.{self.job_id}.scan_checkpoint', None)
        except Exception as e:  # noqa: BLE001 — a broken store means start at the top, not crash
            logger.warning("Could not read backfill checkpoint (starting at the top): %s", e)
            return None
        if (isinstance(saved, dict)
                and isinstance(saved.get('id'), (str, int))
                and not isinstance(saved.get('id'), bool)
                and isinstance(saved.get('name'), str) and saved['name']):
            return {'id': str(saved['id']), 'name': saved['name']}
        return None

    def _save_checkpoint(self, context, artist) -> None:
        if not context.config_manager or artist is None:
            return
        try:
            context.config_manager.set(
                f'repair.jobs.{self.job_id}.scan_checkpoint',
                {'id': str(artist['id']), 'name': str(artist['name'])},
            )
        except Exception as e:  # noqa: BLE001 — losing the checkpoint must not fail the scan,
            # but it must not be silent either: a store that never saves
            # brings back the exact #1709 symptom (every run at the top).
            logger.warning("Could not save backfill checkpoint: %s", e)

    def _rotation_window(self, context, artists, max_artists):
        """This run's slice of the library: up to ``max_artists`` artists
        starting right after the checkpoint, wrapping to the top of the
        list when the end is reached so the window is always full (unless
        the whole library fits in one run)."""
        if not artists:
            return []
        limit = self._coerce_limit(max_artists)
        if limit <= 0:
            return []
        count = min(len(artists), limit)
        start = 0
        checkpoint = self._get_checkpoint(context)
        if checkpoint is not None:
            for idx, artist in enumerate(artists):
                if str(artist.get('id')) == checkpoint['id']:
                    start = idx + 1
                    break
            else:
                # The checkpoint artist is gone (removed / re-identified).
                # Resume at the first artist that sorts at-or-after where
                # it was: '>=' so a same-named survivor (a re-identified
                # artist keeps its name) is scanned rather than skipped —
                # rescanning one artist is dedup-safe, skipping one is a
                # missed discography.
                start = next(
                    (idx for idx, artist in enumerate(artists)
                     if str(artist.get('name') or '') >= checkpoint['name']),
                    len(artists),
                )
        return [artists[(start + k) % len(artists)] for k in range(count)]

    def _scan_artist(self, context, artist, settings, primary_source, result):
        """Scan one artist's discography and create findings for missing tracks.

        Uses the same batched in-memory matching the Library and Artists pages
        use (get_candidate_albums_for_artist + get_candidate_tracks_for_albums)
        so one artist with a big library doesn't trigger thousands of per-track
        SQL queries.

        Returns the number of missing tracks found, or None when the
        artist could not actually be scanned: no discography came back at
        all (nothing on file, or every source failed — the fetch layer
        cannot tell those apart), or every included release's track list
        failed to fetch.
        """
        artist_name = artist['name']
        result.scanned += 1
        # #1504: owning profile for wishlist routing (None -> shared).
        artist_owner_pid = artist.get('owner_profile_id') or None

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
            return None

        source = discography.get('source', primary_source)
        albums = discography.get('albums', [])
        singles = discography.get('singles', [])
        if not albums and not singles:
            # The fetch layer returns an empty (but well-formed) result
            # both for an artist with nothing on file and when every
            # source failed. Report that as "not scanned" (None) and let
            # the caller decide what it means — see scan().
            result.skipped += 1
            return None
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

        # Process albums and singles. Track-fetch attempts are counted so
        # an artist whose releases ALL failed to fetch (a provider outage
        # behind a cached discography) reports "not scanned" (None)
        # instead of a clean zero — see scan().
        fetches_attempted = 0
        fetches_failed = 0
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
            fetches_attempted += 1
            try:
                tracks_data = get_album_tracks_for_source(source, str(release_id))
            except Exception as exc:
                logger.debug("Track fetch failed for %s / %s: %s", artist_name, release_name, exc)
                tracks_data = None

            if not tracks_data:
                fetches_failed += 1
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
                    include_downloads=True,
                )
                if db_track and confidence >= 0.7:
                    continue  # Already owned

                # Check if already in wishlist
                try:
                    track_id = track_item.get('id', '')
                    if track_id and self._is_in_wishlist(context.db, track_id):
                        continue
                except Exception as e:
                    logger.debug("wishlist membership check failed: %s", e)

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
                                # #1504: owning profile for wishlist routing.
                                'owner_profile_id': artist_owner_pid,
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
                                    # #1504: route to the artist owner's wishlist.
                                    profile_id=artist_owner_pid or 1,
                                )
                            except Exception as wl_err:
                                logger.debug("Auto-add to wishlist failed for '%s': %s", track_name, wl_err)
                    except Exception as e:
                        logger.debug("Error creating finding for %s: %s", track_name, e)
                        result.errors += 1

        if fetches_attempted and fetches_failed == fetches_attempted:
            # Every included release's track list failed to fetch: this
            # artist was not actually scanned, whatever the count says.
            return None
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

    def _get_library_artists(self, context, min_album_tracks=0):
        """Get all artists from the library database with source IDs.

        With ``min_album_tracks`` set, only artists owning that many tracks on
        one album, or on the watchlist, are returned (#1572)."""
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
            # #1504: owning profile so backfill findings/wishlist route correctly.
            if 'owner_profile_id' in columns:
                select.append("owner_profile_id")

            # Only artists actually IN the library — those you own at least one track
            # or album by. The `artists` table also carries bare rows for featured/
            # guest and re-identified artists (a "feat." on a single track, an
            # unknown-artist fix, etc.). Without this filter the backfill pulled their
            # ENTIRE discography and wishlisted it, flooding the wishlist with artists
            # "not in my library" (#977).
            cursor.execute(f"""
                SELECT {', '.join(select)}
                FROM artists
                WHERE name IS NOT NULL AND name != '' AND name != 'Unknown Artist'
                  AND (
                    EXISTS (SELECT 1 FROM tracks t
                            WHERE t.artist_id = artists.id AND COALESCE(t.file_path, '') != '')
                    OR EXISTS (SELECT 1 FROM albums al WHERE al.artist_id = artists.id)
                  )
                ORDER BY name, id
            """)
            artists = [dict(row) for row in cursor.fetchall()]
            if min_album_tracks > 0:
                cursor.execute("""
                    SELECT DISTINCT artist_id FROM (
                        SELECT artist_id, album_id FROM tracks
                        WHERE COALESCE(file_path, '') != '' AND album_id IS NOT NULL
                        GROUP BY artist_id, album_id
                        HAVING COUNT(*) >= ?)
                """, (min_album_tracks,))
                committed = {str(r[0]) for r in cursor.fetchall()}
                watched = watchlist_artist_names(cursor)
                artists = [a for a in artists
                           if str(a['id']) in committed
                           or str(a['name']).strip().casefold() in watched]
            return artists
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
            # Same coercion the scan uses, so a stored non-numeric limit
            # estimates the same window the scan will actually take.
            limit = self._coerce_limit(settings.get('max_artists_per_run', 50))
            return min(row[0] if row else 0, max(limit, 0))
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
