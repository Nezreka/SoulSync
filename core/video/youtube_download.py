"""YouTube download worker — the fulfillment lane for wished YouTube videos.

Model B (Boulder): YouTube grabs flow through the SAME ``video_downloads`` queue +
history as movies/TV, so the Downloads page, live progress, and History modal all work
for YouTube for free. But the mechanism is different — there's no slskd transfer to poll;
yt-dlp fetches the stream directly. So this worker owns a YouTube download end to end:

    pick stream (quality profile → yt-dlp format) → download into the library, organised
    as a Plex "TV by date" show (channel/Season YEAR/channel - DATE - title) → mark the
    row completed + archive it to history → remove the video from the wishlist.

The slskd ``download_monitor`` simply SKIPS ``source='youtube'`` rows (they have no
transfer to match), so this lane never disturbs the movie/TV pipeline.

The orchestration (``process_youtube_download``) is PURE — the actual yt-dlp run and all
DB writes are injected seams — so the lifecycle (dest planning, completion → archive +
unwish, failure → archive, no unwish) is unit-tested without a network or a DB. Production
(``run_youtube_download``) lazily binds the real calls and runs it on a worker thread.

Isolated: imports only sibling ``core.video`` modules; nothing from the music side.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from typing import Any, Callable, Dict, Optional

from core.video import organization, youtube_quality
from core.video.youtube_quality import format_selection

from utils.logging_config import get_logger

logger = get_logger("video.youtube_download")   # NOT bare getLogger — that never reaches app.log

try:
    import yt_dlp
except Exception:   # noqa: BLE001 - optional at import; absence handled at call time
    yt_dlp = None


def youtube_fields_from_download(dl: Dict[str, Any]) -> Dict[str, Any]:
    """Organising fields for a YouTube download row. The channel/video-title/date that
    ``render_path('youtube', …)`` needs ride in ``search_ctx`` (a generic JSON column);
    fall back to the row's own columns when absent."""
    ctx = dl.get("search_ctx")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    return {
        "channel": ctx.get("channel") or dl.get("title"),
        "title": ctx.get("video_title") or dl.get("title"),
        "published_at": ctx.get("published_at") or dl.get("year"),
        "youtube_id": dl.get("media_id"),
        # for the CHANNEL-level sidecars (poster/fanart/tvshow.nfo): the row's
        # poster_url is the channel avatar on youtube rows, channel_id keys the
        # remembered channel meta (banner/description)
        "channel_id": ctx.get("channel_id"),
        "poster_url": dl.get("poster_url"),
    }


def quality_override_from_download(dl: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A per-channel quality override stashed in the row's ``search_ctx`` at enqueue time,
    or None (use the global YouTube quality profile)."""
    ctx = dl.get("search_ctx")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    ctx = ctx if isinstance(ctx, dict) else {}
    q = ctx.get("quality")
    return q if isinstance(q, dict) else None


def plan_destination(dl: Dict[str, Any], settings: Dict[str, Any], container: str) -> Dict[str, str]:
    """Where this video lands in the library: ``{dir, filename, path}`` under the youtube
    root (``target_dir``), organised by the youtube template. Pure."""
    ext = "." + str(container or "mp4").lstrip(".")
    return organization.render_path("youtube", dl.get("target_dir"),
                                    youtube_fields_from_download(dl), settings, ext)


def ydl_download_opts(profile: Any, dest_dir: str, dest_stem: str,
                      *, progress_hook: Optional[Callable] = None, postprocess_hook: Optional[Callable] = None,
                      cookie_opts: Optional[dict] = None) -> dict:
    """The yt-dlp options dict for one download: format selection from the quality profile,
    a fixed output path (dir + stem + yt-dlp's own ext), polite defaults. Pure."""
    sel = format_selection(profile)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "format": sel["format"],
        "format_sort": sel["format_sort"],
        "merge_output_format": sel["merge_output_format"],
        "paths": {"home": str(dest_dir or "")},
        "outtmpl": dest_stem + ".%(ext)s",
        # sidecars: the episode thumbnail (→ '<name>-thumb.jpg' on import) + the metadata
        # json we mine for the .nfo (description / duration). ffmpeg (already needed to merge)
        # normalises the thumbnail to jpg.
        "writethumbnail": True,
        "writeinfojson": True,
        # FFmpegMetadata embeds title/upload date/description into the container —
        # Plex's Local Media Assets shows the EMBEDDED title on episodes (the
        # filename-derived title only works on the Personal Media agent), and
        # Jellyfin/Kodi read it too. ffmpeg is already required for the merge.
        "postprocessors": [{"key": "FFmpegMetadata"},
                           {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
    }
    if cookie_opts:
        opts.update(cookie_opts)
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if postprocess_hook:
        opts["postprocessor_hooks"] = [postprocess_hook]   # fires while ffmpeg merges/converts
    return opts


def _stem_and_container(dest: Dict[str, str], container: str) -> tuple:
    """(filename stem, final ext) — strip the ext render_path put on the filename so
    yt-dlp can own the extension during the merge."""
    fn = dest.get("filename") or "download"
    cont = str(container or "mp4").lstrip(".")
    stem = fn[:-(len(cont) + 1)] if fn.lower().endswith("." + cont.lower()) else os.path.splitext(fn)[0]
    return stem or "download", cont


def download_one(video_id: Any, dest_dir: str, dest_stem: str, profile: Any, container: str,
                 *, ydl_factory=None, progress_hook=None, postprocess_hook=None, cookie_opts=None) -> Dict[str, Any]:
    """Run yt-dlp for ONE video into ``dest_dir/dest_stem.ext``. Returns
    ``{ok, dest_path|None, error|None, title|None, published_at|None}`` — the
    extractor's own title/date ride along because they're AUTHORITATIVE (an
    enqueue-time context can lose the title; the extractor never does). The
    yt-dlp class is injectable for tests."""
    vid = str(video_id or "").strip()
    if not vid:
        return {"ok": False, "dest_path": None, "error": "No video id"}
    factory = ydl_factory or (yt_dlp.YoutubeDL if yt_dlp else None)
    if factory is None:
        return {"ok": False, "dest_path": None, "error": "yt-dlp unavailable"}
    opts = ydl_download_opts(profile, dest_dir, dest_stem, progress_hook=progress_hook,
                             postprocess_hook=postprocess_hook, cookie_opts=cookie_opts)
    url = vid if vid.startswith("http") else "https://www.youtube.com/watch?v=" + vid
    info = {}
    try:
        with factory(opts) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as e:   # noqa: BLE001 - any yt-dlp failure → a failed download, not a crash
        logger.info("youtube download failed for %s: %s", vid, e)
        return {"ok": False, "dest_path": None, "error": str(e)}
    dest_path = os.path.join(str(dest_dir or ""), dest_stem + "." + str(container or "mp4").lstrip("."))
    up = str(info.get("upload_date") or "")          # yt-dlp: YYYYMMDD
    published = f"{up[0:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 and up.isdigit() else None
    return {"ok": True, "dest_path": dest_path, "error": None,
            "title": info.get("title"), "published_at": published}


def authoritative_download_fields(dl: Dict[str, Any], res: Dict[str, Any]) -> Dict[str, Any]:
    """Fold yt-dlp's extracted title/date back into a row whose enqueue-time
    context LOST them (an upstream title-parser change can blank every scanned
    title — files then land as '$channel - $date -'). The extractor is the last
    word: when the context already carries a real video title the row returns
    unchanged; otherwise the returned copy carries the real title (+ date when
    missing) in both the row and its search_ctx, so the organised path, the
    sidecars, AND the history snapshot all get it. Pure."""
    title = (res or {}).get("title")
    if not title:
        return dl
    ctx = dl.get("search_ctx")
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except (ValueError, TypeError):
            ctx = {}
    ctx = dict(ctx) if isinstance(ctx, dict) else {}
    if str(ctx.get("video_title") or "").strip():
        return dl
    ctx["video_title"] = title
    if not ctx.get("published_at") and (res or {}).get("published_at"):
        ctx["published_at"] = res["published_at"]
    return dict(dl, title=title, search_ctx=json.dumps(ctx))


def _default_move(src: str, dest: str) -> None:
    """Move a finished staged file into the library, creating the target folders."""
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)


def build_episode_nfo(fields: Dict[str, Any], *, description: Any = None, runtime: Any = None) -> str:
    """A Jellyfin/Kodi/Plex ``<episodedetails>`` sidecar for a YouTube 'episode'. Pure — the
    description/runtime come from yt-dlp's info json. season = upload year, episode = MMDD
    (Plex 'by date' matches on ``<aired>``; the numbers help Jellyfin/Kodi)."""
    from xml.sax.saxutils import escape
    date = str(fields.get("published_at") or "")[:10]
    year = date[:4]
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<episodedetails>',
           '  <title>%s</title>' % escape(str(fields.get("title") or fields.get("channel") or "Video"))]
    if year.isdigit():
        out.append('  <season>%s</season>' % year)
    if len(date) == 10 and date[5:7].isdigit() and date[8:10].isdigit():
        out.append('  <episode>%d</episode>' % int(date[5:7] + date[8:10]))
    if description:
        out.append('  <plot>%s</plot>' % escape(str(description)))
    if date:
        out.append('  <aired>%s</aired>' % escape(date))
    if fields.get("channel"):
        out.append('  <studio>%s</studio>' % escape(str(fields["channel"])))
    if fields.get("youtube_id"):
        out.append('  <uniqueid type="youtube" default="true">%s</uniqueid>' % escape(str(fields["youtube_id"])))
    try:
        if runtime:
            out.append('  <runtime>%d</runtime>' % round(float(runtime) / 60))
    except (TypeError, ValueError):
        pass
    out.append('</episodedetails>')
    return "\n".join(out) + "\n"


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _default_sidecars(staged_video: str, final_video: str, fields: Dict[str, Any],
                      settings: Dict[str, Any],
                      channel_meta_lookup: Optional[Callable[[str], Any]] = None,
                      channel_videos_lookup: Optional[Callable[[str], Any]] = None) -> None:
    """Place the YouTube episode's sidecars next to the imported video — gated by the SAME
    post-processing toggles as the movie/TV side: ``save_artwork`` → episode art in BOTH
    server conventions (``<name>.jpg`` for Plex Local Media Assets, ``<name>-thumb.jpg``
    for Jellyfin/Kodi), ``write_nfo`` → ``<name>.nfo`` (metadata). yt-dlp dropped a
    thumbnail + ``.info.json`` next to the staged video; we always clean those up (move the
    wanted ones into the library, delete the rest), so nothing litters the download folder
    when a toggle is off. Also seeds the CHANNEL folder's show-level assets (poster/fanart/
    tvshow.nfo) once. Best-effort — never fails the grab."""
    settings = settings if isinstance(settings, dict) else {}
    want_thumb, want_nfo = bool(settings.get("save_artwork")), bool(settings.get("write_nfo"))
    try:
        src_dir, src_stem = os.path.dirname(staged_video), os.path.splitext(os.path.basename(staged_video))[0]
        dst_dir, dst_stem = os.path.dirname(final_video), os.path.splitext(os.path.basename(final_video))[0]
        # thumbnail: episode art in both conventions when wanted (Plex reads the
        # SAME-STEM jpg, Jellyfin/Kodi read -thumb — two cheap copies, both servers
        # happy), else discard the staged copy
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            src_thumb = os.path.join(src_dir, src_stem + ext)
            if os.path.exists(src_thumb):
                if want_thumb:
                    os.makedirs(dst_dir or ".", exist_ok=True)
                    art_ext = ".jpg" if ext == ".jpeg" else ext
                    plex_thumb = os.path.join(dst_dir, dst_stem + art_ext)
                    shutil.move(src_thumb, plex_thumb)
                    try:
                        shutil.copy2(plex_thumb, os.path.join(dst_dir, dst_stem + "-thumb" + art_ext))
                    except OSError:
                        pass
                else:
                    _silent_remove(src_thumb)
                break
        # info json → mine for the nfo (when wanted), then always drop it
        info = {}
        info_path = os.path.join(src_dir, src_stem + ".info.json")
        if os.path.exists(info_path):
            try:
                with open(info_path, encoding="utf-8") as f:
                    info = json.load(f)
            except (ValueError, OSError):
                info = {}
            _silent_remove(info_path)
        if want_nfo:
            os.makedirs(dst_dir or ".", exist_ok=True)
            with open(os.path.join(dst_dir, dst_stem + ".nfo"), "w", encoding="utf-8") as f:
                f.write(build_episode_nfo(fields, description=info.get("description"), runtime=info.get("duration")))
        _ensure_channel_assets(final_video, fields, settings, channel_meta_lookup, channel_videos_lookup)
    except Exception:   # noqa: BLE001 - sidecars are a nice-to-have, never fatal to the grab
        logger.exception("youtube sidecars failed for %s", final_video)


def _maxres_url(thumb_url: Any) -> Optional[str]:
    """i.ytimg thumbnail → its maxresdefault variant (the UI's ytHiRes mirror)."""
    m = re.search(r"/vi/([^/]+)/", str(thumb_url or ""))
    return ("https://i.ytimg.com/vi/" + m.group(1) + "/maxresdefault.jpg") if m else None


def _season_hero_bytes(fields: Dict[str, Any], year: str,
                       channel_videos_lookup: Optional[Callable[[str], Any]] = None) -> Optional[bytes]:
    """The image the in-app channel page shows for this year-season: the year's
    NEWEST video's thumbnail, maxres first (exact ytGroupByYear rule). Fallbacks:
    the raw thumb, then the imported video's own thumbnail."""
    candidates = []
    cid = str(fields.get("channel_id") or "").strip()
    if cid and channel_videos_lookup is not None:
        try:
            vids = channel_videos_lookup(cid) or []
        except Exception:   # noqa: BLE001 - the cache is a bonus
            vids = []
        for v in vids:      # newest-first from the cache, same as the UI
            if not isinstance(v, dict) or not v.get("thumbnail_url"):
                continue
            if str(v.get("published_at") or "").startswith(year):
                mx = _maxres_url(v["thumbnail_url"])
                if mx:
                    candidates.append(mx)
                candidates.append(v["thumbnail_url"])
                break
    # last resort: the video being imported (a real frame from the year too)
    mx = _maxres_url(fields.get("poster_url"))
    if mx:
        candidates.append(mx)
    if fields.get("poster_url"):
        candidates.append(fields["poster_url"])
    for url in candidates:
        data = _fetch_bytes(url)
        if data:
            return data
    return None


def _fetch_bytes(url: Any, timeout: int = 15) -> Optional[bytes]:
    """Best-effort in-memory fetch of an ABSOLUTE http(s) url (video thumbs for
    the season-poster backdrop). None on anything less than a clean 2xx read."""
    u = str(url or "")
    if not (u.startswith("http://") or u.startswith("https://")):
        return None
    try:
        import urllib.request
        req = urllib.request.Request(u, headers={"User-Agent": "SoulSync"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:   # noqa: BLE001 - a missing thumb just means the avatar backdrop
        return None


def _channel_dir_of(final_video: str, channel: Any) -> Optional[str]:
    """The ancestor directory named after the channel — template-agnostic (works for
    the default channel/Season YYYY/ layout AND custom depths). None when the user's
    template doesn't give the channel its own folder (flat layouts get no show assets)."""
    from core.video.organization import sanitize
    want = sanitize(channel)
    if not want:
        return None
    d = os.path.dirname(os.path.abspath(final_video))
    for _ in range(6):
        if os.path.basename(d) == want:
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _ensure_channel_assets(final_video: str, fields: Dict[str, Any], settings: Dict[str, Any],
                           channel_meta_lookup: Optional[Callable[[str], Any]] = None,
                           channel_videos_lookup: Optional[Callable[[str], Any]] = None) -> None:
    """Seed the CHANNEL folder's show-level assets so servers index it like a real
    show: ``poster.jpg`` (channel avatar — rides the download row), ``fanart.jpg``
    (channel banner, via the remembered channel meta) and ``tvshow.nfo``. Reuses the
    movie/TV sidecar writer, so it's idempotent (existing files are never refetched)
    and each file is independently best-effort."""
    channel_dir = _channel_dir_of(final_video, fields.get("channel"))
    if not channel_dir:
        return

    def _http(u):
        u = str(u or "")
        return u if u.startswith("http://") or u.startswith("https://") else ""

    meta: Dict[str, Any] = {"title": fields.get("channel")}
    cid = str(fields.get("channel_id") or "").strip()
    if cid and channel_meta_lookup is not None:
        try:
            remembered = channel_meta_lookup(cid) or {}
        except Exception:   # noqa: BLE001 - the lookup is a bonus, not a dependency
            remembered = {}
        if isinstance(remembered, dict):
            # the AVATAR is the channel poster. The download row's poster_url is
            # the VIDEO thumbnail (every enqueue path) — never use it here, and
            # never hand the fetcher a relative/proxied URL.
            if _http(remembered.get("avatar_url")):
                meta["poster_url"] = remembered["avatar_url"]
            if _http(remembered.get("banner_url")):
                meta["backdrop_url"] = remembered["banner_url"]
            if remembered.get("description"):
                meta["overview"] = remembered["description"]
    from core.video import sidecars as _sidecars
    from core.video.importer import real_fs
    fs = real_fs()
    _sidecars.write(channel_dir, "youtube_channel", meta, settings, fs)
    # season folder poster (the year folder between channel and file, when the
    # template has one). A COMPOSED per-year card — blurred-avatar backdrop,
    # circular avatar, the big year — so seasons are visually distinct from the
    # channel poster (Boulder: the plain avatar copy made them identical).
    # Render trouble falls back to the avatar copy; either way art lands.
    season_dir = os.path.dirname(os.path.abspath(final_video))
    if settings.get("save_artwork") and meta.get("poster_url") and \
            os.path.normpath(season_dir) != os.path.normpath(channel_dir):
        try:
            fs.makedirs(season_dir)   # pre-move seeding: the dir may not exist yet
            year = str(fields.get("published_at") or "")[:4]
            # Plex reads season art from the SHOW folder as season<NN>-poster.jpg
            # (Boulder's screenshots: the in-season poster.jpg was never read —
            # the cards were Plex's automatic show-poster crop). Jellyfin/Kodi
            # read poster.jpg inside the season folder. Write BOTH.
            targets = [os.path.join(season_dir, "poster.jpg")]
            if year.isdigit():
                targets.append(os.path.join(channel_dir, "season%s-poster.jpg" % year))
            missing = [t for t in targets if not os.path.isfile(t)]
            if missing:
                chan_poster = os.path.join(channel_dir, "poster.jpg")
                data = None
                if year.isdigit():
                    try:
                        from core.video.collections.poster_gen import render_season_poster
                        # hero = the SAME image the channel page shows for this
                        # year (its newest video's thumb, maxres first)
                        hero = _season_hero_bytes(fields, year, channel_videos_lookup)
                        if hero is None and os.path.isfile(chan_poster):
                            with open(chan_poster, "rb") as f:
                                hero = f.read()
                        if hero:
                            data = render_season_poster(hero, year, str(fields.get("channel") or ""))
                    except Exception:   # noqa: BLE001 - fall back to the avatar copy
                        data = None
                for target in missing:
                    if data:
                        with open(target, "wb") as f:
                            f.write(data)
                        logger.info("season poster: composed %s (hero=year's newest thumb)", target)
                    else:
                        fs.save_url(meta["poster_url"], target)
                        logger.info("season poster: render unavailable — plain avatar copy at %s", target)
            else:
                logger.info("season poster: keeping existing %s (delete to regenerate)",
                            " + ".join(targets))
        except Exception:   # noqa: BLE001 - season art is a nicety
            pass


def process_youtube_download(
    dl: Dict[str, Any],
    *,
    profile: Any,
    settings: Dict[str, Any],
    download: Callable = download_one,
    update_row: Callable[..., Any],
    archive: Callable[[Dict[str, Any], Dict[str, Any]], Any],
    clear_wishlist: Callable[[Any], Any],
    stage_dir: Optional[str] = None,
    move: Callable[[str, str], Any] = _default_move,
    sidecars: Callable[[str, str, Dict[str, Any], Dict[str, Any]], Any] = _default_sidecars,
    channel_assets: Callable[..., Any] = _ensure_channel_assets,
    progress_hook: Optional[Callable] = None,
    postprocess_hook: Optional[Callable] = None,
    cookie_opts: Optional[dict] = None,
    now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Fulfil one queued YouTube download. PURE — all I/O injected.

    Pipeline (same shape as the movie/TV lane): download into ``stage_dir`` (the shared
    download folder) → flip to 'importing' → MOVE into the organised library path → completed.
    When ``stage_dir`` is None it downloads straight into the library (legacy fallback, e.g.
    no download folder configured), skipping the move.

    On success: row → completed (with dest_path), snapshot to history, and remove the video
    from the wishlist (it's in the library now; history is the permanent record). On
    failure: row → failed + a history snapshot; the wishlist row is LEFT so a later scan/run
    can retry. Returns a small result dict."""
    now = now or (lambda: "")
    settings = settings if isinstance(settings, dict) else {}
    container = format_selection(profile)["merge_output_format"]
    dest = plan_destination(dl, settings, container)        # the FINAL organised library path
    stem, cont = _stem_and_container(dest, container)
    # Download target: the staging folder when set, else straight to the library. Either way
    # do NOT write the organised DIR back to target_dir — it's the youtube ROOT, and
    # plan_destination re-derives the channel/season folders under it; clobbering it re-nests
    # on a re-run (the orphan reaper re-queues an interrupted download).
    dl_dir = stage_dir if stage_dir else dest.get("dir")
    update_row(dl.get("id"), status="downloading", progress=0, filename=dest.get("filename"))

    res = download(dl.get("media_id"), dl_dir, stem, profile, cont,
                   progress_hook=progress_hook, postprocess_hook=postprocess_hook, cookie_opts=cookie_opts)

    if not res.get("ok"):
        err = res.get("error") or "Download failed"
        completed = now()
        update_row(dl.get("id"), status="failed", error=err, completed_at=completed)
        archive(dl, {"status": "failed", "error": err, "completed_at": completed})
        return {"status": "failed", "error": err}

    # The extractor's metadata is authoritative: a titleless row re-plans its
    # destination with the REAL title so files never land as '$channel - $date -'.
    fixed = authoritative_download_fields(dl, res)
    replanned = fixed is not dl
    if replanned:
        dl = fixed
        dest = plan_destination(dl, settings, container)

    staged_path = res.get("dest_path") or os.path.join(dl_dir or "", stem + "." + cont)
    final_path = dest.get("path") or staged_path

    # Staged build → post-process into the library (the visible 'importing'
    # phase). An UNSTAGED download moves only when the title fix re-planned its
    # name — the file on disk still wears the titleless stem and needs the rename.
    if staged_path and final_path and staged_path != final_path and (stage_dir or replanned):
        update_row(dl.get("id"), status="importing", progress=100, filename=dest.get("filename"))
        # Show/season art must EXIST before the video lands: Plex's folder watch
        # ingests the mp4 the instant it appears and reads show-level art at SHOW
        # CREATION — art written after the move stays invisible until a manual
        # metadata refresh (the "channel has no poster" report; ytdl-sub avoids
        # this by moving its prepared output art-first). Best-effort, never blocks.
        try:
            channel_assets(final_path, youtube_fields_from_download(dl), settings)
        except Exception:   # noqa: BLE001
            logger.exception("youtube channel assets (pre-move) failed for %s", final_path)
        try:
            move(staged_path, final_path)
        except Exception as e:   # noqa: BLE001 - downloaded fine but couldn't be placed
            err = "Import failed: " + str(e)
            completed = now()
            update_row(dl.get("id"), status="import_failed", error=err, completed_at=completed)
            archive(dl, {"status": "import_failed", "error": err, "completed_at": completed})
            logger.exception("youtube download %s: import move failed", dl.get("id"))
            return {"status": "import_failed", "error": err}
        dest_path = final_path
    else:
        dest_path = staged_path or final_path

    # episode sidecars (-thumb.jpg + .nfo) next to the imported video — gated by the
    # save_artwork / write_nfo post-processing toggles (shared with the movie/TV side).
    sidecars(staged_path, dest_path, youtube_fields_from_download(dl), settings)

    completed = now()
    update_row(dl.get("id"), status="completed", progress=100,
               dest_path=dest_path, completed_at=completed,
               filename=dest.get("filename"), title=dl.get("title"))
    archive(dl, {"status": "completed", "dest_path": dest_path, "completed_at": completed})
    try:
        clear_wishlist(dl.get("media_id"))
    except Exception:   # noqa: BLE001 - unwish is best-effort; the file is already in place
        logger.exception("youtube download %s: unwish failed", dl.get("id"))
    return {"status": "completed", "dest_path": dest_path}


# ── concurrency + pacing (the music side's lesson: cap concurrency AND space starts) ──
# yt-dlp 429s if hammered, so fetch STARTS are spaced ≥ this far apart across all workers.
_DELAY_SECONDS = 3.0
_pace_lock = threading.Lock()
_last_start = [0.0]


def _pace(delay: float) -> None:
    """Block until at least ``delay`` seconds after the previous fetch start, then reserve
    this start slot. Reserving under the lock (sleeping outside it) staggers concurrent
    workers without serialising them past the delay."""
    if delay <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        start_at = max(now, _last_start[0] + delay) if _last_start[0] else now
        _last_start[0] = start_at
    wait = start_at - time.monotonic()
    if wait > 0:
        time.sleep(wait)


# Download ids with a live worker thread right now. After a restart this is empty, so any
# row still marked 'downloading' is an orphan (its thread died) → the reaper re-queues it.
_active_worker_ids: set = set()


def _spawn_worker(dl_id: Any, db_provider: Callable) -> None:
    threading.Thread(target=run_youtube_download, args=(dl_id, db_provider),
                     daemon=True, name="yt-dl-%s" % dl_id).start()


def requeue_orphaned_youtube(db_provider: Callable) -> int:
    """Recover YouTube downloads stuck in 'downloading' with no live worker (e.g. after a
    restart killed the threads) by putting them back to 'queued' so the pump re-runs them.
    A download whose worker is alive is in ``_active_worker_ids`` and is left untouched.
    Returns the count recovered."""
    n = 0
    for d in (db_provider().get_active_video_downloads() or []):
        if (d.get("source") == "youtube" and d.get("status") == "downloading"
                and d.get("id") not in _active_worker_ids):
            db_provider().update_video_download(d["id"], status="queued", progress=0)
            n += 1
    return n


def start_next_queued(db_provider: Callable) -> Any:
    """Claim the next queued YouTube download and start it. Returns its id, or None if the
    queue is empty. The wishlist pump uses this to fill slots; each finished worker calls it
    once (one-out-one-in) so the queue drains continuously at the established concurrency."""
    row = db_provider().claim_next_youtube_queued()
    if not row:
        return None
    _spawn_worker(row["id"], db_provider)
    return row["id"]


# ── production wiring ─────────────────────────────────────────────────────────
def run_youtube_download(dl_id: Any, db_provider: Callable) -> None:
    """Production entry: fetch the row, bind real seams, fulfil it. Called on a worker
    thread by the pump; on finish it starts the next queued download (one-out-one-in)."""
    _active_worker_ids.add(dl_id)             # mark this download as having a live worker
    db = db_provider()
    dl = db.get_video_download(dl_id)
    if not dl:
        _active_worker_ids.discard(dl_id)
        start_next_queued(db_provider)        # keep the queue moving even on a stale id
        return
    # Per-channel quality override (stashed in search_ctx at enqueue) wins over the global.
    override = quality_override_from_download(dl)
    profile = youtube_quality.normalize(override) if override else youtube_quality.load(db)
    settings = organization.load(db)
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _progress(d):
        # yt-dlp progress hook → row progress %. Best-effort; never raises into yt-dlp.
        try:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                if total:
                    db.update_video_download(dl_id, progress=int(got * 100 / total))
        except Exception:   # noqa: BLE001, S110 - a progress glitch must not abort the download
            pass

    def _postprocess(d):
        # yt-dlp finished the bytes and is now merging audio+video / converting (ffmpeg) — flip
        # to 'importing' so the card stops sitting on a stuck-looking 100% 'Downloading' until
        # it (and the library move that follows) are done. Best-effort.
        try:
            if d.get("status") in ("started", "processing"):
                db.update_video_download(dl_id, status="importing", progress=100)
        except Exception:   # noqa: BLE001, S110 - a hook glitch must not abort the download
            pass

    def _archive(row, upd):
        try:
            db.record_download_history({**row, **upd})
        except Exception:
            logger.exception("youtube download %s: history snapshot failed", dl_id)

    cookie_opts = None
    try:
        from core.video.youtube import _cookie_opts
        cookie_opts = _cookie_opts()
    except Exception:   # noqa: BLE001 - cookies are optional
        cookie_opts = None

    try:
        _pace(_DELAY_SECONDS)                  # space fetch starts to avoid yt-dlp 429s
        # Stage into the shared download folder (a 'youtube' subfolder), then transfer to the
        # library — same pipeline as movies/TV. Falls back to straight-to-library if no
        # download folder is configured.
        from config.settings import config_manager
        dl_root = str(config_manager.get("soulseek.download_path", "") or "").strip()
        stage_dir = os.path.join(dl_root, "youtube") if dl_root else None

        process_youtube_download(
            dl, profile=profile, settings=settings,
            update_row=db.update_video_download, archive=_archive,
            clear_wishlist=lambda vid: db.remove_youtube_from_wishlist("video", vid),
            stage_dir=stage_dir,
            # default sidecars + the remembered-channel-meta lookup (banner/description
            # for the channel folder's fanart.jpg / tvshow.nfo)
            sidecars=lambda st, fin, flds, stg: _default_sidecars(
                st, fin, flds, stg, channel_meta_lookup=db.get_channel_meta,
                channel_videos_lookup=db.get_channel_videos),
            channel_assets=lambda fin, flds, stg: _ensure_channel_assets(
                fin, flds, stg, db.get_channel_meta, db.get_channel_videos),
            progress_hook=_progress, postprocess_hook=_postprocess, cookie_opts=cookie_opts, now=_now)
    finally:
        _active_worker_ids.discard(dl_id)      # worker done — no longer protects this row
        start_next_queued(db_provider)         # one out, one in — drain the queue


__all__ = [
    "youtube_fields_from_download", "plan_destination", "ydl_download_opts",
    "download_one", "process_youtube_download", "run_youtube_download",
    "start_next_queued", "requeue_orphaned_youtube", "quality_override_from_download",
]
