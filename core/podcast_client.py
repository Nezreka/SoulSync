"""Podcast Client — search podcasts via iTunes Search API and parse episode metadata from RSS feeds.

Two distinct concerns:
  - search_podcasts(): queries the iTunes Search API for podcast discovery; returns PodcastShow
    summaries with episodes=[].
  - fetch_feed(): fetches and fully parses an RSS 2.0 / iTunes-namespace feed into structured
    dataclasses with a complete episode list.

No authentication required. Both operations hit public, unauthenticated endpoints.

Cover art strategy: iTunes API thumbnails cap at 600x600. The RSS feed's <itunes:image>
href carries the original upload — typically 1400x1400 or 3000x3000. fetch_feed() always
prefers the feed-level image over whatever the iTunes API returned.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("podcast_client")

# ---------------------------------------------------------------------------
# iTunes API endpoints
# ---------------------------------------------------------------------------

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"

# Browser-like UA — some podcast CDNs and hosts block the default Python UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_FETCH_TIMEOUT = 15   # seconds — full feed download
_SEARCH_TIMEOUT = 10  # seconds — iTunes API call

# Target size when upgrading iTunes CDN artwork URLs.
_ITUNES_ART_TARGET = 3000

# RSS namespace URIs for iTunes and PodcastIndex extensions.
_RSS_NS: Dict[str, str] = {
    "itunes":  "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "podcast": "https://podcastindex.org/namespace/1.0",
}

# Matches "...600x600bb.jpg" in iTunes CDN URLs — captures size and extension.
_ITUNES_ART_SIZE_RE = re.compile(r"/(\d+)x(\d+)bb(\.\w+)$")


# ---------------------------------------------------------------------------
# Pure helpers — module-level, each independently testable
# ---------------------------------------------------------------------------

def _upgrade_itunes_art_url(url: str, target: int = _ITUNES_ART_TARGET) -> str:
    """Rewrite an iTunes CDN artwork URL to request a larger size.

    iTunes API returns artworkUrl600 at 600x600. The CDN serves up to
    3000x3000 by rewriting the size segment in the URL path — identical
    pattern to _upgrade_spotify_image_url and the Deezer CDN rewriter.

    Defensive on every input:
      - Empty/None URL -> returned as-is
      - Non-iTunes CDN URL -> returned as-is
      - Already at or above target -> returned as-is
    The CDN returns source-native bytes when source < target, so asking for
    3000 on a 1400-pixel upload just returns 1400 — no upscaling, no error.
    """
    if not url:
        return url
    match = _ITUNES_ART_SIZE_RE.search(url)
    if not match:
        return url
    current = int(match.group(1))
    if current >= target:
        return url
    ext = match.group(3)
    return url[: match.start()] + f"/{target}x{target}bb{ext}"


def _parse_duration_to_seconds(raw: Optional[str]) -> Optional[int]:
    """Parse an <itunes:duration> value to integer seconds.

    iTunes duration arrives in three shapes:
      - "HH:MM:SS"   -> hours/minutes/seconds
      - "MM:SS"      -> minutes/seconds
      - "NNN"        -> raw seconds as a string

    Returns None on empty/None input. Logs and returns None on parse failure
    rather than raising — malformed duration metadata must not block a download.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return int(parts[0])
    except (ValueError, IndexError):
        logger.debug("Could not parse duration %r", raw)
        return None


def _parse_pub_date(raw: Optional[str]) -> Optional[datetime]:
    """Parse an RSS pubDate string (RFC 2822) to a UTC-aware datetime.

    Returns None on empty input or parse failure — bad pub dates must not
    break feed ingestion.
    """
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        # Normalise to UTC regardless of the original timezone offset.
        return dt.astimezone(timezone.utc)
    except Exception:
        logger.debug("Could not parse pub_date %r", raw)
        return None


def _ns_find(element: ET.Element, tag: str, ns: Dict[str, str]) -> Optional[ET.Element]:
    """Find a child element, resolving prefix:localname to Clark notation.

    xml.etree.ElementTree requires {uri}localname for namespaced tags.
    This helper accepts prefix:localname and resolves through ns.
    Unqualified tags are passed through unchanged.
    """
    if ":" in tag:
        prefix, local = tag.split(":", 1)
        uri = ns.get(prefix, "")
        return element.find(f"{{{uri}}}{local}")
    return element.find(tag)


def _ns_text(element: ET.Element, tag: str, ns: Dict[str, str], default: str = "") -> str:
    """Return stripped, HTML-unescaped text of a child element, or default."""
    child = _ns_find(element, tag, ns)
    if child is not None and child.text:
        return html.unescape(child.text.strip())
    return default


def _ns_attr(element: ET.Element, tag: str, ns: Dict[str, str], attr: str, default: str = "") -> str:
    """Return an attribute of a namespaced child element, or default."""
    child = _ns_find(element, tag, ns)
    if child is not None:
        return child.attrib.get(attr, default)
    return default


def _best_artwork(*candidates: Optional[str]) -> Optional[str]:
    """Return the first non-empty string from candidates, else None.

    Callers pass preferred -> fallback order. Feed-level <itunes:image> should
    come before the iTunes API thumbnail so the higher-res source wins.
    """
    for c in candidates:
        if c and c.strip():
            return c.strip()
    return None


def _parse_categories(channel: ET.Element) -> List[str]:
    """Extract all <itunes:category> text values from a channel element.

    iTunes categories are nested: the outer element names the top-level
    category (e.g. "Technology") and may contain inner elements for
    sub-categories (e.g. "Tech News"). Both levels are collected.
    """
    categories: List[str] = []
    uri = _RSS_NS["itunes"]
    for cat_el in channel.findall(f"{{{uri}}}category"):
        text = cat_el.attrib.get("text", "").strip()
        if text:
            categories.append(text)
        for sub in cat_el.findall(f"{{{uri}}}category"):
            sub_text = sub.attrib.get("text", "").strip()
            if sub_text and sub_text not in categories:
                categories.append(sub_text)
    return categories


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PodcastEpisode:
    """A single podcast episode, fully parsed from an RSS <item>.

    guid is the stable identity across feed refreshes — use it for
    de-duplication, not the enclosure URL (CDNs sometimes rotate those).
    """

    guid: str
    title: str
    enclosure_url: str
    enclosure_type: str              # e.g. "audio/mpeg", "audio/x-m4a"
    enclosure_length: Optional[int]  # bytes from the <enclosure length="..."> attr
    pub_date: Optional[datetime]     # UTC-normalised from <pubDate>
    duration_seconds: Optional[int]  # parsed from <itunes:duration>
    description: str                 # plain-text or HTML description
    show_notes: str                  # <content:encoded> when present (full HTML)
    season: Optional[int]            # <itunes:season>
    episode_number: Optional[int]    # <itunes:episode>
    episode_type: str                # "full" | "trailer" | "bonus"
    artwork_url: Optional[str]       # episode-level <itunes:image> if present
    chapter_url: Optional[str]       # <podcast:chapters> href (PodcastIndex namespace)
    transcript_url: Optional[str]    # <podcast:transcript> url (PodcastIndex namespace)
    show_title: Optional[str] = None # show/podcast title
    author: Optional[str] = None     # show author / podcast host


@dataclass
class PodcastShow:
    """A podcast show with its episode list.

    When returned from search_podcasts() the episodes list is empty —
    only show-level metadata is populated. Call fetch_feed() with the
    feed_url to get the full episode list.
    """

    title: str
    author: str
    description: str
    artwork_url: Optional[str]
    feed_url: Optional[str]
    itunes_id: Optional[int]
    website: Optional[str]
    language: str
    explicit: bool
    categories: List[str]
    episode_count: Optional[int]         # from iTunes API (approximate) or len(episodes) after feed parse
    episodes: List[PodcastEpisode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class PodcastClient:
    """Searches iTunes for podcasts and parses RSS feeds into structured data.

    No authentication required. No rate-limit budget is shared with any other
    SoulSync client — iTunes Search has generous public limits and feed fetches
    are plain CDN traffic.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_podcasts(self, query: str, limit: int = 20) -> List[PodcastShow]:
        """Search iTunes for podcasts matching query.

        Returns PodcastShow objects with episodes=[]. Call fetch_feed()
        to populate episodes for a specific show.

        Fails open: network/parse errors are logged and an empty list is
        returned so no UI path is broken by a search failure.
        """
        if not query or not query.strip():
            return []
        limit = max(1, min(limit, 200))
        try:
            resp = self._session.get(
                _ITUNES_SEARCH_URL,
                params={"term": query.strip(), "media": "podcast", "limit": limit},
                timeout=_SEARCH_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("iTunes podcast search failed for %r: %s", query, exc)
            return []

        results = []
        for item in data.get("results", []):
            show = self._itunes_result_to_show(item)
            if show is not None:
                results.append(show)
        logger.debug("search_podcasts(%r) -> %d results", query, len(results))
        return results

    def lookup_by_itunes_id(self, itunes_id: int) -> Optional[PodcastShow]:
        """Fetch show-level metadata directly by iTunes podcast ID.

        Useful when you already know the iTunes ID and want fresh artwork or
        the current feed URL without a text search.
        """
        try:
            resp = self._session.get(
                _ITUNES_LOOKUP_URL,
                params={"id": itunes_id, "media": "podcast"},
                timeout=_SEARCH_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if results:
                return self._itunes_result_to_show(results[0])
        except Exception as exc:
            logger.warning("iTunes lookup failed for id=%s: %s", itunes_id, exc)
        return None

    # ------------------------------------------------------------------
    # Feed parsing
    # ------------------------------------------------------------------

    def fetch_feed(
        self,
        feed_url: str,
        show_hint: Optional[PodcastShow] = None,
    ) -> Optional[PodcastShow]:
        """Fetch and fully parse an RSS feed URL into a PodcastShow.

        Returns a PodcastShow with a populated episodes list, or None on
        unrecoverable error (bad URL, network failure, XML parse failure).

        show_hint is a PodcastShow from search_podcasts(). When provided,
        its iTunes-derived metadata (itunes_id, episode_count) is merged into
        the result. The feed itself is authoritative for everything else —
        especially artwork, where the feed-level image is always higher-res
        than the iTunes API thumbnail.
        """
        try:
            resp = self._session.get(feed_url, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to fetch feed %r: %s", feed_url, exc)
            return None

        try:
            # Parse from bytes so ElementTree honours the XML encoding
            # declaration — parsing from text after decode can mis-handle
            # non-UTF-8 feeds.
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("RSS parse error for %r: %s", feed_url, exc)
            return None

        channel = root.find("channel")
        if channel is None:
            # Atom or non-standard shape — treat root as channel.
            channel = root

        show = self._parse_channel(channel, feed_url)

        # Merge hint metadata that only iTunes carries.
        if show_hint is not None:
            if show.itunes_id is None:
                show.itunes_id = show_hint.itunes_id
            if show.episode_count is None:
                show.episode_count = show_hint.episode_count
            # Prefer feed artwork (higher-res); fall back to iTunes thumbnail.
            if not show.artwork_url:
                show.artwork_url = show_hint.artwork_url

        return show

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _itunes_result_to_show(self, item: Dict) -> Optional[PodcastShow]:
        """Convert one iTunes Search API result dict into a PodcastShow.

        Returns None when the item has no feedUrl — there is nothing
        actionable without a feed URL.
        """
        feed_url = item.get("feedUrl")
        if not feed_url:
            return None

        # Prefer artworkUrl600 and upgrade the CDN URL to max supported size.
        raw_art = (
            item.get("artworkUrl600")
            or item.get("artworkUrl100")
            or item.get("artworkUrl30")
        )
        artwork = _upgrade_itunes_art_url(raw_art) if raw_art else None

        explicit_flag = (item.get("contentAdvisoryRating") or "").lower()
        explicit = explicit_flag in ("explicit", "yes", "true")

        # iTunes returns a top-level "Podcasts" genre — strip it, it is noise.
        genres: List[str] = item.get("genres") or []
        categories = [g for g in genres if g.lower() != "podcasts"]

        return PodcastShow(
            title=html.unescape(item.get("collectionName") or item.get("trackName") or ""),
            author=html.unescape(item.get("artistName") or ""),
            description=html.unescape(item.get("description") or ""),
            artwork_url=artwork,
            feed_url=feed_url,
            itunes_id=item.get("collectionId") or item.get("trackId"),
            website=item.get("collectionViewUrl"),
            language="",    # iTunes Search does not return language
            explicit=explicit,
            categories=categories,
            episode_count=item.get("trackCount"),
            episodes=[],
        )

    def _parse_channel(self, channel: ET.Element, feed_url: str) -> PodcastShow:
        """Extract show-level metadata from a parsed RSS <channel> element."""
        ns = _RSS_NS

        title = (
            _ns_text(channel, "itunes:title", ns)
            or _ns_text(channel, "title", {})
        )
        author = (
            _ns_text(channel, "itunes:author", ns)
            or _ns_text(channel, "managingEditor", {})
            or _ns_text(channel, "author", {})
        )
        description = (
            _ns_text(channel, "itunes:summary", ns)
            or _ns_text(channel, "description", {})
        )
        language = _ns_text(channel, "language", {})
        website = _ns_text(channel, "link", {})

        # Feed artwork: <itunes:image href="..."> is canonical; <image><url>
        # is the RSS 2.0 fallback (lower-res in practice).
        feed_art = _ns_attr(channel, "itunes:image", ns, "href")
        rss_art = ""
        image_el = channel.find("image")
        if image_el is not None:
            url_el = image_el.find("url")
            if url_el is not None and url_el.text:
                rss_art = url_el.text.strip()

        artwork = _best_artwork(feed_art, rss_art)

        explicit_raw = _ns_text(channel, "itunes:explicit", ns).lower()
        explicit = explicit_raw in ("yes", "true", "explicit")

        categories = _parse_categories(channel)

        episodes = []
        for item_el in channel.findall("item"):
            ep = self._parse_item(item_el, show_title=title, author=author)
            if ep is not None:
                episodes.append(ep)

        return PodcastShow(
            title=title,
            author=author,
            description=description,
            artwork_url=artwork,
            feed_url=feed_url,
            itunes_id=None,
            website=website,
            language=language,
            explicit=explicit,
            categories=categories,
            episode_count=len(episodes) if episodes else None,
            episodes=episodes,
        )

    def _parse_item(
        self,
        item: ET.Element,
        show_title: Optional[str] = None,
        author: Optional[str] = None,
    ) -> Optional[PodcastEpisode]:
        """Parse one RSS <item> element into a PodcastEpisode.

        Returns None when the item has no downloadable enclosure — items without
        audio files (show-note-only entries, chapter markers, etc.) are silently
        skipped rather than returned as incomplete episodes.
        """
        ns = _RSS_NS

        # Enclosure is the load-bearing element — no audio, no episode.
        enclosure_el = item.find("enclosure")
        if enclosure_el is None:
            return None
        enc_url = enclosure_el.attrib.get("url", "").strip()
        if not enc_url:
            return None
        enc_type = enclosure_el.attrib.get("type", "")
        try:
            enc_length: Optional[int] = int(enclosure_el.attrib.get("length", 0)) or None
        except (ValueError, TypeError):
            enc_length = None

        # GUID is the stable episode identity across feed refreshes.
        guid_el = item.find("guid")
        guid = (guid_el.text or "").strip() if guid_el is not None else enc_url

        title_raw = (
            _ns_text(item, "itunes:title", ns)
            or _ns_text(item, "title", {})
        )
        title = html.unescape(title_raw)

        description = (
            _ns_text(item, "itunes:summary", ns)
            or _ns_text(item, "description", {})
        )

        # <content:encoded> carries full show notes (HTML) when present.
        show_notes = _ns_text(item, "content:encoded", ns)

        pub_date = _parse_pub_date(_ns_text(item, "pubDate", {}))
        duration_seconds = _parse_duration_to_seconds(_ns_text(item, "itunes:duration", ns))

        # Season and episode numbers from the iTunes namespace.
        season_raw = _ns_text(item, "itunes:season", ns)
        ep_num_raw = _ns_text(item, "itunes:episode", ns)
        try:
            season: Optional[int] = int(season_raw) if season_raw else None
        except ValueError:
            season = None
        try:
            episode_number: Optional[int] = int(ep_num_raw) if ep_num_raw else None
        except ValueError:
            episode_number = None

        episode_type = _ns_text(item, "itunes:episodeType", ns) or "full"

        # Episode-level artwork overrides show artwork in per-episode display.
        ep_art = _ns_attr(item, "itunes:image", ns, "href") or None

        # PodcastIndex.org namespace extensions (chapters, transcripts).
        chapters_el = _ns_find(item, "podcast:chapters", ns)
        chapter_url = chapters_el.attrib.get("url") if chapters_el is not None else None

        transcript_el = _ns_find(item, "podcast:transcript", ns)
        transcript_url = transcript_el.attrib.get("url") if transcript_el is not None else None

        return PodcastEpisode(
            guid=guid,
            title=title,
            enclosure_url=enc_url,
            enclosure_type=enc_type,
            enclosure_length=enc_length,
            pub_date=pub_date,
            duration_seconds=duration_seconds,
            description=description,
            show_notes=show_notes,
            season=season,
            episode_number=episode_number,
            episode_type=episode_type,
            artwork_url=ep_art,
            chapter_url=chapter_url,
            transcript_url=transcript_url,
            show_title=show_title,
            author=author,
        )


# ---------------------------------------------------------------------------
# Module-level convenience — mirrors the bandcamp_client / deezer_client pattern
# ---------------------------------------------------------------------------

_default_client: Optional[PodcastClient] = None


def get_podcast_client() -> PodcastClient:
    """Return the process-wide PodcastClient singleton.

    Constructed lazily on first call. The client holds only a requests.Session;
    no credentials are needed, so there is nothing to configure ahead of time.
    """
    global _default_client
    if _default_client is None:
        _default_client = PodcastClient()
    return _default_client
