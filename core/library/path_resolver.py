"""Resolve database-stored file paths to actual files on disk.

Database track rows store file paths as the media server reported them
(`/music/Artist/Album/track.flac`, `H:\\Music\\Artist\\...`, etc). When
SoulSync runs in Docker, those paths don't exist as-is inside the
container — the user's library is bind-mounted at a container path
(commonly `/music`) that has nothing to do with what Plex/Jellyfin
recorded. Same problem for native installs that point at a NAS via SMB:
the path the media server scanned isn't the path SoulSync reads.

The resolver tries the raw path first (cheap happy-path), then walks
progressively shorter suffixes against every configured base directory:
the transfer folder, the slskd download folder, every configured Plex
library location, and every entry in the user's `library.music_paths`
config. The first existing match wins.

This module replaces four duplicated copies of the same function (each
with the same incomplete logic) that lived in
`core/repair_worker.py` and three modules under `core/repair_jobs/`.
The duplicates only checked the transfer + download folders and
silently returned None for files in the actual media-server library —
which is why, for example, the Album Completeness "Auto-Fill" button
returned ``Could not determine album folder from existing tracks`` for
every Docker user (issue #476).

The web server has its own near-duplicate at
``web_server.py:_resolve_library_file_path`` which already covers the
full search space; this module is the lifted, shared version usable
from any background worker.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger


logger = get_logger("library.path_resolver")


@dataclass
class ResolveAttempt:
    """Diagnostic record for a single `resolve_library_file_path` call.

    Returned by `resolve_library_file_path_with_diagnostic` so callers
    that need to surface a useful error message (instead of just a
    silent None) can describe what was tried. Pure data — no side
    effects, no rendering opinions.

    Fields:
        raw_path_existed: True if `os.path.exists(file_path)` returned
            True at the start of the resolver. When this is True the
            resolver short-circuits and `base_dirs_tried` will be empty.
        base_dirs_tried: The ordered list of base directories the
            resolver suffix-walked against (already filtered by
            `os.path.isdir`).
        had_config_manager: Whether a config_manager was supplied. Useful
            for distinguishing "no candidates discovered" from "couldn't
            even read config to discover".
        had_plex_client: Same, for the Plex API probe.
    """
    raw_path_existed: bool = False
    base_dirs_tried: List[str] = field(default_factory=list)
    had_config_manager: bool = False
    had_plex_client: bool = False


def _docker_resolve_path(path_str: Any) -> Optional[str]:
    """Translate Windows-style paths to the Docker container layout.

    Mirrors ``core/imports/paths.docker_resolve_path`` but kept local to
    avoid a cross-package import in case this module is consumed early
    in a job startup. Returns the input unchanged outside Docker.
    """
    if not isinstance(path_str, str):
        return None
    if (
        os.path.exists("/.dockerenv")
        and len(path_str) >= 3
        and path_str[1] == ":"
        and path_str[0].isalpha()
    ):
        drive_letter = path_str[0].lower()
        rest = path_str[2:].replace("\\", "/")
        return f"/host/mnt/{drive_letter}{rest}"
    return path_str


def _collect_base_dirs(
    transfer_folder: Optional[str],
    download_folder: Optional[str],
    config_manager: Any,
    plex_client: Any,
) -> list[str]:
    """Build the ordered list of base directories to probe."""
    candidates: list[Optional[str]] = []

    if transfer_folder:
        candidates.append(_docker_resolve_path(transfer_folder))
    if download_folder:
        candidates.append(_docker_resolve_path(download_folder))

    if config_manager is not None:
        try:
            transfer_cfg = config_manager.get("soulseek.transfer_path", "") or ""
            download_cfg = config_manager.get("soulseek.download_path", "") or ""
            if transfer_cfg:
                candidates.append(_docker_resolve_path(transfer_cfg))
            if download_cfg:
                candidates.append(_docker_resolve_path(download_cfg))
        except Exception as e:
            logger.debug("soulseek paths read failed: %s", e)

    # Plex-reported library locations (handles "Plex scanned at /music but
    # SoulSync mounts at /library" cases).
    if plex_client is not None:
        try:
            server = getattr(plex_client, "server", None)
            music_library = getattr(plex_client, "music_library", None)
            if server is not None and music_library is not None:
                for loc in getattr(music_library, "locations", []) or []:
                    if loc:
                        candidates.append(loc)
        except Exception as e:
            logger.debug("plex locations read failed: %s", e)

    # User-configured library music paths (Settings → Library → Music Paths).
    if config_manager is not None:
        try:
            music_paths = config_manager.get("library.music_paths", []) or []
            if isinstance(music_paths, Iterable):
                for p in music_paths:
                    if isinstance(p, str) and p.strip():
                        candidates.append(_docker_resolve_path(p.strip()))
        except Exception as e:
            logger.debug("music paths read failed: %s", e)

    # Normalize to absolute forms so resolution does NOT depend on the calling
    # thread's CWD. A relative config like "./Transfer" otherwise only resolves
    # when os.path.isdir("./Transfer") happens to be true from the current CWD —
    # which fails in background workers whose CWD isn't the app root, leaving
    # base_dirs empty and every track "unresolved". For each candidate we try
    # the raw form first (cheap, preserves an already-absolute path), then its
    # os.path.abspath() form so "./Transfer" → "/app/Transfer".
    expanded: list[str] = []
    for c in candidates:
        if not c:
            continue
        expanded.append(c)
        if not os.path.isabs(c):
            expanded.append(os.path.abspath(c))

    # De-duplicate while preserving order, drop empties / non-existent dirs.
    seen: set[str] = set()
    out: list[str] = []
    for c in expanded:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isdir(c):
            out.append(c)
    return out


def library_base_dirs(
    config_manager: Any = None,
    *,
    transfer_folder: Optional[str] = None,
    download_folder: Optional[str] = None,
    plex_client: Any = None,
) -> List[str]:
    """The existing directories a stored library path can be resolved against.

    The public form of the search roots the suffix walk uses: transfer and
    download folders, Plex-reported library locations, and
    ``library.music_paths``. Callers that need to judge whether some path
    belongs to the library at all — rather than resolve one file — ask here,
    instead of reaching for the private collector.
    """
    return _collect_base_dirs(transfer_folder, download_folder, config_manager, plex_client)


def resolve_library_file_path(
    file_path: Any,
    *,
    transfer_folder: Optional[str] = None,
    download_folder: Optional[str] = None,
    config_manager: Any = None,
    plex_client: Any = None,
) -> Optional[str]:
    """Resolve a stored DB path to an actual file on disk.

    Args:
        file_path: The path as recorded in the database (may not exist
            as-is in the current process's filesystem view).
        transfer_folder: Optional explicit transfer-folder override
            (bypasses the config_manager lookup). Useful when the caller
            already cached one.
        download_folder: Optional explicit download-folder override.
        config_manager: When provided, the resolver also pulls
            ``soulseek.transfer_path``, ``soulseek.download_path``, and
            ``library.music_paths`` from config to expand the search.
        plex_client: When provided, every Plex-reported music-library
            location is added to the search.

    Returns:
        The first existing path on disk, or None when no match is found.
        Never raises — failure is the None return.
    """
    resolved, _ = resolve_library_file_path_with_diagnostic(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
        plex_client=plex_client,
    )
    return resolved


def resolve_library_file_path_with_diagnostic(
    file_path: Any,
    *,
    transfer_folder: Optional[str] = None,
    download_folder: Optional[str] = None,
    config_manager: Any = None,
    plex_client: Any = None,
) -> Tuple[Optional[str], ResolveAttempt]:
    """Same as ``resolve_library_file_path`` but also returns a
    ``ResolveAttempt`` describing what the resolver tried.

    Use this when you need to surface a useful "we tried X, Y, Z" error
    to the user instead of a silent None. Issue #558 (gabistek, Navidrome
    on Docker): the resolver was returning None because Navidrome doesn't
    expose library filesystem paths via API (unlike Plex), and the user
    hadn't configured ``library.music_paths``. The Album Completeness
    fix endpoint surfaced a generic "Could not determine album folder"
    error with no diagnostic — user had no way to know what to configure.
    """
    attempt = ResolveAttempt(
        had_config_manager=config_manager is not None,
        had_plex_client=plex_client is not None,
    )

    if not isinstance(file_path, str) or not file_path:
        return None, attempt

    if os.path.exists(file_path):
        attempt.raw_path_existed = True
        return file_path, attempt

    path_parts = file_path.replace("\\", "/").split("/")
    base_dirs = _collect_base_dirs(transfer_folder, download_folder, config_manager, plex_client)
    attempt.base_dirs_tried = list(base_dirs)
    if not base_dirs:
        return None, attempt

    # Try progressively shorter path suffixes against each base dir.
    #
    # Start at index 0 so a clean RELATIVE library path is tried in FULL first.
    # SoulSync's own library scanner stores paths like
    # "Asketa/Another Side/01 - Track.flac" (no leading slash) — index 0 is the
    # artist folder and dropping it (the old range(1, ...)) meant the artist
    # segment was never joined, so nothing under transfer/ ever resolved and
    # every track looked unreadable to the quality scanner.
    #
    # For ABSOLUTE media-server paths ("/music/Artist/Album/track.flac") index 0
    # is the empty leading segment and i=0 yields os.path.join(base, "", ...) ==
    # base/Artist/... which simply won't exist and harmlessly falls through to
    # i=1 ("music/...") etc. A Windows drive part ("E:") at i=0 likewise just
    # fails on POSIX and falls through. So starting at 0 is safe for every form
    # and only ADDS the relative-full-path match that was missing.
    for base in base_dirs:
        for i in range(0, len(path_parts)):
            candidate = os.path.join(base, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate, attempt

    sibling = _resolve_via_sibling_album_folder(path_parts, base_dirs)
    if sibling:
        return sibling, attempt
    # Filename wrong as well as the album folder — Navidrome synthesizes the
    # whole path from tags, so no exact segment is left to match on (#1127).
    return _resolve_via_synthesized_filename(path_parts, base_dirs), attempt


def _resolve_via_sibling_album_folder(
    path_parts: List[str], base_dirs: List[str]
) -> Optional[str]:
    """Last resort for a reported path whose ALBUM segment is wrong (#1127).

    Suffix-walking can only drop leading segments, so it repairs a wrong
    *prefix* but never a wrong *interior* segment. Navidrome's Subsonic API
    reports a song's ``path`` synthesized as ``<AlbumArtist>/<Album>/<file>``
    rather than the true relative path, so anyone whose folders aren't named
    exactly ``<Album>`` gets a path that can never resolve. With the template
    ``$albumartist/$albumartist - $album/...`` the DB holds
    ``Beck/Guero/01-01 - E-Pro.flac`` while disk holds
    ``Beck/Beck - Guero/01-01 - E-Pro.flac`` — same artist folder, same
    filename, different album folder. That made Dead File Cleaner call every
    file unreachable and Album Completeness refuse to fix anything.

    So: keep the artist folder and the filename, and look one level down for
    the file under a DIFFERENTLY-NAMED album folder.

    Deliberately conservative — it returns a match only when exactly ONE album
    folder under that artist holds a file with this basename. Callers include
    Dead File Cleaner, which DELETES what it resolves; guessing between two
    albums that both contain "01 - Intro.flac" would be worse than failing.
    Cost is one ``scandir`` per (base dir, artist folder).
    """
    if len(path_parts) < 3:
        return None
    basename = path_parts[-1]
    artist_segment = path_parts[-3]
    if not basename or not artist_segment or artist_segment in (".", ".."):
        return None

    matches: List[str] = []
    for base in base_dirs:
        artist_dir = os.path.join(base, artist_segment)
        if not os.path.isdir(artist_dir):
            continue
        try:
            entries = list(os.scandir(artist_dir))
        except OSError as e:
            logger.debug("sibling-album scan failed for %s: %s", artist_dir, e)
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            candidate = os.path.join(entry.path, basename)
            if os.path.exists(candidate) and candidate not in matches:
                matches.append(candidate)

    if not matches:
        return None

    # Ambiguity is about hitting two different ALBUMS, not two paths. The same
    # library is often reachable through several configured base dirs (a
    # transfer path plus a music path, duplicate mounts, a symlink), and every
    # one of those yields the same album folder NAME — that is one album seen
    # repeatedly, not a genuine conflict. Comparing full paths there refused
    # perfectly resolvable files.
    album_names = {os.path.basename(os.path.dirname(m)) for m in matches}
    if len(album_names) == 1:
        logger.debug("resolved %r via sibling album folder: %s", basename, matches[0])
        return matches[0]
    logger.debug(
        "sibling-album fallback found %d different album folders for %r under %r "
        "(%s) — ambiguous, refusing to guess",
        len(album_names), basename, artist_segment, sorted(album_names))
    return None


# A leading track / disc-track number on a filename: "01 - ", "01-01 - ",
# "1-01 ", "01. ", "01_". Anchored at the start and followed by a separator, so
# a title that simply BEGINS with digits ("1979.flac", "99 Luftballons.flac")
# keeps its name — those have no separator run after the number.
_LEADING_TRACK_NUM = re.compile(
    r"^\d{1,3}[-_.]\d{1,3}[\s._-]+"   # disc-track: "01-01 - ", "1-01 ", "01-01. "
    r"|^\d{1,3}\s*[-_.]\s*"           # track + a real separator: "01 - ", "01.", "01_"
)


def _strip_track_number(basename: str) -> str:
    """Name with any leading track / disc-track numbering removed, lowercased.

    ``"01-01 - E-Pro"``, ``"01 - E-Pro"`` and ``"E-Pro"`` all reduce to
    ``"e-pro"``. Works on a full filename too, but the resolver hands it the
    extension-less stem so the extension stays a separate, explicit rule.

    Only the ANCHORED leading run goes — the title itself may contain dashes
    ("E-Pro") or start with digits ("1979", "99 Luftballons"), and collapsing
    those onto a shared identity is how the wrong file gets deleted.
    """
    stripped = _LEADING_TRACK_NUM.sub("", basename or "", count=1).strip()
    return (stripped or (basename or "")).lower()


def _resolve_via_synthesized_filename(
    path_parts: List[str], base_dirs: List[str]
) -> Optional[str]:
    """Last resort for a reported path whose FILENAME is wrong too (#1127).

    ``_resolve_via_sibling_album_folder`` assumes the basename is real and only
    the album folder is wrong. For Navidrome that assumption doesn't hold: the
    Subsonic ``path`` is synthesized from tags end to end, filename included, so
    the DB holds ``Beck/Guero/01-01 - E-Pro.flac`` while a single-disc album
    organised by SoulSync's own template is on disk as
    ``Beck/Beck - Guero/01 - E-Pro.flac``. SoulSync never writes a ``01-``
    disc prefix on a single-disc album (#981), so that leading ``01-`` can only
    have come from the tag — which is exactly what the second reporter noticed.

    With both the album folder AND the filename wrong there is nothing left to
    match on exactly, so this compares the filename with its leading track /
    disc-track numbering removed, plus the extension, across every album folder
    under the artist.

    Runs ONLY after every exact strategy has already failed, so it can never
    change a path that resolves today — it can only turn a None into a hit.

    Conservative by design: exactly ONE file across all album folders may match,
    and the extension must be identical. Dead File Cleaner DELETES what this
    resolves, so an ambiguous guess is far worse than failing.
    """
    if len(path_parts) < 3:
        return None
    basename = path_parts[-1]
    artist_segment = path_parts[-3]
    if not basename or not artist_segment or artist_segment in (".", ".."):
        return None

    # Stem and extension compared separately. Folding the extension into the
    # stem also works, but then the extension rule is implicit and a later
    # change to the stripping could drop it without anything noticing.
    wanted_base, wanted_ext = os.path.splitext(basename)
    wanted_stem = _strip_track_number(wanted_base)
    if not wanted_stem or not wanted_ext:
        return None

    matches: List[str] = []
    for base in base_dirs:
        artist_dir = os.path.join(base, artist_segment)
        if not os.path.isdir(artist_dir):
            continue
        try:
            artist_entries = list(os.scandir(artist_dir))
        except OSError as e:
            logger.debug("synthesized-name scan failed for %s: %s", artist_dir, e)
            continue
        # is_dir() stats, and a broken symlink raises. Skip that one entry
        # rather than abandoning the whole artist — same as the sibling step.
        album_dirs = []
        for e in artist_entries:
            try:
                if e.is_dir():
                    album_dirs.append(e)
            except OSError:
                continue
        for album_dir in album_dirs:
            try:
                entries = list(os.scandir(album_dir.path))
            except OSError:
                continue
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                entry_base, entry_ext = os.path.splitext(entry.name)
                if entry_ext.lower() != wanted_ext.lower():
                    continue
                if _strip_track_number(entry_base) != wanted_stem:
                    continue
                if entry.path not in matches:
                    matches.append(entry.path)

    if not matches:
        return None
    # Same reasoning as the sibling-album step: one library reachable through
    # several base dirs yields the same file repeatedly, which is not a
    # conflict. Compare (album folder, filename) so a genuine second copy under
    # a different album still refuses.
    identities = {
        (os.path.basename(os.path.dirname(m)), os.path.basename(m)) for m in matches
    }
    if len(identities) == 1:
        logger.debug(
            "resolved %r via synthesized-filename fallback: %s", basename, matches[0])
        return matches[0]
    logger.debug(
        "synthesized-filename fallback found %d distinct files for %r under %r "
        "(%s) — ambiguous, refusing to guess",
        len(identities), basename, artist_segment, sorted(identities))
    return None


__all__ = [
    "ResolveAttempt",
    "resolve_library_file_path",
    "resolve_library_file_path_with_diagnostic",
    "resolve_via_last_resort_fallbacks",
]


def resolve_via_last_resort_fallbacks(
    file_path: Any, base_dirs: List[str]
) -> Optional[str]:
    """Both interior-segment fallbacks, for callers with their own base-dir logic.

    ``web_server.py::_resolve_library_file_path`` is a near-duplicate resolver
    with its own confusable-tolerant scan, and it never had the #1127 sibling
    step at all — so the same library resolved from a repair job and failed from
    the web server. One entry point, both callers, no drift.

    Expects every exact strategy to have failed already.
    """
    if not isinstance(file_path, str) or not file_path or not base_dirs:
        return None
    path_parts = file_path.replace("\\", "/").split("/")
    return (_resolve_via_sibling_album_folder(path_parts, base_dirs)
            or _resolve_via_synthesized_filename(path_parts, base_dirs))
