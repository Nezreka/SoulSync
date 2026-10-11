"""Current library presence of a quarantine entry's intended recording."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from core.downloads.atomic_album_publish import contains_staging_segment
from core.imports.context import get_import_context_album, get_import_track_info
from core.imports.paths import import_profile_id, library_root_for_profile
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
                and not str(path).endswith('.quarantined'))


def _inside(path, root):
    if not root:
        return False
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def _presence(database, entry, context, config):
    title, artist = entry.get('expected_track') or '', entry.get('expected_artist') or ''
    if not title or not artist:
        return 'unknown', None
    profile = import_profile_id(context)
    scope = library_scope_for_profile(profile)
    root = library_root_for_profile(profile, announce=False)
    history_root = root or (config.get('soulseek.transfer_path', './Transfer') if scope == 'shared' else None)
    identifiers = _identifiers(context)
    album = get_import_context_album(context).get('name') or ''
    token = set_library_scope(scope)
    try:
        candidates = database.get_quarantine_library_candidates(
            title, [artist], identifiers, config.get_active_media_server())
    finally:
        reset_library_scope(token)
    uncertain = False
    conflicting_paths = {candidate.get('file_path') for candidate in candidates
                         if identifiers.get('isrc') and candidate.get('isrc')
                         and identifiers['isrc'].casefold() != candidate['isrc'].casefold()}
    for candidate in candidates:
        if candidate.get('file_path') in conflicting_paths:
            continue
        if not _usable_path(candidate.get('file_path')):
            continue
        if candidate.get('file_state', 'active') != 'active':
            continue
        expected_isrc, found_isrc = identifiers.get('isrc'), candidate.get('isrc')
        if expected_isrc and found_isrc and expected_isrc.casefold() != found_isrc.casefold():
            continue
        if not _strict_identity_matches(SimpleNamespace(**candidate), title, artist, album, False):
            continue
        path = resolve_library_file_path(candidate['file_path'], config_manager=config, library_root=root)
        if not path or not os.path.isfile(path) or not _usable_path(path):
            uncertain = True
            continue
        # History has no owner column. Its physical output root must prove it
        # belongs to this library before it can fill the pre-scan gap.
        if candidate.get('_history_only') and not _inside(path, history_root):
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
                matches[key] = _presence(database, entry, context, config)
            except Exception as exc:
                logger.debug("Quarantine library lookup for %s failed: %s", entry['id'], exc)
                matches[key] = ('unknown', None)
        entry['library_status'], entry['library_match'] = matches[key]
