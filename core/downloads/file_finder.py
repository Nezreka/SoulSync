"""Robust completed-download file finder.

Walks a download directory (and optional transfer directory) to find
the local file matching an API-reported remote filename. Handles:

- Arbitrary subdirectory layouts (slskd flat / username-prefixed /
  remote-tree-preserved). Pre-extract callers in the Soulseek
  album-bundle path tried three hard-coded candidate paths and
  silently failed on layouts that didn't match — see issue #715
  (Billy Ocean album task fails after slskd finishes downloading
  release). The per-track flow already used a recursive walk and
  worked; the bundle path didn't, so users on common slskd configs
  with username-prefixed downloads saw bundles time out 22 minutes
  after slskd reported every transfer Completed.
- slskd dedup suffix ``_<10-or-more-digit-timestamp>`` appended when
  a file with the same basename already exists.
- YouTube / Tidal encoded filename format ``id||title`` — the
  ``||`` half is the human title and used for matching.
- Multiple files sharing a basename — disambiguates by counting
  how many remote-path directory components appear in the local
  path.

This was lifted verbatim from ``web_server._find_completed_file_robust``
so both the per-track download poll AND the Soulseek album-bundle
poll go through one finder. Pre-extract the bundle path probed
three hard-coded candidates only, which is why bundle downloads
on slskd setups with username-prefixed download dirs silently
timed out (#715).
"""

from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher
from typing import Optional, Tuple

from unidecode import unidecode

logger = logging.getLogger(__name__)


AUDIO_EXTENSIONS = frozenset({
    '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.wma',
    '.alac', '.aiff', '.aif', '.dsf', '.dff', '.ape',
})

# slskd appends a 10+ digit timestamp suffix when a file with the
# same basename already exists. Match-strip those so we still
# resolve the transfer.
_SLSKD_DEDUP_SUFFIX = re.compile(r'_\d{10,}$')

# AcoustID-quarantined files live under this dirname and must be
# skipped — they are known-wrong matches the verifier rejected.
_QUARANTINE_DIRNAME = 'ss_quarantine'

# Confidence floor for accepting a fuzzy basename match. Anything
# below this is treated as "no match" so we don't drag in unrelated
# files.
_FUZZY_THRESHOLD = 0.85


def _is_audio_candidate(path: str) -> bool:
    return os.path.splitext(str(path or ''))[1].lower() in AUDIO_EXTENSIONS


def _normalize_for_finding(text: str) -> str:
    """Match-engine-style normalisation for fuzzy filename comparison.

    Lowercases, transliterates unicode, drops bracketed content
    ("(Remastered 2016)", "[FLAC]"), strips punctuation, collapses
    whitespace. Mirrors ``matching_engine.py``'s text normaliser so
    finder + matcher agree on equivalence.
    """
    if not text:
        return ""
    text = unidecode(text).lower()
    text = re.sub(r'[._/]', ' ', text)
    text = re.sub(r'[\[\(].*?[\]\)]', '', text)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    return ' '.join(text.split()).strip()


def _extract_basename(api_filename: str) -> str:
    """Cross-platform rightmost-separator split, with YouTube /
    Tidal ``id||title`` encoded filenames pre-normalised — the id
    half is stripped so the title becomes the basename. Mirrors
    the strip-then-split order ``web_server`` used."""
    if not api_filename:
        return ""
    if '||' in api_filename:
        _id, title = api_filename.split('||', 1)
        api_filename = title
    last_slash = max(api_filename.rfind('/'), api_filename.rfind('\\'))
    return api_filename[last_slash + 1:] if last_slash != -1 else api_filename


def _api_dir_parts(api_filename: str) -> list[str]:
    """Lowercased remote-path directory components, sans the
    filename itself. Used to disambiguate when several local files
    share the same basename — the one whose path mirrors the
    remote folder structure wins."""
    if not api_filename:
        return []
    normalized = api_filename.replace('\\', '/')
    return [p.lower() for p in normalized.split('/')[:-1] if p]


def _path_matches_api_dirs(file_path: str, api_dirs: list[str]) -> bool:
    """``True`` iff every remote directory component appears as a
    path part of the local file. Cheap "is this file on a sibling
    tree" check."""
    if not api_dirs:
        return False
    path_parts = set(p.lower() for p in file_path.replace('\\', '/').split('/'))
    return all(d in path_parts for d in api_dirs)


def _search_in_directory(
    search_dir: str,
    location_name: str,
    target_basename: str,
    normalized_target: str,
    api_dirs: list[str],
) -> Tuple[Optional[str], float]:
    """Walk ``search_dir`` once, return (best_match, similarity).

    Priority order, highest first:
      1. Exact basename match with directory-structure confirmation
      2. Exact basename match without disambiguation (when no api_dirs)
      3. slskd-dedup-suffix basename match (same two tiers as exact)
      4. Best fuzzy basename match above ``_FUZZY_THRESHOLD``
    """
    best_fuzzy_path: Optional[str] = None
    highest_fuzzy_similarity = 0.0
    exact_matches: list[str] = []

    for root, dirs, files in os.walk(search_dir):
        # Strip quarantine subdir from the walk in place — these
        # files are known-bad and matching them would re-poison the
        # post-process pipeline.
        dirs[:] = [d for d in dirs if d != _QUARANTINE_DIRNAME]

        for filename in files:
            file_path = os.path.join(root, filename)
            if not _is_audio_candidate(file_path):
                continue

            # Tier 1 + 2: exact basename match.
            if filename == target_basename:
                if api_dirs and _path_matches_api_dirs(file_path, api_dirs):
                    logger.info(
                        "Found path-confirmed match in %s: %s",
                        location_name, file_path,
                    )
                    return file_path, 1.0
                if not api_dirs:
                    logger.info(
                        "Found exact match in %s: %s",
                        location_name, file_path,
                    )
                    return file_path, 1.0
                exact_matches.append(file_path)
                continue

            # Tier 3: slskd dedup suffix.
            stem, ext = os.path.splitext(filename)
            stripped_stem = _SLSKD_DEDUP_SUFFIX.sub('', stem)
            if stripped_stem != stem and stripped_stem + ext == target_basename:
                if api_dirs and _path_matches_api_dirs(file_path, api_dirs):
                    logger.info(
                        "Found path-confirmed dedup match in %s: %s",
                        location_name, file_path,
                    )
                    return file_path, 1.0
                if not api_dirs:
                    logger.info(
                        "Found dedup-suffix match in %s: %s",
                        location_name, file_path,
                    )
                    return file_path, 1.0
                exact_matches.append(file_path)
                continue

            # Tier 4: fuzzy basename match. Cheaper than path-walking
            # the whole tree a second time, so always compute and
            # keep the best one as a fallback.
            normalized_file = _normalize_for_finding(filename)
            similarity = SequenceMatcher(
                None, normalized_target, normalized_file,
            ).ratio()
            if similarity > highest_fuzzy_similarity:
                highest_fuzzy_similarity = similarity
                best_fuzzy_path = file_path

    if exact_matches:
        if len(exact_matches) == 1:
            logger.info(
                "Found exact match in %s: %s",
                location_name, exact_matches[0],
            )
            return exact_matches[0], 1.0
        # Multiple basename collisions — pick the one whose path
        # carries the most of the remote directory tree (album
        # folder etc.). Breaks ties deterministically.
        best = exact_matches[0]
        best_score = -1
        for m in exact_matches:
            m_parts = set(p.lower() for p in m.replace('\\', '/').split('/'))
            score = sum(1 for d in api_dirs if d in m_parts)
            if score > best_score:
                best_score = score
                best = m
        logger.info(
            "Found %d files named '%s' in %s, picked best path match: %s",
            len(exact_matches), target_basename, location_name, best,
        )
        return best, 1.0

    return best_fuzzy_path, highest_fuzzy_similarity


def find_completed_audio_file(
    download_dir: str,
    api_filename: str,
    transfer_dir: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Locate a completed download's local file via recursive walk.

    Tries the downloads tree first; if nothing above the fuzzy
    threshold lands AND a transfer_dir was passed, tries that too.

    Returns ``(file_path, location)`` where ``location`` is
    ``'downloads'`` / ``'transfer'`` / ``None``. Both elements are
    ``None`` when the file isn't found anywhere — callers should
    treat that as "not yet" (still mid-write) or "lost".
    """
    # YouTube / Tidal encoded filenames carry the id ahead of ``||``.
    # Strip it up front so basename + dir-component extraction both
    # operate on the title half.
    if api_filename and '||' in api_filename:
        _id, api_filename = api_filename.split('||', 1)
    target_basename = _extract_basename(api_filename)
    normalized_target = _normalize_for_finding(target_basename)
    api_dirs = _api_dir_parts(api_filename)

    best_dl_path, dl_sim = _search_in_directory(
        download_dir, 'downloads', target_basename, normalized_target, api_dirs,
    )

    if dl_sim > _FUZZY_THRESHOLD:
        if dl_sim < 1.0:
            logger.info(
                "Found fuzzy match in downloads (%.2f): %s",
                dl_sim, best_dl_path,
            )
        return (best_dl_path, 'downloads')

    if transfer_dir and os.path.exists(transfer_dir):
        best_tx_path, tx_sim = _search_in_directory(
            transfer_dir, 'transfer', target_basename, normalized_target, api_dirs,
        )
        if tx_sim > _FUZZY_THRESHOLD:
            if tx_sim < 1.0:
                logger.info(
                    "Found fuzzy match in transfer (%.2f): %s",
                    tx_sim, best_tx_path,
                )
            return (best_tx_path, 'transfer')

    return (None, None)


__all__ = ['find_completed_audio_file', 'AUDIO_EXTENSIONS']
