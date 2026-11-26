"""what the info sheet shows about one item (scrap/ITEM_INFO_CONTRACT.md).

    describe(item_type, item_id, admin=False) -> {title, subtitle, artwork, sections}
                                                  or raises Refused

the server does the work and both clients only draw it: every value is
display text, sections come in order, a section with no rows and a row
with no value are left out. only what is stored is read. ffprobe is never
run here; a probe the player already made (core/player/tracks) is used
when there is one, and the network is never asked.

who may ask is who may play: the profile's section, its rating cap and
hidden libraries for video, its own library and explicit setting for
music, a home library it can see, a podcast it follows. a hidden library
answers like a missing item, so it is never named.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.player.playable import Refused, check
from utils.logging_config import get_logger

logger = get_logger("player.info")

ITEM_TYPES = ("movie", "episode", "track", "audiobook", "podcast_episode", "home", "youtube")


# ── db handles (tests swap these) ────────────────────────────────────────

def _video_db():
    from api.video import get_video_db
    return get_video_db()


def _music_db():
    from database.music_database import get_database
    return get_database()


def _player_db():
    from api.player import player_db
    return player_db()


def _audiobook_db():
    from core.audiobook_database import get_audiobook_db
    return get_audiobook_db()


def _profile_id() -> Optional[int]:
    from core.player.playable import current_profile_id
    return current_profile_id()


# ── the answer ───────────────────────────────────────────────────────────

class Info:
    """an answer being put together"""

    def __init__(self, title: str, subtitle: str = "", artwork: Optional[str] = None):
        self.title = title
        self.subtitle = subtitle
        self.artwork = artwork
        self.sections: List[Dict[str, Any]] = []

    def add(self, sid: str, title: str, rows: Iterable[Optional[Dict[str, Any]]],
            group: Optional[str] = None) -> None:
        kept = [r for r in rows if r]
        if not kept:
            return
        out: Dict[str, Any] = {"id": sid, "title": f"{title} · {group}" if group else title, "rows": kept}
        if group:
            out["group"] = group
        self.sections.append(out)

    def json(self) -> Dict[str, Any]:
        return {"title": self.title, "subtitle": self.subtitle, "artwork": self.artwork,
                "sections": self.sections}


def row(label: str, value: Any, *, mono: bool = False, copy: bool = False) -> Optional[Dict[str, Any]]:
    """a label and its value as text; nothing when there's no value"""
    if value is None or value is False:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v not in (None, ""))
    text = str(value).strip()
    if not text:
        return None
    out: Dict[str, Any] = {"label": label, "value": text}
    if mono:
        out["mono"] = True
    if copy:
        out["copy"] = True
    return out


def ident(label: str, value: Any) -> Optional[Dict[str, Any]]:
    """an id: monospace, with a copy button"""
    return row(label, value, mono=True, copy=True)


# ── words for numbers ────────────────────────────────────────────────────

def size_text(n: Any) -> Optional[str]:
    try:
        size = float(n)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return None


def clock(seconds: Any) -> Optional[str]:
    """2:05:09, 3:45"""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def runtime(seconds: Any) -> Optional[str]:
    """1h 57m, 42m, 3m 33s, 45s"""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    if total < 60:
        return f"{total}s"
    if total < 600 and total % 60:
        return f"{total // 60}m {total % 60}s"
    h, m = divmod(total // 60, 60)
    return f"{h}h {m}m" if h and m else f"{h}h" if h else f"{m}m"


def bitrate_text(bps: Any) -> Optional[str]:
    try:
        rate = float(bps)
    except (TypeError, ValueError):
        return None
    if rate <= 0:
        return None
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.1f} Mbps"
    return f"{int(round(rate / 1000))} kbps"


def _overall_bps(size: Any, seconds: Any) -> Optional[float]:
    try:
        if size and seconds and float(seconds) > 0:
            return float(size) * 8 / float(seconds)
    except (TypeError, ValueError):
        pass
    return None


def date_text(value: Any, with_time: bool = False) -> Optional[str]:
    """Nov 3, 2026 from an iso date or time, or a unix time"""
    if value in (None, "", 0):
        return None
    when: Optional[_dt.datetime] = None
    if isinstance(value, (int, float)):
        try:
            when = _dt.datetime.fromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip().replace("Z", "")
        try:
            when = _dt.datetime.fromisoformat(text.replace(" ", "T")[:19])
        except ValueError:
            try:
                when = _dt.datetime.fromisoformat(text[:10])
            except ValueError:
                return text[:10] if text[:4].isdigit() else None
    day = f"{when.strftime('%b')} {when.day}, {when.year}"
    if with_time and (when.hour or when.minute):
        hour = when.hour % 12 or 12
        return f"{day}, {hour}:{when.minute:02d} {'PM' if when.hour >= 12 else 'AM'}"
    return day


_LANGS = {
    "en": "English", "eng": "English", "ja": "Japanese", "jpn": "Japanese", "es": "Spanish",
    "spa": "Spanish", "fr": "French", "fre": "French", "fra": "French", "de": "German", "ger": "German",
    "deu": "German", "it": "Italian", "ita": "Italian", "pt": "Portuguese", "por": "Portuguese",
    "ru": "Russian", "rus": "Russian", "zh": "Chinese", "chi": "Chinese", "zho": "Chinese",
    "ko": "Korean", "kor": "Korean", "nl": "Dutch", "dut": "Dutch", "nld": "Dutch", "sv": "Swedish",
    "swe": "Swedish", "no": "Norwegian", "nor": "Norwegian", "nob": "Norwegian", "da": "Danish",
    "dan": "Danish", "fi": "Finnish", "fin": "Finnish", "pl": "Polish", "pol": "Polish", "tr": "Turkish",
    "tur": "Turkish", "ar": "Arabic", "ara": "Arabic", "hi": "Hindi", "hin": "Hindi", "he": "Hebrew",
    "heb": "Hebrew", "cs": "Czech", "cze": "Czech", "ces": "Czech", "hu": "Hungarian", "hun": "Hungarian",
    "el": "Greek", "gre": "Greek", "ell": "Greek", "th": "Thai", "tha": "Thai", "uk": "Ukrainian",
    "ukr": "Ukrainian", "vi": "Vietnamese", "vie": "Vietnamese", "id": "Indonesian", "ind": "Indonesian",
    "ro": "Romanian", "rum": "Romanian", "ron": "Romanian",
}


def language(code: Any) -> Optional[str]:
    text = str(code or "").strip()
    if not text or text.lower() in ("und", "unk", "zxx"):
        return None
    return _LANGS.get(text.lower().split("-")[0], text.upper() if len(text) <= 3 else text)


def languages(value: Any) -> Optional[str]:
    """'ja,en' or a json list -> Japanese, English"""
    if value in (None, ""):
        return None
    codes: List[Any]
    if isinstance(value, (list, tuple)):
        codes = list(value)
    else:
        text = str(value).strip()
        try:
            parsed = json.loads(text) if text.startswith("[") else None
        except ValueError:
            parsed = None
        codes = parsed if isinstance(parsed, list) else text.split(",")
    names = list(dict.fromkeys(n for n in (language(c) for c in codes) if n))
    return ", ".join(names) or None


_CODECS = {
    "h264": "H.264", "avc": "H.264", "avc1": "H.264", "x264": "H.264", "hevc": "HEVC", "h265": "HEVC",
    "x265": "HEVC", "av1": "AV1", "vp9": "VP9", "vp8": "VP8", "mpeg2video": "MPEG-2", "mpeg4": "MPEG-4",
    "vc1": "VC-1", "aac": "AAC", "ac3": "AC-3", "eac3": "E-AC-3", "truehd": "TrueHD", "dts": "DTS",
    "flac": "FLAC", "opus": "Opus", "mp3": "MP3", "vorbis": "Vorbis", "alac": "ALAC", "pcm_s16le": "PCM",
    "pcm_s24le": "PCM", "subrip": "SRT", "srt": "SRT", "ass": "ASS", "ssa": "SSA", "webvtt": "WebVTT",
    "vtt": "WebVTT", "mov_text": "MOV text", "hdmv_pgs_subtitle": "PGS", "pgssub": "PGS",
    "dvd_subtitle": "VobSub", "dvdsub": "VobSub", "dvb_subtitle": "DVB", "sub": "SUB",
}


def codec(name: Any) -> Optional[str]:
    text = str(name or "").strip()
    if not text:
        return None
    return _CODECS.get(text.lower(), text.upper() if len(text) <= 6 else text)


def channels(n: Any, layout: Any = None) -> Optional[str]:
    try:
        count = int(n)
    except (TypeError, ValueError):
        return str(layout) if layout else None
    named = {1: "Mono", 2: "Stereo", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}.get(count)
    if named:
        return named
    return f"{count} channels" if count > 0 else None


def resolution_label(text: Any, height: Any = None) -> Optional[str]:
    """2160p -> 4K"""
    raw = str(text or "").strip().lower()
    try:
        h = int(height) if height else None
    except (TypeError, ValueError):
        h = None
    if raw in ("4k", "uhd", "2160p") or (h and h >= 2000):
        return "4K"
    if raw:
        return raw if raw.endswith(("p", "i")) else raw.upper()
    if h:
        return f"{h}p"
    return None


def _ext(path: Any) -> Optional[str]:
    ext = os.path.splitext(str(path or "").split("?")[0])[1].lstrip(".")
    return ext.upper() if ext else None


def _base(path: Any) -> Optional[str]:
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or None


_FROM = {"plex": "Plex", "jellyfin": "Jellyfin", "emby": "Emby", "navidrome": "Navidrome",
         "subsonic": "Subsonic", "disk": "Disk", "soulseek": "Soulseek", "tidal": "Tidal",
         "qobuz": "Qobuz", "deezer": "Deezer", "youtube": "YouTube", "torrent": "Torrent",
         "usenet": "Usenet", "hifi": "HiFi"}


def source_name(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return _FROM.get(text.lower(), text[:1].upper() + text[1:])


_RELEASE = {"bluray": "Blu-ray", "web-dl": "WEB-DL", "webdl": "WEB-DL", "webrip": "WEBRip",
            "hdtv": "HDTV", "dvd": "DVD", "remux": "Remux", "youtube": "YouTube"}


def _yes(value: Any) -> Optional[str]:
    return "Yes" if value not in (None, 0, "", "0", False) else None


# ── shared reads ─────────────────────────────────────────────────────────

def _rows(conn, sql: str, args: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, tuple(args))
    names = [d[0] for d in cur.description or ()]
    return [dict(zip(names, tuple(r), strict=True)) for r in cur.fetchall()]


def _first(conn, sql: str, args: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    got = _rows(conn, sql, args)
    return got[0] if got else None


def _maybe(fn: Callable[[], Any], default: Any = None) -> Any:
    """an extra that may be missing (an older table) never fails the sheet"""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        logger.debug("info extra unavailable", exc_info=True)
        return default


def _int_id(item_id: Any, what: str = "library") -> int:
    try:
        return int(str(item_id).strip())
    except (TypeError, ValueError):
        raise Refused(400, "bad_id", f"That isn't a {what} id.") from None


def _progress(item_type: str, item_id: str) -> Optional[Dict[str, Any]]:
    pid = _profile_id()
    if pid is None:
        return None

    def read():
        from core.player import progress
        return progress.lookup(_player_db(), pid, item_type, [item_id]).get(item_id)
    return _maybe(read)


def _progress_rows(item_type: str, item_id: str, total: Any = None, last_played: Any = None) -> List[Any]:
    p = _progress(item_type, item_id)
    out: List[Any] = []
    if p:
        if p.get("finished"):
            out.append(row("Progress", "Finished"))
        elif p.get("position_seconds"):
            whole = p.get("duration_seconds") or total
            at = runtime(p["position_seconds"])
            out.append(row("Progress", f"{at} in of {runtime(whole)}" if runtime(whole) else f"{at} in"))
        out.append(row("Last played", date_text(p.get("updated_at"))))
    elif last_played:
        out.append(row("Last played", date_text(last_played)))
    return out


# ── video ────────────────────────────────────────────────────────────────

def _video_allowed(db, kind: str, rating: Any, title_id: int) -> None:
    """the rating cap (403, as playing says) and a hidden library (404)"""
    from api.video.kids import _in_hidden_library, kind_allows, video_cap
    cap = video_cap()
    if not kind_allows(cap, kind, rating):
        raise Refused(403, "restricted", "That isn't available on this profile.")
    if title_id in _in_hidden_library(db, cap, kind, [title_id]):
        raise Refused(404, "not_found", "That isn't in your library.")


def _library_name(db, kind: str, title_id: int) -> Optional[str]:
    def read():
        from core.player import libraries as L
        pdb = _player_db()
        lid = L.library_ids_for(db, pdb, kind, [title_id]).get(title_id)
        return next((x["name"] for x in L.libraries(pdb) if x["id"] == lid), None)
    return _maybe(read)


def _probe_for(file_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """the player's own probe of this file, when it made one"""
    def read():
        from core.player import tracks as K
        return (K.cached(("id", ("media-file", file_row.get("id"), None)))
                or K.cached_like(_base(file_row.get("relative_path")) or "", file_row.get("size_bytes")))
    return _maybe(read)


def _hdr_from_probe(v: Dict[str, Any]) -> Optional[str]:
    transfer = str(v.get("transfer") or "").lower()
    if transfer == "smpte2084":
        return "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return "SDR" if transfer else None


def _version_label(f: Dict[str, Any]) -> str:
    bits = [resolution_label(f.get("resolution")), f.get("dynamic_range"), f.get("edition")]
    return " ".join(str(b) for b in bits if b) or _ext(f.get("relative_path")) or "File"


def _video_files(info: Info, files: List[Dict[str, Any]], *, admin: bool, server: Optional[str]) -> Optional[Dict]:
    """a file, video and audio section for each file (version), and the
    first file's subtitles and chapters; the first file's probe back"""
    labels: Dict[str, int] = {}
    first_probe = None
    for n, f in enumerate(files):
        group = None
        if len(files) > 1:
            group = _version_label(f)
            labels[group] = labels.get(group, 0) + 1
            if labels[group] > 1:
                group = f"{group} ({labels[group]})"
        probe = _probe_for(f) or {}
        if n == 0:
            first_probe = probe
        fmt = probe.get("format") or {}
        seconds = f.get("runtime_seconds") or fmt.get("duration")
        path = f.get("relative_path")
        info.add("file", "File", [
            row("File name", _base(path)),
            row("Container", _ext(path)),
            row("Size", size_text(f.get("size_bytes"))),
            row("Duration", clock(seconds)),
            row("Overall bitrate", bitrate_text(fmt.get("bitrate") or _overall_bps(f.get("size_bytes"), seconds))),
            row("Quality", f.get("quality")),
            row("Release", _RELEASE.get(str(f.get("release_source") or "").lower(), f.get("release_source"))),
            row("Edition", f.get("edition")),
            row("Added", date_text(f.get("added_at"))),
            row("Path", path, mono=True, copy=True) if admin else None,
            row("Server", server, mono=True, copy=True) if admin else None,
        ], group)
        v = (probe.get("video") or [{}])[0]
        size = None
        if v.get("width") and v.get("height"):
            size = f"{v['width']}×{v['height']}"
            label = resolution_label(f.get("resolution"), v.get("height"))
            size = f"{size} ({label})" if label else size
        info.add("video", "Video", [
            row("Codec", codec(v.get("codec") or f.get("video_codec"))),
            row("Profile", v.get("profile")),
            row("Resolution", size or resolution_label(f.get("resolution"))),
            row("Aspect ratio", f.get("aspect")),
            row("Frame rate", f"{v['fps']:g} fps" if v.get("fps") else None),
            row("Dynamic range", f.get("dynamic_range") or _hdr_from_probe(v)),
            row("Bit depth", f"{v['bit_depth']}-bit" if v.get("bit_depth") else None),
            row("Bitrate", bitrate_text(v.get("bitrate"))),
        ], group)
        streams = probe.get("audio") or []
        if streams:
            audio_rows = [row(f"Track {i + 1}", " · ".join(x for x in (
                language(a.get("language")), codec(a.get("codec")),
                channels(a.get("channels"), a.get("layout")), bitrate_text(a.get("bitrate")),
                a.get("title"), "default" if a.get("default") else None) if x))
                for i, a in enumerate(streams)]
        else:
            audio_rows = [
                row("Codec", " ".join(x for x in (codec(f.get("audio_codec")),
                                                  "Atmos" if f.get("atmos") else None) if x)),
                row("Channels", channels(f.get("audio_channels"))),
                row("Languages", languages(f.get("audio_languages"))),
            ]
        info.add("audio", "Audio", audio_rows, group)
    return first_probe


def _video_subtitles(info: Info, first: Optional[Dict[str, Any]], probe: Optional[Dict[str, Any]],
                     downloaded: Any = None) -> None:
    subs = (probe or {}).get("subtitles") or []
    if subs:
        rows = [row(language(x.get("language")) or "Unknown language", " · ".join(y for y in (
            codec(x.get("codec")), "forced" if x.get("forced") else None, "SDH" if x.get("sdh") else None,
            x.get("title"), "external" if x.get("external") else "embedded") if y)) for x in subs]
    else:
        rows = [row("Embedded", languages((first or {}).get("subtitle_languages")))]
    rows.append(row("Downloaded", languages(downloaded)))
    info.add("subtitles", "Subtitles", rows)


def _video_chapters(info: Info, probe: Optional[Dict[str, Any]]) -> None:
    marks = (probe or {}).get("chapters") or []
    info.add("chapters", "Chapters", [row("Chapters", str(len(marks)) if marks else None)])


def _genres(conn, table: str, column: str, title_id: int) -> List[str]:
    return _maybe(lambda: [r["name"] for r in _rows(
        conn, f"SELECT g.name FROM {table} x JOIN genres g ON g.id = x.genre_id "  # noqa: S608 - our own names
              f"WHERE x.{column} = ? ORDER BY g.name", (title_id,))], [])


def _scores(t: Dict[str, Any]) -> Optional[str]:
    parts = []
    if t.get("rating"):
        parts.append(f"TMDB {float(t['rating']):.1f}")
    if t.get("imdb_rating"):
        parts.append(f"IMDb {float(t['imdb_rating']):.1f}")
    if t.get("rt_rating"):
        parts.append(f"Rotten Tomatoes {int(t['rt_rating'])}%")
    if t.get("metacritic"):
        parts.append(f"Metacritic {int(t['metacritic'])}")
    return " · ".join(parts) or None


def movie(item_id: Any, admin: bool) -> Info:
    check("movie")
    mid = _int_id(item_id)
    db = _video_db()
    conn = db._get_connection()
    try:
        m = _first(conn, "SELECT * FROM movies WHERE id = ?", (mid,))
        if not m:
            raise Refused(404, "not_found", "That isn't in your library.")
        _video_allowed(db, "movie", m.get("content_rating"), mid)
        files = _rows(conn, "SELECT * FROM media_files WHERE movie_id = ? "
                            "ORDER BY COALESCE(size_bytes, 0) DESC, id", (mid,))
        genres = _genres(conn, "movie_genres", "movie_id", mid)
    finally:
        conn.close()
    seconds = (files[0].get("runtime_seconds") if files else None) or (m.get("runtime_minutes") or 0) * 60
    info = Info(m.get("title") or "Movie",
                " · ".join(x for x in (str(m["year"]) if m.get("year") else None, m.get("content_rating"),
                                       runtime(seconds)) if x),
                f"/api/video/poster/movie/{mid}?w=342")
    info.add("details", "Details", [
        row("Title", m.get("title")),
        row("Year", m.get("year")),
        row("Released", date_text(m.get("release_date"))),
        row("Runtime", runtime(seconds)),
        row("Genres", genres),
        row("Rating", m.get("content_rating")),
        row("Scores", _scores(m)),
        row("Studio", m.get("studio")),
        row("Collection", m.get("tmdb_collection_name")),
        row("Added", date_text(m.get("server_added_at") or m.get("added_at"))),
        row("Plays", m.get("play_count") or None),
        *_progress_rows("movie", str(mid), seconds, m.get("last_viewed_at")),
    ])
    info.add("ids", "IDs", [ident("TMDB", m.get("tmdb_id")), ident("IMDb", m.get("imdb_id"))])
    server = source_name(m.get("server_source"))
    probe = _video_files(info, files, admin=admin, server=server)
    _video_subtitles(info, files[0] if files else None, probe, m.get("subtitle_langs"))
    _video_chapters(info, probe)
    info.add("source", "Source", [row("From", server or ("Disk" if files else None)),
                                  row("Library", _library_name(db, "movie", mid))])
    return info


def episode(item_id: Any, admin: bool) -> Info:
    check("episode")
    eid = _int_id(item_id)
    db = _video_db()
    conn = db._get_connection()
    try:
        e = _first(conn, "SELECT * FROM episodes WHERE id = ?", (eid,))
        s = _first(conn, "SELECT * FROM shows WHERE id = ?", (e["show_id"],)) if e else None
        if not e or not s:
            raise Refused(404, "not_found", "That isn't in your library.")
        _video_allowed(db, "show", s.get("content_rating"), int(s["id"]))
        files = _rows(conn, "SELECT * FROM media_files WHERE episode_id = ? "
                            "ORDER BY COALESCE(size_bytes, 0) DESC, id", (eid,))
        genres = _genres(conn, "show_genres", "show_id", int(s["id"]))
    finally:
        conn.close()
    season, number = e.get("season_number"), e.get("episode_number")
    where = f"Special {number}" if season == 0 else f"Season {season}, Episode {number}"
    seconds = (files[0].get("runtime_seconds") if files else None) or (e.get("runtime_minutes") or 0) * 60
    title = e.get("title") or f"Episode {number}"
    info = Info(title, " · ".join(x for x in (s.get("title"), f"S{season} E{number}") if x),
                f"/api/video/poster/episode/{eid}?w=500")
    info.add("details", "Details", [
        row("Title", title),
        row("Show", s.get("title")),
        row("Episode", where),
        row("Aired", date_text(e.get("air_date"))),
        row("Runtime", runtime(seconds)),
        row("Genres", genres),
        row("Rating", s.get("content_rating")),
        row("Score", f"{float(e['rating']):.1f}" if e.get("rating") else None),
        row("Network", s.get("network")),
        row("Added", date_text(e.get("added_at"))),
        row("Plays", e.get("play_count") or None),
        *_progress_rows("episode", str(eid), seconds, e.get("last_viewed_at")),
    ])
    info.add("ids", "IDs", [
        ident("TVDB", e.get("tvdb_id")),
        ident("Show TMDB", s.get("tmdb_id")),
        ident("Show TVDB", s.get("tvdb_id")),
        ident("Show IMDb", s.get("imdb_id")),
    ])
    server = source_name(e.get("server_source") or s.get("server_source"))
    probe = _video_files(info, files, admin=admin, server=server)
    _video_subtitles(info, files[0] if files else None, probe, s.get("subtitle_langs"))
    _video_chapters(info, probe)
    info.add("source", "Source", [row("From", server or ("Disk" if files else None)),
                                  row("Library", _library_name(db, "show", int(s["id"])) or s.get("library_name"))])
    return info


def home(item_id: Any, admin: bool) -> Info:
    check("home")
    from core.player import home_access
    from core.player import libraries as L
    from core.video import home_videos as H
    if not str(item_id).isdigit():
        raise Refused(400, "bad_id", "That isn't a home video id.")
    db, pdb = _video_db(), _player_db()
    conn = db._get_connection()
    try:
        r = H.get(conn, int(item_id))
    finally:
        conn.close()
    if r is None or r["root"] not in home_access.visible_roots(pdb, db, home_access.hidden_now()):
        raise Refused(404, "not_found", "That home video isn't in your library.")
    labels = _maybe(lambda: {x["id"]: x["label"] for x in H.roots(db)}, {})
    v = H.video_json(r, labels)
    lid = _maybe(lambda: L.home_library_of(L.libraries(pdb), r["root"]))
    library = _maybe(lambda: next((x["name"] for x in L.libraries(pdb) if x["id"] == lid), None))
    when = r.get("user_filmed_at") or r.get("filmed_at")
    info = Info(v["title"], " · ".join(x for x in (v.get("album"), date_text(when)) if x), v.get("thumb_url"))
    info.add("details", "Details", [
        row("Title", v["title"]),
        row("Filmed", date_text(when, with_time=True)),
        row("Album", v.get("album")),
        row("Runtime", runtime(r.get("duration_s"))),
        row("Added", date_text(r.get("added_at"))),
        *_progress_rows("home", str(r["id"]), r.get("duration_s")),
    ])
    path = r.get("path") or (r["source_key"] if r.get("source") == "disk" else None)
    probe = _maybe(lambda: _probe_like(path, r.get("size_bytes"))) or {}
    fmt = probe.get("format") or {}
    info.add("file", "File", [
        row("File name", _base(path)),
        row("Container", _ext(path)),
        row("Size", size_text(r.get("size_bytes"))),
        row("Duration", clock(r.get("duration_s"))),
        row("Overall bitrate", bitrate_text(fmt.get("bitrate") or _overall_bps(r.get("size_bytes"), r.get("duration_s")))),
        row("Added", date_text(r.get("added_at"))),
        row("Path", path, mono=True, copy=True) if admin else None,
        row("Server", source_name(r.get("source")), mono=True, copy=True) if admin else None,
    ])
    pv = (probe.get("video") or [{}])[0]
    width, height = pv.get("width") or r.get("width"), pv.get("height") or r.get("height")
    info.add("video", "Video", [
        row("Codec", codec(pv.get("codec") or r.get("video_codec"))),
        row("Profile", pv.get("profile")),
        row("Resolution", f"{width}×{height}" if width and height else None),
        row("Frame rate", f"{pv['fps']:g} fps" if pv.get("fps") else None),
        row("Dynamic range", _hdr_from_probe(pv)),
        row("Bit depth", f"{pv['bit_depth']}-bit" if pv.get("bit_depth") else None),
    ])
    streams = probe.get("audio") or []
    info.add("audio", "Audio", [row(f"Track {i + 1}", " · ".join(x for x in (
        language(a.get("language")), codec(a.get("codec")), channels(a.get("channels"), a.get("layout")),
        bitrate_text(a.get("bitrate"))) if x)) for i, a in enumerate(streams)]
        or [row("Codec", codec(r.get("audio_codec")))])
    info.add("source", "Source", [row("From", source_name(r.get("source"))), row("Library", library)])
    return info


def _probe_like(path: Any, size: Any) -> Optional[Dict[str, Any]]:
    from core.player import tracks as K
    return K.cached_like(_base(path) or "", size)


# ── youtube ──────────────────────────────────────────────────────────────

def youtube(item_id: Any, admin: bool) -> Info:
    check("youtube")
    from core.player import youtube as Y
    from core.player.sources.youtube import VIDEO_ID_CHARS
    vid = str(item_id).strip()
    if not (6 <= len(vid) <= 20) or not set(vid) <= VIDEO_ID_CHARS:
        raise Refused(400, "bad_id", "That isn't a YouTube video id.")
    db = _video_db()
    conn = db._get_connection()
    try:
        got = _first(conn, "SELECT * FROM video_download_history WHERE source = 'youtube' "
                           "AND outcome = 'completed' AND pruned_at IS NULL AND media_id = ? "
                           "ORDER BY completed_at DESC LIMIT 1", (vid,))
        listed = _maybe(lambda: _first(conn, "SELECT * FROM youtube_channel_videos WHERE youtube_id = ? "
                                             "LIMIT 1", (vid,)))
        own = _maybe(lambda: _first(conn, "SELECT * FROM channel_videos WHERE youtube_id = ?", (vid,)))
        if not (got or listed or own):
            raise Refused(404, "not_found", "That video isn't in your library.")
        channel_id = (got or {}).get("channel_id") or (listed or {}).get("channel_id")
        channel = _maybe(lambda: (_first(conn, "SELECT title FROM youtube_channel_meta WHERE channel_id = ?",
                                         (channel_id,)) or {}).get("title")) if channel_id else None
        dated = _maybe(lambda: (_first(conn, "SELECT published_at FROM youtube_video_dates WHERE youtube_id = ?",
                                       (vid,)) or {}).get("published_at"))
        stats = _maybe(lambda: _first(conn, "SELECT * FROM youtube_video_stats WHERE youtube_id = ?", (vid,))) or {}
        segments = _maybe(lambda: _rows(conn, "SELECT category, start_sec, end_sec FROM youtube_video_segments "
                                              "WHERE youtube_id = ? ORDER BY start_sec", (vid,)), [])
    finally:
        conn.close()
    title = (got or {}).get("title") or (listed or {}).get("title") or (own or {}).get("title") or "Video"
    seconds = (own or {}).get("duration_seconds") or Y.parse_duration((listed or {}).get("duration"))
    published = (got or {}).get("published_at") or (own or {}).get("published_at") or dated
    info = Info(title, " · ".join(x for x in (channel, date_text(published)) if x),
                Y.thumbnail(vid, (listed or {}).get("thumbnail_url") or (got or {}).get("poster_url")))
    info.add("details", "Details", [
        row("Title", title),
        row("Channel", channel),
        row("Published", date_text(published)),
        row("Runtime", runtime(seconds)),
        row("Views", f"{int(listed['view_count']):,}" if (listed or {}).get("view_count") else None),
        row("Likes", f"{int(stats['like_count']):,}" if stats.get("like_count") else None),
        *_progress_rows("youtube", vid, seconds),
    ])
    info.add("ids", "IDs", [ident("YouTube", vid), ident("Channel", channel_id)])
    if got:
        path = got.get("dest_path")
        probe = _maybe(lambda: _probe_like(path, got.get("size_bytes"))) or {}
        fmt = probe.get("format") or {}
        info.add("file", "File", [
            row("File name", _base(path)),
            row("Container", _ext(path)),
            row("Size", size_text(got.get("size_bytes"))),
            row("Duration", clock(seconds or fmt.get("duration"))),
            row("Overall bitrate", bitrate_text(fmt.get("bitrate") or _overall_bps(got.get("size_bytes"), seconds))),
            row("Quality", got.get("quality_label")),
            row("Downloaded", date_text(got.get("completed_at"))),
            row("Path", path, mono=True, copy=True) if admin else None,
        ])
        pv = (probe.get("video") or [{}])[0]
        info.add("video", "Video", [
            row("Codec", codec(pv.get("codec") or got.get("video_codec"))),
            row("Resolution", f"{pv['width']}×{pv['height']}" if pv.get("width") and pv.get("height")
                else resolution_label(got.get("resolution") or got.get("quality_label"))),
            row("Frame rate", f"{pv['fps']:g} fps" if pv.get("fps") else None),
            row("Bit depth", f"{pv['bit_depth']}-bit" if pv.get("bit_depth") else None),
        ])
        info.add("audio", "Audio", [row(f"Track {i + 1}", " · ".join(x for x in (
            language(a.get("language")), codec(a.get("codec")), channels(a.get("channels"), a.get("layout")),
            bitrate_text(a.get("bitrate"))) if x)) for i, a in enumerate(probe.get("audio") or [])])
    marks = []
    for cat, label in (("intro", "Intro"), ("outro", "Credits"), ("sponsor", "Sponsor")):
        found = [s for s in segments if s.get("category") == cat]
        if found:
            spans = ", ".join(f"{clock(s['start_sec']) or '0:00'} to {clock(s['end_sec'])}" for s in found[:3])
            marks.append(row(label, spans))
    info.add("chapters", "Chapters", marks)
    info.add("source", "Source", [row("From", "YouTube"),
                                  row("Plays from", "Your library" if got else "YouTube")])
    return info


# ── music ────────────────────────────────────────────────────────────────

def _kbps(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n // 1000 if n > 10_000 else n  # some scans keep bps


def _music_group(f: Dict[str, Any]) -> str:
    fmt = str(f.get("format") or _ext(f.get("path")) or "File").upper()
    if f.get("bit_depth") and f.get("sample_rate"):
        return f"{fmt} {f['bit_depth']}/{float(f['sample_rate']) / 1000:g}"
    kbps = _kbps(f.get("bitrate"))
    return f"{fmt} {kbps}" if kbps else fmt


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        out = json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
    return out if isinstance(out, type(default)) else default


def _gain(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return f"{float(text.split()[0]):+.2f} dB"
    except (TypeError, ValueError):
        return text


def track(item_id: Any, admin: bool) -> Info:
    check("track")
    from core.connect.may_play import may_play_track
    from core.player.sources.music import _scope, hides_explicit
    tid = _int_id(item_id, "track")
    db = _music_db()
    with db._get_connection() as conn:
        scope, hide = _scope(), hides_explicit()
        if not may_play_track(conn, tid, scope=scope, hide_explicit=hide):
            if hide and may_play_track(conn, tid, scope=scope, hide_explicit=False):
                raise Refused(403, "restricted", "That track isn't available on this profile.")
            raise Refused(404, "not_found", "That track isn't in your library.")
        t = _first(conn, "SELECT * FROM lib2_tracks WHERE id = ?", (tid,))
        if not t:
            raise Refused(404, "not_found", "That track isn't in your library.")
        al = _first(conn, "SELECT * FROM lib2_albums WHERE id = ?", (t["album_id"],)) or {}
        ar = _first(conn, "SELECT * FROM lib2_artists WHERE id = ?", (al.get("primary_artist_id"),)) or {}
        files = _rows(conn, """
            SELECT * FROM lib2_track_files
            WHERE track_id = ? AND COALESCE(import_status, 'imported') = 'imported'
              AND COALESCE(file_state, 'active') NOT IN ('deleted', 'quarantined')
            ORDER BY is_primary DESC, CASE WHEN quality_tier = 'lossless' THEN 0 ELSE 1 END,
                     COALESCE(bitrate, 0) DESC, id""", (tid,))
    artist = t.get("track_artist") or ar.get("name")
    seconds = (t.get("duration") or 0) / 1000 or None
    total = al.get("track_count") or al.get("expected_track_count")
    number = t.get("track_number")
    track_no = f"{number} of {total}" if number and total else number
    info = Info(t.get("title") or "Track", " · ".join(x for x in (artist, al.get("title")) if x),
                f"/api/library/v2/artwork/album/{t['album_id']}?size=thumb")
    info.add("details", "Details", [
        row("Title", t.get("title")),
        row("Artist", artist),
        row("Album", al.get("title")),
        row("Album artist", ar.get("name") if ar.get("name") and ar.get("name") != artist else None),
        row("Track", track_no),
        row("Disc", t.get("disc_number") if (t.get("disc_number") or 1) > 1 else None),
        row("Released", date_text(al.get("release_date")) or al.get("year")),
        row("Runtime", clock(seconds)),
        row("Genres", _json(al.get("genres"), [])),
        row("Label", al.get("label")),
        row("BPM", int(round(float(t["bpm"]))) if t.get("bpm") else None),
        row("Explicit", _yes(t.get("explicit"))),
        row("Added", date_text(t.get("added_at"))),
        row("Plays", t.get("play_count") or None),
        *_progress_rows("track", str(tid), seconds, t.get("last_played")),
    ])
    info.add("ids", "IDs", [
        ident("ISRC", t.get("isrc")),
        ident("MusicBrainz recording", t.get("musicbrainz_id")),
        ident("MusicBrainz release", al.get("musicbrainz_id")),
        ident("MusicBrainz release group", al.get("musicbrainz_release_group_id")),
        ident("MusicBrainz artist", ar.get("musicbrainz_id")),
        ident("UPC", al.get("upc")),
        ident("Spotify", t.get("spotify_id")),
    ])
    groups: Dict[str, int] = {}
    for f in files:
        group = None
        if len(files) > 1:
            group = _music_group(f)
            groups[group] = groups.get(group, 0) + 1
            if groups[group] > 1:
                group = f"{group} ({groups[group]})"
        tags = _json(f.get("tags_json"), {})
        path = f.get("path")
        info.add("file", "File", [
            row("File name", _base(path)),
            row("Container", str(f.get("format") or "").upper() or _ext(path)),
            row("Size", size_text(f.get("size"))),
            row("Duration", clock(seconds)),
            row("Got from", source_name(f.get("source"))),
            row("Added", date_text(f.get("added_at"))),
            row("Path", path, mono=True, copy=True) if admin else None,
            row("Server", source_name(f.get("server_source") or t.get("server_source")), mono=True, copy=True)
            if admin else None,
        ], group)
        kbps = _kbps(f.get("bitrate"))
        info.add("audio", "Audio", [
            row("Format", codec(f.get("format")) or _ext(path)),
            row("Bitrate", f"{kbps} kbps" if kbps else bitrate_text(_overall_bps(f.get("size"), seconds))),
            row("Sample rate", f"{float(f['sample_rate']) / 1000:g} kHz" if f.get("sample_rate") else None),
            row("Bit depth", f"{f['bit_depth']}-bit" if f.get("bit_depth") else None),
            row("ReplayGain track", _gain(tags.get("replaygain_track_gain"))),
            row("ReplayGain album", _gain(tags.get("replaygain_album_gain"))),
        ], group)
    tags = _json(files[0].get("tags_json"), {}) if files else {}
    of = lambda n, total: f"{n} of {total}" if n and total else n  # noqa: E731
    info.add("tags", "Tags", [
        row("Title", tags.get("title")),
        row("Artist", tags.get("artist")),
        row("Album", tags.get("album")),
        row("Album artist", tags.get("albumartist")),
        row("Track", of(tags.get("track_number"), tags.get("total_tracks"))),
        row("Disc", of(tags.get("disc_number"), tags.get("total_discs"))),
        row("Year", tags.get("year")),
        row("Genre", tags.get("genre")),
        row("Embedded art", "Yes" if tags.get("cover") else ("No" if "cover" in tags else None)),
        row("Lyrics", "Yes" if tags.get("lyrics") else None),
    ])
    info.add("source", "Source", [row("From", source_name(t.get("server_source")) or ("Disk" if files else None)),
                                  row("Library", "Music")])
    return info


# ── audiobooks ───────────────────────────────────────────────────────────

def audiobook(item_id: Any, admin: bool) -> Info:
    check("audiobook")
    key = str(item_id).strip()
    if not key or len(key) > 200:
        raise Refused(400, "bad_id", "That isn't a book id.")
    b = _audiobook_db().get_library_entry(key)
    if not b:
        raise Refused(404, "not_found", "That book isn't in your library.")
    facts = b.get("metadata_json") if isinstance(b.get("metadata_json"), dict) else {}
    book = b.get("catalog_book") if isinstance(b.get("catalog_book"), dict) else {}
    files = sorted((p for p in (b.get("file_paths") or []) if isinstance(p, str)),
                   key=lambda p: os.path.basename(p).lower())
    seconds = facts.get("duration_seconds") or (b.get("runtime_minutes") or 0) * 60 or None
    series = b.get("series_title") or facts.get("series_title")
    seq = b.get("series_sequence") or facts.get("series_sequence")
    author = b.get("author") or facts.get("author")
    asin = b.get("catalog_asin") or (key if not key.startswith("local:") else None)
    info = Info(b.get("title") or "Book", author or "", f"/api/audiobooks/library/{key}/cover")
    info.add("details", "Details", [
        row("Title", b.get("title")),
        row("Author", author),
        row("Narrator", b.get("narrator") or facts.get("narrator")),
        row("Series", f"{series}, book {seq}" if series and seq else series),
        row("Runtime", runtime(seconds)),
        row("Released", date_text(book.get("release_date"))),
        row("Publisher", book.get("publisher") or book.get("publisher_name")),
        row("Language", language(facts.get("language") or book.get("language"))),
        row("Edition", str(facts.get("format_type") or "").capitalize() or None),
        row("Added", date_text(b.get("imported_at"))),
        *_progress_rows("audiobook", key, seconds),
    ])
    info.add("ids", "IDs", [ident("ASIN", asin)])
    size = b.get("size_bytes")
    info.add("file", "File", [
        row("File name", _base(files[0]) if len(files) == 1 else None),
        row("Files", str(len(files)) if len(files) > 1 else None),
        row("Container", str(b.get("audio_format") or "").upper() or (_ext(files[0]) if files else None)),
        row("Size", size_text(size)),
        row("Duration", clock(seconds)),
        row("Overall bitrate", bitrate_text(_overall_bps(size, seconds))),
        row("Added", date_text(b.get("imported_at"))),
        row("Path", b.get("path"), mono=True, copy=True) if admin else None,
    ])
    count = _maybe(lambda: _chapter_count(files))
    info.add("chapters", "Chapters", [row("Chapters", str(count) if count else None)])
    origin = {"soulsync": "SoulSync download", "download": "SoulSync download", "disk": "Found on disk",
              "scan": "Found on disk"}.get(str(b.get("origin") or b.get("source") or "").lower())
    info.add("source", "Source", [row("From", origin), row("Library", "Audiobooks")])
    return info


def _chapter_count(files: List[str]) -> Optional[int]:
    from core.player.sources import audiobook as A
    return A.known_chapters(files)


# ── podcasts ─────────────────────────────────────────────────────────────

def _podcast_check() -> None:
    from core.player.playable import _device, _may
    if _profile_id() is None:
        raise Refused(401, "no_profile", "Pick a profile first.")
    device = _device()
    if device is not None:
        from core.connect.pick import KIND_ITEM_TYPES
        if "podcast" not in KIND_ITEM_TYPES.get(device.get("kind") or "", ()):
            raise Refused(403, "not_for_this_app", "This app can't play that.")
    if not _may("podcasts.access"):
        raise Refused(403, "not_allowed", "That isn't part of your profile.")


def _cached_feed(feed_url: str) -> Optional[Dict[str, Any]]:
    """the podcast page's own copy of the feed, however old: never fetched here"""
    from api import podcasts as P
    hit = P._show_cache.get(feed_url)
    return hit.get("show") if hit else None


def podcast_episode(item_id: Any, admin: bool) -> Info:
    _podcast_check()
    from core.player.offline_catalog import episode_key
    pod, _, ep_key = str(item_id or "").partition(":")
    if not pod.isdigit() or not ep_key:
        raise Refused(400, "bad_id", "That isn't a podcast episode.")
    db = _music_db()
    subs = db.get_watchlist_podcasts(profile_id=_profile_id()) or []
    sub = next((p for p in subs if int(p.get("id") or 0) == int(pod)), None)
    if not sub:
        raise Refused(404, "not_found", "You don't follow that podcast.")
    feed_url = sub.get("feed_url") or ""
    show = _maybe(lambda: _cached_feed(feed_url)) or {}
    ep = next((e for e in show.get("episodes") or [] if episode_key(e) == ep_key), None) or {}

    def downloaded():
        with db._get_connection() as conn:
            return _first(conn, "SELECT * FROM downloaded_podcast_episodes WHERE feed_url = ? "
                                "AND pruned_at IS NULL AND (guid = ? OR enclosure_url = ? OR title = ?) "
                                "ORDER BY downloaded_at DESC LIMIT 1", (feed_url, ep_key, ep_key, ep_key))
    got = _maybe(downloaded)
    if show.get("episodes") and not ep and not got:
        raise Refused(404, "not_found", "That episode isn't in the feed any more.")
    show_title = show.get("title") or sub.get("title") or "Podcast"
    title = ep.get("title") or (got or {}).get("title") or "Episode"
    seconds = ep.get("duration_seconds")
    published = ep.get("pub_date") or (got or {}).get("pub_date")
    info = Info(title, " · ".join(x for x in (show_title, date_text(published)) if x),
                ep.get("artwork_url") or show.get("artwork_url") or sub.get("artwork_url"))
    number = ep.get("episode_number")
    info.add("details", "Details", [
        row("Title", title),
        row("Show", show_title),
        row("Author", ep.get("author") or show.get("author") or sub.get("author")),
        row("Episode", f"Season {ep['season']}, Episode {number}" if ep.get("season") and number else number),
        row("Published", date_text(published)),
        row("Runtime", runtime(seconds)),
        row("Kind", str(ep.get("episode_type") or "").capitalize() if ep.get("episode_type") not in
            (None, "", "full") else None),
        *_progress_rows("podcast_episode", ep_key, seconds),
    ])
    info.add("ids", "IDs", [ident("GUID", ep.get("guid") or (got or {}).get("guid"))])
    url = ep.get("enclosure_url") or (got or {}).get("enclosure_url")
    path = (got or {}).get("file_path")
    size = ep.get("enclosure_length")
    info.add("file", "File", [
        row("File name", _base(path) if path else None),
        row("Container", _ext(path or url)),
        row("Size", size_text(size)),
        row("Duration", clock(seconds)),
        row("Overall bitrate", bitrate_text(_overall_bps(size, seconds))),
        row("Downloaded", date_text((got or {}).get("downloaded_at"))),
        row("Path", path, mono=True, copy=True) if admin else None,
    ])
    info.add("audio", "Audio", [row("Format", ep.get("enclosure_type"))])
    info.add("source", "Source", [
        row("From", "Your library" if got else "The podcast's feed"),
        row("Feed", feed_url, mono=True, copy=True),
    ])
    return info


# ── one way in ───────────────────────────────────────────────────────────

BUILDERS: Dict[str, Callable[[Any, bool], Info]] = {
    "movie": movie, "episode": episode, "track": track, "audiobook": audiobook,
    "podcast_episode": podcast_episode, "home": home, "youtube": youtube,
}
ALIASES = {"podcast": "podcast_episode", "home_video": "home", "music": "track"}


def describe(item_type: str, item_id: Any, admin: bool = False) -> Dict[str, Any]:
    kind = ALIASES.get(str(item_type or "").strip().lower(), str(item_type or "").strip().lower())
    build = BUILDERS.get(kind)
    if build is None:
        raise Refused(400, "unknown_kind", "There's no info for that kind of thing.")
    text = str(item_id or "").strip()
    if not text or len(text) > 400:
        raise Refused(400, "bad_id", "That isn't an item id.")
    out = build(text, bool(admin)).json()
    return {"item_type": kind, "item_id": text, **out}
