"""Folder-first audio identity audit feeding the album review queue.

Opt-in and read-only: the scan records evidence-backed proposals. Fix handlers
recheck the file before any tag, path, or wishlist write. No album is declared
complete from folder layout alone; missing slots need explicit intent.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from functools import partial

from core.auto_import_worker import _read_file_tags
from core.imports.staging import AUDIO_EXTENSIONS
from core.library.duplicate_keep import pick_duplicate_to_keep
from core.library.duplicate_rules import lossy_companion_exts
from core.library.path_resolver import resolve_library_file_path
from core.library.retag_planner import _artist_credit_includes, plan_track
from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.reconciliation import display, file_evidence, grouping, projection
from core.reconciliation import selection as release_selection
from core.reconciliation.candidates import ScoredCandidate, assignment_evidence
from core.reconciliation.catalogue import catalogue_scan, deferred
from core.reconciliation.catalogue import current as catalogue_current
from core.reconciliation.discovery import (
    search_release_candidates as _search_release_candidates,
)
from core.reconciliation.discovery import supported_search_context
from core.reconciliation.evidence import (
    ReconciliationEvidence,
    album_folders_audio_path_digest,
    file_identity,
)
from core.reconciliation.grouping import (
    _SOURCES,
)
from core.reconciliation.identity import missing_slot_key, review_case_key
from core.reconciliation.inventory import (
    InventoryRequest,
    ScanScope,
    directory_observations,
    discovery_roots,
    inventory_record,
    known_membership_agrees,
    load_inventory_request,
    track_selection,
)
from core.reconciliation.metadata import (
    album_consistency,
    confirmed_recording,
    recording_id_conflict,
)
from core.reconciliation.plan import (
    audio_contradicts_local_title,
    display_title_compatible,
    semantic_title_key,
    title_difference_kind,
)
from core.reconciliation.store import retire_unseen_plans
from core.reconciliation.tools import ToolEvidence
from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobContext,
    JobResult,
    RepairJob,
    all_library_roots,
    walk_library,
    watchlist_artist_names,
)
from utils.logging_config import get_logger

logger = get_logger('repair_job.library_reconciliation')


def _read_reconciliation_tags(path):
    from core.library.release_identity import read_release_evidence
    tags = dict(_read_file_tags(path))
    tags.update(read_release_evidence(path))
    return tags


_read_reconciliation_tags.required_cache_keys = (
    *_read_file_tags.required_cache_keys, 'musicbrainz_release_id', 'artists_list',
)


_WATCHLIST_REMOVAL_PREFIX = '.watchlist-removal-'


def _retire_stale_findings(db, observed: set[tuple[str, str, str]],
                           target_folder: str | None = None) -> None:
    """Close proposals absent from a completed library scan."""
    conn = db._get_connection()
    try:
        rows = conn.execute('''
            SELECT id, finding_type, entity_type, entity_id FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
              AND (? IS NULL OR (json_valid(details_json) AND
                   json_extract(details_json, '$.album_folder') = ?))
        ''', (target_folder, target_folder)).fetchall()
        stale = [row[0] for row in rows if tuple(str(part) for part in row[1:4])
                 not in observed]
        if stale:
            conn.executemany('''
                UPDATE repair_findings SET status = 'resolved', user_action = 'obsolete',
                    resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', [(finding_id,) for finding_id in stale])
            conn.commit()
    finally:
        conn.close()


def _preserve_folder_findings(db, observed: set[tuple[str, str, str]], folder: str) -> None:
    """Keep existing decisions when transient evidence left one folder unscanned."""
    conn = db._get_connection()
    try:
        rows = conn.execute('''
            SELECT finding_type, entity_type, entity_id
            FROM repair_findings
            WHERE job_id = 'library_reconciliation' AND status = 'pending'
              AND json_valid(details_json)
              AND json_extract(details_json, '$.album_folder') = ?
        ''', (folder,)).fetchall()
        observed.update((row[0], row[1], str(row[2])) for row in rows)
    finally:
        conn.close()


@deferred('finding')
def _emit_finding(context: JobContext, observed: set[tuple[str, str, str]], **finding):
    observed.add((finding['finding_type'], finding['entity_type'],
                  str(finding['entity_id'])))
    return bool(context.create_finding and context.create_finding(**finding))


@register_job
class LibraryReconciliationJob(RepairJob):
    job_id = 'library_reconciliation'
    display_name = 'Reconcile Library Albums'
    description = 'Compare album folders, audio fingerprints, tags, and exact release tracklists'
    help_text = (
        'Opt-in, read-only scan. Fingerprints audio and compares each folder with '
        'its linked or inferred release. Findings enter the Review Queue with '
        'the file, AcoustID candidates, expected slot and recommended action. '
        'Missing release slots are never requested without complete-album intent.'
    )
    icon = 'repair-icon-acoustid'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'min_acoustid_score': 0.80,
        'catalogue_cache_enabled': True,
        'matching_cache_mode': 'reuse',
        'source_preference': 'active_preferred',
        'album_title_policy': 'preserve',
    }
    setting_options = {
        'matching_cache_mode': ['off', 'shadow', 'reuse'],
        'source_preference': ['active_preferred', 'best_fit'],
        'album_title_policy': ['preserve', 'exact_release'],
    }
    auto_fix = False

    def _get_settings(self, context: JobContext) -> dict:
        merged = self.default_settings.copy()
        if context.config_manager:
            configured = context.config_manager.get(
                f'repair.jobs.{self.job_id}.settings', {}) or {}
            if isinstance(configured, dict):
                merged.update(configured)
        # Legacy installations may still store digital mode. Source positions
        # are now authoritative; never flatten a selected multidisc release.
        merged.pop('numbering_mode', None)
        if merged.get('source_preference') not in ('active_preferred', 'best_fit'):
            merged['source_preference'] = 'active_preferred'
        if merged.get('album_title_policy') not in ('preserve', 'exact_release'):
            merged['album_title_policy'] = 'preserve'
        return merged

    def _load_rows(self, context: JobContext, roots: list[str], selection=None) -> tuple[dict, dict]:
        """One DB read for path→track and album metadata, with old-schema guards."""
        conn = context.db._get_connection()
        try:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(albums)')}
            track_columns = {row[1] for row in conn.execute('PRAGMA table_info(tracks)')}
            source_cols = [col for col in _SOURCES if col in columns]
            extras = ', '.join(f'al.{col}' for col in source_cols)
            if extras:
                extras = ', ' + extras
            canonical = (', al.canonical_source, al.canonical_album_id'
                         if {'canonical_source', 'canonical_album_id'} <= columns else '')
            if canonical and 'canonical_locked' in columns:
                canonical += ', al.canonical_locked'
            owner = ', al.owner_profile_id' if 'owner_profile_id' in columns else ''
            record_type = ', al.record_type' if 'record_type' in columns else ''
            locked = ', t.metadata_locked' if 'metadata_locked' in track_columns else ''
            track_artist = (', t.track_artist' if 'track_artist' in track_columns
                            else ', NULL AS track_artist')
            recording_disambiguation = (
                ', t.recording_disambiguation'
                if 'recording_disambiguation' in track_columns
                else ', NULL AS recording_disambiguation'
            )
            size = ', t.file_size' if 'file_size' in track_columns else ''
            bitrate = ', t.bitrate' if 'bitrate' in track_columns else ''
            artist = ', ar.name AS artist_name' if 'artists' in {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            } else ''
            artist_join = 'LEFT JOIN artists ar ON ar.id = al.artist_id' if artist else ''
            if artist and 'aliases' in {row[1] for row in conn.execute('PRAGMA table_info(artists)')}:
                artist += ', ar.aliases AS artist_aliases'
            cursor = conn.execute(f'''
                SELECT t.id, t.file_path, t.title, t.album_id, t.track_number,
                       t.disc_number, t.duration, t.musicbrainz_recording_id,
                       al.title AS album_title{artist}{canonical}{owner}{record_type}{locked}{size}{bitrate}
                       {recording_disambiguation}{track_artist}{extras}
                FROM tracks t LEFT JOIN albums al ON al.id = t.album_id
                {artist_join}
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  {('AND ' + selection[0]) if selection else ''}
            ''', selection[1] if selection else [])
            names = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            tracks = {}
            albums = {}
            for row in rows:
                data = dict(zip(names, row, strict=True))
                if data.get('artist_aliases'):
                    try:
                        aliases = json.loads(data['artist_aliases'])
                        data['artist_aliases'] = [name for name in aliases if isinstance(name, str) and name.strip()] if isinstance(aliases, list) else []
                    except (ValueError, TypeError):
                        data['artist_aliases'] = []
                path = data.get('file_path')
                if path:
                    # The same library is commonly mounted at both /music and
                    # /app/Music/library. A stored /music path may exist, but
                    # the scanner can be walking the other mount. Index scan
                    # aliases only when they resolve to the same physical file;
                    # matching suffixes in separate profile roots are not enough.
                    direct_exists = os.path.isfile(path)
                    candidates = []
                    for root in roots:
                        physical = resolve_library_file_path(path, library_root=root)
                        if not physical or physical in candidates:
                            continue
                        candidates.append(physical)
                    physical_paths = [path] if direct_exists else []
                    if direct_exists:
                        for physical in candidates:
                            try:
                                if (physical not in physical_paths and
                                        os.path.samefile(path, physical)):
                                    physical_paths.append(physical)
                            except OSError:
                                continue
                    elif len(candidates) == 1:
                        physical_paths = candidates
                    elif candidates:
                        # A stale root prefix can resolve to the same relative
                        # path in two independent profile libraries. Only keep
                        # multiple aliases when the OS proves they are one file.
                        try:
                            if all(os.path.samefile(candidates[0], candidate)
                                   for candidate in candidates[1:]):
                                physical_paths = candidates
                        except OSError:
                            pass
                    if not physical_paths and not candidates:
                        physical = resolve_library_file_path(
                            path, transfer_folder=context.transfer_folder,
                            config_manager=context.config_manager)
                        if physical:
                            physical_paths.append(physical)
                    for physical in physical_paths:
                        stored_parts = os.path.normpath(path).replace('\\', '/').split('/')[-2:]
                        resolved_parts = os.path.normpath(physical).replace('\\', '/').split('/')[-2:]
                        if [part.casefold() for part in stored_parts] != [
                                part.casefold() for part in resolved_parts]:
                            continue
                        elif data.get('file_size'):
                            try:
                                if os.path.getsize(physical) != data['file_size']:
                                    continue
                            except OSError:
                                continue
                        tracks[os.path.normpath(physical)] = data
                if data.get('album_id') is not None:
                    albums[data['album_id']] = data
            return tracks, albums
        finally:
            conn.close()

    def _release_candidates(
        self, files: list[dict], album: dict | None, context: JobContext, *,
        include_audio: bool = False,
        exact_musicbrainz_releases: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        candidates = []
        if album:
            if album.get('canonical_source') and album.get('canonical_album_id'):
                candidates.append((album['canonical_source'], str(album['canonical_album_id'])))
            for column, source in _SOURCES.items():
                if album.get(column):
                    identifier = str(album[column])
                    candidates.append((source, identifier))
                    if column == 'musicbrainz_release_id' and exact_musicbrainz_releases is not None:
                        exact_musicbrainz_releases.add(identifier)
        if include_audio and context.mb_client:
            # Only a few top fingerprint IDs are needed to seed release
            # discovery. Cap the fan-out; a single recording can belong to
            # hundreds of compilations and is not a release verdict.
            release_votes: Counter[str] = Counter()
            seen = set()
            for file in files:
                recordings = (file.get('acoustid') or {}).get('recordings') or []
                if fingerprint_is_ambiguous(recordings):
                    continue
                top = max(recordings, key=lambda row: float(row.get('score') or 0),
                          default={})
                mbid = top.get('mbid')
                if not mbid or mbid in seen or float(top.get('score') or 0) < 0.8:
                    continue
                if len(seen) >= 3:
                    break
                seen.add(mbid)
                try:
                    data = context.mb_client.get_recording(mbid, includes=['releases']) or {}
                except Exception as exc:
                    logger.debug('Could not expand release candidate %s: %s', mbid, exc)
                    continue
                for release in data.get('releases') or []:
                    if release.get('id'):
                        release_votes[release['id']] += 1
                        if exact_musicbrainz_releases is not None:
                            exact_musicbrainz_releases.add(str(release['id']))
                if len(seen) >= 3:
                    break
            candidates.extend(('musicbrainz', release_id)
                              for release_id, _ in release_votes.most_common(5))
        return list(dict.fromkeys(candidates))[:6]

    def _intents(self, context: JobContext) -> dict[str, tuple[str, str, str, list[str]]]:
        from core.reconciliation.album_state import intents
        with context.db._get_connection() as conn:
            return intents(conn)

    def _assignment_overrides(self, context: JobContext) -> dict[tuple[str, str, str, str], tuple]:
        from core.reconciliation.album_state import assignments
        with context.db._get_connection() as conn:
            return assignments(conn)

    def _wishlisted_slots(self, context: JobContext) -> dict[int, set[tuple[str, str, str]]]:
        """Read exact requested tracks once for the current scan."""
        conn = context.db._get_connection()
        try:
            from core.reconciliation.fulfillment import outstanding
            durable = outstanding(conn)
            replacements = conn.execute('''SELECT case_id, source, release_id, source_track_id, state, published_path
                FROM reconcile_fulfillment WHERE replacement_request=1
                  AND state IN ('requested', 'published', 'cancelled')''').fetchall()
            cancelled = conn.execute("SELECT profile_id, source, source_track_id, release_id FROM reconcile_fulfillment WHERE state='cancelled'").fetchall()
            rows = conn.execute('''
                SELECT profile_id, spotify_track_id, spotify_data
                FROM wishlist_tracks
            ''').fetchall()
        except Exception as exc:
            logger.debug('Could not read wishlist state for reconciliation: %s', exc)
            return {}
        finally:
            conn.close()
        by_profile: dict[int, set[tuple[str, str, str]]] = defaultdict(set)
        self._published_by_profile = defaultdict(set)
        self._cancelled_by_profile = defaultdict(set)
        self._replacement_requests = defaultdict(list)
        for case_id, source, release_id, track_id, state, path in replacements:
            self._replacement_requests[(case_id, source, release_id)].append({
                'source_track_id': track_id, 'state': state, 'published_path': path, 'replacement_request': True})
        for profile_id, source, track_id, release_id in cancelled:
            self._cancelled_by_profile[int(profile_id or 1)].add((source, track_id, release_id))
        for profile_id, track_id, release_id, state, source in durable:
            by_profile[int(profile_id or 1)].add((source, track_id, release_id))
            if state == 'published':
                self._published_by_profile[int(profile_id or 1)].add((source, track_id, release_id))
        for profile_id, stored_id, raw in rows:
            try:
                payload = json.loads(raw or '{}')
                source = str(payload.get('provider') or 'spotify')
                track_id = str(payload.get('id') or str(stored_id).split('::')[0])
                release_id = str((payload.get('album') or {}).get('id') or '')
                if track_id and release_id:
                    by_profile[int(profile_id or 1)].add((source, track_id, release_id))
            except (TypeError, ValueError, AttributeError):
                continue
        return by_profile

    @catalogue_scan
    def scan(self, context: JobContext, *, target_folder: str | None = None,
             scope: ScanScope | str = ScanScope.ALBUM) -> JobResult:
        scan_started = time.perf_counter()
        result = JobResult()
        roots = all_library_roots(context)
        inventory_request = load_inventory_request(context.db, target_folder, ScanScope(scope), roots=roots)
        plan_only = inventory_request.scope == ScanScope.PLAN
        client = context.acoustid_client
        if not plan_only and client is None:
            result.stopped_early = 'AcoustID client is unavailable'
            return result
        available, reason = (True, '') if plan_only else client.is_available()
        if not available:
            result.stopped_early = reason or 'AcoustID unavailable'
            return result
        settings = self._get_settings(context)
        numbering_mode = 'release'
        preferred_source = ''
        if settings['source_preference'] == 'active_preferred' and context.config_manager:
            preferred_source = str(context.config_manager.get('metadata.fallback_source', '') or '')
        rank_candidate = lambda row: release_selection._candidate_rank(
            files, row, preferred_source)
        roots = all_library_roots(context)
        if not roots or any(not os.path.isdir(root) for root in roots):
            result.stopped_early = 'A configured library root is unavailable'
            return result
        from core.reconciliation.migration import repair_legacy_ids
        if not plan_only:
            repair_legacy_ids(context.db)
        def selection_for(request):
            return track_selection(context.db, request, title_key=grouping._album_identity_title,
                base_title_key=lambda title: normalize(re.split(r'\s*[\[(]', str(title or ''), maxsplit=1)[0]))
        selection = selection_for(inventory_request)
        if selection is None:
            inventory_request = InventoryRequest(ScanScope.LIBRARY, inventory_request.saved)
        tracks, albums = (self._load_rows(context, roots, selection) if selection else
                          self._load_rows(context, roots))
        if (inventory_request.scope == ScanScope.ALBUM and
                not known_membership_agrees(inventory_request, tracks)):
            inventory_request = InventoryRequest(ScanScope.DISCOVERY, inventory_request.saved)
            selection = selection_for(inventory_request)
            if selection is None:
                inventory_request = InventoryRequest(ScanScope.LIBRARY, inventory_request.saved)
            tracks, albums = (self._load_rows(context, roots, selection) if selection else
                              self._load_rows(context, roots))
        walk_roots = roots
        if inventory_request.scope == ScanScope.DISCOVERY:
            walk_roots = discovery_roots(inventory_request, tracks, roots)
            if walk_roots is None:
                # Broaden the walk without discarding the verified track IDs
                # needed to follow an album whose old folders have moved.
                inventory_request = InventoryRequest(ScanScope.LIBRARY, inventory_request.saved)
                tracks, albums = self._load_rows(context, roots)
                walk_roots = roots
        intents = self._intents(context)
        with context.db._get_connection() as conn:
            watched_artists = watchlist_artist_names(conn.cursor())
        wishlisted_by_profile = self._wishlisted_slots(context)
        assignment_overrides = self._assignment_overrides(context)
        from core.reconciliation import choices as review_choices
        kept_numbering = review_choices.applied_numbering_choices(context.db)
        reviewed_releases = review_choices.load_release_choices(context.db)
        from core.reconciliation.audio_copy import load_choices as audio_copy_choices
        audio_copies = audio_copy_choices(context.db)
        numbering_history = review_choices.flattened_numbering_history(context.db)
        playlist_membership = {}
        if context.playlist_membership and not plan_only:
            try:
                playlist_membership = context.playlist_membership() or {}
            except Exception as exc:
                logger.debug('Could not read playlist membership: %s', exc)
        if inventory_request.scope in (ScanScope.ALBUM, ScanScope.PLAN):
            folders = {os.path.normpath(target_folder): list(inventory_request.saved['paths'])}
            trusted_logical_groups = {}
            if inventory_request.saved.get('trusted_album_id') is not None:
                trusted_logical_groups[os.path.normpath(target_folder)] = inventory_request.saved['trusted_album_id']
        else:
            folders: dict[str, list[str]] = defaultdict(list)
            for root in walk_roots:
                if not os.path.isdir(root):
                    continue
                for dirpath, dirs, filenames in walk_library(root):
                    # Watchlist-removal snapshots are recovery material outside
                    # the active library. Auditing them manufactures duplicate,
                    # extra and unresolved findings for files awaiting deletion.
                    dirs[:] = [name for name in dirs
                               if not name.casefold().startswith(_WATCHLIST_REMOVAL_PREFIX)]
                    for name in filenames:
                        if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                            folders[dirpath].append(os.path.join(dirpath, name))
            folders = grouping._group_disc_folders(folders, tracks, roots)
            folders, trusted_logical_groups = grouping._group_logical_albums(folders, tracks, albums)
            from core.reconciliation.fulfillment import group_delivered_files
            folders = group_delivered_files(context.db, folders, tracks, albums, trusted_logical_groups)
            discovery_cache = ReconciliationEvidence(context.db, client)
            try:
                folders = grouping._group_unassigned_downloads(
                    folders, tracks, albums, trusted_logical_groups,
                    lambda path: discovery_cache.read_tags(path, _read_reconciliation_tags))
            finally:
                discovery_cache.close()
        relocated_from = None
        target_member_folders: set[str] = set()
        if target_folder is not None:
            target_folder = os.path.normpath(target_folder)
            # A previously separate download folder may now belong to a
            # logical album anchored in its sibling. Resolve its old case to
            # that group so a review action can replan and retire both cases.
            if target_folder not in folders:
                matches = [folder for folder, paths in folders.items()
                           if any(os.path.dirname(path) == target_folder
                                  for path in paths)]
                if len(matches) == 1:
                    target_folder = matches[0]
            if target_folder not in folders:
                # Database track IDs survive a rename. Only follow a move when
                # all surviving known members have one unambiguous folder owner.
                known_ids = set((inventory_request.saved or {}).get('track_ids') or [])
                # _load_rows indexes verified aliases of the same physical
                # file (for example /music and /app/Music/library). Compare
                # surviving track IDs, since only the configured root is walked.
                current_ids = {row['id'] for path, row in tracks.items()
                               if row.get('id') in known_ids and os.path.isfile(path)}
                owners = [folder for folder, paths in folders.items()
                          if current_ids and current_ids.issubset({
                              tracks.get(os.path.normpath(path), {}).get('id')
                              for path in paths})]
                if len(owners) == 1:
                    relocated_from, target_folder = target_folder, owners[0]
            if target_folder not in folders:
                result.stopped_early = 'The album location could not be verified; refresh the library inventory'
                return result
            target_member_folders = {target_folder} | {
                os.path.dirname(path) for path in folders[target_folder]}
            if relocated_from:
                target_member_folders.add(relocated_from)
        inventory_seconds = time.perf_counter() - scan_started
        observed: set[tuple[str, str, str]] = set()
        observed_plans: set[str] = set()
        plans_complete = True
        completed = True
        album_folder_counts: Counter[object] = Counter()
        for paths in folders.values():
            album_folder_counts.update({tracks[os.path.normpath(path)]['album_id']
                for path in paths if os.path.normpath(path) in tracks and
                tracks[os.path.normpath(path)].get('album_id') is not None})
        if inventory_request.scope in (ScanScope.ALBUM, ScanScope.PLAN):
            for album_id in inventory_request.album_ids:
                album_folder_counts[album_id] = inventory_request.saved.get(
                    'album_folder_counts', {}).get(str(album_id), 1)
        if target_folder is not None:
            folders = {target_folder: folders[target_folder]}
        directory_cache = {}
        release_cache = {}
        companion_extensions = lossy_companion_exts(context.config_manager, context.db, logger)
        release_meta_cache = {}
        recording_checks = dict((inventory_request.planning or {}).get('recordings') or {})
        tool_evidence = ToolEvidence(context.db)
        release_search_cache = {}
        release_group_cache = {}
        exact_musicbrainz_releases: set[str] = set()
        cache = ReconciliationEvidence(context.db, client)
        cached_files = {file['path']: file for file in (inventory_request.planning or {}).get('files', [])}
        cached_candidates = (inventory_request.planning or {}).get('candidates', [])
        if plan_only:
            for candidate in cached_candidates:
                key = (candidate['source'], candidate['release_id'])
                release_cache[key] = candidate['slots']
                release_meta_cache[key] = candidate['metadata']
                if key[0] == 'musicbrainz':
                    exact_musicbrainz_releases.add(key[1])
        lookup_error_reported = False
        try:
            for folder_index, (folder, paths) in enumerate(sorted(folders.items())):
                if context.check_stop() or context.wait_if_paused():
                    completed = False
                    break
                with catalogue_current.get().album(folder_index + 1, caches=(
                        release_cache, release_meta_cache, release_search_cache, release_group_cache)):
                    emit = partial(_emit_finding, context, observed)

                    files = []
                    cancelled = False
                    evidence_error = ''
                    for path in sorted(paths):
                        if context.check_stop():
                            cancelled = True
                            break
                        db_row = tracks.get(os.path.normpath(path))
                        try:
                            stat = os.stat(path)
                        except OSError:
                            result.skipped += 1
                            completed = False
                            continue
                        if plan_only:
                            files.append({**cached_files[path], 'db': db_row})
                            result.scanned += 1
                            continue
                        audio = cache.identify(path)
                        if audio.get('status') == 'error':
                            if not lookup_error_reported:
                                result.errors += 1
                                lookup_error_reported = True
                            if audio.get('deferred') or audio.get('invalid_key'):
                                # The evidence cache only marks errors deferred
                                # after its circuit breaker establishes a service
                                # problem. Preserve this logical album rather than
                                # replacing good findings with outage noise.
                                evidence_error = audio.get('error') or 'AcoustID lookup failed'
                                break
                            # A decoder, malformed fingerprint, or isolated lookup
                            # failure belongs to this file. Keep auditing siblings
                            # and surface only this track as uncertain.
                        playlists = (list(playlist_membership.get(str(db_row['id']), []))
                                     if db_row else [])
                        files.append(cache.file_evidence(path, _read_reconciliation_tags, audio=audio,
                            library_fields={'db': db_row, 'playlists': playlists,
                                            'size_bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                                            'file_identity': file_identity(stat)}))
                        result.scanned += 1
                    # The evidence cache uses its own SQLite connection. Release
                    # its write lock before create_finding opens another one.
                    cache.checkpoint()
                    if cancelled:
                        completed = False
                        break
                    if evidence_error:
                        _preserve_folder_findings(context.db, observed, folder)
                        logger.warning('Skipping reconciliation for %s: %s', folder, evidence_error)
                        if context.update_progress:
                            context.update_progress(folder_index + 1, len(folders))
                        continue
                    if not files:
                        continue
                    album_counts = Counter(f['db']['album_id'] for f in files
                                           if f.get('db') and f['db'].get('album_id') is not None)
                    trusted_anchor_id = trusted_logical_groups.get(folder)
                    if len(album_counts) > 1 and trusted_anchor_id is None:
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_unresolved',
                            severity='warning', entity_type='folder', entity_id=folder,
                            file_path=None, title=f'Mixed albums: {os.path.basename(folder)}',
                            description='This folder contains tracks assigned to more than one library album.',
                            details={'album_folder': folder, 'album_title': os.path.basename(folder),
                                     'album_ids': list(album_counts), 'file_count': len(files),
                                     'folder_issue': 'mixed_ownership',
                                     'checked_files': [{
                                         'file_path': member['path'],
                                         'title': member['tags'].get('title') or os.path.basename(member['path']),
                                         'album': (albums.get((member.get('db') or {}).get('album_id')) or {}).get('album_title') or '',
                                         'artist': (albums.get((member.get('db') or {}).get('album_id')) or {}).get('artist_name') or '',
                                     } for member in files],
                                     'evidence_signature': file_evidence._evidence_signature(files, list(album_counts))}):
                            result.findings_created += 1
                        continue
                    album_id = (trusted_anchor_id if trusted_anchor_id is not None else
                                album_counts.most_common(1)[0][0] if album_counts else None)
                    album = albums.get(album_id)
                    if album_id is not None and album_folder_counts[album_id] > 1:
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_unresolved',
                            severity='info', entity_type='folder', entity_id=folder,
                            file_path=None, title=f'Split album: {os.path.basename(folder)}',
                            description='This album spans separate folders that cannot be safely combined.',
                            details={'album_id': album_id, 'album_folder': folder,
                                     'profile_id': album.get('owner_profile_id') if album else 1,
                                     'album_title': os.path.basename(folder), 'file_count': len(files),
                                     'folder_issue': 'split_ownership',
                                     'evidence_signature': file_evidence._evidence_signature(files, album_id)}):
                            result.findings_created += 1
                        continue
                    member_folders = sorted({os.path.dirname(file['path']) for file in files})
                    parents = tuple(sorted({os.path.dirname(path) for path in member_folders}))
                    if parents not in directory_cache:
                        directory_cache[parents] = directory_observations(member_folders, roots)
                    album_inventory = inventory_record(files, trusted_album_id=trusted_anchor_id,
                        album_folder_counts={key: album_folder_counts[key] for key in album_counts},
                        roots=roots, observations=directory_cache[parents])
                    file_evidence._mark_lossy_companions(files, companion_extensions)
                    candidates = ([(row['source'], row['release_id']) for row in cached_candidates]
                        if plan_only else self._release_candidates(files, album, context,
                            exact_musicbrainz_releases=exact_musicbrainz_releases))
                    reviewed_release = reviewed_releases.get(str(album_id))
                    if reviewed_release and reviewed_release not in candidates:
                        candidates.insert(0, reviewed_release)
                    if reviewed_release and reviewed_release[0] == 'musicbrainz':
                        exact_musicbrainz_releases.add(reviewed_release[1])
                    linked_candidates = list(candidates)
                    scored = release_selection._score_release_candidates(
                        files, candidates, release_cache, exact_musicbrainz_releases,
                        audio_copy_release=audio_copies.get(str(album_id)))
                    best_initial = (max(scored, key=rank_candidate) if scored else None)
                    if not plan_only and (not best_initial or best_initial[6]['extra_files']
                            or best_initial[6]['missing_slots']):
                        additional = [candidate for candidate in self._release_candidates(
                            files, album, context, include_audio=True,
                            exact_musicbrainz_releases=exact_musicbrainz_releases)
                            if candidate not in candidates]
                        scored.extend(release_selection._score_release_candidates(
                            files, additional, release_cache, exact_musicbrainz_releases))
                        candidates.extend(additional)
                    best_expanded = (max(scored, key=rank_candidate) if scored else None)
                    # Discover digital siblings of a physical release. Format is
                    # only a tie breaker after audio/tracklist fit, and has no
                    # effect on the positions written to file or library metadata.
                    needs_digital = bool(
                        not plan_only and best_expanded and not release_selection._edition_preference(release_selection._release_metadata(
                            best_expanded, release_meta_cache, exact_musicbrainz_releases))[0]
                    )
                    search_album = supported_search_context(
                        album, files, best_expanded,
                        release_selection._release_metadata(best_expanded, release_meta_cache, exact_musicbrainz_releases)
                        if best_expanded and not plan_only else {})
                    needs_artist_discovery = search_album is not album
                    if (needs_digital or needs_artist_discovery) and not plan_only and not any(row.source == 'musicbrainz' for row in scored):
                        searched = [candidate for candidate in _search_release_candidates(
                            search_album, context, release_search_cache) if candidate not in candidates]
                        exact_musicbrainz_releases.update(
                            release_id for source, release_id in searched
                            if source == 'musicbrainz')
                        candidates.extend(searched)
                        scored.extend(release_selection._score_release_candidates(
                            files, searched, release_cache, exact_musicbrainz_releases))
                        best_expanded = (max(scored, key=rank_candidate) if scored else None)
                    group_seed = max(
                        (row for row in scored if row.source == 'musicbrainz'),
                        key=rank_candidate,
                        default=None,
                    )
                    if (not plan_only and best_expanded and group_seed and
                            (best_expanded[6]['extra_files'] or
                             best_expanded[6]['missing_slots'] or
                             release_selection._systematic_numbering_drift(files, best_expanded) or
                             release_selection._single_position_outlier(files, best_expanded) or
                             not release_selection._edition_preference(
                                   release_selection._release_metadata(best_expanded,
                                                     release_meta_cache,
                                                     exact_musicbrainz_releases))[0])):
                        editions = [candidate for candidate in release_selection._release_group_candidates(
                            group_seed[4], files, context, release_group_cache)
                            if candidate not in candidates]
                        exact_musicbrainz_releases.update(release_id for _, release_id in editions)
                        candidates.extend(editions)
                        scored.extend(release_selection._score_release_candidates(
                            files, editions, release_cache, exact_musicbrainz_releases))
                    if not plan_only and (not scored or max(row.matched for row in scored) == 0):
                        searched = [candidate for candidate in _search_release_candidates(
                            search_album, context, release_search_cache) if candidate not in candidates]
                        exact_musicbrainz_releases.update(
                            release_id for source, release_id in searched
                            if source == 'musicbrainz')
                        candidates.extend(searched)
                        scored.extend(release_selection._score_release_candidates(
                            files, searched, release_cache, exact_musicbrainz_releases))
                    if not scored:
                        unidentified_header = {'album_id': album_id,
                            'album_title': album.get('album_title') if album else os.path.basename(folder),
                            'profile_id': album.get('owner_profile_id') if album else 1,
                            'artist_name': album.get('artist_name') if album else '',
                            'album_folder': folder, 'source': '', 'release_id': '',
                            'evidence_signature': file_evidence._evidence_signature(files, candidates)}
                        unknown_case = projection._persist_unidentified_plan(context.db, unidentified_header, {
                            'state': 'unidentified', 'source': '', 'release_id': '',
                            'local_file_count': len(files), 'release_track_count': 0,
                            'library_root': next((root for root in sorted(roots, key=len, reverse=True)
                                if folder.startswith(root.rstrip(os.sep) + os.sep)), ''),
                            'inventory': album_inventory, 'candidates': [],
                            'assignments': [{'status': 'uncertain', 'file_path': file['path'],
                                'file': os.path.basename(file['path']), 'track_id': (file.get('db') or {}).get('id'),
                                'current_title': file['tags'].get('title') or os.path.basename(file['path']),
                                'file_duration_ms': file['tags'].get('duration_ms') or 0,
                                'current_tags': file['tags'], 'acoustid': file['acoustid']}
                                for file in files], 'categories': ['release_conflicts']})
                        observed_plans.add(unknown_case)
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_unresolved',
                            severity='info', entity_type='album' if album_id else 'folder',
                            entity_id=str(album_id) if album_id else folder, file_path=None,
                            title=f'Identify release: {os.path.basename(folder)}',
                            description='No usable exact release tracklist was found for these files.',
                            details={'album_id': album_id, 'album_title':
                                     album.get('album_title') if album else os.path.basename(folder),
                                     'profile_id': album.get('owner_profile_id') if album else 1,
                                     'artist_name': album.get('artist_name') if album else '',
                                     'album_folder': folder, 'file_count': len(files),
                                     'acoustid_statuses': [f['acoustid'].get('status') for f in files],
                                     'checked_files': file_evidence._checked_file_evidence(files),
                                     'attempted_releases': [
                                         {'source': source, 'release_id': release_id}
                                         for source, release_id in candidates],
                                     'evidence_signature': file_evidence._evidence_signature(files, candidates)}):
                            result.findings_created += 1
                        cache.checkpoint()
                        continue
                    scored.sort(key=rank_candidate, reverse=True)
                    # Consult release metadata only for equally fitting editions.
                    # Otherwise this would make an extra remote call for every
                    # poor candidate in every album.
                    leading_rank = rank_candidate(scored[0])
                    leading_count = sum(rank_candidate(row) == leading_rank for row in scored)
                    if leading_count > 1:
                        def edition_order(row):
                            metadata = release_selection._release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases)
                            # A lone recording can belong to many compilations.
                            # Its known album context precedes format/country,
                            # without claiming the tied editions are equivalent.
                            context_fit = int(len(files) == 1 and bool(album) and
                                not release_selection._single_file_album_context_conflict(album, metadata))
                            return context_fit, *release_selection._edition_preference(metadata)
                        scored[:leading_count] = sorted(
                            scored[:leading_count], key=edition_order, reverse=True)
                    # A single fingerprint proves the recording, but that same
                    # recording can occur on an original album and many later
                    # compilations. If the best audio-derived release contradicts
                    # the local album context, make one exact title search. Only
                    # an equally fitting title result may replace it; otherwise
                    # surface the album identity conflict instead of retagging the
                    # file and inventing a nearly complete album's missing tracks.
                    release_meta = {}
                    if not plan_only and len(files) == 1 and album and not linked_candidates:
                        release_meta = release_selection._release_metadata(
                            scored[0], release_meta_cache, exact_musicbrainz_releases)
                        if release_selection._single_file_album_context_conflict(album, release_meta):
                            searched = [candidate for candidate in _search_release_candidates(
                                search_album, context, release_search_cache)
                                if candidate not in candidates]
                            exact_musicbrainz_releases.update(
                                release_id for source, release_id in searched
                                if source == 'musicbrainz')
                            if searched:
                                candidates.extend(searched)
                                scored.extend(release_selection._score_release_candidates(
                                    files, searched, release_cache, exact_musicbrainz_releases))
                                scored.sort(key=rank_candidate, reverse=True)
                                release_selection._promote_album_context_candidate(scored, searched)
                                release_meta = release_selection._release_metadata(
                                    scored[0], release_meta_cache, exact_musicbrainz_releases)
                        if release_selection._single_file_album_context_conflict(album, release_meta):
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_unresolved',
                                severity='info', entity_type='album', entity_id=str(album_id),
                                file_path=files[0]['path'],
                                title=f'Confirm album: {album.get("album_title") or os.path.basename(folder)}',
                                description=('The audio identifies the recording, but no equally '
                                             'fitting release matches the local album context.'),
                                details={
                                    'album_id': album_id,
                                    'album_title': album.get('album_title') or os.path.basename(folder),
                                    'profile_id': album.get('owner_profile_id') or 1,
                                    'artist_name': album.get('artist_name') or '',
                                    'album_folder': folder, 'file_count': 1,
                                    'checked_files': file_evidence._checked_file_evidence(files),
                                    'candidates': [projection._candidate_evidence(
                                        files, row, release_selection._release_metadata(
                                            row, release_meta_cache,
                                            exact_musicbrainz_releases))
                                                   for row in scored[:6]],
                                    'evidence_signature': file_evidence._evidence_signature(
                                        files, [(row.source, row.release_id, row.clear, row.matched)
                                                for row in scored]),
                                }):
                                result.findings_created += 1
                            cache.checkpoint()
                            continue
                    retained_partial = release_selection._retain_partial_release(files, album, scored,
                        intents.get(str(album_id)), watched_artists,
                        lambda row: release_selection._release_metadata(row, release_meta_cache, exact_musicbrainz_releases))
                    ranked_candidates = list(scored)
                    retained_current = release_selection._supported_current_edition(files, album, scored)
                    if retained_current:
                        scored.remove(retained_current)
                        scored.insert(0, retained_current)
                    pinned_row = None
                    best_unpinned_rank = rank_candidate(scored[0])
                    if reviewed_release or (album and album.get('canonical_locked')):
                        chosen_release = reviewed_release or (album.get('canonical_source'),
                                                             str(album.get('canonical_album_id') or ''))
                        pinned_row = next((row for row in scored
                                           if (row.source, row.release_id) == chosen_release), None)
                        if pinned_row:
                            scored.remove(pinned_row)
                            reviewed_plan = release_selection._match_release(files, pinned_row.slots,
                                source=pinned_row.source, release_id=pinned_row.release_id,
                                catalogue_review=True)
                            pinned_row = ScoredCandidate(
                                sum(pair['confidence'] in ('clear', 'manual') for pair in reviewed_plan['pairs']),
                                len(reviewed_plan['pairs']), -len(reviewed_plan['extra_files']),
                                pinned_row.source, pinned_row.release_id, pinned_row.slots, reviewed_plan)
                            scored.insert(0, pinned_row)
                    clear, matched, _extras, source, release_id, slots, plan = scored[0]
                    # An explicitly selected, loaded release stays selected.
                    # Any catalogue disagreement belongs to its track decisions.
                    explicit_release = reviewed_releases.get(str(album_id)) == (source, release_id)
                    locked_conflict = bool(album and album.get('canonical_locked') and
                        (not pinned_row or (not explicit_release and pinned_row.matched == 0
                         and rank_candidate(pinned_row) != best_unpinned_rank)))
                    tied_rows = [row for row in scored
                                 if rank_candidate(row) == rank_candidate(scored[0])]
                    # Digital format resolves otherwise identical audio ties,
                    # including a physical edition's different medium split.
                    # Keep material track/version differences as release choices.
                    if release_selection._edition_preference(release_selection._release_metadata(
                            scored[0], release_meta_cache, exact_musicbrainz_releases))[0]:
                        tied_rows = [row for row in tied_rows
                            if release_selection._edition_preference(release_selection._release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases))[0]
                            or not release_selection._candidate_shapes_equivalent(
                                scored[0], row, ignore_positions=True)]
                    equivalent_tie = (len(tied_rows) > 1 and
                                      all(release_selection._candidate_shapes_equivalent(row, tied_rows[0])
                                          for row in tied_rows[1:]))
                    # A stored but unlocked release is a useful ordering hint. It
                    # cannot erase a real edition decision when equally fitting
                    # candidates contain different tracklists or positions.
                    tied = (len(tied_rows) > 1 and not equivalent_tie)
                    if retained_partial or retained_current or (pinned_row and not locked_conflict):
                        tied = False
                    pinned = bool(album and album.get('canonical_source') == source
                                  and str(album.get('canonical_album_id') or '') == release_id)
                    edition_conflict = bool(
                        not explicit_release and album_id is not None and album and not album.get('canonical_locked') and
                        release_selection._has_unexplained_identified_extras(files, scored[0])
                    )
                    symmetric_mismatch = bool(
                        not explicit_release and album_id is not None and not pinned and
                        plan['extra_files'] and plan['missing_slots'] and
                        len(plan['extra_files']) >= 2 and
                        len(plan['missing_slots']) >= 2
                    )
                    position_conflict = bool(
                        not explicit_release and not (album and album.get('canonical_locked')) and
                        release_selection._distinctive_position_conflict(files, scored[0], scored[1:]))
                    digital_edition_choice = bool(
                        not explicit_release and album_id is not None and album and not retained_current and not album.get('canonical_locked') and
                        release_selection._full_coverage_digital_alternative(
                            scored[0], ranked_candidates, len(files),
                            lambda row: release_selection._release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases))
                    )
                    album_identity_conflict = bool(
                        not explicit_release and not (album and album.get('canonical_locked'))
                        and len(files) <= 2 and len(slots) > len(files)
                        and release_selection._single_file_album_context_conflict(album, release_selection._release_metadata(
                            scored[0], release_meta_cache, exact_musicbrainz_releases)))
                    if (locked_conflict or tied or edition_conflict or symmetric_mismatch or
                            position_conflict or digital_edition_choice or album_identity_conflict or
                            (matched == 0 and not pinned)):
                        blockers = [row for row in tied_rows if tied and
                                    not release_selection._candidate_shapes_equivalent(scored[0], row)]
                        blockers.extend(row for row in ranked_candidates if
                            (position_conflict and release_selection._distinctive_position_conflict(files, scored[0], [row])) or
                            (digital_edition_choice and release_selection._full_coverage_digital_alternative(
                                scored[0], [row], len(files), lambda candidate: release_selection._release_metadata(
                                    candidate, release_meta_cache, exact_musicbrainz_releases))))
                        visible_candidates = release_selection._review_candidates(scored[0], ranked_candidates, blockers)
                        differences = {(row[3], row[4]): release_selection._candidate_difference(scored[0], row)
                                       for row in visible_candidates if row is not scored[0]}
                        issue_type = ('reconciliation_release' if album_id is not None and
                                      (tied or locked_conflict or edition_conflict or
                                       symmetric_mismatch or position_conflict or
                                       digital_edition_choice or album_identity_conflict)
                                      else 'reconciliation_unresolved')
                        if locked_conflict:
                            description = ('The locked release does not fit as well as another candidate.'
                                           if pinned_row else
                                           'The locked release tracklist could not be loaded.')
                        elif tied:
                            description = 'Multiple releases fit equally well.'
                        elif edition_conflict:
                            description = ('No compared edition accounts for every identified album '
                                           'file. Confirm the edition before reviewing extra audio.')
                        elif symmetric_mismatch:
                            description = ('Several local files and release tracks remain unmatched. '
                                           'Compare the assignments before choosing this edition.')
                        elif position_conflict:
                            description = ('Another edition preserves the position of a distinct '
                                           'recording. Choose the release before changing track numbers.')
                        elif digital_edition_choice:
                            description = ('A digital edition covers every file, but one track has '
                                           'different evidence. Compare the editions before choosing.')
                        elif album_identity_conflict:
                            description = ('This recording appears on a differently named collection. '
                                           'Choose the intended release before changing the album or requesting its other tracks.')
                        else:
                            description = 'No release track could be assigned confidently.'
                        choice_common = {
                            'album_id': album_id,
                            'album_title': album.get('album_title') if album else os.path.basename(folder),
                            'profile_id': album.get('owner_profile_id') if album else 1,
                            'artist_name': album.get('artist_name') if album else '',
                            'album_folder': folder,
                            'source': source,
                            'release_id': release_id,
                            'locked_release': {'source': album.get('canonical_source'),
                                               'release_id': str(album.get('canonical_album_id') or '')}
                            if locked_conflict else None,
                            'current_release': {
                                'source': album.get('canonical_source') or '',
                                'release_id': str(album.get('canonical_album_id') or ''),
                                'locked': bool(album.get('canonical_locked')),
                            } if album else None,
                            'evidence_signature': file_evidence._evidence_signature(
                                files, [(row.source, row.release_id, row.clear, row.matched)
                                        for row in ranked_candidates]),
                            'release_decision_reason': description,
                        }
                        case_id = projection._persist_selected_plan(
                            context.db, choice_common, files, scored[0], inventory=album_inventory,
                            candidates=visible_candidates, state='release_conflict',
                            candidate_differences=differences,
                            candidate_metadata={
                                (row.source, row.release_id): release_selection._release_metadata(
                                    row, release_meta_cache, exact_musicbrainz_releases)
                                for row in visible_candidates
                            },
                            numbering_mode=numbering_mode,
                        )
                        if case_id:
                            observed_plans.add(case_id)
                        else:
                            plans_complete = False
                        if emit(
                            job_id=self.job_id, finding_type=issue_type,
                            severity='warning' if tied or locked_conflict else 'info',
                            entity_type='album' if album_id is not None else 'folder',
                            entity_id=str(album_id) if album_id is not None else folder,
                            file_path=None, title=f'Choose release: {os.path.basename(folder)}',
                            description=description,
                            details={**choice_common,
                                     'candidates': [projection._candidate_evidence(
                                         files, row, release_selection._release_metadata(
                                             row, release_meta_cache,
                                             exact_musicbrainz_releases))
                                                    | {'difference_summary': differences.get((row[3], row[4]), '')}
                                                    for row in visible_candidates]}):
                            result.findings_created += 1
                        cache.checkpoint()
                        continue
                    forced_pairs = {}
                    for file_index, file in enumerate(files):
                        from core.reconciliation.choices import assignment_for_file
                        override = assignment_for_file(assignment_overrides, album_id, source, release_id, file, files, db=context.db)
                        if not override:
                            continue
                        slot_key, size_bytes, mtime_ns, fingerprint_key = override
                        if (file['size_bytes'] != size_bytes or file['mtime_ns'] != mtime_ns or
                                (file['acoustid'].get('fingerprint_key') or '') != fingerprint_key):
                            continue
                        slot_index = next((index for index, slot in enumerate(slots)
                                           if projection._wanted_slot_key(slot) == slot_key), None)
                        if slot_index is not None:
                            forced_pairs[file_index] = slot_index
                    if forced_pairs:
                        try:
                            plan = release_selection._match_release(files, slots, source=source, release_id=release_id,
                                                  forced_pairs=forced_pairs, catalogue_review=bool(pinned_row))
                        except ValueError as exc:
                            logger.warning('Ignoring conflicting manual assignments for %s: %s',
                                           folder, exc)
                        else:
                            scored[0] = ScoredCandidate(sum(pair['confidence'] in ('clear', 'manual') for pair in plan['pairs']),
                                         len(plan['pairs']), -len(plan['extra_files']),
                                         source, release_id, slots, plan)
                    release_meta = release_selection._release_metadata(
                        scored[0], release_meta_cache, exact_musicbrainz_releases)
                    # MusicBrainz album IDs can be release-group IDs. Its album
                    # adapter picks a concrete representative release and exposes
                    # that ID here; a missing-track request must target that exact
                    # edition even while the stored canonical pin remains a group.
                    exact_release_id = (release_meta.get('musicbrainz_release_id')
                                        if source == 'musicbrainz' else None) or release_id
                    identity_target = file_evidence._release_identity_target(files, album, source, exact_release_id)
                    consistency = album_consistency(files)
                    # Differing embedded release IDs are repairable when one
                    # selected edition clearly accounts for the entire folder.
                    # Keep the conflicts as evidence without asking the same
                    # album identity question on every track.
                    consistency_review = set(consistency)
                    if (source == 'musicbrainz' and not plan['extra_files'] and
                            not plan['missing_slots'] and len(plan['pairs']) == len(files)
                            and all(pair['confidence'] in ('clear', 'manual') for pair in plan['pairs'])):
                        consistency_review.discard('musicbrainz_release_id')
                    consistent_album_title = (next(
                        (str((member.get('tags') or {}).get('album') or '').strip()
                         for member in files if str(
                             (member.get('tags') or {}).get('album') or '').strip()), '')
                        if 'album' not in consistency else '')
                    release_title = (release_meta.get('name') or release_meta.get('title') or
                                     (album.get('album_title') if album else '') or
                                     os.path.basename(folder))
                    library_album_title = album.get('album_title') if album else ''
                    display_release_title = display._display_album_title(
                        files, library_album_title, release_title,
                        settings['album_title_policy'])
                    release_artists = release_meta.get('artists') or []
                    release_artist_names = [
                        artist.get('name') if isinstance(artist, dict) else str(artist)
                        for artist in release_artists
                        if ((isinstance(artist, dict) and artist.get('name')) or
                            (not isinstance(artist, dict) and str(artist).strip()))
                    ]
                    release_artist = (' & '.join(release_artist_names) if release_artist_names
                                      else (album.get('artist_name') if album else ''))
                    common = {
                        'album_id': album_id, 'album_title': display_release_title,
                        'library_album_title': library_album_title,
                        'current_release': {'source': album.get('canonical_source') or '',
                            'release_id': str(album.get('canonical_album_id') or ''),
                            'locked': bool(album.get('canonical_locked'))} if album else None,
                        'artist_name': release_artist or (album.get('artist_name') if album else ''),
                        'release_artist_names': release_artist_names,
                        'album_consistency': consistency,
                        'release_disambiguation': release_meta.get('disambiguation') or '',
                        'profile_id': album.get('owner_profile_id') if album else 1,
                        'album_folder': folder,
                        'library_root': next((root for root in sorted(roots, key=len, reverse=True) if os.path.commonpath([folder, root]) == root), ''),
                        'album_folders': sorted({os.path.dirname(file['path']) for file in files}),
                        'source': source, 'release_id': release_id,
                        'numbering_mode': numbering_mode,
                        'exact_release_id': exact_release_id,
                        'release_track_count': len(slots), 'local_file_count': len(files),
                        'companion_count': sum(bool(file.get('companion_of')) for file in files),
                        'release_disc_count': len({slot.get('disc_number') or 1 for slot in slots}),
                        'folder_audio_path_digest': album_folders_audio_path_digest(
                            [os.path.dirname(file['path']) for file in files]),
                        'evidence_signature': file_evidence._evidence_signature(files, (source, release_id)),
                        'playlist_tracks': [
                            {'file': os.path.basename(file['path']),
                             'title': file['tags'].get('title') or '',
                             'playlists': list(file.get('playlists') or [])}
                            for file in files if file.get('playlists')],
                    }
                    replacement_files = [{
                        'track_id': str(member['db']['id']), 'file_path': member['path'],
                        'title': member['tags'].get('title') or os.path.basename(member['path']),
                        'artist': member['tags'].get('artist') or '',
                        'duration_ms': member['tags'].get('duration_ms') or 0,
                        'size_bytes': member['size_bytes'], 'mtime_ns': member['mtime_ns'],
                        'file_identity': member.get('file_identity'),
                        'fingerprint_key': member['acoustid'].get('fingerprint_key') or '',
                    } for index, member in enumerate(files) if index in plan['extra_files'] and member.get('db') and not member.get('companion_of')]
                    assignment_options = [{
                        'key': projection._wanted_slot_key(slot), 'title': slot['title'],
                        'artist': slot.get('artist') or '',
                        'disc_number': slot.get('disc_number') or 1,
                        'track_number': slot.get('track_number'),
                        'display_track_number': slot.get('track_number'),
                        'duration_ms': slot.get('duration_ms') or 0,
                        'preview_url': slot.get('preview_url') or '',
                    } for slot in slots]
                    raw_duplicate_groups = []
                    for extra_index, rivals in plan.get('competing_files', {}).items():
                        indexes = set([extra_index, *rivals])
                        if (file_evidence._same_recording_copies(files, indexes) and
                                all((files[index].get('db') or {}).get('id')
                                    for index in indexes)):
                            overlapping = [group for group in raw_duplicate_groups
                                           if group & indexes]
                            for group in overlapping:
                                indexes.update(group)
                                raw_duplicate_groups.remove(group)
                            raw_duplicate_groups.append(indexes)
                    duplicate_groups = {}
                    duplicate_indexes = set()
                    extra_indexes = set(plan['extra_files'])
                    for indexes in raw_duplicate_groups:
                        if not file_evidence._same_recording_copies(files, list(indexes)):
                            continue
                        leaders = sorted(indexes & extra_indexes)
                        if not leaders:
                            continue
                        duplicate_groups[leaders[0]] = sorted(indexes)
                        duplicate_indexes.update(indexes)
                    album_title_proposed = False
                    # Absolute filenames are compatible only when the complete
                    # source sequence is known; never compress excluded media
                    # or an incomplete source tracklist into new positions.
                    disc_tracks: dict[int, list[int]] = defaultdict(list)
                    for source_slot in slots:
                        disc_tracks[source_slot.get('disc_number') or 1].append(source_slot.get('track_number'))
                    disc_totals = {disc: len(numbers) if all(number is not None for number in numbers)
                                   and sorted(numbers) == list(range(1, len(numbers) + 1)) else 0
                                   for disc, numbers in disc_tracks.items()}
                    absolute_filenames = (len(disc_tracks) > 1 and
                        sorted(disc_tracks) == list(range(1, max(disc_tracks) + 1)) and
                        not any(slot.get('excluded_video_tracks') for slot in slots) and
                        all(disc_totals.values()))
                    positions = [(slot.get('disc_number') or 1, slot.get('track_number')) for slot in slots]
                    routine_numbering = (not plan['extra_files'] and
                        len(set(positions)) == len(slots) and
                        all(disc > 0 and track is not None and track > 0 for disc, track in positions) and
                        all(pair['confidence'] in ('clear', 'manual') and
                            not projection._pair_audio_conflict(files[pair['file_index']], slots[pair['slot_index']], pair)
                            for pair in plan['pairs']))
                    planned_paths = {}
                    for pair in release_selection._review_pairs(files, plan):
                        member, target_slot = files[pair['file_index']], slots[pair['slot_index']]
                        absolute_number = (sum(total for disc, total in disc_totals.items()
                            if disc < target_slot['disc_number']) + target_slot['track_number']
                            if absolute_filenames else None)
                        planned_paths[member['path']] = (display._suggest_filename(member['path'], target_slot,
                            absolute_number=absolute_number) if member.get('db') else '')
                    target_counts = Counter(path for path in planned_paths.values() if path)
                    tool_evidence.compare(files, slots, plan, source)
                    plan_action_changes: dict[str, dict] = {}
                    for pair in release_selection._review_pairs(files, plan):
                        file = files[pair['file_index']]
                        slot = slots[pair['slot_index']]
                        tags = file['tags']
                        if (pair['confidence'] not in ('clear', 'manual') or
                                projection._pair_audio_conflict(file, slot, pair)):
                            competing_paths = [files[extra_index]['path']
                                               for extra_index, rivals in
                                               plan.get('competing_files', {}).items()
                                               if pair['file_index'] in rivals]
                            if (pair['file_index'] in duplicate_indexes and
                                    pair.get('ambiguity_reasons') == [
                                        'Another local file fits this release track']):
                                # The duplicate decision below owns both copies;
                                # emitting a second "confirm this match" card makes
                                # one problem look like two unrelated problems.
                                continue
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_unresolved',
                                severity='info', entity_type='track' if file.get('db') else 'file',
                                entity_id=str(file['db']['id']) if file.get('db') else file['path'],
                                file_path=file['path'], title=f'Confirm: {slot["title"]}',
                                description=('The fingerprint identifies audio outside this release slot.'
                                             if projection._pair_audio_conflict(file, slot, pair) else
                                             'This file fits a release slot, but the evidence is not decisive.'),
                                details={**common, 'current_tags': tags, 'acoustid': file['acoustid'],
                                         'tool_checks': file.get('tool_checks') or [],
                                         'audio_conflict': ('different_recording'
                                                            if projection._pair_audio_conflict(file, slot, pair)
                                                            else ''),
                                         'fingerprint_key': file['acoustid'].get('fingerprint_key'),
                                         'expected_title': slot['title'], 'match_reasons': pair['reasons'], 'pairing_checks': pair.get('checks') or [],
                                         'expected_artist': slot.get('artist') or common['artist_name'],
                                         'expected_artist_names': slot.get('artist_names') or [],
                                         'recording_mbid': slot.get('recording_mbid') or '',
                                         'file_duration_ms': tags.get('duration_ms') or 0,
                                         'expected_duration_ms': slot.get('duration_ms') or 0,
                                         'track_id': file['db']['id'] if file.get('db') else None,
                                         'replacement_available': bool(slot.get('source_track_id')),
                                         'replacement_track': {
                                             'name': slot['title'],
                                             'artist': slot.get('artist') or common['artist_name'],
                                             'track_number': slot.get('track_number'),
                                             'disc_number': slot.get('disc_number') or 1,
                                             'duration_ms': slot.get('duration_ms') or 0,
                                             'source_track_id': slot.get('source_track_id'),
                                         },
                                         'expected_preview_url': slot.get('preview_url') or '',
                                         'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
                                         'competitors': pair.get('competitors') or [],
                                         'catalogue_disagreement': pair.get('catalogue_disagreement') or False,
                                         'competing_copies': competing_paths,
                                         'expected_recording_disambiguation':
                                         slot.get('recording_disambiguation') or '',
                                         'current_recording_disambiguation':
                                         (file.get('db') or {}).get('recording_disambiguation') or '',
                                         'confidence': pair['confidence'],
                                         'assignment_options': assignment_options, 'replacement_files': [member for member in replacement_files if member['file_path'] != file['path']],
                                         'size_bytes': file['size_bytes'],
                                         'mtime_ns': file['mtime_ns'],
                                         'file_identity': file.get('file_identity'),
                                         'playlists': list(file.get('playlists') or [])}):
                                result.findings_created += 1
                            continue
                        target = {'name': slot['title'],
                                  'track_number': slot['track_number'],
                                  'disc_number': slot['disc_number'],
                                  'artists': [{'name': name}
                                              for name in slot.get('artist_names') or
                                              ([slot['artist']] if slot['artist'] else [])]}
                        current_album_title = tags.get('album') or ''
                        preserved_album_title = (current_album_title if current_album_title.strip()
                                                 else consistent_album_title)
                        metadata_album_title = (preserved_album_title
                            if settings['album_title_policy'] == 'preserve' and
                            'album' not in consistency and
                            display._album_title_preservable(preserved_album_title, release_title)
                            else common['album_title'])
                        # MusicBrainz exposes the individual artists but not its
                        # join phrases in this adapter. Keep a complete local
                        # collaboration credit rather than reducing it to one
                        # artist or inventing punctuation.
                        current_album_artist = (tags.get('album_artist') or
                                                tags.get('artist') or '')
                        metadata_artist = (current_album_artist
                            if current_album_artist and (
                                len(release_artist_names) > 1 or
                                _artist_credit_includes(current_album_artist,
                                                        common['artist_name']))
                            else common['artist_name'])
                        meta = {'name': metadata_album_title, 'artists':
                                [{'name': metadata_artist}] if metadata_artist else [],
                                # Track totals describe this disc, not the sum
                                # of every medium in a multidisc release.
                                'total_tracks': disc_totals[slot['disc_number']]
                                if not slot.get('excluded_video_tracks') else 0}
                        tag_plan = plan_track(tags, target, meta)
                        expected_discs = max(int(member.get('disc_number') or 1) for member in slots)
                        if tags.get('total_discs') and tags['total_discs'] != expected_discs:
                            tag_plan['changes']['total_discs'] = {'old': tags['total_discs'], 'new': expected_discs}
                            tag_plan['db_data']['total_discs'] = expected_discs
                            tag_plan['db_data']['disc_number'] = slot['disc_number']
                        # Minor title spelling/version annotations are often valid;
                        # only act on a substantive title difference or hard slot
                        # fields. Album/artist edits require an explicit source.
                        changed = tag_plan['changes']
                        if (settings['album_title_policy'] == 'preserve' and 'album' not in consistency
                                and display._album_title_preservable(current_album_title, release_title)):
                            changed.pop('album', None)
                            tag_plan['db_data'].pop('album_title', None)
                        title_kind = title_difference_kind(tags.get('title') or '', slot)
                        if display_title_compatible(tags.get('title') or '', slot):
                            changed.pop('title', None)
                            tag_plan['db_data'].pop('title', None)
                        recording = confirmed_recording(file, slot, source)
                        confirmed_mbid = recording.get('mbid') or ''
                        id_conflict = (recording_id_conflict(file, slot, context.mb_client, recording_checks)
                                       if not confirmed_mbid and source != 'musicbrainz' else {})
                        confirmed_disambiguation = (
                            (slot.get('recording_disambiguation') if source == 'musicbrainz'
                             else recording.get('disambiguation')) or ''
                            if confirmed_mbid else ''
                        )
                        if confirmed_mbid and tags.get('mbid') != confirmed_mbid:
                            changed['musicbrainz_recording_id'] = {
                                'old': tags.get('mbid') or '', 'new': confirmed_mbid}
                            tag_plan['db_data']['musicbrainz_recording_id'] = confirmed_mbid
                        # A database album assignment alone cannot unite the album
                        # in a player that groups by the embedded release identity.
                        if identity_target and tags.get('musicbrainz_release_id') != identity_target:
                            changed['musicbrainz_release_id'] = {
                                'old': tags.get('musicbrainz_release_id') or '', 'new': identity_target}
                            tag_plan['db_data']['musicbrainz_release_id'] = identity_target
                        elif (source != 'musicbrainz' and 'musicbrainz_release_id' in consistency
                              and tags.get('musicbrainz_release_id')):
                            changed['musicbrainz_release_id'] = {
                                'old': tags['musicbrainz_release_id'], 'new': ''}
                            tag_plan['db_data']['clear_musicbrainz_ids'] = ['musicbrainz_release_id']
                        db_updates = {}
                        row = file.get('db') or {}
                        if id_conflict:
                            changed['musicbrainz_recording_id'] = {'old': tags['mbid'], 'new': ''}
                            tag_plan['db_data'].setdefault('clear_musicbrainz_ids', []).append('musicbrainz_recording_id')
                            if row.get('musicbrainz_recording_id') == tags['mbid']:
                                db_updates['musicbrainz_recording_id'] = None
                                changed['library_recording_mbid'] = {'old': tags['mbid'], 'new': ''}
                        if (row and not display_title_compatible(
                                row.get('title') or '', slot)):
                            db_updates['title'] = slot['title']
                            changed['library_title'] = {
                                'old': row.get('title') or '', 'new': slot['title']}
                        for field in ('track_number', 'disc_number'):
                            if (row and slot.get(field) is not None and
                                    row.get(field) != slot[field]):
                                db_updates[field] = slot[field]
                                changed[f'library_{field}'] = {
                                    'old': str(row.get(field) or ''),
                                    'new': str(slot[field])}
                        target_track_artist = tag_plan['db_data'].get('track_artist')
                        if (row and target_track_artist and
                                row.get('track_artist') != target_track_artist):
                            db_updates['track_artist'] = target_track_artist
                            changed['library_track_artist'] = {
                                'old': row.get('track_artist') or '',
                                'new': target_track_artist,
                            }
                        if (row and trusted_anchor_id is not None and
                                row.get('album_id') != album_id):
                            db_updates['album_id'] = album_id
                            changed['library_album_assignment'] = {
                                'old': str(row.get('album_id') or ''),
                                'new': str(album_id),
                                'old_label': ' · '.join(filter(None, (
                                    (albums.get(row.get('album_id')) or {}).get('album_title'),
                                    (albums.get(row.get('album_id')) or {}).get('artist_name')))) + ' (separate library entry)',
                                'new_label': f"{common['album_title']} · {common['artist_name']} (reviewed album)",
                            }
                        if (row and confirmed_mbid and
                                row.get('musicbrainz_recording_id') != confirmed_mbid):
                            db_updates['musicbrainz_recording_id'] = confirmed_mbid
                            changed['library_recording_mbid'] = {
                                'old': row.get('musicbrainz_recording_id') or '',
                                'new': confirmed_mbid}
                        if row and confirmed_mbid:
                            current_disambiguation = row.get('recording_disambiguation') or ''
                            next_disambiguation = confirmed_disambiguation or None
                            mbid_changed = row.get('musicbrainz_recording_id') != confirmed_mbid
                            if ((confirmed_disambiguation and
                                 current_disambiguation != confirmed_disambiguation) or
                                    (mbid_changed and current_disambiguation)):
                                db_updates['recording_disambiguation'] = next_disambiguation
                                changed['library_recording_disambiguation'] = {
                                    'old': current_disambiguation,
                                    'new': confirmed_disambiguation,
                                }
                        target_path = planned_paths[file['path']]
                        filename_conflict = bool(target_path and
                            (os.path.exists(target_path) or target_counts[target_path] > 1))
                        if target_path:
                            changed['filename'] = {'old': os.path.basename(file['path']),
                                                   'new': os.path.basename(target_path)}
                        db_album_title = ''
                        if (album and file.get('db') and not album_title_proposed and
                                album.get('album_title') != display_release_title):
                            db_album_title = display_release_title
                            changed['library_album_title'] = {
                                'old': album.get('album_title') or '',
                                'new': display_release_title}
                        if set(changed) == {'library_recording_disambiguation'}:
                            # This internal evidence improves later comparisons,
                            # but does not warrant a review decision by itself.
                            continue
                        if not changed:
                            continue
                        if row.get('metadata_locked'):
                            plan_action_changes[file['path']] = {
                                'changes': dict(changed), 'status': 'uncertain',
                            }
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_unresolved',
                                severity='info', entity_type='track',
                                entity_id=str(file['db']['id']), file_path=file['path'],
                                title=f'Locked metadata: {slot["title"]}',
                                description='The file matches this release slot, but its metadata is locked.',
                                details={**common, 'current_tags': tags, 'acoustid': file['acoustid'],
                                         'changes': changed, 'expected_title': slot['title']}):
                                result.findings_created += 1
                            continue
                        plan_action_changes[file['path']] = {'changes': dict(changed)}
                        if db_album_title:
                            album_title_proposed = True
                        details = {**common, 'track_id': row.get('id'),
                                   'tool_checks': file.get('tool_checks') or [],
                                   'assignment_evidence': assignment_evidence(file, pair),
                                   'metadata_review_reason': (
                                       'The proposed filename conflicts with another file; review the rename'
                                       if filename_conflict else
                                       id_conflict.get('reason') or
                                       'Choose consistent embedded album metadata for this album'
                                       if id_conflict or consistency_review.intersection(changed) else ''),
                                   'recording_id_conflict': id_conflict,
                                   'recording_identity': recording,
                                   'replacement_track': {'name': slot['title'], 'artist': slot.get('artist'),
                                       'source_track_id': slot.get('source_track_id'), 'disc_number': slot.get('disc_number') or 1,
                                       'track_number': slot.get('track_number'), 'duration_ms': slot.get('duration_ms') or 0},
                                   'expected_title': slot['title'],
                                   'expected_artist': slot['artist'],
                                   'expected_artist_names': slot.get('artist_names') or [],
                                   'artist_aliases': slot.get('artist_aliases') or [],
                                   'expected_duration_ms': slot['duration_ms'],
                                   'recording_mbid': slot['recording_mbid'],
                                   'expected_recording_disambiguation':
                                   slot.get('recording_disambiguation') or '',
                                   'current_recording_disambiguation':
                                   row.get('recording_disambiguation') or '',
                                   'current_title': tags.get('title'),
                                   'current_album': tags.get('album'),
                                   'current_tags': tags, 'acoustid': file['acoustid'],
                                   'current_library_metadata': {
                                       key: row.get(key) for key in
                                       ('title', 'track_number', 'disc_number',
                                        'musicbrainz_recording_id',
                                        'recording_disambiguation', 'track_artist',
                                        'album_id')},
                                   'fingerprint_key': file['acoustid'].get('fingerprint_key'),
                                   'size_bytes': file['size_bytes'], 'mtime_ns': file['mtime_ns'],
                                   'file_identity': file.get('file_identity'),
                                   'match_reasons': pair['reasons'], 'pairing_checks': pair.get('checks') or [],
                                   'confidence': pair['confidence'],
                                   'routine_numbering': routine_numbering and not filename_conflict,
                                   'numbering_recovery': review_choices.recovers_flattened_numbering(
                                       numbering_history, file, album_id=album_id,
                                       source=source, release_id=release_id),
                                   'title_difference_kind': title_kind,
                                   'assignment_options': assignment_options, 'replacement_files': [member for member in replacement_files if member['file_path'] != file['path']],
                                   'saved_assignment': file['path'] in {files[i]['path'] for i in forced_pairs},
                                   'changes': changed, 'db_data': tag_plan['db_data'],
                                   'db_track_updates': db_updates,
                                   'db_album_title': db_album_title,
                                   'target_path': target_path}
                        if not row:
                            details['file_only'] = True
                        if review_choices.retains_numbering(kept_numbering.get(file['path']), details):
                            projected = review_choices.project(details, ('', json.dumps(details), 'numbering', 'dismiss', ''))
                            for key in ('planned_choice', 'plan_keep', 'numbering_choice'):
                                projected.pop(key, None)
                            details = projected
                            changed = details['changes']
                            plan_action_changes[file['path']] = {'changes': dict(changed),
                                'status': 'change' if changed else 'confirmed'}
                            if not changed:
                                continue
                        if emit(
                            job_id=self.job_id, finding_type='reconciliation_metadata',
                            severity='warning', entity_type='track' if row else 'file',
                            entity_id=str(row['id']) if row else file['path'], file_path=file['path'],
                            title=(f'Rename file: {slot["title"]}'
                                   if set(changed) == {'filename'}
                                   else f'Update metadata: {slot["title"]}'),
                            description=(details['metadata_review_reason'] or
                                ('Audio and release evidence support these metadata corrections.'
                                 if details['assignment_evidence'] == 'audio' else
                                 'The assignment was confirmed during review.'
                                 if details['assignment_evidence'] == 'user' else
                                 'Metadata agrees with the release; audio identity is not independently confirmed.')),
                            details=details):
                            result.findings_created += 1

                    missing = [{**slots[i], 'wanted_key': projection._wanted_slot_key(slots[i])}
                               for i in plan['missing_slots']]
                    # Kept files still occupy their release slots while another
                    # copy is requested. Preserve that acquisition without
                    # presenting those occupied slots as missing from coverage.
                    from core.reconciliation.fulfillment import (
                        assignment_fulfills_request,
                    )
                    replacement_requests = getattr(self, '_replacement_requests', {}).get(
                        (review_case_key(common, None), source, str(exact_release_id)), [])
                    replacement_by_id = {request['source_track_id']: request for request in replacement_requests}
                    occupied_requests = []
                    if replacement_requests:
                        request_assignments, _ = projection._album_plan_assignments(files, slots, plan)
                        for slot in slots:
                            request = replacement_by_id.get(str(slot.get('source_track_id') or ''))
                            if (request and slot not in [slots[i] for i in plan['missing_slots']] and
                                    not any(assignment_fulfills_request(request, row) for row in request_assignments)):
                                occupied_requests.append(slot)
                    for file_index in plan['extra_files']:
                        if file_index in duplicate_indexes and file_index not in duplicate_groups:
                            continue
                        file = files[file_index]
                        audio = file['acoustid']
                        recordings = audio.get('recordings') or []
                        best = max(recordings, key=lambda r: r.get('score') or 0) if recordings else {}
                        competing_paths = [files[index]['path'] for index in
                                           plan.get('competing_files', {}).get(file_index, [])]
                        same_audio_paths = [files[pair['file_index']]['path'] for pair in plan['pairs']
                                            if audio.get('fingerprint_key') and
                                            files[pair['file_index']]['acoustid'].get('fingerprint_key') ==
                                            audio['fingerprint_key']]
                        competing_paths = list(dict.fromkeys(competing_paths + same_audio_paths))
                        duplicate_indexes = duplicate_groups.get(file_index) or []
                        duplicate_copies = [file_evidence._duplicate_copy(files[index])
                                            for index in duplicate_indexes]
                        recommended_copy = pick_duplicate_to_keep(duplicate_copies) or {}
                        different_recording = audio_contradicts_local_title(file)
                        identified = (audio.get('status') == 'ok' and
                                      float(best.get('score') or 0) >= 0.80 and
                                      not fingerprint_is_ambiguous(recordings) and
                                      not competing_paths)
                        replacement_track = None
                        version_conflict = False
                        if different_recording and album_id is not None and file.get('db'):
                            local_key = semantic_title_key({
                                'title': file['tags'].get('title') or file['db'].get('title') or '',
                            })
                            matching_slots = [
                                slot for slot in missing
                                if local_key[0] and semantic_title_key(slot) == local_key
                                and slot.get('source_track_id')
                            ]
                            if len(matching_slots) == 1:
                                slot = matching_slots[0]
                                replacement_track = {
                                    'name': slot['title'],
                                    'artist': slot.get('artist') or common['artist_name'],
                                    'track_number': slot.get('track_number'),
                                    'disc_number': slot.get('disc_number') or 1,
                                    'duration_ms': slot.get('duration_ms') or 0,
                                    'source_track_id': slot['source_track_id'],
                                }
                        if not different_recording and not duplicate_copies:
                            local_key = semantic_title_key({
                                'title': file['tags'].get('title') or
                                         (file.get('db') or {}).get('title') or '',
                            })
                            version_matches = [slot for slot in missing
                                               if local_key[0] and
                                               semantic_title_key(slot)[0] == local_key[0]]
                            if len(version_matches) == 1:
                                version_conflict = True
                                slot = version_matches[0]
                                if slot.get('source_track_id'):
                                    replacement_track = {
                                        'name': slot['title'],
                                        'artist': slot.get('artist') or common['artist_name'],
                                        'track_number': slot.get('track_number'),
                                        'disc_number': slot.get('disc_number') or 1,
                                        'duration_ms': slot.get('duration_ms') or 0,
                                        'source_track_id': slot['source_track_id'],
                                    }
                        duplicate_slot = next((slots[pair['slot_index']] for pair in plan['pairs']
                            if pair['file_index'] in duplicate_indexes), {}) if duplicate_copies else {}
                        details = {**common, 'current_title': file['tags'].get('title'),
                                   'tool_checks': file.get('tool_checks') or [],
                                   'current_tags': file['tags'], 'acoustid': audio,
                                   'file_duration_ms': file['tags'].get('duration_ms') or 0,
                                   'fingerprint_key': audio.get('fingerprint_key'),
                                   'size_bytes': file['size_bytes'], 'mtime_ns': file['mtime_ns'],
                                   'file_identity': file.get('file_identity'),
                                   'track_id': file['db']['id'] if file.get('db') else None,
                                   'competing_copies': competing_paths,
                                   'same_audio_copies': same_audio_paths,
                                   'duplicate_copies': duplicate_copies,
                                   **({'expected_title': duplicate_slot.get('title'),
                                       'expected_duration_ms': duplicate_slot.get('duration_ms'),
                                       'expected_track_number': duplicate_slot.get('track_number'),
                                       'expected_disc_number': duplicate_slot.get('disc_number')}
                                      if duplicate_slot else {}),
                                   'recommended_keeper': str(recommended_copy.get('track_id') or ''),
                                   'keeper_reason': ('Preserves playlist membership; quality breaks ties'
                                       if recommended_copy.get('playlists') else
                                       'Highest format and bitrate rank among these copies'),
                                   'resolution_kind': ('duplicate_copies'
                                                       if duplicate_copies else ''),
                                   'tracks': duplicate_copies,
                                   'playlists': list(file.get('playlists') or []),
                                   'acoustid_title': best.get('title'),
                                   'acoustid_artist': best.get('artist'),
                                   'acoustid_score': best.get('score'),
                                   'audio_conflict': ('different_recording'
                                                      if different_recording else ''),
                                   'version_conflict': version_conflict,
                                   'expected_artist': (replacement_track or {}).get('artist') or '',
                                   'replacement_track': replacement_track}
                        details['assignment_options'] = assignment_options
                        details['replacement_files'] = [member for member in replacement_files if member['file_path'] != file['path']]
                        if emit(
                            job_id=self.job_id,
                            finding_type=('reconciliation_duplicate' if duplicate_copies else
                                          'reconciliation_extra'
                                          if identified or different_recording else
                                          'reconciliation_unresolved'),
                            severity=('warning'
                                      if identified or different_recording or duplicate_copies
                                      else 'info'),
                            entity_type='track' if file.get('db') else 'file',
                            entity_id=str(file['db']['id']) if file.get('db') else file['path'],
                            file_path=file['path'],
                            title=((f'Duplicate copies: {best.get("title") or file["tags"].get("title")}'
                                   if duplicate_copies else
                                   f'Wrong audio: {file["tags"].get("title") or os.path.basename(file["path"])}'
                                   if different_recording else
                                   f'Unassigned audio: {file["tags"].get("title") or os.path.basename(file["path"])}')),
                            description=('These files identify as the same release track; choose a keeper.'
                                         if duplicate_copies else
                                         'Another file also fits this release slot; choose a keeper.'
                                         if competing_paths else
                                         'The fingerprint identifies different recordings than the file title.'
                                         if different_recording else
                                         'Audio does not fit a slot on the selected release.'
                                         if identified else 'Audio identity could not be established.'),
                            details=details):
                            result.findings_created += 1

                    saved_intent = intents.get(str(album_id)) if album_id is not None else None
                    intent_scope = (saved_intent[0] if saved_intent and
                                    saved_intent[0] in ('complete', 'partial') and
                                    saved_intent[1:3] == (source, release_id) else '')
                    wanted_slots = (set(saved_intent[3]) if saved_intent and saved_intent[1:3] == (source, release_id) else set())
                    wishlisted_slots = wishlisted_by_profile.get(
                        int(common.get('profile_id') or 1), set())
                    assignment_settled = (
                        not plan['extra_files'] and
                        all(pair['confidence'] in ('clear', 'manual') and
                            not projection._pair_audio_conflict(files[pair['file_index']], slots[pair['slot_index']], pair)
                            for pair in plan['pairs'])
                    )
                    intent_inferred = bool(missing) and assignment_settled and release_selection._inferred_partial(
                        files, album, watched_artists, saved_intent)
                    if intent_inferred:
                        intent_scope = 'partial'
                    # A requested slot is already decided. Asking about it again
                    # must not depend on a broader whole-album scope choice.
                    undecided_missing = [slot for slot in missing
                        if projection._wanted_slot_key(slot) not in wanted_slots and
                        (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) not in wishlisted_slots]
                    reconsidering = bool(saved_intent and saved_intent[0] == 'review' and
                                         saved_intent[1:3] == (source, release_id))
                    if missing and not undecided_missing and not intent_scope and not reconsidering:
                        intent_scope = 'partial'
                        wanted_slots.update(projection._wanted_slot_key(slot) for slot in missing)
                    if (missing or occupied_requests) and album_id is not None:
                        if not intent_scope and (undecided_missing or reconsidering):
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_intent',
                                severity='info', entity_type='album', entity_id=str(album_id),
                                file_path=None, title=f'Is {common["album_title"]} meant to be complete?',
                                description=f'{len(undecided_missing)} release track(s) need a collection choice.',
                                details={**common, 'missing_tracks': [
                                    {**slot, 'requested': (source, str(slot.get('source_track_id') or ''),
                                                          str(exact_release_id)) in wishlisted_slots}
                                    for slot in (missing if reconsidering else undecided_missing)],
                                         'wanted_slots': sorted(wanted_slots),
                                         'suggested_scope': 'partial' if len(files) == 1 and files[0].get('playlists') else 'complete'}):
                                result.findings_created += 1
                        for slot in [*missing, *occupied_requests]:
                            key = missing_slot_key(album_id, source, release_id, slot)
                            replacement_request = replacement_by_id.get(str(slot.get('source_track_id') or ''))
                            fulfillment_state = (
                                'wishlisted' if
                                (source, str(slot.get('source_track_id') or ''),
                                 str(exact_release_id)) in wishlisted_slots else '')
                            wanted = intent_scope == 'complete' or projection._wanted_slot_key(slot) in wanted_slots
                            if intent_scope == 'discard' or (not wanted and not fulfillment_state):
                                continue
                            if (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) in getattr(self, '_published_by_profile', {}).get(int(common.get('profile_id') or 1), set()):
                                fulfillment_state = 'downloaded'
                            if not fulfillment_state and (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) in getattr(self, '_cancelled_by_profile', {}).get(int(common.get('profile_id') or 1), set()):
                                fulfillment_state = 'cancelled'
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_missing',
                                severity='info', entity_type='album_slot', entity_id=key,
                                file_path=None, title=f'{"Requested copy" if replacement_request else "Missing"}: {slot["title"]}',
                                description=('Acquisition was cancelled. Request this track again or deliberately change the album completeness choice.' if fulfillment_state == 'cancelled' else
                                             'Another copy has been requested. The current file is kept while the new copy is acquired and checked.' if slot in occupied_requests else
                                             'This track is absent from the intended complete release.'),
                                details={**common, 'fulfillment_state': fulfillment_state,
                                         **({'replacement_request': True} if replacement_request else {}),
                                         'missing_tracks': [{
                                    'name': slot['title'], 'track_number': slot['track_number'],
                                    'disc_number': slot['disc_number'],
                                    'source_track_id': slot['source_track_id'],
                                    'source': source, 'duration_ms': slot['duration_ms'],
                                }] }):
                                result.findings_created += 1
                    case_id = projection._persist_selected_plan(
                        context.db, common, files, scored[0], inventory=album_inventory, candidates=scored, recording_checks=recording_checks,
                        candidate_metadata={
                            (row.source, row.release_id): release_selection._release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases)
                            for row in scored[:6]
                        },
                        intent_scope=intent_scope, action_changes=plan_action_changes,
                        intent_inferred=intent_inferred,
                        numbering_mode=numbering_mode, wanted_slots=wanted_slots,
                        wishlisted_slots=wishlisted_slots,
                    )
                    if case_id:
                        observed_plans.add(case_id)
                    else:
                        plans_complete = False
                    cache.checkpoint()
                    if context.update_progress:
                        context.update_progress(folder_index + 1, len(folders))
            if completed and not catalogue_current.get().failed and not context.check_stop() and context.create_finding:
                tool_evidence.finish()
                if relocated_from and plans_complete and len(observed_plans) == 1:
                    from core.reconciliation.redirects import record_relocation
                    record_relocation(context.db, relocated_from, next(iter(observed_plans)))
                cache.checkpoint()
                if target_folder is None:
                    _retire_stale_findings(context.db, observed)
                    if plans_complete:
                        from core.reconciliation.redirects import (
                            record_observed_relocations,
                        )
                        record_observed_relocations(context.db, observed_plans)
                        retire_unseen_plans(context.db, observed_plans)
                else:
                    for member_folder in target_member_folders:
                        _retire_stale_findings(context.db, observed, member_folder)
                        if plans_complete:
                            retire_unseen_plans(context.db, observed_plans,
                                                target_folder=member_folder)
        finally:
            elapsed_seconds = time.perf_counter() - scan_started
            cache.metrics['inventory_scope'] = inventory_request.scope.value
            cache.metrics['inventory_seconds'] = round(inventory_seconds, 3)
            cache.metrics['elapsed_seconds'] = round(elapsed_seconds, 3)
            catalogue_current.get().evidence_metrics = dict(cache.metrics)
            logger.info('Library reconciliation: %d files, %d findings, %d fingerprints, '
                        '%d fingerprint hits, %d lookups, %d lookup hits, %d tag reads, '
                        '%d tag hits, %d releases in %.1fs (inventory %.1fs, fingerprint %.1fs, '
                        'lookup %.1fs, tags %.1fs)',
                        result.scanned, result.findings_created + catalogue_current.get().created,
                        cache.metrics['fingerprints'], cache.metrics['fingerprint_hits'],
                        cache.metrics['lookups'], cache.metrics['lookup_hits'],
                        cache.metrics['tag_reads'], cache.metrics['tag_hits'],
                        len(release_cache), elapsed_seconds, inventory_seconds,
                        cache.metrics['fingerprint_seconds'], cache.metrics['lookup_seconds'],
                        cache.metrics['tag_seconds'])
            cache.close()
        if context.report_progress:
            context.report_progress(log_line=f'Reconciliation cache: {cache.metrics}')
        return result
