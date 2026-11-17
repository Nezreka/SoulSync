"""Health for the help sheet: one list of what needs the admin, in plain words.

Nothing here invents a new way to detect a problem. Each check asks the thing
SoulSync already uses (the media server probe /status runs, the onboarding
path checks with their docker hints, the acquisition path mapping health, the
video health strip, the enrichment registry, the review queue counts, the
automations' last errors, the setup checklist, the update check), plus one new
check: free disk on the music folders, through the video disk guard.

Every check runs on its own daemon thread with its own timeout, so a media
server that never answers costs at most a few seconds and turns into one
"couldn't check" line, never a hung request or a missing list. A check whose
previous run is still stuck is not started again; the new run waits on the old
one. Results are cached for 30 seconds.

A finding: {id, severity, area, title, detail, fix?, docs?}; see
scrap/HELP_API.md for the shapes the sheet reads.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("help.health")

SEVERITIES = ("error", "warning", "info", "ok")
_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
CACHE_SECONDS = 30.0
STATUS_STALE_SECONDS = 300.0
LOW_DISK_GB = 2.0
CRITICAL_DISK_GB = 1.0

# ---------------------------------------------------------------- providers
# Set by api/help.configure() from web_server; tests set fakes. Every value is a
# zero-argument callable unless named otherwise. A missing provider skips the
# check that needs it rather than guessing.
_providers: Dict[str, Any] = {}


def configure(**providers) -> None:
    _providers.update({k: v for k, v in providers.items() if v is not None})


def reset() -> None:
    """forget providers, cache and stuck probes (tests)"""
    _providers.clear()
    with _state_lock:
        _cache.clear()
        _inflight.clear()


def _p(name):
    return _providers.get(name)


class _ConfigShim:
    """config_manager's get, for helpers that take the manager"""

    def get(self, key, default=None):
        return _config_get(key, default)


def _config_get(key, default=None):
    get = _p("config_get")
    if get is None:
        from core.settings import config_manager
        get = config_manager.get
    try:
        value = get(key, default)
    except Exception:  # noqa: BLE001 - an unreadable key reads as unset
        return default
    return default if value is None else value


# ---------------------------------------------------------------- shapes
def finding(fid: str, severity: str, area: str, title: str, detail: str = "",
            fix: Optional[list] = None, docs: Optional[str] = None) -> dict:
    out = {"id": fid, "severity": severity, "area": area, "title": title,
           "detail": _plain(detail)}
    if fix:
        out["fix"] = fix
    if docs:
        out["docs"] = docs
    return out


def _plain(text: str) -> str:
    """no em or en dashes in anything the sheet shows"""
    text = str(text or "")
    for dash in (" — ", " – ", "—", "–"):
        text = text.replace(dash, ", ")
    return text.replace(" - ", ", ").strip()


def fix_settings(label: str, tab: str, media: Optional[str] = None) -> dict:
    out = {"label": label, "kind": "settings", "tab": tab}
    if media:
        out["media"] = media
    return out


def fix_page(label: str, path: str, media: Optional[str] = None) -> dict:
    out = {"label": label, "kind": "page", "path": path}
    if media:
        out["media"] = media
    return out


def fix_post(label: str, url: str, body: Optional[dict] = None, recheck: bool = True) -> dict:
    out = {"label": label, "kind": "post", "url": url, "recheck": recheck}
    if body:
        out["body"] = body
    return out


def fix_external(label: str, href: str) -> dict:
    return {"label": label, "kind": "external", "href": href}


def _ms(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms"


def _gb(v: float) -> str:
    return f"{v / 1024:.1f} TB" if v >= 1024 else f"{v:.1f} GB"


_NUMBERS = ("No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten")


def _count_word(n: int) -> str:
    return _NUMBERS[n] if 0 <= n < len(_NUMBERS) else str(n)


def summary_sentence(counts: dict) -> str:
    """'Two things need you. Four are worth a look. Everything else, 14 checks, is running.'"""
    e, w, ok = counts.get("error", 0), counts.get("warning", 0), counts.get("ok", 0)
    parts = []
    if e:
        parts.append(f"{_count_word(e)} {'thing needs' if e == 1 else 'things need'} you.")
    if w:
        lead = _count_word(w) if parts else f"Nothing needs you. {_count_word(w)}"
        parts.append(f"{lead} {'is' if w == 1 else 'are'} worth a look.")
    if not parts:
        return f"Everything is running. {ok} {'check' if ok == 1 else 'checks'} passed."
    if ok:
        parts.append(f"Everything else, {ok} {'check' if ok == 1 else 'checks'}, is running.")
    return " ".join(parts)


# ---------------------------------------------------------------- services
def check_media_server() -> List[dict]:
    probe = _p("media_server_probe")
    active = str(_config_get("active_media_server", "plex") or "plex")
    try:
        from core.media_server import specs
        desc = specs.describe(active)
    except Exception:  # noqa: BLE001
        desc = None
    name = desc.display_name if desc else active.title()
    fixes = [fix_post("Test again", "/api/test-connection", {"service": "server"}),
             fix_settings("Open Connections", "connections")]
    if desc is not None and desc.is_external:
        try:
            cfg = desc.config_from(_ConfigShim())
        except Exception:  # noqa: BLE001
            cfg = {}
        if not desc.is_configured(cfg):
            return [finding("services.media_server", "info", "services",
                            f"{name} isn't set up yet",
                            "Add its address and sign in so SoulSync can scan your library and send it new music.",
                            [fix_settings("Open Connections", "connections")], "gs-connecting")]
    if probe is None:
        return []
    started = time.monotonic()
    connected = bool(probe())
    took = time.monotonic() - started
    url = ""
    if desc is not None and desc.config_section:
        url = str(_config_get(f"{desc.config_section}.base_url", "") or "")
    if connected:
        return [finding("services.media_server", "ok", "services", f"{name} connected", _ms(took))]
    where = f"{url} didn't answer. " if url else "It didn't answer. "
    return [finding("services.media_server", "error", "services", f"{name} isn't answering",
                    where + f"Library scans and updates to {name} wait until it's back.",
                    fixes, "gs-connecting")]


def check_metadata() -> List[dict]:
    out = []
    source = None
    try:
        from core.metadata.registry import get_primary_source
        source = get_primary_source()
    except Exception:  # noqa: BLE001
        source = None
    status_fn = _p("spotify_status")
    if status_fn is None:
        from core.metadata.status import get_spotify_status
        status_fn = get_spotify_status
    status = status_fn() or {}
    if status.get("rate_limited"):
        rl = status.get("rate_limit") or {}
        secs = int(rl.get("remaining_seconds") or 0)
        mins = max(1, round(secs / 60)) if secs else 0
        when = f"For about {mins} more minute{'s' if mins != 1 else ''}. " if mins else ""
        out.append(finding("services.spotify_rate_limit", "warning", "services",
                           "Spotify is rate limited",
                           when + "It resumes on its own, nothing to do. Other metadata sources keep working.",
                           docs="ts-common"))
    if source:
        label = {"itunes": "iTunes", "musicbrainz": "MusicBrainz", "jiosaavn": "JioSaavn"}.get(
            str(source), str(source).title())
        out.append(finding("services.metadata", "ok", "services", f"{label} metadata"))
    return out


_SOURCE_NAMES = {
    "soulseek": "Soulseek (slskd)", "hifi": "HiFi", "youtube": "YouTube", "tidal": "Tidal",
    "qobuz": "Qobuz", "deezer": "Deezer", "deezer_dl": "Deezer", "torrent": "Torrent client",
    "usenet": "Usenet client", "lidarr": "Lidarr", "soundcloud": "SoundCloud", "amazon": "Amazon Music",
}


def download_sources() -> List[str]:
    """the sources downloads use, in order: the one mode, or the hybrid chain"""
    fn = _p("download_sources")
    if fn is not None:
        return list(fn() or [])
    mode = str(_config_get("download_source.mode", "hybrid") or "hybrid")
    if mode != "hybrid":
        return [mode]
    order = _config_get("download_source.hybrid_order", ["hifi", "youtube", "soulseek"]) or []
    return [order] if isinstance(order, str) else list(order)


def check_download_source(source: str, *, only: bool) -> List[dict]:
    """one source of the chain. ``only``: nothing else to fall back to, so
    down is an error rather than a warning"""
    probe = _p("download_probe")
    if probe is None:
        return []
    name = _SOURCE_NAMES.get(source, source.replace("_", " ").title())
    started = time.monotonic()
    state = probe(source)   # True, False, or "unconfigured" / "missing"
    took = time.monotonic() - started
    fid = f"services.download.{source}"
    tab = fix_settings("Open Downloads settings", "downloads")
    if state is True:
        return [finding(fid, "ok", "services", f"{name} connected", _ms(took))]
    if state == "unconfigured":
        return [finding(fid, "warning" if not only else "error", "services",
                        f"{name} isn't set up",
                        "It's in your download sources but has no address or sign-in yet, so it's skipped.",
                        [tab], "set-download")]
    if state == "missing":
        return []   # reported once by check_download_clients
    sev = "error" if only else "warning"
    then = ("Nothing downloads until it's back." if only
            else "Downloads use your other sources until it's back.")
    test = "soulseek" if source == "soulseek" else source
    return [finding(fid, sev, "services", f"{name} isn't answering", f"It didn't answer the connection test. {then}",
                    [fix_post("Test again", "/api/test-connection", {"service": test}), tab], "set-download")]


def check_download_clients() -> List[dict]:
    fn = _p("download_failures")
    if fn is None:
        return []
    failures = [str(f) for f in (fn() or [])]
    if not failures:
        return []
    if any(f.startswith("ALL") for f in failures):
        return [finding("services.download_clients", "error", "services", "Downloads couldn't start",
                        "The download system failed when SoulSync started, so nothing can download. "
                        "The app log says why; restarting SoulSync tries again.",
                        [{"label": "Open the logs", "kind": "logs"}], "ts-logs")]
    names = ", ".join(failures[:5])
    return [finding("services.download_clients", "warning", "services",
                    f"{len(failures)} download {'client' if len(failures) == 1 else 'clients'} didn't start",
                    f"{names} failed when SoulSync started and are skipped. The app log says why.",
                    [{"label": "Open the logs", "kind": "logs"}], "ts-logs")]


# ---------------------------------------------------------------- paths + disk
def _resolve(path: str) -> str:
    try:
        from core.imports.paths import docker_resolve_path
        return docker_resolve_path(path)
    except Exception:  # noqa: BLE001
        return os.path.abspath(os.path.expanduser(path)) if path else path


def music_folders() -> List[tuple]:
    """[(key, label, configured path, settings tab)] for the folders the music
    side reads and writes"""
    rows = [
        ("download", "Download folder", str(_config_get("soulseek.download_path", "./downloads") or ""), "downloads"),
        ("transfer", "Transfer folder", str(_config_get("soulseek.transfer_path", "./Transfer") or ""), "downloads"),
    ]
    staging = str(_config_get("import.staging_path", "") or "")
    if staging:
        rows.append(("staging", "Import folder", staging, "downloads"))
    paths = _config_get("library.music_paths", []) or []
    if isinstance(paths, list):
        for i, p in enumerate(x for x in paths if isinstance(x, str) and x.strip()):
            rows.append((f"music_library_{i}", "Music library folder", p, "library"))
    mv = str(_config_get("library.music_videos_path", "") or "")
    if mv:
        rows.append(("music_videos", "Music videos folder", mv, "library"))
    return rows


def _is_docker() -> bool:
    fn = _p("is_docker")
    if fn is not None:
        return bool(fn())
    from core.onboarding.environment import is_docker
    return is_docker()


def _path_title(label: str, message: str) -> str:
    m = message.lower()
    if "can't write" in m:
        return f"{label} isn't writable"
    if "doesn't exist" in m and "can't create" in m:
        return f"{label} is missing"
    if "a file, not a folder" in m:
        return f"{label} is a file, not a folder"
    if "same folder" in m:
        return f"{label} is the same folder as another"
    if "windows path" in m:
        return f"{label} is a Windows path"
    return f"{label} has a problem"


def check_paths() -> List[dict]:
    from core.onboarding.paths import check_paths as onboarding_check_paths
    docker = _is_docker()
    rows = music_folders()
    labels = {key: label for key, label, _p_, _t in rows}
    tabs = {key: tab for key, _l, _p_, tab in rows}
    # download, transfer and import are checked together, so two of them on
    # one folder, or one inside another, is caught. library folders usually ARE
    # the transfer folder, so they are checked one at a time.
    grouped = {key: _resolve(path) for key, _l, path, _t in rows if key in ("download", "transfer", "staging")}
    kwargs = {}
    if _p("mounts") is not None:
        kwargs["mounts"] = _p("mounts")()
    if _p("writable") is not None:
        kwargs["writable"] = _p("writable")
    results = onboarding_check_paths(grouped, docker=docker, **kwargs) if grouped else {}
    for key, _l, path, _t in rows:
        if key not in grouped:
            results.update(onboarding_check_paths({key: _resolve(path)}, docker=docker, **kwargs))

    out = []
    docs = "gs-docker" if docker else "gs-folders"
    for key, _l, _path, _t in rows:
        res = results.get(key) or {}
        label = labels[key]
        shown = res.get("path") or _path
        fix = [fix_settings(f"Open {label.lower()}", tabs[key])]
        fid = f"paths.{key}"
        if res.get("state") == "problem":
            # the onboarding messages already carry the docker hints (PUID and
            # PGID, "is the volume mapped in your compose file?")
            message = (res.get("message") or "SoulSync can't use it").rstrip(".")
            detail = f"{shown}: {message}."
            out.append(finding(fid, "error", "paths", _path_title(label, res.get("message") or ""),
                               detail, fix, docs))
            continue
        if res.get("unmapped"):
            out.append(finding(fid + ".unmapped", "warning", "paths", f"{label} isn't on a mapped volume",
                               f"{shown} lives inside the container, so its files are lost when the "
                               "container is rebuilt. Map it in your compose file.", fix, "gs-docker"))
        inside = res.get("inside")
        if inside:
            out.append(finding(fid + ".inside", "warning", "paths",
                               f"{label} is inside the {labels.get(inside, inside).lower()}",
                               "Half-finished downloads can get scanned or imported into themselves. "
                               "Give each its own folder.", fix, "gs-folders"))
        if res.get("state") == "create":
            out.append(finding(fid, "info", "paths", f"{label} doesn't exist yet",
                               f"{shown} is made the first time SoulSync uses it.", fix, "gs-folders"))
        elif res.get("state") == "ok":
            out.append(finding(fid, "ok", "paths", f"{label} writable"))
    return out


def check_path_mapping() -> List[dict]:
    from core.acquisition.path_health import inspect_mapping_configuration, inspect_reported_path
    health = inspect_mapping_configuration(_config_get).to_public_dict()
    fix = [fix_settings("Open path mapping", "downloads")]
    out = []
    if health["configured_count"]:
        if health["invalid_indexes"]:
            n = len(health["invalid_indexes"])
            out.append(finding("paths.mapping", "error", "paths",
                               f"{n} path {'mapping is' if n == 1 else 'mappings are'} incomplete",
                               "A mapping needs both the client's folder and SoulSync's folder. "
                               "Downloads from that client can't be found until it's fixed.", fix, "set-download"))
        elif health["valid_count"] != health["readable_target_count"]:
            n = health["valid_count"] - health["readable_target_count"]
            out.append(finding("paths.mapping", "error", "paths",
                               f"{n} path {'mapping points' if n == 1 else 'mappings point'} at a missing folder",
                               "SoulSync can't read the folder a mapping sends downloads to. "
                               "Check the SoulSync side of the mapping, or the volume in Docker.", fix, "set-download"))
        else:
            out.append(finding("paths.mapping", "ok", "paths", "Path mapping"))
    imports_fn = _p("open_import_paths")
    if imports_fn is not None:
        bad = 0
        for reported in imports_fn() or []:
            if inspect_reported_path(reported, config_get=_config_get).status in ("unreadable", "mapping_unavailable"):
                bad += 1
        if bad:
            out.append(finding("paths.mapping.imports", "warning", "paths",
                               f"{bad} {'download' if bad == 1 else 'downloads'} can't be found to import",
                               "The download client says they're done, but the folder it reports isn't one "
                               "SoulSync can read. A path mapping usually fixes this.", fix, "set-download"))
    return out


def _video_settings() -> dict:
    fn = _p("video_settings")
    if fn is None:
        return {}
    try:
        return fn() or {}
    except Exception:  # noqa: BLE001 - no video db, no floor
        return {}


def check_disk() -> List[dict]:
    from core.video.disk_guard import free_gb
    try:
        floor = float(_video_settings().get("min_free_disk_gb") or 0)
    except (TypeError, ValueError):
        floor = 0.0
    seen = {}
    out = []
    for key, label, path, tab in music_folders():
        resolved = _resolve(path)
        if not resolved or not os.path.isdir(resolved):
            continue   # check_paths reports missing folders
        try:
            dev = os.stat(resolved).st_dev
        except OSError:
            continue
        if dev in seen:
            continue   # one line per drive
        seen[dev] = key
        free = free_gb(resolved)
        if free is None:
            continue
        fid = f"disk.{key}"
        fix = [fix_settings(f"Open {label.lower()}", tab)]
        drive = label.replace(" folder", "")
        if free < CRITICAL_DISK_GB:
            out.append(finding(fid, "error", "disk", f"{drive} drive is full",
                               f"{_gb(free)} free on the drive holding {resolved}. Downloads and imports "
                               "fail until there's room.", fix, "gs-folders"))
        elif floor and free < floor:
            out.append(finding(fid, "warning", "disk", f"{drive} drive is nearly full",
                               f"{_gb(free)} free, under your {floor:.0f} GB minimum.", fix, "gs-folders"))
        elif free < LOW_DISK_GB:
            out.append(finding(fid, "warning", "disk", f"{drive} drive is nearly full",
                               f"{_gb(free)} free on the drive holding {resolved}.", fix, "gs-folders"))
        else:
            out.append(finding(fid, "ok", "disk", f"{drive} drive, {_gb(free)} free"))
    return out


# ---------------------------------------------------------------- video
_VIDEO_ROOT_KEYS = ("movies_path", "tv_path", "youtube_path")


def _video_fix(cid: str) -> list:
    if cid.endswith("_space") or cid.endswith("_path") or cid.startswith(("library_", "movies", "tv")):
        return [fix_settings("Open Library folders", "library", "video")]
    if cid == "recycle_path":
        return [fix_settings("Open Library settings", "library", "video")]
    if cid == "repair_errors":
        return [fix_page("Open Tools", "/tools", "video")]
    if cid == "monitor":
        return [fix_page("Open Downloads", "/active-downloads", "video")]
    if cid == "import_lists":
        return [fix_page("Open Automations", "/automations", "video")]
    if cid.startswith(("source_", "indexers")):
        return [fix_settings("Open Downloads settings", "downloads", "video")]
    if cid == "youtube_health":
        return [fix_settings("Open YouTube settings", "downloads", "video")]
    return []


def _video_title(check: dict) -> str:
    cid, label, status = check.get("id", ""), check.get("label", ""), check.get("status")
    if status == "ok":
        return label
    if cid.endswith("_space"):
        return f"{label} drive is nearly full"
    detail = str(check.get("detail") or "")
    if "unreachable" in detail:
        return f"{label} folder is unreachable"
    return f"{label} needs a look"


def check_video() -> List[dict]:
    get_db = _p("video_db")
    if get_db is None:
        return []
    db = get_db()
    if not any(str(db.get_setting(k) or "").strip() for k in _VIDEO_ROOT_KEYS):
        return []   # the video side isn't in use
    from core.video.health import collect
    data = collect(db) or {}
    out = []
    for check in data.get("checks") or []:
        status = check.get("status")
        sev = {"error": "error", "warning": "warning"}.get(status, "ok")
        cid = str(check.get("id") or "check")
        out.append(finding(f"video.{cid}", sev, "video", _video_title(check),
                           str(check.get("detail") or ""),
                           _video_fix(cid) if sev != "ok" else None,
                           "vdl-queue" if cid.startswith(("source_", "indexers", "monitor")) and sev != "ok" else None))
    return out


# ---------------------------------------------------------------- workers
def check_workers() -> List[dict]:
    services_fn = _p("enrichment_services")
    if services_fn is None:
        from core.enrichment.services import all_services
        services_fn = all_services
    out = []
    running = 0
    for svc in services_fn() or []:
        worker = svc.get_worker()
        if worker is None:
            continue
        name = getattr(svc, "display_name", svc.id)
        key = getattr(svc, "config_paused_key", "") or ""
        paused = bool(getattr(worker, "paused", False))
        if paused and key and _config_get(key, False):
            out.append(finding(f"workers.{svc.id}", "warning", "workers", f"{name} enrichment is paused",
                               "Someone paused it. New albums wait for its genres, links and details until it runs.",
                               [fix_post("Resume", f"/api/enrichment/{svc.id}/resume")], "set-services"))
            continue
        thread = getattr(worker, "thread", None)
        if thread is not None and not thread.is_alive() and not paused and getattr(worker, "running", True):
            out.append(finding(f"workers.{svc.id}", "warning", "workers", f"{name} enrichment stopped",
                               "Its worker isn't running. Restarting SoulSync starts it again; the app log says why it stopped.",
                               [{"label": "Open the logs", "kind": "logs"}], "ts-logs"))
            continue
        running += 1
    if running:
        out.append(finding("workers.running", "ok", "workers",
                           f"{running} enrichment {'worker' if running == 1 else 'workers'} running"))
    return out


# ---------------------------------------------------------------- downloads
def check_review_queue() -> List[dict]:
    fn = _p("review_counts")
    if fn is None:
        return []
    counts = fn() or {}
    q, u = int(counts.get("quarantine") or 0), int(counts.get("unverified") or 0)
    total = q + u
    if not total:
        return [finding("downloads.review", "ok", "downloads", "Nothing waiting for review")]
    bits = []
    if q:
        bits.append(f"{q} in quarantine")
    if u:
        bits.append(f"{u} imported but not verified")
    return [finding("downloads.review", "warning", "downloads",
                    f"{total} {'download is' if total == 1 else 'downloads are'} waiting for you",
                    f"They arrived but didn't match well enough to file on their own ({', '.join(bits)}).",
                    [fix_page("Open review", "/active-downloads")], "wf-download")]


def check_automations() -> List[dict]:
    fn = _p("automations")
    if fn is None:
        return []
    failed = [a for a in (fn() or []) if a.get("enabled") and a.get("last_error")]
    if not failed:
        return [finding("automations.failed", "ok", "automations", "Automations, 0 failed")]
    names = ", ".join(str(a.get("name") or f"#{a.get('id')}") for a in failed[:4])
    more = f" and {len(failed) - 4} more" if len(failed) > 4 else ""
    first = str(failed[0].get("last_error") or "")[:200]
    return [finding("automations.failed", "warning", "automations",
                    f"{len(failed)} {'automation' if len(failed) == 1 else 'automations'} failed last time",
                    f"{names}{more}. Last error: {first}",
                    [fix_page("Open Automations", "/automations")], "wf-auto")]


# ---------------------------------------------------------------- setup + updates
def check_setup() -> List[dict]:
    fn = _p("checklist")
    if fn is None:
        return []
    data = fn() or {}
    if data.get("dismissed"):
        return []
    out = []
    for item in data.get("items") or []:
        if item.get("done"):
            continue
        fix = {"label": item.get("action") or "Set up", "kind": "checklist", "item": item.get("id")}
        if item.get("href"):
            fix["href"] = item["href"]
        out.append(finding(f"setup.{item.get('id')}", "info", "setup", str(item.get("title") or ""),
                           str(item.get("description") or ""), [fix]))
    return out


def check_updates() -> List[dict]:
    fn = _p("update_info")
    if fn is None:
        return []
    info = fn() or {}
    current = info.get("current_version") or ""
    if info.get("update_available"):
        latest = info.get("latest_version")
        sev = "warning" if str(info.get("severity") or "") in ("critical", "major") else "info"
        title = f"SoulSync {latest} is out" if latest else "A SoulSync update is out"
        how = "Pull the new image and recreate the container." if info.get("is_docker") else "Update when it suits you."
        crit = " It fixes something serious." if info.get("severity") == "critical" else ""
        fix = [fix_external("Release notes", info["release_url"])] if info.get("release_url") else None
        return [finding("updates.version", sev, "updates", title, f"You're on {current}.{crit} {how}", fix)]
    return [finding("updates.version", "ok", "updates", f"{current} is the latest" if current else "Up to date")]


# ---------------------------------------------------------------- runner
# (id, label for "couldn't check", fn, timeout seconds)
def _plan() -> List[tuple]:
    plan = [
        ("media_server", "the media server", check_media_server, 5.0),
        ("metadata", "metadata sources", check_metadata, 3.0),
        ("download_clients", "download clients", check_download_clients, 2.0),
        ("paths", "folders", check_paths, 4.0),
        ("path_mapping", "path mapping", check_path_mapping, 4.0),
        ("disk", "free disk space", check_disk, 4.0),
        ("video", "the video side", check_video, 5.0),
        ("workers", "enrichment workers", check_workers, 2.0),
        ("review", "the review queue", check_review_queue, 4.0),
        ("automations", "automations", check_automations, 3.0),
        ("setup", "setup", check_setup, 5.0),
        ("updates", "for updates", check_updates, 5.0),
    ]
    if _p("download_probe") is not None:
        try:
            sources = [s for s in download_sources() if s]
        except Exception:  # noqa: BLE001
            sources = []
        only = len(sources) == 1
        for s in sources:
            name = _SOURCE_NAMES.get(s, s)
            plan.append((f"download.{s}", name, (lambda s=s: check_download_source(s, only=only)), 5.0))
    return plan


_state_lock = threading.Lock()
_run_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_inflight: Dict[str, Future] = {}


def _start(cid: str, fn: Callable) -> Future:
    """one daemon thread per check; a still-running probe from an earlier run
    is reused instead of piling a second one onto a dead server"""
    with _state_lock:
        fut = _inflight.get(cid)
        if fut is not None and not fut.done():
            return fut
        fut = Future()
        _inflight[cid] = fut

    def run():
        try:
            fut.set_result(fn())
        except BaseException as exc:  # noqa: BLE001 - reported as its own finding
            fut.set_exception(exc)

    threading.Thread(target=run, name=f"help-health-{cid}", daemon=True).start()
    return fut


def _couldnt(cid: str, label: str, why: str) -> dict:
    return finding(f"checks.{cid}", "warning", "checks", f"Couldn't check {label}", why,
                   [{"label": "Check again", "kind": "recheck"}])


def collect(plan: Optional[List[tuple]] = None) -> dict:
    """run every check now (no cache) and build the response"""
    plan = plan if plan is not None else _plan()
    started = time.monotonic()
    futures = [(cid, label, _start(cid, fn), timeout) for cid, label, fn, timeout in plan]
    findings: List[dict] = []
    for cid, label, fut, timeout in futures:
        left = max(0.0, started + timeout - time.monotonic())
        try:
            result = fut.result(timeout=left)
        except FutureTimeout:
            findings.append(_couldnt(cid, label, f"It didn't answer in {timeout:.0f} seconds. "
                                                 "Something it talks to may be down or slow."))
            continue
        except Exception as exc:  # noqa: BLE001
            logger.debug("health check %s failed", cid, exc_info=True)
            findings.append(_couldnt(cid, label, f"The check itself failed ({type(exc).__name__}). "
                                                 "The app log has the details."))
            continue
        for f in result or []:
            if isinstance(f, dict) and f.get("severity") in _ORDER:
                findings.append(f)
    findings.sort(key=lambda f: (_ORDER[f["severity"]], f["area"], f["id"]))
    counts = {s: 0 for s in SEVERITIES}
    for f in findings:
        counts[f["severity"]] += 1
    status = "error" if counts["error"] else ("warning" if counts["warning"] else "ok")
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_seconds": 0,
        "took_ms": int((time.monotonic() - started) * 1000),
        "counts": counts,
        "summary": summary_sentence(counts),
        "findings": findings,
    }


def run_health(fresh: bool = False) -> dict:
    """the cached result when it's under 30 s old, else a new run. requests
    that arrive during a run wait for it and share its result, a "check
    again" included (a result under a second old counts as fresh)."""
    with _run_lock:
        now = time.monotonic()
        cached = _cache.get("result")
        if cached and now - _cache["at"] < (1.0 if fresh else CACHE_SECONDS):
            return {**cached, "age_seconds": int(now - _cache["at"])}
        result = collect()
        _cache["result"] = result
        _cache["at"] = time.monotonic()
        return result


_bg_running = threading.Event()


def status_snapshot() -> dict:
    """the dot's poll: the last result's error count, never a run on this
    thread. a stale or missing result starts one in the background."""
    cached = _cache.get("result")
    age = time.monotonic() - _cache.get("at", 0) if cached else None
    if (cached is None or age > STATUS_STALE_SECONDS) and not _bg_running.is_set():
        _bg_running.set()

        def bg():
            try:
                run_health(fresh=True)
            except Exception:  # noqa: BLE001
                logger.debug("background health run failed", exc_info=True)
            finally:
                _bg_running.clear()

        threading.Thread(target=bg, name="help-health-bg", daemon=True).start()
    if cached is None:
        return {"errors": 0, "checked_at": None, "refreshing": True}
    return {"errors": int(cached["counts"]["error"]), "checked_at": cached["checked_at"],
            "refreshing": _bg_running.is_set()}
