"""the server's log files, for the help sheet's Logs view.

every file SoulSync writes lives in one folder, the one app.log is in
(``logging.path``, or ``SOULSYNC_LOG_FILE``; ``/app/logs`` in docker):

- ``app.log`` (utils/logging_config.setup_logging, 10 MB x 3 backups)
- ``post_processing.log`` and ``source_reuse.log`` (web_server, 5 MB x 2)
- ``acoustid.log`` (core/acoustid_client, 5 MB x 2)

anything else ending in ``.log`` in that folder (old ``album_search.log`` and
``newmusic.log`` from earlier versions) is listed after them. the video side
logs through the same ``soulsync`` logger into app.log, and gunicorn logs to
stdout, so there are no other files.

a download only ever names a file from :func:`list_logs`: the client's string
is looked up in that list and never joined onto a path. files are read line by
line while they stream, so a 40 MB log never sits in memory, and every line goes
through ``utils.logging_config.redact`` unless the caller asks for raw.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from utils.logging_config import app_log_path, redact

# name -> (label, what it is)
KNOWN_LOGS = (
    ("app", "App log", "Everything SoulSync does. The one to send with a bug report."),
    ("post_processing", "Post-processing failures",
     "Downloads that couldn't be tagged, moved or imported, and why."),
    ("acoustid", "AcoustID fingerprints", "Every fingerprint check and what it matched."),
    ("source_reuse", "Source reuse", "When a download reused a source it already had."),
)
_KNOWN = {name: (label, desc) for name, label, desc in KNOWN_LOGS}

_FILE_RE = re.compile(r"^(?P<base>[A-Za-z0-9][A-Za-z0-9_.-]*\.log)(?:\.(?P<n>\d{1,3}))?$")
_CHUNK = 64 * 1024
_MAX_LINE = 1024 * 1024


def _configured_app_log() -> Optional[str]:
    try:
        from core.settings import config_manager
        return config_manager.get("logging.path", "logs/app.log")
    except Exception:  # noqa: BLE001 - no config still has a log folder
        return None


def app_log_file(configured: Optional[str] = None) -> Path:
    """where app.log is, the way web_server works it out"""
    return Path(app_log_path(configured if configured is not None else _configured_app_log()))


def log_dir(configured: Optional[str] = None) -> Path:
    return app_log_file(configured).parent


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_size(n: int) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        v = n / 1024 ** 2
        return f"{v:.0f} MB" if v >= 10 else f"{v:.1f} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def list_logs(directory: Optional[Path] = None, app_file: Optional[Path] = None) -> list:
    """[{name, label, description, bytes, last_written, files: [...]}], the
    known logs first, then any other ``*.log`` in the folder. a log's backups
    (``app.log.1`` ...) sit in its ``files`` after the current file."""
    if app_file is None:
        app_file = app_log_file()
    directory = Path(directory) if directory is not None else app_file.parent
    groups: dict = {}
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []
    for entry in entries:
        m = _FILE_RE.match(entry.name)
        if not m:
            continue
        try:
            # a symlink could point anywhere; only real files in the folder
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        base = m.group("base")
        n = int(m.group("n")) if m.group("n") else 0
        groups.setdefault(base, []).append({
            "name": entry.name,
            "bytes": int(st.st_size),
            "size": human_size(st.st_size),
            "last_written": _iso(st.st_mtime),
            "backup": n > 0,
            "_n": n,
            "_mtime": st.st_mtime,
        })

    app_base = app_file.name
    out = []
    for base, files in groups.items():
        files.sort(key=lambda f: f["_n"])
        stem = "app" if base == app_base else base[:-4]
        label, desc = _KNOWN.get(stem, (stem.replace("_", " ").capitalize() + " log",
                                        "A log file in SoulSync's log folder."))
        newest = max(f["_mtime"] for f in files)
        out.append({
            "name": stem,
            "base": base,
            "label": label,
            "description": desc,
            "bytes": sum(f["bytes"] for f in files),
            "last_written": _iso(newest),
            "files": [{k: v for k, v in f.items() if not k.startswith("_")} for f in files],
        })
    order = {name: i for i, (name, _l, _d) in enumerate(KNOWN_LOGS)}
    out.sort(key=lambda g: (order.get(g["name"], len(order)), g["name"]))
    return out


def find(name: str, logs: list) -> tuple:
    """(group, file) for a name from the list. ``name`` is either a file name
    (``app.log.2``) or a log name (``app``). (None, None) for anything else,
    so a path, ``..`` or a file the list doesn't hold never resolves."""
    name = str(name or "")
    if not name or "/" in name or "\\" in name or "\x00" in name:
        return None, None
    for group in logs:
        if group["name"] == name:
            return group, None
        for f in group["files"]:
            if f["name"] == name:
                return group, f
    return None, None


def safe_path(directory: Path, file_name: str) -> Optional[Path]:
    """the file inside the log folder, or None when it would leave it"""
    try:
        root = Path(directory).resolve()
        path = (root / file_name).resolve()
    except OSError:
        return None
    if path.parent != root or not path.is_file():
        return None
    return path


def iter_file(path: Path, *, raw: bool = False) -> Iterator[bytes]:
    """the file's bytes, a chunk at a time. without ``raw`` each line goes
    through ``redact`` on the way out. reads to the end it finds, so a file
    being written to streams what was there."""
    if raw:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    return
                yield chunk
    buf: list = []
    size = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        while True:
            line = fh.readline(_MAX_LINE)
            if not line:
                break
            out = redact(line).encode("utf-8")
            buf.append(out)
            size += len(out)
            if size >= _CHUNK:
                yield b"".join(buf)
                buf, size = [], 0
    if buf:
        yield b"".join(buf)


class _Sink(io.RawIOBase):
    """a write-only stream zipfile writes into; the generator drains it"""

    def __init__(self):
        self._buf = bytearray()
        self._pos = 0

    def writable(self):
        return True

    def write(self, b):
        self._buf += b
        self._pos += len(b)
        return len(b)

    def tell(self):
        return self._pos

    def drain(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out


def iter_zip(members: list, *, raw: bool = False, readme: str = "") -> Iterator[bytes]:
    """a zip of ``members`` ([(arcname, path)]) built while it streams.
    ``readme`` goes in first as README.txt."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if readme:
            zf.writestr("README.txt", readme)
            yield sink.drain()
        for arcname, path in members:
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                continue
            info = zipfile.ZipInfo(arcname, date_time=datetime.fromtimestamp(mtime).timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            try:
                with zf.open(info, "w", force_zip64=True) as dest:
                    for chunk in iter_file(path, raw=raw):
                        dest.write(chunk)
                        if sink.tell() and len(sink._buf) >= _CHUNK:
                            yield sink.drain()
            except OSError:
                continue
            yield sink.drain()
    yield sink.drain()


def readme_text(logs: list, *, version: str, platform_line: str, raw: bool,
                now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        f"SoulSync {version} logs",
        f"Made {now.strftime('%Y-%m-%d %H:%M UTC')} on {platform_line}",
        "Secrets: left in (raw download). Don't post these publicly." if raw
        else "Secrets: removed. API keys, tokens and passwords show as ***.",
        "",
        "What's inside:",
    ]
    for group in logs:
        lines.append(f"  {group['base']}: {group['label']}. {group['description']}")
        for f in group["files"]:
            note = " (older, rotated)" if f["backup"] else ""
            lines.append(f"    {f['name']}  {f['size']}  last written {f['last_written']}{note}")
    return "\n".join(lines) + "\n"


def zip_name(version: str, what: str = "", today: Optional[datetime] = None) -> str:
    today = today or datetime.now(timezone.utc)
    middle = f"-{what}" if what else ""
    return f"soulsync-logs{middle}-{version}-{today.strftime('%Y-%m-%d')}.zip"


def platform_line() -> str:
    import platform as _platform
    docker = os.path.exists("/.dockerenv")
    return f"{_platform.system()} {_platform.release()}{', Docker' if docker else ''}"


def tail_lines(path: Path, count: int) -> list:
    """the last ``count`` lines of a file, redacted, without reading it whole
    into memory (a deque over the lines)"""
    from collections import deque
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            last = deque((line.rstrip("\r\n") for line in iter(lambda: fh.readline(_MAX_LINE), "")),
                         maxlen=max(0, int(count)))
    except OSError:
        return []
    return [redact(line) for line in last]


def error_lines(paths: list, *, since: datetime, limit: int = 100,
                level_re: Callable = re.compile(r" - (ERROR|CRITICAL) - ").search) -> list:
    """ERROR and CRITICAL lines written after ``since``, oldest first, redacted.
    ``paths`` newest file first (app.log, app.log.1); a traceback's lines ride
    with the error above them."""
    from collections import deque
    stamp = since.strftime("%Y-%m-%d %H:%M:%S")
    out: deque = deque(maxlen=limit)
    for path in reversed(paths):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                keep = False
                for line in iter(lambda: fh.readline(_MAX_LINE), ""):
                    line = line.rstrip("\r\n")
                    head = line[:19]
                    if len(head) == 19 and head[4] == "-" and head[10] == " ":
                        keep = head >= stamp and bool(level_re(line))
                        if keep:
                            out.append(redact(line))
                    elif keep and line:
                        out.append(redact(line))
        except OSError:
            continue
    return list(out)
