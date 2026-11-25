"""Live folder watching: a change on disk reaches the library without a scan.

Watches every music library folder (the music library folder, each profile's
own library folder, Settings > Library extra music folders) and the Import
folder. A change is collapsed to its album folder, waits until the folder is
quiet and its files stop growing, and then that album folder alone goes
through the library's own per-folder rescan (``core.library2.folder_rescan``).
A change in the Import folder wakes the auto-import instead of waiting for its
next timer.

Two ways to watch, picked per folder and shown in Settings > Library > Folders:

- **live**: the system's change notifications (inotify on Linux,
  ReadDirectoryChangesW on Windows, FSEvents on macOS) through ``watchdog``.
- **polling**: a timer walks the folders and compares each one with what it
  saw last time. Used on network shares (SMB, NFS, a Windows UNC path or
  mapped drive, Docker Desktop's host folders), where change notifications
  don't arrive reliably, when a library has more folders than Linux lets one
  program watch, and when watchdog is not installed.

What is never acted on: temp and partial files, dotfiles and system folders,
files still growing, and paths SoulSync itself is writing
(``core.library.own_writes``). A library folder that vanished is an outage:
nothing in it is marked missing, the watcher waits for it to come back.

Resource limits: one worker thread plus watchdog's own; pending changes are a
bounded map that coarsens to parent folders instead of growing; every thread
stops on ``stop()``.
"""

from __future__ import annotations

import errno
import os
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.library import own_writes
from core.library.folder_watch_paths import (
    AUTO, LIVE, MODES, OFF, POLLING,
    album_unit, classify, coarsen, ignored_dir_name, is_audio_name, is_ignored,
    is_under, norm, parent, parse_mounts,
)
from utils.logging_config import get_logger

logger = get_logger("library.folder_watch")

ENABLED_KEY = "library.watch_folders"
POLL_MINUTES_KEY = "library.watch_poll_minutes"
ROOT_MODES_KEY = "library.watch_root_modes"
MAX_LIVE_KEY = "library.watch_max_live_folders"

DEFAULT_POLL_MINUTES = 15
DEFAULT_MAX_LIVE_FOLDERS = 100_000
#: seconds a folder has to be quiet before it is read
QUIET_SECONDS = 5.0
#: a file modified this recently is still being written
STABLE_SECONDS = 3.0
#: pending album folders per library folder before they coarsen to parents
MAX_PENDING_UNITS = 2_000
#: moves the system reported, kept per library folder until processed
MAX_PENDING_MOVES = 5_000
#: a polling walk stops here: a folder this big is reported, not walked forever
MAX_POLL_FOLDERS = 500_000
#: a poll that finds this share of known folders gone is an outage, not a delete
POLL_VANISH_SHARE = 0.5
POLL_VANISH_MIN = 20
#: share of max_user_watches the watcher allows itself (other programs need some)
INOTIFY_SHARE = 0.8
#: polling rounds it takes to check every file's size and date once
STAT_SLICES = 12

LIBRARY = "library"
IMPORT = "import"


def watchdog_available() -> bool:
    try:
        import watchdog.observers  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - not installed: polling only
        return False


# ── pending changes ──────────────────────────────────────────────────────


@dataclass
class _Unit:
    recursive: bool
    first: float
    last: float
    snapshot: Optional[Tuple] = None
    looks: int = 0


class PendingChanges:
    """Album folders waiting for their quiet window, per library folder.

    Bounded: past ``cap`` folders for one library folder the set coarsens to
    parent folders (5,000 albums copied at once become their artist folders,
    then the library folder itself), so a flood never grows memory."""

    def __init__(self, cap: int = MAX_PENDING_UNITS, max_moves: int = MAX_PENDING_MOVES):
        self.cap = cap
        self.max_moves = max_moves
        self._lock = threading.Lock()
        self._units: Dict[str, Dict[str, _Unit]] = {}
        self._moves: Dict[str, List[Tuple[str, str]]] = {}

    def add(self, root: str, folder: str, recursive: bool, now: float) -> None:
        with self._lock:
            units = self._units.setdefault(root, {})
            unit = units.get(folder)
            if unit is None and self._covered(units, root, folder):
                return
            if unit is None:
                units[folder] = _Unit(recursive, now, now)
            else:
                unit.recursive = unit.recursive or recursive
                unit.last = now
                unit.snapshot = None
                unit.looks = 0
            if len(units) > self.cap:
                self._coarsen(root, now)

    @staticmethod
    def _covered(units: Dict[str, _Unit], root: str, folder: str) -> bool:
        """Already inside a folder that is read with everything in it."""
        up = folder
        while True:
            nxt = parent(up)
            if not nxt or nxt == up or not is_under(nxt, root):
                return False
            above = units.get(nxt)
            if above is not None and above.recursive:
                return True
            up = nxt

    def add_move(self, root: str, src: str, dst: str) -> None:
        with self._lock:
            moves = self._moves.setdefault(root, [])
            if len(moves) < self.max_moves:
                moves.append((src, dst))

    def _coarsen(self, root: str, now: float) -> None:
        units = self._units[root]
        merged = coarsen({f: u.recursive for f, u in units.items()}, root, self.cap // 2)
        first = min((u.first for u in units.values()), default=now)
        self._units[root] = {f: _Unit(r, first, now) for f, r in merged.items()}
        logger.info("folder watch: many changes at once under %s, reading %d folder(s) "
                    "instead of %d", root, len(merged), len(units))

    def ready(self, now: float, quiet: float) -> Dict[str, List[Tuple[str, _Unit]]]:
        """Pop every folder quiet for ``quiet`` seconds, per library folder."""
        out: Dict[str, List[Tuple[str, _Unit]]] = {}
        with self._lock:
            for root, units in self._units.items():
                done = [f for f, u in units.items() if now - u.last >= quiet]
                for folder in done:
                    out.setdefault(root, []).append((folder, units.pop(folder)))
        return out

    def requeue(self, root: str, folder: str, unit: _Unit, now: float) -> None:
        with self._lock:
            units = self._units.setdefault(root, {})
            current = units.get(folder)
            if current is not None:
                current.recursive = current.recursive or unit.recursive
                return
            unit.last = now
            units[folder] = unit

    def take_moves(self, root: str) -> List[Tuple[str, str]]:
        with self._lock:
            return self._moves.pop(root, [])

    def drop(self, root: str) -> int:
        with self._lock:
            self._moves.pop(root, None)
            return len(self._units.pop(root, {}) or {})

    def count(self, root: Optional[str] = None) -> int:
        with self._lock:
            if root is not None:
                return len(self._units.get(root, {}))
            return sum(len(u) for u in self._units.values())


def folder_snapshot(folder: str, recursive: bool) -> Tuple[Tuple, float]:
    """``(sorted (path, size, mtime) of the audio under folder, newest mtime)``."""
    entries: List[Tuple[str, int, float]] = []
    newest = 0.0
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive and not ignored_dir_name(entry.name):
                                stack.append(entry.path)
                            continue
                        if not is_audio_name(entry.name):
                            continue
                        st = entry.stat()
                    except OSError:
                        continue
                    entries.append((entry.path, st.st_size, st.st_mtime))
                    newest = max(newest, st.st_mtime)
        except OSError:
            continue
    entries.sort()
    return tuple(entries), newest


# ── polling ──────────────────────────────────────────────────────────────


class DirectoryPoller:
    """Finds changed folders by walking a library folder on a timer.

    Every round reads each folder's own date and the names of the audio in it
    (one directory listing per folder); the sizes and dates of the files
    themselves are read for a twelfth of the folders per round, so an edit in
    place (a retag) is found within twelve rounds without statting every file
    of a network share every time. The first walk only learns the layout."""

    def __init__(self, root: str, *, excluded: Tuple[str, ...] = (),
                 max_folders: int = MAX_POLL_FOLDERS, slices: int = STAT_SLICES):
        self.root = norm(root)
        self.excluded = tuple(excluded)
        self.max_folders = max_folders
        self.slices = max(1, slices)
        self.state: Dict[str, Tuple[int, int, Optional[int]]] = {}
        self.primed = False
        self.rounds = 0
        self.truncated = False

    def _in_slice(self, folder: str) -> bool:
        return zlib.crc32(folder.encode("utf-8", "replace")) % self.slices == self.rounds % self.slices

    def _walk(self) -> Dict[str, Tuple[int, int, Optional[int]]]:
        seen: Dict[str, Tuple[int, int, Optional[int]]] = {}
        stack = [self.root]
        self.truncated = False
        while stack:
            folder = stack.pop()
            if len(seen) >= self.max_folders:
                self.truncated = True
                break
            try:
                mtime = os.stat(folder).st_mtime_ns
                names: List[str] = []
                files: List[os.DirEntry] = []
                with os.scandir(folder) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not ignored_dir_name(entry.name) and not any(
                                        is_under(entry.path, ex) for ex in self.excluded):
                                    stack.append(entry.path)
                            elif is_audio_name(entry.name):
                                names.append(entry.name)
                                files.append(entry)
                        except OSError:
                            continue
            except OSError:
                continue
            names.sort()
            name_sig = zlib.crc32("\0".join(names).encode("utf-8", "replace"))
            stat_sig = None
            if files and self._in_slice(folder):
                parts = []
                for entry in sorted(files, key=lambda e: e.name):
                    try:
                        st = entry.stat()
                        parts.append(f"{entry.name}\0{st.st_size}\0{st.st_mtime_ns}")
                    except OSError:
                        parts.append(entry.name)
                stat_sig = zlib.crc32("\n".join(parts).encode("utf-8", "replace"))
            seen[norm(folder)] = (mtime, name_sig, stat_sig)
        return seen

    def poll(self) -> Optional[List[Tuple[str, str]]]:
        """``[(folder, "new" | "changed" | "gone")]`` since the last round; ``None`` when the
        round looks like the storage went away (most folders vanished at
        once), in which case nothing is learned from it."""
        walked = self._walk()
        self.rounds += 1
        if not self.primed:
            self.state = walked
            self.primed = True
            return []
        old = self.state
        gone = [f for f in old if f not in walked]
        if not self.truncated and old and len(gone) >= POLL_VANISH_MIN \
                and len(gone) >= POLL_VANISH_SHARE * len(old):
            logger.warning("folder watch: %d of %d folders under %s vanished at once - "
                           "treating it as the storage being away", len(gone), len(old), self.root)
            return None
        changed: List[Tuple[str, str]] = []
        for folder, (mtime, names, stats) in walked.items():
            before = old.get(folder)
            if before is None:
                changed.append((folder, "new"))
                continue
            b_mtime, b_names, b_stats = before
            if mtime != b_mtime or names != b_names:
                changed.append((folder, "changed"))
            elif stats is not None and b_stats is not None and stats != b_stats:
                changed.append((folder, "changed"))
            if stats is None and b_stats is not None:
                walked[folder] = (mtime, names, b_stats)
        if self.truncated:
            # a walk that stopped early did not see the rest: they are not gone
            for folder, value in old.items():
                walked.setdefault(folder, value)
        else:
            changed.extend((folder, "gone") for folder in gone)
        self.state = walked
        return changed


# ── live ─────────────────────────────────────────────────────────────────


def _make_handler(service: "FolderWatchService", root: str):
    from watchdog.events import FileSystemEventHandler

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: D401 - watchdog's callback
            kind = getattr(event, "event_type", "")
            if kind in ("opened", "closed_no_write"):
                return
            if kind == "modified" and event.is_directory:
                return  # the change inside it has its own event
            try:
                src = os.fsdecode(event.src_path)
                if kind == "moved":
                    service.on_move(root, src, os.fsdecode(event.dest_path), event.is_directory)
                else:
                    service.on_change(root, src, event.is_directory)
            except Exception as exc:  # noqa: BLE001 - one odd event never stops the watch
                logger.debug("folder watch: event skipped (%s): %s", kind, exc)

    return _Handler()


# ── the service ──────────────────────────────────────────────────────────


@dataclass
class RootState:
    path: str
    kind: str
    setting: str = AUTO
    mode: str = LIVE
    reason: str = ""
    note: str = ""
    reachable: bool = True
    watch: Any = None
    poller: Optional[DirectoryPoller] = None
    next_poll: float = 0.0
    last_change: Optional[float] = None
    last_check: Optional[float] = None
    changes: int = 0
    folders: int = 0
    excluded: Tuple[str, ...] = field(default_factory=tuple)


def _populated(folder: str) -> bool:
    try:
        with os.scandir(folder) as it:
            return next(iter(it), None) is not None
    except OSError:
        return False


def _reachable(folder: str) -> bool:
    """There, and not an empty mount point a share dropped out from under."""
    return os.path.isdir(folder) and _populated(folder)


def _read_mounts() -> List[Tuple[str, str]]:
    for source in ("/proc/self/mountinfo", "/proc/mounts"):
        try:
            with open(source, encoding="utf-8", errors="replace") as handle:
                mounts = parse_mounts(handle.read())
            if mounts:
                return mounts
        except OSError:
            continue
    return []


def _windows_drive_type(drive: str) -> int:
    import ctypes  # noqa: PLC0415 - Windows only
    return int(ctypes.windll.kernel32.GetDriveTypeW(drive))  # type: ignore[attr-defined]


def _inotify_watch_limit() -> Optional[int]:
    try:
        with open("/proc/sys/fs/inotify/max_user_watches", encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def count_folders(root: str, stop_at: int) -> int:
    """Folders under ``root`` (itself included), stopping at ``stop_at``."""
    count = 0
    stack = [root]
    while stack:
        folder = stack.pop()
        count += 1
        if count >= stop_at:
            return count
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False) and not ignored_dir_name(entry.name):
                            stack.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return count


def _get(config_manager: Any, key: str, default: Any) -> Any:
    try:
        value = config_manager.get(key, default) if config_manager is not None else default
    except Exception:  # noqa: BLE001
        return default
    return default if value is None else value


def configured_folders(config_manager: Any, database: Any = None) -> Tuple[List[str], Optional[str], List[str]]:
    """``(library folders, import folder, folders to leave out)`` as
    configured: the music library folder, each profile's own library folder
    (lib2_library_roots), the extra music folders, and the Import folder. The
    Import and download folders are left out of a library folder holding them."""
    from core.imports.paths import config_root_path

    library: List[str] = []

    def _add(value: Any) -> None:
        text = config_root_path(value) if value else ""
        folder = norm(text)
        if folder and folder not in library:
            library.append(folder)

    _add(_get(config_manager, "soulseek.transfer_path", "./Transfer"))
    extra = _get(config_manager, "library.music_paths", []) or []
    if isinstance(extra, (list, tuple)):
        for value in extra:
            _add(value)
    if database is not None:
        try:
            from contextlib import closing

            from core.library2.library_roots import current_roots
            with closing(database._get_connection()) as conn:
                for prefix in current_roots(conn.cursor()):
                    _add(prefix)
        except Exception as exc:  # noqa: BLE001 - the configured folders still count
            logger.debug("folder watch: library folder table unreadable: %s", exc)
    staging_raw = _get(config_manager, "import.staging_path", "./Staging")
    staging = norm(config_root_path(staging_raw)) if staging_raw else None
    downloads_raw = _get(config_manager, "soulseek.download_path", "")
    excluded = [f for f in (staging, norm(config_root_path(downloads_raw)) if downloads_raw else "") if f]
    # a library folder inside another one is watched through the outer one
    outer = [f for f in library if not any(o != f and is_under(f, o) for o in library)]
    return outer, staging, excluded


class FolderWatchService:
    """The watcher: roots, their mode, pending changes, one worker thread."""

    def __init__(self, *, config_manager: Any = None, get_database: Optional[Callable[[], Any]] = None,
                 process_library: Optional[Callable[..., Dict[str, Any]]] = None,
                 trigger_import: Optional[Callable[[], Any]] = None,
                 busy: Optional[Callable[[], bool]] = None,
                 platform: str = sys.platform,
                 mounts_reader: Callable[[], List[Tuple[str, str]]] = _read_mounts,
                 drive_type: Optional[Callable[[str], int]] = None,
                 inotify_limit: Callable[[], Optional[int]] = _inotify_watch_limit,
                 observer_factory: Optional[Callable[[], Any]] = None,
                 use_watchdog: Optional[bool] = None,
                 quiet: float = QUIET_SECONDS, stable: float = STABLE_SECONDS,
                 refresh_every: float = 30.0, tick_seconds: float = 1.0):
        self.config_manager = config_manager
        self.get_database = get_database
        self.process_library = process_library or self._default_process
        self.trigger_import = trigger_import
        self.busy = busy
        self.platform = platform
        self.mounts_reader = mounts_reader
        self.drive_type = drive_type if drive_type is not None else (
            _windows_drive_type if platform.startswith("win") else None)
        self.inotify_limit = inotify_limit
        self.observer_factory = observer_factory
        self.use_watchdog = watchdog_available() if use_watchdog is None else use_watchdog
        self.quiet = quiet
        self.stable = stable
        self.refresh_every = refresh_every
        self.tick_seconds = tick_seconds
        self.pending = PendingChanges()
        self.roots: Dict[str, RootState] = {}
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._observer: Any = None
        self._next_refresh = 0.0
        self._import_recheck_at: Optional[float] = None
        self.enabled = False
        self.last_change: Optional[float] = None
        self.last_change_folder: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FolderWatch", daemon=True)
        self._thread.start()
        logger.info("folder watch started")

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None
        self._stop_observer()
        with self._lock:
            for state in self.roots.values():
                state.watch = None
            self.roots.clear()
        logger.info("folder watch stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick(time.monotonic())
            except Exception as exc:  # noqa: BLE001 - the watcher outlives one bad round
                logger.error("folder watch round failed: %s", exc, exc_info=True)
            self._stop.wait(self.tick_seconds)

    # -- events ------------------------------------------------------------

    def _root_for(self, root: str) -> Optional[RootState]:
        with self._lock:
            return self.roots.get(root)

    def on_change(self, root: str, path: str, is_dir: bool, now: Optional[float] = None) -> bool:
        """One change from the system or a poll. Returns whether it was kept."""
        state = self._root_for(root)
        if state is None or state.mode == OFF:
            return False
        now = time.monotonic() if now is None else now
        if state.kind == IMPORT:
            if is_ignored(path, root, is_dir=is_dir):
                return False
            self.pending.add(root, norm(root), True, now)
            return True
        if is_ignored(path, root, is_dir=is_dir, excluded=state.excluded):
            return False
        if own_writes.is_own(path):
            return False
        unit = album_unit(path, root, is_dir=is_dir)
        if unit is None:
            return False
        self.pending.add(root, unit[0], unit[1], now)
        return True

    def on_move(self, root: str, src: str, dst: str, is_dir: bool,
                now: Optional[float] = None) -> bool:
        """A rename the system reported: both ends change, and the pair is
        kept so the record can follow the file."""
        kept_src = self.on_change(root, src, is_dir, now)
        kept_dst = self.on_change(root, dst, is_dir, now)
        state = self._root_for(root)
        if state is not None and state.kind == LIBRARY and kept_dst:
            self.pending.add_move(root, norm(src), norm(dst))
        return kept_src or kept_dst

    # -- the round ---------------------------------------------------------

    def tick(self, now: float) -> None:
        if now >= self._next_refresh:
            self.refresh(now)
            self._next_refresh = now + self.refresh_every
        if not self.enabled:
            return
        self._poll_due(now)
        self._flush(now)

    def _poll_due(self, now: float) -> None:
        with self._lock:
            due = [s for s in self.roots.values()
                   if s.mode == POLLING and s.poller is not None and s.reachable and now >= s.next_poll]
        for state in due:
            if self._stop.is_set():
                return
            state.next_poll = now + self._poll_seconds()
            state.last_check = time.time()
            changed = state.poller.poll()
            if changed is None:
                state.reachable = False
                state.note = "most of its folders vanished at once: treated as the storage being away"
                continue
            for folder, what in changed:
                if what == "changed":
                    self._changed_folder(state, folder, now)
                else:
                    self.on_change(state.path, folder, True, now)

    def _changed_folder(self, state: RootState, folder: str, now: float) -> bool:
        """A folder whose own listing changed (a poll): its files, not every
        album under it. A disc folder counts as its album; the root as its
        loose files."""
        if is_ignored(folder, state.path, is_dir=True, excluded=state.excluded) \
                or own_writes.is_own(folder):
            return False
        if state.kind == IMPORT:
            return self.on_change(state.path, folder, True, now)
        unit = album_unit(folder, state.path, is_dir=True)
        if unit is None:
            self.pending.add(state.path, norm(state.path), False, now)
            return True
        recursive = unit[0] != norm(folder)  # a disc folder lifted to its album
        self.pending.add(state.path, unit[0], recursive, now)
        return True

    def _poll_seconds(self) -> float:
        try:
            minutes = float(_get(self.config_manager, POLL_MINUTES_KEY, DEFAULT_POLL_MINUTES))
        except (TypeError, ValueError):
            minutes = DEFAULT_POLL_MINUTES
        return max(1.0, minutes) * 60.0

    def _flush(self, now: float) -> None:
        ready = self.pending.ready(now, self.quiet)
        for root, items in ready.items():
            if self._stop.is_set():
                return
            state = self._root_for(root)
            if state is None or state.mode == OFF:
                continue
            if state.kind == IMPORT:
                self._wake_import(now)
                continue
            if self.busy is not None and self._safe_busy():
                for folder, unit in items:
                    self.pending.requeue(root, folder, unit, now)
                continue
            settled: List[Tuple[str, bool]] = []
            for folder, unit in items:
                if self._still_growing(folder, unit, now, state.mode):
                    self.pending.requeue(root, folder, unit, now)
                else:
                    settled.append((folder, unit.recursive))
            if settled:
                self._process(state, settled)
        if self._import_recheck_at is not None and now >= self._import_recheck_at:
            self._import_recheck_at = None
            self._call_import()

    def _safe_busy(self) -> bool:
        try:
            return bool(self.busy())
        except Exception:  # noqa: BLE001
            return False

    def _still_growing(self, folder: str, unit: _Unit, now: float, mode: str) -> bool:
        """A copy still in progress: a file written within the last few
        seconds, or sizes that changed since the last look. A polled folder
        is always looked at twice (no event stream says when it went quiet)."""
        if not os.path.isdir(folder):
            return False
        snapshot, newest = folder_snapshot(folder, unit.recursive)
        previous = unit.snapshot
        unit.snapshot = snapshot
        unit.looks += 1
        if newest and time.time() - newest < self.stable:
            return True
        if previous is None:
            return mode == POLLING
        return previous != snapshot

    def _process(self, state: RootState, units: List[Tuple[str, bool]]) -> None:
        if not _reachable(state.path):
            # a vanished library folder is an outage, never a mass delete
            state.reachable = False
            state.note = "not reachable: nothing in it is marked missing until it is back"
            logger.warning("folder watch: %s is not reachable, %d change(s) set aside",
                           state.path, len(units))
            self.pending.take_moves(state.path)
            return
        moves = self.pending.take_moves(state.path)
        try:
            result = self.process_library(state.path, units, moves, excluded=state.excluded) or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("folder watch: rescan of %d folder(s) under %s failed: %s",
                         len(units), state.path, exc, exc_info=True)
            return
        if result.get("outage"):
            state.reachable = False
            state.note = "not reachable: nothing in it is marked missing until it is back"
            return
        wall = time.time()
        state.last_change = wall
        state.changes += len(units)
        self.last_change = wall
        self.last_change_folder = units[-1][0]
        self.last_result = result

    def _wake_import(self, now: float) -> None:
        self._call_import()
        # the auto-import takes a folder on its second look, once it saw it
        # unchanged: a second wake a few seconds later saves a whole interval
        self._import_recheck_at = now + max(self.quiet, 3.0)
        state = None
        with self._lock:
            for s in self.roots.values():
                if s.kind == IMPORT:
                    state = s
        if state is not None:
            state.last_change = time.time()
            state.changes += 1

    def _call_import(self) -> None:
        if self.trigger_import is None:
            return
        try:
            self.trigger_import()
        except Exception as exc:  # noqa: BLE001
            logger.debug("folder watch: import wake failed: %s", exc)

    def _default_process(self, root: str, units: List[Tuple[str, bool]],
                         moves: List[Tuple[str, str]], *, excluded=()) -> Dict[str, Any]:
        from core.library2.folder_rescan import process_changes
        database = self.get_database() if self.get_database else None
        if database is None:
            from database.music_database import get_database
            database = get_database()
        return process_changes(database, root, units, moves, excluded=excluded,
                               config_manager=self.config_manager)

    # -- roots and modes ---------------------------------------------------

    def _settings(self) -> Tuple[bool, Dict[str, str], int]:
        enabled = bool(_get(self.config_manager, ENABLED_KEY, False))
        modes = _get(self.config_manager, ROOT_MODES_KEY, {}) or {}
        if not isinstance(modes, dict):
            modes = {}
        clean = {norm(k): (v if v in MODES else AUTO) for k, v in modes.items() if k}
        try:
            cap = int(_get(self.config_manager, MAX_LIVE_KEY, DEFAULT_MAX_LIVE_FOLDERS))
        except (TypeError, ValueError):
            cap = DEFAULT_MAX_LIVE_FOLDERS
        return enabled, clean, max(1, cap)

    def _desired(self) -> List[Tuple[str, str, Tuple[str, ...]]]:
        database = None
        if self.get_database is not None:
            try:
                database = self.get_database()
            except Exception:  # noqa: BLE001
                database = None
        library, staging, excluded = configured_folders(self.config_manager, database)
        out = [(f, LIBRARY, tuple(e for e in excluded if is_under(e, f))) for f in library
               if not (staging and is_under(f, staging))]
        if staging:
            out.append((staging, IMPORT, ()))
        return out

    def refresh(self, now: Optional[float] = None) -> None:
        """Bring the roots in line with the settings and the disk: new
        folders watched, removed ones dropped, modes recomputed when a setting
        changed, a vanished folder noted and a returned one watched again."""
        with self._refresh_lock:
            self._refresh(time.monotonic() if now is None else now)

    def _refresh(self, now: float) -> None:
        enabled, modes, cap = self._settings()
        self.enabled = enabled
        desired = self._desired() if enabled else []
        wanted = {path: (kind, excluded) for path, kind, excluded in desired}
        with self._lock:
            for path in [p for p in self.roots if p not in wanted]:
                self._unwatch(self.roots.pop(path))
                self.pending.drop(path)
        mounts = None
        budget = self._live_budget(cap)
        for path, (kind, excluded) in wanted.items():
            setting = modes.get(path, AUTO)
            with self._lock:
                state = self.roots.get(path)
            reachable = _reachable(path)
            if state is not None and state.setting == setting and state.reachable == reachable \
                    and state.kind == kind and state.excluded == excluded:
                if state.mode == LIVE and state.watch is not None:
                    budget -= state.folders
                continue
            previous = state
            if previous is not None:
                self._unwatch(previous)
            if mounts is None:
                mounts = self._mounts()
            state = RootState(path=path, kind=kind, setting=setting, excluded=excluded)
            if previous is not None:
                state.last_change, state.changes = previous.last_change, previous.changes
            self._choose_mode(state, mounts, budget)
            if state.mode == LIVE:
                budget -= state.folders
            with self._lock:
                self.roots[path] = state

    def _mounts(self) -> List[Tuple[str, str]]:
        if self.platform.startswith("win"):
            return []
        try:
            return self.mounts_reader() or []
        except Exception:  # noqa: BLE001
            return []

    def _live_budget(self, cap: int) -> int:
        if not self.platform.startswith("linux"):
            return cap
        limit = None
        try:
            limit = self.inotify_limit()
        except Exception:  # noqa: BLE001
            limit = None
        return min(cap, int(limit * INOTIFY_SHARE)) if limit else cap

    def _choose_mode(self, state: RootState, mounts, budget: int) -> None:
        state.reachable = _reachable(state.path)
        auto_mode, why = classify(state.path, platform=self.platform, mounts=mounts,
                                  drive_type=self.drive_type)
        state.reason = why
        if state.setting == OFF:
            state.mode, state.note = OFF, "turned off for this folder"
            return
        mode = auto_mode if state.setting == AUTO else state.setting
        if not state.reachable:
            state.mode = mode
            state.note = ("not reachable: nothing in it is marked missing until it is back"
                          if os.path.isdir(state.path) or state.kind == LIBRARY else "")
            return
        if mode == LIVE and not self.use_watchdog:
            mode = POLLING
            state.note = "live watching needs the watchdog package; checking on a timer instead"
        if mode == LIVE and self.platform.startswith("linux"):
            folders = count_folders(state.path, budget + 1)
            state.folders = folders
            if folders > budget:
                mode = POLLING
                state.note = (f"more than {budget:,} folders, past what the system lets one program "
                              "watch live; checking on a timer instead (raise "
                              "fs.inotify.max_user_watches to watch it live)")
        if mode == LIVE and not self._watch_live(state):
            mode = POLLING
        state.mode = mode
        if mode == POLLING:
            state.poller = DirectoryPoller(state.path, excluded=state.excluded)
            state.next_poll = 0.0  # the first walk learns the layout now

    def _ensure_observer(self) -> Any:
        if self._observer is None:
            if self.observer_factory is not None:
                self._observer = self.observer_factory()
            else:
                from watchdog.observers import Observer
                self._observer = Observer()
            self._observer.daemon = True
            self._observer.start()
        return self._observer

    def _watch_live(self, state: RootState) -> bool:
        try:
            observer = self._ensure_observer()
            state.watch = observer.schedule(_make_handler(self, state.path) if self.observer_factory is None
                                            else _TestHandler(self, state.path),
                                            state.path, recursive=True)
            return True
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EMFILE):
                state.note = ("the system ran out of folder watches; checking on a timer instead "
                              "(raise fs.inotify.max_user_watches to watch it live)")
            else:
                state.note = f"live watching failed ({exc}); checking on a timer instead"
        except Exception as exc:  # noqa: BLE001
            state.note = f"live watching failed ({exc}); checking on a timer instead"
        logger.warning("folder watch: %s: %s", state.path, state.note)
        return False

    def _unwatch(self, state: RootState) -> None:
        if state.watch is not None and self._observer is not None:
            try:
                self._observer.unschedule(state.watch)
            except Exception as exc:  # noqa: BLE001 - already gone with its folder
                logger.debug("folder watch: unwatch %s: %s", state.path, exc)
        state.watch = None
        state.poller = None

    def _stop_observer(self) -> None:
        observer, self._observer = self._observer, None
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.debug("folder watch: observer stop: %s", exc)

    # -- status ------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        poll_minutes = self._poll_seconds() / 60.0
        with self._lock:
            roots = list(self.roots.values())
        items = []
        for s in roots:
            items.append({
                "path": s.path, "kind": s.kind, "setting": s.setting, "mode": s.mode,
                "reason": s.reason, "note": s.note, "reachable": s.reachable,
                "poll_minutes": poll_minutes if s.mode == POLLING else None,
                "last_change": s.last_change, "last_check": s.last_check,
                "changes": s.changes, "pending": self.pending.count(s.path),
            })
        watching = sum(1 for s in roots if s.mode != OFF and s.reachable)
        return {
            "enabled": self.enabled, "running": self.running,
            "live_available": self.use_watchdog, "watching": watching,
            "last_change": self.last_change, "last_change_folder": self.last_change_folder,
            "pending": self.pending.count(), "poll_minutes": poll_minutes, "roots": items,
        }


class _TestHandler:
    """What a fake observer is handed in tests: the same routing, no watchdog."""

    def __init__(self, service: FolderWatchService, root: str):
        self.service = service
        self.root = root

    def dispatch(self, event_type: str, src: str, is_dir: bool = False, dest: Optional[str] = None):
        if event_type == "moved":
            return self.service.on_move(self.root, src, dest or "", is_dir)
        return self.service.on_change(self.root, src, is_dir)


# ── the one service of the process ───────────────────────────────────────

_service: Optional[FolderWatchService] = None
_service_lock = threading.Lock()


def get_service() -> Optional[FolderWatchService]:
    return _service


def install(service: FolderWatchService) -> FolderWatchService:
    global _service
    with _service_lock:
        if _service is not None and _service is not service:
            _service.stop()
        _service = service
    return service


def status() -> Dict[str, Any]:
    service = _service
    if service is None:
        return {"enabled": False, "running": False, "watching": 0, "roots": [],
                "pending": 0, "last_change": None, "live_available": watchdog_available()}
    return service.status()


__all__ = [
    "DirectoryPoller", "FolderWatchService", "PendingChanges", "configured_folders",
    "count_folders", "folder_snapshot", "get_service", "install", "status",
    "watchdog_available",
]
