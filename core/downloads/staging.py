"""Staging-folder match shortcut for downloads.

`try_staging_match(task_id, batch_id, track, deps)` is the per-track
shortcut the task worker calls before kicking off a Soulseek search.
If the user has dropped audio files matching the track into the
configured staging folder, we copy directly to the transfer dir and
hand off to post-processing — skipping the network round-trip entirely.

1. Pull the staging-file cache for the batch (one scan per batch).
2. Compute title + artist similarity (SequenceMatcher) against each
   staging entry; require title >= 0.80 and combined score >= 0.75.
   Score weighting flips based on whether artist info is available on
   both sides:
   - both have artist: 0.55*title + 0.45*artist
   - either side missing artist: 0.80*title + 0.20*artist (lean on title)
3. Copy the matched file to the transfer dir (suffix "_staging" if a
   file with that name already exists).
4. Mark the task as 'post_processing' with username='staging'.
5. Build a synthetic spotify_artist / spotify_album context (mirrors
   the modal-worker's logic so the path template applies cleanly) and
   store it in matched_downloads_context under "staging_<task_id>".
6. Hand off to `_post_process_matched_download_with_verification` which
   does tagging, path building, AcoustID verification, and DB insertion.

Returns True if the staging shortcut won; False to fall through to the
normal Soulseek search path.

Lifted verbatim from web_server.py. Wide dependency surface
(matching_engine, post-processing helper, file-system helpers, staging
cache, runtime state) all injected via `StagingDeps`.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

# `shutil` and `SequenceMatcher` are imported inline inside try_staging_match()
# to keep the lift byte-identical with the original web_server.py function body.

from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

logger = logging.getLogger(__name__)


def _coerce_positive_int(value: Any, default: int = 0) -> int:
    try:
        coerced = int(str(value).split('/')[0])
    except (TypeError, ValueError):
        return default
    return coerced if coerced > 0 else default


def _extract_explicit_track_number(filename: str) -> int:
    """Extract a track number only when the filename visibly carries one."""
    basename = os.path.splitext(os.path.basename(str(filename or '')))[0].strip()
    if not basename:
        return 0

    match = re.match(r"^\d[\-\.](\d{1,2})\s*[\-\.]\s*", basename)
    if match:
        num = int(match.group(1))
        return num if 1 <= num <= 99 else 0

    match = re.match(r"^\(?(\d{1,3})\)?\s*[\-\.)\]]\s*", basename)
    if match:
        num = int(match.group(1))
        return num if 1 <= num <= 999 else 0

    return 0


def _extract_release_filename_title(stem: str) -> str:
    """Extract the trailing title segment from a release-style filename stem.

    Slskd album bundles often arrive untagged with stems like
    'Artist - Album - 03 - Title' or '03 - Title'. The full stem is too
    noisy to fuzzy-match against a clean Spotify title, so when we
    detect a bare track-number segment between ' - ' delimiters we
    return the trailing segments as an extra match candidate.

    Returns '' when no clear track-number signal is present so we don't
    accidentally extract tails from real song titles that legitimately
    contain ' - ' (e.g. 'Hold Me - Live').
    """
    if not stem:
        return ''
    parts = [p.strip() for p in stem.split(' - ') if p.strip()]
    if len(parts) < 2:
        return ''
    for i, part in enumerate(parts):
        if re.fullmatch(r'\d{1,3}', part) and i < len(parts) - 1:
            return ' - '.join(parts[i + 1:]).strip()
    return ''


def _staging_title_variants(title: Any, normalize: Callable[[str], str]) -> list[str]:
    """Return conservative title variants for release-file matching.

    Torrent / usenet release files often encode featured artists in the
    filename/title while streaming metadata keeps them in the artist credit.
    Strip only feature/bonus noise here; keep version words like remix,
    extended, live, acoustic, etc. so distinct recordings do not collapse.
    """
    raw = str(title or '').strip()
    if not raw:
        return []

    compacted_separators = re.sub(r'[_]+', ' ', raw)
    compacted_separators = re.sub(r'\s+', ' ', compacted_separators).strip()

    without_feat = re.sub(
        r'\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+[^)\]]*[\)\]]',
        '',
        compacted_separators,
        flags=re.IGNORECASE,
    )
    without_feat = re.sub(
        r'\s+(?:feat\.?|ft\.?|featuring)\s+.*$',
        '',
        without_feat,
        flags=re.IGNORECASE,
    )
    without_bonus = re.sub(
        r'\s*[\(\[]\s*bonus\s+track\s*[\)\]]',
        '',
        without_feat,
        flags=re.IGNORECASE,
    )

    release_tail = _extract_release_filename_title(compacted_separators)

    variants: list[str] = []
    for candidate in (raw, compacted_separators, without_feat, without_bonus, release_tail):
        normalized = normalize(candidate)
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants


@dataclass
class StagingDeps:
    """Bundle of cross-cutting deps the staging-match helper needs."""
    config_manager: Any
    matching_engine: Any
    get_staging_file_cache: Callable[[str], list]
    docker_resolve_path: Callable[[str], str]
    post_process_matched_download_with_verification: Callable
    # Optional batch-field accessor. Returns ``download_batches[batch_id].get(field)``
    # when the runtime state is available, ``None`` otherwise. Injected so
    # this module doesn't have to import from runtime_state directly —
    # keeps the dep surface explicit and the function unit-testable
    # without a live batch dict.
    get_batch_field: Callable[[str, str], Any] = None    # type: ignore[assignment]


def try_staging_match(task_id, batch_id, track, deps: StagingDeps):
    """Check if a matching file exists in the staging folder before downloading.

    Returns True if a match was found and the file was moved to the transfer folder.
    Returns False to fall through to normal download.

    Every silent-False exit point logs at INFO with the rejection reason
    so #706 / #708-class "track staged but never imported, ends up
    re-added to wishlist" loops can be diagnosed from app.log without
    a re-instrumentation round-trip. Per-candidate skips log at DEBUG
    so the noise stays out of INFO unless explicitly turned up.
    """
    track_title = (track.name or '').strip() if hasattr(track, 'name') else ''
    track_artist = (track.artists[0] if (hasattr(track, 'artists') and track.artists) else '').strip()
    # Compact identifier for the log lines below so a multi-batch
    # wishlist run can be greppable per-track.
    _track_label = f"'{track_title}' by '{track_artist}'"

    staging_files = deps.get_staging_file_cache(batch_id or task_id)
    if not staging_files:
        logger.info(
            "[Staging] No match attempted for %s — staging cache empty for batch %s",
            _track_label, batch_id or task_id,
        )
        return False

    if not track_title:
        logger.info(
            "[Staging] No match attempted for task %s — track has empty title",
            task_id,
        )
        return False

    from difflib import SequenceMatcher
    normalize = deps.matching_engine.normalize_string
    norm_title = normalize(track_title)
    norm_artist = normalize(track_artist)
    title_variants = _staging_title_variants(track_title, normalize) or [norm_title]

    best_match = None
    best_score = 0.0
    # Track per-candidate scoring so the rejection log can show the
    # near-miss that DID exist (useful when title-sim is 0.79 — one
    # point below the threshold).
    candidate_scores: list = []

    for sf in staging_files:
        sf_title_variants = _staging_title_variants(sf['title'], normalize)
        sf_norm_artist = normalize(sf['artist'])

        if not sf_title_variants:
            logger.debug(
                "[Staging] Skip candidate %s — no usable title variants",
                os.path.basename(sf.get('full_path', '?')),
            )
            continue

        # Title similarity (primary)
        title_sim = max(
            SequenceMatcher(None, expected, candidate).ratio()
            for expected in title_variants
            for candidate in sf_title_variants
        )
        if title_sim < 0.80:
            logger.debug(
                "[Staging] Skip candidate %s — title_sim=%.2f below 0.80 threshold (%s vs %s)",
                os.path.basename(sf.get('full_path', '?')),
                title_sim, norm_title, '|'.join(sf_title_variants[:3]),
            )
            candidate_scores.append((sf, title_sim, None, 0.0))
            continue

        # Artist similarity (secondary)
        artist_sim = 0.0
        if norm_artist and sf_norm_artist:
            artist_sim = SequenceMatcher(None, norm_artist, sf_norm_artist).ratio()
        elif not norm_artist and not sf_norm_artist:
            artist_sim = 0.5  # Both unknown — neutral
        elif norm_artist and not sf_norm_artist:
            artist_sim = 0.3  # Staging file lacks artist — partial credit if title is strong
        elif sf_norm_artist and not norm_artist:
            artist_sim = 0.3  # Track lacks artist — same partial credit

        # Combined score: title-weighted (these are user-curated staging files)
        # If artist info is available, require it to match. If not, lean on title.
        if norm_artist and sf_norm_artist:
            combined = (title_sim * 0.55) + (artist_sim * 0.45)
        else:
            combined = (title_sim * 0.80) + (artist_sim * 0.20)

        candidate_scores.append((sf, title_sim, artist_sim, combined))

        if combined > best_score:
            best_score = combined
            best_match = sf

    # Require high confidence to avoid false positives
    if not best_match or best_score < 0.75:
        # Log the rejection with the best near-miss so we can see why
        # the staged files didn't claim this wishlist track. Pre-fix
        # this returned False silently and the loop "download album,
        # stage files, never claim them, re-add to wishlist" was
        # impossible to debug from logs alone.
        if candidate_scores:
            near_miss = max(candidate_scores, key=lambda c: c[3])
            sf, title_sim, artist_sim, combined = near_miss
            logger.info(
                "[Staging] No match for %s in batch %s — best candidate %s "
                "(title_sim=%.2f, artist_sim=%s, combined=%.2f) below 0.75 threshold",
                _track_label, batch_id or task_id,
                os.path.basename(sf.get('full_path', '?')),
                title_sim,
                f"{artist_sim:.2f}" if artist_sim is not None else 'n/a',
                combined,
            )
        else:
            logger.info(
                "[Staging] No match for %s in batch %s — %d staging files "
                "but none had usable title variants",
                _track_label, batch_id or task_id, len(staging_files),
            )
        return False

    logger.info(f"[Staging] Match found for '{track_title}' by '{track_artist}': "
          f"{os.path.basename(best_match['full_path'])} (score: {best_score:.2f})")

    # Copy the file to the transfer folder
    try:
        transfer_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.transfer_path', './Transfer'))
        dest_filename = os.path.basename(best_match['full_path'])
        dest_path = os.path.join(transfer_dir, dest_filename)
        os.makedirs(transfer_dir, exist_ok=True)

        # Don't overwrite existing files
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(dest_filename)
            dest_path = os.path.join(transfer_dir, f"{base}_staging{ext}")

        import shutil
        shutil.copy2(best_match['full_path'], dest_path)
        logger.info(f"[Staging] Copied to transfer: {dest_path}")

        # Mark task as completed with staging context.
        # If the batch was populated by the torrent / usenet album-bundle
        # flow, prefer that provenance label over generic 'staging' so the
        # download history reflects the real source. The accessor is
        # injected via StagingDeps so this module doesn't reach into
        # runtime_state directly (see deps.get_batch_field docstring).
        _provenance_override = None
        if batch_id and deps.get_batch_field is not None:
            try:
                _provenance_override = deps.get_batch_field(batch_id, 'album_bundle_source')
            except Exception as _exc:
                logger.debug("get_batch_field failed: %s", _exc)
        _provenance_username = _provenance_override or 'staging'
        _private_album_bundle_staging = False
        if batch_id and deps.get_batch_field is not None:
            try:
                _private_album_bundle_staging = bool(
                    deps.get_batch_field(batch_id, 'album_bundle_private_staging')
                )
            except Exception as _exc:
                logger.debug("get_batch_field failed: %s", _exc)
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'post_processing'
                download_tasks[task_id]['filename'] = dest_path
                download_tasks[task_id]['username'] = _provenance_username
                download_tasks[task_id]['staging_match'] = True

        if _private_album_bundle_staging:
            try:
                os.remove(best_match['full_path'])
                logger.debug("[Staging] Removed private album-bundle staging file: %s", best_match['full_path'])
            except FileNotFoundError:
                pass
            except Exception as _exc:
                logger.debug("[Staging] Could not remove private album-bundle staging file: %s", _exc)

        # Run post-processing (tagging, AcoustID verification, path building)
        context_key = f"staging_{task_id}"
        with tasks_lock:
            track_info = download_tasks.get(task_id, {}).get('track_info', {})
        if not isinstance(track_info, dict):
            track_info = {}
        else:
            track_info = dict(track_info)

        # Build spotify_artist / spotify_album context so post-processing can apply
        # the path template. Without these, _post_process_matched_download returns
        # early and the file stays at the transfer root with its original filename.
        # Mirror the context-building logic from the sync modal worker.
        has_explicit_context = track_info.get('_is_explicit_album_download', False)

        if has_explicit_context:
            explicit_artist = track_info.get('_explicit_artist_context', {})
            if isinstance(explicit_artist, str):
                explicit_artist = {'name': explicit_artist}
            elif not isinstance(explicit_artist, dict):
                explicit_artist = {}
            spotify_artist_ctx = {
                'id': explicit_artist.get('id', 'staging'),
                'name': explicit_artist.get('name', track_artist),
                'genres': explicit_artist.get('genres', [])
            }
            explicit_album = track_info.get('_explicit_album_context', {})
            if not isinstance(explicit_album, dict):
                explicit_album = {}
            _album_image_url = explicit_album.get('image_url')
            if not _album_image_url and explicit_album.get('images'):
                _imgs = explicit_album['images']
                if isinstance(_imgs, list) and _imgs:
                    _album_image_url = _imgs[0].get('url') if isinstance(_imgs[0], dict) else None
            spotify_album_ctx = {
                'id': explicit_album.get('id', 'staging'),
                'name': explicit_album.get('name', getattr(track, 'album', '') or ''),
                'release_date': explicit_album.get('release_date', ''),
                'image_url': _album_image_url,
                'album_type': explicit_album.get('album_type', 'album'),
                'total_tracks': explicit_album.get('total_tracks', 0),
                'total_discs': explicit_album.get('total_discs', 1),
                'artists': explicit_album.get('artists', [{'name': spotify_artist_ctx.get('name', '')}])
            }
            is_album_ctx = True
            has_clean_data = True
        else:
            fallback_album = track_info.get('album', {})
            if isinstance(fallback_album, str):
                fallback_album = {'name': fallback_album}
            elif not isinstance(fallback_album, dict):
                fallback_album = {}
            track_album_name = getattr(track, 'album', '') or fallback_album.get('name', '') or ''
            spotify_artist_ctx = {
                'id': 'staging',
                'name': track_artist or 'Unknown',
                'genres': []
            }
            spotify_album_ctx = {
                'id': 'staging',
                'name': track_album_name,
                'release_date': fallback_album.get('release_date', ''),
                'image_url': fallback_album.get('image_url'),
                'album_type': fallback_album.get('album_type', 'album'),
                'total_tracks': fallback_album.get('total_tracks', 0),
                'total_discs': fallback_album.get('total_discs', 1),
                'artists': [{'name': track_artist}] if track_artist else []
            }
            is_album_ctx = bool(
                track_album_name and
                track_album_name.strip() and
                track_album_name.lower() not in ('unknown album', '') and
                track_album_name.lower() != track_title.lower()
            )
            has_clean_data = bool(track_title and track_artist and track_album_name)

        file_track_number = (
            _coerce_positive_int(best_match.get('track_number'), 0) or
            _extract_explicit_track_number(best_match.get('full_path', ''))
        )
        file_disc_number = _coerce_positive_int(best_match.get('disc_number'), 0)
        if _private_album_bundle_staging:
            track_number = (
                file_track_number or
                _coerce_positive_int(track_info.get('track_number'), 0) or
                _coerce_positive_int(track_info.get('trackNumber'), 0) or
                _coerce_positive_int(getattr(track, 'track_number', 0), 0) or
                1
            )
            disc_number = (
                file_disc_number or
                _coerce_positive_int(track_info.get('disc_number'), 0) or
                _coerce_positive_int(track_info.get('discNumber'), 0) or
                _coerce_positive_int(getattr(track, 'disc_number', 0), 0) or
                1
            )
        else:
            track_number = (
                _coerce_positive_int(track_info.get('track_number'), 0) or
                _coerce_positive_int(track_info.get('trackNumber'), 0) or
                _coerce_positive_int(getattr(track, 'track_number', 0), 0) or
                file_track_number
            )
            disc_number = (
                _coerce_positive_int(track_info.get('disc_number'), 0) or
                _coerce_positive_int(track_info.get('discNumber'), 0) or
                _coerce_positive_int(getattr(track, 'disc_number', 0), 0) or
                file_disc_number or
                1
            )
        track_info['track_number'] = track_number
        track_info['disc_number'] = disc_number

        context = {
            'track_info': track_info,
            'spotify_artist': spotify_artist_ctx,
            'spotify_album': spotify_album_ctx,
            'original_search_result': {
                'username': _provenance_username,
                'filename': best_match.get('full_path', ''),
                'title': track_title,
                'artist': track_artist,
                'spotify_clean_title': track_title,
                'spotify_clean_album': spotify_album_ctx.get('name', ''),
                'spotify_clean_artist': track_artist,
                'track_number': track_number,
                'disc_number': disc_number,
            },
            'is_album_download': is_album_ctx,
            'has_clean_spotify_data': has_clean_data,
            'staging_source': True,
        }

        # Store context in the matched downloads context store (used by post-processing)
        with matched_context_lock:
            matched_downloads_context[context_key] = context

        # Trigger post-processing which handles tagging, path building, and DB insertion
        deps.post_process_matched_download_with_verification(context_key, context, dest_path, task_id, batch_id)
        return True

    except Exception as e:
        logger.error(f"[Staging] Failed to use staging file: {e}")
        return False
