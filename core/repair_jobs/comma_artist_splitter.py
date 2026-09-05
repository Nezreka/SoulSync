"""Comma Artist Splitter Job — finds "dummy" artists whose name is really
several artists joined by separators ("Camellia, Toby Fox" or "Artist & Guest").

Multi-artist tracks tagged with a single separator-joined artist string make the
media server mint a fake artist per unique string (art-less, wrong scrobbles,
clutters the artist grid). The scan flags an artist only when BOTH checks agree
the string is not a real act:

  1. The full string is looked up on the metadata APIs (Deezer / iTunes /
     Spotify). An exact match means it's a genuinely separator-named artist
     ("Tyler, The Creator") — skipped. A built-in whitelist of famous
     comma-named acts short-circuits the lookup. If NO API could be reached
     the artist is skipped entirely (fail-safe: never flag unverified).
  2. Every separated part must itself resolve to a known artist —
     in the user's own library first, else an exact API match. One
     unresolvable part kills the finding.

The fix re-tags the affected files: display artist becomes "A; B", the
per-artist list is written to the multi-value Artists tag (Picard convention,
same frames as issue #587), and an album-artist equal to the combined string
becomes the primary (first) artist. The server's next scan then credits each
artist individually and the dummy dissolves. Dry run is enabled by default;
disabling it applies verified splits immediately.

Supported separators: comma (,), semicolon (;), ampersand (&), forward slash (/).
"""

import json
import os
import re
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.comma_artist_splitter")

# Famous genuinely-comma-named acts — never flagged, no API call spent.
KNOWN_COMMA_ARTISTS = frozenset({
    'tyler, the creator',
    'earth, wind & fire',
    'earth, wind and fire',
    'crosby, stills & nash',
    'crosby, stills and nash',
    'crosby, stills, nash & young',
    'crosby, stills, nash and young',
    'emerson, lake & palmer',
    'emerson, lake and palmer',
    'blood, sweat & tears',
    'blood, sweat and tears',
    'peter, paul & mary',
    'peter, paul and mary',
    'me, mom & morgentaler',
    'now, now',
    'sammy davis, jr.',
})

# Cap on comma-artists examined per scan run (each may cost API lookups).
SCAN_ARTIST_LIMIT = 300
# Sample of affected tracks stored in the finding for display; the fix
# re-queries the DB so it always covers ALL tracks, not just the sample.
TRACK_SAMPLE_LIMIT = 40

_API_SOURCES = ('deezer', 'itunes', 'spotify')


def normalize_artist_name(name: str, symbols: list = None) -> str:
    """Casefold + whitespace-collapse for exact-name comparison.

    Only comma spacing is canonicalized ("Tyler,The Creator" equals
    "Tyler, The Creator"). Other separators stay intact so exact-name checks
    still distinguish real acts like "Earth, Wind & Fire".
    """
    text = re.sub(r'\s*,\s*', ', ', str(name or ''))
    return ' '.join(text.casefold().split())


def split_artist_parts(name: str, symbols: list = None) -> list:
    """Split a separator-joined artist string into clean parts.
    Supports: comma (,), semicolon (;), ampersand (&), forward slash (/)."""
    if symbols is None:
        symbols = [',', ';', '/', '&']
    if not symbols:
        return [str(name or '').strip()] if str(name or '').strip() else []
    parts = re.split(r'[{}]+'.format(''.join(symbols)), str(name or ''))
    return [p.strip() for p in parts if p.strip()]


@register_job
class CommaArtistSplitterJob(RepairJob):
    job_id = 'comma_artist_splitter'
    display_name = 'Comma Artist Splitter'
    description = 'Finds artists that are really several artists joined by commas and splits their tags'
    help_text = (
        'Multi-artist tracks are often tagged with one artist field holding a comma-joined '
        'string like "Camellia, Toby Fox". Your media server treats that string as a single '
        '(fake) artist: it gets no artist image, clutters your artist grid, and scrobbles '
        'credit the wrong name.\n\n'
        'This job scans your library for comma-joined artist names and verifies each one two '
        'ways before flagging it:\n'
        '1. The full string is checked against the metadata APIs — a real comma-named artist '
        'like "Tyler, The Creator" is recognized and skipped.\n'
        '2. Every part must itself be a known artist (in your own library, or an exact API '
        'match). If any part can\'t be verified, nothing is flagged.\n\n'
        'In dry run mode (default), each finding shows exactly how the artist would be split; '
        'approving it re-tags the affected files with a properly separated artist list (the '
        'same multi-artist tag convention Picard uses). Disable dry run to apply every '
        'verified split automatically. After your media server rescans, each artist is '
        'credited individually and the combined dummy artist disappears.\n\n'
        'Settings:\n'
        '- Comma Splitter: Enable splitting commas (,)\n'
        '- Semicolon Splitter: Enable splitting semicolons (;)\n'
        '- Forward Slash Splitter: Enable splitting forward slashes (/)\n'
        '- Ampersand Splitter: Enable splitting ampersands (&)\n'
        '- Dry Run: When enabled, only reports issues without modifying files'
    )
    icon = 'repair-icon-artist'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    default_settings = {
        'comma_splitter': True,
        'semicolon_splitter': True,
        'forward_slash_splitter': True,
        'ampersand_splitter': True,
        'dry_run': True,
    }
    auto_fix = True
    writes_library_files = True

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM lib2_track_files f
                    WHERE f.path IS NOT NULL AND f.path != ''
                      AND COALESCE(f.file_state, 'active') = 'active'
                """)
                return cursor.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

    def _get_settings(self, context: JobContext) -> dict:
        """Read job settings from config, falling back to defaults."""
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        merged.update(cfg)
        if 'dry_run' not in cfg and 'apply_automatically' in cfg:
            merged['dry_run'] = not bool(cfg.get('apply_automatically', False))
        return merged

    def _get_symbols(self, settings: dict) -> list:
        """Return the list of separators enabled in the settings."""
        symbols = []
        if settings.get('comma_splitter', True):
            symbols.append(',')
        if settings.get('semicolon_splitter', True):
            symbols.append(';')
        if settings.get('forward_slash_splitter', True):
            symbols.append('/')
        if settings.get('ampersand_splitter', True):
            symbols.append('&')
        return symbols

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        dry_run = bool(settings.get('dry_run', True))
        symbols: list = self._get_symbols(settings)
        sources: list = [s for s in _API_SOURCES
                         if not (s == 'spotify' and context.is_spotify_rate_limited())]

        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                WITH native_files AS (
                    SELECT t.id AS track_id, f.id AS file_id, f.path,
                           COALESCE(
                               (SELECT ta.artist_id
                                  FROM lib2_track_artists ta
                                 WHERE ta.track_id = t.id
                                 ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                                          ta.position, ta.artist_id
                                 LIMIT 1),
                               al.primary_artist_id
                           ) AS artist_id
                      FROM lib2_track_files f
                      JOIN lib2_tracks t ON t.id = f.track_id
                 LEFT JOIN lib2_albums al ON al.id = t.album_id
                     WHERE f.path IS NOT NULL AND f.path != ''
                       AND COALESCE(f.file_state, 'active') = 'active'
                )
                SELECT nf.track_id, nf.file_id, nf.path, nf.artist_id,
                       ar.name, ar.image_url
                  FROM native_files nf
             LEFT JOIN lib2_artists ar ON ar.id = nf.artist_id
                LIMIT {SCAN_ARTIST_LIMIT * 10}
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        if not tracks:
            if context.report_progress:
                context.report_progress(phase='No tracks to scan',
                                        log_line='Nothing to check', log_type='success')
            return result

        if context.update_progress:
            context.update_progress(0, len(tracks))
        if context.report_progress:
            context.report_progress(phase=f'Scanning {len(tracks)} track(s) for multi-artist metadata...',
                                    total=len(tracks))

        from mutagen import File as MutagenFile
        from core.library2.paths import resolve_lib2_path

        # Memoize: combined_artist_string → (parts, primary, display)
        memo_splits = {}
        # Per-run memo of API lookups
        search_memo = {}
        # Track: combined_artist → (artist_id, db_artist_name, thumb_url, files_with_this_tag)
        findings_map = {}

        for i, (track_id, file_id, file_path, artist_id,
                db_artist_name, thumb_url) in enumerate(tracks):
            if context.check_stop():
                logger.debug("Scan stopped by user request")
                return result
            if context.wait_if_paused():
                return result

            if context.update_progress:
                context.update_progress(i + 1, len(tracks))

            result.scanned += 1
            try:
                resolved = (
                    file_path if os.path.isfile(file_path) else
                    resolve_lib2_path(file_path, config_manager=context.config_manager)
                )
                if not resolved or not os.path.exists(resolved):
                    logger.debug(f"File not found or inaccessible: {file_path}")
                    continue

                audio = MutagenFile(resolved)
                if audio is None or audio.tags is None:
                    logger.debug(f"Unsupported or untagged file: {file_path}")
                    continue

                # Extract the artist tag value from the file metadata
                file_artist = self._get_artist_tag(audio)
                if not file_artist:
                    logger.debug(f"No artist tag found for file: {file_path}")
                    continue

                # Check if it contains separators
                parts = split_artist_parts(file_artist, symbols)
                if len(parts) < 2:
                    logger.debug(f"Artist tag does not contain multiple parts: {file_artist}")
                    continue

                # Memoize the split
                if file_artist not in memo_splits:
                    norm_full = normalize_artist_name(file_artist, symbols)
                    if norm_full in KNOWN_COMMA_ARTISTS:
                        memo_splits[file_artist] = None
                    else:
                        # Check 1: is the FULL string a real artist?
                        is_real, checked_sources = self._full_string_is_real_artist(
                            file_artist, search_memo, symbols, sources)
                        if is_real:
                            memo_splits[file_artist] = None
                        elif not checked_sources:
                            # No API reachable → fail-safe
                            memo_splits[file_artist] = None
                        else:
                            # Check 2: every part must resolve
                            parts_resolution = self._resolve_parts(context, parts, search_memo, symbols, sources)
                            if parts_resolution is None:
                                memo_splits[file_artist] = None
                            else:
                                memo_splits[file_artist] = (parts, parts[0], '; '.join(parts), checked_sources, parts_resolution)

                split_info = memo_splits.get(file_artist)
                if split_info is None:
                    result.skipped += 1
                    continue

                parts, primary, display, checked_sources, parts_resolution = split_info

                # Track this finding
                if file_artist not in findings_map:
                    findings_map[file_artist] = {
                        'artist_id': artist_id,
                        'artist_ids': set(),
                        'db_artist_name': db_artist_name,
                        'thumb_url': thumb_url,
                        'parts': parts,
                        'primary': primary,
                        'display': display,
                        'checked_sources': checked_sources,
                        'parts_resolution': parts_resolution,
                        'files': []
                    }
                if artist_id is not None:
                    findings_map[file_artist]['artist_ids'].add(int(artist_id))
                findings_map[file_artist]['files'].append({
                    'title': (audio.get('title', ['Unknown'])[0]
                              if isinstance(audio.get('title'), list)
                              else audio.get('title', 'Unknown')),
                    'file_path': file_path,
                    'lib2_file_id': int(file_id),
                    'lib2_track_id': int(track_id),
                })

            except Exception as e:
                logger.debug("Error scanning track %s: %s", file_path, e)
                result.errors += 1

        fixer = None
        if not dry_run:
            from core.repair_worker import RepairWorker

            fixer = RepairWorker(context.db, transfer_folder=context.transfer_folder)
            fixer._config_manager = context.config_manager

        # Create findings from the map
        for combined_name, info in sorted(findings_map.items(),
                                         key=lambda x: len(x[1]['files']),
                                         reverse=True)[:SCAN_ARTIST_LIMIT]:
            if context.check_stop():
                return result

            sample_files = info['files'][:TRACK_SAMPLE_LIMIT]
            artist_ids = sorted(info.get('artist_ids') or [])
            track_ids = sorted({
                int(item['lib2_track_id']) for item in info['files']
            })
            if artist_ids:
                finding_entity_type = 'artist'
                finding_entity_id = f'lib2:{artist_ids[0]}'
            else:
                # A malformed/uncredited native track still has a resolvable
                # subject; the details carry every related track/file id.
                finding_entity_type = 'track'
                finding_entity_id = f'lib2:{track_ids[0]}'

            if context.report_progress:
                context.report_progress(
                    scanned=result.scanned,
                    log_line=f'"{combined_name}" → {len(info["parts"])} artists ({len(info["files"])} track(s))',
                    log_type='warning')

            details = {
                'combined_name': combined_name,
                'artist_name': combined_name,
                'split_artists': info['parts'],
                'primary_artist': info['primary'],
                'new_display_artist': info['display'],
                'db_artist_id': info['artist_id'],
                'db_artist_name': info['db_artist_name'],
                'artist_thumb_url': info['thumb_url'],
                'parts_resolution': info['parts_resolution'],
                'checked_sources': info['checked_sources'],
                'file_count': len(info['files']),
                'track_count': len(track_ids),
                'files': sample_files,
                'all_files': info['files'],
                'library_v2_native': True,
                'library_v2': {
                    'artist_ids': artist_ids,
                    'track_ids': track_ids,
                    'file_ids': sorted({
                        int(item['lib2_file_id']) for item in info['files']
                    }),
                },
            }

            if dry_run and context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='comma_artist_split',
                        severity='warning',
                        entity_type=finding_entity_type,
                        entity_id=finding_entity_id,
                        file_path=None,
                        title=f'Combined artist: {combined_name}',
                        description=(
                            f'"{combined_name}" looks like {len(info["parts"])} separate artists — the fix '
                            f're-tags {len(info["files"])} track(s) so each artist is credited '
                            f'individually'
                        ),
                        details=details,
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("Error creating finding for %s: %s", combined_name, e)
                    result.errors += 1
            elif not dry_run:
                applied = fixer._fix_comma_artist_split(
                    finding_entity_type, finding_entity_id, None, details)
                if applied.get('success') and applied.get('action') == 'artists_split':
                    result.auto_fixed += applied.get('fixed', 0)
                    self._record_resolved_finding(
                        context=context,
                        combined_name=combined_name,
                        info=info,
                        details=details,
                        fixed_count=int(applied.get('fixed', 0)),
                        entity_type=finding_entity_type,
                        entity_id=finding_entity_id,
                    )
                elif not applied.get('success'):
                    result.errors += 1
                    logger.warning("Could not auto-apply comma-artist split for %s: %s",
                                   combined_name, applied.get('error'))

            if context.sleep_or_stop(0.15):
                return result

        if context.update_progress:
            context.update_progress(len(tracks), len(tracks))
        if context.report_progress:
            context.report_progress(
                phase=(f'Done — {result.findings_created} splittable artist(s) found'
                       if dry_run else f'Done — {result.auto_fixed} track(s) re-tagged'),
                log_line=(f'{result.findings_created} finding(s), '
                          if dry_run else f'{result.auto_fixed} track(s) fixed, ') +
                         f'{result.skipped} skipped, {result.scanned} checked',
                log_type='success')
        return result

    def _record_resolved_finding(self, context: JobContext, combined_name: str, info: dict,
                                 details: dict, fixed_count: int,
                                 entity_type: str, entity_id: str) -> None:
        """Persist an auto-applied split as a resolved finding entry."""
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE repair_findings
                SET status = 'resolved',
                    user_action = 'artists_split',
                    resolved_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND finding_type = ? AND entity_type = ? AND entity_id = ?
                  AND status = 'pending'
            """, (self.job_id, 'comma_artist_split', entity_type, entity_id))
            if cursor.rowcount > 0:
                conn.commit()
                return

            cursor.execute("""
                SELECT id FROM repair_findings
                WHERE job_id = ? AND finding_type = ? AND entity_type = ? AND entity_id = ?
                  AND status IN ('resolved', 'dismissed')
                LIMIT 1
            """, (self.job_id, 'comma_artist_split', entity_type, entity_id))
            if cursor.fetchone():
                conn.commit()
                return

            cursor.execute("""
                INSERT INTO repair_findings
                    (job_id, finding_type, severity, status, entity_type, entity_id,
                     file_path, title, description, details_json, user_action, resolved_at)
                VALUES (?, ?, ?, 'resolved', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                self.job_id,
                'comma_artist_split',
                'warning',
                entity_type,
                entity_id,
                None,
                f'Combined artist: {combined_name}',
                (
                    f'"{combined_name}" was auto-split into {len(info["parts"])} artists — '
                    f're-tagged {fixed_count} track(s)'
                ),
                json.dumps(details),
                'artists_split',
            ))
            conn.commit()
        except Exception as e:
            logger.warning("Could not record resolved auto-split finding for %s: %s",
                           combined_name, e)
        finally:
            if conn:
                conn.close()

    # --- verification helpers -------------------------------------------------

    def _get_artist_tag(self, audio):
        """Extract artist tag value from audio file (handles all formats)."""
        try:
            from mutagen.id3 import ID3
            from mutagen.mp4 import MP4

            if isinstance(audio.tags, ID3):
                tpe1 = audio.tags.get('TPE1')
                if tpe1:
                    val = tpe1.text[0] if tpe1.text else None
                    return str(val) if val else None
            elif isinstance(audio, MP4):
                val = audio.tags.get('\xa9ART')
                if val:
                    return str(val[0]) if isinstance(val, list) else str(val)
            elif hasattr(audio, 'get'):  # Vorbis family
                val = audio.get('artist')
                if val:
                    return str(val[0]) if isinstance(val, list) else str(val)
        except Exception:  # noqa: S110
            # A tag read that blows up on one file just means no artist could
            # be extracted from it; the caller treats None as exactly that,
            # and per-file logging would spam a scan over thousands of files.
            pass
        return None

    def _search_artist_names(self, source: str, query: str, memo: dict, symbols: list):
        """Normalized artist-name set from one API source, memoized per run.
        Returns None when the source is unreachable/unusable (≠ empty result)."""
        key = (source, normalize_artist_name(query, symbols))
        if key in memo:
            return memo[key]
        names = None
        try:
            from core.metadata_service import get_client_for_source
            client = get_client_for_source(source)
            if client is not None and hasattr(client, 'search_artists'):
                results = client.search_artists(query, limit=10)
                names = set()
                for r in (results or []):
                    n = r.get('name') if isinstance(r, dict) else getattr(r, 'name', None)
                    if n:
                        names.add(normalize_artist_name(n, symbols))
        except Exception as e:
            logger.debug("Artist search failed on %s for %r: %s", source, query, e)
            names = None
        memo[key] = names
        return names

    def _full_string_is_real_artist(self, name: str, memo: dict, symbols: list, sources: list):
        """Returns (is_real, checked_sources). checked_sources lists sources
        that answered — empty means the check could not run at all."""
        checked = []
        norm = normalize_artist_name(name, symbols)
        for source in sources:
            names = self._search_artist_names(source, name, memo, symbols)
            if names is None:
                continue
            checked.append(source)
            if norm in names:
                return True, checked
        return False, checked

    def _resolve_parts(self, context: JobContext, parts: list, memo: dict, symbols: list, sources: list):
        """Verify every part is a known artist. Returns the resolution list
        for the finding details, or None if any part can't be verified."""
        resolution = []
        for part in parts:
            entry = {'name': part, 'in_library': False,
                     'library_artist_id': None, 'verified_via': None}
            library_id = self._library_artist_id(context, part)
            if library_id is not None:
                entry['in_library'] = True
                entry['library_artist_id'] = library_id
                entry['verified_via'] = 'library'
            else:
                norm = normalize_artist_name(part, symbols)
                for source in sources:
                    names = self._search_artist_names(source, part, memo, symbols)
                    if names and norm in names:
                        entry['verified_via'] = source
                        break
                if entry['verified_via'] is None:
                    return None
            resolution.append(entry)
        return resolution

    def _library_artist_id(self, context: JobContext, name: str):
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM lib2_artists "
                "WHERE canonical_artist_id IS NULL "
                "AND LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
                (name,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    def _sample_tracks(self, context: JobContext, artist_id):
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT t.title, f.path, al.title
                  FROM lib2_tracks t
                  JOIN lib2_track_files f ON f.track_id = t.id
             LEFT JOIN lib2_albums al ON al.id = t.album_id
                 WHERE f.path IS NOT NULL AND f.path != ''
                   AND COALESCE(f.file_state, 'active') = 'active'
                   AND (al.primary_artist_id = ? OR EXISTS (
                       SELECT 1 FROM lib2_track_artists ta
                       WHERE ta.track_id = t.id AND ta.artist_id = ?))
              ORDER BY al.title, t.track_number
                LIMIT {TRACK_SAMPLE_LIMIT}
            """, (artist_id, artist_id))
            return [{'title': r[0], 'file_path': r[1], 'album': r[2] or ''}
                    for r in cursor.fetchall()]
        except Exception:
            return []
        finally:
            if conn:
                conn.close()
