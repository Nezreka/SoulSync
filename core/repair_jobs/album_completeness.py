"""Album Completeness Checker Job — finds albums missing tracks."""

import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

from core.metadata_service import (
    get_album_tracks_for_source,
    get_primary_source,
    get_source_priority,
)
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from core.worker_utils import set_album_api_track_count
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_complete")


@register_job
class AlbumCompletenessJob(RepairJob):
    job_id = 'album_completeness'
    display_name = 'Album Completeness'
    description = 'Checks if all tracks from albums are present'
    help_text = (
        'Compares the number of tracks you have for each album against the expected total '
        'from your configured metadata sources. Counts cached during normal enrichment are '
        'used when no canonical edition is pinned; otherwise the exact canonical source and '
        'album ID are queried directly. The same canonical tracklist is used for both the '
        'expected total and the missing-track calculation. Fragmented library rows are '
        'combined only when they safely match that same canonical edition. Albums where '
        'tracks are missing get flagged as findings with details about which tracks are '
        'absent.\n\n'
        'Useful for catching partial downloads or albums where some tracks failed to download. '
        'You can use the Download Missing feature from the album page to fill gaps.\n\n'
        'Settings:\n'
        '- Min Tracks For Check: Only check albums with at least this many expected tracks '
        '(skips singles and EPs)\n'
        '- Min Completion %: Only flag albums where you already have at least this percentage '
        'of tracks (e.g. 30% skips albums where you only have 1 track from a playlist import, '
        'but catches albums where a download partially failed)'
    )
    icon = 'repair-icon-completeness'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'min_tracks_for_check': 3,
        'min_completion_pct': 0,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        settings = self._get_settings(context)
        min_tracks = settings.get('min_tracks_for_check', 3)
        min_completion_pct = settings.get('min_completion_pct', 0)
        primary_source = self._get_primary_source()

        # Fetch all albums with ANY external source ID — not just Spotify.
        albums = []
        conn = None
        has_itunes = False
        has_deezer = False
        has_discogs = False
        has_hydrabase = False
        has_musicbrainz = False
        has_api_track_count = False
        has_canonical = False
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            # Check which source columns exist (older DBs may lack some).
            cursor.execute("PRAGMA table_info(albums)")
            columns = {row[1] for row in cursor.fetchall()}
            has_itunes = 'itunes_album_id' in columns
            has_deezer = 'deezer_id' in columns
            has_discogs = 'discogs_id' in columns
            has_hydrabase = 'soul_id' in columns
            has_musicbrainz = 'musicbrainz_release_id' in columns
            has_canonical = (
                'canonical_source' in columns
                and 'canonical_album_id' in columns
            )

            # Detect the `api_track_count` column — older DBs may not have it
            # yet (migration runs on app start, but repair-job code mustn't
            # assume it's present). When absent, fall back to the pre-column
            # behavior: look up expected total via API every scan, don't try
            # to persist it.
            has_api_track_count = 'api_track_count' in columns

            # Build SELECT with available source ID columns.
            # NOTE: `al.track_count` is deliberately NOT selected. That
            # column holds the OBSERVED track count written by server syncs
            # (Plex leafCount, SoulSync standalone len(tracks)) — always
            # equal to COUNT(t.id), so it's worthless for completeness.
            # The expected total comes from `al.api_track_count` (cached
            # from metadata-source enrichment) or a live API lookup.
            select_cols = [
                ('al.id', 'album_id'),
                ('al.artist_id', 'artist_id'),
                ('al.title', 'album_title'),
                ('ar.name', 'artist_name'),
                ('al.spotify_album_id', 'spotify_album_id'),
                ('COUNT(t.id)', 'actual_count'),
                ('al.thumb_url', 'album_thumb_url'),
                ('ar.thumb_url', 'artist_thumb_url'),
            ]
            if has_api_track_count:
                select_cols.append(('al.api_track_count', 'api_track_count'))
            if has_itunes:
                select_cols.append(('al.itunes_album_id', 'itunes_album_id'))
            if has_deezer:
                select_cols.append(('al.deezer_id', 'deezer_album_id'))
            if has_discogs:
                select_cols.append(('al.discogs_id', 'discogs_album_id'))
            if has_hydrabase:
                select_cols.append(('al.soul_id', 'hydrabase_album_id'))
            if has_musicbrainz:
                select_cols.append(
                    ('al.musicbrainz_release_id', 'musicbrainz_album_id')
                )
            if has_canonical:
                select_cols.extend([
                    ('al.canonical_source', 'canonical_source'),
                    ('al.canonical_album_id', 'canonical_album_id'),
                ])

            # WHERE: album has at least one source ID or a complete canonical pair.
            where_parts = [
                "(al.spotify_album_id IS NOT NULL AND al.spotify_album_id != '')",
            ]
            if has_itunes:
                where_parts.append(
                    "(al.itunes_album_id IS NOT NULL AND al.itunes_album_id != '')"
                )
            if has_deezer:
                where_parts.append(
                    "(al.deezer_id IS NOT NULL AND al.deezer_id != '')"
                )
            if has_discogs:
                where_parts.append(
                    "(al.discogs_id IS NOT NULL AND al.discogs_id != '')"
                )
            if has_hydrabase:
                where_parts.append(
                    "(al.soul_id IS NOT NULL AND al.soul_id != '')"
                )
            if has_musicbrainz:
                where_parts.append(
                    "(al.musicbrainz_release_id IS NOT NULL "
                    "AND al.musicbrainz_release_id != '')"
                )
            if has_canonical:
                where_parts.append(
                    "(al.canonical_source IS NOT NULL AND al.canonical_source != '' "
                    "AND al.canonical_album_id IS NOT NULL AND al.canonical_album_id != '')"
                )
            where_clause = ' OR '.join(where_parts)

            select_sql = ', '.join(
                f'{expr} AS {alias}'
                for expr, alias in select_cols
            )
            cursor.execute(f"""
                SELECT {select_sql}
                FROM albums al
                LEFT JOIN artists ar ON ar.id = al.artist_id
                LEFT JOIN tracks t ON t.album_id = al.id
                WHERE {where_clause}
                GROUP BY al.id
            """)
            raw_rows = cursor.fetchall()
            column_index = {
                alias: idx
                for idx, (_, alias) in enumerate(select_cols)
            }
            albums = [
                {
                    alias: row[idx]
                    for alias, idx in column_index.items()
                }
                for row in raw_rows
            ]
            for order, album in enumerate(albums):
                album['_scan_order'] = order
        except Exception as e:
            logger.error("Error fetching albums: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        # Rows that share a source ID with a canonical album are candidates for
        # the same release. A sibling joins the logical album only when at least
        # one of its local tracks strictly matches the canonical tracklist.
        work_items = self._prepare_work_items(context, albums)

        total = len(work_items)
        if context.update_progress:
            context.update_progress(0, total)

        logger.info("Checking completeness of %d logical albums", total)

        if context.report_progress:
            context.report_progress(
                phase=f'Checking {total} logical albums...',
                total=total,
            )

        for i, work_item in enumerate(work_items):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            row = work_item['row']
            album_id = row['album_id']
            title = row['album_title']
            artist_name = row['artist_name']
            spotify_album_id = row['spotify_album_id']
            actual_count = int(row['actual_count'] or 0)
            album_thumb = row['album_thumb_url']
            artist_thumb = row['artist_thumb_url']
            itunes_album_id = row.get('itunes_album_id')
            deezer_album_id = row.get('deezer_album_id')
            discogs_album_id = row.get('discogs_album_id')
            hydrabase_album_id = row.get('hydrabase_album_id')
            musicbrainz_album_id = row.get('musicbrainz_album_id')
            canonical_source = row.get('canonical_source')
            canonical_album_id = row.get('canonical_album_id')
            cached_api_count = row.get('api_track_count')

            result.scanned += 1

            if context.report_progress:
                context.report_progress(
                    scanned=i + 1,
                    total=total,
                    phase=f'Checking {i + 1} / {total}',
                    log_line=(
                        f'Album: {title or "Unknown"} — '
                        f'{artist_name or "Unknown"}'
                    ),
                    log_type='info',
                )

            album_ids = self._source_ids_from_row(row)
            resolved_source = primary_source
            resolved_album_id = (
                self._get_album_id_for_source(primary_source, album_ids)
                or ''
            )
            related_album_ids = (
                work_item.get('related_album_ids')
                or [album_id]
            )
            raw_local_count = work_item.get('raw_local_count')
            missing_tracks = None

            # A complete canonical pair is authoritative. `_prepare_work_items`
            # performs exactly one lookup per canonical group and stores the
            # resulting list — including an empty list when the lookup failed.
            if canonical_source and canonical_album_id:
                canonical_items = work_item.get('canonical_items', [])
                expected_total = len(canonical_items)
                resolved_source = str(canonical_source)
                resolved_album_id = str(canonical_album_id)

                if canonical_items:
                    actual_count = int(
                        work_item.get(
                            'effective_actual_count',
                            actual_count,
                        )
                    )
                    owned_reference_indexes = set(
                        work_item.get(
                            'owned_reference_indexes',
                            set(),
                        )
                    )
                    missing_tracks = self._build_missing_tracks(
                        canonical_items,
                        owned_reference_indexes,
                        resolved_source,
                    )
                    if raw_local_count is None:
                        raw_local_count = actual_count
            else:
                # Preserve the existing behavior for albums without a pinned
                # canonical edition.
                expected_total = cached_api_count
                if not expected_total:
                    expected_total = self._get_expected_total(
                        context,
                        primary_source,
                        album_ids,
                    )
                    if (
                        expected_total
                        and expected_total > 0
                        and has_api_track_count
                    ):
                        self._save_api_track_count(
                            context,
                            album_id,
                            expected_total,
                        )

            # Skip singles/EPs based on expected track count (not local count).
            if expected_total and expected_total < min_tracks:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            if not expected_total or actual_count >= expected_total:
                result.skipped += 1
                if context.update_progress and (i + 1) % 5 == 0:
                    context.update_progress(i + 1, total)
                continue

            effective_raw_local_count = (
                raw_local_count
                if raw_local_count is not None
                else int(row['actual_count'] or 0)
            )
            if actual_count == 0 and effective_raw_local_count == 0:
                result.skipped += 1
                continue

            if min_completion_pct > 0 and expected_total > 0:
                completion = (actual_count / expected_total) * 100
                if completion < min_completion_pct:
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 5 == 0:
                        context.update_progress(i + 1, total)
                    continue

            if missing_tracks is None:
                missing_tracks = self._find_missing_tracks(
                    context,
                    primary_source,
                    album_id,
                    album_ids,
                )

            if context.report_progress:
                context.report_progress(
                    log_line=(
                        f'Incomplete: {title or "Unknown"} '
                        f'({actual_count}/{expected_total})'
                    ),
                    log_type='skip',
                )

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='incomplete_album',
                        severity='info',
                        entity_type='album',
                        entity_id=str(album_id),
                        file_path=None,
                        title=(
                            f'Incomplete: {title or "Unknown"} '
                            f'({actual_count}/{expected_total})'
                        ),
                        description=(
                            f'Album "{title}" by {artist_name or "Unknown"} '
                            f'has {actual_count} of {expected_total} tracks'
                        ),
                        details={
                            'album_id': album_id,
                            'album_title': title,
                            'artist': artist_name,
                            'primary_source': resolved_source,
                            'primary_album_id': resolved_album_id,
                            'canonical_source': canonical_source or '',
                            'canonical_album_id': canonical_album_id or '',
                            'spotify_album_id': spotify_album_id or '',
                            'itunes_album_id': itunes_album_id or '',
                            'deezer_album_id': deezer_album_id or '',
                            'discogs_album_id': discogs_album_id or '',
                            'hydrabase_album_id': hydrabase_album_id or '',
                            'musicbrainz_album_id': (
                                musicbrainz_album_id or ''
                            ),
                            'expected_tracks': expected_total,
                            'actual_tracks': actual_count,
                            'raw_local_tracks': effective_raw_local_count,
                            'related_album_ids': [
                                str(value)
                                for value in related_album_ids
                            ],
                            'missing_tracks': missing_tracks,
                            'album_thumb_url': album_thumb or None,
                            'artist_thumb_url': artist_thumb or None,
                        },
                    )
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug(
                        "Error creating completeness finding for album %s: %s",
                        album_id,
                        e,
                    )
                    result.errors += 1

            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)

        logger.info(
            "Completeness check: %d logical albums checked, %d incomplete found",
            result.scanned,
            result.findings_created,
        )
        return result

    def _prepare_work_items(self, context, albums):
        """Collapse safely validated fragments into logical canonical albums."""
        groups = self._build_candidate_groups(albums)
        work_items = []

        for group in groups:
            # This prep phase front-loads the canonical API lookups + track
            # matching, so honour Stop/Pause here too — otherwise a stop would
            # be ignored until every group is processed.
            check_stop = getattr(context, 'check_stop', None)
            if callable(check_stop) and check_stop():
                break

            anchor = group['anchor']
            members = group['members']
            canonical_source = anchor.get('canonical_source') or ''
            canonical_album_id = anchor.get('canonical_album_id') or ''

            if canonical_source and canonical_album_id:
                canonical_tracks = self._get_album_tracks(
                    str(canonical_source),
                    str(canonical_album_id),
                )
                canonical_items = self._extract_track_items(
                    canonical_tracks
                )

                if canonical_items:
                    local_by_album = {
                        str(member['album_id']): self._load_local_tracks(
                            context,
                            [member['album_id']],
                        )
                        for member in members
                    }
                    anchor_tracks = local_by_album.get(
                        str(anchor['album_id']),
                        [],
                    )

                    # The anchor's persisted disc/track slots remain
                    # authoritative even when titles or durations drift.
                    anchor_reference = self._owned_reference_for_tracks(
                        canonical_items,
                        anchor_tracks,
                        str(canonical_source),
                    )

                    included = [anchor]
                    excluded = []
                    supplemental_reference = set()

                    for member in members:
                        if member is anchor:
                            continue

                        member_tracks = local_by_album.get(
                            str(member['album_id']),
                            [],
                        )
                        matched_reference, _ = (
                            self._match_fragment_tracks(
                                canonical_items,
                                member_tracks,
                                str(canonical_source),
                            )
                        )
                        if matched_reference:
                            included.append(member)
                            supplemental_reference.update(
                                matched_reference - anchor_reference
                            )
                        else:
                            excluded.append(member)

                    combined_local = []
                    for member in included:
                        combined_local.extend(
                            local_by_album.get(
                                str(member['album_id']),
                                [],
                            )
                        )

                    owned_reference_indexes = (
                        anchor_reference | supplemental_reference
                    )
                    work_items.append({
                        'row': anchor,
                        'canonical_items': canonical_items,
                        'related_album_ids': [
                            member['album_id']
                            for member in included
                        ],
                        'raw_local_count': len(combined_local),
                        'effective_actual_count': (
                            int(anchor.get('actual_count') or 0)
                            + len(supplemental_reference)
                        ),
                        'owned_reference_indexes': (
                            owned_reference_indexes
                        ),
                        '_scan_order': min(
                            member['_scan_order']
                            for member in included
                        ),
                    })

                    for member in excluded:
                        # An excluded member that is itself pinned to this
                        # canonical edition is still evaluated against it (no
                        # fallback). It must report only the tracks it *doesn't*
                        # own — compute its own owned slots from its local
                        # tracks, exactly like the anchor, instead of leaving
                        # the set empty (which would flag the whole tracklist as
                        # missing, including tracks the row already has).
                        if self._same_canonical_pair(
                            member,
                            canonical_source,
                            canonical_album_id,
                        ):
                            member_tracks = local_by_album.get(
                                str(member['album_id']),
                                [],
                            )
                            member_owned = (
                                self._owned_reference_for_tracks(
                                    canonical_items,
                                    member_tracks,
                                    str(canonical_source),
                                )
                            )
                            work_items.append(
                                self._independent_work_item(
                                    member,
                                    canonical_items=canonical_items,
                                    owned_reference_indexes=member_owned,
                                    effective_actual_count=len(
                                        member_owned
                                    ),
                                )
                            )
                        else:
                            work_items.append(
                                self._independent_work_item(member)
                            )
                    continue

                # The canonical lookup was attempted and returned no usable
                # tracklist. Canonical rows must not fall back to another
                # provider; non-canonical siblings remain independent.
                for member in members:
                    work_items.append(
                        self._independent_work_item(
                            member,
                            canonical_items=(
                                []
                                if self._same_canonical_pair(
                                    member,
                                    canonical_source,
                                    canonical_album_id,
                                )
                                else None
                            ),
                        )
                    )
                continue

            for member in members:
                work_items.append(
                    self._independent_work_item(member)
                )

        work_items.sort(key=lambda item: item['_scan_order'])
        return work_items

    def _independent_work_item(
        self,
        row,
        canonical_items=None,
        owned_reference_indexes=None,
        effective_actual_count=None,
    ):
        item = {
            'row': row,
            'related_album_ids': [row['album_id']],
            '_scan_order': row['_scan_order'],
        }
        if canonical_items is not None:
            item['canonical_items'] = canonical_items
        if owned_reference_indexes is not None:
            item['owned_reference_indexes'] = owned_reference_indexes
        if effective_actual_count is not None:
            item['effective_actual_count'] = effective_actual_count
        return item

    def _same_canonical_pair(
        self,
        row,
        canonical_source,
        canonical_album_id,
    ):
        return (
            str(row.get('canonical_source') or '')
            == str(canonical_source)
            and str(row.get('canonical_album_id') or '')
            == str(canonical_album_id)
        )

    def _build_candidate_groups(self, albums):
        """Group non-canonical rows around unambiguous canonical anchors."""
        canonical_groups = defaultdict(list)

        for album in albums:
            source = album.get('canonical_source') or ''
            canonical_id = album.get('canonical_album_id') or ''
            if source and canonical_id:
                key = (
                    str(album.get('artist_id') or ''),
                    str(source),
                    str(canonical_id),
                )
                canonical_groups[key].append(album)

        canonical_row_ids = {
            str(row['album_id'])
            for rows in canonical_groups.values()
            for row in rows
        }

        alias_to_groups = defaultdict(set)
        for key, anchor_rows in canonical_groups.items():
            for row in anchor_rows:
                artist_id = str(row.get('artist_id') or '')
                for source, source_id in (
                    self._source_ids_from_row(row).items()
                ):
                    if source_id:
                        alias_to_groups[
                            (artist_id, source, str(source_id))
                        ].add(key)

                canonical_source = (
                    row.get('canonical_source') or ''
                )
                canonical_id = (
                    row.get('canonical_album_id') or ''
                )
                alias_to_groups[
                    (
                        artist_id,
                        str(canonical_source),
                        str(canonical_id),
                    )
                ].add(key)

        # Assign each non-canonical row to a group in ONE pass over the albums
        # (O(N)), instead of rescanning every album for every group (O(G*N),
        # which degrades badly once many editions are pinned). A row joins a
        # group only when its stored IDs resolve to exactly one canonical group
        # — identical to the old `matches == {key}` rule, just computed once.
        assigned_candidate_ids = set()
        candidates_by_group = defaultdict(list)
        for row in albums:
            row_id = str(row['album_id'])
            if row_id in canonical_row_ids:
                continue

            artist_id = str(row.get('artist_id') or '')
            matches = set()
            for source, source_id in (
                self._source_ids_from_row(row).items()
            ):
                if source_id:
                    matches.update(
                        alias_to_groups.get(
                            (artist_id, source, str(source_id)),
                            set(),
                        )
                    )

            if len(matches) == 1:
                (key,) = tuple(matches)
                candidates_by_group[key].append(row)
                assigned_candidate_ids.add(row_id)

        groups = []
        for key, anchor_rows in canonical_groups.items():
            anchor = max(
                anchor_rows,
                key=lambda row: (
                    int(row.get('actual_count') or 0),
                    1 if row.get('album_title') else 0,
                    -int(row.get('_scan_order') or 0),
                ),
            )
            members = list(anchor_rows) + candidates_by_group.get(key, [])

            groups.append({
                'anchor': anchor,
                'members': sorted(
                    members,
                    key=lambda row: row['_scan_order'],
                ),
                '_scan_order': min(
                    row['_scan_order']
                    for row in members
                ),
            })

        grouped_ids = canonical_row_ids | assigned_candidate_ids
        for row in albums:
            if str(row['album_id']) in grouped_ids:
                continue
            groups.append({
                'anchor': row,
                'members': [row],
                '_scan_order': row['_scan_order'],
            })

        groups.sort(key=lambda group: group['_scan_order'])
        return groups

    def _source_ids_from_row(self, row):
        """Return every stored release ID available on an album row."""
        return {
            'spotify': row.get('spotify_album_id') or '',
            'itunes': row.get('itunes_album_id') or '',
            'deezer': row.get('deezer_album_id') or '',
            'discogs': row.get('discogs_album_id') or '',
            'hydrabase': row.get('hydrabase_album_id') or '',
            'musicbrainz': row.get('musicbrainz_album_id') or '',
        }

    def _load_local_tracks(self, context, album_ids):
        """Load local tracks while tolerating older track-table schemas."""
        ids = [
            str(album_id)
            for album_id in album_ids
            if album_id is not None
        ]
        if not ids:
            return []

        placeholders = ','.join('?' for _ in ids)
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tracks)")
            columns = {
                row[1]
                for row in cursor.fetchall()
            }

            select_cols = [
                (
                    'id' if 'id' in columns else 'NULL',
                    'id',
                ),
                ('album_id', 'album_id'),
                (
                    'title' if 'title' in columns else "''",
                    'title',
                ),
                (
                    'track_number'
                    if 'track_number' in columns
                    else 'NULL',
                    'track_number',
                ),
                (
                    'disc_number'
                    if 'disc_number' in columns
                    else '1',
                    'disc_number',
                ),
                (
                    'duration'
                    if 'duration' in columns
                    else '0',
                    'duration',
                ),
                (
                    'musicbrainz_recording_id'
                    if 'musicbrainz_recording_id' in columns
                    else "''",
                    'musicbrainz_recording_id',
                ),
            ]
            select_sql = ', '.join(
                f'{expression} AS {alias}'
                for expression, alias in select_cols
            )
            cursor.execute(
                f"""
                    SELECT {select_sql}
                    FROM tracks
                    WHERE album_id IN ({placeholders})
                """,
                ids,
            )
            return [
                {
                    'id': row[0],
                    'album_id': row[1],
                    'title': row[2] or '',
                    'track_number': row[3],
                    'disc_number': (
                        row[4]
                        if row[4] is not None
                        else 1
                    ),
                    'duration_ms': row[5] or 0,
                    'musicbrainz_recording_id': row[6] or '',
                }
                for row in cursor.fetchall()
            ]
        except Exception as e:
            logger.debug(
                "Failed loading local tracks for albums %s: %s",
                ids,
                e,
            )
            return []
        finally:
            if conn:
                conn.close()

    def _normalize_title(self, value):
        text = unicodedata.normalize('NFKD', str(value or ''))
        text = ''.join(
            char
            for char in text
            if not unicodedata.combining(char)
        )
        text = text.casefold()
        text = re.sub(r'[\W_]+', ' ', text, flags=re.UNICODE)
        return ' '.join(text.split())

    def _as_int(self, value, default=None):
        if value is None or value == '':
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        match = re.search(r'\d+', str(value))
        return int(match.group(0)) if match else default

    def _reference_duration_ms(self, item):
        value = item.get('duration_ms')
        if value not in (None, ''):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        value = item.get('duration')
        if value not in (None, ''):
            try:
                return int(float(value) * 1000)
            except (TypeError, ValueError):
                return 0
        return 0

    def _reference_slots_for_local_tracks(
        self,
        reference_items,
        local_tracks,
    ):
        """Map unambiguous local disc/track slots to reference indexes."""
        slots = defaultdict(list)
        for index, item in enumerate(reference_items):
            number = self._as_int(item.get('track_number'))
            disc = self._as_int(
                item.get('disc_number'),
                1,
            )
            if number is not None:
                slots[(disc, number)].append(index)

        matched = set()
        for track in local_tracks:
            number = self._as_int(track.get('track_number'))
            disc = self._as_int(
                track.get('disc_number'),
                1,
            )
            indexes = slots.get((disc, number), [])
            if len(indexes) == 1:
                matched.add(indexes[0])
        return matched

    def _owned_reference_for_tracks(
        self,
        reference_items,
        local_tracks,
        reference_source,
    ):
        """Reference indexes a set of local tracks owns: fuzzy one-to-one
        matches plus the persisted disc/track slots (authoritative even when
        titles or durations drift). Used for the anchor and for any excluded
        sibling that is still evaluated against this canonical edition."""
        owned, _ = self._match_tracks(
            reference_items,
            local_tracks,
            reference_source,
        )
        owned.update(
            self._reference_slots_for_local_tracks(
                reference_items,
                local_tracks,
            )
        )
        return owned

    def _track_match_score(
        self,
        reference,
        local,
        reference_source,
    ):
        """Return a score when a local track plausibly matches a reference."""
        reference_id = str(reference.get('id') or '')
        local_mbid = str(
            local.get('musicbrainz_recording_id') or ''
        )
        if (
            reference_source == 'musicbrainz'
            and reference_id
            and reference_id == local_mbid
        ):
            return 1000

        reference_number = self._as_int(
            reference.get('track_number')
        )
        local_number = self._as_int(
            local.get('track_number')
        )
        reference_disc = self._as_int(
            reference.get('disc_number'),
            1,
        )
        local_disc = self._as_int(
            local.get('disc_number'),
            1,
        )

        same_number = (
            reference_number is not None
            and local_number is not None
            and reference_number == local_number
        )
        same_slot = same_number and reference_disc == local_disc

        reference_title = self._normalize_title(
            reference.get('name')
            or reference.get('title')
        )
        local_title = self._normalize_title(
            local.get('title')
        )
        title_ratio = (
            SequenceMatcher(
                None,
                reference_title,
                local_title,
            ).ratio()
            if reference_title and local_title
            else 0.0
        )
        exact_title = bool(
            reference_title
            and reference_title == local_title
        )

        reference_duration = self._reference_duration_ms(
            reference
        )
        local_duration = (
            self._as_int(
                local.get('duration_ms'),
                0,
            )
            or 0
        )
        has_durations = (
            reference_duration > 0
            and local_duration > 0
        )
        duration_close = (
            has_durations
            and abs(
                reference_duration - local_duration
            ) <= 15000
        )

        if same_slot and title_ratio >= 0.65:
            return (
                800
                + int(title_ratio * 100)
                + (30 if duration_close else 0)
            )
        if same_slot and duration_close:
            return 740 + int(title_ratio * 100)
        if exact_title:
            return (
                700
                + (50 if duration_close else 0)
                + (20 if same_number else 0)
            )
        if (
            title_ratio >= 0.92
            and (duration_close or not has_durations)
        ):
            return 650 + int(title_ratio * 100)
        if (
            same_number
            and title_ratio >= 0.80
            and duration_close
        ):
            return 620 + int(title_ratio * 100)
        return None

    def _fragment_track_match_score(
        self,
        reference,
        local,
        reference_source,
    ):
        """Use stricter matching when accepting a sibling fragment."""
        reference_id = str(reference.get('id') or '')
        local_mbid = str(
            local.get('musicbrainz_recording_id') or ''
        )
        if (
            reference_source == 'musicbrainz'
            and reference_id
            and reference_id == local_mbid
        ):
            return 1000

        reference_title = self._normalize_title(
            reference.get('name')
            or reference.get('title')
        )
        local_title = self._normalize_title(
            local.get('title')
        )
        if not reference_title or not local_title:
            return None

        title_ratio = SequenceMatcher(
            None,
            reference_title,
            local_title,
        ).ratio()
        exact_title = reference_title == local_title

        reference_number = self._as_int(
            reference.get('track_number')
        )
        local_number = self._as_int(
            local.get('track_number')
        )
        reference_disc = self._as_int(
            reference.get('disc_number'),
            1,
        )
        local_disc = self._as_int(
            local.get('disc_number'),
            1,
        )
        same_slot = (
            reference_number is not None
            and local_number is not None
            and reference_number == local_number
            and reference_disc == local_disc
        )

        reference_duration = self._reference_duration_ms(
            reference
        )
        local_duration = (
            self._as_int(
                local.get('duration_ms'),
                0,
            )
            or 0
        )
        duration_close = (
            reference_duration > 0
            and local_duration > 0
            and abs(
                reference_duration - local_duration
            ) <= 15000
        )

        if exact_title:
            return (
                900
                + (30 if same_slot else 0)
                + (20 if duration_close else 0)
            )
        if (
            title_ratio >= 0.95
            and (same_slot or duration_close)
        ):
            return 800 + int(title_ratio * 100)
        if (
            title_ratio >= 0.85
            and same_slot
            and duration_close
        ):
            return 700 + int(title_ratio * 100)
        return None

    def _match_fragment_tracks(
        self,
        reference_items,
        local_tracks,
        reference_source,
    ):
        """One-to-one strict match used only for candidate siblings."""
        return self._greedy_match(
            reference_items,
            local_tracks,
            reference_source,
            self._fragment_track_match_score,
        )

    def _match_tracks(
        self,
        reference_items,
        local_tracks,
        reference_source,
    ):
        """One-to-one general match for tracks already on the anchor."""
        return self._greedy_match(
            reference_items,
            local_tracks,
            reference_source,
            self._track_match_score,
        )

    def _greedy_match(
        self,
        reference_items,
        local_tracks,
        reference_source,
        scorer,
    ):
        candidates = []
        for reference_index, reference in enumerate(
            reference_items
        ):
            for local_index, local in enumerate(local_tracks):
                score = scorer(
                    reference,
                    local,
                    reference_source,
                )
                if score is not None:
                    candidates.append(
                        (
                            score,
                            reference_index,
                            local_index,
                        )
                    )

        candidates.sort(reverse=True)
        matched_reference = set()
        matched_local = set()

        for _, reference_index, local_index in candidates:
            if (
                reference_index in matched_reference
                or local_index in matched_local
            ):
                continue
            matched_reference.add(reference_index)
            matched_local.add(local_index)

        return matched_reference, matched_local

    def _build_missing_tracks(
        self,
        reference_items,
        matched_reference,
        reference_source,
    ):
        missing_tracks = []
        for index, item in enumerate(reference_items):
            if index in matched_reference:
                continue

            track_artists = []
            for artist in item.get('artists', []):
                if isinstance(artist, dict):
                    track_artists.append(
                        artist.get('name', '')
                    )
                elif isinstance(artist, str):
                    track_artists.append(artist)

            source_track_id = item.get('id', '')
            missing_tracks.append({
                'track_number': item.get('track_number'),
                'name': (
                    item.get('name')
                    or item.get('title')
                    or ''
                ),
                'disc_number': item.get('disc_number', 1),
                'source': item.get(
                    '_source',
                    reference_source,
                ),
                'source_track_id': source_track_id,
                'track_id': source_track_id,
                'spotify_track_id': source_track_id,
                'duration_ms': self._reference_duration_ms(
                    item
                ),
                'artists': track_artists,
            })
        return missing_tracks

    def _save_api_track_count(self, context, album_id, count):
        """Persist a metadata-API track count via the shared worker helper."""
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            set_album_api_track_count(
                cursor,
                album_id,
                count,
            )
            conn.commit()
        except Exception as e:
            logger.debug(
                "Failed to cache api_track_count for album %s: %s",
                album_id,
                e,
            )
        finally:
            if conn:
                conn.close()

    def _get_expected_total(
        self,
        context,
        primary_source,
        album_ids,
    ):
        """Get the expected count from the active provider priority."""
        for source in get_source_priority(primary_source):
            album_id = self._get_album_id_for_source(
                source,
                album_ids,
            )
            if not album_id:
                continue
            api_tracks = self._get_album_tracks(
                source,
                album_id,
            )
            items = self._extract_track_items(api_tracks)
            if items:
                return len(items)
        return 0

    def _find_missing_tracks(
        self,
        context,
        primary_source,
        album_id,
        album_ids,
        resolved_source=None,
        resolved_items=None,
    ):
        """Identify missing tracks from one resolved metadata edition."""
        owned_numbers = set()
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT track_number FROM tracks "
                "WHERE album_id = ? "
                "AND track_number IS NOT NULL",
                (album_id,),
            )
            for track in cursor.fetchall():
                owned_numbers.add(track[0])
        except Exception:
            return []
        finally:
            if conn:
                conn.close()

        if resolved_items is None:
            api_tracks = None
            for source in get_source_priority(
                primary_source
            ):
                source_album_id = (
                    self._get_album_id_for_source(
                        source,
                        album_ids,
                    )
                )
                if not source_album_id:
                    continue
                api_tracks = self._get_album_tracks(
                    source,
                    source_album_id,
                )
                if self._extract_track_items(api_tracks):
                    resolved_source = source
                    break
            items = self._extract_track_items(api_tracks)
        else:
            items = resolved_items

        if not items:
            return []

        track_source = resolved_source or primary_source
        matched_reference = {
            index
            for index, item in enumerate(items)
            if (
                item.get('track_number')
                and item.get('track_number') in owned_numbers
            )
        }
        return self._build_missing_tracks(
            items,
            matched_reference,
            track_source,
        )

    def _get_settings(self, context: JobContext) -> dict:
        if not context.config_manager:
            return self.default_settings.copy()
        cfg = context.config_manager.get(
            f'repair.jobs.{self.job_id}.settings',
            {},
        )
        merged = self.default_settings.copy()
        merged.update(cfg)
        return merged

    def _get_primary_source(self) -> str:
        """Return the active metadata source for prioritization."""
        try:
            return get_primary_source()
        except Exception:
            return 'deezer'

    def _get_album_id_for_source(
        self,
        source: str,
        album_ids: dict,
    ) -> str:
        return album_ids.get(source, '')

    def _get_album_tracks(
        self,
        source: str,
        album_id: str,
    ):
        """Fetch album tracks from a specific source."""
        try:
            return get_album_tracks_for_source(
                source,
                album_id,
            )
        except Exception as e:
            logger.debug(
                "Error getting %s album tracks for %s: %s",
                source.capitalize(),
                album_id,
                e,
            )
            return None

    def _extract_track_items(self, api_tracks):
        """Normalize provider responses to a list of track dicts."""
        if not api_tracks:
            return []
        if isinstance(api_tracks, dict):
            items = (
                api_tracks.get('items')
                or api_tracks.get('tracks')
                or []
            )
            if isinstance(items, dict):
                items = items.get('items') or []
            return items if items else []
        if isinstance(api_tracks, list):
            return api_tracks
        return []

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()

            cursor.execute("PRAGMA table_info(albums)")
            columns = {
                row[1]
                for row in cursor.fetchall()
            }

            where_parts = [
                "(spotify_album_id IS NOT NULL "
                "AND spotify_album_id != '')",
            ]
            if 'itunes_album_id' in columns:
                where_parts.append(
                    "(itunes_album_id IS NOT NULL "
                    "AND itunes_album_id != '')"
                )
            if 'deezer_id' in columns:
                where_parts.append(
                    "(deezer_id IS NOT NULL "
                    "AND deezer_id != '')"
                )
            if 'discogs_id' in columns:
                where_parts.append(
                    "(discogs_id IS NOT NULL "
                    "AND discogs_id != '')"
                )
            if 'soul_id' in columns:
                where_parts.append(
                    "(soul_id IS NOT NULL "
                    "AND soul_id != '')"
                )
            if 'musicbrainz_release_id' in columns:
                where_parts.append(
                    "(musicbrainz_release_id IS NOT NULL "
                    "AND musicbrainz_release_id != '')"
                )
            if (
                'canonical_source' in columns
                and 'canonical_album_id' in columns
            ):
                where_parts.append(
                    "(canonical_source IS NOT NULL "
                    "AND canonical_source != '' "
                    "AND canonical_album_id IS NOT NULL "
                    "AND canonical_album_id != '')"
                )

            cursor.execute(f"""
                SELECT COUNT(*) FROM albums
                WHERE {' OR '.join(where_parts)}
            """)
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
