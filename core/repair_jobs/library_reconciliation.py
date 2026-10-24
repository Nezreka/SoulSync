"""Folder-first audio identity audit feeding the album review queue.

Opt-in and read-only: the scan records evidence-backed proposals. Fix handlers
recheck the file before any tag, path, or wishlist write. No album is declared
complete from folder layout alone; missing slots need explicit intent.
"""

from __future__ import annotations

import os
import re
import hashlib
import json
import time
from collections import Counter, defaultdict
from functools import partial

from core.auto_import_worker import _read_file_tags
from core.imports.staging import AUDIO_EXTENSIONS
from core.library.path_resolver import resolve_library_file_path
from core.library.duplicate_keep import pick_duplicate_to_keep
from core.library.duplicate_rules import is_lossy_companion_file, is_lossy_companion_pair, lossy_companion_exts
from core.library.reconciliation_candidates import ScoredCandidate, assignment_evidence, pair_display_fields
from core.library_reorganize import _safe_filename
from core.library.reconciliation_inventory import (
    ScanScope, InventoryRequest, load_inventory_request, track_selection,
    known_membership_agrees, discovery_roots, inventory_record, directory_observations,
)
from core.library.reconciliation_evidence import (
    ReconciliationEvidence, album_folders_audio_path_digest, file_identity,
)
from core.library.reconciliation_plan import (
    audio_contradicts_local_title, reconcile_folder, _title_score,
    display_title_compatible, file_version_markers, semantic_title_key,
    title_difference_kind, slot_version_markers, _version_markers,
    recording_supports_file_slot,
)
from core.library.reconciliation_catalogue import catalogue_scan, current as catalogue_current, deferred, timed, stage
from core.library.reconciliation_match_cache import cached_match
from core.library.reconciliation_metadata import album_consistency, confirmed_recording, recording_id_conflict
from core.library.reconciliation_tools import ToolEvidence
from core.library.reconciliation_store import persist_album_plan, retire_unseen_plans
from core.library.retag_planner import plan_track, _artist_credit_includes
from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.metadata.album_tracks import get_album_for_source, get_album_tracks_for_source
from core.repair_jobs import register_job
from core.repair_jobs.base import RepairJob, JobContext, JobResult, all_library_roots, walk_library, watchlist_artist_names
from core.repair_review_identity import missing_slot_key
from core.text.fold import fold_title
from utils.logging_config import get_logger

logger = get_logger('repair_job.library_reconciliation')


def _read_reconciliation_tags(path):
    from core.library.release_identity import read_release_identity
    tags = dict(_read_file_tags(path))
    tags['musicbrainz_release_id'], tags['release_disambiguation'] = read_release_identity(path)
    return tags


_read_reconciliation_tags.required_cache_keys = ('musicbrainz_release_id',)


def _release_identity_target(files, album, source, release_id):
    """Unify inconsistent file identity or honor an explicitly pinned edition.

    An automatically selected catalogue-equivalent pressing is not evidence
    that a coherent set of embedded release IDs needs rewriting.
    """
    if source != 'musicbrainz':
        return ''
    identities = {str((file.get('tags') or {}).get('musicbrainz_release_id') or '').strip()
                  for file in files} - {''}
    # A missing tag is not evidence against the coherent existing pressing.
    # Fill it only when that identity agrees with the selected release, or the
    # user has explicitly pinned the target edition.
    return release_id if (len(identities) > 1 or identities == {release_id}
                          or (album or {}).get('canonical_locked')) else ''

_SOURCES = {
    'musicbrainz_release_id': 'musicbrainz',
    'spotify_album_id': 'spotify',
    'itunes_album_id': 'itunes',
    'deezer_id': 'deezer',
}
_DISC_FOLDER = re.compile(r'^(?:cd|disc|disk|volume|vol)\s*[-_. ]*\d+\b', re.I)
_WATCHLIST_REMOVAL_PREFIX = '.watchlist-removal-'
_ALBUM_EDITION_WORD = re.compile(
    r'\b(?:anniversary|bit|cd|deluxe|edition|expanded|khz|limited|lp|master|mono|'
    r'pressing|reissue|remaster(?:ed)?|special|stereo|target|vinyl)\b', re.I)
_ALBUM_MATERIAL_QUALIFIER = re.compile(
    r"\b(?:acoustic|anniversary|bit|collector(?:s|'s)?|deluxe|demo|edition|expanded|"
    r'instrumental|khz|live|master|mono|remix|reissue|remaster(?:ed)?|special|'
    r'stereo|soundtrack|version)\b', re.I)
_SOUNDTRACK_SUFFIX = re.compile(
    r'\s*[\[(]?(?:original\s+(?:motion\s+picture|television)\s+)?soundtrack[\])]?$|'
    r'\s*[\[(]?music\s+from\s+the\s+(?:motion\s+picture|film)[\])]?$', re.I)
_PRESSING_PLANT_DETAIL = re.compile(
    r'\b(?:EDC\s+Blackburn|EDC\s+Germany|Blackburn\s+Ltd\.?|'
    r'Columbia\s+House|club\s+edition|manufactured\s+by)\b', re.I)


def _clean_album_display_title(title: str) -> str:
    """Drop known manufacturing flavor while preserving a named edition."""
    match = (re.match(r'^(.*?)\s+\(([^()]*)\)\s*$', title.strip()) or
             re.match(r'^(.*?)\s+\[([^\[\]]*)\]\s*$', title.strip()))
    if not match or not _PRESSING_PLANT_DETAIL.search(match.group(2)):
        return title
    parts = [part.strip() for part in re.split(r'[,;/]', match.group(2))]
    kept = [part for part in parts if part and not _PRESSING_PLANT_DETAIL.search(part)]
    return f'{match.group(1)} ({", ".join(kept)})' if kept else match.group(1)


def _suggest_filename(path: str, slot: dict, *, absolute_number: int | None = None) -> str:
    """Keep the file's numbering convention while correcting a clear slot."""
    if not str(slot.get('title') or '').strip():
        return ''
    stem, extension = os.path.splitext(os.path.basename(path))
    from core.text.track_prefix import parse_track_prefix
    from core.imports.paths import sanitize_filename
    parsed = parse_track_prefix(stem)
    if parsed.number is not None:
        prefix = stem[:-len(parsed.remainder)]
        track, disc = int(slot.get('track_number') or 0), int(slot.get('disc_number') or 1)
        # A single-folder album may use absolute filenames with per-disc tags.
        # Count against the complete release, including absent slots.
        compatible = (parsed.number == track and parsed.disc in (None, disc) or
                      parsed.disc is None and parsed.number == absolute_number)
        if not compatible and not parsed.side and track > 0:
            numbers = list(re.finditer(r'\d+', prefix))
            values = ([disc, track] if parsed.disc is not None and len(numbers) == 2
                      else [disc * 100 + track] if parsed.disc is not None else [track])
            if parsed.disc is not None and len(numbers) == 1 and not (disc <= 9 and track < 100):
                # Packed DDTT cannot encode these positions. Preserve the
                # prefix rather than making it look like another disc/track.
                values = []
            for match, value in reversed(list(zip(numbers, values))):
                prefix = prefix[:match.start()] + str(value).zfill(len(match[0])) + prefix[match.end():]
        title = (parsed.remainder if display_title_compatible(parsed.remainder, slot)
                 else sanitize_filename(slot['title']))
        proposed = f'{prefix}{title}{extension}'
    else:
        # Preserve an unrecognized numeric prefix (for example a year). It is
        # not evidence of a track position and must not be rewritten as one.
        unknown = re.match(r'^(\d{4,}\s*[-_.]\s*)(.+)$', stem)
        current = unknown[2] if unknown else stem
        title = (current if display_title_compatible(current, slot)
                 else sanitize_filename(slot['title']))
        proposed = f'{unknown[1] if unknown else ""}{title}{extension}'
    return os.path.join(os.path.dirname(path), proposed) if proposed != os.path.basename(path) else ''


def _group_disc_folders(folders: dict[str, list[str]], tracks: dict,
                        roots: list[str]) -> dict[str, list[str]]:
    """Treat conventional disc subfolders as one release-level folder audit."""
    groups = {folder: list(paths) for folder, paths in folders.items()}
    by_album: dict[object, list[str]] = defaultdict(list)
    for folder, paths in folders.items():
        ids = {tracks[os.path.normpath(path)]['album_id'] for path in paths
               if os.path.normpath(path) in tracks and
               tracks[os.path.normpath(path)].get('album_id') is not None}
        if len(ids) == 1:
            by_album[next(iter(ids))].append(folder)
    normalized_roots = {os.path.normpath(root) for root in roots}
    for album_folders in by_album.values():
        if len(album_folders) < 2:
            continue
        parent = os.path.commonpath(album_folders)
        if (parent in normalized_roots or
                (parent in groups and parent not in album_folders) or not all(
            folder == parent or
            (os.path.dirname(folder) == parent and _DISC_FOLDER.match(os.path.basename(folder)))
            for folder in album_folders
        )):
            continue
        merged = []
        for folder in album_folders:
            merged.extend(groups.pop(folder))
        groups[parent] = merged
    return groups


def _album_identity_title(value: object) -> str:
    """Album title used only to find conservative soundtrack fragments."""
    title = _SOUNDTRACK_SUFFIX.sub('', str(value or '').strip()).strip(' -–—()[]')
    return normalize(title)


def _album_has_release_id(album: dict | None) -> bool:
    if not album:
        return False
    return bool((album.get('canonical_source') and album.get('canonical_album_id')) or
                any(album.get(column) for column in _SOURCES))


def _group_logical_albums(folders: dict[str, list[str]], tracks: dict,
                           albums: dict) -> tuple[dict[str, list[str]], dict[str, object]]:
    """Join DB-backed folder fragments only when one release is authoritative.

    Folders assigned to the same album row are one logical album. A second,
    narrow rule absorbs tiny soundtrack fragments into one large linked
    soundtrack when titles, owner and unused track positions agree. The
    returned mapping marks the only mixed-ID groups the scanner may accept.
    """
    groups = {folder: list(paths) for folder, paths in folders.items()}

    # Lists only grow during this pass. Cache by identity and length, retaining
    # the list to avoid identity reuse after a group is removed.
    group_facts = {}

    def cached_fact(name, paths, compute):
        key = (name, id(paths))
        previous = group_facts.get(key)
        if previous is None or previous[1] != len(paths):
            previous = (paths, len(paths), compute())
            group_facts[key] = previous
        return previous[2]

    def album_ids(paths):
        return cached_fact('albums', paths, lambda: {tracks[os.path.normpath(path)]['album_id'] for path in paths
                if os.path.normpath(path) in tracks and
                tracks[os.path.normpath(path)].get('album_id') is not None})

    by_album: dict[object, list[str]] = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) == 1:
            by_album[next(iter(ids))].append(folder)
    for album_folders in by_album.values():
        if len(album_folders) < 2:
            continue
        representative = min(album_folders,
                             key=lambda folder: (-len(groups[folder]), folder))
        merged = []
        for folder in album_folders:
            merged.extend(groups.pop(folder))
        groups[representative] = merged

    def compute_positions(paths):
        values = set()
        for path in paths:
            row = tracks.get(os.path.normpath(path)) or {}
            try:
                track_number = int(row.get('track_number') or 0)
                disc_number = int(row.get('disc_number') or 1)
            except (TypeError, ValueError):
                return set()
            if track_number <= 0:
                return set()
            values.add((disc_number, track_number))
        return values

    def positions(paths):
        # The soundtrack rule accumulates positions into its own working set.
        return set(cached_fact('positions', paths, lambda: compute_positions(paths)))

    def release_identity(album):
        if album.get('canonical_source') and album.get('canonical_album_id'):
            return album['canonical_source'], str(album['canonical_album_id'])
        for column, source in _SOURCES.items():
            if album.get(column):
                return source, str(album[column])
        return None

    # A box set can be split between files in the release folder and a Disc N
    # child while the media-server scan assigns those two locations different
    # album rows. Join only this physical parent/child shape, with the same
    # normalized album title and at most one authoritative release identity.
    # Position overlap is tolerated only when that release anchor exists:
    # split media-server rows often carry damaged disc numbers, and those are
    # exactly what reconciliation must repair. Without an anchor, positions
    # must remain disjoint to avoid merging coincidentally named copies.
    trusted: dict[str, object] = {}
    # A media-server rescan can split a single physical album into several
    # library rows. Require an exact title/artist/owner and disjoint positions.
    # A shared provider ID can connect rows whose preferred sources differ;
    # conflicting authoritative identities from the same source cannot.
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) < 2:
            continue
        rows = [albums.get(album_id) or {} for album_id in ids]
        identities = {release_identity(row) for row in rows if release_identity(row)}
        shared_catalog_id = any(rows[0].get(column) and
                                all(row.get(column) == rows[0][column] for row in rows)
                                for column in _SOURCES)
        compatible_sources = len({source for source, _ in identities}) == len(identities)
        names = {(fold_title(row.get('album_title') or '', drop_brackets=False),
                  fold_title(row.get('artist_name') or '', drop_brackets=False),
                  row.get('owner_profile_id') or 1) for row in rows}
        if (len(names) == 1 and all(next(iter(names))[:2])
                and (len(identities) == 1 or (shared_catalog_id and compatible_sources))
                and len(positions(paths)) == len(paths)):
            anchors = [album_id for album_id in ids if release_identity(albums.get(album_id) or {})]
            trusted[folder] = min(anchors, key=lambda album_id: (
                not albums[album_id].get('canonical_locked'),
                not albums[album_id].get('canonical_source'),
                not albums[album_id].get('musicbrainz_release_id'), str(album_id)))
    for child in sorted(list(groups)):
        if child not in groups or not _DISC_FOLDER.match(os.path.basename(child)):
            continue
        parent = os.path.dirname(child)
        if parent not in groups:
            continue
        parent_ids, child_ids = album_ids(groups[parent]), album_ids(groups[child])
        if len(parent_ids) != 1 or len(child_ids) != 1 or parent_ids == child_ids:
            continue
        parent_id, child_id = next(iter(parent_ids)), next(iter(child_ids))
        parent_album, child_album = albums.get(parent_id) or {}, albums.get(child_id) or {}
        if (_album_identity_title(parent_album.get('album_title')) !=
                _album_identity_title(child_album.get('album_title'))):
            continue

        identities = {identity for identity in
                      (release_identity(parent_album), release_identity(child_album))
                      if identity}
        if len(identities) > 1:
            continue
        parent_positions, child_positions = positions(groups[parent]), positions(groups[child])
        if (not parent_positions or not child_positions or
                (parent_positions & child_positions and not identities)):
            continue
        anchor_id = (child_id if release_identity(child_album) and
                     not release_identity(parent_album) else parent_id)
        groups[parent].extend(groups.pop(child))
        trusted[parent] = anchor_id

    # Replacement downloads can be imported as a new album row in a sibling
    # folder, sometimes with an extra edition qualifier on the original row.
    # Join only a one-file gap filler to one nearby release-identified anchor.
    def base_title(album):
        return normalize(re.split(r'\s*[\[(]', str(album.get('album_title') or ''), maxsplit=1)[0])

    def sibling_key(folder, album):
        return (os.path.dirname(folder), base_title(album),
                normalize(album.get('artist_name') or ''),
                album.get('owner_profile_id') or 1)

    sibling_groups = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) == 1:
            sibling_groups[sibling_key(folder, albums.get(next(iter(ids))) or {})].append(folder)

    def siblings(folder, album):
        # Preserve original dict order and all ambiguous candidates. Merges
        # only remove keys; existing checks reject groups that became mixed-ID.
        for candidate in sibling_groups.get(sibling_key(folder, album), ()):
            if candidate in groups:
                yield candidate, groups[candidate]

    for fragment in sorted(list(groups)):
        if fragment not in groups or len(groups[fragment]) != 1:
            continue
        ids = album_ids(groups[fragment])
        if len(ids) != 1:
            continue
        fragment_id = next(iter(ids))
        fragment_album = albums.get(fragment_id) or {}
        fragment_positions = positions(groups[fragment])
        if not fragment_positions or not base_title(fragment_album):
            continue
        candidates = []
        for anchor, paths in siblings(fragment, fragment_album):
            if (anchor == fragment or len(paths) < 3 or
                    os.path.dirname(anchor) != os.path.dirname(fragment)):
                continue
            anchor_ids = album_ids(paths)
            if len(anchor_ids) != 1 or fragment_id in anchor_ids:
                continue
            anchor_id = next(iter(anchor_ids))
            anchor_album = albums.get(anchor_id) or {}
            if (not _album_has_release_id(anchor_album) or
                    base_title(anchor_album) != base_title(fragment_album) or
                    normalize(anchor_album.get('artist_name') or '') !=
                    normalize(fragment_album.get('artist_name') or '') or
                    (anchor_album.get('owner_profile_id') or 1) !=
                    (fragment_album.get('owner_profile_id') or 1)):
                continue
            anchor_identity = release_identity(anchor_album)
            fragment_identity = release_identity(fragment_album)
            # Downloaders may tag a replacement track with a different
            # edition's release ID. A shared provider album ID still proves
            # these sibling folders belong to the same album. Require the
            # title, artist, unused position, and one-file checks above too.
            shared_catalog_id = any(
                anchor_album.get(column) and
                anchor_album.get(column) == fragment_album.get(column)
                for column in _SOURCES
            )
            if (fragment_identity and fragment_identity != anchor_identity and
                    not shared_catalog_id):
                continue
            anchor_positions = positions(paths)
            if anchor_positions and not fragment_positions & anchor_positions:
                candidates.append((anchor, anchor_id))
        if len(candidates) == 1:
            anchor, anchor_id = candidates[0]
            groups[anchor].extend(groups.pop(fragment))
            trusted[anchor] = anchor_id

    # A digital deluxe addendum can arrive as a second media-server album:
    # the regular folder holds the opening sequence and the deluxe folder only
    # higher-numbered extras. Join this narrow, position-disjoint shape so the
    # release comparison sees the whole owned collection. The deluxe release
    # remains a review choice; no file is retagged by this grouping step.
    for deluxe_folder in sorted(list(groups)):
        if deluxe_folder not in groups:
            continue
        deluxe_ids = album_ids(groups[deluxe_folder])
        if len(deluxe_ids) != 1 or len(groups[deluxe_folder]) < 2:
            continue
        deluxe_id = next(iter(deluxe_ids))
        deluxe_album = albums.get(deluxe_id) or {}
        title = str(deluxe_album.get('album_title') or '')
        if (not re.search(r'\bdeluxe\b', title, re.I) or
                not _album_has_release_id(deluxe_album)):
            continue
        extra_positions = positions(groups[deluxe_folder])
        if len(extra_positions) != len(groups[deluxe_folder]):
            continue
        candidates = []
        for regular_folder, regular_paths in siblings(deluxe_folder, deluxe_album):
            if (regular_folder == deluxe_folder or len(regular_paths) < 8 or
                    os.path.dirname(regular_folder) != os.path.dirname(deluxe_folder)):
                continue
            regular_ids = album_ids(regular_paths)
            if len(regular_ids) != 1 or deluxe_id in regular_ids:
                continue
            regular_album = albums.get(next(iter(regular_ids))) or {}
            regular_title = str(regular_album.get('album_title') or '')
            if (re.search(r'\bdeluxe\b', regular_title, re.I) or
                    base_title(regular_album) != base_title(deluxe_album) or
                    normalize(regular_album.get('artist_name') or '') !=
                    normalize(deluxe_album.get('artist_name') or '') or
                    (regular_album.get('owner_profile_id') or 1) !=
                    (deluxe_album.get('owner_profile_id') or 1)):
                continue
            regular_positions = positions(regular_paths)
            count = len(regular_paths)
            if (regular_positions == {(1, number) for number in range(1, count + 1)} and
                    extra_positions and
                    all(disc == 1 and count < number <= count * 3
                        for disc, number in extra_positions) and
                    min(number for _, number in extra_positions) == count + 1):
                candidates.append(regular_folder)
        if len(candidates) == 1:
            regular_folder = candidates[0]
            groups[deluxe_folder].extend(groups.pop(regular_folder))
            trusted[deluxe_folder] = deluxe_id

    buckets: dict[tuple[object, str], list[str]] = defaultdict(list)
    for folder, paths in groups.items():
        ids = album_ids(paths)
        if len(ids) != 1:
            continue
        album = albums.get(next(iter(ids))) or {}
        key = _album_identity_title(album.get('album_title'))
        if key:
            buckets[(album.get('owner_profile_id') or 1, key)].append(folder)

    for bucket in buckets.values():
        anchors = []
        for folder in bucket:
            if folder not in groups:
                continue
            ids = album_ids(groups[folder])
            album_id = next(iter(ids))
            album = albums.get(album_id) or {}
            title = str(album.get('album_title') or '')
            folder_signals_soundtrack = any(
                'soundtrack' in os.path.dirname(path).casefold()
                for path in groups[folder]
            )
            if (len(groups[folder]) >= 8 and _album_has_release_id(album) and
                    ('soundtrack' in title.casefold() or folder_signals_soundtrack or
                     str(album.get('record_type') or '').casefold() == 'compilation')):
                anchors.append((folder, album_id))
        if len(anchors) != 1:
            continue
        anchor_folder, anchor_id = anchors[0]
        anchor_album = albums.get(anchor_id) or {}
        anchor_positions = positions(groups[anchor_folder])
        if not anchor_positions:
            continue
        fragments = []
        for folder in bucket:
            if folder == anchor_folder:
                continue
            paths = groups[folder]
            ids = album_ids(paths)
            fragment_id = next(iter(ids))
            fragment_album = albums.get(fragment_id) or {}
            fragment_positions = positions(paths)
            if (len(paths) <= 3 and not _album_has_release_id(fragment_album) and
                    (fragment_album.get('owner_profile_id') or 1) ==
                    (anchor_album.get('owner_profile_id') or 1) and
                    fragment_positions and not (anchor_positions & fragment_positions)):
                fragments.append(folder)
                anchor_positions.update(fragment_positions)
        if fragments:
            for folder in fragments:
                groups[anchor_folder].extend(groups.pop(folder))
            trusted[anchor_folder] = anchor_id
    return groups, trusted


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


def _track_items(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        items = data.get('items') or data.get('tracks') or []
        if isinstance(items, dict):
            items = items.get('items') or []
        return [row for row in items if isinstance(row, dict)]
    return []


def _group_unassigned_downloads(folders, tracks, albums, trusted, read_tags):
    """Include sibling download fragments with unanimous exact album/artist tags.

    This only establishes album context. The joint audio assignment still checks
    every recording, duration and version and retains extras/duplicates for review.
    Multiple possible owners or incomplete tags leave the fragment separate.
    """
    def identity(value):
        # Audio-title normalization removes edition qualifiers; folder ownership
        # must retain them so a regular and deluxe release cannot collide.
        return ' '.join(str(value or '').casefold().split())

    anchors = defaultdict(list)
    for folder, paths in folders.items():
        ids = {tracks[os.path.normpath(path)].get('album_id') for path in paths
               if os.path.normpath(path) in tracks}
        ids.discard(None)
        album_id = trusted.get(folder) if folder in trusted else next(iter(ids)) if len(ids) == 1 else None
        album = albums.get(album_id) or {}
        if album_id is not None and _album_has_release_id(album):
            anchors[os.path.dirname(folder)].append((folder, album_id, album))
    groups = {folder: list(paths) for folder, paths in folders.items()}
    for folder, paths in folders.items():
        possible = anchors.get(os.path.dirname(folder), [])
        if not possible or any(os.path.normpath(path) in tracks for path in paths):
            continue
        identities = {(identity(tags.get('album')),
                       identity(tags.get('album_artist') or tags.get('artist')))
                      for tags in (read_tags(path) for path in paths)}
        if len(identities) != 1 or any(not value for value in next(iter(identities))):
            continue
        matches = [(anchor, album_id) for anchor, album_id, album in possible
                   if (identity(album.get('album_title')),
                       identity(album.get('artist_name'))) in identities]
        if len(matches) == 1:
            anchor, album_id = matches[0]
            groups[anchor].extend(groups.pop(folder))
            trusted[anchor] = album_id
    return groups


def _normalize_slot(item: dict, source: str) -> dict:
    def positive_int(value, default=None):
        try:
            number = int(value)
            return number if number > 0 else default
        except (TypeError, ValueError):
            return default

    artists = item.get('artists') or []
    artist_names = [str(a.get('name')).strip() for a in artists
                    if isinstance(a, dict) and str(a.get('name') or '').strip()]
    artist_names.extend(str(a).strip() for a in artists
                        if not isinstance(a, dict) and str(a or '').strip())
    artist_names = list(dict.fromkeys(artist_names))
    artist = ' & '.join(artist_names)
    return {
        'title': item.get('name') or item.get('title') or '',
        'artist': artist or '',
        'artist_names': artist_names,
        'track_number': positive_int(item.get('track_number')),
        'disc_number': positive_int(item.get('disc_number'), 1),
        'medium_format': item.get('medium_format') or '',
        'release_track_id': item.get('release_track_id') or '',
        'duration_ms': positive_int(item.get('duration_ms'), 0),
        'preview_url': item.get('preview_url') or item.get('previewUrl') or '',
        'source_track_id': item.get('id') or '',
        'recording_mbid': item.get('id') if source == 'musicbrainz' else '',
        'recording_disambiguation': (
            str(item.get('disambiguation') or '').strip()
            if source == 'musicbrainz' else ''
        ),
    }


def _match_release(files, slots, *, source, release_id, forced_pairs=None, catalogue_review=False):
    plan = (reconcile_folder(files, slots, forced_pairs=forced_pairs, catalogue_review=True)
            if catalogue_review else cached_match(files, slots, source=source, release_id=release_id,
                        forced_pairs=forced_pairs, compute=reconcile_folder))
    # The cached solver retains a complete partition. Intentional companions
    # share their source's assignment without consuming another release slot.
    assigned_paths = {files[pair['file_index']]['path'] for pair in plan['pairs']}
    return {**plan, 'extra_files': [index for index in plan['extra_files']
                                   if files[index].get('companion_of') not in assigned_paths]}


def _review_pairs(files, plan):
    pairs_by_path = {files[pair['file_index']]['path']: pair for pair in plan['pairs']}
    return [*plan['pairs'], *[
        {**pairs_by_path[file['companion_of']], 'file_index': index}
        for index, file in enumerate(files)
        if file.get('companion_of') in pairs_by_path and file['path'] not in pairs_by_path]]


def _score_release_candidates(files: list[dict], candidates: list[tuple[str, str]],
                              release_cache: dict,
                              exact_musicbrainz_releases: set[str] | None = None) -> list[tuple]:
    scored = []
    aliases = {fold_title(name): [row['artist_name'], *row['artist_aliases']]
               for file in files for row in [file.get('db') or {}]
               if row.get('artist_name') and row.get('artist_aliases')
               for name in [row['artist_name'], *row['artist_aliases']]}
    for source, release_id in candidates:
        key = (source, release_id)
        if key not in release_cache:
            is_exact_release = (source == 'musicbrainz' and
                                release_id in (exact_musicbrainz_releases or set()))
            raw = (get_album_tracks_for_source(source, release_id, exact_release=True)
                   if is_exact_release else
                   get_album_tracks_for_source(source, release_id))
            items = _track_items(raw)
            excluded = sum(item.get('video') is True for item in items)
            release_cache[key] = [{**_normalize_slot(item, source),
                                   'excluded_video_tracks': excluded}
                                  for item in items if item.get('video') is not True]
        slots = [{**slot, 'artist_aliases': aliases.get(fold_title(slot.get('artist') or ''), [])}
                 for slot in release_cache[key]] if aliases else release_cache[key]
        if not slots:
            continue
        plan = _match_release(files, slots, source=source, release_id=release_id)
        clear = sum(pair['confidence'] == 'clear' for pair in plan['pairs'])
        matched = len(plan['pairs'])
        scored.append(ScoredCandidate(clear, matched, -len(plan['extra_files']),
                       source, release_id, slots, plan))
    return scored


def _continuous_numbering(scored: tuple) -> int:
    """Describe an actual one-disc 1..N source tracklist for the review UI."""
    slots = scored[5]
    positions = [slot.get('track_number') for slot in slots]
    discs = {slot.get('disc_number') or 1 for slot in slots}
    return int(bool(slots) and discs == {1} and positions == list(range(1, len(slots) + 1)))


def _inferred_partial(files: list[dict], album: dict | None, watched: set[str], saved_intent) -> bool:
    """A lone playlist acquisition is not an instruction to collect its album."""
    return bool(not saved_intent and album and len(files) == 1
        and files[0].get('playlists')
        and str(album.get('artist_name') or '').strip().casefold() not in watched)


def _complete_collection_fit(files: list[dict], scored: tuple) -> int:
    """Prefer an exact tracklist only when the folder looks like a collection.

    This resolves a full 11-track album against an 11-track release instead of
    calling a 14-track deluxe edition an equal fit. A lone playlist track is
    deliberately excluded: its absent album siblings are acquisition intent,
    not evidence for the shortest release that happens to contain the song.
    """
    files = [file for file in files if not file.get('companion_of')]
    if len(files) <= 1 or (len(files) < 5 and any(file.get('playlists') for file in files)):
        return 0
    slots, plan = scored[5], scored[6]
    return int(not plan['extra_files'] and not plan['missing_slots'] and
               len(slots) == len(files))


def _distinctive_position_conflict(files: list[dict], selected: tuple,
                                   candidates: list[tuple]) -> bool:
    """Hold metadata when a plausible edition preserves a version's position.

    A short, completely matched release can otherwise win over a longer
    edition that puts a distinct live/remix file at its existing track number.
    The missing opener is an edition question, not proof that track 16 is #1.
    """
    selected_pairs = {pair['file_index']: selected[5][pair['slot_index']]
                      for pair in selected[6]['pairs']}
    for file_index, selected_slot in selected_pairs.items():
        file = files[file_index]
        tags = file.get('tags') or {}
        current_number = tags.get('track_number')
        markers = file_version_markers(file)
        local_key = semantic_title_key({'title': tags.get('title') or ''})[0]
        if not markers and local_key:
            recordings = (file.get('acoustid') or {}).get('recordings') or []
            best = max((float(row.get('score') or 0) for row in recordings), default=0)
            markers = frozenset().union(*(
                _version_markers(str(row.get('title') or ''))
                for row in recordings
                if float(row.get('score') or 0) >= best and
                semantic_title_key({'title': row.get('title') or ''})[0] == local_key
            ))
        if not markers or not current_number or current_number == selected_slot.get('track_number'):
            continue
        for candidate in candidates:
            if candidate is selected or candidate[0] < selected[0] - 1 or candidate[1] < selected[1] - 1:
                continue
            alternate = next((slot for slot in candidate[5]
                              if slot.get('track_number') == current_number and
                              semantic_title_key(slot)[0] == local_key and
                              slot_version_markers(slot) == markers), None)
            if alternate:
                return True
        if (current_number >= len(files) + 2 and
                not slot_version_markers(selected_slot)):
            # The catalog search may not contain that edition, but the
            # fingerprint still contradicts renumbering a versioned #16 to #1.
            return True
    return False


def _single_position_outlier(files: list[dict], scored: tuple) -> bool:
    """One high-numbered version can signal an edition beyond a complete short list."""
    for pair in scored[6]['pairs']:
        file = files[pair['file_index']]
        number = int((file.get('tags') or {}).get('track_number') or 0)
        slot_number = int(scored[5][pair['slot_index']].get('track_number') or 0)
        if number >= len(files) + 2 and number != slot_number:
            return True
    return False


def _candidate_rank(files: list[dict], scored: tuple, preferred_source: str = '') -> tuple:
    rank = (*scored[:3], _complete_collection_fit(files, scored))
    # A configured provider is a tie breaker after actual file coverage. It
    # must never outrank a release that identifies more audio or fewer extras.
    source_fit = int(bool(preferred_source) and scored[3] == preferred_source)
    return (*rank, source_fit)


def _promote_album_context_candidate(
        scored: list[tuple], context_candidates: list[tuple[str, str]]) -> None:
    """Prefer a title-derived candidate when one file has equal audio fit."""
    if not scored or not context_candidates:
        return
    best_audio_fit = scored[0][:3]
    contextual = next((row for candidate in context_candidates for row in scored
                       if row[:3] == best_audio_fit and
                       (row[3], row[4]) == candidate), None)
    if contextual is not None and contextual is not scored[0]:
        scored.remove(contextual)
        scored.insert(0, contextual)


def _single_file_album_context_conflict(album: dict | None,
                                        release_meta: dict | None) -> bool:
    """A lone recording cannot silently replace a differently named album."""
    current = str((album or {}).get('album_title') or '').strip()
    release = str((release_meta or {}).get('name') or
                  (release_meta or {}).get('title') or '').strip()
    if not current or not release or _album_titles_compatible(current, release):
        return False
    return _title_score(_album_identity_title(current),
                        _album_identity_title(release)) < 0.86


def _release_metadata(scored: tuple, cache: dict, exact_releases: set[str]) -> dict:
    source, release_id = scored[3], scored[4]
    key = (source, release_id)
    if key not in cache:
        exact = source == 'musicbrainz' and release_id in exact_releases
        cache[key] = ((get_album_for_source(source, release_id, exact_release=True)
                       if exact else get_album_for_source(source, release_id)) or {})
    return cache[key]


def _release_display_metadata(source, release_id, meta):
    """Use the provider adapters for covers and complete release credits."""
    from core.metadata.types import Album
    converter = getattr(Album, f'from_{source}_dict', None)
    adapted = converter(meta) if converter else None
    credits = meta.get('artists') or meta.get('artist-credit') or []
    names = [str(artist.get('name') or (artist.get('artist') or {}).get('name') or '')
             if isinstance(artist, dict) else str(artist) for artist in credits]
    names = [name for name in names if name]
    if not names and adapted:
        names = [name for name in adapted.artists if name and name != 'Unknown Artist']
    return {
        'release_artist': ' & '.join(names),
        'image_url': meta.get('image_url') or meta.get('thumb_url') or
            next((image.get('url') for image in meta.get('images') or []
                  if isinstance(image, dict) and image.get('url')), '') or
            (adapted.image_url if adapted else '') or
            (f'https://coverartarchive.org/release/{release_id}/front-250' if source == 'musicbrainz' else ''),
    }


def _supported_current_edition(files, album, candidates):
    """Retain a coherent existing edition supported by every owned file."""
    if not album or album.get('canonical_locked'):
        return None
    identity = album.get('canonical_source'), str(album.get('canonical_album_id') or '')
    members = [file for file in files if not file.get('companion_of')]
    if identity[0] != 'musicbrainz' or not members or not all(
        (file.get('tags') or {}).get('musicbrainz_release_id') == identity[1]
        for file in members):
        return None
    return next((row for row in candidates if (row.source, row.release_id) == identity
                 and row.clear == row.matched == len(members)
                 and not row.plan['extra_files'] and not row.plan['missing_slots']), None)


def _edition_preference(meta: dict) -> tuple[int, int]:
    media = meta.get('media') or []
    formats = ' '.join(str(row.get('format') or '') for row in media
                       if isinstance(row, dict))
    formats += ' ' + str(meta.get('format') or '')
    country = str(meta.get('country') or '').casefold()
    digital = int(any(word in formats.casefold() for word in ('digital', 'download', 'stream')))
    international = int(country in ('xw', 'worldwide', 'international', 'world'))
    return digital, international


def _full_coverage_digital_alternative(selected: tuple, ranked: list[tuple],
                                       file_count: int, metadata) -> bool:
    """A near-equal digital edition should remain an explicit album choice."""
    clear, matched, _, _, _, _, plan = selected
    if (matched != file_count or
            plan['extra_files'] or plan['missing_slots'] or
            _edition_preference(metadata(selected))[0]):
        return False
    return any(
        row[3] == 'musicbrainz' and row[1] == matched and row[0] >= clear - 1 and
        not row[6]['extra_files'] and not row[6]['missing_slots'] and
        _edition_preference(metadata(row))[0]
        for row in ranked[1:6]
    )


def _has_unexplained_identified_extras(files: list[dict], scored: tuple) -> bool:
    """Whether a plausible album file fits no slot or competing local copy."""
    plan = scored[6]
    competing = plan.get('competing_files') or {}
    unexplained = [index for index in plan['extra_files'] if not competing.get(index)]
    if not unexplained or scored[1] == 0:
        return False
    for index in unexplained:
        recordings = (files[index].get('acoustid') or {}).get('recordings') or []
        if ((files[index].get('acoustid') or {}).get('status') != 'ok' or
                fingerprint_is_ambiguous(recordings) or
                max((float(row.get('score') or 0) for row in recordings), default=0) < 0.80):
            return False
    return True


def _systematic_numbering_drift(files: list[dict], scored: tuple) -> bool:
    _clear, matched, _extras, _source, _release_id, slots, plan = scored
    if matched < 2:
        return False
    changed = 0
    for pair in plan['pairs']:
        tags = files[pair['file_index']].get('tags') or {}
        slot = slots[pair['slot_index']]
        if (tags.get('track_number') != slot.get('track_number') or
                (tags.get('disc_number') or 1) != (slot.get('disc_number') or 1)):
            changed += 1
    return changed >= 2 and changed * 2 >= matched


def _release_group_candidates(release_id: str, files: list[dict], context: JobContext,
                              cache: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Find bounded alternate editions when shape/numbering says to look wider."""
    if release_id in cache:
        return cache[release_id]
    client = context.mb_client
    if not client:
        cache[release_id] = []
        return []
    try:
        release = client.get_release(release_id, includes=['release-groups']) or {}
        group_id = str((release.get('release-group') or {}).get('id') or '')
        if not group_id and hasattr(client, 'get_release_group'):
            group = client.get_release_group(release_id) or {}
            group_id = str(group.get('id') or '')
        releases = (client.browse_release_group_releases(group_id, limit=100)
                    if group_id else [])
    except Exception as exc:
        logger.debug('Could not expand release group for %s: %s', release_id, exc)
        releases = []

    def media_shape(row):
        media = row.get('media') or []
        count = 0
        for medium in media:
            if not isinstance(medium, dict):
                continue
            try:
                count += int(medium.get('track-count') or
                             len(medium.get('tracks') or []))
            except (TypeError, ValueError):
                continue
        digital = any('digital' in str(medium.get('format') or '').casefold()
                      for medium in media if isinstance(medium, dict))
        return count, len(media), digital

    wanted = len(files)
    releases = [row for row in releases if row.get('id') and row.get('id') != release_id]
    releases.sort(key=lambda row: (
        media_shape(row)[0] == wanted,
        media_shape(row)[2],
        str(row.get('country') or '').casefold() in ('xw', 'worldwide', 'international'),
        media_shape(row)[1] == 1,
        str(row.get('status') or '').casefold() == 'official',
    ), reverse=True)
    cache[release_id] = [('musicbrainz', str(row['id'])) for row in releases[:12]]
    return cache[release_id]


def _search_release_candidates(album: dict | None, context: JobContext,
                               search_cache: dict) -> list[tuple[str, str]]:
    """Bounded Picard-style title/artist fallback for albums with no usable ID."""
    if not album or not context.mb_client:
        return []
    title = str(album.get('album_title') or '').strip()
    artist = str(album.get('artist_name') or '').strip()
    if not title:
        return []
    key = (title.casefold(), artist.casefold())
    if key not in search_cache:
        try:
            search_cache[key] = context.mb_client.search_release(
                title, artist_name=artist or None, limit=4)
        except Exception as exc:
            logger.debug('Could not search MusicBrainz release %r by %r: %s',
                         title, artist, exc)
            search_cache[key] = []
    candidates = []
    for row in search_cache[key] or []:
        release_id = str(row.get('id') or '')
        score = float(row.get('score') or 0)
        release_title = str(row.get('title') or '')
        if (release_id and score >= 90 and
                normalize(title) == normalize(release_title)):
            candidates.append(('musicbrainz', release_id))
    return candidates[:4]


def _trailing_album_qualifier(value: str) -> tuple[str, str] | None:
    """Find one balanced trailing qualifier, including nested pressing detail."""
    value = value.strip()
    if not value or value[-1] not in ')]':
        return None
    opening = '(' if value[-1] == ')' else '['
    closing = value[-1]
    depth = 0
    for index in range(len(value) - 1, -1, -1):
        if value[index] == closing:
            depth += 1
        elif value[index] == opening:
            depth -= 1
            if depth == 0:
                if index and value[index - 1].isspace():
                    return value[:index].strip(), value[index + 1:-1]
                return None
    return None


def _album_titles_compatible(current: str, release: str) -> bool:
    """Keep local edition detail when both names share the same album identity."""

    def without_repeated_qualifier(value: str) -> str:
        qualified = _trailing_album_qualifier(value)
        if not qualified:
            return value
        base, qualifier = qualified
        base_words = normalize(base)
        qualifier_words = normalize(qualifier)
        if qualifier_words and f' {qualifier_words} ' in f' {base_words} ':
            return base.strip()
        return value

    cleaned_current = without_repeated_qualifier(current or '')
    cleaned_release = without_repeated_qualifier(release or '')
    current_normalized = normalize(cleaned_current)
    release_normalized = normalize(cleaned_release)
    if not current_normalized or not release_normalized:
        return False
    current_qualifier = _trailing_album_qualifier(cleaned_current)
    release_qualifier = _trailing_album_qualifier(cleaned_release)
    if current_qualifier or release_qualifier:
        current_base, current_detail = current_qualifier or (cleaned_current, '')
        release_base, release_detail = release_qualifier or (cleaned_release, '')
        if normalize(current_base) != normalize(release_base):
            return False
        release_is_material = bool(_ALBUM_MATERIAL_QUALIFIER.search(release_detail))
        current_is_material = bool(_ALBUM_MATERIAL_QUALIFIER.search(current_detail))
        if release_detail and not release_is_material:
            # Retailer, pressing-plant and record-club labels identify the
            # catalog release. They are not useful display-title metadata.
            return True
        if not release_detail:
            # Preserve useful detail that is already present locally when the
            # selected release uses only the base title.
            return True
        if not current_detail or not current_is_material:
            return False
        return normalize(current_detail) == normalize(release_detail)
    if current_normalized == release_normalized:
        # Some normalizers intentionally collapse known title flavor words
        # (for example R and Rated R). The shorter local spelling must not
        # suppress a fuller provider title when neither side has a qualifier.
        return len(cleaned_current.strip()) >= len(cleaned_release.strip())
    if current_normalized.startswith(f'{release_normalized} '):
        local_detail = current_normalized[len(release_normalized):].strip()
        return bool(_ALBUM_EDITION_WORD.search(local_detail))
    return False


def _display_album_title(files: list[dict], library_title: str,
                         release_title: str, policy: str = 'preserve') -> str:
    """Prefer one consistent, clean local title over catalog-only qualifiers."""
    if policy == 'exact_release':
        return release_title
    library_title = _clean_album_display_title(library_title or '')
    tag_titles = [str((file.get('tags') or {}).get('album') or '').strip()
                  for file in files]
    tag_titles = [_clean_album_display_title(title) for title in tag_titles if title]
    if tag_titles:
        normalized = {re.sub(r'\s+', ' ', title).casefold() for title in tag_titles}
        library_qualifier = _trailing_album_qualifier(library_title)
        # Physical pressing descriptions belong to the release evidence. Only
        # clean them when all files and the selected release agree on the base
        # title; never erase a local live/remix/deluxe title merely for brevity.
        pressing_only = bool(library_qualifier
            and re.search(r'\b(?:black|white|colou?red|gold|silver)\s+(?:disc|vinyl)\b',
                          library_qualifier[1], re.I)
            and not re.search(r'\b(?:live|remix|acoustic|instrumental|remaster(?:ed)?)\b',
                              library_qualifier[1], re.I)
            and normalize(release_title) == normalize(tag_titles[0]))
        clean_library_variant = bool(
            library_qualifier and
            normalize(tag_titles[0]) == normalize(library_qualifier[0]) and
            (pressing_only or not _ALBUM_MATERIAL_QUALIFIER.search(library_qualifier[1]))
        )
        if len(normalized) == 1 and clean_library_variant:
            return tag_titles[0]
    if library_title and _album_title_preservable(library_title, release_title):
        return library_title
    if (tag_titles and len(normalized) == 1 and
            _album_title_preservable(tag_titles[0], release_title)):
        return tag_titles[0]
    return release_title


def _album_title_preservable(current: str, release: str) -> bool:
    if _album_titles_compatible(current, release):
        return True
    # A catalog edition can be exact while a consistent local display title
    # intentionally omits its qualifier. Keep the release ID as the identity.
    qualified = _trailing_album_qualifier(release)
    return bool(qualified and current.strip() and
                normalize(current) == normalize(qualified[0]))


def _wanted_slot_key(slot: dict) -> str:
    source_id = str(slot.get('source_track_id') or '').strip()
    if source_id:
        return (f"id:{source_id}:{slot.get('disc_number') or 1}:"
                f"{slot.get('track_number') or 0}")
    return (f"position:{slot.get('disc_number') or 1}:"
            f"{slot.get('track_number') or 0}:"
            f"{normalize(slot.get('title') or slot.get('name') or '')}")


def _candidate_evidence(files: list[dict], scored: tuple,
                        release_meta: dict | None = None) -> dict:
    """Keep the actual file-to-slot comparison behind a release decision."""
    clear, matched, _extras, source, release_id, slots, plan = scored
    pairs = []
    for pair in plan['pairs']:
        file = files[pair['file_index']]
        slot = slots[pair['slot_index']]
        recordings = file['acoustid'].get('recordings') or []
        slot_mbid = str(slot.get('recording_mbid') or '')

        def support_rank(recording, slot_mbid=slot_mbid, slot=slot):
            score = float(recording.get('score') or 0)
            eligible = score >= 0.80
            mbid_match = bool(slot_mbid and recording.get('mbid') == slot_mbid)
            title_match = _title_score(str(recording.get('title') or ''),
                                       str(slot.get('title') or '')) >= 0.88
            artist = str(recording.get('artist') or '')
            slot_artist = str(slot.get('artist') or '')
            artist_match = (not artist or not slot_artist or
                            _title_score(artist, slot_artist) >= 0.75)
            return (eligible and mbid_match, eligible and title_match and artist_match, score)

        top = max(recordings, key=support_rank, default={})

        def identity(recording):
            # AcoustID often links one fingerprint to several MusicBrainz
            # recording rows for the same named performance. Different MBIDs
            # are useful internally, but repeating "Song by Artist" ten times
            # is not useful review evidence. Keep version qualifiers while
            # folding punctuation/case variants.
            def part(value):
                folded = re.sub(r'[^\w\s]+', ' ', str(value or '').casefold())
                return re.sub(r'\s+', ' ', folded).strip()
            return part(recording.get('title')), part(recording.get('artist'))

        top_identity = identity(top)
        grouped = {}
        for recording in recordings:
            key = identity(recording)
            if not any(key) or key == top_identity:
                continue
            current = grouped.get(key)
            if current is None or support_rank(recording) > support_rank(current):
                grouped[key] = recording
        alternatives = [
            {'mbid': recording.get('mbid') or '',
             'title': recording.get('title') or '',
             'artist': recording.get('artist') or '',
             'score': recording.get('score'),
             'recording_count': sum(identity(other) == key for other in recordings)}
            for key, recording in grouped.items()
        ]
        alternatives.sort(key=support_rank, reverse=True)
        pairs.append({
            **pair_display_fields(file, slot),
            'assignment_evidence': assignment_evidence(file, pair),
            'tool_checks': file.get('tool_checks') or [],
            'tag_title': file['tags'].get('title') or '',
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
            'audio_recording_count': sum(identity(recording) == top_identity
                                         for recording in recordings),
            'audio_alternatives': alternatives,
            'slot_recording_disambiguation':
            slot.get('recording_disambiguation') or '',
            'local_recording_disambiguation':
            (file.get('db') or {}).get('recording_disambiguation') or '',
            'slot_number': slot.get('track_number'),
            'disc_number': slot.get('disc_number'),
            'duration_delta_ms': pair.get('duration_delta_ms'),
            'confidence': pair['confidence'],
            'reasons': pair['reasons'], 'pairing_checks': pair.get('checks') or [],
            'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
            'competitors': pair.get('competitors') or [],
            'catalogue_disagreement': pair.get('catalogue_disagreement') or False,
            'playlists': list(file.get('playlists') or []),
        })
    release_meta = release_meta or {}
    media = release_meta.get('media') or []
    formats = list(dict.fromkeys(
        str(medium.get('format') or '').strip()
        for medium in media if isinstance(medium, dict) and medium.get('format')
    ))
    if not formats and release_meta.get('format'):
        raw_format = release_meta.get('format')
        formats = ([str(value) for value in raw_format]
                   if isinstance(raw_format, (list, tuple)) else [str(raw_format)])
    return {
        'source': source, 'release_id': release_id,
        'release_title': release_meta.get('name') or release_meta.get('title') or '',
        **_release_display_metadata(source, release_id, release_meta),
        **release_catalog_details(release_meta),
        'country': release_meta.get('country') or '',
        'disambiguation': release_meta.get('disambiguation') or '',
        'format': ', '.join(value for value in formats if value),
        'date': (release_meta.get('date') or release_meta.get('release_date') or
                 release_meta.get('releaseDate') or ''),
        'matched': matched, 'clear': clear, 'total_slots': len(slots),
        'pairs': pairs,
        'missing': [slots[index]['title'] for index in plan['missing_slots']],
        'extra': [os.path.basename(files[index]['path'])
                  for index in plan['extra_files']],
    }


def _audio_conflicts_with_slot(file: dict, slot: dict) -> bool:
    """Strong audio contradicts both the local title and its proposed slot."""
    if not audio_contradicts_local_title(file):
        return False
    recordings = (file.get('acoustid') or {}).get('recordings') or []
    return not any(
        float(recording.get('score') or 0) >= 0.90 and
        recording_supports_file_slot(recording, file, slot)
        for recording in recordings
    )


def _pair_audio_conflict(file, slot, pair):
    # An explicitly selected release's naming disagreement has its own review
    # decision. Once confirmed, it must not reopen as an unreviewed audio veto.
    return not pair.get('catalogue_disagreement') and _audio_conflicts_with_slot(file, slot)


@timed('assignment_preparation')
def _album_plan_assignments(files: list[dict], slots: list[dict], plan: dict) -> tuple[list[dict], list[str]]:
    """Compact complete track matrix for the review read model."""
    assignments: list[dict] = []
    categories: set[str] = set()
    for pair in _review_pairs(files, plan):
        file = files[pair['file_index']]
        slot = slots[pair['slot_index']]
        tags = file.get('tags') or {}
        row = file.get('db') or {}
        recordings = (file.get('acoustid') or {}).get('recordings') or []
        audio = max(recordings, key=lambda item: float(item.get('score') or 0), default={})
        title_kind = title_difference_kind(str(tags.get('title') or ''), slot)
        changes = {}
        if title_kind in ('material', 'version'):
            changes['title'] = {'old': tags.get('title') or '', 'new': slot['title']}
            categories.add('title_issues')
        elif title_kind != 'exact':
            categories.add('title_conventions')
        for field in ('track_number', 'disc_number'):
            current = tags.get(field)
            proposed = slot.get(field)
            if proposed is not None and current != proposed:
                changes[field] = {'old': current, 'new': proposed}
                categories.add('numbering')
        different_recording = _pair_audio_conflict(file, slot, pair)
        if pair['confidence'] not in ('clear', 'manual') or different_recording:
            status = 'uncertain'
            categories.add('audio_conflicts')
        else:
            status = 'change' if changes else 'confirmed'
            if changes and not ({'track_number', 'disc_number'} & set(changes)):
                categories.add('metadata')
        assignments.append({
            'status': status,
            'companion_of': file.get('companion_of') or '',
            'assignment_evidence': assignment_evidence(file, pair),
            'tool_checks': file.get('tool_checks') or [],
            'file_path': file['path'], **pair_display_fields(file, slot),
            'track_id': row.get('id'),
            'current_title': tags.get('title') or row.get('title') or '',
            'current_track_number': tags.get('track_number'),
            'current_disc_number': tags.get('disc_number') or 1,
            'slot_artist': slot.get('artist') or '',
            'source_track_id': slot.get('source_track_id'),
            'slot_track_number': slot.get('track_number'),
            'slot_disc_number': slot.get('disc_number') or 1,
            'display_track_number': slot.get('track_number'),
            'title_difference': title_kind, 'changes': changes,
            'current_tags': tags, 'acoustid': file.get('acoustid') or {},
            'confidence': pair['confidence'], 'reasons': pair.get('reasons') or [], 'pairing_checks': pair.get('checks') or [],
            'ambiguity_reasons': pair.get('ambiguity_reasons') or [],
            'duration_delta_ms': pair.get('duration_delta_ms'),
            'audio_title': audio.get('title') or '',
            'audio_artist': audio.get('artist') or '',
            'audio_score': audio.get('score'),
            'audio_conflict': ('different_recording' if different_recording else ''),
            'playlists': list(file.get('playlists') or []),
        })
    for index in plan['missing_slots']:
        slot = slots[index]
        categories.add('missing_tracks')
        assignments.append({
            'status': 'missing', 'file_path': '', 'file': '',
            'slot_title': slot['title'], 'slot_artist': slot.get('artist') or '',
            'slot_track_number': slot.get('track_number'),
            'slot_disc_number': slot.get('disc_number') or 1,
            'display_track_number': slot.get('track_number'),
            'source_track_id': slot.get('source_track_id'),
            'slot_duration_ms': slot.get('duration_ms') or 0,
        })
    for index in plan['extra_files']:
        file = files[index]
        tags = file.get('tags') or {}
        recordings = (file.get('acoustid') or {}).get('recordings') or []
        audio = max(recordings, key=lambda item: float(item.get('score') or 0), default={})
        duplicate = bool((plan.get('competing_files') or {}).get(index))
        different_recording = audio_contradicts_local_title(file)
        categories.add('duplicates' if duplicate else
                       'audio_conflicts' if different_recording else 'extra_files')
        assignments.append({
            'status': 'duplicate' if duplicate else 'extra',
            'companion_of': file.get('companion_of') or '',
            'file_path': file['path'], 'file': os.path.basename(file['path']),
            'track_id': (file.get('db') or {}).get('id'),
            'current_title': tags.get('title') or '',
            'current_track_number': tags.get('track_number'),
            'current_disc_number': tags.get('disc_number') or 1,
            'file_duration_ms': tags.get('duration_ms') or 0,
            'current_tags': tags,
            'acoustid': file.get('acoustid') or {},
            'audio_title': audio.get('title') or '',
            'audio_artist': audio.get('artist') or '',
            'audio_score': audio.get('score'),
            'audio_conflict': ('different_recording' if different_recording else ''),
            'playlists': list(file.get('playlists') or []),
        })
    return assignments, sorted(categories)


def release_catalog_details(meta: dict) -> dict:
    labels = meta.get('label-info') or meta.get('label_info') or []
    names = [str((row.get('label') or {}).get('name') or '') for row in labels if isinstance(row, dict) and isinstance(row.get('label'), dict)]
    numbers = [str(row.get('catalog-number') or row.get('catalog_number') or '') for row in labels if isinstance(row, dict)]
    return {
        'label_catalogs': [{'label': str((row.get('label') or {}).get('name') or ''),
                            'catalog_number': str(row.get('catalog-number') or row.get('catalog_number') or '')}
                           for row in labels if isinstance(row, dict) and isinstance(row.get('label'), dict)],
        'label': ', '.join(dict.fromkeys(filter(None, names))) or (meta.get('label') if isinstance(meta.get('label'), str) else ''),
        'catalog_number': ', '.join(dict.fromkeys(filter(None, numbers))) or meta.get('catalog_number') or '',
        'barcode': meta.get('barcode') or '',
        'disc_count': len(meta.get('media') or []) or meta.get('disc_count') or None,
    }


def candidate_preview(files: list[dict], row: tuple, release_meta: dict) -> dict:
    candidate = ScoredCandidate(*row)
    release_slots, candidate_plan = candidate.slots, candidate.plan
    media = release_meta.get('media') or []
    formats = [str(medium.get('format') or '') for medium in media
               if isinstance(medium, dict) and medium.get('format')]
    return {
        'source': candidate.source, 'release_id': candidate.release_id, 'clear': candidate.clear,
        'release_title': release_meta.get('name') or release_meta.get('title') or '',
        **release_catalog_details(release_meta),
        'country': release_meta.get('country') or '',
        'date': release_meta.get('date') or release_meta.get('release_date') or '',
        'disambiguation': release_meta.get('disambiguation') or '',
        'format': ', '.join(dict.fromkeys(formats)) or release_meta.get('format') or '',
        **_release_display_metadata(candidate.source, candidate.release_id, release_meta),
        'matched': candidate.matched, 'total_slots': len(release_slots),
        'excluded_video_tracks': max((slot.get('excluded_video_tracks', 0)
                                      for slot in release_slots), default=0),
        'missing': len(candidate_plan['missing_slots']),
        'extra': len(candidate_plan['extra_files']),
        'continuous_numbering': bool(_continuous_numbering(row)),
        'pairs': [{
            **pair_display_fields(files[pair['file_index']], release_slots[pair['slot_index']],
                                  duration_fallback=False, strict_title=False),
            'file_path': files[pair['file_index']]['path'],
            'local_title': (files[pair['file_index']].get('tags') or {}).get('title') or '',
            'slot_source_track_id': release_slots[pair['slot_index']].get('source_track_id') or '',
            'disc_number': release_slots[pair['slot_index']].get('disc_number') or 1,
            'track_number': release_slots[pair['slot_index']].get('track_number'),
            'display_track_number': release_slots[pair['slot_index']].get('track_number'),
            'confidence': pair.get('confidence') or '',
            'assignment_evidence': assignment_evidence(files[pair['file_index']], pair),
        } for pair in candidate_plan['pairs']],
        'missing_slots': [{
            'slot_title': release_slots[index].get('title') or '',
            'disc_number': release_slots[index].get('disc_number') or 1,
            'track_number': release_slots[index].get('track_number'),
            'display_track_number': release_slots[index].get('track_number'),
            'missing': True,
        } for index in candidate_plan['missing_slots']],
        'missing_tracks': [release_slots[index].get('title') or ''
                           for index in candidate_plan['missing_slots']],
        'extra_files': [os.path.basename(files[index]['path'])
                        for index in candidate_plan['extra_files']],
    }


@deferred('plan')
@timed('plan_preparation_inclusive')
def _persist_selected_plan(db, common: dict, files: list[dict], scored: tuple,
                           *, candidates: list[tuple] | None = None,
                           candidate_metadata: dict[tuple[str, str], dict] | None = None,
                           candidate_differences: dict[tuple[str, str], str] | None = None,
                           state: str = '', intent_scope: str = '',
                           intent_inferred: bool = False,
                           action_changes: dict[str, dict] | None = None,
                           numbering_mode: str = '',
                           inventory: dict | None = None,
                           wanted_slots: set[str] | None = None,
                           wishlisted_slots: set[tuple[str, str, str]] | None = None) -> str:
    clear, matched, _extras, source, release_id, slots, plan = scored
    assignments, categories = _album_plan_assignments(files, slots, plan)
    for assignment in assignments:
        if (assignment.get('status') == 'missing' and
                (source, str(assignment.get('source_track_id') or ''),
                 str(common.get('exact_release_id') or release_id)) in
                (wishlisted_slots or set())):
            assignment['fulfillment_state'] = 'wishlisted'
    if intent_scope == 'partial':
        for assignment in assignments:
            if (assignment.get('status') == 'missing' and not assignment.get('fulfillment_state') and
                    _wanted_slot_key({'source_track_id': assignment.get('source_track_id'),
                                      'disc_number': assignment.get('slot_disc_number'),
                                      'track_number': assignment.get('slot_track_number'),
                                      'title': assignment.get('slot_title')}) not in (wanted_slots or set())):
                assignment['status'] = 'not_owned'
        if not wanted_slots:
            categories = [category for category in categories
                          if category != 'missing_tracks']
        categories.append('partial_albums')
    for assignment in assignments:
        action = (action_changes or {}).get(assignment.get('file_path') or '')
        if not action:
            continue
        assignment['changes'] = action.get('changes') or {}
        assignment['status'] = action.get('status') or (
            'change' if assignment['changes'] else assignment['status'])
        changed_fields = set(assignment['changes'])
        if changed_fields & {
            'track_number', 'disc_number', 'library_track_number',
            'library_disc_number',
        }:
            categories.append('numbering')
        if changed_fields & {'title', 'library_title', 'filename'}:
            difference = assignment.get('title_difference')
            categories.append(
                'title_conventions'
                if difference in ('typography', 'flavor', 'cosmetic') else 'title_issues')
        if changed_fields - {
            'track_number', 'disc_number', 'library_track_number',
            'library_disc_number', 'title', 'library_title', 'filename',
        }:
            categories.append('metadata')
    if state == 'release_conflict':
        categories = sorted(set(categories) | {'release_conflicts'})
    elif not state and intent_scope == 'partial':
        state = ('review' if any(row.get('status') in ('uncertain', 'extra', 'duplicate', 'missing')
                                 for row in assignments)
                 else 'changes' if any(row.get('status') == 'change'
                                       for row in assignments)
                 else 'confirmed')
    candidate_rows = candidates or [scored]
    try:
        payload = {
            'source': source, 'release_id': release_id, 'state': state,
            'library_root': common.get('library_root') or '',
            'intent_scope': intent_scope,
            'intent_inferred': intent_inferred,
            'release_decision_reason': common.get('release_decision_reason') or '',
            'current_release': common.get('current_release'),
            'numbering_mode': numbering_mode,
            'inventory': inventory,
            'release_track_count': len(slots), 'local_file_count': len(files),
            'matched': matched, 'clear': clear, 'assignments': assignments,
            'categories': sorted(set(categories)),
            'candidates': [candidate_preview(files, row,
                            (candidate_metadata or {}).get((row[3], row[4])) or {})
                            | {'difference_summary': (candidate_differences or {}).get((row[3], row[4]), '')}
                            for row in candidate_rows[:6]],
        }
        from core.library.reconciliation_fulfillment import settle_assignments
        with stage('plan_persistence'):
            case_id = persist_album_plan(db, common, payload)
            settle_assignments(db, case_id, assignments, common.get('exact_release_id') or release_id, source)
        return case_id
    except Exception as exc:
        # The snapshot is a review read model. A transient persistence problem
        # must not discard the findings that remain the actionable source of
        # truth or abort a full library scan.
        logger.warning('Could not persist reconciliation plan for %s: %s',
                       common.get('album_folder') or common.get('album_id'), exc)
        return ''


def _candidate_shape(scored: tuple, *, ignore_positions: bool = False) -> tuple:
    """The release facts reconciliation can distinguish, excluding edition ID."""
    _clear, _matched, _extras, _source, _release_id, slots, plan = scored

    def slot_shape(slot: dict) -> tuple:
        track_number = slot.get('track_number')
        return (0 if ignore_positions else slot.get('disc_number') or 1,
                False if ignore_positions else track_number is None,
                0 if ignore_positions else track_number or 0, semantic_title_key(slot),
                normalize(slot.get('recording_disambiguation') or ''))

    pairs = tuple(sorted(
        (pair['file_index'], *slot_shape(slots[pair['slot_index']]))
        for pair in plan['pairs']))
    missing = tuple(sorted(
        slot_shape(slots[index])
        for index in plan['missing_slots']))
    return len(slots), pairs, missing, tuple(sorted(plan['extra_files']))


def _candidate_shapes_equivalent(left: tuple, right: tuple, *, ignore_positions: bool = False) -> bool:
    """Compare release mappings without treating absent detail as conflict.

    MusicBrainz often supplies a recording comment or version marker that
    iTunes/Deezer omit. Missing provider detail is unknown, not evidence of a
    different edition. Two explicit, different markers or comments still keep
    the releases distinct. Ignoring positions is reserved for comparing the
    same audio across formats; competing layouts within one format stay distinct.
    """
    if left[3] == right[3] == 'musicbrainz':
        def recording_shape(candidate):
            slots, plan = candidate[5], candidate[6]
            if not slots or any(not slot.get('recording_mbid') for slot in slots):
                return None
            def identity(index):
                slot = slots[index]
                return (0 if ignore_positions else slot.get('disc_number') or 1,
                        0 if ignore_positions else slot.get('track_number') or 0,
                        slot['recording_mbid'])
            return (len(slots), tuple(sorted((pair['file_index'], *identity(pair['slot_index']))
                    for pair in plan['pairs'])),
                    tuple(sorted(identity(index) for index in plan['missing_slots'])),
                    tuple(sorted(plan['extra_files'])))
        identity = recording_shape(left)
        if identity is not None and identity == recording_shape(right):
            # Release-specific translations/arrangement labels may differ even
            # when every positioned recording and file assignment is identical.
            return True
    left_shape = _candidate_shape(left, ignore_positions=ignore_positions)
    right_shape = _candidate_shape(right, ignore_positions=ignore_positions)
    if left_shape[0] != right_shape[0] or left_shape[3] != right_shape[3]:
        return False

    def compatible_slot(a: tuple, b: tuple, prefix_length: int) -> bool:
        if a[:prefix_length] != b[:prefix_length]:
            return False
        left_title, right_title = a[prefix_length], b[prefix_length]
        # Provider spelling and capitalization differences do not define a
        # different edition when the positions, assignments, and version
        # markers agree. Numeric/version changes remain hard boundaries in
        # _title_score and the marker comparison below.
        if _title_score(left_title[0], right_title[0]) < 0.94:
            return False
        left_markers, right_markers = left_title[1], right_title[1]
        if left_markers and right_markers and left_markers != right_markers:
            return False
        left_comment, right_comment = a[prefix_length + 1], b[prefix_length + 1]
        return not left_comment or not right_comment or left_comment == right_comment

    left_pairs = sorted(left_shape[1], key=lambda row: (*row[:4], row[4][0]))
    right_pairs = sorted(right_shape[1], key=lambda row: (*row[:4], row[4][0]))
    if (len(left_pairs) != len(right_pairs) or
            any(not compatible_slot(a, b, 4)
                for a, b in zip(left_pairs, right_pairs, strict=True))):
        return False
    left_missing = sorted(left_shape[2], key=lambda row: (*row[:3], row[3][0]))
    right_missing = sorted(right_shape[2], key=lambda row: (*row[:3], row[3][0]))
    return (len(left_missing) == len(right_missing) and
            all(compatible_slot(a, b, 3)
                for a, b in zip(left_missing, right_missing, strict=True)))


def _owned_candidate(candidate: tuple) -> ScoredCandidate:
    """Compare partial acquisitions using only the recordings actually owned."""
    clear, matched, extras, source, release, slots, plan = candidate
    pairs = sorted(plan['pairs'], key=lambda pair: pair['file_index'])
    return ScoredCandidate(clear, matched, extras, source, release,
        [slots[pair['slot_index']] for pair in pairs],
        {**plan, 'pairs': [{**pair, 'slot_index': index} for index, pair in enumerate(pairs)],
         'missing_slots': []})


def _owned_candidates_equivalent(left: tuple, right: tuple) -> bool:
    first, second = _owned_candidate(left), _owned_candidate(right)
    if first.source == second.source == 'musicbrainz':
        for a, b in zip(first.slots, second.slots):
            if a.get('recording_mbid') and b.get('recording_mbid') and a['recording_mbid'] != b['recording_mbid']:
                return False
    return _candidate_shapes_equivalent(first, second)


def _retain_partial_release(files, album, scored, saved_intent, watched, metadata):
    if not album or album.get('canonical_locked'):
        return False
    current = next((row for row in scored if (row[3], row[4]) ==
                    (album.get('canonical_source'), str(album.get('canonical_album_id') or ''))), None)
    partial = (saved_intent and saved_intent[0] == 'partial' and not saved_intent[3]
               and current and saved_intent[1:3] == (current[3], current[4]))
    if not partial and not _inferred_partial(files, album, watched, saved_intent):
        return False
    if (not current or current[:3] != scored[0][:3] or current[6]['extra_files']
            or any(pair['confidence'] not in ('clear', 'manual') for pair in current[6]['pairs'])
            or _single_file_album_context_conflict(album, metadata(current))):
        return False
    if not all(_owned_candidates_equivalent(current, row)
               for row in scored if row[:3] == current[:3]):
        return False
    scored.remove(current)
    scored.insert(0, current)
    return True


def _candidate_difference(selected: tuple, other: tuple) -> str:
    """Explain the actual mapping difference without exposing ranking scores."""
    first, second = ScoredCandidate(*selected), ScoredCandidate(*other)
    left = {pair['file_index']: first.slots[pair['slot_index']] for pair in first.plan['pairs']}
    right = {pair['file_index']: second.slots[pair['slot_index']] for pair in second.plan['pairs']}
    if left.keys() != right.keys() or first.plan['extra_files'] != second.plan['extra_files']:
        return 'Different local files are assigned or remain unmatched.'
    if _candidate_shapes_equivalent(first, second):
        if first.clear != second.clear:
            return 'The same track mapping has different assignment confidence.'
        return 'The same track mapping; catalogue edition details differ.'
    for index, slot in left.items():
        alternate = right[index]
        label = slot.get('title') or 'An owned track'
        if (slot.get('disc_number') or 1, slot.get('track_number')) != (
                alternate.get('disc_number') or 1, alternate.get('track_number')):
            return (f'{label}: disc {slot.get("disc_number") or 1}, track {slot.get("track_number")} '
                    f'→ disc {alternate.get("disc_number") or 1}, track {alternate.get("track_number")}.')
        def single(item, source):
            return ScoredCandidate(1, 1, 0, source, '', [item],
                {'pairs': [{'file_index': 0, 'slot_index': 0}], 'missing_slots': [], 'extra_files': []})
        if not _candidate_shapes_equivalent(single(slot, first.source), single(alternate, second.source)):
            return f'{label}: a different recording or version is proposed.'
    if _owned_candidates_equivalent(first, second):
        return 'Only unowned release tracks differ.'
    return 'The release tracklist or assignment evidence differs.'


def _review_candidates(selected: tuple, ranked: list[tuple], blockers=()) -> list[tuple]:
    """Keep the selected edition and the actual conflicting alternatives visible."""
    result = [selected]
    identities = {(selected[3], selected[4])}
    for candidate in [*blockers, *ranked]:
        identity = (candidate[3], candidate[4])
        if identity in identities:
            continue
        if len(result) >= 6:
            break
        # Spend the first slots on distinct outcomes. Equivalent catalogue
        # editions are filled afterward, if the six-card limit allows them.
        if candidate in blockers or not any(_candidate_shapes_equivalent(candidate, row) for row in result):
            result.append(candidate)
            identities.add(identity)
    for candidate in ranked:
        if len(result) >= 6:
            break
        if (candidate[3], candidate[4]) not in identities:
            result.append(candidate)
            identities.add((candidate[3], candidate[4]))
    return result


def _same_recording_copies(files: list[dict], indexes: list[int]) -> bool:
    """Require shared strong audio identity and compatible lengths."""
    if len(indexes) < 2:
        return False
    version_sets = {file_version_markers(files[index]) for index in indexes}
    if len(version_sets) != 1:
        return False
    identity_titles = []
    for index in indexes:
        file = files[index]
        row = file.get('db') or {}
        title = ((file.get('tags') or {}).get('title') or row.get('title') or
                 os.path.splitext(os.path.basename(file.get('path') or ''))[0])
        disambiguation = str(row.get('recording_disambiguation') or '').strip()
        identity_titles.append(
            f'{title} ({disambiguation})' if disambiguation else str(title)
        )
    if any(_title_score(left, right) < 0.83
           for offset, left in enumerate(identity_titles)
           for right in identity_titles[offset + 1:]):
        return False
    if any(version_sets) or any(
            (files[index].get('db') or {}).get('recording_disambiguation')
            for index in indexes):
        literal_titles = {
            re.sub(r'\s+', ' ', re.sub(r'[^\w\s]+', ' ', title.casefold())).strip()
            for title in identity_titles
        }
        if len(literal_titles) != 1:
            # Category parity (both say "remix" or "live") is insufficient:
            # named remixes and performances from different years remain
            # separate recordings even when a bad external ID links them.
            return False
    evidence = [files[index].get('acoustid') or {} for index in indexes]
    fingerprint_keys = [item.get('fingerprint_key') for item in evidence]
    if all(fingerprint_keys) and len(set(fingerprint_keys)) == 1:
        return True
    strong_ids = [
        {str(row.get('mbid')) for row in item.get('recordings') or []
         if row.get('mbid') and float(row.get('score') or 0) >= 0.90}
        for item in evidence]
    if not strong_ids or not set.intersection(*strong_ids):
        return False
    durations = [int((files[index].get('tags') or {}).get('duration_ms') or 0)
                 for index in indexes]
    durations = [duration for duration in durations if duration]
    return len(durations) < 2 or max(durations) - min(durations) <= 3000


def _mark_lossy_companions(files, extensions):
    """Protect configured companions only when the audio also agrees."""
    if not extensions:
        return
    for index, file in enumerate(files):
        if not is_lossy_companion_file(file['path'], extensions):
            continue
        for other_index, other in enumerate(files):
            if (is_lossy_companion_pair(file['path'], other['path'], extensions)
                    and _same_recording_copies(files, [index, other_index])):
                file['companion_of'] = other['path']
                break


def _duplicate_copy(file: dict) -> dict:
    row = file.get('db') or {}
    path = file.get('path') or ''
    quality = {'format': os.path.splitext(path)[1].lstrip('.').upper()}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        info = getattr(audio, 'info', None)
        if info:
            quality.update({
                'sample_rate': getattr(info, 'sample_rate', None),
                'bits_per_sample': getattr(info, 'bits_per_sample', None),
                'channels': getattr(info, 'channels', None),
                'bitrate': getattr(info, 'bitrate', None),
            })
    except Exception as exc:
        logger.debug('Could not read duplicate audio quality for %s: %s', path, exc)
    return {
        'track_id': row.get('id'),
        'title': row.get('title') or file.get('tags', {}).get('title') or '',
        'artist': row.get('artist_name') or file.get('tags', {}).get('artist') or '',
        'album': row.get('album_title') or file.get('tags', {}).get('album') or '',
        'file_path': path,
        'fingerprint_key': file.get('acoustid', {}).get('fingerprint_key'),
        'size_bytes': file.get('size_bytes'),
        'mtime_ns': file.get('mtime_ns'),
        'bitrate': quality.get('bitrate') or row.get('bitrate') or 0,
        'duration': (file.get('tags') or {}).get('duration_ms') or row.get('duration') or 0,
        'quality': quality,
        'track_number': row.get('track_number'),
        'recording_disambiguation': row.get('recording_disambiguation') or '',
        'playlists': list(file.get('playlists') or []),
    }


def _checked_file_evidence(files: list[dict]) -> list[dict]:
    checked = []
    for file in files:
        recordings = file['acoustid'].get('recordings') or []
        top = max(recordings, key=lambda row: float(row.get('score') or 0),
                  default={})
        checked.append({
            'file': os.path.basename(file['path']),
            'tag_title': file['tags'].get('title') or '',
            'audio_status': file['acoustid'].get('status'),
            'audio_title': top.get('title') or '',
            'audio_artist': top.get('artist') or '',
            'audio_score': top.get('score'),
        })
    return checked


@deferred('finding')
def _emit_finding(context: JobContext, observed: set[tuple[str, str, str]], **finding):
    observed.add((finding['finding_type'], finding['entity_type'],
                  str(finding['entity_id'])))
    return bool(context.create_finding and context.create_finding(**finding))


def _evidence_signature(files: list[dict], extra: object = None) -> str:
    evidence = [(file['path'], file['acoustid'].get('fingerprint_key'),
                 file['acoustid'].get('status')) for file in files]
    raw = json.dumps([evidence, extra], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


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
        conn = context.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_album_intent (
                    album_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    source TEXT NOT NULL, release_id TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_wanted_slots (
                    album_id TEXT PRIMARY KEY, source TEXT NOT NULL,
                    release_id TEXT NOT NULL, slots_json TEXT NOT NULL
                )
            ''')
            return {str(row[0]): (row[1], row[2], row[3],
                                  json.loads(row[4] or '[]') if row[4] else [])
                    for row in conn.execute('''
                        SELECT i.album_id, i.scope, i.source, i.release_id,
                               CASE WHEN w.source = i.source AND w.release_id = i.release_id
                                    THEN w.slots_json ELSE '[]' END
                        FROM reconcile_album_intent i
                        LEFT JOIN reconcile_wanted_slots w ON w.album_id = i.album_id
                        UNION ALL
                        SELECT w.album_id, '', w.source, w.release_id, w.slots_json
                        FROM reconcile_wanted_slots w
                        WHERE NOT EXISTS (SELECT 1 FROM reconcile_album_intent i WHERE i.album_id=w.album_id AND i.source=w.source AND i.release_id=w.release_id)
                    ''')}
        finally:
            conn.close()

    def _assignment_overrides(self, context: JobContext) -> dict[tuple[str, str, str, str], tuple]:
        conn = context.db._get_connection()
        try:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS reconcile_track_assignment (
                    album_id TEXT NOT NULL, source TEXT NOT NULL,
                    release_id TEXT NOT NULL, file_path TEXT NOT NULL,
                    slot_key TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL, fingerprint_key TEXT NOT NULL,
                    PRIMARY KEY (album_id, source, release_id, file_path)
                )
            ''')
            return {(str(row[0]), row[1], row[2], row[3]): tuple(row[4:])
                    for row in conn.execute('''
                        SELECT album_id, source, release_id, file_path, slot_key,
                               size_bytes, mtime_ns, fingerprint_key
                        FROM reconcile_track_assignment
                    ''')}
        finally:
            conn.close()

    def _wishlisted_slots(self, context: JobContext) -> dict[int, set[tuple[str, str, str]]]:
        """Read exact requested tracks once for the current scan."""
        conn = context.db._get_connection()
        try:
            from core.library.reconciliation_fulfillment import backfill_requests, outstanding
            backfill_requests(conn)
            durable = outstanding(conn)
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
        client = context.acoustid_client
        if client is None:
            result.stopped_early = 'AcoustID client is unavailable'
            return result
        available, reason = client.is_available()
        if not available:
            result.stopped_early = reason or 'AcoustID unavailable'
            return result
        settings = self._get_settings(context)
        numbering_mode = 'release'
        preferred_source = ''
        if settings['source_preference'] == 'active_preferred' and context.config_manager:
            preferred_source = str(context.config_manager.get('metadata.fallback_source', '') or '')
        rank_candidate = lambda row: _candidate_rank(
            files, row, preferred_source)
        roots = all_library_roots(context)
        if not roots or any(not os.path.isdir(root) for root in roots):
            result.stopped_early = 'A configured library root is unavailable'
            return result
        inventory_request = load_inventory_request(context.db, target_folder, ScanScope(scope), roots=roots)
        def selection_for(request):
            return track_selection(context.db, request, title_key=_album_identity_title,
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
        from core.library import reconciliation_choices as review_choices
        kept_numbering = review_choices.applied_numbering_choices(context.db)
        reviewed_releases = review_choices.load_release_choices(context.db)
        numbering_history = review_choices.flattened_numbering_history(context.db)
        playlist_membership = {}
        if context.playlist_membership:
            try:
                playlist_membership = context.playlist_membership() or {}
            except Exception as exc:
                logger.debug('Could not read playlist membership: %s', exc)
        if inventory_request.scope == ScanScope.ALBUM:
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
            folders = _group_disc_folders(folders, tracks, roots)
            folders, trusted_logical_groups = _group_logical_albums(folders, tracks, albums)
            discovery_cache = ReconciliationEvidence(context.db, client)
            try:
                folders = _group_unassigned_downloads(
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
        if inventory_request.scope == ScanScope.ALBUM:
            for album_id in inventory_request.album_ids:
                album_folder_counts[album_id] = inventory_request.saved.get(
                    'album_folder_counts', {}).get(str(album_id), 1)
        if target_folder is not None:
            folders = {target_folder: folders[target_folder]}
        directory_cache = {}
        release_cache = {}
        companion_extensions = lossy_companion_exts(context.config_manager, context.db, logger)
        release_meta_cache = {}
        recording_checks = {}
        tool_evidence = ToolEvidence(context.db)
        release_search_cache = {}
        release_group_cache = {}
        exact_musicbrainz_releases: set[str] = set()
        cache = ReconciliationEvidence(context.db, client)
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
                                     'evidence_signature': _evidence_signature(files, list(album_counts))}):
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
                                     'evidence_signature': _evidence_signature(files, album_id)}):
                            result.findings_created += 1
                        continue
                    member_folders = sorted({os.path.dirname(file['path']) for file in files})
                    parents = tuple(sorted({os.path.dirname(path) for path in member_folders}))
                    if parents not in directory_cache:
                        directory_cache[parents] = directory_observations(member_folders, roots)
                    album_inventory = inventory_record(files, trusted_album_id=trusted_anchor_id,
                        album_folder_counts={key: album_folder_counts[key] for key in album_counts},
                        roots=roots, observations=directory_cache[parents])
                    _mark_lossy_companions(files, companion_extensions)
                    candidates = self._release_candidates(
                        files, album, context,
                        exact_musicbrainz_releases=exact_musicbrainz_releases)
                    linked_candidates = list(candidates)
                    scored = _score_release_candidates(
                        files, candidates, release_cache, exact_musicbrainz_releases)
                    best_initial = (max(scored, key=rank_candidate) if scored else None)
                    if (not best_initial or best_initial[6]['extra_files']
                            or best_initial[6]['missing_slots']):
                        additional = [candidate for candidate in self._release_candidates(
                            files, album, context, include_audio=True,
                            exact_musicbrainz_releases=exact_musicbrainz_releases)
                            if candidate not in candidates]
                        scored.extend(_score_release_candidates(
                            files, additional, release_cache, exact_musicbrainz_releases))
                        candidates.extend(additional)
                    best_expanded = (max(scored, key=rank_candidate) if scored else None)
                    # Discover digital siblings of a physical release. Format is
                    # only a tie breaker after audio/tracklist fit, and has no
                    # effect on the positions written to file or library metadata.
                    needs_digital = bool(
                        best_expanded and not _edition_preference(_release_metadata(
                            best_expanded, release_meta_cache, exact_musicbrainz_releases))[0]
                    )
                    if needs_digital and not any(row.source == 'musicbrainz' for row in scored):
                        searched = [candidate for candidate in _search_release_candidates(
                            album, context, release_search_cache) if candidate not in candidates]
                        exact_musicbrainz_releases.update(
                            release_id for source, release_id in searched
                            if source == 'musicbrainz')
                        candidates.extend(searched)
                        scored.extend(_score_release_candidates(
                            files, searched, release_cache, exact_musicbrainz_releases))
                        best_expanded = (max(scored, key=rank_candidate) if scored else None)
                    group_seed = max(
                        (row for row in scored if row.source == 'musicbrainz'),
                        key=rank_candidate,
                        default=None,
                    )
                    if (best_expanded and group_seed and
                            (best_expanded[6]['extra_files'] or
                             best_expanded[6]['missing_slots'] or
                             _systematic_numbering_drift(files, best_expanded) or
                             _single_position_outlier(files, best_expanded) or
                             not _edition_preference(
                                   _release_metadata(best_expanded,
                                                     release_meta_cache,
                                                     exact_musicbrainz_releases))[0])):
                        editions = [candidate for candidate in _release_group_candidates(
                            group_seed[4], files, context, release_group_cache)
                            if candidate not in candidates]
                        exact_musicbrainz_releases.update(release_id for _, release_id in editions)
                        candidates.extend(editions)
                        scored.extend(_score_release_candidates(
                            files, editions, release_cache, exact_musicbrainz_releases))
                    if not scored or max(row.matched for row in scored) == 0:
                        searched = [candidate for candidate in _search_release_candidates(
                            album, context, release_search_cache) if candidate not in candidates]
                        exact_musicbrainz_releases.update(
                            release_id for source, release_id in searched
                            if source == 'musicbrainz')
                        candidates.extend(searched)
                        scored.extend(_score_release_candidates(
                            files, searched, release_cache, exact_musicbrainz_releases))
                    if not scored:
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
                                     'checked_files': _checked_file_evidence(files),
                                     'attempted_releases': [
                                         {'source': source, 'release_id': release_id}
                                         for source, release_id in candidates],
                                     'evidence_signature': _evidence_signature(files, candidates)}):
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
                            metadata = _release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases)
                            # A lone recording can belong to many compilations.
                            # Its known album context precedes format/country,
                            # without claiming the tied editions are equivalent.
                            context_fit = int(len(files) == 1 and bool(album) and
                                not _single_file_album_context_conflict(album, metadata))
                            return context_fit, *_edition_preference(metadata)
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
                    if len(files) == 1 and album and not linked_candidates:
                        release_meta = _release_metadata(
                            scored[0], release_meta_cache, exact_musicbrainz_releases)
                        if _single_file_album_context_conflict(album, release_meta):
                            searched = [candidate for candidate in _search_release_candidates(
                                album, context, release_search_cache)
                                if candidate not in candidates]
                            exact_musicbrainz_releases.update(
                                release_id for source, release_id in searched
                                if source == 'musicbrainz')
                            if searched:
                                candidates.extend(searched)
                                scored.extend(_score_release_candidates(
                                    files, searched, release_cache, exact_musicbrainz_releases))
                                scored.sort(key=rank_candidate, reverse=True)
                                _promote_album_context_candidate(scored, searched)
                                release_meta = _release_metadata(
                                    scored[0], release_meta_cache, exact_musicbrainz_releases)
                        if _single_file_album_context_conflict(album, release_meta):
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
                                    'checked_files': _checked_file_evidence(files),
                                    'candidates': [_candidate_evidence(
                                        files, row, _release_metadata(
                                            row, release_meta_cache,
                                            exact_musicbrainz_releases))
                                                   for row in scored[:6]],
                                    'evidence_signature': _evidence_signature(
                                        files, [(row.source, row.release_id, row.clear, row.matched)
                                                for row in scored]),
                                }):
                                result.findings_created += 1
                            cache.checkpoint()
                            continue
                    retained_partial = _retain_partial_release(files, album, scored,
                        intents.get(str(album_id)), watched_artists,
                        lambda row: _release_metadata(row, release_meta_cache, exact_musicbrainz_releases))
                    ranked_candidates = list(scored)
                    retained_current = _supported_current_edition(files, album, scored)
                    if retained_current:
                        scored.remove(retained_current)
                        scored.insert(0, retained_current)
                    pinned_row = None
                    best_unpinned_rank = rank_candidate(scored[0])
                    if album and album.get('canonical_locked'):
                        pinned_row = next((row for row in scored
                                           if (row.source, row.release_id) ==
                                           (album.get('canonical_source'),
                                            str(album.get('canonical_album_id') or ''))), None)
                        if pinned_row:
                            scored.remove(pinned_row)
                            reviewed_plan = _match_release(files, pinned_row.slots,
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
                    if _edition_preference(_release_metadata(
                            scored[0], release_meta_cache, exact_musicbrainz_releases))[0]:
                        tied_rows = [row for row in tied_rows
                            if _edition_preference(_release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases))[0]
                            or not _candidate_shapes_equivalent(
                                scored[0], row, ignore_positions=True)]
                    equivalent_tie = (len(tied_rows) > 1 and
                                      all(_candidate_shapes_equivalent(row, tied_rows[0])
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
                        album_id is not None and album and not album.get('canonical_locked') and
                        _has_unexplained_identified_extras(files, scored[0])
                    )
                    symmetric_mismatch = bool(
                        album_id is not None and not pinned and
                        plan['extra_files'] and plan['missing_slots'] and
                        len(plan['extra_files']) >= 2 and
                        len(plan['missing_slots']) >= 2
                    )
                    position_conflict = bool(
                        not (album and album.get('canonical_locked')) and
                        _distinctive_position_conflict(files, scored[0], scored[1:]))
                    digital_edition_choice = bool(
                        album_id is not None and album and not retained_current and not album.get('canonical_locked') and
                        _full_coverage_digital_alternative(
                            scored[0], ranked_candidates, len(files),
                            lambda row: _release_metadata(
                                row, release_meta_cache, exact_musicbrainz_releases))
                    )
                    if (locked_conflict or tied or edition_conflict or symmetric_mismatch or
                            position_conflict or digital_edition_choice or
                            (matched == 0 and not pinned)):
                        blockers = [row for row in tied_rows if tied and
                                    not _candidate_shapes_equivalent(scored[0], row)]
                        blockers.extend(row for row in ranked_candidates if
                            (position_conflict and _distinctive_position_conflict(files, scored[0], [row])) or
                            (digital_edition_choice and _full_coverage_digital_alternative(
                                scored[0], [row], len(files), lambda candidate: _release_metadata(
                                    candidate, release_meta_cache, exact_musicbrainz_releases))))
                        visible_candidates = _review_candidates(scored[0], ranked_candidates, blockers)
                        differences = {(row[3], row[4]): _candidate_difference(scored[0], row)
                                       for row in visible_candidates if row is not scored[0]}
                        issue_type = ('reconciliation_release' if album_id is not None and
                                      (tied or locked_conflict or edition_conflict or
                                       symmetric_mismatch or position_conflict or
                                       digital_edition_choice)
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
                            'evidence_signature': _evidence_signature(
                                files, [(row.source, row.release_id, row.clear, row.matched)
                                        for row in ranked_candidates]),
                            'release_decision_reason': description,
                        }
                        case_id = _persist_selected_plan(
                            context.db, choice_common, files, scored[0], inventory=album_inventory,
                            candidates=visible_candidates, state='release_conflict',
                            candidate_differences=differences,
                            candidate_metadata={
                                (row.source, row.release_id): _release_metadata(
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
                                     'candidates': [_candidate_evidence(
                                         files, row, _release_metadata(
                                             row, release_meta_cache,
                                             exact_musicbrainz_releases))
                                                    | {'difference_summary': differences.get((row[3], row[4]), '')}
                                                    for row in visible_candidates]}):
                            result.findings_created += 1
                        cache.checkpoint()
                        continue
                    forced_pairs = {}
                    for file_index, file in enumerate(files):
                        from core.library.reconciliation_choices import assignment_for_file
                        override = assignment_for_file(assignment_overrides, album_id, source, release_id, file, files, db=context.db)
                        if not override:
                            continue
                        slot_key, size_bytes, mtime_ns, fingerprint_key = override
                        if (file['size_bytes'] != size_bytes or file['mtime_ns'] != mtime_ns or
                                (file['acoustid'].get('fingerprint_key') or '') != fingerprint_key):
                            continue
                        slot_index = next((index for index, slot in enumerate(slots)
                                           if _wanted_slot_key(slot) == slot_key), None)
                        if slot_index is not None:
                            forced_pairs[file_index] = slot_index
                    if forced_pairs:
                        try:
                            plan = _match_release(files, slots, source=source, release_id=release_id,
                                                  forced_pairs=forced_pairs, catalogue_review=bool(pinned_row))
                        except ValueError as exc:
                            logger.warning('Ignoring conflicting manual assignments for %s: %s',
                                           folder, exc)
                        else:
                            scored[0] = ScoredCandidate(sum(pair['confidence'] in ('clear', 'manual') for pair in plan['pairs']),
                                         len(plan['pairs']), -len(plan['extra_files']),
                                         source, release_id, slots, plan)
                    release_meta = _release_metadata(
                        scored[0], release_meta_cache, exact_musicbrainz_releases)
                    # MusicBrainz album IDs can be release-group IDs. Its album
                    # adapter picks a concrete representative release and exposes
                    # that ID here; a missing-track request must target that exact
                    # edition even while the stored canonical pin remains a group.
                    exact_release_id = (release_meta.get('musicbrainz_release_id')
                                        if source == 'musicbrainz' else None) or release_id
                    identity_target = _release_identity_target(files, album, source, exact_release_id)
                    consistency = album_consistency(files)
                    consistent_album_title = (next(
                        (str((member.get('tags') or {}).get('album') or '').strip()
                         for member in files if str(
                             (member.get('tags') or {}).get('album') or '').strip()), '')
                        if 'album' not in consistency else '')
                    release_title = (release_meta.get('name') or release_meta.get('title') or
                                     (album.get('album_title') if album else '') or
                                     os.path.basename(folder))
                    library_album_title = album.get('album_title') if album else ''
                    display_release_title = _display_album_title(
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
                        'evidence_signature': _evidence_signature(files, (source, release_id)),
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
                        'key': _wanted_slot_key(slot), 'title': slot['title'],
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
                        if (_same_recording_copies(files, indexes) and
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
                        if not _same_recording_copies(files, list(indexes)):
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
                            not _pair_audio_conflict(files[pair['file_index']], slots[pair['slot_index']], pair)
                            for pair in plan['pairs']))
                    planned_paths = {}
                    for pair in _review_pairs(files, plan):
                        member, target_slot = files[pair['file_index']], slots[pair['slot_index']]
                        absolute_number = (sum(total for disc, total in disc_totals.items()
                            if disc < target_slot['disc_number']) + target_slot['track_number']
                            if absolute_filenames else None)
                        planned_paths[member['path']] = (_suggest_filename(member['path'], target_slot,
                            absolute_number=absolute_number) if member.get('db') else '')
                    target_counts = Counter(path for path in planned_paths.values() if path)
                    tool_evidence.compare(files, slots, plan, source)
                    plan_action_changes: dict[str, dict] = {}
                    for pair in _review_pairs(files, plan):
                        file = files[pair['file_index']]
                        slot = slots[pair['slot_index']]
                        tags = file['tags']
                        if (pair['confidence'] not in ('clear', 'manual') or
                                _pair_audio_conflict(file, slot, pair)):
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
                                             if _pair_audio_conflict(file, slot, pair) else
                                             'This file fits a release slot, but the evidence is not decisive.'),
                                details={**common, 'current_tags': tags, 'acoustid': file['acoustid'],
                                         'tool_checks': file.get('tool_checks') or [],
                                         'audio_conflict': ('different_recording'
                                                            if _pair_audio_conflict(file, slot, pair)
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
                            _album_title_preservable(preserved_album_title, release_title)
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
                        # Minor title spelling/version annotations are often valid;
                        # only act on a substantive title difference or hard slot
                        # fields. Album/artist edits require an explicit source.
                        changed = tag_plan['changes']
                        if (settings['album_title_policy'] == 'preserve' and 'album' not in consistency
                                and _album_title_preservable(current_album_title, release_title)):
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
                                       if id_conflict or set(consistency).intersection(changed) else ''),
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

                    missing = [{**slots[i], 'wanted_key': _wanted_slot_key(slots[i])}
                               for i in plan['missing_slots']]
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
                        duplicate_copies = [_duplicate_copy(files[index])
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
                            not _pair_audio_conflict(files[pair['file_index']], slots[pair['slot_index']], pair)
                            for pair in plan['pairs'])
                    )
                    intent_inferred = bool(missing) and assignment_settled and _inferred_partial(
                        files, album, watched_artists, saved_intent)
                    if intent_inferred:
                        intent_scope = 'partial'
                    # A requested slot is already decided. Asking about it again
                    # must not depend on a broader whole-album scope choice.
                    undecided_missing = [slot for slot in missing
                        if _wanted_slot_key(slot) not in wanted_slots and
                        (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) not in wishlisted_slots]
                    if missing and not undecided_missing and not intent_scope:
                        intent_scope = 'partial'
                        wanted_slots.update(_wanted_slot_key(slot) for slot in missing)
                    if missing and album_id is not None:
                        if not intent_scope and assignment_settled and undecided_missing:
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_intent',
                                severity='info', entity_type='album', entity_id=str(album_id),
                                file_path=None, title=f'Is {common["album_title"]} meant to be complete?',
                                description=f'{len(undecided_missing)} release track(s) need a collection choice.',
                                details={**common, 'missing_tracks': undecided_missing,
                                         'suggested_scope': 'partial' if len(files) == 1 and files[0].get('playlists') else 'complete'}):
                                result.findings_created += 1
                        for slot in missing:
                            key = missing_slot_key(album_id, source, release_id, slot)
                            fulfillment_state = (
                                'wishlisted' if
                                (source, str(slot.get('source_track_id') or ''),
                                 str(exact_release_id)) in wishlisted_slots else '')
                            wanted = intent_scope == 'complete' or _wanted_slot_key(slot) in wanted_slots
                            if not wanted and not fulfillment_state:
                                continue
                            if not assignment_settled and not fulfillment_state:
                                continue
                            if (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) in getattr(self, '_published_by_profile', {}).get(int(common.get('profile_id') or 1), set()):
                                fulfillment_state = 'downloaded'
                            if not fulfillment_state and (source, str(slot.get('source_track_id') or ''), str(exact_release_id)) in getattr(self, '_cancelled_by_profile', {}).get(int(common.get('profile_id') or 1), set()):
                                fulfillment_state = 'cancelled'
                            if emit(
                                job_id=self.job_id, finding_type='reconciliation_missing',
                                severity='info', entity_type='album_slot', entity_id=key,
                                file_path=None, title=f'Missing: {slot["title"]}',
                                description=('Acquisition was cancelled. Request this track again or deliberately change the album completeness choice.' if fulfillment_state == 'cancelled' else 'This track is absent from the intended complete release.'),
                                details={**common, 'fulfillment_state': fulfillment_state,
                                         'missing_tracks': [{
                                    'name': slot['title'], 'track_number': slot['track_number'],
                                    'disc_number': slot['disc_number'],
                                    'source_track_id': slot['source_track_id'],
                                    'source': source, 'duration_ms': slot['duration_ms'],
                                }] }):
                                result.findings_created += 1
                    case_id = _persist_selected_plan(
                        context.db, common, files, scored[0], inventory=album_inventory, candidates=scored,
                        candidate_metadata={
                            (row.source, row.release_id): _release_metadata(
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
                    from core.library.reconciliation_redirects import record_relocation
                    record_relocation(context.db, relocated_from, next(iter(observed_plans)))
                cache.checkpoint()
                if target_folder is None:
                    _retire_stale_findings(context.db, observed)
                    if plans_complete:
                        from core.library.reconciliation_redirects import record_observed_relocations
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
