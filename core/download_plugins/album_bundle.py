"""Shared helpers for the album-bundle download flow.

The torrent and usenet download plugins both implement a
``download_album_to_staging`` method that searches Prowlarr for a
whole release, hands it to the active downloader, walks the
resulting audio files, and copies them into the staging folder. The
two implementations share the same release-picker heuristic and the
same staging-path collision logic.

Pulled out of ``core/download_plugins/torrent.py`` so the usenet
plugin doesn't have to import private helpers from a sibling
plugin (Cin's "no leaky module boundaries" standard).

Also exposes ``atomic_copy_to_staging`` — the audio file is copied
to a ``.tmp.<random>`` sidecar first and atomically renamed onto its
final extension. The Auto-Import worker filters by audio extension
so the in-flight ``.tmp`` file is never picked up mid-copy, closing
the race between the album-bundle copy loop and Auto-Import's
folder scan.
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("download_plugins.album_bundle")


# Album-pick size floor / ceiling. Single-track torrents (~10 MB)
# are rejected when bigger candidates exist; anything past 3 GB is
# treated as suspicious (multi-disc box-set + scans + extras).
ALBUM_PICK_MIN_BYTES = 40 * 1024 * 1024
ALBUM_PICK_MAX_BYTES = 3 * 1024 * 1024 * 1024


# Quality-score weights for the album-pick heuristic. Mirrors the
# tier order in ``core/imports/file_ops.py``'s ``quality_tiers`` —
# higher number = preferred.
_QUALITY_SCORE = {'flac': 4, 'ogg': 3, 'aac': 2, 'mp3': 1}


# Default poll cadence + timeout for the album-download poll loop.
# Both are overridable through config so users with slow trackers
# / large box-sets can extend the deadline without editing code.
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_POLL_TIMEOUT_SECONDS = 6 * 60 * 60


def get_poll_interval() -> float:
    """Return the per-poll sleep duration (seconds). Configurable via
    ``download_source.album_bundle_poll_interval_seconds``."""
    raw = config_manager.get('download_source.album_bundle_poll_interval_seconds',
                             DEFAULT_POLL_INTERVAL_SECONDS)
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_POLL_INTERVAL_SECONDS


def get_poll_timeout() -> float:
    """Return the total deadline for an album-bundle download
    (seconds). Configurable via
    ``download_source.album_bundle_timeout_seconds``."""
    raw = config_manager.get('download_source.album_bundle_timeout_seconds',
                             DEFAULT_POLL_TIMEOUT_SECONDS)
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_POLL_TIMEOUT_SECONDS


def quality_score(title: str, quality_guess) -> int:
    """Map a release title's inferred quality to a sortable integer.

    ``quality_guess`` is the function from each plugin that maps a
    title string to a quality string ('flac' / 'mp3' / etc.) — passed
    in so this module doesn't have to import either plugin and risk
    a circular import."""
    return _QUALITY_SCORE.get(quality_guess(title) or '', 0)


# Words that decorate an album title in a release name but aren't part of
# the album's identity — stripped before computing title relevance so e.g.
# 'Heroes (2017 Remaster)' and a 'David Bowie - Heroes - ... 2017' release
# still match on the core token 'heroes'.
_ALBUM_TITLE_NOISE = {
    'remaster', 'remastered', 'remasters', 'edition', 'deluxe', 'expanded',
    'anniversary', 'special', 'platinum', 'collectors', 'collector',
    'bonus', 'version', 'mono', 'stereo', 'reissue', 'the',
}

# Minimum fraction of the album's core title tokens that must appear in a
# release title for it to be considered the same album. Below this we refuse
# the candidate — downloading a different (often more-popular) album is far
# worse than failing the bundle and falling back to per-track search.
_ALBUM_TITLE_RELEVANCE_FLOOR = 0.55


def _normalize_album_text(text: Any) -> str:
    lowered = str(text or '').lower()
    lowered = re.sub(r"[‘’'`]", '', lowered)             # drop apostrophes/quotes
    lowered = re.sub(r'[^a-z0-9]+', ' ', lowered)        # everything else → space
    return re.sub(r'\s+', ' ', lowered).strip()


def _album_core_tokens(album_name: Any) -> list:
    """Significant title tokens — digits (years) and edition/remaster noise
    removed. Falls back to the raw tokens if stripping leaves nothing."""
    tokens = _normalize_album_text(album_name).split()
    core = [t for t in tokens if not t.isdigit() and t not in _ALBUM_TITLE_NOISE]
    return core or tokens


def album_title_relevance(release_title: Any, album_name: Any) -> float:
    """How well a release title matches the requested album (0.0–1.0).

    Token-coverage based: the fraction of the album's core tokens present in
    the release title, with a bonus when the full core phrase appears as a
    substring. Robust to the codec/year/group noise that pads release names
    ('Artist-Album-24-192-WEB-FLAC-REMASTERED-2017-GROUP')."""
    core = _album_core_tokens(album_name)
    if not core:
        return 0.0
    release_norm = _normalize_album_text(release_title)
    release_tokens = set(release_norm.split())
    present = sum(1 for token in core if token in release_tokens)
    coverage = present / len(core)
    phrase = ' '.join(core)
    if phrase and phrase in release_norm:
        coverage = max(coverage, 0.9)
    return coverage


def pick_best_album_release(
    candidates, quality_guess, album_name: str = '', artist_name: str = '',
) -> Optional[object]:
    """Pick the single best torrent / NZB for an album-bundle download.

    Heuristic, in priority order:
    1. Title relevance — the release must actually be the requested album.
       Prowlarr/indexers return broad fuzzy matches (a 'Heroes' search also
       returns 'Scary Monsters'), and ranking purely by popularity then
       grabs the wrong, more-popular album. When ``album_name`` is supplied
       we drop candidates whose title doesn't cover the album's core tokens
       (``_ALBUM_TITLE_RELEVANCE_FLOOR``), and refuse rather than download a
       mismatch if none qualify. Soulseek is unaffected — it uses the
       title/artist/coverage-aware album pre-flight instead of this picker.
    2. Reasonable album-ish size (40 MB – 3 GB) — drops single-track
       releases that snuck in and quarantines suspicious giants.
    3. Higher seeders > lower (dead torrents = dead downloads).
       Usenet releases use ``grabs`` as a popularity proxy when
       seeders is None.
    4. Higher quality (FLAC > AAC > MP3) inferred from title.
    5. Larger size as tiebreaker (often = higher bitrate).
    """
    if not candidates:
        return None

    pool = list(candidates)

    # 1. Title relevance gate (only when we know the target album).
    if album_name:
        relevant = [
            c for c in pool
            if album_title_relevance(getattr(c, 'title', '') or '', album_name)
            >= _ALBUM_TITLE_RELEVANCE_FLOOR
        ]
        if not relevant:
            logger.warning(
                "[album_bundle] No candidate title matched album %r (checked %d) "
                "— refusing to grab a mismatched release",
                album_name, len(pool),
            )
            return None
        pool = relevant

    # 2. Size sanity.
    sized = [c for c in pool
             if ALBUM_PICK_MIN_BYTES <= (c.size or 0) <= ALBUM_PICK_MAX_BYTES]
    pool = sized or pool
    if not pool:
        return None

    def _score(c) -> tuple:
        seeders = c.seeders if c.seeders is not None else (c.grabs or 0)
        # Relevance bucket first so a strong title match always beats a
        # weakly-matching but more-popular release.
        relevance = album_title_relevance(getattr(c, 'title', '') or '', album_name) if album_name else 0.0
        return (round(relevance, 2), seeders, quality_score(c.title or '', quality_guess), c.size or 0)

    return max(pool, key=_score)


def unique_staging_path(staging_dir: Path, src: Path) -> Path:
    """Return a destination path inside ``staging_dir`` that doesn't
    collide with an existing file. Appends ``_1``, ``_2``, ... before
    the extension when needed; gives up after 1000 candidates and
    returns the unsuffixed path so the caller will overwrite (better
    than infinite loop or crash)."""
    dest = staging_dir / src.name
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    for i in range(1, 1000):
        candidate = staging_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return dest


def atomic_copy_to_staging(src: Path, dest: Path) -> bool:
    """Copy ``src`` to ``dest`` without exposing a partial file to
    folder scanners.

    The Auto-Import worker filters by audio extension when scanning
    Staging — see ``AUDIO_EXTENSIONS`` in ``core/auto_import_worker.py``.
    Naming the in-flight file ``<dest>.tmp.<random>`` keeps it
    invisible until the rename atomically swings it to its final
    extension. ``os.replace`` (used by ``Path.rename`` on Python 3.x)
    is atomic on the same filesystem, so Auto-Import either sees the
    file at its final name (complete) or doesn't see it at all
    (in flight).

    Returns True on success, False on copy / rename failure. Caller
    is expected to log the failure case so we don't double-log here.
    """
    tmp = dest.with_name(f"{dest.name}.tmp.{uuid.uuid4().hex[:8]}")
    try:
        shutil.copy2(src, tmp)
    except Exception:
        # Best-effort cleanup of the partial file. If unlink fails
        # (locked, permissions) we leave it — Auto-Import ignores it
        # anyway because of the .tmp extension.
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception as cleanup_exc:
            logger.debug("album_bundle tmp cleanup failed: %s", cleanup_exc)
        raise
    try:
        tmp.replace(dest)
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception as cleanup_exc:
            logger.debug("album_bundle tmp cleanup failed: %s", cleanup_exc)
        raise


# Number of consecutive None-status reads tolerated before treating the
# job as gone. Sized for the SAB queue→history transition window: SAB
# removes the slot from the queue before adding it to history, and on a
# busy server (par2 verify + unrar) that window can be several poll
# intervals. At the default 2s interval, 5 retries = ~10s of tolerance
# before we give up and emit a terminal failure. Override via
# ``download_source.album_bundle_transient_miss_threshold`` for users
# whose servers need more headroom (very large multi-disc box sets,
# slow disks, etc.).
DEFAULT_TRANSIENT_MISS_THRESHOLD = 5


def get_transient_miss_threshold() -> int:
    """Return the configured transient-miss threshold for poll loops."""
    raw = config_manager.get('download_source.album_bundle_transient_miss_threshold',
                             DEFAULT_TRANSIENT_MISS_THRESHOLD)
    try:
        value = int(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_TRANSIENT_MISS_THRESHOLD


class TransientMissCounter:
    """Bounded retry counter for adapter status reads.

    Both the album-bundle poll (in ``poll_album_download``) and the
    per-track download threads in ``usenet.py`` / ``torrent.py`` need
    the same "tolerate N consecutive missing or unmapped reads before
    declaring the job gone" logic. Lifted into one class so the rule
    is in one place and unit-testable in isolation — the per-track
    paths used to carry inline counters that mirrored this logic by
    hand, which is exactly the kind of duplication that drifts."""

    def __init__(self, threshold: Optional[int] = None) -> None:
        self.threshold = threshold if threshold is not None else get_transient_miss_threshold()
        self.misses = 0

    def record_miss(self) -> bool:
        """Bump the miss counter. Returns True when the counter has
        reached the threshold (caller should give up)."""
        self.misses += 1
        return self.misses >= self.threshold

    def reset(self) -> None:
        """Successful read — reset the counter back to zero."""
        self.misses = 0


def poll_album_download(
    *,
    get_status: Callable[[], Optional[Any]],
    title: str,
    emit: Callable[..., None],
    complete_states: frozenset,
    failed_states: frozenset = frozenset(['failed']),
    is_shutdown: Optional[Callable[[], bool]] = None,
    transient_miss_threshold: int = DEFAULT_TRANSIENT_MISS_THRESHOLD,
    poll_interval: Optional[float] = None,
    timeout: Optional[float] = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    log_prefix: str = '[album_bundle]',
) -> Optional[str]:
    """Drive the per-poll status loop for an album-bundle download.

    Lifted out of ``UsenetDownloadPlugin._poll_album_download`` and the
    sibling torrent method so the loop is testable in isolation and so
    both plugins share the same exit semantics.

    Contract:
    - ``get_status()`` returns the adapter status object for the bound
      job, ``None`` when the client doesn't know about the job
      currently (transient or terminal — disambiguated by retry count).
    - ``emit(state, **fields)`` is the plugin's progress callback —
      this function calls it on EVERY successful poll with
      ``state='downloading'`` and ALWAYS calls it once more with
      ``state='failed'`` before returning ``None`` on any failure path,
      so the UI doesn't freeze on the last 'downloading' emit.
    - ``complete_states`` is the adapter's terminal-success set
      ('completed' alone for usenet; 'seeding' + 'completed' for
      torrent because seeding-but-files-on-disk also counts).
    - ``failed_states`` is the explicit-failure set. The adapter-level
      'error' (unmapped state default) is intentionally NOT in here —
      that's treated as a transient miss because a real SAB / NZBGet
      / qBit never returns a literal 'error' state on a healthy job;
      it's only our default fallback for unknown queue strings. Real
      example: SAB's 'Pp' post-processing state was unmapped → became
      'error' → poll infinite-looped until the 6-hour timeout.
    - ``transient_miss_threshold`` is the number of consecutive None /
      'error' reads tolerated before declaring the job gone. Sized for
      the SAB queue→history gap window.

    Returns the adapter's reported save_path on terminal success, or
    ``None`` on any failure (timeout / disappeared / explicit failed
    / shutdown). On every failure path emits ``'failed'`` once with an
    ``error`` field describing why.
    """
    interval = poll_interval if poll_interval is not None else get_poll_interval()
    deadline = monotonic() + (timeout if timeout is not None else get_poll_timeout())
    last_save_path: Optional[str] = None
    misses = TransientMissCounter(transient_miss_threshold)
    # Separate counter for "client reports terminal-success state but no
    # save_path field has landed yet." SAB History flips ``status`` to
    # 'Completed' a few seconds before its post-processing pipeline
    # writes the final ``storage`` field — see issue #721 (Forty Licks
    # stuck at 61%): SAB shows Completed in the UI, but
    # ``_parse_history_slot`` returns ``save_path=None`` for those few
    # seconds because ``storage`` isn't populated yet. Pre-fix the
    # poll returned ``None`` on the first such read, the bundle
    # plugin marked the batch failed, but the UI still displayed the
    # last ``downloading`` progress emit. Now we retry up to the
    # same threshold so SAB has a window to write the path.
    completed_no_path_misses = TransientMissCounter(transient_miss_threshold)

    def _fail(reason: str) -> None:
        try:
            emit('failed', release=title, error=reason)
        except Exception as cb_exc:
            logger.debug("%s terminal emit failed: %s", log_prefix, cb_exc)

    while monotonic() < deadline:
        if is_shutdown and is_shutdown():
            # Shutdown is a clean exit — don't paint failure on the UI;
            # the app is going away anyway.
            return None

        try:
            status = get_status()
        except Exception as e:
            logger.warning("%s Poll error: %s", log_prefix, e)
            status = None

        if status is None:
            if misses.record_miss():
                logger.error(
                    "%s '%s' missing from client for %d consecutive polls — giving up",
                    log_prefix, title, misses.misses,
                )
                _fail('Disappeared from client (no status after retries)')
                return None
            sleep(interval)
            continue

        # Reset the miss counter only when the adapter returned a state
        # we actually recognise. The default-fallback 'error' is treated
        # as a continuing transient miss below, so it must NOT reset
        # here — otherwise a persistently-unmapped state loops forever.
        if status.state != 'error':
            misses.reset()

        emit('downloading', progress=status.progress, downloaded=status.downloaded,
             speed=status.download_speed)
        if status.save_path:
            last_save_path = status.save_path

        if status.state in complete_states:
            if last_save_path:
                completed_no_path_misses.reset()
                return last_save_path
            # Terminal-success state but no save_path landed yet.
            # SAB History flips ``Completed`` a few seconds before
            # ``storage`` is populated — give the adapter a few more
            # polls before declaring this a hard failure. Without this
            # tolerance, every TAR / unrar-bearing usenet release
            # would race the path-write window and randomly fail.
            if completed_no_path_misses.record_miss():
                logger.error(
                    "%s '%s' reported terminal success but no save_path landed "
                    "after %d consecutive polls — bundle cannot stage. Adapter "
                    "may need new history-slot fallback fields (storage / path "
                    "/ download_path / dirname). Last status: state=%r progress=%r",
                    log_prefix, title, completed_no_path_misses.misses,
                    status.state, status.progress,
                )
                _fail('Client reported success but never provided a save_path')
                return None
            logger.info(
                "%s '%s' is %s on the client but save_path not yet set — "
                "retrying (poll %d/%d)",
                log_prefix, title, status.state,
                completed_no_path_misses.misses, completed_no_path_misses.threshold,
            )
            sleep(interval)
            continue
        if status.state in failed_states:
            error = getattr(status, 'error', None) or 'Client reported failure'
            logger.error("%s '%s' failed: %s", log_prefix, title, error)
            _fail(error)
            return None
        if status.state == 'error':
            # Unmapped adapter state — see contract docstring. Warn so
            # we hear about new states the adapter map needs to grow
            # without breaking the user's download. The miss counter
            # was intentionally NOT reset above for this branch.
            logger.warning(
                "%s '%s' returned unmapped state — treating as transient",
                log_prefix, title,
            )
            if misses.record_miss():
                _fail('Client returned unmapped state repeatedly')
                return None

        sleep(interval)

    logger.error("%s '%s' timed out", log_prefix, title)
    _fail('Download timed out')
    return None


def copy_audio_files_atomically(
    sources: Iterable[Path], staging_dir: Path,
) -> list:
    """Convenience wrapper: pick a non-colliding staging path for
    each source, copy via ``atomic_copy_to_staging``. Returns the
    list of final destination paths (as strings). Files that fail
    to copy are logged and skipped; the caller decides what to do
    with a partial result."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    out: list = []
    for src in sources:
        dest = unique_staging_path(staging_dir, src)
        try:
            atomic_copy_to_staging(src, dest)
            out.append(str(dest))
        except Exception as e:
            logger.warning("[album_bundle] Failed to stage %s -> %s: %s", src, dest, e)
    return out


# Re-export so callers don't have to remember which module owns
# what. The ``time`` import is kept so plugins can ``from
# core.download_plugins.album_bundle import time`` if they want to,
# avoiding a second std-lib import line for a single use.
__all__ = [
    "ALBUM_PICK_MIN_BYTES",
    "ALBUM_PICK_MAX_BYTES",
    "album_title_relevance",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_POLL_TIMEOUT_SECONDS",
    "DEFAULT_TRANSIENT_MISS_THRESHOLD",
    "TransientMissCounter",
    "atomic_copy_to_staging",
    "copy_audio_files_atomically",
    "get_poll_interval",
    "get_poll_timeout",
    "get_transient_miss_threshold",
    "pick_best_album_release",
    "poll_album_download",
    "quality_score",
    "time",
    "unique_staging_path",
]
