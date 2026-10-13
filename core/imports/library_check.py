"""Existing-library pre-check for manual imports (SeadogsBooty's request).

When artists/releases are added to the Import page queue, SoulSync used to
run the full pipeline on everything — copying, retagging, renaming — even
for tracks already sitting in the managed library. This module answers one
question per file-to-track pairing, asked at the MATCHING stage (after the
album is identified against the metadata source, before any transcoding,
tagging or file moves):

    is this exact track already in the library, and is the copy there fine?

The identity comes from the metadata source's tracklist (stable external
IDs). The file->track LINK itself comes from the file's own tags (an ISRC or
MBID tag paired by ``find_exact_id_matches``) — which is why the safety rule
is absolute: skip ONLY on a confident exact-ID pairing (``match_type`` in
``('mbid', 'isrc')``). A fuzzy pairing NEVER skips — it fails open and
proceeds, because a false "already owned" is silent data loss (the
Ktzenjammer lesson), while an unnecessary import is just wasted work.

Two more guards keep the skip honest:

* **Release scoping.** External IDs (ISRC/MBID) identify the RECORDING,
  which legitimately repeats across single/album/compilation/remaster.
  A DB hit is only skippable when the hit's album is the SAME release
  being imported (strict album-title match) — otherwise the album would
  land incomplete.
* **The pipeline's own arbiter.** "Satisfactory" is decided exactly the way
  the import pipeline decides replacement (``pipeline.py`` protection
  block): ``replace_lower`` + the measured ``is_profile_upgrade`` probe,
  or the metadata check for files the pipeline would refresh. The gate
  never invents its own quality heuristic.

Decisions:
    "import"      — not in the library (or the library row is stale, or the
                    hit is a different release), process normally
    "skip_owned"  — confidently the same track of the same release, library
                    copy is satisfactory, skip it
    "upgrade"     — confidently the same track, the pipeline's arbiter says
                    the incoming file would replace the library copy:
                    proceed so the existing upgrade logic handles it
    "uncertain"   — cannot prove identity or the arbiter errored: fail open,
                    proceed (never skip)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("imports.library_check")

# Pairings that prove identity. Anything else (fuzzy scoring, position-only)
# must fail open — never skip.
EXACT_MATCH_TYPES = ("mbid", "isrc")

DECISION_IMPORT = "import"
DECISION_SKIP_OWNED = "skip_owned"
DECISION_UPGRADE = "upgrade"
DECISION_UNCERTAIN = "uncertain"


def _album_title_of_track(db: Any, track_row: Dict[str, Any]) -> str:
    """The library album title for a track row, "" when unresolvable.

    Used for release scoping: a recording-level DB hit (ISRC/MBID) is only
    skippable when it belongs to the SAME release being imported. Never
    raises — unresolvable means "unknown", and unknown fails open.
    """
    try:
        album_id = track_row.get("album_id")
        if album_id is None:
            return ""
        conn = db._get_connection()
        try:
            row = conn.execute(
                "SELECT title FROM albums WHERE id = ?", (album_id,)
            ).fetchone()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: S110
                pass
        if row is None:
            return ""
        try:
            title = row["title"]
        except (TypeError, KeyError, IndexError):
            title = row[0] if row else ""
        return str(title or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _same_release(incoming_album_name: str, library_album_title: str) -> bool:
    """Strict album-title match: the DB hit is the same release being imported.

    Deliberately strict (case-insensitive, whitespace-normalized). A
    mismatch fails open — the album gets its own copy, which is how
    SoulSync files per-release libraries. Edition variants ("Album" vs
    "Album (Deluxe)") correctly do NOT match.
    """
    if not incoming_album_name or not library_album_title:
        return False
    normalize = lambda s: " ".join(str(s).strip().casefold().split())  # noqa: E731
    return normalize(incoming_album_name) == normalize(library_album_title)


def _library_file_has_metadata(library_path: str) -> Optional[bool]:
    """Mirror of the pipeline's metadata check (``has_metadata``).

    Returns True/False, or None when unreadable (fail open -> proceed).
    """
    try:
        from mutagen import File as MutagenFile
        existing_file = MutagenFile(library_path)
        tags = existing_file.tags if existing_file is not None else None
        return existing_file is not None and len(tags or {}) > 2
    except Exception:  # noqa: BLE001
        return None


def _resolve_replace_lower(quality_profile: Optional[Dict[str, Any]]) -> bool:
    """The pipeline's ``replace_lower`` flag, resolved the same way.

    ``pipeline.py``: ``quality_profile.get('replace_lower_quality',
    config_manager.get('import.replace_lower_quality', False))``.
    """
    try:
        from core.settings import config_manager
        fallback = bool(config_manager.get("import.replace_lower_quality", False))
    except Exception:  # noqa: BLE001
        fallback = False
    return bool((quality_profile or {}).get("replace_lower_quality", fallback))


def check_track_library_status(
    db: Any,
    *,
    track: Dict[str, Any],
    staging_file_path: str,
    match_type: Optional[str],
    confidence: float = 0.0,
    source_hint: str = "",
    album_name: str = "",
    quality_profile: Optional[Dict[str, Any]] = None,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Decide whether an incoming file's paired track needs importing.

    ``track`` is the canonical metadata-source track (with stable IDs),
    ``staging_file_path`` the incoming file, ``match_type``/``confidence``
    describe how the file was paired to the track, ``album_name`` the
    incoming release title (for release scoping), ``quality_profile`` the
    resolved quality profile (for the replacement arbiter), ``quiet``
    drops per-track logging to debug (annotation pass).

    Returns ``{"decision", "reason", "match_type", "confidence",
    "library_path"}``. ``library_path`` is the existing file when one was
    found. Every decision is logged — the per-track audit trail.
    """
    file_name = os.path.basename(staging_file_path or "?")
    track_name = str(track.get("name") or "Unknown Track")

    def _decide(decision: str, reason: str,
                library_path: Optional[str] = None) -> Dict[str, Any]:
        result = {
            "decision": decision,
            "reason": reason,
            "match_type": match_type,
            "confidence": confidence,
            "library_path": library_path,
        }
        log = logger.debug if quiet else logger.info
        log(
            "[Library Check] %s -> %s | pair=%s (confidence %.2f) | %s%s",
            file_name,
            decision.upper(),
            match_type or "fuzzy",
            confidence,
            reason,
            f" | library: {library_path}" if library_path else "",
        )
        return result

    # Hard safety rule: only exact-ID pairings may skip. Fuzzy pairings fail
    # open — a wrong "already owned" silently loses music.
    if (match_type or "").lower() not in EXACT_MATCH_TYPES:
        return _decide(
            DECISION_UNCERTAIN,
            f"paired by fuzzy scoring ({match_type or 'no match type'}) — "
            "cannot prove identity, proceeding",
        )

    if db is None:
        return _decide(
            DECISION_UNCERTAIN,
            "library database unavailable — proceeding",
        )

    try:
        from core.library.track_identity import (
            extract_external_ids,
            find_library_track_by_external_id,
        )
    except Exception as exc:                                # noqa: BLE001
        logger.debug("[Library Check] identity module unavailable: %s", exc)
        return _decide(DECISION_UNCERTAIN, "identity lookup unavailable — proceeding")

    ids = extract_external_ids(track, source_hint or None)
    if not ids:
        return _decide(
            DECISION_UNCERTAIN,
            "paired by exact ID but the track carries no stable external IDs — proceeding",
        )

    try:
        row = find_library_track_by_external_id(db, external_ids=ids)
    except Exception as exc:                                # noqa: BLE001
        logger.debug("[Library Check] DB lookup failed: %s", exc)
        return _decide(DECISION_UNCERTAIN, "library lookup failed — proceeding")

    if row is None:
        return _decide(DECISION_IMPORT, "not in the library")

    library_path = str(row.get("file_path") or "")
    matched_id = next(
        (f"{name}={value}" for name, value in ids.items() if value),
        "external id",
    )
    # A deleted file must never block its own re-import (the provenance
    # docstring's warning): the row is stale, the track is missing.
    if not library_path or not os.path.exists(library_path):
        return _decide(
            DECISION_IMPORT,
            f"library has it ({matched_id}) but the file is gone — re-importing",
            library_path or None,
        )

    # Release scoping: the IDs above identify the RECORDING, which repeats
    # across single/album/compilation/remaster. Skipping on a different
    # release's row would land this album incomplete — so the hit only
    # counts when it's the same release being imported. Anything else
    # fails open: the album gets its own copy.
    if not _same_release(album_name, _album_title_of_track(db, row)):
        return _decide(
            DECISION_IMPORT,
            f"recording is in the library ({matched_id}) but under a different "
            f"release — importing for this album",
            library_path,
        )

    # "Satisfactory" mirrors the pipeline's own replacement arbiter
    # (pipeline.py protection block), so the gate never contradicts it:
    #
    # * replace_lower on + measured is_profile_upgrade -> the pipeline
    #   WOULD replace: route to the existing upgrade logic.
    # * replace_lower on + measured decline -> the pipeline's protection
    #   would decline: skip the wasted work.
    # * replace_lower off -> the pipeline only ever overwrites a
    #   metadata-less file ("safe to overwrite"): skip when the library
    #   copy has metadata, otherwise let the pipeline refresh it.
    # * any arbiter error -> fail open, proceed (never skip on uncertainty).
    replace_lower = _resolve_replace_lower(quality_profile)
    if replace_lower:
        try:
            from core.imports.quality_replace import is_profile_upgrade
            if is_profile_upgrade(
                library_path, staging_file_path, quality_profile or {}
            ):
                return _decide(
                    DECISION_UPGRADE,
                    f"library has it ({matched_id}) and the measured quality "
                    f"check says the incoming file is an upgrade — routing to "
                    f"the existing upgrade logic",
                    library_path,
                )
            return _decide(
                DECISION_SKIP_OWNED,
                f"already in the library ({matched_id}) — the measured quality "
                f"check says the incoming file is not an upgrade, leaving it "
                f"untouched",
                library_path,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Library Check] arbiter error, failing open: %s", exc)
            return _decide(
                DECISION_UNCERTAIN,
                "quality comparison unavailable — proceeding",
                library_path,
            )

    has_metadata = _library_file_has_metadata(library_path)
    if has_metadata is False:
        return _decide(
            DECISION_IMPORT,
            f"library has it ({matched_id}) but the copy lacks metadata — "
            f"letting the pipeline refresh it",
            library_path,
        )
    if has_metadata is None:
        return _decide(
            DECISION_UNCERTAIN,
            "could not read the library copy — proceeding",
            library_path,
        )
    return _decide(
        DECISION_SKIP_OWNED,
        f"already in the library ({matched_id}) — leaving it untouched",
        library_path,
    )
