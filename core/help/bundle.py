"""Report a problem: the diagnostics bundle, built as the text people read.

The preview in the help sheet is the bundle. Each part is a block of plain
text with its own heading, every part can be switched off, and the text the
UI shows, copies and downloads is exactly what :func:`build` returns (see
scrap/HELP_API.md for the join rule).

One source with Copy Debug Info: the system, services and paths parts read
``core.debug_info.collect_debug_info()`` (what /api/debug-info returns).
Secrets go through the two redactions SoulSync already has:
``config_manager.redacted_config()`` for settings (plus a key-name pass for
anything it doesn't list) and ``utils.logging_config.redact`` for every log
line, every value and finally the whole text.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import quote, urlencode

from utils.logging_config import REDACTED, redact

# id, label, hint, on by default
PARTS = (
    ("system", "Version and platform", "SoulSync, Docker, Python, ffmpeg", True),
    ("services", "Services and Health", "Connected or not, and today's findings", True),
    ("settings", "Settings, secrets removed", "Only what you changed. Keys, tokens and passwords show as ***", True),
    ("paths", "Folder paths", "Turn off if they show your name", True),
    ("logs", "Last 200 log lines", "From app.log, secrets removed", True),
    ("errors", "Recent errors only", "Errors from the last 24 hours, secrets removed", False),
)
PART_IDS = tuple(p[0] for p in PARTS)
DEFAULT_PARTS = tuple(p[0] for p in PARTS if p[3])

GITHUB_REPO = "Nezreka/SoulSync"
GITHUB_URL_CAP = 8000
DISCORD_CAP = 1900
NOTE_CAP = 4000

_providers: Dict[str, Any] = {}


def configure(**providers) -> None:
    _providers.update({k: v for k, v in providers.items() if v is not None})


def reset() -> None:
    _providers.clear()


def _p(name):
    return _providers.get(name)


# ---------------------------------------------------------------- inputs
def _debug_info(lines: int) -> dict:
    fn = _p("debug_info")
    if fn is None:
        from core.debug_info import collect_debug_info
        fn = collect_debug_info
    try:
        return fn(log_lines=lines) or {}
    except Exception:  # noqa: BLE001 - a part says it couldn't read, the bundle still builds
        return {}


def _health() -> Optional[dict]:
    fn = _p("health")
    if fn is None:
        from core.help.health import run_health
        fn = run_health
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


def _config() -> dict:
    fn = _p("redacted_config")
    if fn is None:
        from core.settings import config_manager
        fn = config_manager.redacted_config
    try:
        return fn() or {}
    except Exception:  # noqa: BLE001
        return {}


def _version() -> str:
    fn = _p("version")
    if fn is not None:
        return str(fn() or "unknown")
    try:
        from core import debug_info
        return str(debug_info.SOULSYNC_VERSION or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def _log_files() -> list:
    """[app.log, app.log.1, ...] newest first"""
    fn = _p("app_log_files")
    if fn is not None:
        return list(fn() or [])
    from core.help.logs import app_log_file
    app = app_log_file()
    return [app] + [app.with_name(f"{app.name}.{n}") for n in (1, 2, 3)]


# ---------------------------------------------------------------- settings
_SENTINEL = "__redacted_unchanged__"
_SECRET_KEY_RE = re.compile(
    r"(?i)(pass(word|wd)?$|secret|token|api_?key|apikey|cookie|session|webhook|credential|"
    r"private|auth|signature|(^|_)pin($|_)|(^|_)sig$|(^|_)key$)")


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, str):
        return redact(v)
    return str(v)


_MISSING = object()


def settings_lines(cfg: dict, defaults: Optional[dict] = None) -> List[str]:
    """``a.b.c  value`` lines, secrets as ***, empty values left out. a list of
    objects (instances, mappings) is shown as a count, never its contents.
    with ``defaults``, only what differs from them: the settings someone
    changed are the ones that explain a bug, and the full list runs to 800
    lines."""
    out: List[str] = []

    def walk(node: dict, prefix: str, base: Any):
        for key in sorted(node, key=str):
            value = node[key]
            path = f"{prefix}.{key}" if prefix else str(key)
            default = base.get(key, _MISSING) if isinstance(base, dict) else _MISSING
            if isinstance(value, dict):
                if value:
                    walk(value, path, default)
                continue
            if value in (None, "", [], ()):
                continue
            if defaults is not None and default is not _MISSING and value == default:
                continue
            if value == _SENTINEL or (_SECRET_KEY_RE.search(str(key)) and value not in (False, 0)):
                out.append(f"{path}  {REDACTED}")
                continue
            if isinstance(value, (list, tuple)):
                if all(isinstance(x, (str, int, float, bool)) for x in value) and len(value) <= 8:
                    out.append(f"{path}  {', '.join(_scalar(x) for x in value)}")
                else:
                    out.append(f"{path}  [{len(value)} items]")
                continue
            out.append(f"{path}  {_scalar(value)}")

    walk(cfg if isinstance(cfg, dict) else {}, "", defaults)
    return out


# ---------------------------------------------------------------- parts
def _kv(rows: Iterable[tuple]) -> List[str]:
    rows = [(str(k), str(v)) for k, v in rows if v not in (None, "")]
    width = max((len(k) for k, _ in rows), default=0)
    return [f"{k.ljust(width)}  {v}" for k, v in rows]


def _yes(v) -> str:
    return "connected" if v else "not connected"


def part_system(info: dict) -> List[str]:
    lib = info.get("library") or {}
    docker = info.get("docker")
    rows = [
        ("Version", info.get("version")),
        ("Platform", f"{info.get('os', 'unknown')}{', Docker' if docker else ''}"),
        ("Python", info.get("python")),
        ("Runner", info.get("runner")),
        ("ffmpeg", info.get("ffmpeg")),
        ("Uptime", info.get("uptime")),
        ("Memory", f"{info.get('memory_usage')} (system {info.get('system_memory')})" if info.get("memory_usage") else None),
        ("Database", info.get("database_size")),
        ("Library", f"{lib.get('artists', 0)} artists, {lib.get('albums', 0)} albums, {lib.get('tracks', 0)} tracks" if lib else None),
        ("Lists", f"watchlist {info.get('watchlist_count', 0)}, wishlist {info.get('wishlist_count', 0)}" if info else None),
    ]
    return _kv(rows) if info else ["(couldn't read the system info)"]


def part_services(info: dict, health: Optional[dict]) -> List[str]:
    svc = info.get("services") or {}
    lines: List[str] = []
    if svc:
        cfg = info.get("config") or {}
        mode = svc.get("download_source") or cfg.get("source_mode")
        hybrid = cfg.get("hybrid_sources") or []
        paused = [k for k, v in (info.get("enrichment_workers") or {}).items() if v == "paused"]
        rl = info.get("spotify_rate_limit") or {}
        spotify = _yes(svc.get("spotify_connected"))
        if rl.get("active"):
            spotify += f", rate limited {int(rl.get('remaining_seconds') or 0) // 60} min"
        lines += _kv([
            ("Media server", f"{svc.get('media_server_type') or 'none'}, {_yes(svc.get('media_server_connected'))}"),
            ("Metadata", f"{svc.get('music_source')}; Spotify {spotify}"),
            ("Downloads", f"{mode}{': ' + ', '.join(map(str, hybrid)) if hybrid else ''}; "
                          f"Soulseek {_yes(svc.get('soulseek_connected'))}"),
            ("Tidal", _yes(svc.get("tidal_connected"))),
            ("Qobuz", _yes(svc.get("qobuz_connected"))),
            ("HiFi", f"{svc.get('hifi_instance_count', 0)} instances"),
            ("Paused", ", ".join(paused) if paused else "none"),
            ("Failed", ", ".join(map(str, info.get("download_client_failures") or [])) or "none"),
            ("Active", f"{info.get('active_downloads', 0)} downloads, {info.get('active_syncs', 0)} syncs"),
        ])
    if health:
        c = health.get("counts") or {}
        lines.append("")
        lines.append(f"Health: {c.get('error', 0)} errors, {c.get('warning', 0)} warnings, "
                     f"{c.get('ok', 0)} fine (checked {health.get('checked_at')})")
        for f in health.get("findings") or []:
            if f.get("severity") in ("error", "warning"):
                detail = f" {f['detail']}" if f.get("detail") else ""
                lines.append(f"  [{f['severity']}] {f.get('title')}.{detail}")
    elif not svc:
        lines.append("(couldn't read the services)")
    return lines


def _defaults() -> Optional[dict]:
    fn = _p("default_config")
    if fn is None:
        try:
            from core.settings import config_manager
            fn = config_manager._get_default_config
        except Exception:  # noqa: BLE001
            return None
    try:
        return fn()
    except Exception:  # noqa: BLE001 - no defaults: show everything
        return None


def part_settings(cfg: dict) -> List[str]:
    if not cfg:
        return ["(couldn't read the settings)"]
    defaults = _defaults()
    lines = settings_lines(cfg, defaults)
    if defaults is None:
        return lines
    if not lines:
        return ["Every setting is at its default."]
    return [f"{len(lines)} changed from the defaults (the rest are default):"] + lines


def part_paths(info: dict) -> List[str]:
    paths = info.get("paths") or {}
    if not paths:
        return ["(couldn't read the folders)"]

    def state(exists, writable=None):
        if not exists:
            return "missing"
        if writable is None:
            return "found"
        return "ok" if writable else "exists, not writable"

    rows = [
        ("Download", f"{paths.get('download_path')}  {state(paths.get('download_path_exists'), paths.get('download_path_writable'))}"),
        ("Music library", f"{paths.get('transfer_folder')}  {state(paths.get('transfer_folder_exists'), paths.get('transfer_folder_writable'))}"),
    ]
    if paths.get("staging_folder"):
        rows.append(("Import", f"{paths.get('staging_folder')}  {state(paths.get('staging_folder_exists'))}"))
    for i, lib in enumerate(paths.get("music_library_paths") or []):
        rows.append((f"Library {i + 1}", f"{lib.get('path')}  {state(lib.get('exists'))}"))
    if paths.get("music_videos_path"):
        rows.append(("Music videos", f"{paths.get('music_videos_path')}  {state(paths.get('music_videos_path_exists'))}"))
    return _kv(rows)


def part_logs(lines: int) -> List[str]:
    from core.help.logs import tail_lines
    files = _log_files()
    got = tail_lines(files[0], lines) if files else []
    return got or ["(app.log is empty or couldn't be read)"]


def part_errors(now: datetime) -> List[str]:
    from core.help.logs import error_lines
    got = error_lines(_log_files(), since=(now - timedelta(hours=24)).astimezone().replace(tzinfo=None))
    return got or ["No errors in the last 24 hours."]


# ---------------------------------------------------------------- build
def _norm_parts(parts) -> List[str]:
    if parts is None:
        return list(DEFAULT_PARTS)
    if isinstance(parts, str):
        parts = [p.strip() for p in parts.split(",")]
    if isinstance(parts, dict):
        parts = [k for k, v in parts.items() if v]
    return [p for p in PART_IDS if p in set(map(str, parts))]


def filename(version: str, now: datetime) -> str:
    return f"soulsync-diagnostics-{version.split('+')[0]}-{now.strftime('%Y-%m-%d')}.txt"


def header_line(now: datetime) -> str:
    return f"SoulSync diagnostics, {now.strftime('%Y-%m-%d %H:%M')} UTC"


def note_block(note: str, where: str) -> str:
    out = "What happened\n" + note
    if where:
        out += f"\nWhere: {where}"
    return out


def join(header: str, note: str, where: str, parts: List[dict]) -> str:
    """the one rule the UI uses too: header, the note, then each included
    part, separated by a blank line"""
    text = header
    if note or where:
        text += "\n\n" + note_block(note, where)
    for part in parts:
        if part["included"]:
            text += "\n\n" + part["text"]
    return text + "\n"


def build(parts=None, note: str = "", where: str = "", lines: int = 200,
          now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    included = set(_norm_parts(parts))
    lines = max(10, min(int(lines or 200), 500))
    note = redact(str(note or "").strip()[:NOTE_CAP])
    where = redact(str(where or "").strip()[:200])

    info = _debug_info(20)
    health = _health()
    version = _version()
    if version == "unknown":
        version = str(info.get("version") or "unknown")

    builders: Dict[str, Callable[[], List[str]]] = {
        "system": lambda: part_system(info),
        "services": lambda: part_services(info, health),
        "settings": lambda: part_settings(_config()),
        "paths": lambda: part_paths(info),
        "logs": lambda: part_logs(lines),
        "errors": lambda: part_errors(now),
    }
    out_parts = []
    for pid, label, hint, default in PARTS:
        title = f"Last {lines} log lines" if pid == "logs" else label
        try:
            body = builders[pid]()
        except Exception as exc:  # noqa: BLE001 - one part never sinks the bundle
            body = [f"(couldn't build this part: {type(exc).__name__})"]
        text = redact(f"== {title} ==\n" + "\n".join(body))
        out_parts.append({"id": pid, "label": title, "hint": hint, "default": default,
                          "included": pid in included, "text": text,
                          "bytes": len(text.encode("utf-8"))})

    header = header_line(now)
    text = join(header, note, where, out_parts)
    platform = f"{info.get('os', 'unknown')}{', Docker' if info.get('docker') else ''}, Python {info.get('python', '?')}"
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": version,
        "filename": filename(version, now),
        "parts": out_parts,
        "text": text,
        "bytes": len(text.encode("utf-8")),
        "discord": discord_text(version, platform, note, where, health, info),
        "github": github_issue(version, platform, note, where, health),
    }


# ---------------------------------------------------------------- sharing
def _problems(health: Optional[dict], limit: int) -> List[str]:
    out = []
    for f in (health or {}).get("findings") or []:
        if f.get("severity") in ("error", "warning"):
            out.append(f"{f['severity']}: {f.get('title')}")
        if len(out) >= limit:
            break
    return out


def health_line(health: Optional[dict]) -> str:
    if not health:
        return "Health: not checked"
    c = health.get("counts") or {}
    return f"Health: {c.get('error', 0)} errors, {c.get('warning', 0)} warnings"


def discord_text(version: str, platform: str, note: str, where: str,
                 health: Optional[dict], info: dict) -> str:
    """one Discord message: version, platform, the note, health, services"""
    def make(note_cap: int) -> str:
        lines = [f"**SoulSync {version}** on {platform}"]
        if note:
            short = note if len(note) <= note_cap else note[:note_cap].rstrip() + "..."
            lines += ["> " + ln for ln in short.splitlines() if ln.strip()]
        if where:
            lines.append(f"Where: {where}")
        lines.append(health_line(health))
        lines += [f"- {p}" for p in _problems(health, 6)]
        svc = info.get("services") or {}
        if svc:
            lines.append(f"Media server: {svc.get('media_server_type') or 'none'} "
                         f"({_yes(svc.get('media_server_connected'))}); "
                         f"downloads: {svc.get('download_source')}; metadata: {svc.get('music_source')}")
        lines.append("Full diagnostics in the attached file.")
        return redact("\n".join(lines))

    cap = 800
    text = make(cap)
    while len(text) > DISCORD_CAP and cap > 50:
        cap //= 2
        text = make(cap)
    return text[:DISCORD_CAP]


def github_issue(version: str, platform: str, note: str, where: str,
                 health: Optional[dict], repo: Optional[str] = None) -> dict:
    """a prefilled issue on the bug_report.yml form, under GitHub's ~8 KB URL
    cap. the note is trimmed to fit; the full bundle goes as a file."""
    repo = repo or str(_p("github_repo") or GITHUB_REPO)
    base = f"https://github.com/{repo}/issues/new?"
    first = (note.splitlines()[0] if note else "").strip()
    title = (first[:80] + ("..." if len(first) > 80 else "")) if first else "Something isn't working"
    diag = "\n".join([health_line(health)] + _problems(health, 8)
                     + ["", "(The full diagnostics file is attached below.)"])

    def url_for(body: str) -> str:
        q = {
            "template": "bug_report.yml",
            "title": title,
            "what-happened": body,
            "where": where,
            "version": version,
            "platform": platform,
            "diagnostics": diag,
        }
        return base + urlencode({k: v for k, v in q.items() if v}, quote_via=quote)

    body = note
    url = url_for(body)
    trimmed = False
    while len(url) > GITHUB_URL_CAP and body:
        body = body[: max(0, int(len(body) * 0.8) - 20)].rstrip()
        url = url_for(body + "\n\n(trimmed, the rest is in the attached file)" if body else "")
        trimmed = True
    return {"url": url, "length": len(url), "trimmed": trimmed}
