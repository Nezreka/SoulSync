"""Suspect Album Tag Detector — finds tracks that are probably filed under the
wrong album due to bad embedded tags (e.g. a Daughtry song filed under "Hitzone 43").

Detection signals (singles are never suspects):
  1. The album has exactly 1 locally-owned track AND the album has no cover art.
  2. The album name matches a known compilation / sampler pattern (case-insensitive)
     BUT the track's artist is NOT "Various Artists" or "Various" -- an artist's
     own "Greatest Hits" excepted.
  3. The album has exactly 1 locally-owned track AND the DB track_count field says
     the real release has more than 3 tracks. Only together with another signal:
     one wished-for track of a big album is the normal Wishlist case.
  4. (#1567) The album has MORE than 1 locally-owned track AND at least 3
     distinct track artists AND (duplicate (disc, track) numbers OR the album
     artist is not among the track artists) — the fingerprint of a poisoned
     batch that filed unrelated tracks under one bogus album. Genuine Various
     Artists compilations are exempt, as is the artist-mismatch half of the
     signal on albums carrying a real shared provider album id (genuine
     curator/DJ/label-credited compilations — M3 decision, see
     _scan_multi_track_albums).

The fix action is "reidentify" — the existing Re-identify modal handles the actual fix.
This job only DETECTS and emits findings; it never modifies files itself.
"""

import re
from core.imports.compilation import is_various_artists_name
from core.imports.context import is_real_provider_album_id
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, not_locked_sql
from utils.logging_config import get_logger

logger = get_logger("repair_job.suspect_album_tag")

# Album name patterns that are almost always compilations / samplers.
# A track by a named artist inside one of these is very likely wrong-tagged.
_COMPILATION_PATTERNS = re.compile(
    r'\b(hitzone|now\s*(?:\d+|that\'?s\s+what\s+i\s+call\s+music)|top\s*\d{2,3}|vol\.?\s*\d+|various\s*artists?|'
    r'best\s+of|greatest\s+hits?|ultimate\s+collection|the\s+collection|'
    r'essential\s+hits?|playlist|soundtrack|ost|karaoke|tribute\s+to|'
    r'years\s+of\s+hits?|summer\s+hits?|chart\s+hits?)\b',
    re.IGNORECASE,
)

# The artist's own compilation: suspect only when filed under somebody else.
_ARTIST_COMPILATION = re.compile(
    r'\b(best\s+of|greatest\s+hits?|ultimate\s+collection|the\s+collection|essential\s+hits?)\b',
    re.IGNORECASE,
)

# The various-artists vocabulary is shared (core/imports/compilation.py);
# _is_various_artist is kept as a thin local alias so existing call sites
# read unchanged.
def _is_various_artist(name: str) -> bool:
    return is_various_artists_name(name)


@register_job
class SuspectAlbumTagDetector(RepairJob):
    job_id = 'suspect_album_tag_detector'
    display_name = 'Suspect Album Tags'
    description = (
        'Finds tracks that are probably filed under the wrong album due to bad '
        'embedded tags (e.g. a single song by a named artist inside a compilation).'
    )
    help_text = (
        'Scans for tracks that look like they ended up in the wrong album.\n\n'
        'Detection signals:\n'
        '- The album has only 1 of your tracks AND no cover art\n'
        '- The album name matches a known compilation pattern (Hitzone, Now, Vol., '
        'Greatest Hits, etc.) but the artist is not "Various Artists"\n'
        '- The album has only 1 of your tracks AND the original release has 4+ tracks '
        '(only together with another signal)\n'
        '- Singles and an artist\'s own "Greatest Hits" are never flagged\n\n'
        '- Multi-track albums with 3+ distinct artists AND duplicate track numbers\n'
        '(within a disc — repeats across discs are normal for multi-disc sets), '
        'or where the album artist is not among the track artists, are flagged as '
        'a possibly poisoned batch (genuine Various Artists compilations exempt; '
        'the artist-mismatch half is also waived when the album carries a real '
        'shared provider album id, i.e. a genuine curator/DJ-credited compilation)\n\n'
        'For each finding, click Re-identify to search for the correct album and '
        'let SoulSync re-file the track automatically.\n\n'
        'Settings:\n'
        '- Min compilation track count: only flag lone-track albums where the full '
        'release has at least this many tracks (default: 4)\n'
        '- Check cover art: flag albums with 1 local track and no cover art (default: on)\n'
        '- Min distinct artists: multi-track albums need at least this many distinct '
        'track artists before the poisoned-batch signal can fire (default: 3)'
    )
    icon = 'repair-icon-metadata'
    default_enabled = False
    default_interval_hours = 168   # weekly
    default_settings = {
        'min_compilation_track_count': 4,
        'check_missing_art': True,
        'min_multi_track_distinct_artists': 3,
    }
    auto_fix = False   # findings only; fix via Re-identify modal
    writes_library_files = False

    # ------------------------------------------------------------------ #
    def _get_settings(self, context: JobContext):
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(
            f'repair.jobs.{self.job_id}.settings', {})
        merged = self.default_settings.copy()
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def _get_setting(self, context: JobContext, key: str, default=None):
        return self._get_settings(context).get(key, default)

    # ------------------------------------------------------------------ #
    def estimate_scope(self, context: JobContext) -> int:
        try:
            conn = context.db._get_connection()
            try:
                cur = conn.cursor()
                album_not_locked = not_locked_sql(cur, 'albums', 'al')
                cur.execute(f"""
                    SELECT COUNT(*) FROM (
                        SELECT al.id
                        FROM albums al
                        JOIN tracks t ON t.album_id = al.id
                        WHERE t.file_path IS NOT NULL AND t.file_path != ''
                          {album_not_locked}
                        GROUP BY al.id
                        HAVING COUNT(t.id) = 1
                    )
                """)
                row = cur.fetchone()
                return row[0] if row else 0
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Scope estimate failed: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        min_track_count = int(settings.get('min_compilation_track_count', 4))
        check_art = bool(settings.get('check_missing_art', True))

        if context.report_progress:
            context.report_progress(phase='Scanning for suspect album tags…',
                                    log_line='Loading tracks…', log_type='info')

        conn = context.db._get_connection()
        try:
            cur = conn.cursor()
            album_not_locked = not_locked_sql(cur, 'albums', 'al')
            # Pull every album that has exactly 1 locally-owned track.
            # Include the DB track_count (from the metadata source) so we
            # can tell if the full release is actually bigger.
            # Join both track artist and album artist to get accurate track-level identity.
            cur.execute(f"""
                SELECT
                    al.id          AS album_id,
                    al.title       AS album_title,
                    al.thumb_url   AS thumb_url,
                    al.record_type AS album_type,
                    al.track_count AS full_track_count,
                    COALESCE(tar.id, aar.id)   AS artist_id,
                    COALESCE(tar.name, aar.name, '') AS artist_name,
                    aar.name       AS album_artist_name,
                    t.id           AS track_id,
                    t.title        AS track_title,
                    t.file_path    AS file_path
                FROM albums al
                JOIN tracks  t  ON t.album_id = al.id
                LEFT JOIN artists tar ON tar.id = t.artist_id
                LEFT JOIN artists aar ON aar.id = al.artist_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  {album_not_locked}
                GROUP BY al.id
                HAVING COUNT(t.id) = 1
                ORDER BY al.title
                LIMIT 2000
            """)
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

        total = len(rows)
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} single-track albums…',
                                    total=total, log_line=f'{total} candidates',
                                    log_type='info')

        for i, row in enumerate(rows):
            if context.check_stop():
                break
            if i % 50 == 0 and context.wait_if_paused():
                break

            result.scanned += 1
            if i % 50 == 0 and context.update_progress:
                context.update_progress(i, total)

            album_title   = (row['album_title'] or '').strip()
            artist_name   = (row['artist_name'] or '').strip()
            album_artist  = (row.get('album_artist_name') or '').strip()
            thumb_url     = row['thumb_url']
            full_count    = row['full_track_count'] or 0
            track_id      = row['track_id']
            track_title   = (row['track_title'] or '').strip()

            reasons = []
            if (row.get('album_type') or '').lower() == 'single':
                result.skipped += 1
                continue

            # Signal 1: no cover art + lone track
            if check_art and not thumb_url:
                reasons.append('no cover art')

            # Signal 2: compilation-pattern album name OR Various Artists album with named track artist
            own_album = album_artist.lower() == artist_name.lower()
            is_compilation_title = bool(_COMPILATION_PATTERNS.search(
                _ARTIST_COMPILATION.sub('', album_title) if own_album else album_title))
            is_va_track = _is_various_artist(artist_name)
            is_va_album = _is_various_artist(album_artist)

            if is_compilation_title and not is_va_track:
                reasons.append(f'album name "{album_title}" looks like a compilation')
            elif is_va_album and not is_va_track:
                reasons.append(f'lone track in Various Artists compilation "{album_title}"')

            # Signal 3: full release has many tracks but we only own 1 --
            # corroboration only, never a reason on its own.
            if full_count >= min_track_count and reasons:
                reasons.append(
                    f'only 1 of {full_count} tracks locally owned')

            if not reasons:
                result.skipped += 1
                continue

            # Build the description shown in the Repair dashboard card
            reason_text = '; '.join(reasons)
            title = f'Suspect album tag: "{track_title}"'
            description = (
                f'Track "{track_title}" by {artist_name} is the only track in '
                f'album "{album_title}" ({reason_text}). '
                f'It may be tagged with the wrong album.'
            )

            details = {
                'track_id':    track_id,
                'track_title': track_title,
                'album_title': album_title,
                'artist_name': artist_name,
                'album_artist_name': album_artist,
                'reasons':     reasons,
                'full_track_count': full_count,
                'has_cover':   bool(thumb_url),
                'album_thumb_url': thumb_url or '',
                'thumb_url':   thumb_url or '',
                # Pre-fill the Re-identify search query
                'reidentify_query': f'{track_title} {artist_name}'.strip(),
            }

            try:
                if context.create_finding:
                    created = context.create_finding(
                        job_id=self.job_id,
                        finding_type='suspect_album_tag',
                        severity='warning',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=row['file_path'] or '',
                        title=title,
                        description=description,
                        details=details,
                    )
                    if created:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            except Exception as e:
                logger.warning('Could not create finding for track %s: %s', track_id, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)

        # Signal 4 (#1567): multi-track poisoned batches. The single-track
        # signals above never see an album that swallowed several unrelated
        # tracks whole (11 tracks filed as one bogus "Music"/Nickelback
        # album), so it gets its own pass.
        if not context.check_stop():
            self._scan_multi_track_albums(context, result, settings)

        if context.report_progress:
            context.report_progress(
                phase='Done',
                log_line=(f'Found {result.findings_created} suspect album tag(s)'),
                log_type='success' if result.findings_created == 0 else 'warning',
            )

        return result

    # ------------------------------------------------------------------ #
    def _scan_multi_track_albums(self, context: JobContext, result: JobResult, settings: dict) -> None:
        """Signal 4 (#1567): flag multi-track albums that look like a poisoned
        batch — >1 locally-owned tracks, >= ``min_multi_track_distinct_artists``
        distinct track artists, AND (duplicate (disc_number, track_number)
        pairs OR the album artist is not among the track artists). Duplicate
        counting is per-disc so genuine multi-disc comps stay quiet. Genuine
        Various Artists compilations are exempt, as is the artist-mismatch
        half on albums with a real shared provider album id (curator/DJ
        credit is legitimate there — M3 decision). Emits one album-level
        finding per suspect album; the fix stays the per-track Re-identify
        action.
        """
        min_artists = int(settings.get('min_multi_track_distinct_artists', 3) or 3)

        if context.report_progress:
            context.report_progress(phase='Checking multi-track albums for poisoned batches…',
                                    log_line='Loading multi-track albums…', log_type='info')

        conn = context.db._get_connection()
        try:
            cur = conn.cursor()
            album_not_locked = not_locked_sql(cur, 'albums', 'al')
            # The provider album-id columns are migration-added; on a schema
            # that predates them the curator-credit exemption below simply
            # doesn't apply (fail-open toward flagging, the old behavior).
            # Tolerant of dict-shaped test doubles as well as sqlite3.Row.
            try:
                cur.execute("PRAGMA table_info(albums)")
                _album_cols = set()
                for _prow in cur.fetchall():
                    if isinstance(_prow, dict):
                        _album_cols.add(_prow.get("name"))
                    else:
                        _album_cols.add(_prow[1])
            except Exception:  # noqa: BLE001 — fail open, no exemption
                _album_cols = set()
            _has_spotify_id = 'spotify_album_id' in _album_cols
            _has_itunes_id = 'itunes_album_id' in _album_cols
            _sp_id_col = 'al.spotify_album_id' if _has_spotify_id else "''"
            _it_id_col = 'al.itunes_album_id' if _has_itunes_id else "''"
            # R4F2: bundle-flow imports stamp every track with the BATCH
            # artist_id, so counting distinct artists via tar.name can never
            # see a poisoned batch (all rows join to the one batch artist —
            # the real #1567 DB shape). The per-track truth is
            # tracks.track_artist — prefer it, falling back to the joined
            # artist name when empty. On a schema predating the column,
            # fall back to the joined name (old behavior).
            try:
                cur.execute("PRAGMA table_info(tracks)")
                _track_cols = set()
                for _trow in cur.fetchall():
                    if isinstance(_trow, dict):
                        _track_cols.add(_trow.get("name"))
                    else:
                        _track_cols.add(_trow[1])
            except Exception:  # noqa: BLE001 — fail open, old behavior
                _track_cols = set()
            if 'track_artist' in _track_cols:
                _eff_artist = ("COALESCE(NULLIF(TRIM(t.track_artist), ''), "
                               "TRIM(COALESCE(tar.name, '')))")
            else:
                _eff_artist = "TRIM(COALESCE(tar.name, ''))"
            # One row per album with >1 locally-owned track. The HAVING
            # clause does the >= N distinct-artists gate in SQL so huge
            # libraries only ship candidate albums back to Python.
            # Duplicate track numbers are counted per (disc_number, track_number):
            # a genuine multi-disc comp legitimately repeats track numbers
            # across discs (#1567 M3) — only repeats WITHIN a disc are the
            # poisoned-batch fingerprint. Rows with NULL disc_number are
            # excluded from the duplicate count: an unknown disc is not
            # provably the same disc as another (MINOR 1, #1567 round 3).
            cur.execute(f"""
                SELECT
                    al.id          AS album_id,
                    al.title       AS album_title,
                    al.record_type AS album_type,
                    al.track_count AS full_track_count,
                    {_sp_id_col}    AS spotify_album_id,
                    {_it_id_col}    AS itunes_album_id,
                    COALESCE(aar.name, '') AS album_artist_name,
                    COUNT(t.id)    AS local_track_count,
                    COUNT(DISTINCT CASE WHEN {_eff_artist} != ''
                                    THEN LOWER({_eff_artist}) END) AS distinct_artists,
                    GROUP_CONCAT(DISTINCT {_eff_artist}) AS artist_names,
                    SUM(CASE WHEN t.track_number IS NOT NULL THEN 1 ELSE 0 END) AS numbered_tracks,
                    -- MINOR 1 (#1567 round 3): NULL disc numbers cannot prove
                    -- two tracks share a disc — a legit multi-disc rip that
                    -- never set disc_number would collapse all its discs
                    -- into one (COALESCE(t.disc_number, 1)) and false-positive
                    -- the duplicate fingerprint. Only rows with an explicit
                    -- disc participate in the duplicate count; NULL-disc rows
                    -- simply don't count as duplicates.
                    COUNT(DISTINCT CASE WHEN t.track_number IS NOT NULL
                                         AND t.disc_number IS NOT NULL
                                    THEN t.disc_number || ':' || t.track_number
                                    END) AS distinct_numbers,
                    SUM(CASE WHEN t.track_number IS NOT NULL
                              AND t.disc_number IS NOT NULL
                             THEN 1 ELSE 0 END) AS numbered_for_dup_check,
                    SUM(CASE WHEN {_eff_artist} != ''
                              AND LOWER({_eff_artist}) = LOWER(TRIM(COALESCE(aar.name, '')))
                             THEN 1 ELSE 0 END) AS album_artist_track_hits
                FROM albums al
                JOIN tracks  t  ON t.album_id = al.id
                LEFT JOIN artists tar ON tar.id = t.artist_id
                LEFT JOIN artists aar ON aar.id = al.artist_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  {album_not_locked}
                GROUP BY al.id
                HAVING COUNT(t.id) > 1
                   AND COUNT(DISTINCT CASE WHEN {_eff_artist} != ''
                                       THEN LOWER({_eff_artist}) END) >= ?
                ORDER BY al.title
                LIMIT 2000
            """, (min_artists,))
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

        total = len(rows)
        if total >= 2000:
            # The SQL LIMIT truncated the candidate set — a library with
            # more than 2000 suspect multi-track albums won't be fully
            # scanned this run. Loud so it doesn't look like a clean bill.
            logger.warning(
                "[Suspect Album Tags] candidate list hit the 2000-album SQL "
                "LIMIT — results are truncated; re-run after fixing the "
                "flagged albums to scan the rest.")
        if context.report_progress:
            context.report_progress(phase=f'Checking {total} multi-track album(s)…',
                                    total=total, log_line=f'{total} candidates',
                                    log_type='info')

        for i, row in enumerate(rows):
            if context.check_stop():
                break
            if i % 50 == 0 and context.wait_if_paused():
                break

            result.scanned += 1
            if i % 50 == 0 and context.update_progress:
                context.update_progress(i, total)

            album_id      = row.get('album_id')
            album_title   = (row.get('album_title') or '').strip()
            album_artist  = (row.get('album_artist_name') or '').strip()
            local_count   = row.get('local_track_count') or 0
            distinct      = row.get('distinct_artists') or 0
            artist_names  = (row.get('artist_names') or '').strip()
            full_count    = row.get('full_track_count') or 0

            if (row.get('album_type') or '').lower() == 'single':
                result.skipped += 1
                continue
            # Genuine multi-artist compilations carry a Various Artists
            # credit — the batch that poisoned #1567 claimed one real band.
            if _is_various_artist(album_artist):
                result.skipped += 1
                continue
            if distinct < min_artists:
                result.skipped += 1
                continue

            reasons = []
            # Duplicate (disc, track) numbers, ignoring NULL track numbers
            # (NULLs are "unknown", not duplicates — several untagged tracks
            # must not trip this) and NULL disc numbers (an unknown disc is
            # not provably the same disc as another — MINOR 1, #1567 round
            # 3: legit multi-disc rips that never set disc_number used to
            # collapse into one disc and false-positive this). Repeats ACROSS
            # discs are legitimate (multi-disc comps); only repeats within one
            # explicitly-numbered disc trip this. numbered_for_dup_check
            # counts the same population distinct_numbers measures (rows with
            # both set); older/hand-built rows without the column fall back to
            # numbered_tracks.
            numbered = row.get('numbered_tracks') or 0
            distinct_nums = row.get('distinct_numbers') or 0
            numbered_on_discs = row.get('numbered_for_dup_check')
            if numbered_on_discs is None:
                numbered_on_discs = numbered
            if numbered_on_discs and numbered_on_discs > distinct_nums:
                reasons.append(
                    f'duplicate track numbers ({numbered_on_discs} numbered tracks '
                    f'share {distinct_nums} distinct numbers)')
            # Album artist not among the track artists. An empty album
            # artist is its own reason — with 3+ distinct track artists and
            # no album credit, the album identity is unanchored.
            #
            # Curator-credit exemption — M3 decision, documented: a foreign
            # album artist is EXPECTED on genuine curator/DJ/label-credited
            # compilations, so the mismatch alone is not a poison signal when
            # the album carries a REAL shared provider album id (a poisoned
            # batch has no trustworthy shared identity — #1567's had none).
            # Pipeline placeholder ids ('wishlist_album', '_name_*', ...) do
            # NOT count as real. The duplicate-numbers signal still fires on
            # real-id albums: number collisions within a disc stay suspicious
            # regardless of the credit.
            hits = row.get('album_artist_track_hits') or 0
            has_real_provider_id = is_real_provider_album_id(
                row.get('spotify_album_id')) or is_real_provider_album_id(
                row.get('itunes_album_id'))
            if album_artist:
                if hits == 0 and not has_real_provider_id:
                    reasons.append(
                        f'album artist "{album_artist}" is not among the '
                        f'{distinct} track artists')
            else:
                reasons.append(
                    f'no album artist set for {distinct} distinct track artists')

            if not reasons:
                result.skipped += 1
                continue

            reason_text = '; '.join(reasons)
            shown_artists = artist_names[:160] + ('…' if len(artist_names) > 160 else '')
            title = f'Suspect album: "{album_title}" may be a poisoned batch'
            description = (
                f'Album "{album_title}" holds {local_count} locally-owned tracks by '
                f'{distinct} distinct artists ({shown_artists}) — {reason_text}. '
                f'The tracks may have been filed under the wrong album by a bad batch.'
            )
            details = {
                'album_id': album_id,
                'album_title': album_title,
                'album_artist_name': album_artist,
                'local_track_count': local_count,
                'distinct_artists': distinct,
                'artist_names': artist_names,
                'reasons': reasons,
                'full_track_count': full_count,
                # Pre-fill the Re-identify search query
                'reidentify_query': f'{album_title} {album_artist}'.strip(),
            }

            try:
                if context.create_finding:
                    created = context.create_finding(
                        job_id=self.job_id,
                        finding_type='suspect_album_tag',
                        severity='warning',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=title,
                        description=description,
                        details=details,
                    )
                    if created:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
            except Exception as e:
                logger.warning('Could not create finding for album %s: %s', album_id, e)
                result.errors += 1

        if context.update_progress:
            context.update_progress(total, total)
