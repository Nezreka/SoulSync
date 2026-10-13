"""Current library presence of a quarantine entry's intended recording."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from core.downloads.atomic_album_publish import contains_staging_segment
from core.imports.context import get_import_context_album, get_import_track_info
from core.imports.paths import config_root_path, import_profile_id, library_root_for_profile
from core.imports.quarantine import get_quarantine_entry_context
from core.library.path_resolver import resolve_library_file_path
from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope
from core.wishlist.library_match import _strict_identity_matches
from utils.logging_config import get_logger

logger = get_logger("imports.quarantine_library")


def _identifiers(context):
    info = get_import_track_info(context)
    external = info.get('external_ids') or {}
    if not isinstance(external, dict):
        external = {}
    source = str(context.get('source') or info.get('source') or '').lower()
    return {
        'isrc': str(info.get('isrc') or external.get('isrc') or '').strip(),
        'spotify': str(info.get('spotify_track_id') or external.get('spotify') or
                       (info.get('id') if source == 'spotify' else '') or '').strip(),
    }


def _usable_path(path):
    parts = str(path or '').replace('\\', '/').split('/')
    return bool(path and not contains_staging_segment(str(path))
                and 'ss_quarantine' not in parts
                and '.deleted' not in parts
                and not str(path).endswith('.quarantined'))


def _inside(path, root):
    if not root:
        return False
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def _library_paths(database, config):
    """Published roots only: downloader and private-library roots are separate."""
    shared = [config_root_path(config.get('soulseek.transfer_path', './Transfer'))]
    music_paths = config.get('library.music_paths', []) or []
    if isinstance(music_paths, (list, tuple)):
        shared.extend(config_root_path(path) for path in music_paths if path)
    private = [(profile['id'], config_root_path(profile['root']))
               for profile in database.get_own_library_profiles(strict=True)]
    return shared, private, config_root_path(config.get('soulseek.download_path', ''))


def _presence(database, entry, context, config, library_paths):
    title, artist = entry.get('expected_track') or '', entry.get('expected_artist') or ''
    if not title or not artist:
        return 'unknown', None
    profile = import_profile_id(context)
    scope = library_scope_for_profile(profile)
    root = library_root_for_profile(profile, announce=False)
    shared, private, download_root = library_paths
    roots = [config_root_path(root)] if root else (shared if scope == 'shared' else [])
    foreign = [path for owner, path in private if owner != scope]
    deleted_roots = [os.path.join(path, 'deleted') for path in roots + [p for _, p in private]]

    def permitted(path):
        return (_usable_path(path) and _usable_path(os.path.realpath(path))
                and not any(_inside(path, folder) for folder in foreign + deleted_roots)
                and (not _inside(path, download_root)
                     or any(_inside(path, folder) for folder in roots)))

    def resolve(raw):
        if not _usable_path(raw):
            return None
        if os.path.isfile(raw):
            return raw if permitted(raw) and (not root or _inside(raw, root)) else None
        for folder in roots:
            path = resolve_library_file_path(raw, library_root=folder)
            if path and os.path.isfile(path) and permitted(path):
                return path
        return None

    identifiers = _identifiers(context)
    album = get_import_context_album(context).get('name') or ''
    token = set_library_scope(scope)
    try:
        candidates = database.get_quarantine_library_candidates(
            title, [artist], identifiers, config.get_active_media_server())
    finally:
        reset_library_scope(token)
    # A relative catalogue path and an absolute history path may be the same
    # file. Conflicting recording IDs must reject both spellings of that copy.
    resolved = {}
    for candidate in candidates:
        raw = candidate.get('file_path')
        conflict = (identifiers.get('isrc') and candidate.get('isrc')
                    and identifiers['isrc'].casefold() != candidate['isrc'].casefold())
        relevant = _strict_identity_matches(SimpleNamespace(**candidate), title, artist, album, False)
        if raw and raw not in resolved and candidate.get('file_state', 'active') == 'active' and (relevant or conflict):
            resolved[raw] = resolve(raw)
    conflicting_paths = {os.path.normcase(os.path.realpath(path))
                         for candidate in candidates
                         if identifiers.get('isrc') and candidate.get('isrc')
                         and identifiers['isrc'].casefold() != candidate['isrc'].casefold()
                         and (path := resolved.get(candidate.get('file_path')))}
    uncertain = False
    for candidate in candidates:
        if not _usable_path(candidate.get('file_path')):
            continue
        if candidate.get('file_state', 'active') != 'active':
            continue
        expected_isrc, found_isrc = identifiers.get('isrc'), candidate.get('isrc')
        if expected_isrc and found_isrc and expected_isrc.casefold() != found_isrc.casefold():
            continue
        if not _strict_identity_matches(SimpleNamespace(**candidate), title, artist, album, False):
            continue
        path = resolved.get(candidate['file_path'])
        if not path:
            uncertain = True
            continue
        if os.path.normcase(os.path.realpath(path)) in conflicting_paths:
            continue
        # History has no owner column. Its physical output root must prove it
        # belongs to this library before it can fill the pre-scan gap.
        if candidate.get('_history_only') and not any(_inside(path, folder) for folder in roots):
            continue
        quality = candidate.get('quality') or Path(path).suffix.lstrip('.').upper()
        if not candidate.get('quality') and candidate.get('bitrate'):
            quality += f" {candidate['bitrate']} kbps"
        return 'present', {
            'album': candidate.get('album_title') or '',
            'filename': Path(path).name,
            'quality': quality,
            'verification_status': candidate.get('verification_status') or 'unknown',
        }
    return ('unknown' if uncertain else 'missing'), None


def annotate_quarantine_library(entries, quarantine_dir, config):
    """Enrich in place. One lookup per target and library, fresh on every call.

    An unavailable lookup must keep the queue usable without claiming absence.
    Thin sidecars fall back to the shared library and their expected metadata.
    """
    if not entries:
        return
    from database.music_database import get_database
    try:
        database = get_database()
        library_paths = _library_paths(database, config)
    except Exception as exc:
        logger.debug("Quarantine library lookup unavailable: %s", exc)
        for entry in entries:
            entry.update(library_status='unknown', library_match=None)
        return
    matches = {}
    for entry in entries:
        context = get_quarantine_entry_context(quarantine_dir, entry['id'])
        key = (entry.get('expected_track'), entry.get('expected_artist'),
               import_profile_id(context), str(get_import_context_album(context).get('name') or ''),
               tuple(sorted(_identifiers(context).items())))
        if key not in matches:
            try:
                matches[key] = _presence(database, entry, context, config, library_paths)
            except Exception as exc:
                logger.debug("Quarantine library lookup for %s failed: %s", entry['id'], exc)
                matches[key] = ('unknown', None)
        entry['library_status'], entry['library_match'] = matches[key]
