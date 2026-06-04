"""Normalize streaming/YouTube-style source track metadata for matching.

Issue #768: source playlists — especially ones seeded from YouTube — carry
video-style metadata: the title is ``"Artist - Song"`` and the artist is a
channel name like ``"Official Arctic Monkeys"``, ``"Arctic Monkeys - Topic"``,
or ``"ColdplayVEVO"``. The library/media-server side has the clean ``"Song"`` /
``"Arctic Monkeys"``. Both matching paths (the sync confidence scorer and the
playlist-editor reconcile) then fail to pair them — the track is reported
"not matched" / shows up as an orphan "extra" even though it exists.

These helpers strip that channel/video decoration so the cleaned source can be
compared against the clean library metadata. Pure, no I/O.

Conservative by construction:
- ``strip_artist_prefix`` removes a leading ``"<artist><sep>"`` only when the
  prefix EQUALS the artist we're matching against. So ``"Death - Pull the
  Plug"`` by ``"Death"`` is helped, while ``"Marvin Gaye"`` by Charlie Puth
  (title is not ``"Charlie Puth - ..."``) is left untouched, and a hyphenated
  word like ``"Self-Titled"`` is never split (a separator needs surrounding
  whitespace, or a colon).
- ``clean_source_artist`` only removes well-known channel decorations.

Both are intended to be applied as ADDITIONAL match candidates (best-of), so
an over-eager strip can only add a comparison, never remove the original.
"""

from __future__ import annotations

import re

from core.text.normalize import normalize_for_comparison

# Artist/title separator: a dash/pipe/tilde flanked by whitespace, OR a colon
# (with optional trailing space). Whitespace-flanking keeps "Self-Titled" and
# "Jay-Z" intact while still splitting "Artist - Title".
_SEP_SPLIT = re.compile(r"\s+[-–—|~]\s+|\s*:\s+")

# YouTube auto-generated artist channel: "Arctic Monkeys - Topic".
_TOPIC_SUFFIX = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
# "Official " / "The Official " channel prefix.
_OFFICIAL_PREFIX = re.compile(r"^\s*(?:the\s+)?official\s+", re.IGNORECASE)
# Trailing VEVO, attached ("ColdplayVEVO") or spaced ("Coldplay VEVO").
_VEVO_SUFFIX = re.compile(r"\s*vevo\s*$", re.IGNORECASE)


def clean_source_artist(artist: str) -> str:
    """Strip well-known streaming-channel decoration from an artist name.

    ``"Official Arctic Monkeys"`` → ``"Arctic Monkeys"``;
    ``"Arctic Monkeys - Topic"`` → ``"Arctic Monkeys"``;
    ``"ColdplayVEVO"`` → ``"Coldplay"``. Returns the input unchanged when
    nothing matches, and never returns empty for non-empty input."""
    if not artist:
        return artist
    s = artist.strip()

    topic = _TOPIC_SUFFIX.sub("", s).strip()
    if topic and topic != s:
        s = topic

    official = _OFFICIAL_PREFIX.sub("", s).strip()
    if official:
        s = official

    # Only strip VEVO if at least 2 chars of name remain (don't empty "VEVO").
    vevo = _VEVO_SUFFIX.sub("", s).strip()
    if len(vevo) >= 2 and vevo != s:
        s = vevo

    return s or artist


def strip_artist_prefix(title: str, artist: str) -> str:
    """Remove a leading ``"<artist><separator>"`` from ``title`` when the prefix
    equals ``artist`` (accent/case-folded). Otherwise return ``title`` unchanged.

    ``("Arctic Monkeys - Do I Wanna Know?", "Arctic Monkeys")`` → ``"Do I Wanna
    Know?"``. Never returns an empty string."""
    if not title or not artist:
        return title
    na = normalize_for_comparison(artist)
    if not na:
        return title
    parts = _SEP_SPLIT.split(title, maxsplit=1)
    if len(parts) == 2:
        left, right = parts
        right = right.strip()
        if right and normalize_for_comparison(left) == na:
            return right
    return title


def canonical_source_track(title: str, artist: str) -> tuple[str, str]:
    """Best-effort clean (title, artist) for matching a streaming/YouTube source
    against clean library metadata. Cleans the artist first, then strips a
    leading artist prefix from the title using EITHER the cleaned or the raw
    artist (YouTube titles prepend the real artist, not the channel name)."""
    cleaned_artist = clean_source_artist(artist)
    new_title = strip_artist_prefix(title, cleaned_artist)
    if new_title == title and cleaned_artist != artist:
        new_title = strip_artist_prefix(title, artist)
    return new_title, cleaned_artist


__all__ = ["clean_source_artist", "strip_artist_prefix", "canonical_source_track"]
