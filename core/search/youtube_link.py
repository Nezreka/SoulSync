"""a pasted youtube link on the files search (#2322).

the box takes a youtube video or playlist link and shows exactly that video
(or the playlist's videos) instead of searching. only youtube hosts count, and
yt-dlp never sees the pasted text: the link is boiled down to an id and a
clean url is rebuilt from it. a download later rebuilds the url from the id
again (YouTubeClient.download), so nothing the user typed reaches yt-dlp.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from utils.logging_config import get_logger

logger = get_logger("search.youtube_link")

# the most videos one playlist link shows
PLAYLIST_CAP = 200

_VIDEO_HOSTS = frozenset({
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com',
})
_SHORT_HOSTS = frozenset({'youtu.be', 'www.youtu.be'})
_VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_LIST_ID = re.compile(r'^[A-Za-z0-9_-]{2,64}$')
# path forms that carry the id as the next segment
_ID_PATHS = ('shorts', 'live', 'embed', 'v')
# placeholders yt-dlp keeps in a flat playlist for videos you can't watch
_GONE_TITLES = frozenset({'[private video]', '[deleted video]', '[unavailable video]'})


def parse_youtube_url(value: Any) -> Optional[dict]:
    """``{"kind": "video"|"playlist", "id": str}`` for a youtube link, else None.

    a watch link that also names a list is the video: that's what was on
    screen, and a mix list (RD...) never ends. only a /playlist link is a
    playlist. no userinfo, no odd ports, no other hosts.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or len(raw) > 2048 or any(c.isspace() for c in raw):
        return None
    candidate = raw if '://' in raw else f'https://{raw}'
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in ('http', 'https'):
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if port not in (None, 80, 443):
        return None
    host = (parsed.hostname or '').lower().rstrip('.')
    query = parse_qs(parsed.query)
    segments = [s for s in parsed.path.split('/') if s]

    def first(key):
        values = query.get(key) or []
        return values[0] if values else ''

    if host in _SHORT_HOSTS:
        video = segments[0] if len(segments) == 1 else ''
        return {'kind': 'video', 'id': video} if _VIDEO_ID.match(video) else None
    if host not in _VIDEO_HOSTS:
        return None
    if segments == ['watch']:
        video = first('v')
        return {'kind': 'video', 'id': video} if _VIDEO_ID.match(video) else None
    if len(segments) == 2 and segments[0] in _ID_PATHS:
        video = segments[1]
        return {'kind': 'video', 'id': video} if _VIDEO_ID.match(video) else None
    if segments == ['playlist']:
        listed = first('list')
        return {'kind': 'playlist', 'id': listed} if _LIST_ID.match(listed) else None
    return None


def is_youtube_url(value: Any) -> bool:
    return parse_youtube_url(value) is not None


def canonical_url(link: Mapping[str, str]) -> str:
    """the clean url yt-dlp is given, rebuilt from the id alone."""
    if link['kind'] == 'playlist':
        return f"https://www.youtube.com/playlist?list={link['id']}"
    return f"https://www.youtube.com/watch?v={link['id']}"


def _thumbnail(entry: Mapping[str, Any]) -> Optional[str]:
    thumb = entry.get('thumbnail')
    if thumb:
        return thumb
    thumbs = entry.get('thumbnails')
    if isinstance(thumbs, list) and thumbs:
        last = thumbs[-1]
        if isinstance(last, Mapping):
            return last.get('url')
    return None


def _extract(url: str, *, playlist: bool) -> Optional[dict]:
    """yt-dlp metadata only, never a download."""
    import yt_dlp

    from core.youtube_client import _resolve_cookie_opts

    opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': not playlist,
    }
    if playlist:
        opts['extract_flat'] = 'in_playlist'
        opts['playlistend'] = PLAYLIST_CAP + 1
    opts.update(_resolve_cookie_opts())
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def _row(client, entry: Mapping[str, Any]) -> dict:
    """one video as a files-search track row, ready for /api/download."""
    from core.search.basic import _quality_score

    best = None
    formats = entry.get('formats')
    if formats:
        try:
            best = client._get_best_audio_format(formats)
        except Exception as exc:  # noqa: BLE001 - the row still downloads
            logger.debug("youtube link: no best format: %s", exc)
    track = client._youtube_to_track_result(dict(entry), best)
    row = track.__dict__.copy()
    row['result_type'] = 'track'
    row['quality_score'] = _quality_score(track)
    row['thumbnail'] = _thumbnail(entry)
    row['video_id'] = entry.get('id')
    row['channel'] = entry.get('channel') or entry.get('uploader') or ''
    row['video_title'] = entry.get('title') or ''
    return row


def resolve_youtube_link(
    raw: Any,
    client,
    *,
    extract: Optional[Callable[..., Optional[dict]]] = None,
) -> dict:
    """what a pasted link points at. raises ValueError for anything that isn't
    a youtube link, LookupError when youtube can't find it."""
    link = parse_youtube_url(raw)
    if link is None:
        raise ValueError("That isn't a YouTube video or playlist link.")
    extract = extract or _extract
    url = canonical_url(link)
    info = extract(url, playlist=link['kind'] == 'playlist')
    if not info:
        raise LookupError("YouTube didn't find that link.")

    if link['kind'] == 'video':
        if not _VIDEO_ID.match(str(info.get('id') or '')):
            info = {**info, 'id': link['id']}
        row = _row(client, info)
        return {
            'kind': 'video', 'id': link['id'], 'url': url,
            'title': info.get('title') or '', 'channel': row['channel'],
            'thumbnail': row['thumbnail'], 'results': [row],
            'count': 1, 'truncated': False,
        }

    rows = []
    entries = [e for e in (info.get('entries') or []) if isinstance(e, Mapping)]
    for entry in entries:
        video = str(entry.get('id') or '')
        if not _VIDEO_ID.match(video):
            continue
        if str(entry.get('title') or '').strip().lower() in _GONE_TITLES:
            continue
        rows.append(_row(client, entry))
        if len(rows) >= PLAYLIST_CAP:
            break
    return {
        'kind': 'playlist', 'id': link['id'], 'url': url,
        'title': info.get('title') or '',
        'channel': info.get('channel') or info.get('uploader') or '',
        'thumbnail': _thumbnail(info) or (rows[0]['thumbnail'] if rows else None),
        'results': rows, 'count': len(rows),
        'truncated': len(entries) > PLAYLIST_CAP,
    }
