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

    return None


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
