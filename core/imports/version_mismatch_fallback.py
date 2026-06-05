"""Last-resort acceptance of a version-mismatched download.

Some tracks simply don't exist on the configured sources in the wanted cut —
every copy is, say, the instrumental. The retry engine correctly rejects each
one (version mismatch) and eventually gives up, leaving the track missing.

This module provides an OPT-IN fallback: once a track's retries are fully
exhausted, if every quarantined candidate for it failed the *same* way (same
matched version, e.g. all ``instrumental``) and there are at least ``min_count``
of them, accept the best (first-tried) one rather than failing outright.

Hard safety rules:
- Only ``Version mismatch`` quarantines qualify. Audio/artist mismatches
  (a genuinely different recording) and integrity/duration failures
  (truncated or wrong file) never participate.
- All qualifying entries must share the same matched version. A mix
  (instrumental + live) is ambiguous → no acceptance.
- The chosen candidate is re-imported with only the AcoustID gate bypassed;
  the integrity / duration / bit-depth gates still run, so a truncated or
  corrupt file is never let through by this path.

``select_version_mismatch_fallback`` is the pure decision core (no I/O) so it
can be tested directly. ``try_accept_version_mismatch_fallback`` wires it to the
quarantine store + re-import dispatch via injected callables.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Matches the reason string written by acoustid_verification's version gate:
#   "Version mismatch: expected '<title>' (<exp>) but file is '<title>' (<got>)"
# We only need the matched (<got>) version to test cross-entry consistency.
_VERSION_MISMATCH_RE = re.compile(
    r"^Version mismatch:.*\bbut file is\b.*\(([^()]+)\)\s*$"
)


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().casefold()


def matched_version(reason: Optional[str]) -> Optional[str]:
    """Return the matched version token (e.g. ``'instrumental'``) for a
    Version-mismatch reason string, or None if the reason isn't a version
    mismatch / can't be parsed."""
    if not reason:
        return None
    m = _VERSION_MISMATCH_RE.match(reason.strip())
    if not m:
        return None
    return m.group(1).strip().casefold()


def select_version_mismatch_fallback(
    entries: List[Dict[str, Any]],
    expected_title: str,
    expected_artist: str,
    min_count: int,
) -> Optional[Dict[str, Any]]:
    """Pick the quarantine entry to accept as a last resort, or None.

    ``entries`` are dicts as produced by
    :func:`core.imports.quarantine.list_quarantine_entries` (needs ``id``,
    ``reason``, ``expected_track``, ``expected_artist``, ``has_full_context``).

    Returns the chosen entry (the first-tried = oldest = best, by ascending
    ``id`` whose timestamp prefix sorts chronologically) when, for this track,
    there are at least ``min_count`` version-mismatch entries that all share the
    same matched version and carry full context. Otherwise None.
    """
    title = _norm(expected_title)
    artist = _norm(expected_artist)

    candidates = []
    for e in entries:
        if not e.get("has_full_context"):
            continue
        if _norm(e.get("expected_track")) != title:
            continue
        if _norm(e.get("expected_artist")) != artist:
            continue
        version = matched_version(e.get("reason"))
        if version is None:
            continue
        candidates.append((version, e))

    if len(candidates) < max(1, int(min_count or 1)):
        return None

    versions = {v for v, _ in candidates}
    if len(versions) != 1:
        # Inconsistent wrong versions (e.g. instrumental + live) — ambiguous,
        # don't guess which the user wants.
        return None

    # First tried = oldest = highest-confidence (the retry walks candidates
    # best-first). The id is a "<date>_<time>_<name>" timestamp prefix, so the
    # lexicographically smallest id is the earliest attempt.
    return min((e for _, e in candidates), key=lambda e: e["id"])


def try_accept_version_mismatch_fallback(
    *,
    quarantine_dir: str,
    restore_dir: str,
    expected_title: str,
    expected_artist: str,
    task_id: str,
    batch_id: Optional[str],
    config_get: Callable[[str, Any], Any],
    list_entries: Callable[[str], List[Dict[str, Any]]],
    approve_entry: Callable[..., Optional[Any]],
    reprocess: Callable[..., None],
) -> bool:
    """Orchestrate the last-resort acceptance. Returns True if a candidate was
    accepted and re-dispatched (caller must then NOT mark the task failed).

    All I/O is injected so this is testable without a filesystem or the
    web_server pipeline:
      - ``config_get(key, default)`` — settings lookup.
      - ``list_entries(quarantine_dir)`` — quarantine.list_quarantine_entries.
      - ``approve_entry(quarantine_dir, entry_id, restore_dir)`` ->
        ``(restored_path, context, trigger)`` or None — quarantine.approve_quarantine_entry.
      - ``reprocess(restored_path, context, task_id, batch_id)`` — re-run the
        verification pipeline on the restored file.
    """
    if not config_get("post_processing.accept_version_mismatch_fallback", False):
        return False

    try:
        min_count = int(config_get("post_processing.version_mismatch_min_count", 2))
    except (TypeError, ValueError):
        min_count = 2
    if min_count < 1:
        min_count = 1

    try:
        entries = list_entries(quarantine_dir) or []
    except Exception as exc:  # never let the fallback break the failure path
        logger.debug("[Version-Mismatch Fallback] listing quarantine failed: %s", exc)
        return False

    chosen = select_version_mismatch_fallback(
        entries, expected_title, expected_artist, min_count
    )
    if not chosen:
        return False

    version = matched_version(chosen.get("reason")) or "?"
    try:
        result = approve_entry(quarantine_dir, chosen["id"], restore_dir)
    except Exception as exc:
        logger.error("[Version-Mismatch Fallback] approve failed for %s: %s", chosen["id"], exc)
        return False
    if not result:
        return False

    restored_path, context, _trigger = result
    if not isinstance(context, dict):
        return False
    # Bypass ONLY the AcoustID gate — integrity / duration / bit-depth still run,
    # so a truncated or genuinely wrong file is still caught.
    context["_skip_quarantine_check"] = "acoustid"
    context["_version_mismatch_fallback"] = version
    context["task_id"] = task_id
    if batch_id:
        context["batch_id"] = batch_id

    logger.warning(
        "[Version-Mismatch Fallback] retries exhausted for '%s - %s'; accepting "
        "best quarantined candidate (%s, entry %s) as last resort",
        expected_artist, expected_title, version, chosen["id"],
    )

    try:
        reprocess(restored_path, context, task_id, batch_id)
    except Exception as exc:
        logger.error("[Version-Mismatch Fallback] re-import dispatch failed: %s", exc)
        return False
    return True
