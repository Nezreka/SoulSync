"""what an artist page says about an artist: bio, tags and popularity, and
which source each came from.

last.fm used to be the only source of all three. from december 2026 a last.fm
key needs a pro subscription, so most instances lose it. everything here is a
fallback chain with last.fm first: whatever last.fm stored wins, and the free
sources only fill a field last.fm left empty.

the free sources never write the ``lastfm`` bucket. each has its own bucket in
``lib2_artists.enrichment`` (``audiodb``, ``wikipedia``, ``musicbrainz``,
``listenbrainz``, ``deezer``), so last.fm data is never overwritten, and the
moment last.fm works again its data is first in line.

pure functions only. the fetching is ``core.enrichment.lastfm_fallback``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

#: the fallback sources are asked again after this long
FALLBACK_TTL_DAYS = 30

#: bookkeeping bucket: when the fallbacks were last asked, and for which mbid
CHECK_KEY = "about_fallback"

# fallback order per field, Last.fm first
BIO_ORDER = (("lastfm", "bio"), ("audiodb", "bio"), ("wikipedia", "bio"))
TAG_ORDER = (("lastfm", "tags"), ("musicbrainz", "genres"), ("musicbrainz", "tags"))

FIELDS = ("bio", "tags", "popularity")


def load_enrichment(raw: Any) -> Dict[str, Any]:
    """The enrichment column as a dict, whatever state it is in."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bucket(enrichment: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = enrichment.get(key)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _tags(value: Any) -> List[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [part for part in value.split(",")]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        label = item.get("name") if isinstance(item, dict) else item
        label = _text(label) if isinstance(label, str) else None
        if label and label.lower() not in {t.lower() for t in out}:
            out.append(label)
    return out


def _count(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def lastfm_gaps(enrichment: Dict[str, Any]) -> Set[str]:
    """The fields Last.fm has nothing for. These are what a fallback may fill."""
    lastfm = _bucket(enrichment, "lastfm")
    gaps = set()
    if not _text(lastfm.get("bio")):
        gaps.add("bio")
    if not _tags(lastfm.get("tags")):
        gaps.add("tags")
    if not (_count(lastfm.get("listeners")) or _count(lastfm.get("playcount"))):
        gaps.add("popularity")
    return gaps


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def fallback_due(enrichment: Dict[str, Any], mbid: Optional[str] = None,
                 now: Optional[datetime] = None,
                 ttl_days: int = FALLBACK_TTL_DAYS) -> Set[str]:
    """The fields to ask the free sources for now, or an empty set.

    Nothing when Last.fm has every field. Otherwise due when never asked, when
    the last ask is older than the ttl, or when the artist has an mbid it did
    not have last time (the MusicBrainz worker matched it since, and the mbid
    is what opens MusicBrainz, Wikipedia and ListenBrainz).
    """
    gaps = lastfm_gaps(enrichment)
    if not gaps:
        return set()
    check = _bucket(enrichment, CHECK_KEY)
    checked_at = _parse_time(check.get("checked_at"))
    if checked_at is None:
        return gaps
    now = now or datetime.now(timezone.utc)
    if now - checked_at >= timedelta(days=ttl_days):
        return gaps
    if mbid and str(mbid) != str(check.get("mbid") or ""):
        return gaps
    return set()


def check_stamp(mbid: Optional[str], now: Optional[datetime] = None) -> Dict[str, Any]:
    """What ``CHECK_KEY`` holds after an ask."""
    now = now or datetime.now(timezone.utc)
    return {"checked_at": now.replace(microsecond=0).isoformat(),
            "mbid": str(mbid) if mbid else None}


def resolve_artist_about(raw_enrichment: Any) -> Dict[str, Any]:
    """Bio, tags and popularity for the artist page, each with its source.

    ``popularity_source`` says whose numbers ``listeners``/``plays`` are:
    Last.fm listeners and scrobbles, or ListenBrainz users and listens. They
    are never mixed. Deezer fans are their own number (``fans``), never
    passed off as listeners.
    """
    enrichment = load_enrichment(raw_enrichment)
    about: Dict[str, Any] = {
        "bio": None, "bio_source": None, "bio_url": None,
        "tags": [], "tags_source": None,
        "listeners": None, "plays": None, "popularity_source": None,
        "fans": None, "fans_source": None,
    }
    for source, key in BIO_ORDER:
        bucket = _bucket(enrichment, source)
        text = _text(bucket.get(key))
        if text:
            about.update(bio=text, bio_source=source,
                         bio_url=_text(bucket.get("url")) if source != "audiodb" else None)
            break
    for source, key in TAG_ORDER:
        tags = _tags(_bucket(enrichment, source).get(key))
        if tags:
            about.update(tags=tags[:10], tags_source=source)
            break
    lastfm = _bucket(enrichment, "lastfm")
    listenbrainz = _bucket(enrichment, "listenbrainz")
    if _count(lastfm.get("listeners")) or _count(lastfm.get("playcount")):
        about.update(listeners=_count(lastfm.get("listeners")),
                     plays=_count(lastfm.get("playcount")),
                     popularity_source="lastfm")
    elif _count(listenbrainz.get("listeners")) or _count(listenbrainz.get("listens")):
        about.update(listeners=_count(listenbrainz.get("listeners")),
                     plays=_count(listenbrainz.get("listens")),
                     popularity_source="listenbrainz")
    fans = _count(_bucket(enrichment, "deezer").get("fans"))
    if fans:
        about.update(fans=fans, fans_source="deezer")
    return about


__all__ = [
    "CHECK_KEY", "FALLBACK_TTL_DAYS", "check_stamp", "fallback_due",
    "lastfm_gaps", "load_enrichment", "resolve_artist_about",
]
