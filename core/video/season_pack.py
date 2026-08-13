"""Map the files inside a downloaded season pack onto the episodes they are.

This is the video twin of the music side's staging shortcut
(:mod:`core.downloads.staging` + ``album_bundle_dispatch``), and it is worth
being explicit about what is borrowed and what is deliberately different,
because copying the music design wholesale would be a bug.

**Borrowed — the shape.** A pack download does not import anything. It fills a
folder, and then the ORDINARY per-episode path claims files out of that folder
one at a time. The music album bundle works exactly this way: it stages the
album, marks the batch ``staged``, and then *deliberately does not early-return*
so the per-track flow runs and each track pulls its own file. That keeps one
import path — tagging, ffprobe verification, template rename, DB insert — rather
than growing a second one that drifts.

**Borrowed — the loud failure.** Music learned this the hard way (#706/#708): a
staged file that silently failed to match produced "download the album, stage the
files, never claim them, re-add to the wishlist", a loop that was impossible to
diagnose from logs. So every unclaimed file here carries a REASON, and the caller
is expected to log it.

**Deliberately different — no fuzzy matching.** Music has to score title/artist
similarity because audio filenames are unreliable. Episode files are not: they
declare ``S01E03``, or a date, or an absolute number, and
:func:`core.video.release_parse.parse_release` already reads all three. So this
matches STRUCTURALLY and exactly. Porting music's 0.75-similarity threshold would
risk filing episode 4 as episode 3 — a silent mis-shelving that a library never
forgives and that nobody notices until they watch it.

Pure: no filesystem, no DB, no network. The caller supplies the file list.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

# Containers worth importing. Anything else in a pack (nfo, jpg, txt, srt) is
# not an episode; subtitles ride along with their video via the existing
# sidecar handling, not through here.
VIDEO_EXTS = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv", ".mpg", ".mpeg"})

# Path fragments that mean "not the episode". 'sample' is the classic trap: a
# 30-second sample sits beside the real file, parses to the same SxxExx, and is
# smaller — so without this the pack would import the sample and call it done.
_JUNK_PARTS = ("sample", "extras", "featurette", "trailer", "behind the scenes",
               "deleted scenes", "bonus", "proof", "screens")

# Below this a "video file" is a sample or a stub, whatever it claims to be.
MIN_EPISODE_BYTES = 32 * 1024 * 1024      # 32 MB


def _is_junk(path: str) -> bool:
    low = str(path or "").replace("\\", "/").lower()
    return any(part in low for part in _JUNK_PARTS)


def _ext(path: str) -> str:
    return os.path.splitext(str(path or ""))[1].lower()


def classify_file(path: Any, size_bytes: Any = None) -> Optional[str]:
    """Why this file cannot be an episode, or None if it could be.

    Returned as a REASON rather than a bool so the caller can log what it
    skipped — a pack that imports nothing must be able to say why."""
    p = str(path or "")
    if not p:
        return "empty path"
    if _ext(p) not in VIDEO_EXTS:
        return "not a video file (%s)" % (_ext(p) or "no extension")
    if _is_junk(p):
        return "looks like a sample or extra"
    try:
        if size_bytes is not None and int(size_bytes) < MIN_EPISODE_BYTES:
            return "only %.1f MB — too small to be an episode" % (int(size_bytes) / 1048576.0)
    except (TypeError, ValueError):
        pass
    return None


def episode_keys_for(filename: Any, *, want_season: Any = None,
                     air_dates: Optional[Dict[str, tuple]] = None,
                     absolute_map: Optional[Dict[int, tuple]] = None) -> List[tuple]:
    """Every (season, episode) this filename claims — usually one, sometimes two.

    A multi-episode file (``S01E01E02``) genuinely IS both episodes, so it
    claims both; the existing parser already reports the span. Daily and anime
    naming are resolved through the caller's maps rather than guessed at here,
    which keeps this pure and keeps the numbering authority in one place."""
    from core.video.release_parse import has_absolute_episode, parse_release
    parsed = parse_release(os.path.basename(str(filename or "")))
    season, ep = parsed.get("season"), parsed.get("episode")

    if ep is not None and season is not None:
        end = parsed.get("episode_end") or ep
        if end < ep:
            end = ep
        return [(season, n) for n in range(ep, end + 1)]

    # Daily series: the air date IS the identity ('Show.2026.07.08...').
    if parsed.get("air_date") and air_dates:
        hit = air_dates.get(parsed["air_date"])
        if hit:
            return [hit]

    # Anime: scene numbering is absolute with no season.
    if absolute_map:
        for absolute, key in absolute_map.items():
            if has_absolute_episode(os.path.basename(str(filename or "")), absolute):
                return [key]

    # A bare 'S01' with no episode is the pack itself, not an episode.
    return []


def map_pack(files: Iterable[Any], *, want_season: Any = None,
             air_dates: Optional[Dict[str, tuple]] = None,
             absolute_map: Optional[Dict[int, tuple]] = None) -> Dict[str, Any]:
    """Assign a pack's files to episodes.

    ``files`` = [{"path", "size_bytes"}, ...]. Returns
    ``{"claimed": {(s, e): {...}}, "skipped": [{"path", "why"}]}``.

    Two rules earn their place:

    * **Largest wins a contested episode.** Packs routinely carry a sample or a
      duplicate that parses to the same numbers; the real episode is the big
      one. Music's staging picks by similarity score, which has no equivalent
      here — size is the honest signal.
    * **A wrong-season file is skipped, not re-homed.** A pack of season 2 that
      contains a stray season 1 file must not quietly file it, because the
      caller asked for one season and the wishlist rows it will satisfy are
      that season's."""
    claimed: Dict[tuple, Dict[str, Any]] = {}
    skipped: List[Dict[str, str]] = []

    for entry in (files or []):
        if isinstance(entry, dict):
            path, size = entry.get("path"), entry.get("size_bytes")
        else:
            path, size = entry, None
        why = classify_file(path, size)
        if why:
            skipped.append({"path": str(path or ""), "why": why})
            continue

        keys = episode_keys_for(path, want_season=want_season,
                                air_dates=air_dates, absolute_map=absolute_map)
        if not keys:
            skipped.append({"path": str(path), "why": "no episode number in the filename"})
            continue

        if want_season is not None:
            try:
                wanted = int(want_season)
            except (TypeError, ValueError):
                wanted = None
            if wanted is not None:
                off = [k for k in keys if k[0] != wanted]
                if off and len(off) == len(keys):
                    skipped.append({"path": str(path),
                                    "why": "season %s, but this pack is season %s" % (off[0][0], wanted)})
                    continue
                keys = [k for k in keys if k[0] == wanted]

        for key in keys:
            prev = claimed.get(key)
            if prev is None:
                claimed[key] = {"path": str(path), "size_bytes": size}
                continue
            # Contested: keep the bigger file, and say what lost.
            try:
                bigger = int(size or 0) > int(prev.get("size_bytes") or 0)
            except (TypeError, ValueError):
                bigger = False
            loser = str(path) if not bigger else str(prev["path"])
            if bigger:
                claimed[key] = {"path": str(path), "size_bytes": size}
            skipped.append({"path": loser,
                            "why": "another file for S%02dE%02d was larger" % key})

    return {"claimed": claimed, "skipped": skipped}


def unclaimed_episodes(claimed: Dict[tuple, Any], wanted: Iterable[tuple]) -> List[tuple]:
    """Wanted episodes the pack did NOT supply — these fall through to an
    ordinary per-episode search, exactly as an unmatched track does on the
    music side. Returning them (rather than assuming a pack is complete) is
    what keeps a partial pack from silently leaving gaps."""
    have = set(claimed or {})
    return [k for k in (wanted or []) if k not in have]


__all__ = ["map_pack", "episode_keys_for", "classify_file", "unclaimed_episodes",
           "VIDEO_EXTS", "MIN_EPISODE_BYTES"]
