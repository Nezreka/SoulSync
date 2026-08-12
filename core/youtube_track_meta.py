"""Derive a track's artist + title from a yt-dlp playlist entry.

Flat playlist extraction (used to dodge YouTube rate limits) gives sparse
per-entry data: often just ``title``, ``id``, ``duration``, and an
``uploader``/``channel`` that — for a playlist like "Likes" — is the PLAYLIST
OWNER, not the track artist. GitHub #863: every track came out as the owner
("Wing It"), or "Unknown Artist" when ``uploader`` was absent, because the
parser used ``entry['uploader']`` as the artist.

The artist is usually recoverable from one of, in priority order:

1. yt-dlp music-metadata fields (``artists`` / ``artist`` / ``creator``),
   populated for YouTube Music tracks.
2. An auto-generated ``"<Artist> - Topic"`` channel name.
3. The classic ``"<Artist> - <Title>"`` form embedded in the video title.
4. The per-entry channel — but ONLY on music.youtube.com (see below).

This module is the single, pure place that decides which signal wins, so the
precedence is unit-testable instead of buried in the web_server endpoint. It
deliberately does NOT fall back to the channel/uploader as the artist — on a
youtube.com playlist that's the owner, and mislabelling every track is worse
than an honest "Unknown Artist" (which downstream MusicBrainz discovery can
still try to fix).

That #863 rule is right for youtube.com and wrong for music.youtube.com. On a
YT Music playlist every flat entry carries its OWN channel — the artist's
channel — not the playlist owner's, because YT Music serves one channel per
track rather than one per playlist. Measured on a 69-track YT Music playlist:
68 entries carried the correct artist in ``channel``, and all 69 were stored as
"Unknown Artist" because this module refused to look. So the fallback is
available behind ``allow_channel_artist``, which the caller sets from the
playlist URL's host — never inferred here, since an entry alone cannot tell you
which kind of playlist it came from.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Tuple
from urllib.parse import urlparse

# Hosts whose playlists serve a per-TRACK channel (the artist), not the
# playlist owner's channel. Bare "youtube.com" is deliberately absent.
_MUSIC_HOSTS = frozenset({'music.youtube.com', 'music.youtube.co.uk'})

# Trailing "- Topic" on an auto-generated YouTube Music channel.
_TOPIC_RE = re.compile(r'\s*-\s*topic\s*$', re.IGNORECASE)

# "Artist - Title": a hyphen/en-dash/em-dash flanked by spaces, both sides
# non-empty. Splits on the FIRST such separator so "A - B (C Remix)" → ("A",
# "B (C Remix)"). Spaces around the dash are required so hyphenated names like
# "Jean-Michel Jarre" aren't split.
_TITLE_SPLIT_RE = re.compile(r'^\s*(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+?)\s*$')


def strip_topic_suffix(name: Any) -> str:
    """Strip a trailing ``" - Topic"`` from an auto-generated channel name.

    Shared so the yt-dlp path and the YouTube Music catalog path agree: the
    catalog hands back the raw channel name for artists without a canonical
    entry, so ``"Example Band - Topic"`` reaches the mirror and never matches the
    ``Example Band`` already in the library. Returns the input stripped of the suffix,
    or unchanged when there isn't one.
    """
    text = str(name or '').strip()
    if not text:
        return ''
    stripped = _TOPIC_RE.sub('', text).strip()
    # A channel literally named "- Topic" would strip to nothing; keep the
    # original rather than inventing an empty artist.
    return stripped or text


def _first_music_field(entry: Mapping[str, Any]) -> str:
    """First non-empty value from yt-dlp's music-metadata fields.

    ``creators`` (plural) rides alongside ``artists``: current yt-dlp emits it
    as a key on flat entries — always None today, but only the plural form
    will carry a value if upstream ever populates it (PR #1136's author
    caught the singular-only lookup silently missing it)."""
    for list_key in ('artists', 'creators'):
        values = entry.get(list_key)
        if isinstance(values, (list, tuple)):
            for a in values:
                s = str(a or '').strip()
                if s:
                    return s
    for key in ('artist', 'creator'):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def is_music_youtube_url(url: Any) -> bool:
    """True when ``url`` points at music.youtube.com.

    Drives ``allow_channel_artist``. Parsed rather than substring-matched so a
    youtube.com URL that merely mentions the string (a ``?next=`` parameter, a
    channel named "music.youtube.com") can't turn the fallback on.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        host = (urlparse(url.strip()).hostname or '').lower()
    except ValueError:
        return False
    return host in _MUSIC_HOSTS


def derive_artist_and_title(
    entry: Mapping[str, Any], allow_channel_artist: bool = False
) -> Tuple[str, str]:
    """Return ``(artist, title)`` from a yt-dlp (flat) playlist entry.

    ``artist`` is ``''`` when no reliable signal exists — the caller defaults
    that to "Unknown Artist" rather than using the playlist owner's channel
    (#863). ``title`` is the raw video title, except when an "Artist - Title"
    split provided the artist, in which case it's the right-hand side.

    ``allow_channel_artist`` opts into the plain channel/uploader as a
    last-resort artist. Pass ``is_music_youtube_url(playlist_url)`` — on
    music.youtube.com the per-entry channel IS the artist; on youtube.com it is
    the playlist owner and must stay off (#863). It is the LAST signal tried, so
    turning it on can only fill in tracks that would otherwise have been
    "Unknown Artist" — it never overrides a better one.
    """
    if not isinstance(entry, Mapping):
        return '', 'Unknown Track'

    title = str(entry.get('title') or '').strip() or 'Unknown Track'

    # 1. Music-metadata fields (YouTube Music).
    field_artist = _first_music_field(entry)
    if field_artist:
        return field_artist, title

    # 2. "<Artist> - Topic" auto-channel — the channel name IS the artist.
    channel = str(entry.get('uploader') or entry.get('channel') or '').strip()
    if _TOPIC_RE.search(channel):
        stripped = strip_topic_suffix(channel)
        if stripped and stripped != channel:
            return stripped, title

    # 3. "<Artist> - <Title>" embedded in the title.
    m = _TITLE_SPLIT_RE.match(title)
    if m:
        artist = m.group('artist').strip()
        rest = m.group('title').strip()
        if artist and rest:
            return artist, rest

    # 4. Plain channel — the artist on music.youtube.com, the playlist owner on
    #    youtube.com. Only the caller knows which, hence the flag.
    if allow_channel_artist and channel:
        return channel, title

    # 5. No reliable artist signal — caller defaults to "Unknown Artist".
    return '', title


__all__ = ['derive_artist_and_title', 'is_music_youtube_url', 'strip_topic_suffix']
