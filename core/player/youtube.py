"""youtube for the /player app, read from what soulsync already keeps.

three sources, all in the video db:
  * the channels a profile follows (video_watchlist, kind 'channel', source
    'youtube'), with their cached meta (youtube_channel_meta);
  * the channel catalogs the follow cache keeps (youtube_channel_videos,
    youtube_video_dates for upload dates): what can play as an embed;
  * the download ledger (video_download_history, source 'youtube',
    completed and not pruned): what is on disk and plays from the library.

nothing here calls youtube. a video is "downloaded" when the ledger has it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

LATEST_MAX = 60
DOWNLOADS_MAX = 120
CHANNEL_VIDEOS_MAX = 400


def parse_duration(text: Any) -> Optional[int]:
    """'1:02:03' / '32:08' / '45' as seconds, None for anything else."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text) if text > 0 else None
    parts = str(text).strip().split(":")
    if not parts or not all(p.isdigit() for p in parts) or len(parts) > 3:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return seconds or None


def thumbnail(youtube_id: str, url: Any = None) -> str:
    """the cached thumbnail, else youtube's own 16:9 still for the id."""
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return f"https://i.ytimg.com/vi/{youtube_id}/mqdefault.jpg"


def downloaded_ids(conn) -> Dict[str, Dict[str, Any]]:
    """youtube id -> its ledger row, for every video still on disk."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in conn.execute("""
            SELECT media_id, title, channel_id, published_at, completed_at, poster_url,
                   size_bytes, quality_label
            FROM video_download_history
            WHERE source = 'youtube' AND outcome = 'completed' AND pruned_at IS NULL
              AND media_id IS NOT NULL
            ORDER BY completed_at DESC"""):
        if r[0] not in out:
            out[r[0]] = {"title": r[1], "channel_id": r[2], "published_at": r[3],
                         "completed_at": r[4], "poster_url": r[5], "size_bytes": r[6],
                         "quality": r[7]}
    return out


def _meta(conn) -> Dict[str, Dict[str, Any]]:
    return {r[0]: {"title": r[1], "handle": r[2], "avatar": r[3], "banner": r[4],
                   "subscribers": r[5], "description": r[6]}
            for r in conn.execute("""SELECT channel_id, title, handle, avatar_url, banner_url,
                                            subscriber_count, description
                                     FROM youtube_channel_meta""")}


def followed(conn, profile_id: int) -> Dict[str, Dict[str, Any]]:
    return {r[0]: {"title": r[1], "avatar": r[2], "followed_at": r[3]}
            for r in conn.execute("""SELECT source_id, title, poster_url, date_added
                                     FROM video_watchlist
                                     WHERE kind = 'channel' AND source = 'youtube'
                                       AND state = 'follow' AND profile_id = ?
                                       AND source_id IS NOT NULL""", (int(profile_id),))}


def _video(row, channel_title: Optional[str], ledger: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    vid = row["youtube_id"]
    on_disk = ledger.get(vid)
    return {
        "id": vid,
        "title": row["title"] or (on_disk or {}).get("title") or "",
        "channel_id": row["channel_id"],
        "channel_title": channel_title,
        "thumbnail": thumbnail(vid, row["thumbnail_url"]),
        "duration_seconds": parse_duration(row["duration"]),
        "published_at": row["published_at"],
        "views": row["view_count"],
        "downloaded": on_disk is not None,
    }


def _ledger_video(vid: str, entry: Dict[str, Any], channel_title: Optional[str]) -> Dict[str, Any]:
    return {
        "id": vid,
        "title": entry.get("title") or "",
        "channel_id": entry.get("channel_id"),
        "channel_title": channel_title,
        "thumbnail": thumbnail(vid, entry.get("poster_url")),
        "duration_seconds": None,
        "published_at": entry.get("published_at"),
        "views": None,
        "downloaded": True,
        "downloaded_at": entry.get("completed_at"),
    }


def home(conn, profile_id: int) -> Dict[str, Any]:
    """the youtube page: followed channels (and any channel you have
    downloads from), the newest uploads across followed channels, and the
    newest downloads."""
    meta = _meta(conn)
    follows = followed(conn, profile_id)
    ledger = downloaded_ids(conn)

    counts = {r[0]: (r[1], r[2]) for r in conn.execute("""
        SELECT v.channel_id, COUNT(*), MAX(d.published_at)
        FROM youtube_channel_videos v LEFT JOIN youtube_video_dates d ON d.youtube_id = v.youtube_id
        GROUP BY v.channel_id""")}
    on_disk_by_channel: Dict[str, int] = {}
    for entry in ledger.values():
        cid = entry.get("channel_id")
        if cid:
            on_disk_by_channel[cid] = on_disk_by_channel.get(cid, 0) + 1

    channel_ids = list(dict.fromkeys([*follows.keys(), *on_disk_by_channel.keys()]))
    channels = []
    for cid in channel_ids:
        m = meta.get(cid, {})
        f = follows.get(cid, {})
        total, latest = counts.get(cid, (0, None))
        channels.append({
            "id": cid,
            "title": m.get("title") or f.get("title") or cid,
            "handle": m.get("handle"),
            "avatar": m.get("avatar") or f.get("avatar"),
            "banner": m.get("banner"),
            "subscribers": m.get("subscribers"),
            "followed": cid in follows,
            "video_count": total,
            "downloaded_count": on_disk_by_channel.get(cid, 0),
            "latest_at": latest,
        })
    channels.sort(key=lambda c: (c["latest_at"] or ""), reverse=True)
    titles = {c["id"]: c["title"] for c in channels}

    latest: List[Dict[str, Any]] = []
    if follows:
        ids = list(follows)
        rows = conn.execute(
            """SELECT v.channel_id, v.youtube_id, v.title, v.thumbnail_url, v.duration, v.view_count,
                      d.published_at
               FROM youtube_channel_videos v JOIN youtube_video_dates d ON d.youtube_id = v.youtube_id
               WHERE v.channel_id IN (%s) AND d.published_at IS NOT NULL
               ORDER BY d.published_at DESC LIMIT ?""" % ",".join("?" for _ in ids),
            [*ids, LATEST_MAX]).fetchall()
        latest = [_video(r, titles.get(r["channel_id"]), ledger) for r in rows]

    downloads = [_ledger_video(vid, entry, titles.get(entry.get("channel_id")) or
                               (meta.get(entry.get("channel_id") or "", {}).get("title")))
                 for vid, entry in list(ledger.items())[:DOWNLOADS_MAX]]
    # durations for downloads come from the channel catalog when it has them
    if downloads:
        ids = [d["id"] for d in downloads]
        found = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT youtube_id, duration, view_count FROM youtube_channel_videos WHERE youtube_id IN (%s)"
            % ",".join("?" for _ in ids), ids)}
        for d in downloads:
            hit = found.get(d["id"])
            if hit:
                d["duration_seconds"] = parse_duration(hit[0])
                d["views"] = hit[1]

    return {"channels": channels, "latest": latest, "downloads": downloads}


def channel(conn, channel_id: str, profile_id: int) -> Optional[Dict[str, Any]]:
    meta = _meta(conn).get(channel_id)
    follows = followed(conn, profile_id)
    ledger = downloaded_ids(conn)
    rows = conn.execute(
        """SELECT v.channel_id, v.youtube_id, v.title, v.thumbnail_url, v.duration, v.view_count,
                  d.published_at
           FROM youtube_channel_videos v LEFT JOIN youtube_video_dates d ON d.youtube_id = v.youtube_id
           WHERE v.channel_id = ?
           ORDER BY d.published_at IS NULL, d.published_at DESC LIMIT ?""",
        (channel_id, CHANNEL_VIDEOS_MAX)).fetchall()
    on_disk = {vid: e for vid, e in ledger.items() if e.get("channel_id") == channel_id}
    if meta is None and not rows and not on_disk and channel_id not in follows:
        return None
    title = (meta or {}).get("title") or follows.get(channel_id, {}).get("title") or channel_id
    videos = [_video(r, title, ledger) for r in rows]
    seen = {v["id"] for v in videos}
    # downloads the catalog cache doesn't list (older uploads) still belong here
    videos += [_ledger_video(vid, e, title) for vid, e in on_disk.items() if vid not in seen]
    return {
        "channel": {
            "id": channel_id,
            "title": title,
            "handle": (meta or {}).get("handle"),
            "avatar": (meta or {}).get("avatar") or follows.get(channel_id, {}).get("avatar"),
            "banner": (meta or {}).get("banner"),
            "subscribers": (meta or {}).get("subscribers"),
            "description": (meta or {}).get("description"),
            "followed": channel_id in follows,
            "downloaded_count": len(on_disk),
        },
        "videos": videos,
    }
