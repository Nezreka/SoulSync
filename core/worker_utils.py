"""Shared helpers for background workers."""

import logging
import re
import threading
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

# Artist-match acceptance gate. Stricter than the 0.80 each worker uses for
# album/track titles: artist names are short, so 0.80 lets distinct artists
# slip through ("ODESZA"/"odessa", "Blance"/"Blanke", "Lady A"/"Lady Gaga" all
# score 0.80-0.83). 0.85 rejects those while still tolerating real variation
# that survives normalization.
ARTIST_NAME_MATCH_THRESHOLD = 0.85

def normalize_artist_name(name: str) -> str:
    """Lowercase, drop ' - ...' suffixes / parentheticals / punctuation, and
    collapse whitespace — the same normalization the per-worker matchers use."""
    name = (name or '').lower().strip()
    name = re.sub(r'\s+[-–—]\s+.*$', '', name)
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def artist_name_matches(query: str, result: str,
                        threshold: float = ARTIST_NAME_MATCH_THRESHOLD) -> bool:
    """True if two artist names match at/above ``threshold`` after normalization."""
    nq, nr = normalize_artist_name(query), normalize_artist_name(result)
    if not nq or not nr:
        return False
    return SequenceMatcher(None, nq, nr).ratio() >= threshold


# --- Same-name artist disambiguation by owned-catalog overlap -------------
# The name gate (above) can't separate two artists who share a name ("Rone" has
# ~5). The decisive signal is the library itself: the user owns albums by the
# RIGHT one. So when several candidates clear the name gate, fetch each one's
# catalog and pick the one whose releases overlap the albums actually owned.

def normalize_release_title(title: str) -> str:
    """Collapse an album/release title for tolerant comparison — drop edition
    suffixes ('(Deluxe)', ' - Remastered'), punctuation, and case."""
    t = (title or '').lower().strip()
    t = re.sub(r'\s*\(.*?\)\s*', ' ', t)
    t = re.sub(r'\s*\[.*?\]\s*', ' ', t)
    t = re.sub(r'\s+[-–—]\s+.*$', '', t)
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def catalog_overlap_score(owned_titles, candidate_titles, threshold: float = 0.85) -> int:
    """How many OWNED album titles appear in the candidate's catalog (fuzzy,
    edition-insensitive). The disambiguation signal — higher = better match."""
    owned = {normalize_release_title(t) for t in (owned_titles or []) if t}
    owned.discard('')
    cand = {normalize_release_title(t) for t in (candidate_titles or []) if t}
    cand.discard('')
    if not owned or not cand:
        return 0
    score = 0
    for o in owned:
        if o in cand or any(SequenceMatcher(None, o, c).ratio() >= threshold for c in cand):
            score += 1
    return score


def pick_artist_by_catalog(candidates, owned_titles, fetch_titles) -> tuple:
    """Choose, among same-name candidates, the one whose catalog best overlaps the
    OWNED albums. Returns ``(chosen, overlap_score)``.

    ``candidates``   — artist objects already past the name gate (order = the
                       worker's existing best-by-name order; candidates[0] is the
                       current behavior's pick).
    ``owned_titles`` — the library artist's owned album titles.
    ``fetch_titles(candidate) -> list[str]`` — that candidate's album titles;
                       called ONLY when disambiguation is actually needed (2+
                       candidates and we have owned albums), so the common
                       single-candidate path costs no extra API calls.

    Falls back to candidates[0] (unchanged behavior) when there's nothing to
    disambiguate or no candidate overlaps the owned catalog.
    """
    candidates = list(candidates or [])
    if not candidates:
        return None, 0
    if len(candidates) == 1:
        return candidates[0], 0
    owned = [t for t in (owned_titles or []) if t]
    if not owned:
        return candidates[0], 0

    best, best_score = None, 0
    for cand in candidates:
        try:
            titles = fetch_titles(cand) or []
        except Exception as exc:
            logger.debug("catalog disambiguation: fetch_titles failed: %s", exc)
            titles = []
        score = catalog_overlap_score(owned, titles)
        if score > best_score:
            best, best_score = cand, score
    if best is not None and best_score > 0:
        return best, best_score
    return candidates[0], 0  # no overlap signal → keep the best-by-name pick


def release_titles(albums) -> list:
    """Extract titles from a list of album objects/dicts (handles ``.title``/
    ``.name`` / dict keys) — the candidate side of catalog disambiguation."""
    out = []
    for al in albums or []:
        if isinstance(al, dict):
            t = al.get('title') or al.get('name')
        else:
            t = getattr(al, 'title', None) or getattr(al, 'name', None)
        if t:
            out.append(t)
    return out


# --- Idle-queue back-off ----------------------------------------------------
# Enrichment workers poll their queue on a fixed cadence even once there's
# nothing left to enrich — a fully-caught-up library still runs the full
# multi-query "find next item" lookup every idle wake, forever. Escalating the
# sleep the longer the queue stays empty (reset the moment work appears) cuts
# that idle DB/CPU cost without adding latency once real work shows up.

IDLE_BACKOFF_BASE = 10   # first (and normal) idle sleep, seconds — matches the old fixed interval
IDLE_BACKOFF_CAP = 60    # cap so a freshly-idle worker still notices new work within a minute


def idle_backoff_seconds(empty_streak: int) -> float:
    """Seconds to sleep after finding the queue empty, escalating with
    ``empty_streak`` (consecutive empty polls) up to IDLE_BACKOFF_CAP."""
    if empty_streak <= 0:
        return IDLE_BACKOFF_BASE
    return min(IDLE_BACKOFF_BASE * (2 ** min(empty_streak, 3)), IDLE_BACKOFF_CAP)


def interruptible_sleep(stop_event: threading.Event, seconds: float, step: float = 0.5) -> bool:
    """Sleep in chunks so shutdown can interrupt long waits."""
    if seconds <= 0:
        return stop_event.is_set()

    remaining = float(seconds)
    while remaining > 0 and not stop_event.is_set():
        wait_for = min(step, remaining)
        if stop_event.wait(wait_for):
            break
        remaining -= wait_for
    return stop_event.is_set()


# --- Enrichment "process this group first" override -----------------------
# Each enrichment worker normally processes artist -> album -> track. A user
# can pin one entity type to run first via the Manage Enrichment Workers modal;
# the choice is stored in config as "<service>_enrichment_priority" and read
# at the top of each worker's _get_next_item so it takes effect live. When the
# pinned group is exhausted (or unset), the worker falls back to its normal
# chain — so the default path is unchanged.

PRIORITY_ENTITIES = ('artist', 'album', 'track')


def read_enrichment_priority(service: str) -> str:
    """Return the pinned entity ('artist'|'album'|'track') for a worker, or ''.

    Read every loop so the override applies without restarting the worker.
    Any error / unset / invalid value yields '' (no override)."""
    try:
        from core.settings import config_manager
        val = (config_manager.get(f'{service}_enrichment_priority', '') or '')
        val = str(val).strip().lower()
        return val if val in PRIORITY_ENTITIES else ''
    except Exception:
        return ''


