"""Kometa logo-pack installer — source brand art into the drop-in logo folders.

SoulSync ships no logo art (trademarks). This module knows where Kometa keeps
its public overlay images and, on the user's request, copies the relevant ones
into ``<data>/video_poster_assets/logos/<field>/<ourname>.png`` — the same
drop-in packs ``logos.py``/``AssetStore.read_logo`` already read. So a logo
badge that fell back to text lights up once a pack is installed.

Two source shapes, both from Kometa's public repos:

* CURATED — small "quality" fields (resolution/audio/source/hdr/aspect) where we
  hand-map each of our normalized values to one Kometa file. Precise, tiny.
* MIRROR — big name-keyed fields (streaming/network/studio) where we pull the
  whole Kometa folder and re-slug each filename with OUR ``_slug`` so a title's
  value resolves to it at render time. Best-effort matching; unmatched values
  just keep the text fallback.

The engine's I/O (list a repo folder, fetch a file) is injected, so the plan +
install are unit-testable without hitting GitHub.
"""

from __future__ import annotations

from utils.logging_config import get_logger

from .logos import _slug

logger = get_logger("video.overlays.logo_packs")

_MAIN = "Kometa-Team/Kometa"                      # defaults/overlays/images/*
_IMAGES = "Kometa-Team/Default-Images"           # <field>/logos/*
_REF = "master"


def _raw(repo: str, path: str) -> str:
    return "https://raw.githubusercontent.com/%s/%s/%s" % (repo, _REF, path)


def _main(path: str) -> str:
    return _raw(_MAIN, "defaults/overlays/images/" + path)


def _img(path: str) -> str:
    return _raw(_IMAGES, path)


# field -> { our normalized value : source image url }. Values match logos.py
# normalizer output (e.g. audio 'atmos', resolution '4k', source 'bluray').
CURATED = {
    "resolution": {
        "4k": _main("resolution/4k.png"), "1080p": _main("resolution/1080p.png"),
        "720p": _main("resolution/720p.png"), "480p": _main("resolution/480p.png"),
    },
    "audio_codec": {
        "atmos": _main("audio_codec/standard/atmos.png"),
        "truehd": _main("audio_codec/standard/truehd.png"),
        "dts_hd": _main("audio_codec/standard/ma.png"),          # DTS-HD Master Audio
        "dts": _main("audio_codec/standard/dts.png"),
        "eac3": _main("audio_codec/standard/plus.png"),          # Dolby Digital Plus
        "ac3": _main("audio_codec/standard/digital.png"),        # Dolby Digital
        "aac": _main("audio_codec/standard/aac.png"),
        "flac": _main("audio_codec/standard/flac.png"),
    },
    "hdr": {
        "hdr": _main("resolution/hdr.png"),
        "dolby_vision": _main("resolution/dv.png"),
        "hdr10plus": _main("resolution/dvhdrplus.png"),
    },
    "source": {
        "bluray": _img("video_format/logos/bluray.png"),
        "web": _img("video_format/logos/web.png"),
        "remux": _img("video_format/logos/remux.png"),
        "hdtv": _img("video_format/logos/hdtv.png"),
        "dvd": _img("video_format/logos/dvd.png"),
    },
    "aspect": {   # our canonical buckets (slugged) -> nearest Kometa aspect logo
        "4_3": _img("aspect/logos/1.33.png"),
        "16_9": _img("aspect/logos/1.78.png"),
        "2_40_1": _img("aspect/logos/2.35.png"),
    },
}

# field -> (repo, folder). Whole folder pulled + re-slugged to our convention.
MIRROR = {
    "streaming": (_MAIN, "defaults/overlays/images/streaming/color"),
    "network": (_MAIN, "defaults/overlays/images/network/color"),
    "studio": (_MAIN, "defaults/overlays/images/studio/standard"),
}

# Every field this installer can source (for the UI + gating).
SOURCEABLE_FIELDS = tuple(CURATED.keys()) + tuple(MIRROR.keys())


def _default_list_folder(repo: str, path: str):
    """[(name, download_url)] for a repo folder via the GitHub contents API."""
    import requests
    url = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (repo, path, _REF)
    r = requests.get(url, timeout=30, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return [(it["name"], it["download_url"]) for it in r.json()
            if it.get("type") == "file" and str(it.get("name", "")).lower().endswith((".png", ".webp", ".jpg", ".jpeg"))]


def _default_fetch(url: str) -> bytes | None:
    import requests
    r = requests.get(url, timeout=30)
    return r.content if r.status_code == 200 else None


def build_plan(*, list_folder=_default_list_folder) -> list:
    """The full install work-list: [(field, our_name, url)] across curated +
    mirror fields. Mirror folders are enumerated live and re-slugged; a folder
    that fails to list is skipped (logged), never aborts the whole plan."""
    jobs = []
    for field, mapping in CURATED.items():
        for name, url in mapping.items():
            jobs.append((field, name, url))
    for field, (repo, path) in MIRROR.items():
        try:
            entries = list_folder(repo, path)
        except Exception:
            logger.warning("logo pack: could not list %s/%s", repo, path, exc_info=True)
            continue
        seen = set()
        for fname, url in entries:
            base = fname.rsplit(".", 1)[0]
            name = _slug(base)
            if not name or name in seen:      # first file wins on a slug collision
                continue
            seen.add(name)
            jobs.append((field, name, url))
    return jobs


def pack_status(store) -> dict:
    """What's installed, for the UI + the palette gate: per-field counts, whether
    ANY pack exists, and which fields this installer can source."""
    counts = store.logo_pack_counts()
    return {"installed": bool(counts), "counts": counts,
            "sourceable": list(SOURCEABLE_FIELDS)}


# ── single background install job with progress (mirrors the apply job) ────────
import threading  # noqa: E402

_JOB = {"running": False, "phase": "idle", "done": 0, "total": 0,
        "installed": 0, "failed": 0, "field": None, "error": None}
_lock = threading.Lock()


def start_install(store) -> bool:
    """Kick a background install; False if one's already running."""
    with _lock:
        if _JOB["running"]:
            return False
        _JOB.update(running=True, phase="starting", done=0, total=0,
                    installed=0, failed=0, field=None, error=None)
    threading.Thread(target=_run_install, args=(store,), daemon=True).start()
    return True


def _run_install(store):
    try:
        _JOB["phase"] = "running"
        res = install(store, on_progress=lambda p: _JOB.update(p))
        _JOB.update(installed=res["installed"], failed=res["failed"], phase="done")
    except Exception as e:
        logger.exception("logo pack install failed")
        _JOB.update(phase="error", error=str(e))
    finally:
        _JOB["running"] = False


def install_status() -> dict:
    return dict(_JOB)


def install(store, *, on_progress=None, list_folder=_default_list_folder, fetch=_default_fetch) -> dict:
    """Download every planned logo into its pack folder. Best-effort per file;
    one failure never sinks the run. Returns totals."""
    jobs = build_plan(list_folder=list_folder)
    total = len(jobs)
    done = ok = failed = 0
    for field, name, url in jobs:
        try:
            data = fetch(url)
            if data:
                store.write_logo(field, name, data)
                ok += 1
            else:
                failed += 1
        except Exception:
            logger.warning("logo pack: fetch/write failed for %s/%s", field, name, exc_info=True)
            failed += 1
        done += 1
        if on_progress:
            on_progress({"done": done, "total": total, "ok": ok, "failed": failed, "field": field})
    return {"total": total, "installed": ok, "failed": failed}
