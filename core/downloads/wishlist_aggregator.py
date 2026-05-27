"""Merge sibling download_batches statuses into one view for the
wishlist-run model.

When the wishlist runs are split into per-album sub-batches
(Phase 1c.2.1), the frontend modal polls the ORIGINAL batch id
allocated by ``start_manual_wishlist_download_batch`` /
``process_wishlist_automatically``. That batch id is now just one
sibling among N. Without merging, the modal goes blank after the
first sibling finishes because subsequent siblings live under
fresh batch ids the modal never learned about.

This module is the merge layer: pure function, no IO, no runtime
state. ``build_batched_status`` in ``core/downloads/status.py``
calls into it when a requested batch has ``wishlist_run_id`` set
and at least one sibling exists.

Design notes:

- ``track_index`` re-indexed to a global 0..N-1 across the merged
  results so the modal's ``data-track-index`` DOM keys don't
  collide between siblings (each sibling locally starts at 0).
  Tasks reference their analysis result via track_index, so the
  remap is applied to tasks too.
- ``task_id`` is a uuid per task — no collision concern across
  siblings.
- Phase aggregation surfaces the LEAST-complete pre-terminal phase
  so the modal stays "alive" until every sibling is done. Sticky
  ``error`` so failures don't get hidden by a running sibling.
- ``album_bundle`` is picked from whichever sibling currently has
  an active bundle download — gives the user a useful progress
  bar even when the primary sibling is past its bundle stage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_PHASE_PRIORITY = (
    'analysis',
    'album_downloading',
    'downloading',
    'complete',
)
_ACTIVE_BUNDLE_STATES = frozenset({
    'searching',
    'downloading',
    'downloading_release',
    'staging',
})


def _aggregate_phases(phases: List[str]) -> str:
    """Pick the merged phase for a multi-sibling wishlist run.

    Rules:
    - ``error`` is sticky — if any sibling errored, surface error.
    - Otherwise return the LEAST-complete pre-terminal phase in
      priority order (analysis < album_downloading < downloading
      < complete).
    - If all siblings are ``complete``, return ``complete``.
    - Fallback to the first non-empty phase if nothing matches a
      known priority.
    """
    phases = [p for p in phases if p]
    if not phases:
        return 'unknown'
    if 'error' in phases:
        return 'error'
    for p in _PHASE_PRIORITY:
        if p in phases:
            if p == 'complete':
                return 'complete' if all(s == 'complete' for s in phases) else 'downloading'
            return p
    return phases[0]


def _pick_active_album_bundle(statuses: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the album_bundle of whichever sibling is currently
    staging or downloading. Falls back to the first non-empty
    bundle when nothing is active (so a completed bundle still
    shows up vs. a totally empty progress bar)."""
    fallback = None
    for s in statuses:
        bundle = s.get('album_bundle')
        if not bundle:
            continue
        if fallback is None:
            fallback = bundle
        state = (bundle.get('state') or '').lower()
        if state in _ACTIVE_BUNDLE_STATES:
            return bundle
    return fallback


def merge_wishlist_run_status(
    primary: Dict[str, Any],
    siblings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a status dict that merges ``siblings`` into ``primary``.

    Empty ``siblings`` is the legacy single-batch case — primary
    is returned unchanged.

    The returned dict has the same shape as a single-batch status
    response from ``build_batch_status_data`` so the frontend
    modal needs no changes to consume it. Tracks and tasks are
    re-indexed globally; phase + progress + active_count
    aggregated across the run.
    """
    if not siblings:
        return primary

    all_statuses = [primary] + list(siblings)

    # Phase aggregation.
    merged_phase = _aggregate_phases([s.get('phase', '') for s in all_statuses])

    # Analysis progress — sum across siblings.
    total = 0
    processed = 0
    has_progress = False
    for s in all_statuses:
        ap = s.get('analysis_progress')
        if isinstance(ap, dict):
            total += int(ap.get('total') or 0)
            processed += int(ap.get('processed') or 0)
            has_progress = True

    # Analysis results — concat + re-index. Build a (batch_obj_id,
    # old_track_index) -> new_track_index map so tasks can be
    # re-indexed consistently.
    merged_results: List[Dict[str, Any]] = []
    track_index_remap: Dict[tuple, int] = {}
    next_index = 0
    for s in all_statuses:
        batch_ref = id(s)
        for r in (s.get('analysis_results') or []):
            old_idx = int(r.get('track_index') or 0)
            track_index_remap[(batch_ref, old_idx)] = next_index
            new_r = dict(r)
            new_r['track_index'] = next_index
            merged_results.append(new_r)
            next_index += 1

    # Tasks — concat + re-index using the remap above. Tasks
    # without a remapped entry keep their original track_index
    # (defensive — shouldn't happen if analysis_results is
    # consistent with the task list).
    merged_tasks: List[Dict[str, Any]] = []
    for s in all_statuses:
        batch_ref = id(s)
        for t in (s.get('tasks') or []):
            old_idx = int(t.get('track_index') or 0)
            new_t = dict(t)
            new_t['track_index'] = track_index_remap.get((batch_ref, old_idx), old_idx)
            merged_tasks.append(new_t)
    merged_tasks.sort(key=lambda x: x.get('track_index', 0))

    # Album bundle — pick the active sibling's, fall back to first
    # bundle present, omit if none.
    merged_bundle = _pick_active_album_bundle(all_statuses)

    # Worker accounting — sum active_count across siblings so the
    # modal's overall download progress display reflects total
    # in-flight work; max_concurrent stays from primary as
    # representative.
    active_total = sum(int(s.get('active_count') or 0) for s in all_statuses)

    merged = dict(primary)  # keeps playlist_id, playlist_name, error, etc.
    merged['phase'] = merged_phase
    if has_progress:
        merged['analysis_progress'] = {'total': total, 'processed': processed}
    merged['analysis_results'] = merged_results
    if merged_tasks or 'tasks' in primary:
        merged['tasks'] = merged_tasks
    if merged_bundle:
        merged['album_bundle'] = merged_bundle
    elif 'album_bundle' in primary:
        merged['album_bundle'] = primary['album_bundle']
    merged['active_count'] = active_total

    return merged


__all__ = ['merge_wishlist_run_status']
