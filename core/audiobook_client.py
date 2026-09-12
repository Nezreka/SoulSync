"""Audiobook Client — search and describe audiobooks via Audible's public catalog API.

Two distinct concerns, same as the podcast client:
  - search()/bestsellers()/series()/similar(): discovery. Every call returns a list of
    AudiobookItem with full metadata already populated, because Audible hands back the
    complete product on the search response. There is no second "fetch detail" round
    trip the way podcasts need an RSS pull.
  - get_book(): a single product by ASIN, for the detail page and for refreshing a
    watchlisted title.

No authentication required. api.audible.com/1.0/catalog is the unauthenticated storefront
catalog that the Audible web player itself reads — no key, no scraping, no HTML parsing.

Metadata hierarchy (deliberate, mirrors the music side's source ordering):
  1. Audible catalog — the primary. Narrators, series + sequence, rating distribution,
     runtime, publisher, sample audio, genre ladder. Nothing else has narrators.
  2. Apple/iTunes audiobook search — fallback only, used when Audible returns nothing.
     It is thin: no narrator, no series, no runtime, artwork capped at 100x100 before
     the CDN rewrite. Good enough to say "this book exists", not good enough to build
     a page on.

Rate limiting: this client is paced by its OWN private gap (_MIN_REQUEST_GAP) and is
deliberately NOT wired to core.prowlarr_throttle or core.slskd_throttle. Those two are
the shared indexer budget for music and video acquisition; Audible and Apple are metadata
services, not indexers, and spending indexer slots on a browse request would slow real
downloads down for nothing. Acquisition, when it lands, uses the shared budget.

Cover art strategy: Audible returns product_images as a {size: url} map and honours
whatever sizes are asked for in image_sizes. The largest requested size that actually
came back wins; a missing size is simply absent from the map rather than an error.
"""

from __future__ import annotations

import html
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from utils.logging_config import get_logger

logger = get_logger("audiobook_client")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Audible runs one catalog host per marketplace. Same paths, same response shape,
# different storefront — a title exclusive to the UK store only resolves on .co.uk.
_AUDIBLE_HOSTS: Dict[str, str] = {
    "us": "api.audible.com",
    "uk": "api.audible.co.uk",
    "de": "api.audible.de",
    "fr": "api.audible.fr",
    "ca": "api.audible.ca",
    "au": "api.audible.com.au",
    "it": "api.audible.it",
    "es": "api.audible.es",
    "in": "api.audible.in",
    "jp": "api.audible.co.jp",
}
_DEFAULT_MARKETPLACE = "us"

# Where to look when a storefront will not answer. English stores only: falling
# back from the US to Germany would return the same books in German, which is a
# worse failure than showing nothing. Non-English stores get no fallback for the
# same reason.
_MARKETPLACE_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    "us": ("uk", "au"),
    "uk": ("us", "au"),
    "ca": ("us", "uk"),
    "au": ("uk", "us"),
    "in": ("uk", "us"),
    "de": (),
    "fr": (),
    "it": (),
    "es": (),
    "jp": (),
}

# Audible sheds load by answering 200 with an EMPTY body rather than an error:
# no "products" key at all and "response_groups": []. A genuine no-match looks
# different — it carries "products": [] and echoes the groups back. Verified
# against the live API while the US store was shedding and the UK store was not.
# Without this distinction a shed looks exactly like "this book does not exist".
_SHED_RETRY_ATTEMPTS = 2
_SHED_RETRY_SLEEP = 0.4

# The language each storefront actually sells in. A person search returns every
# translation Audible carries, so an English author's page comes back with the
# Spanish and French editions of the same novels sitting beside the originals as
# if they were separate series — "Fils des brumes" and "Trilogía Original
# Mistborn" next to "The Mistborn Saga". Editions are filtered to the
# storefront's own language so a bibliography reads as one shelf.
_MARKETPLACE_LANGUAGES: Dict[str, str] = {
    "us": "english",
    "uk": "english",
    "ca": "english",
    "au": "english",
    "in": "english",
    "de": "german",
    "fr": "french",
    "it": "italian",
    "es": "spanish",
    "jp": "japanese",
}

_APPLE_SEARCH_URL = "https://itunes.apple.com/search"
_APPLE_LOOKUP_URL = "https://itunes.apple.com/lookup"

# Everything a card needs, in one request. Asking for a group Audible does not
# know is a 400, so these lists are fixed and verified rather than assembled per
# call.
_SEARCH_RESPONSE_GROUPS = ",".join([
    "contributors",            # authors (with their own ASINs) and narrators
    "media",                   # product_images
    "product_attrs",           # runtime, language, format_type, publisher
    "product_desc",            # title, subtitle, merchandising_summary
    "rating",                  # full star distribution, not just an average
    "sample",                  # sample_url, a directly playable mp3
    "series",                  # series title + this book's sequence in it
    "category_ladders",        # genre breadcrumb, e.g. SF&F > Fantasy > Epic
])

# The detail groups add the long description. product_extended_attrs is
# deliberately NOT in the search list: asking for it visibly RE-RANKS the
# results. The same title=Mistborn query returns the English originals first
# without it and the Spanish editions first with it, same 24 total either way.
# Verified against the live catalog, so the long description is fetched on the
# detail call where ordering does not matter.
_DETAIL_RESPONSE_GROUPS = _SEARCH_RESPONSE_GROUPS + ",product_extended_attrs"

# Cover sizes requested from Audible. It returns only the ones it has, so asking
# for a large size costs nothing when the source art is smaller.
_IMAGE_SIZES = "252,500,1024"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_TIMEOUT = 12          # seconds, every catalog call
_MIN_REQUEST_GAP = 0.2  # seconds between outbound calls from this client

# Audible rejects num_results above 50 with a 400.
_MAX_PAGE_SIZE = 50

# Target size when upgrading Apple CDN artwork URLs. Same rewrite the podcast
# client does — the size segment in the path is what the CDN keys off.
_APPLE_ART_TARGET = 1400
_APPLE_ART_SIZE_RE = re.compile(r"/(\d+)x(\d+)bb(\.\w+)$")

# Cache lifetimes. Searches move (new releases, chart churn); a product's own
# metadata effectively never changes once published.
_SEARCH_TTL = 3600.0    # 1 hour
_DETAIL_TTL = 86400.0   # 24 hours

# Tags worth keeping as line breaks when flattening Audible's HTML summaries.
_BLOCK_TAG_RE = re.compile(r"(?i)</p\s*>|<br\s*/?>")
_ANY_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Pure helpers — module-level, each independently testable
# ---------------------------------------------------------------------------

def clean_summary_html(raw: Optional[str]) -> str:
    """Flatten Audible's HTML summary into readable plain text.

    publisher_summary arrives as a block of <p>/<b>/<i>/<br> markup with escaped
    entities inside it. The UI wants paragraphs, not markup, and definitely not
    raw HTML it would have to trust.

    Paragraph and line breaks survive as newlines; every other tag is dropped;
    entities are unescaped; runs of blank lines collapse to one.
    Returns "" for None/empty input rather than raising.
    """
    if not raw:
        return ""
    text = _BLOCK_TAG_RE.sub("\n", raw)
    text = _ANY_TAG_RE.sub("", text)
    text = html.unescape(text)
    # Collapse horizontal whitespace without eating the newlines we just made.
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    out: List[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def format_runtime(minutes: Optional[int]) -> str:
    """Human runtime string from a minute count, e.g. "24 hrs 39 mins".

    Audible gives runtime_length_min as a plain integer. The UI shows it on
    every card, so the formatting lives here rather than being re-derived in
    each component.

    Returns "" for None, non-numeric, zero or negative input — an unknown
    runtime renders as nothing, never as "0 mins".
    """
    try:
        total = int(minutes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, mins = divmod(total, 60)
    parts: List[str] = []
    if hours:
        parts.append(f"{hours} hr" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} min" + ("s" if mins != 1 else ""))
    return " ".join(parts)


def upgrade_apple_artwork(url: Optional[str], target: int = _APPLE_ART_TARGET) -> Optional[str]:
    """Rewrite an Apple CDN artwork URL to request a larger size.

    The iTunes search response carries artworkUrl100 at 100x100; the same CDN
    serves up to 1400x1400 by rewriting the size segment. Identical treatment to
    _upgrade_itunes_art_url in the podcast client.

    Defensive on every input: empty, non-Apple, or already-large URLs come back
    unchanged. The CDN returns source-native bytes when the source is smaller
    than the target, so over-asking never fails.
    """
    if not url:
        return url
    match = _APPLE_ART_SIZE_RE.search(url)
    if not match:
        return url
    if int(match.group(1)) >= target:
        return url
    return url[: match.start()] + f"/{target}x{target}bb{match.group(3)}"


def best_cover(images: Any) -> Tuple[Optional[str], Optional[str]]:
    """Pick (display_cover, largest_cover) out of Audible's product_images map.

    product_images is {"500": url, "1024": url} with numeric-string keys, and a
    size that does not exist for a title is simply missing. Returns the largest
    available for the hero/detail view and a mid-size one for grid cards, so a
    browse page of 50 covers is not pulling 50 full-size images.

    Accepts a bare string (already a URL) or None and degrades gracefully.
    """
    if isinstance(images, str):
        return (images, images) if images.strip() else (None, None)
    if not isinstance(images, dict) or not images:
        return None, None
    sized: List[Tuple[int, str]] = []
    for key, url in images.items():
        if not url or not isinstance(url, str):
            continue
        try:
            sized.append((int(str(key).split("x")[0]), url))
        except (TypeError, ValueError):
            continue
    if not sized:
        return None, None
    sized.sort(key=lambda pair: pair[0])
    largest = sized[-1][1]
    # Prefer something around 500px for cards; fall back to the largest we have.
    display = next((url for size, url in sized if size >= 400), largest)
    return display, largest


def parse_sequence(raw: Any) -> Optional[float]:
    """Series position as a sortable number, or None when it is not one.

    Audible's sequence is a string and it is not always an integer: box sets and
    novellas carry "2.5", omnibus entries carry "1-3", and standalone entries in
    a loose "series" carry "" or nothing at all. Sorting the raw strings puts
    "10" before "2", so the ordered series view needs a real number.

    "1-3" takes the first number so an omnibus lands where its first book does.
    Anything unparseable returns None and the caller sorts it to the end.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    match = re.match(r"^\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def series_query_variants(series_title: str) -> List[str]:
    """Search terms to try for a series, most specific first.

    A series is NAMED differently from the books in it. "The Mistborn Saga"
    matches no book title at all, while "Mistborn" matches every one of them —
    so searching the series name verbatim finds nothing and the series shelf
    comes back empty. Dropping the leading article and the trailing collective
    noun is what turns the series name back into the words the books share.

    Ordered and de-duplicated: the exact name is tried first so a series that
    genuinely is titled like its books does not get widened for no reason.
    """
    base = (series_title or "").strip()
    if not base:
        return []
    variants = [base]
    without_article = re.sub(r"(?i)^(the|a|an)\s+", "", base).strip()
    trailing = r"(?i)\s+(saga|series|trilogy|chronicles|cycle|novels|collection|duology|books)$"
    for candidate in (
        without_article,
        re.sub(trailing, "", base).strip(),
        re.sub(trailing, "", without_article).strip(),
    ):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def page_offset(page: Any) -> int:
    """Convert a 1-based page number to Audible's 0-based ``page`` parameter.

    Audible's page parameter is ZERO indexed, which is not obvious and is a
    silent data-loss bug rather than an error: page=1 skips the entire first
    page of results, and num_results=50&page=1 on a 24-result query returns an
    empty list that looks exactly like "no matches". Verified against the live
    catalog. Every caller here speaks 1-based pages and this is the only place
    that knows the difference.

    Anything unparseable or below 1 clamps to the first page.
    """
    try:
        value = int(page)
    except (TypeError, ValueError):
        return 0
    return max(0, value - 1)


def normalize_category_name(name: Optional[str]) -> str:
    """Fold a genre name so it matches across storefronts.

    Category IDs are NOT portable — of the 23 genre names the US and UK stores
    share, not one has the same id. Names are almost identical, so names are the
    key and the id is resolved per store. "Almost" is why this folds spelling
    too: the US sells "Comedy & Humor" and the UK sells "Comedy & Humour".
    """
    folded = (name or "").strip().casefold()
    folded = folded.replace("&", "and")
    folded = re.sub(r"\bhumour\b", "humor", folded)
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def response_was_processed(data: Any, key: str = "products") -> bool:
    """Did the storefront actually run this query?

    A processed response always carries its result key, even when the result is
    empty — a real no-match is ``{"products": [], "response_groups": [...]}``.
    A shed one omits the key entirely and echoes back no groups.

    The difference matters twice over: a shed read as "no results" shows the
    user an empty catalogue, and caching it makes that empty catalogue stick
    around long after the store recovered.
    """
    if not isinstance(data, dict):
        return False
    return key in data


def marketplace_chain(marketplace: Optional[str]) -> List[str]:
    """The storefront to ask, then the ones to try if it will not answer."""
    code = (marketplace or "").strip().lower()
    if code not in _AUDIBLE_HOSTS:
        code = _DEFAULT_MARKETPLACE
    return [code, *_MARKETPLACE_FALLBACKS.get(code, ())]


def marketplace_language(marketplace: Optional[str]) -> str:
    """The language a storefront sells in, lowercase, or "" when unknown.

    An unknown code returns "" rather than guessing, and callers read that as
    "do not filter" — showing every translation is a worse page than an
    English-only one, but it beats showing nothing.
    """
    return _MARKETPLACE_LANGUAGES.get((marketplace or "").strip().lower(), "")


def marketplace_host(marketplace: Optional[str]) -> str:
    """Audible catalog host for a marketplace code, defaulting to the US store.

    An unknown or empty code falls back rather than raising: a bad query
    parameter should return US results, not a 500.
    """
    code = (marketplace or "").strip().lower()
    return _AUDIBLE_HOSTS.get(code, _AUDIBLE_HOSTS[_DEFAULT_MARKETPLACE])


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AudiobookPerson:
    """An author or narrator.

    Authors carry their own ASIN, which makes "more by this author" an exact
    lookup instead of a name search. Narrators almost never do — Audible only
    indexes them by name — so asin is optional and usually None for them.
    """

    name: str
    asin: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "asin": self.asin}


@dataclass
class AudiobookSeries:
    """A series a book belongs to, with this book's position in it."""

    asin: Optional[str]
    title: str
    sequence: Optional[str]           # as printed, e.g. "1", "2.5", "1-3"
    sequence_value: Optional[float]   # numeric form for sorting, None when unparseable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asin": self.asin,
            "title": self.title,
            "sequence": self.sequence,
            "sequence_value": self.sequence_value,
        }


@dataclass
class AudiobookRating:
    """Star rating with its full distribution.

    Audible returns counts per star level, not just an average. The distribution
    is what lets the detail page draw a real ratings histogram instead of five
    static stars.
    """

    average: Optional[float]
    count: int
    distribution: Dict[str, int] = field(default_factory=dict)  # {"5": 81915, "4": 13650, ...}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "average": self.average,
            "count": self.count,
            "distribution": self.distribution,
        }


@dataclass
class AudiobookItem:
    """One audiobook, fully described.

    asin is the identity everywhere in this subsystem — it is stable, it is what
    the detail, similar and batch-lookup endpoints key on, and it survives title
    changes. Apple-sourced fallback items carry their Apple collection id in this
    field instead and set source="apple"; anything reading asin must therefore
    check source before assuming it can call Audible with it.
    """

    asin: str
    title: str
    subtitle: str
    authors: List[AudiobookPerson]
    narrators: List[AudiobookPerson]
    series: List[AudiobookSeries]
    publisher: str
    summary: str                        # cleaned plain text, long form
    short_summary: str                  # cleaned plain text, one or two lines
    release_date: Optional[str]         # ISO date, e.g. "2021-05-04"
    runtime_minutes: Optional[int]
    cover_url: Optional[str]            # card-sized
    cover_url_large: Optional[str]      # hero/detail-sized
    sample_url: Optional[str]           # directly playable audio preview
    rating: Optional[AudiobookRating]
    genres: List[str]                   # flattened category ladder, broadest first
    language: str
    format_type: str                    # "unabridged" | "abridged" | ""
    is_adult: bool
    source: str = "audible"             # "audible" | "apple"

    @property
    def runtime_formatted(self) -> str:
        """Runtime as shown in the UI. Derived, never stored, always in sync."""
        return format_runtime(self.runtime_minutes)

    @property
    def author_names(self) -> List[str]:
        return [a.name for a in self.authors]

    @property
    def narrator_names(self) -> List[str]:
        return [n.name for n in self.narrators]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the API layer.

        Everything the frontend could want is included flat: the API blueprint
        does no reshaping, so there is exactly one definition of an audiobook
        payload and the React types can be generated from it by eye.
        """
        return {
            "asin": self.asin,
            "title": self.title,
            "subtitle": self.subtitle,
            "authors": [a.to_dict() for a in self.authors],
            "narrators": [n.to_dict() for n in self.narrators],
            "author_names": self.author_names,
            "narrator_names": self.narrator_names,
            "series": [s.to_dict() for s in self.series],
            "publisher": self.publisher,
            "summary": self.summary,
            "short_summary": self.short_summary,
            "release_date": self.release_date,
            "runtime_minutes": self.runtime_minutes,
            "runtime_formatted": self.runtime_formatted,
            "cover_url": self.cover_url,
            "cover_url_large": self.cover_url_large,
            "sample_url": self.sample_url,
            "rating": self.rating.to_dict() if self.rating else None,
            "genres": self.genres,
            "language": self.language,
            "format_type": self.format_type,
            "is_adult": self.is_adult,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# Parsers — raw API payload to dataclass
# ---------------------------------------------------------------------------

def _parse_people(raw: Any, with_asin: bool = True) -> List[AudiobookPerson]:
    """Build the author/narrator list from a contributors array.

    Skips entries with no usable name instead of emitting blanks — an empty
    author chip in the UI is worse than one fewer chip.
    """
    people: List[AudiobookPerson] = []
    if not isinstance(raw, list):
        return people
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        asin = str(entry.get("asin") or "").strip() or None if with_asin else None
        people.append(AudiobookPerson(name=name, asin=asin))
    return people


def _parse_series(raw: Any) -> List[AudiobookSeries]:
    """Build the series list, keeping the printed sequence and a sortable one.

    A book can legitimately be in more than one series (a saga plus the
    sub-trilogy inside it), so this is a list and the UI picks which to show.
    """
    out: List[AudiobookSeries] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        sequence = entry.get("sequence")
        sequence_text = str(sequence).strip() if sequence not in (None, "") else None
        out.append(AudiobookSeries(
            asin=str(entry.get("asin") or "").strip() or None,
            title=title,
            sequence=sequence_text,
            sequence_value=parse_sequence(sequence),
        ))
    return out


def _parse_rating(raw: Any) -> Optional[AudiobookRating]:
    """Build the rating from Audible's overall_distribution block.

    Returns None when there is no rating data at all — a brand new release with
    no ratings should render as "no ratings yet", not as zero stars.
    """
    if not isinstance(raw, dict):
        return None
    overall = raw.get("overall_distribution")
    if not isinstance(overall, dict):
        return None
    average = overall.get("average_rating")
    try:
        average = round(float(average), 2) if average is not None else None
    except (TypeError, ValueError):
        average = None
    try:
        count = int(overall.get("num_ratings") or 0)
    except (TypeError, ValueError):
        count = 0
    distribution: Dict[str, int] = {}
    for star, key in (
        ("5", "num_five_star_ratings"),
        ("4", "num_four_star_ratings"),
        ("3", "num_three_star_ratings"),
        ("2", "num_two_star_ratings"),
        ("1", "num_one_star_ratings"),
    ):
        try:
            distribution[star] = int(overall.get(key) or 0)
        except (TypeError, ValueError):
            distribution[star] = 0
    # A brand new release comes back with average_rating 0.0 rather than a null,
    # so the count is what decides whether a rating exists. Returning a 0.0-star
    # rating here would paint an empty star row on every new title.
    if count == 0 and not any(distribution.values()):
        return None
    return AudiobookRating(average=average, count=count, distribution=distribution)


def _parse_genres(raw: Any) -> List[str]:
    """Flatten category_ladders into an ordered, de-duplicated genre list.

    Audible returns one ladder per shelf placement, each broad-to-narrow
    ("Science Fiction & Fantasy" > "Fantasy" > "Epic"). A title on two shelves
    repeats its shared prefix, so order is preserved and repeats are dropped —
    the result reads as a breadcrumb and doubles as the genre pill list.
    """
    genres: List[str] = []
    if not isinstance(raw, list):
        return genres
    for ladder_entry in raw:
        if not isinstance(ladder_entry, dict):
            continue
        for node in ladder_entry.get("ladder") or []:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "").strip()
            if name and name not in genres:
                genres.append(name)
    return genres


def product_to_item(product: Any) -> Optional[AudiobookItem]:
    """Convert one Audible catalog product to an AudiobookItem.

    Returns None when the payload has no ASIN or no title — those two are the
    minimum for a card to be clickable, and a product missing either is a
    placeholder Audible has not finished publishing.

    Every other field degrades to empty/None. This runs over live third-party
    JSON on every search, so a missing key must never raise.
    """
    if not isinstance(product, dict):
        return None
    asin = str(product.get("asin") or "").strip()
    title = str(product.get("title") or "").strip()
    if not asin or not title:
        return None

    cover, cover_large = best_cover(product.get("product_images"))

    runtime = product.get("runtime_length_min")
    try:
        runtime_minutes = int(runtime) if runtime is not None else None
    except (TypeError, ValueError):
        runtime_minutes = None
    if runtime_minutes is not None and runtime_minutes <= 0:
        runtime_minutes = None

    # release_date is the storefront date; issue_date is the original publication.
    # Prefer release_date and fall back, both are plain ISO dates.
    release_date = str(product.get("release_date") or product.get("issue_date") or "").strip() or None

    return AudiobookItem(
        asin=asin,
        title=title,
        subtitle=str(product.get("subtitle") or "").strip(),
        authors=_parse_people(product.get("authors")),
        narrators=_parse_people(product.get("narrators")),
        series=_parse_series(product.get("series")),
        publisher=str(product.get("publisher_name") or "").strip(),
        # publisher_summary only ships with the detail response groups; on a
        # search payload the merchandising blurb is the whole description we get.
        summary=clean_summary_html(
            product.get("publisher_summary") or product.get("merchandising_summary")
        ),
        short_summary=clean_summary_html(product.get("merchandising_summary")),
        release_date=release_date,
        runtime_minutes=runtime_minutes,
        cover_url=cover,
        cover_url_large=cover_large,
        sample_url=str(product.get("sample_url") or "").strip() or None,
        rating=_parse_rating(product.get("rating")),
        genres=_parse_genres(product.get("category_ladders")),
        language=str(product.get("language") or "").strip(),
        format_type=str(product.get("format_type") or "").strip(),
        is_adult=bool(product.get("is_adult_product")),
        source="audible",
    )


def apple_result_to_item(result: Any) -> Optional[AudiobookItem]:
    """Convert one iTunes audiobook search result to an AudiobookItem.

    The fallback path. Apple has no narrator, no series and no runtime for
    audiobooks, so those come back empty and the UI simply shows fewer facts.
    The ASIN slot carries Apple's collectionId and source is "apple" — callers
    must not feed that id back to Audible.
    """
    if not isinstance(result, dict):
        return None
    collection_id = result.get("collectionId")
    title = str(result.get("collectionName") or "").strip()
    if not collection_id or not title:
        return None

    artwork = upgrade_apple_artwork(result.get("artworkUrl100") or result.get("artworkUrl60"))
    author_name = str(result.get("artistName") or "").strip()
    authors = [AudiobookPerson(name=author_name)] if author_name else []
    genre = str(result.get("primaryGenreName") or "").strip()

    release_date = str(result.get("releaseDate") or "").strip()
    if "T" in release_date:
        release_date = release_date.split("T", 1)[0]

    summary = clean_summary_html(result.get("description"))
    return AudiobookItem(
        asin=str(collection_id),
        title=title,
        subtitle="",
        authors=authors,
        narrators=[],
        series=[],
        publisher=str(result.get("copyright") or "").strip(),
        summary=summary,
        short_summary=summary.split("\n", 1)[0] if summary else "",
        release_date=release_date or None,
        runtime_minutes=None,
        cover_url=artwork,
        cover_url_large=artwork,
        sample_url=str(result.get("previewUrl") or "").strip() or None,
        rating=None,
        genres=[genre] if genre else [],
        language=str(result.get("country") or "").strip(),
        format_type="",
        is_adult=str(result.get("collectionExplicitness") or "") == "explicit",
        source="apple",
    )



# ---------------------------------------------------------------------------
# Person profile helpers — pure, so the grouping rules are testable on their own
# ---------------------------------------------------------------------------

# How deep a bibliography goes. Three pages of 50 covers every working author
# on the store without turning one page view into a crawl.
_PROFILE_MAX_BOOKS = 150

# A rating average means nothing on a handful of votes, so a title needs this
# many before it can be called a highlight.
_HIGHLIGHT_MIN_RATINGS = 50


def is_credited(book: AudiobookItem, name: str, role: str) -> bool:
    """True when this person really holds that credit on this book.

    Case-folded exact match on the whole name. Substring matching would fold
    "Andy Weir" into "Andy Weirstein"; a looser match is not worth the wrong
    book appearing on someone's page.
    """
    wanted = (name or "").strip().casefold()
    if not wanted:
        return False
    people = book.narrators if role == "narrator" else book.authors
    return any(person.name.strip().casefold() == wanted for person in people)


def group_by_series(
    books: List[AudiobookItem],
) -> Tuple[List[Tuple[str, Optional[str], List[AudiobookItem]]], List[AudiobookItem]]:
    """Split a bibliography into series groups and standalones.

    A book can belong to several series at once — every Mistborn novel is also
    in The Cosmere — so putting each book in all of them would list the same
    title three times under three headings. Each book instead goes to its most
    SPECIFIC series: the one with the fewest of this person's books in it. That
    puts the Mistborn novels under Mistborn and leaves the wider universe to the
    titles that have no tighter home.

    A group of one is not a series, it is a book that happens to carry a series
    tag, so those fall through to standalone.

    Groups are ordered by size then title; books inside a group by sequence,
    with the unnumbered companions last.
    """
    sizes: Dict[str, int] = {}
    for book in books:
        for entry in book.series:
            sizes[entry.title] = sizes.get(entry.title, 0) + 1

    buckets: Dict[str, List[AudiobookItem]] = {}
    asins: Dict[str, Optional[str]] = {}
    standalone: List[AudiobookItem] = []

    for book in books:
        if not book.series:
            standalone.append(book)
            continue
        # Fewest books first, then the longer name, so "The Mistborn Saga" wins
        # over the broader "The Cosmere" when both hold the same count.
        best = min(book.series, key=lambda e: (sizes.get(e.title, 0), -len(e.title)))
        buckets.setdefault(best.title, []).append(book)
        asins.setdefault(best.title, best.asin)

    groups: List[Tuple[str, Optional[str], List[AudiobookItem]]] = []
    for title, group in buckets.items():
        group = collapse_editions(group, title)
        if len(group) < 2:
            standalone.extend(group)
            continue
        group.sort(key=lambda b: _series_sort_key(b, title))
        groups.append((title, asins.get(title), group))

    groups.sort(key=lambda row: (-len(row[2]), row[0].casefold()))
    standalone.sort(key=lambda b: (b.release_date or "", b.title.casefold()), reverse=True)
    return groups, standalone


def collapse_editions(books: List[AudiobookItem], series_title: str) -> List[AudiobookItem]:
    """One entry per position in a series.

    Audible sells the same instalment more than once — the standard narration
    and a separate "[Dramatized Adaptation]" with a full cast are two products at
    the same position. Listing both makes a six-book series read as twelve books
    with every number appearing twice, which is exactly the question a reading
    order is supposed to answer.

    The most-rated edition wins the slot, which reliably picks the main
    narration over the adaptation. Nothing is lost from the app: the other
    editions are still reachable by search and from the book itself.

    Entries with no sequence are never collapsed — with no position to share,
    there is no way to know two of them are the same instalment.
    """
    by_slot: Dict[float, AudiobookItem] = {}
    unnumbered: List[AudiobookItem] = []

    for book in books:
        entry = next((e for e in book.series if e.title == series_title), None)
        slot = entry.sequence_value if entry else None
        if slot is None:
            unnumbered.append(book)
            continue
        held = by_slot.get(slot)
        if held is None or _rating_weight(book) > _rating_weight(held):
            by_slot[slot] = book

    return list(by_slot.values()) + unnumbered


def _rating_weight(book: AudiobookItem) -> int:
    """How much of a hearing a title has had. Ties break toward the better rated."""
    return book.rating.count if book.rating else 0


def _series_sort_key(book: AudiobookItem, series_title: str) -> tuple:
    """Reading order within one series: numbered first, then the rest by title."""
    entry = next((e for e in book.series if e.title == series_title), None)
    sequence = entry.sequence_value if entry else None
    return (sequence is None, sequence if sequence is not None else 0.0, book.title.casefold())


def count_collaborators(books: List[AudiobookItem], role: str) -> List[Dict[str, Any]]:
    """Who this person works with most: narrators for an author, authors for a narrator."""
    counter: Counter = Counter()
    for book in books:
        people = book.authors if role == "narrator" else book.narrators
        for person in people:
            clean = person.name.strip()
            if clean:
                counter[clean] += 1
    return [{"name": name, "count": count} for name, count in counter.most_common(10)]


def pick_highlights(books: List[AudiobookItem], limit: int = 12) -> List[AudiobookItem]:
    """Their best-rated titles.

    Rated titles are ranked by average, but only once enough people have voted —
    otherwise a novella with four five-star ratings outranks a career-defining
    novel with three hundred thousand. When nothing clears the bar the whole
    bibliography is ranked on whatever ratings exist, so the shelf is never
    empty for a newer name.
    """
    rated = [b for b in books if b.rating and b.rating.average is not None]
    qualified = [b for b in rated if b.rating.count >= _HIGHLIGHT_MIN_RATINGS]
    pool = qualified or rated
    pool = sorted(
        pool,
        key=lambda b: (b.rating.average or 0.0, b.rating.count),
        reverse=True,
    )
    return pool[:limit]


def diversify_shelf(
    items: Sequence[AudiobookItem],
    max_per_series: int = 2,
    max_per_author: int = 2,
) -> List[AudiobookItem]:
    """Stop one series or one author from swallowing a whole shelf.

    Sorting a broad genre by bestsellers returns what actually sells, which is
    Harry Potter one through seven — seven of the ten slots on the Science
    Fiction & Fantasy row, and most of Literature & Fiction as well. Accurate
    and useless: a browse shelf exists to show a reader something they had not
    thought of.

    Capping the series alone is not enough, because Audible sells the same books
    under several series names at once — "Harry Potter" and "Harry Potter
    (Full-Cast Edition)" are different series, so a series cap of two lets four
    through. The author cap is what actually holds the line.

    Order is preserved, so the best-selling entry keeps its place. Items with no
    series or no author are never limited by the missing one.
    """
    kept: List[AudiobookItem] = []
    series_counts: Dict[str, int] = {}
    author_counts: Dict[str, int] = {}

    for item in items:
        series = item.series[0].title.casefold() if item.series else ""
        if series and series_counts.get(series, 0) >= max(1, max_per_series):
            continue
        author = item.authors[0].name.casefold() if item.authors else ""
        if author and author_counts.get(author, 0) >= max(1, max_per_author):
            continue
        if series:
            series_counts[series] = series_counts.get(series, 0) + 1
        if author:
            author_counts[author] = author_counts.get(author, 0) + 1
        kept.append(item)
    return kept


def dedupe_across_shelves(
    shelves: Sequence[Sequence[AudiobookItem]],
    limit: int,
    max_per_series: int = 2,
    max_per_author: int = 2,
) -> List[List[AudiobookItem]]:
    """Fill several shelves from one page without repeating a title.

    Shelves are filled in order and the first one to claim a title keeps it —
    the same mega-seller sits in several genres at once, so without this the
    browse page shows the same covers three rows apart and looks broken.

    Each shelf is trimmed to ``limit`` AFTER de-duplication, which is why the
    caller fetches more than it displays: dropping repeats out of an exactly
    sized shelf leaves a short row.
    """
    used: set = set()
    out: List[List[AudiobookItem]] = []
    for shelf in shelves:
        picked: List[AudiobookItem] = []
        for item in diversify_shelf(shelf, max_per_series, max_per_author):
            if item.asin in used:
                continue
            used.add(item.asin)
            picked.append(item)
            if len(picked) >= limit:
                break
        out.append(picked)
    return out


def _empty_profile(name: str, role: str) -> Dict[str, Any]:
    """The shape callers get for an unknown person, so the UI has no special case."""
    return {
        "name": name,
        "role": role,
        "total_books": 0,
        "total_runtime_minutes": 0,
        "runtime_formatted": "",
        "genres": [],
        "collaborators": [],
        "series": [],
        "standalone": [],
        "highlights": [],
    }


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

class _TTLCache:
    """Small thread-safe TTL cache for outbound catalog responses.

    Browse pages fire the same handful of queries constantly (the same chart,
    the same genre shelf, a detail page re-opened) and Audible has no published
    rate limit, which is a reason to be careful rather than a licence not to be.

    Entries are evicted lazily on read plus a bulk sweep when the store grows
    past _MAX_ENTRIES, so an idle process does not hold a timer.
    """

    _MAX_ENTRIES = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        now = time.time()
        with self._lock:
            if len(self._store) >= self._MAX_ENTRIES:
                for stale_key in [k for k, (exp, _) in self._store.items() if exp <= now]:
                    self._store.pop(stale_key, None)
                if len(self._store) >= self._MAX_ENTRIES:
                    self._store.clear()
            self._store[key] = (now + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

# Search modes, mapped to the query parameter Audible expects for each. Only
# these four are accepted; anything else falls back to keywords rather than
# forwarding an unknown parameter that would come back a 400.
SEARCH_TYPES: Dict[str, str] = {
    "keywords": "keywords",
    "title": "title",
    "author": "author",
    "narrator": "narrator",
}

# Sort orders verified against the live catalog. Audible 400s on an unknown
# sort, so this is a whitelist and not a pass-through.
SORT_ORDERS: Dict[str, str] = {
    "relevance": "Relevance",
    "bestsellers": "BestSellers",
    "newest": "-ReleaseDate",
}


class AudiobookClient:
    """Searches Audible's public catalog and falls back to Apple.

    No authentication required. Holds a requests.Session, a private request
    pacer and a TTL cache; nothing about it needs configuring before use, which
    is why get_audiobook_client() can construct it lazily.

    Every public method fails open — network and parse errors are logged and an
    empty list (or None for single-item lookups) is returned. A browse page with
    one dead shelf still renders; a browse page that 500s does not.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        self._cache = _TTLCache()
        self._pace_lock = threading.Lock()
        self._next_request_at = 0.0

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _pace(self) -> None:
        """Space outbound calls by _MIN_REQUEST_GAP.

        Reservation model, same as the shared throttles: the next allowed time is
        claimed under the lock and then slept to outside it, so two threads
        arriving together get two different slots instead of both computing "no
        wait" and firing at once.

        This is a private budget. Audible is a metadata service, not an indexer,
        so it deliberately does not touch core.prowlarr_throttle or
        core.slskd_throttle — spending a shared search slot on a browse request
        would slow real music and video downloads for nothing.
        """
        with self._pace_lock:
            now = time.monotonic()
            at = max(now, self._next_request_at)
            self._next_request_at = at + _MIN_REQUEST_GAP
        delay = at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _get_json(
        self,
        url: str,
        params: Dict[str, Any],
        cache_ttl: float,
        label: str,
        result_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Paced, cached GET returning parsed JSON, or None on any failure.

        The cache key is the full request line, so two callers asking for the
        same page of the same query share one round trip and a marketplace
        change is a different key.

        ``result_key`` names the key a processed response must carry. A response
        without it is a shed, and is returned as None AND NOT CACHED — caching
        one would keep an empty catalogue on screen for the full hour after the
        store recovered.
        """
        cache_key = f"{url}?{sorted(params.items())}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        self._pace()
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("%s failed (%s): %s", label, params, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("%s returned a non-object payload", label)
            return None
        if result_key is not None and not response_was_processed(data, result_key):
            logger.info("%s was shed by the storefront (no %r in the response)", label, result_key)
            return None
        self._cache.set(cache_key, data, cache_ttl)
        return data

    def _get_catalog(
        self,
        marketplace: Optional[str],
        path: str,
        params: Dict[str, Any],
        cache_ttl: float,
        label: str,
        result_key: str = "products",
        params_for: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch from the catalog, riding out a storefront that will not answer.

        Audible sheds load intermittently and per-storefront: while the US store
        was returning nothing for every search, the UK and AU stores answered
        the same queries from the same machine, and the US store recovered on
        its own a minute later.

        So a shed is retried briefly, and then the same question is put to the
        next English storefront. That is the difference between "audiobooks are
        broken this morning" and a page the user never notices was rerouted.
        """
        for index, code in enumerate(marketplace_chain(marketplace)):
            # Some parameters only mean something to one storefront — a genre id
            # in particular — so they are rebuilt for whichever store is asked.
            call_params = params_for(code) if params_for is not None else params
            if call_params is None:
                continue
            for attempt in range(_SHED_RETRY_ATTEMPTS):
                data = self._get_json(
                    self._catalog_url(code, path), call_params, cache_ttl, label,
                    result_key=result_key,
                )
                if data is not None:
                    if index:
                        logger.info("%s answered from the %s store instead", label, code)
                    return data
                if attempt + 1 < _SHED_RETRY_ATTEMPTS:
                    time.sleep(_SHED_RETRY_SLEEP)
        logger.warning("%s: no storefront would answer", label)
        return None

    def resolve_category(self, category: Optional[str], marketplace: str) -> str:
        """The id this storefront uses for a genre, or "" when it has no such genre.

        A numeric value is passed straight through — it is already an id. Anything
        else is matched by folded name against that store's own tree, because an
        id from one store means nothing to another.
        """
        raw = str(category or "").strip()
        if not raw:
            return ""
        if raw.isdigit():
            return raw
        wanted = normalize_category_name(raw)
        for node in self.get_categories(marketplace):
            if normalize_category_name(node.get("name")) == wanted:
                return str(node.get("id") or "")
            for child in node.get("children") or []:
                if normalize_category_name(child.get("name")) == wanted:
                    return str(child.get("id") or "")
        return ""

    def _catalog_url(self, marketplace: Optional[str], path: str = "") -> str:
        base = f"https://{marketplace_host(marketplace)}/1.0/catalog/products"
        return f"{base}/{path}" if path else base

    def _products(self, data: Optional[Dict[str, Any]], key: str = "products") -> List[AudiobookItem]:
        """Convert a catalog response's product array into items, dropping unusable ones."""
        if not data:
            return []
        items: List[AudiobookItem] = []
        for product in data.get(key) or []:
            item = product_to_item(product)
            if item is not None:
                items.append(item)
        return items

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        search_type: str = "keywords",
        limit: int = 20,
        marketplace: str = _DEFAULT_MARKETPLACE,
        page: int = 1,
        sort: Optional[str] = None,
        strict: bool = False,
    ) -> List[AudiobookItem]:
        """Search the Audible catalog.

        search_type picks which field is queried: keywords (everything), title,
        author or narrator. Narrator search is the one Apple cannot do at all and
        is the reason Audible is the primary source.

        Returns [] for an empty query — the caller should not have to guard, and
        an empty query against the catalog returns the whole storefront.
        """
        if not query or not query.strip():
            return []
        param = SEARCH_TYPES.get((search_type or "").strip().lower(), "keywords")
        params: Dict[str, Any] = {
            param: query.strip(),
            "num_results": max(1, min(int(limit or 20), _MAX_PAGE_SIZE)),
            "page": page_offset(page),
            "response_groups": _SEARCH_RESPONSE_GROUPS,
            "image_sizes": _IMAGE_SIZES,
        }
        sort_key = SORT_ORDERS.get((sort or "").strip().lower())
        if sort_key:
            params["products_sort_by"] = sort_key

        data = self._get_catalog(
            marketplace, "", params, _SEARCH_TTL, "Audible search",
        )
        if data is None and strict:
            raise RuntimeError("The audiobook catalogue is unavailable. Try again later.")
        items = self._products(data)
        logger.debug("search(%r, type=%s) -> %d results", query, param, len(items))
        return items

    def search_apple(
        self,
        query: str,
        limit: int = 20,
        country: str = "us",
    ) -> List[AudiobookItem]:
        """Fallback search against the iTunes audiobook catalog.

        Only reached when Audible returns nothing. Results are thin by
        construction — see apple_result_to_item.
        """
        if not query or not query.strip():
            return []
        params = {
            "term": query.strip(),
            "media": "audiobook",
            "limit": max(1, min(int(limit or 20), 200)),
            "country": (country or "us").strip().lower() or "us",
        }
        data = self._get_json(_APPLE_SEARCH_URL, params, _SEARCH_TTL, "Apple audiobook search")
        if not data:
            return []
        items: List[AudiobookItem] = []
        for result in data.get("results") or []:
            item = apple_result_to_item(result)
            if item is not None:
                items.append(item)
        logger.debug("search_apple(%r) -> %d results", query, len(items))
        return items

    def search_with_fallback(
        self,
        query: str,
        search_type: str = "keywords",
        limit: int = 20,
        marketplace: str = _DEFAULT_MARKETPLACE,
        page: int = 1,
        sort: Optional[str] = None,
    ) -> Tuple[List[AudiobookItem], str]:
        """Audible first, Apple only if Audible found nothing.

        Returns (items, source_used) so the caller can tell the UI which
        hierarchy level answered — a page built on Apple data should not promise
        narrators it does not have.

        The fallback is skipped for narrator search past page one: Apple cannot
        search narrators, and paging a fallback that never had page one is a
        guaranteed empty round trip.
        """
        items = self.search(query, search_type, limit, marketplace, page, sort)
        if items:
            return items, "audible"
        mode = (search_type or "").strip().lower()
        if mode == "narrator" or page > 1:
            return [], "audible"
        fallback = self.search_apple(query, limit=limit, country=marketplace)
        return fallback, ("apple" if fallback else "audible")

    # ------------------------------------------------------------------
    # Single product
    # ------------------------------------------------------------------

    def get_book(self, asin: str, marketplace: str = _DEFAULT_MARKETPLACE) -> Optional[AudiobookItem]:
        """Full metadata for one ASIN, or None when it does not resolve.

        Cached for a day: a published product's title, narrator and runtime do
        not change, and the detail page is the most re-opened view in the app.
        """
        asin = (asin or "").strip()
        if not asin:
            return None
        data = self._get_catalog(
            marketplace, asin,
            {"response_groups": _DETAIL_RESPONSE_GROUPS, "image_sizes": _IMAGE_SIZES},
            _DETAIL_TTL,
            "Audible product detail",
            result_key="product",
        )
        if not data:
            return None
        return product_to_item(data.get("product"))

    def get_books_by_asins(
        self,
        asins: List[str],
        marketplace: str = _DEFAULT_MARKETPLACE,
    ) -> List[AudiobookItem]:
        """Batch lookup for a list of ASINs in one request.

        This is what a watchlist or library view should use to refresh many rows
        at once instead of one detail call per row. Audible accepts a comma list
        on the collection endpoint; the batch is capped at the page size.
        """
        clean = [str(a).strip() for a in (asins or []) if str(a or "").strip()]
        if not clean:
            return []
        data = self._get_catalog(
            marketplace, "",
            {
                "asins": ",".join(clean[:_MAX_PAGE_SIZE]),
                "response_groups": _DETAIL_RESPONSE_GROUPS,
                "image_sizes": _IMAGE_SIZES,
            },
            _DETAIL_TTL,
            "Audible batch lookup",
        )
        return self._products(data)

    def get_similar(
        self,
        asin: str,
        limit: int = 12,
        marketplace: str = _DEFAULT_MARKETPLACE,
    ) -> List[AudiobookItem]:
        """Audible's own "listeners also enjoyed" list for an ASIN.

        Returned under similar_products rather than products, which is the only
        reason this is not just another _products() call.
        """
        asin = (asin or "").strip()
        if not asin:
            return []
        data = self._get_catalog(
            marketplace, f"{asin}/sims",
            {
                "num_results": max(1, min(int(limit or 12), _MAX_PAGE_SIZE)),
                "response_groups": _SEARCH_RESPONSE_GROUPS,
                "image_sizes": _IMAGE_SIZES,
            },
            _DETAIL_TTL,
            "Audible similar products",
            result_key="similar_products",
        )
        return self._products(data, key="similar_products")

    # ------------------------------------------------------------------
    # Shelves
    # ------------------------------------------------------------------

    def browse(
        self,
        category: Optional[str] = None,
        sort: str = "bestsellers",
        limit: int = 25,
        marketplace: str = _DEFAULT_MARKETPLACE,
        page: int = 1,
    ) -> List[AudiobookItem]:
        """A shelf: the catalog sorted, optionally narrowed to one genre.

        This is the engine behind every row on the browse page. ``category`` is a
        genre NAME or an id. Prefer the name: ids are not portable between
        storefronts, so an id resolved against one store silently returns
        nothing on another — including when this call falls back.

        No keywords are sent. Audible treats an unfiltered sorted query as
        "the storefront", which is exactly what a chart row is.
        """
        params: Dict[str, Any] = {
            "num_results": max(1, min(int(limit or 25), _MAX_PAGE_SIZE)),
            "page": page_offset(page),
            "products_sort_by": SORT_ORDERS.get((sort or "").strip().lower(), "BestSellers"),
            "response_groups": _SEARCH_RESPONSE_GROUPS,
            "image_sizes": _IMAGE_SIZES,
        }
        wanted = str(category).strip() if category else ""

        def params_for(code: str) -> Optional[Dict[str, Any]]:
            if not wanted:
                return params
            resolved = self.resolve_category(wanted, code)
            if not resolved:
                # This store has no such genre. Skipping it beats asking for the
                # whole storefront and calling the result a genre shelf.
                logger.debug("The %s store has no genre %r", code, wanted)
                return None
            return {**params, "category_id": resolved}

        data = self._get_catalog(
            marketplace, "", params, _SEARCH_TTL, "Audible browse",
            params_for=params_for,
        )
        return self._products(data)

    def get_bestsellers(
        self,
        category: Optional[str] = None,
        limit: int = 25,
        marketplace: str = _DEFAULT_MARKETPLACE,
    ) -> List[AudiobookItem]:
        """Top sellers, overall or within one genre."""
        return self.browse(category, "bestsellers", limit, marketplace)

    def get_new_releases(
        self,
        category: Optional[str] = None,
        limit: int = 25,
        marketplace: str = _DEFAULT_MARKETPLACE,
    ) -> List[AudiobookItem]:
        """Newest first, overall or within one genre."""
        return self.browse(category, "newest", limit, marketplace)

    def get_categories(self, marketplace: str = _DEFAULT_MARKETPLACE) -> List[Dict[str, Any]]:
        """The genre tree: [{id, name, children: [{id, name}, ...]}, ...].

        Cached for a day. The tree is what turns the browse page into real
        navigation instead of a hardcoded list of search terms — the podcast page
        had to hardcode its categories because iTunes exposes no such tree.
        """
        data = None
        for code in marketplace_chain(marketplace):
            url = f"https://{marketplace_host(code)}/1.0/catalog/categories"
            data = self._get_json(
                url, {"category_type": "Genres"}, _DETAIL_TTL, "Audible categories",
                result_key="categories",
            )
            if data is not None:
                break
        if not data:
            return []
        out: List[Dict[str, Any]] = []
        for node in data.get("categories") or []:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or "").strip()
            node_id = str(node.get("id") or "").strip()
            if not name or not node_id:
                continue
            children = []
            for child in node.get("children") or []:
                if not isinstance(child, dict):
                    continue
                child_name = str(child.get("name") or "").strip()
                child_id = str(child.get("id") or "").strip()
                if child_name and child_id:
                    children.append({"id": child_id, "name": child_name})
            out.append({"id": node_id, "name": name, "children": children})
        return out

    # ------------------------------------------------------------------
    # Series
    # ------------------------------------------------------------------

    def get_series(
        self,
        series_title: str,
        series_asin: Optional[str] = None,
        limit: int = 50,
        marketplace: str = _DEFAULT_MARKETPLACE,
    ) -> List[AudiobookItem]:
        """Every book in a series, in reading order.

        Audible has no "list a series" endpoint. series_asin exists on the
        product and looks like it should be queryable, but the catalog silently
        IGNORES it as a filter and hands back the unfiltered storefront — 68,000
        "results" for one trilogy, verified against the live API. Passing it
        through would quietly fill a series shelf with unrelated bestsellers.

        So: search for the series, keep only the products that actually carry the
        matching series, and sort by sequence. Matching prefers the ASIN when one
        is known and falls back to a case-folded title match, which is what keeps
        a title search for "Mistborn" from dragging in every book with that word.

        Each term from series_query_variants() is tried in turn until one
        produces matches, because the series name and the book names differ.
        Keyword search is the last resort — it is fuzzy enough to return
        "Mistletoe Murders" for "Mistborn", so it only runs when the precise
        title searches all came back with nothing in the series.

        Books with no parseable sequence (novellas, companions) sort to the end
        in title order rather than being dropped — they belong to the series and
        the UI can label them.
        """
        title = (series_title or "").strip()
        if not title:
            return []
        target_asin = (series_asin or "").strip() or None

        matched: List[AudiobookItem] = []
        for variant in series_query_variants(title):
            matched = self._collect_series(
                self.search(variant, "title", limit=_MAX_PAGE_SIZE, marketplace=marketplace),
                title, target_asin,
            )
            if matched:
                break
        if not matched:
            matched = self._collect_series(
                self.search(title, "keywords", limit=_MAX_PAGE_SIZE, marketplace=marketplace),
                title, target_asin,
            )
        return matched[: max(1, int(limit or 50))]

    @classmethod
    def _collect_series(
        cls,
        candidates: List[AudiobookItem],
        title: str,
        target_asin: Optional[str],
    ) -> List[AudiobookItem]:
        """Filter a result list down to one series and put it in reading order.

        Sequence None sorts last so unnumbered companions land after book 12
        instead of before book 1.
        """
        rows: List[Tuple[bool, float, str, AudiobookItem]] = []
        seen: set = set()
        for item in candidates:
            entry = cls._matching_series(item, title, target_asin)
            if entry is None or item.asin in seen:
                continue
            seen.add(item.asin)
            # Put the series we were asked about first on the item. A book can be
            # in several series at once (The Lost Metal is book 7 of the Mistborn
            # Saga and book 4 of Wax & Wayne), and a card on a series shelf must
            # show its position in THAT series, not in whichever one Audible
            # happened to list first.
            if item.series and item.series[0] is not entry:
                item.series = [entry] + [s for s in item.series if s is not entry]
            sequence = entry.sequence_value
            rows.append((sequence is None, sequence if sequence is not None else 0.0,
                         item.title.casefold(), item))
        rows.sort(key=lambda row: row[:3])
        return [row[3] for row in rows]

    @staticmethod
    def _matching_series(
        item: AudiobookItem,
        title: str,
        target_asin: Optional[str],
    ) -> Optional[AudiobookSeries]:
        """The series entry on item that matches the requested series, if any.

        ASIN match wins when we have one; otherwise a case-folded title match.
        Returns None when the book is not in the series at all, which is how a
        title search for "Mistborn" drops the unrelated results it drags in.
        """
        folded = title.casefold()
        for entry in item.series:
            if target_asin and entry.asin and entry.asin == target_asin:
                return entry
            if not target_asin and entry.title.casefold() == folded:
                return entry
        return None

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------

    def get_by_author(
        self,
        author: str,
        limit: int = 30,
        marketplace: str = _DEFAULT_MARKETPLACE,
        sort: str = "bestsellers",
    ) -> List[AudiobookItem]:
        """An author's bibliography, bestsellers first by default."""
        return self.search(author, "author", limit=limit, marketplace=marketplace, sort=sort)

    def get_by_narrator(
        self,
        narrator: str,
        limit: int = 30,
        marketplace: str = _DEFAULT_MARKETPLACE,
        sort: str = "bestsellers",
    ) -> List[AudiobookItem]:
        """Everything a narrator has performed.

        The feature no other audiobook source can offer, and the one listeners
        actually browse by once they have a favourite narrator.
        """
        return self.search(narrator, "narrator", limit=limit, marketplace=marketplace, sort=sort)

    # ------------------------------------------------------------------
    # Person profiles
    # ------------------------------------------------------------------

    def get_person_profile(
        self,
        name: str,
        role: str = "author",
        marketplace: str = _DEFAULT_MARKETPLACE,
        max_books: int = _PROFILE_MAX_BOOKS,
    ) -> Dict[str, Any]:
        """Everything one author wrote or one narrator performed, grouped.

        Keyed on the NAME. Audible hands out an ASIN for every author and it
        looks like the right identifier, but the catalog accepts ``author_asin``
        as a filter and then ignores it, returning the unfiltered storefront —
        the same trap ``series_asin`` sets, verified the same way. Name search,
        by contrast, is exact: a 30-result query for an author came back 30/30
        genuinely theirs.

        Pages past the 50-result cap because a prolific author runs well past it
        and a bibliography that silently stops at 50 hides half a career.

        Returns a plain dict rather than a dataclass: it is a view assembled for
        one page, not a domain object anything else reads.
        """
        name = (name or "").strip()
        role = (role or "author").strip().lower()
        if role not in ("author", "narrator"):
            role = "author"
        if not name:
            return _empty_profile(name, role)

        books = self._collect_credits(name, role, marketplace, max_books)
        if not books:
            return _empty_profile(name, role)

        series, standalone = group_by_series(books)
        collaborators = count_collaborators(books, role)
        genres = [genre for genre, _ in Counter(
            genre for book in books for genre in book.genres
        ).most_common(12)]
        total_runtime = sum(book.runtime_minutes or 0 for book in books)

        return {
            "name": name,
            "role": role,
            "total_books": len(books),
            "total_runtime_minutes": total_runtime,
            "runtime_formatted": format_runtime(total_runtime),
            "genres": genres,
            "collaborators": collaborators,
            "series": [
                {"title": title, "asin": asin, "books": [b.to_dict() for b in group]}
                for title, asin, group in series
            ],
            "standalone": [book.to_dict() for book in standalone],
            "highlights": [book.to_dict() for book in pick_highlights(books)],
        }

    def _collect_credits(
        self,
        name: str,
        role: str,
        marketplace: str,
        max_books: int,
    ) -> List[AudiobookItem]:
        """Page the catalog for one person's credits, de-duplicated and verified.

        Every result is re-checked against the credit list before it is kept.
        The catalog widens a person search at the edges — a narrator query can
        return a title they are not on — and one wrong book on a bibliography
        page is more visible than a missing one.

        Editions outside the storefront's language are held back, not dropped: if
        filtering leaves nothing at all the translations are returned instead, so
        a person who only publishes in one language still gets a page.
        """
        collected: List[AudiobookItem] = []
        translations: List[AudiobookItem] = []
        seen: set = set()
        language = marketplace_language(marketplace)
        pages = max(1, -(-max_books // _MAX_PAGE_SIZE))   # ceil

        for page in range(1, pages + 1):
            batch = self.search(
                name, role, limit=_MAX_PAGE_SIZE,
                marketplace=marketplace, page=page, sort="bestsellers",
            )
            if not batch:
                break
            for book in batch:
                if book.asin in seen or not is_credited(book, name, role):
                    continue
                seen.add(book.asin)
                # Held aside rather than dropped: a translator or a narrator who
                # only works in one language would otherwise get an empty page.
                if language and book.language and book.language.strip().casefold() != language:
                    translations.append(book)
                    continue
                collected.append(book)
                if len(collected) >= max_books:
                    return collected
            # A short page is the last page; asking for the next one is a
            # guaranteed empty round trip.
            if len(batch) < _MAX_PAGE_SIZE:
                break
        return collected or translations[:max_books]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Drop every cached response. Used by tests and the settings cache reset."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_default_client: Optional[AudiobookClient] = None
_singleton_lock = threading.Lock()


def get_audiobook_client() -> AudiobookClient:
    """Return the process-wide AudiobookClient singleton.

    Constructed lazily under a lock on first call. Sharing one instance is the
    point: the TTL cache and the request pacer only mean anything if every
    request handler goes through the same object.
    """
    global _default_client
    if _default_client is None:
        with _singleton_lock:
            if _default_client is None:
                _default_client = AudiobookClient()
    return _default_client
