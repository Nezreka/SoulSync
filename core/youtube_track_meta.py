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

This module is the single, pure place that decides which signal wins, so the
precedence is unit-testable instead of buried in the web_server endpoint. It
deliberately does NOT fall back to the channel/uploader as the artist — on a
playlist that's the owner, and mislabelling every track is worse than an honest
"Unknown Artist" (which downstream MusicBrainz discovery can still try to fix).
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Tuple

# Trailing "- Topic" on an auto-generated YouTube Music channel.
_TOPIC_RE = re.compile(r'\s*-\s*topic\s*$', re.IGNORECASE)

# "Artist - Title": a hyphen/en-dash/em-dash flanked by spaces, both sides
# non-empty. Splits on the FIRST such separator so "A - B (C Remix)" → ("A",
# "B (C Remix)"). Spaces around the dash are required so hyphenated names like
# "Jean-Michel Jarre" aren't split.
_TITLE_SPLIT_RE = re.compile(r'^\s*(?P<artist>.+?)\s+[-–—]\s+(?P<title>.+?)\s*$')


def _first_music_field(entry: Mapping[str, Any]) -> str:
    """First non-empty value from yt-dlp's music-metadata fields."""
    artists = entry.get('artists')
    if isinstance(artists, (list, tuple)):
        for a in artists:
            s = str(a or '').strip()
            if s:
                return s
    for key in ('artist', 'creator'):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ''


def derive_artist_and_title(entry: Mapping[str, Any]) -> Tuple[str, str]:
    """Return ``(artist, title)`` from a yt-dlp (flat) playlist entry.

    ``artist`` is ``''`` when no reliable signal exists — the caller defaults
    that to "Unknown Artist" rather than using the playlist owner's channel
    (#863). ``title`` is the raw video title, except when an "Artist - Title"
    split provided the artist, in which case it's the right-hand side.
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
        stripped = _TOPIC_RE.sub('', channel).strip()
        if stripped:
            return stripped, title

    # 3. "<Artist> - <Title>" embedded in the title.
    m = _TITLE_SPLIT_RE.match(title)
    if m:
        artist = m.group('artist').strip()
        rest = m.group('title').strip()
        if artist and rest:
            return artist, rest

    # 4. No reliable artist signal — caller defaults to "Unknown Artist".
    return '', title


__all__ = ['derive_artist_and_title']
