"""Comma Artist Splitter Job — finds "dummy" artists whose name is really
several artists joined by commas ("Camellia, Toby Fox") and splits them.

Multi-artist tracks tagged with a single comma-joined artist string make the
media server mint a fake artist per unique string (art-less, wrong scrobbles,
clutters the artist grid). The scan flags an artist only when BOTH checks agree
the string is not a real act:

  1. The full string is looked up on the metadata APIs (Deezer / iTunes /
     Spotify). An exact match means it's a genuinely comma-named artist
     ("Tyler, The Creator") — skipped. A built-in whitelist of famous
     comma-named acts short-circuits the lookup. If NO API could be reached
     the artist is skipped entirely (fail-safe: never flag unverified).
  2. Every comma-separated part must itself resolve to a known artist —
     in the user's own library first, else an exact API match. One
     unresolvable part kills the finding.

The fix re-tags the affected files: display artist becomes "A; B", the
per-artist list is written to the multi-value Artists tag (Picard convention,
same frames as issue #587), and an album-artist equal to the combined string
becomes the primary (first) artist. The server's next scan then credits each
artist individually and the dummy dissolves. Report-only (auto_fix = False).
"""

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


def normalize_artist_name(name) -> str:
    """Casefold + whitespace-collapse for exact-name comparison."""
    return ' '.join(str(name or '').casefold().split())


def split_comma_parts(name: str) -> list:
    """Split a comma-joined artist string into clean parts."""
    return [p.strip() for p in str(name or '').split(',') if p.strip()]


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
        'Each finding shows exactly how the artist would be split. Approving the fix re-tags '
        'the affected files with a properly separated artist list (the same multi-artist tag '
        'convention Picard uses). After your media server rescans, each artist is credited '
        'individually and the combined dummy artist disappears.\n\n'
        'Nothing is changed until you approve a finding.'
    )
    icon = 'repair-icon-artist'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    auto_fix = False

    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COUNT(DISTINCT ar.id)
                    FROM artists ar
                    JOIN tracks t ON t.artist_id = ar.id
                    WHERE ar.name LIKE '%,%'
                      AND t.file_path IS NOT NULL AND t.file_path != ''
                """)
                return cursor.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            # ORDER BY track count so the biggest offenders are checked first
            # when the per-scan cap bites. LIMIT+1 detects the cap.
            cursor.execute(f"""
                SELECT ar.id, ar.name, ar.thumb_url, COUNT(t.id) AS n
                FROM artists ar
                JOIN tracks t ON t.artist_id = ar.id
                WHERE ar.name LIKE '%,%'
                  AND t.file_path IS NOT NULL AND t.file_path != ''
                GROUP BY ar.id, ar.name, ar.thumb_url
                ORDER BY n DESC
                LIMIT {SCAN_ARTIST_LIMIT + 1}
            """)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching comma artists: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        capped = len(rows) > SCAN_ARTIST_LIMIT
        if capped:
            rows = rows[:SCAN_ARTIST_LIMIT]
            if context.report_progress:
                context.report_progress(
                    log_line=f'More than {SCAN_ARTIST_LIMIT} comma artists — checking the '
                             f'{SCAN_ARTIST_LIMIT} with the most tracks this run; the rest next run.',
                    log_type='warning')

        total = len(rows)
        if total == 0:
            if context.report_progress:
                context.report_progress(phase='No comma-joined artist names found',
                                        log_line='Nothing to check', log_type='success')
            return result

        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Verifying {total} comma artist(s)...', total=total)

        # Per-run memo of API lookups: (source, normalized query) → set of
        # normalized result names, or None for "source unreachable".
        search_memo = {}

        for i, (artist_id, name, thumb_url, track_count) in enumerate(rows):
            if context.check_stop():
                return result
            if context.wait_if_paused():
                return result

            result.scanned += 1
            parts = split_comma_parts(name)
            if len(parts) < 2:
                continue

            norm_full = normalize_artist_name(name)
            if norm_full in KNOWN_COMMA_ARTISTS:
                result.skipped += 1
                continue

            # Check 1: is the FULL string a real artist on any reachable API?
            is_real, checked_sources = self._full_string_is_real_artist(
                context, name, search_memo)
            if is_real:
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'"{name}" is a real artist — skipped', log_type='info')
                continue
            if not checked_sources:
                # No API reachable → cannot verify → never flag (fail-safe).
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'"{name}": no metadata API reachable — skipped (cannot verify)',
                        log_type='warning')
                continue

            # Check 2: every part must resolve to a known artist.
            parts_resolution = self._resolve_parts(context, parts, search_memo)
            if parts_resolution is None:
                result.skipped += 1
                if context.report_progress:
                    context.report_progress(
                        scanned=i + 1, total=total,
                        log_line=f'"{name}": not every part is a known artist — skipped',
                        log_type='info')
                continue

            sample_tracks = self._sample_tracks(context, artist_id)

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'"{name}" → {len(parts)} artists ({track_count} track(s))',
                    log_type='warning')

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='comma_artist_split',
                        severity='warning',
                        entity_type='artist',
                        entity_id=str(artist_id),
                        file_path=None,
                        title=f'Combined artist: {name}',
                        description=(
                            f'"{name}" looks like {len(parts)} separate artists — the fix '
                            f're-tags {track_count} track(s) so each artist is credited '
                            f'individually'
                        ),
                        details={
                            'artist_id': artist_id,
                            'artist_name': name,
                            'artist_thumb_url': thumb_url or None,
                            'combined_name': name,
                            'split_artists': parts,
                            'parts_resolution': parts_resolution,
                            'checked_sources': checked_sources,
                            'new_display_artist': '; '.join(parts),
                            'primary_artist': parts[0],
                            'track_count': track_count,
                            'tracks': sample_tracks,
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("Error creating comma-artist finding for %s: %s", artist_id, e)
                    result.errors += 1

            # Rate-limit courtesy between API-heavy artists.
            if context.sleep_or_stop(0.15):
                return result

            if context.update_progress:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Done — {result.findings_created} splittable artist(s) found',
                log_line=f'{result.findings_created} finding(s), '
                         f'{result.skipped} skipped, {result.scanned} checked',
                log_type='success')
        return result

    # --- verification helpers -------------------------------------------------

    def _search_artist_names(self, source: str, query: str, memo: dict):
        """Normalized artist-name set from one API source, memoized per run.
        Returns None when the source is unreachable/unusable (≠ empty result)."""
        key = (source, normalize_artist_name(query))
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
                        names.add(normalize_artist_name(n))
        except Exception as e:
            logger.debug("Artist search failed on %s for %r: %s", source, query, e)
            names = None
        memo[key] = names
        return names

    def _iter_sources(self, context: JobContext):
        for source in _API_SOURCES:
            if source == 'spotify' and context.is_spotify_rate_limited():
                continue
            yield source

    def _full_string_is_real_artist(self, context: JobContext, name: str, memo: dict):
        """Returns (is_real, checked_sources). checked_sources lists sources
        that answered — empty means the check could not run at all."""
        checked = []
        norm = normalize_artist_name(name)
        for source in self._iter_sources(context):
            names = self._search_artist_names(source, name, memo)
            if names is None:
                continue
            checked.append(source)
            if norm in names:
                return True, checked
        return False, checked

    def _resolve_parts(self, context: JobContext, parts: list, memo: dict):
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
                norm = normalize_artist_name(part)
                for source in self._iter_sources(context):
                    names = self._search_artist_names(source, part, memo)
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
                "SELECT id FROM artists WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
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
                SELECT t.title, t.file_path, al.title
                FROM tracks t
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.artist_id = ? AND t.file_path IS NOT NULL AND t.file_path != ''
                ORDER BY al.title, t.track_number
                LIMIT {TRACK_SAMPLE_LIMIT}
            """, (artist_id,))
            return [{'title': r[0], 'file_path': r[1], 'album': r[2] or ''}
                    for r in cursor.fetchall()]
        except Exception:
            return []
        finally:
            if conn:
                conn.close()
