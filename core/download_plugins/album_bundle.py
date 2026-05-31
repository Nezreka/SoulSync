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
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("download_plugins.album_bundle")

# Minimum album-title relevance a Prowlarr candidate must clear to be eligible
# for an album-bundle download (#730). Prowlarr returns broad fuzzy matches — a
# "Heroes" search also returns other Bowie albums — so without this gate the
# most-popular result wins regardless of whether it's the right album. Below
# this floor we refuse the bundle and let the caller fall back to per-track.
_ALBUM_TITLE_RELEVANCE_FLOOR = 0.6


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


def _normalize_release_text(text: str) -> str:
    """Lowercase, fold accents (Björk -> bjork), strip punctuation to spaces.

    NFKD-decompose then drop combining marks so accented characters fold to
    their base letter instead of fragmenting (the naive approach turned
    'Björk' into 'bj rk'). Collapses runs of whitespace.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.lower()
    # Punctuation -> space (so "heroes" matches "heroes:" / "heroes -"),
    # then collapse whitespace.
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


# Edition / format / qualifier words that appear in stored album names or
# release titles but say nothing about WHICH album it is. Stripped before
# scoring so "Currents" matches "Currents (Deluxe)" and "Heroes" matches
# "Heroes (2017 Remaster)" — the #730 fix must not reject the RIGHT album just
# because the DB name carries an edition suffix the torrent title lacks.
_ALBUM_NOISE_WORDS = frozenset({
    "deluxe", "edition", "remaster", "remastered", "remasters", "remix",
    "expanded", "anniversary", "bonus", "version", "explicit", "clean",
    "reissue", "special", "limited", "collectors", "collector", "the",
    "ep", "lp", "album", "single", "disc", "cd", "vol", "volume",
    "flac", "mp3", "aac", "ogg", "wav", "alac", "m4a", "320", "256", "192",
    "web", "vinyl", "hi", "res", "hires", "24bit", "16bit", "original",
    "soundtrack", "ost",
})


def _significant_words(normalized: str) -> list:
    """Words that actually identify an album: drop pure-digit tokens (years,
    bitrates) and edition/format noise. Keeps at least the raw words if the
    filter would empty it (e.g. an album literally named '1989' or 'Deluxe')."""
    words = [w for w in normalized.split()
             if w not in _ALBUM_NOISE_WORDS and not w.isdigit()]
    return words or normalized.split()


def album_title_relevance(candidate_title: str, album_name: str) -> float:
    """How well a release title matches the requested album, 0.0–1.0.

    Scores the fraction of the album's SIGNIFICANT words (edition/format/year
    noise removed) that appear as whole words in the candidate title.
    Word-boundary, not substring, so "Heroes" does NOT match "Superheroes" and
    a different album sharing no significant words scores 0 — while "Currents"
    still matches "Currents (Deluxe)" and "Heroes" matches the "2017 Remaster".

    Returns 1.0 when there's no album name to check (can't gate on nothing —
    preserves old behavior for callers that don't pass a title).
    """
    norm_album = _normalize_release_text(album_name)
    if not norm_album:
        return 1.0
    norm_title = _normalize_release_text(candidate_title)
    if not norm_title:
        return 0.0
    album_words = _significant_words(norm_album)
    title_words = set(norm_title.split())
    if not album_words:
        return 1.0
    matched = sum(1 for w in album_words if w in title_words)
    return matched / len(album_words)


def pick_best_album_release(candidates, quality_guess,
                            album_name: str = "", artist_name: str = "") -> Optional[object]:
    """Pick the single best torrent / NZB for an album-bundle download.

    Heuristic, in priority order:
    0. Album-TITLE relevance gate (#730): drop candidates whose title doesn't
       sufficiently match the requested album. Prowlarr returns broad fuzzy
       matches, so without this the most-popular result wins even when it's a
       different album. When ``album_name`` is given and NOTHING clears the
       relevance floor, return None — the caller then falls back to per-track
       rather than downloading a confident mismatch.
    1. Reasonable album-ish size (40 MB – 3 GB) — drops single-track
       releases that snuck in and quarantines suspicious giants.
    2. Higher seeders > lower (dead torrents = dead downloads).
       Usenet releases use ``grabs`` as a popularity proxy when
       seeders is None.
    3. Higher quality (FLAC > AAC > MP3) inferred from title.
    4. Larger size as tiebreaker (often = higher bitrate).
    """
    if not candidates:
        return None

    # 0. Title-relevance gate. Only applied when we know the album name; with
    # no name we can't judge relevance, so we don't gate (old behavior).
    if album_name:
        relevant = [
            c for c in candidates
            if album_title_relevance(c.title or "", album_name) >= _ALBUM_TITLE_RELEVANCE_FLOOR
        ]
        if not relevant:
            logger.warning(
                "[Album Bundle] No candidate cleared the title-relevance floor "
                "for '%s' (%d candidates rejected as wrong album) — refusing the "
                "bundle so the caller falls back to per-track.",
                album_name, len(candidates),
            )
            return None
        candidates = relevant

    sized = [c for c in candidates
             if ALBUM_PICK_MIN_BYTES <= (c.size or 0) <= ALBUM_PICK_MAX_BYTES]
    pool = sized or list(candidates)
    if not pool:
        return None

    def _score(c) -> tuple:
        seeders = c.seeders if c.seeders is not None else (c.grabs or 0)
        return (seeders, quality_score(c.title or '', quality_guess), c.size or 0)

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


# How long to keep polling after the client reports terminal success
# but hasn't yet exposed a final save_path. Distinct from the
# transient-miss threshold because the two model different things:
# a transient miss is "the job vanished — fail fast (~10s) so a deleted
# job doesn't hang"; a completed-no-path read is "the download SUCCEEDED
# and the files are on disk — SAB just hasn't finished writing the
# ``storage`` field." The #706 fix reused the 5-poll (~10s) miss window
# here, but #721's own report shows SAB can take 2+ minutes (or, on some
# versions, never expose ``storage`` at all) — so a 10s window false-fails
# a download that actually completed. Expressed in SECONDS (converted to
# a poll count against the live interval) so it's interval-independent.
# Override via ``download_source.album_bundle_completed_no_path_seconds``.
DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS = 120.0


def get_completed_no_path_window_seconds() -> float:
    """Return the completed-but-no-save_path tolerance window (seconds)."""
    raw = config_manager.get('download_source.album_bundle_completed_no_path_seconds',
                             DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS)
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS


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
    completed_no_path_threshold: Optional[int] = None,
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
      the SAB queue→history gap window (~10s) — a vanished job should
      fail fast.
    - ``completed_no_path_threshold`` is a SEPARATE, longer window for
      the "client says complete but no save_path yet" case. The download
      already succeeded, so this defaults to ~120s (configurable via
      ``download_source.album_bundle_completed_no_path_seconds``) instead
      of reusing the 10s miss window — #721 showed SAB can take 2+ minutes
      to write ``storage``. When the window is exhausted the loop falls
      back to the adapter's ``incomplete_path`` (the on-disk in-progress
      dir) if present, and only emits terminal ``failed`` when there's no
      path of any kind to scan.

    Returns the adapter's reported save_path (or, as a last resort, its
    ``incomplete_path``) on terminal success, or ``None`` on any failure
    (timeout / disappeared / explicit failed / shutdown). On every
    failure path emits ``'failed'`` once with an ``error`` field
    describing why.
    """
    interval = poll_interval if poll_interval is not None else get_poll_interval()
    deadline = monotonic() + (timeout if timeout is not None else get_poll_timeout())
    last_save_path: Optional[str] = None
    last_incomplete_path: Optional[str] = None
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
    # last ``downloading`` progress emit.
    #
    # This window is intentionally LONGER than the transient-miss window:
    # the download already SUCCEEDED, so being patient here is cheap and
    # correct, whereas the original 5-poll (~10s) reuse false-failed real
    # completions (#721 reported SAB taking 2+ minutes). Default ~120s,
    # converted from seconds to a poll count against the live interval.
    if completed_no_path_threshold is None:
        completed_no_path_threshold = max(
            transient_miss_threshold,
            int(get_completed_no_path_window_seconds() / max(interval, 0.001)) or 1,
        )
    completed_no_path_misses = TransientMissCounter(completed_no_path_threshold)

    def _fail(reason: str) -> None:
        try:
            emit('failed', release=title, error=reason)
        except Exception as cb_exc:
            logger.debug("%s terminal emit failed: %s", log_prefix, cb_exc)

    # Heartbeat so the otherwise-silent download loop is diagnosable.
    # The loop emits progress to the UI on every poll but logs nothing
    # during normal operation — which made the #721 "stuck at N%" reports
    # impossible to triage from logs alone (we couldn't tell if the poll
    # was alive, what state SAB returned, or whether it had wedged). Log
    # the raw adapter read at most once per heartbeat interval.
    HEARTBEAT_SECONDS = 30.0
    last_heartbeat = monotonic()
    poll_count = 0

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

        poll_count += 1
        now = monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            last_heartbeat = now
            if status is None:
                logger.info("%s '%s' poll #%d: client returned no status (miss %d/%d)",
                            log_prefix, title, poll_count, misses.misses, misses.threshold)
            else:
                logger.info(
                    "%s '%s' poll #%d: state=%r progress=%.2f save_path=%r",
                    log_prefix, title, poll_count,
                    getattr(status, 'state', None), getattr(status, 'progress', 0.0) or 0.0,
                    getattr(status, 'save_path', None),
                )

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
        # Remember the in-progress dir too — never used on a normal
        # completion, only as the last-resort fallback below when the
        # final save_path provably never lands.
        incomplete_path = getattr(status, 'incomplete_path', None)
        if incomplete_path:
            last_incomplete_path = incomplete_path

        if status.state in complete_states:
            if last_save_path:
                completed_no_path_misses.reset()
                return last_save_path
            # Terminal-success state but no save_path landed yet.
            # SAB History flips ``Completed`` a few seconds before
            # ``storage`` is populated — give the adapter a generous
            # window before declaring this a hard failure. Without this
            # tolerance, every TAR / unrar-bearing usenet release
            # would race the path-write window and randomly fail.
            if completed_no_path_misses.record_miss():
                # Last resort before failing: SAB finished and the files
                # are physically on disk (#721), but the final ``storage``
                # field never landed. Fall back to the in-progress dir so
                # the bundle can still scan + stage the audio, rather than
                # leaving the user stuck with a completed-in-SAB download
                # that SoulSync never imports.
                if last_incomplete_path:
                    logger.warning(
                        "%s '%s' completed on the client but never exposed a final "
                        "save_path after %d polls — falling back to the in-progress "
                        "path %r as a last resort. If staging fails, the SAB job "
                        "likely needs its post-process move to finish first.",
                        log_prefix, title, completed_no_path_misses.misses,
                        last_incomplete_path,
                    )
                    return last_incomplete_path
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


def _candidate_download_roots(config_get: Callable[..., Any]) -> list:
    """Directories where THIS process can read finished downloads — used by
    ``resolve_reported_save_path`` for the basename fallback.

    Order matters: most-specific usenet/torrent roots first, then the
    general Soulseek download / transfer dirs, which in the standard
    shared-volume arr setup are bind-mounted to the very directory the
    usenet client writes its completed downloads into. Relative values
    (e.g. ``./downloads``) resolve against the process CWD — the
    container's ``/app`` — which is exactly where those mounts live.
    """
    roots: list = []
    for key in (
        'download_source.usenet_download_path',
        'usenet_client.completed_path',
        'usenet_client.download_path',
        'download_source.torrent_download_path',
        'soulseek.download_path',
        'soulseek.transfer_path',
    ):
        value = config_get(key, None)
        if value:
            roots.append(str(value))
    seen: set = set()
    out: list = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            out.append(root)
    return out


def resolve_reported_save_path(
    reported_path: Optional[str],
    config_get: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """Translate a downloader-reported save_path into one THIS process can read.

    Usenet / torrent clients report paths from inside THEIR OWN container
    (e.g. SAB hands back ``/data/downloads/music/<album>``); SoulSync often
    mounts the very same files at a different point (``/app/downloads/<album>``).
    Feeding the client's path straight to the audio walker then yields
    "No audio files found" even though the files are physically present —
    the classic arr-stack remote-path mismatch.

    Resolution order:
      1. The reported path verbatim, if it's a readable directory here
         (deployments that mirror the client's mount paths).
      2. Explicit prefix mappings from ``download_source.usenet_path_mappings``
         — a list of ``{"from": "...", "to": "..."}`` (Sonarr/Radarr-style
         remote path mapping) for non-shared / oddly-mounted layouts.
      3. Basename fallback: a same-named folder under a known SoulSync
         download root. Zero-config for the standard shared-volume setup —
         the album folder shows up under SoulSync's own ``./downloads``
         mount with the same name the client reported.

    Returns the best resolved path, or ``reported_path`` unchanged when
    nothing better is found (so the caller's existing "no audio" error still
    surfaces, with both paths logged).
    """
    if not reported_path:
        return reported_path
    if config_get is None:
        config_get = config_manager.get

    def _is_dir(candidate) -> bool:
        try:
            return Path(candidate).is_dir()
        except OSError:
            return False

    # 1. Reported path is directly readable — mounts already line up.
    if _is_dir(reported_path):
        return reported_path

    normalized = str(reported_path).replace('\\', '/')

    # 2. Explicit prefix mappings (remote-path-mapping escape hatch).
    mappings = config_get('download_source.usenet_path_mappings', None) or []
    if isinstance(mappings, (list, tuple)):
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            frm = str(mapping.get('from') or '').replace('\\', '/').rstrip('/')
            to = str(mapping.get('to') or '')
            if not frm or not to:
                continue
            if normalized == frm or normalized.startswith(frm + '/'):
                rest = normalized[len(frm):].lstrip('/')
                candidate = str(Path(to) / rest) if rest else to
                if _is_dir(candidate):
                    return candidate

    # 3. Basename fallback under known download roots — covers the standard
    #    shared-volume layout with zero configuration.
    basename = Path(normalized).name
    if basename:
        for root in _candidate_download_roots(config_get):
            candidate = Path(root) / basename
            if _is_dir(candidate):
                return str(candidate)

    return reported_path


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
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_POLL_TIMEOUT_SECONDS",
    "DEFAULT_TRANSIENT_MISS_THRESHOLD",
    "DEFAULT_COMPLETED_NO_PATH_WINDOW_SECONDS",
    "TransientMissCounter",
    "atomic_copy_to_staging",
    "copy_audio_files_atomically",
    "get_completed_no_path_window_seconds",
    "get_poll_interval",
    "get_poll_timeout",
    "get_transient_miss_threshold",
    "resolve_reported_save_path",
    "pick_best_album_release",
    "poll_album_download",
    "quality_score",
    "time",
    "unique_staging_path",
]
