"""Direct-ID detection for manual matching (Ashh's request).

The manual-match modal fuzzy-searches a service and shows the top 8 hits.
When the right release isn't in those 8 (common name like "Idols"), the user
is stuck. But they often already KNOW the exact ID — so let them paste it
and match directly instead of fighting the search ranking.

This module is the pure detector: given a service + the text the user typed,
return the canonical ID if the text *is* an ID (bare or pasted as a URL/URI),
else None. No network, no I/O — the caller decides whether to do a direct
lookup or fall through to the normal fuzzy search.

Conservative by design: only return an ID when the text matches that
service's ID shape unambiguously. Anything else returns None so a normal
text search still runs (pasting "Idols" never looks like an ID).
"""

from __future__ import annotations

import re
from typing import Optional

# MusicBrainz MBIDs are UUIDs (8-4-4-4-12 hex). Accept a bare UUID or one
# embedded in a musicbrainz.org URL (/artist/<id>, /release/<id>, ...).
_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def extract_direct_id(service: str, entity_type: str, query: str) -> Optional[str]:
    """Return the canonical service ID if ``query`` is one, else None.

    ``entity_type`` is accepted for future per-type shapes (e.g. Spotify
    track vs album URLs); MusicBrainz UUIDs are type-agnostic so it's unused
    there today. Deezer URLs carry their kind in the path; a bare number is
    treated as the id for the modal's ``entity_type``."""
    if not query:
        return None
    text = query.strip()
    if not text:
        return None

    service = (service or "").strip().lower()

    if service == "musicbrainz":
        # Bare UUID, or a MB URL — but ONLY a UUID. A search like "Idols"
        # can't match, so normal search is never hijacked.
        m = _UUID_RE.search(text)
        if m and (text == m.group(1) or "musicbrainz.org" in text.lower()):
            return m.group(1).lower()
        return None

    if service == "deezer":
        # A Deezer album/track/artist URL, or a bare numeric id (the modal
        # already knows entity_type, so a number isn't ambiguous the way a
        # bare MB-less UUID would be).
        link = extract_deezer_link(text)
        if link:
            return link[1]
        if text.isdigit() and len(text) >= 3:
            return text
        return None

    ref = parse_direct_ref(service, entity_type, text)
    return ref[1] if ref else None


# deezer.com/{optional locale}/album|track|artist/<digits>
# Locale is us / en / en-gb — same shapes core.search.by_id already accepts.
_DEEZER_PATH_RE = re.compile(
    r"deezer\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(album|track|artist)/(\d+)",
    re.IGNORECASE,
)


def extract_deezer_link(query: str) -> Optional[tuple[str, str]]:
    """``(kind, id)`` from a Deezer album/track/artist URL, else None.

    Pure — no network. Short links (``link.deezer.com``) have no id in the
    path and are rejected so the caller can fall through to text search.
    """
    if not query:
        return None
    m = _DEEZER_PATH_RE.search(query.strip())
    if not m:
        return None
    return (m.group(1).lower(), m.group(2))


# ── every other service (#1901) ──────────────────────────────────────────────
#
# Same rule as above: a link or an id in that service's own shape, or nothing.
# Each parser answers ``(kind, id)``. ``kind`` is what the LINK says it points
# at (artist / album / track), or None for a bare id, which means "whatever the
# modal is matching". The lookup decides what to do when a link's kind isn't
# the one being matched (a Spotify track link while matching its album).

_SPOTIFY_URL_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(?:embed/)?(artist|album|track)/([A-Za-z0-9]{22})",
    re.IGNORECASE,
)
_SPOTIFY_URI_RE = re.compile(r"^spotify:(artist|album|track):([A-Za-z0-9]{22})$", re.IGNORECASE)
_SPOTIFY_BARE_RE = re.compile(r"^[A-Za-z0-9]{22}$")

# music.apple.com/us/album/<slug>/<id>?i=<track id>, /artist/<slug>/<id>,
# /song/<slug>/<id>; the old itunes.apple.com/.../id<digits> links too.
_APPLE_URL_RE = re.compile(
    r"(?:music|itunes)\.apple\.com/(?:[a-z]{2}/)?(artist|album|song)/(?:[^/?#]+/)?(?:id)?(\d+)",
    re.IGNORECASE,
)
_APPLE_TRACK_PARAM_RE = re.compile(r"[?&]i=(\d+)")

_DISCOGS_URL_RE = re.compile(
    r"discogs\.com/(?:[a-z]{2}/)?(?:[^/]+/)?(artist|release|master)/(\d+)", re.IGNORECASE,
)
# the bracket notation Discogs uses in its own text: [a123] [r123] [m123]
_DISCOGS_TAG_RE = re.compile(r"^\[?([arm])(\d+)\]?$", re.IGNORECASE)

_TIDAL_URL_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:album/\d+/)?(artist|album|track)/(\d+)", re.IGNORECASE,
)

_QOBUZ_OPEN_RE = re.compile(
    r"(?:open|play)\.qobuz\.com/(artist|album|track)/([A-Za-z0-9]+)", re.IGNORECASE,
)
# www.qobuz.com/us-en/album/<slug>/<id>, /interpreter/<slug>/<id> (an artist)
_QOBUZ_SHOP_RE = re.compile(
    r"qobuz\.com/[a-z]{2}-[a-z]{2}/(album|interpreter)/[^/?#]+/([A-Za-z0-9]+)", re.IGNORECASE,
)

_AMAZON_URL_RE = re.compile(
    r"music\.amazon\.[a-z.]+/(albums|tracks)/([A-Z0-9]{10})", re.IGNORECASE,
)
_AMAZON_TRACK_PARAM_RE = re.compile(r"[?&]trackAsin=([A-Z0-9]{10})", re.IGNORECASE)
_ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")

_AUDIODB_URL_RE = re.compile(r"theaudiodb\.com/(artist|album|track)/(\d+)", re.IGNORECASE)

_JIOSAAVN_URL_RE = re.compile(
    r"jiosaavn\.com/(artist|album|song)/[^/?#]+/([A-Za-z0-9_-]+)", re.IGNORECASE,
)

# last.fm/music/<artist>, /music/<artist>/<album>, /music/<artist>/_/<track>
_LASTFM_URL_RE = re.compile(r"last\.fm/(?:[a-z]{2}/)?music/([^?#]+)", re.IGNORECASE)

_BANDCAMP_URL_RE = re.compile(
    r"^(?:https?://)?([a-z0-9-]+\.bandcamp\.com/(album|track)/[a-z0-9-]+)", re.IGNORECASE,
)

_KIND = {'song': 'track', 'release': 'album', 'master': 'album', 'interpreter': 'artist',
         'albums': 'album', 'tracks': 'track'}


def _kind(word: str) -> str:
    word = word.lower()
    return _KIND.get(word, word)


def _bare_digits(text: str) -> Optional[str]:
    return text if text.isdigit() and len(text) >= 3 else None


def parse_direct_ref(service: str, entity_type: str, query: str) -> Optional[tuple]:
    """``(kind, id)`` when ``query`` is one of ``service``'s links or ids, else None.

    Pure. ``kind`` is None for a bare id. Never matches ordinary words: a bare
    Spotify id must carry a digit (a 22-letter name isn't an id), and bare
    numbers need three digits."""
    text = (query or '').strip()
    if not text:
        return None
    service = (service or '').strip().lower()

    if service == 'spotify':
        m = _SPOTIFY_URL_RE.search(text) or _SPOTIFY_URI_RE.match(text)
        if m:
            return (m.group(1).lower(), m.group(2))
        if _SPOTIFY_BARE_RE.match(text) and any(c.isdigit() for c in text) \
                and any(c.isalpha() for c in text):
            return (None, text)
        return None

    if service == 'itunes':
        m = _APPLE_URL_RE.search(text)
        if m:
            track = _APPLE_TRACK_PARAM_RE.search(text)
            if track:
                return ('track', track.group(1))
            return (_kind(m.group(1)), m.group(2))
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'discogs':
        m = _DISCOGS_URL_RE.search(text)
        tag = None if m else _DISCOGS_TAG_RE.match(text)
        if m or tag:
            word, num = (m.group(1).lower(), m.group(2)) if m else (tag.group(1).lower(), tag.group(2))
            if word in ('artist', 'a'):
                return ('artist', num)
            # album ids are tagged so a master and a release that share a number
            # can't be confused (discogs_client._discogs_album_endpoints)
            return ('album', f"{'m' if word in ('master', 'm') else 'r'}{num}")
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'tidal':
        m = _TIDAL_URL_RE.search(text)
        if m:
            return (m.group(1).lower(), m.group(2))
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'qobuz':
        m = _QOBUZ_OPEN_RE.search(text) or _QOBUZ_SHOP_RE.search(text)
        if m:
            return (_kind(m.group(1)), m.group(2))
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'amazon':
        m = _AMAZON_URL_RE.search(text)
        if m:
            track = _AMAZON_TRACK_PARAM_RE.search(text)
            if track:
                return ('track', track.group(1).upper())
            return (_kind(m.group(1)), m.group(2).upper())
        return (None, text.upper()) if _ASIN_RE.match(text.upper()) else None

    if service == 'audiodb':
        m = _AUDIODB_URL_RE.search(text)
        if m:
            return (m.group(1).lower(), m.group(2))
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'genius':
        # genius.com links name the page, not the id, so only a bare id works
        bare = _bare_digits(text)
        return (None, bare) if bare else None

    if service == 'jiosaavn':
        m = _JIOSAAVN_URL_RE.search(text)
        return (_kind(m.group(1)), m.group(2)) if m else None

    if service == 'lastfm':
        # last.fm is addressed by url, and the url is what gets stored
        m = _LASTFM_URL_RE.search(text)
        if not m:
            return None
        parts = [p for p in m.group(1).split('/') if p]
        if not parts:
            return None
        if len(parts) >= 3 and parts[1] == '_':
            kind = 'track'
        elif len(parts) >= 2 and parts[1] != '_':
            kind = 'album'
        else:
            kind = 'artist'
        return (kind, 'https://www.last.fm/music/' + '/'.join(parts[:3]))

    if service == 'bandcamp':
        m = _BANDCAMP_URL_RE.match(text)
        return (m.group(2).lower(), 'https://' + m.group(1).lower()) if m else None

    return None
