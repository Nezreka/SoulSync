"""Album-bundle dispatch for torrent / usenet single-source downloads.

Lifted from ``run_full_missing_tracks_process`` so the master
worker doesn't carry a 90-line inline branch and so the gate logic
can be unit-tested in isolation.

The gate fires only when ALL conditions hold:

- Batch is an album-context download (``is_album_download`` flag).
- Active download source is ``torrent``, ``usenet``, or ``soulseek``.
  In hybrid mode the caller may pass the first configured source as a
  source override; later hybrid sources stay per-track to preserve fallback.
- Both album-name and artist-name are populated in batch context.
- The resolved plugin exposes ``download_album_to_staging``.

When the gate engages it runs the plugin synchronously (the master
worker is already on a thread-pool executor) and mirrors the
plugin's lifecycle payloads into the batch state so the Downloads
page can render meaningful progress before per-track tasks exist.

Return semantics: ``True`` means the gate handled the batch — the
master worker should stop and not run per-track analysis. ``False``
means the gate didn't engage (or engaged-and-fell-back) — caller
continues the normal per-track flow.

The ``BatchStateAccess`` Protocol exists so this module doesn't
import ``download_batches`` from runtime_state directly. The
caller (master worker) injects accessors so this module stays
testable without touching live runtime state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from utils.logging_config import get_logger

# Use the project logger factory so these lines land in app.log under the
# ``soulsync.*`` namespace the file handler captures. Plain
# ``logging.getLogger(__name__)`` logs to the console only (the file
# handler is attached to the ``soulsync`` logger), which is why
# ``[Album Bundle] flow failed`` showed up in the terminal but never in
# app.log during the #721 triage.
logger = get_logger("downloads.album_bundle_dispatch")


class BatchStateAccess(Protocol):
    """Narrow shim around the batch-state dict ops the dispatch needs.

    Two methods to keep the surface small:
    - ``update_fields(batch_id, fields)`` — atomic merge into the
      batch dict under tasks_lock.
    - ``mark_failed(batch_id, error)`` — convenience for the failure
      path (sets phase + error + album_bundle_state in one shot).
    """

    def update_fields(self, batch_id: str, fields: dict) -> None: ...

    def mark_failed(self, batch_id: str, error: str) -> None: ...


# Fields the album-bundle progress callback may carry. Anything in
# this set gets mirrored onto the batch row as ``album_bundle_<key>``
# so the Downloads page can render it without coupling to the
# specific payload shape.
_MIRRORED_KEYS = ('progress', 'release', 'speed', 'downloaded',
                  'size', 'seeders', 'grabs', 'count', 'failed')


def is_eligible(
    *,
    mode: str,
    is_album: bool,
    album_name: str,
    artist_name: str,
) -> bool:
    """Pure predicate: does this batch even qualify for the album
    flow? Separate from the resolution+run step so tests can pin
    the gate logic without standing up a plugin."""
    if not is_album:
        return False
    if (mode or '').lower() not in ('torrent', 'usenet', 'soulseek'):
        return False
    if not (album_name or '').strip():
        return False
    if not (artist_name or '').strip():
        return False
    return True


def try_dispatch(
    *,
    batch_id: str,
    is_album: bool,
    album_context: Optional[dict],
    artist_context: Optional[dict],
    config_get: Callable[..., Any],
    plugin_resolver: Callable[[str], Optional[Any]],
    state: BatchStateAccess,
    source_override: Optional[str] = None,
    plugin_kwargs: Optional[dict] = None,
) -> bool:
    """Attempt the album-bundle flow. Returns ``True`` iff the
    master worker should return early (gate engaged and completed
    — success OR failure). ``False`` means fall through to the
    normal per-track flow.

    ``config_get`` is a callable shaped like ``config_manager.get``;
    ``plugin_resolver`` resolves a source-name string to an
    initialised plugin instance (or None); ``state`` is the
    BatchStateAccess shim. Injecting these keeps the module
    dependency-light + unit-testable.
    """
    mode = (source_override or config_get('download_source.mode', 'soulseek') or 'soulseek').lower()
    album_name = (album_context or {}).get('name') or ''
    artist_name = (artist_context or {}).get('name') or ''

    if not is_eligible(mode=mode, is_album=is_album,
                       album_name=album_name, artist_name=artist_name):
        return False

    album_name = album_name.strip()
    artist_name = artist_name.strip()

    plugin = None
    try:
        plugin = plugin_resolver(mode)
    except Exception as exc:
        logger.warning("[Album Bundle] Could not resolve %s plugin: %s", mode, exc)

    if plugin is None or not hasattr(plugin, 'download_album_to_staging'):
        logger.warning(
            "[Album Bundle] Gate matched but plugin / context unavailable "
            "(mode=%s album=%r artist=%r plugin=%s) — falling back to per-track flow",
            mode, album_name, artist_name,
            type(plugin).__name__ if plugin else None,
        )
        return False

    staging_root = config_get(
        'download_source.album_bundle_staging_path',
        'storage/album_bundle_staging',
    ) or 'storage/album_bundle_staging'
    staging_dir = str(Path(staging_root) / _safe_batch_dirname(batch_id))
    logger.info(
        "[Album Bundle] Engaging %s album flow for '%s' by '%s' -> %s",
        mode, album_name, artist_name, staging_dir,
    )
    state.update_fields(batch_id, {
        'phase': 'album_downloading',
        'album_bundle_state': 'searching',
        'album_bundle_source': mode,
        'album_bundle_staging_path': staging_dir,
        'album_bundle_private_staging': True,
    })

    def _emit(payload):
        """Mirror plugin lifecycle into batch state for UI rendering."""
        try:
            fields = {'album_bundle_state': payload.get('state', '')}
            for key in _MIRRORED_KEYS:
                if key in payload:
                    fields[f'album_bundle_{key}'] = payload[key]
            state.update_fields(batch_id, fields)
        except Exception as exc:
            logger.debug("[Album Bundle] emit failed: %s", exc)

    try:
        outcome = plugin.download_album_to_staging(
            album_name, artist_name, staging_dir, _emit,
            **(plugin_kwargs or {}),
        )
    except Exception as exc:
        logger.exception("[Album Bundle] %s plugin raised: %s", mode, exc)
        # An OSError means an I/O step failed after the source already had the
        # album — most importantly the staging dir not being writable (#760),
        # but also any transient filesystem error. Treat it as fallback-eligible
        # so we return to the per-track flow instead of hard-failing the whole
        # batch (the #715 symptom: files download, then the batch fails).
        # Programming errors (TypeError, KeyError, …) are NOT OSError and stay
        # terminal, so genuine bugs still fail loudly. (requests' network
        # exceptions also subclass OSError, but plugins normally catch those
        # internally and return an outcome rather than raising; if one does
        # surface here, falling back to per-track is still the safe choice.)
        is_io_failure = isinstance(exc, OSError)
        outcome = {
            'success': False,
            'error': f'Plugin error: {exc}',
            'fallback': is_io_failure,
        }

    if not outcome.get('success'):
        err = outcome.get('error', 'Album bundle download failed')
        if outcome.get('fallback'):
            logger.warning(
                "[Album Bundle] %s flow could not commit for '%s': %s — falling back to per-track flow",
                mode, album_name, err,
            )
            state.update_fields(batch_id, {
                'phase': 'analysis',
                'album_bundle_state': 'fallback',
                'album_bundle_error': err,
                'album_bundle_private_staging': False,
                'album_bundle_staging_path': None,
            })
            return False
        logger.error("[Album Bundle] %s flow failed for '%s': %s",
                     mode, album_name, err)
        state.mark_failed(batch_id, err)
        return True

    logger.info(
        "[Album Bundle] %s staged %d files for '%s' — handing off to per-track staging matcher",
        mode, len(outcome.get('files', [])), album_name,
    )
    state.update_fields(batch_id, {
        'phase': 'analysis',
        'album_bundle_state': 'staged',
        'album_bundle_partial': bool(outcome.get('partial')),
        'album_bundle_expected_count': outcome.get('expected_count'),
        'album_bundle_completed_count': outcome.get('completed_count', len(outcome.get('files', []))),
    })
    # Engaged-and-succeeded: we DON'T early-return because the
    # per-track flow needs to run to create + complete the per-track
    # task rows. Those tasks will hit try_staging_match and pull the
    # files we just staged.
    return False


def _safe_batch_dirname(batch_id: str) -> str:
    safe = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '_' for ch in str(batch_id or 'batch'))
    return safe or 'batch'
