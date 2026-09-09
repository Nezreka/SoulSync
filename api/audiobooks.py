"""Audiobooks API Blueprint — discovery, detail and preview streaming for audiobooks.

Endpoints:
  - GET /api/audiobooks/search:        multi-mode search (keywords/title/author/narrator).
  - GET /api/audiobooks/book/<asin>:   full detail for one title.
  - GET /api/audiobooks/similar/<asin>: Audible's "listeners also enjoyed" list.
  - GET /api/audiobooks/series:        every book in a series, in reading order.
  - GET /api/audiobooks/author:        an author's bibliography.
  - GET /api/audiobooks/narrator:      everything a narrator has performed.
  - GET /api/audiobooks/person:        an author's or narrator's grouped bibliography.
  - GET /api/audiobooks/browse:        one shelf, sorted, optionally by genre.
  - GET /api/audiobooks/bestsellers:   browse pinned to the bestseller sort.
  - GET /api/audiobooks/new-releases:  browse pinned to the newest sort.
  - GET /api/audiobooks/categories:    the genre tree.
  - GET /api/audiobooks/home:          the whole browse page in one round trip.
  - GET /api/audiobooks/sample-proxy:  CORS-safe streaming proxy for audio previews.

Acquisition (all scoped to the audiobook subsystem):
  - GET    /api/audiobooks/wishlist:          what is wanted, with counts and worker state.
  - POST   /api/audiobooks/wishlist:          want a book by ASIN.
  - PATCH  /api/audiobooks/wishlist/<asin>:   change its narrator strictness.
  - DELETE /api/audiobooks/wishlist/<asin>:   stop wanting it.
  - POST   /api/audiobooks/wishlist/search:   run a wishlist pass now.
  - GET    /api/audiobooks/releases/<asin>:   what the indexers actually have.
  - POST   /api/audiobooks/grab:              send one release to the download client.
  - GET    /api/audiobooks/downloads:         what is downloading or has finished.
  - GET    /api/audiobooks/watchlist:         authors being followed.
  - POST   /api/audiobooks/watchlist:         follow an author.
  - DELETE /api/audiobooks/watchlist/<name>:  stop following them.
  - POST   /api/audiobooks/watchlist/scan:    check followed authors now.

Purely additive. Every route lives under /api/audiobooks, every write goes to the
audiobook subsystem's OWN database file, and nothing here touches the music worker pool,
the music wishlist, the music download batches, or any music table.

Two different budgets are in play and the difference matters. The catalogue endpoints talk
to Audible, which is a metadata service, and are paced by the audiobook client's own
private gap. The release search and the wishlist pass talk to Prowlarr, which forwards to
real indexers, and those deliberately DO spend the shared throttle the music and video
sides spend — it is one Prowlarr in front of one set of indexers, and an indexer cannot
tell which half of the app asked.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from flask import Blueprint, Response, jsonify, request

from core.audiobook_client import (
    SEARCH_TYPES,
    SORT_ORDERS,
    AudiobookItem,
    dedupe_across_shelves,
    get_audiobook_client,
)
from core.audiobook_database import get_audiobook_db
from utils.logging_config import get_logger

logger = get_logger("audiobooks.api")

# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

_MAX_LIMIT = 50   # Audible 400s above this; clamped rather than forwarded
_DEFAULT_LIMIT = 25


def _limit(default: int = _DEFAULT_LIMIT) -> int:
    """Clamp the ``limit`` query parameter into what the catalog accepts.

    A junk value falls back to the default instead of 400ing — a browse page
    with a stale query string should still render.
    """
    try:
        return max(1, min(int(request.args.get("limit", default)), _MAX_LIMIT))
    except (TypeError, ValueError):
        return default


def _page() -> int:
    """The 1-based ``page`` query parameter. The client converts to Audible's 0-based one."""
    try:
        return max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        return 1


def _marketplace() -> str:
    """Storefront code. Unknown codes fall back to US inside the client."""
    return (request.args.get("marketplace") or "us").strip().lower() or "us"


def _sort(default: str = "relevance") -> str:
    """Sort key, validated against the whitelist the client verified against the live API."""
    value = (request.args.get("sort") or "").strip().lower()
    return value if value in SORT_ORDERS else default


def _owned_asins() -> set:
    """Every asin already on disk, in one read.

    Best effort: an unreadable database costs the badge, not the page. Returns
    a set rather than asking per book because a page of results asks about
    twenty at once.
    """
    try:
        return get_audiobook_db().owned_asins()
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the owned audiobooks: %s", exc)
        return set()


def _mark_owned(payloads: List[Dict[str, Any]], owned: Optional[set] = None) -> List[Dict[str, Any]]:
    """Stamp ``owned`` on book payloads, in place.

    Every list route funnels through here, so the badge cannot be right on
    search and missing on a shelf.
    """
    if owned is None:
        owned = _owned_asins()
    for payload in payloads:
        payload["owned"] = str(payload.get("asin") or "") in owned
    return payloads


def _items(items: List[AudiobookItem]) -> List[Dict[str, Any]]:
    """Serialise a result list. The client's to_dict is the only payload shape."""
    return _mark_owned([item.to_dict() for item in items])


# ---------------------------------------------------------------------------
# Sample proxy safety
# ---------------------------------------------------------------------------

# Audio previews are served from these hosts. The proxy exists so the browser can
# play a sample without a CORS or mixed-content failure; it is NOT a general
# fetcher, so the host is checked against this list and anything else is refused.
# Without the check the endpoint is an open relay that will fetch internal
# addresses on behalf of whoever can reach the web UI.
_SAMPLE_HOST_SUFFIXES: Tuple[str, ...] = (
    "audible.com",
    "audible.co.uk",
    "audible.de",
    "audible.fr",
    "audible.ca",
    "audible.com.au",
    "audible.it",
    "audible.es",
    "audible.in",
    "audible.co.jp",
    "apple.com",
    "mzstatic.com",
)

_PROXY_TIMEOUT = 15
_PROXY_CHUNK = 64 * 1024


def is_allowed_sample_url(url: str) -> bool:
    """True when url is an https audio URL on a known preview host.

    Matching is on the registrable suffix with a dot boundary, so
    ``samples.audible.com`` passes and ``audible.com.evil.net`` and
    ``notaudible.com`` do not. http is refused outright — every one of these
    hosts serves https, so a plain-http URL is either a downgrade attempt or a
    redirect target we should not be following.
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in _SAMPLE_HOST_SUFFIXES)


# ---------------------------------------------------------------------------
# Home shelves
# ---------------------------------------------------------------------------

# The browse page's opening rows, keyed by genre NAME rather than id. Audible's
# category ids are per-storefront — of the 23 genre names the US and UK stores
# share, not one has the same id — so a hardcoded id silently returns an empty
# shelf on any store but the one it came from, including when a shed US request
# falls back to the UK.
_HOME_SHELVES: Tuple[Dict[str, str], ...] = (
    {"key": "bestsellers",  "title": "Top Sellers",                  "category": "",   "sort": "bestsellers"},
    {"key": "new",          "title": "New Releases",                 "category": "",   "sort": "newest"},
    {"key": "scifi",        "title": "Science Fiction & Fantasy",    "category": "Science Fiction & Fantasy",    "sort": "bestsellers"},
    {"key": "mystery",      "title": "Mystery, Thriller & Suspense", "category": "Mystery, Thriller & Suspense", "sort": "bestsellers"},
    {"key": "biographies",  "title": "Biographies & Memoirs",        "category": "Biographies & Memoirs",        "sort": "bestsellers"},
    {"key": "fiction",      "title": "Literature & Fiction",         "category": "Literature & Fiction",         "sort": "bestsellers"},
)

# One worker per shelf, capped. The client paces its own outbound calls, so this
# bounds concurrency at the request layer and nothing more.
_HOME_WORKERS = 4

# Each shelf is fetched deeper than it is shown, because de-duplication happens
# after the fetch: the same bestseller appears in several genres, and trimming
# repeats out of an exactly-sized shelf leaves a visibly short row.
_HOME_OVERFETCH = 3


def create_audiobooks_blueprint() -> Blueprint:
    bp = Blueprint("audiobooks_api", __name__, url_prefix="/api/audiobooks")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @bp.route("/search", methods=["GET"])
    def search():
        """Search audiobooks, Audible first with an Apple fallback.

        ``type`` picks the field: keywords, title, author or narrator. The
        response reports which source answered so the UI can avoid promising
        narrator data that an Apple-sourced result does not carry.
        """
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"success": True, "query": "", "results": [], "source": "audible"})

        search_type = (request.args.get("type") or "keywords").strip().lower()
        if search_type not in SEARCH_TYPES:
            search_type = "keywords"

        client = get_audiobook_client()
        items, source = client.search_with_fallback(
            query,
            search_type=search_type,
            limit=_limit(20),
            marketplace=_marketplace(),
            page=_page(),
            sort=_sort(),
        )
        return jsonify({
            "success": True,
            "query": query,
            "type": search_type,
            "page": _page(),
            "source": source,
            "results": _items(items),
        })

    # ------------------------------------------------------------------
    # Single title
    # ------------------------------------------------------------------

    @bp.route("/book/<asin>", methods=["GET"])
    def book_detail(asin: str):
        """Full metadata for one ASIN."""
        item = get_audiobook_client().get_book(asin, marketplace=_marketplace())
        if item is None:
            return jsonify({"success": False, "error": f"No audiobook found for {asin}"}), 404
        return jsonify({"success": True, "book": _mark_owned([item.to_dict()])[0]})

    @bp.route("/similar/<asin>", methods=["GET"])
    def similar(asin: str):
        """Audible's own recommendations for an ASIN."""
        items = get_audiobook_client().get_similar(
            asin, limit=_limit(12), marketplace=_marketplace(),
        )
        return jsonify({"success": True, "asin": asin, "results": _items(items)})

    # ------------------------------------------------------------------
    # Series and people
    # ------------------------------------------------------------------

    @bp.route("/series", methods=["GET"])
    def series():
        """Every book in a series, in reading order.

        ``asin`` is the series ASIN off a product and makes the match exact;
        without it the series is matched on its name, which is looser.
        """
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name parameter is required"}), 400
        items = get_audiobook_client().get_series(
            name,
            series_asin=(request.args.get("asin") or "").strip() or None,
            limit=_limit(50),
            marketplace=_marketplace(),
        )
        return jsonify({"success": True, "series": name, "results": _items(items)})

    @bp.route("/author", methods=["GET"])
    def author():
        """An author's bibliography."""
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name parameter is required"}), 400
        items = get_audiobook_client().get_by_author(
            name, limit=_limit(30), marketplace=_marketplace(), sort=_sort("bestsellers"),
        )
        return jsonify({"success": True, "author": name, "results": _items(items)})

    @bp.route("/narrator", methods=["GET"])
    def narrator():
        """Everything a narrator has performed."""
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name parameter is required"}), 400
        items = get_audiobook_client().get_by_narrator(
            name, limit=_limit(30), marketplace=_marketplace(), sort=_sort("bestsellers"),
        )
        return jsonify({"success": True, "narrator": name, "results": _items(items)})

    @bp.route("/person", methods=["GET"])
    def person():
        """The grouped bibliography behind an author or narrator page.

        One request rather than the page assembling this itself: building it
        means paging the catalog three times and grouping the result, and that
        belongs on the side of the wire that already caches.
        """
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name parameter is required"}), 400

        role = (request.args.get("role") or "author").strip().lower()
        if role not in ("author", "narrator"):
            role = "author"

        profile = get_audiobook_client().get_person_profile(
            name, role=role, marketplace=_marketplace(),
        )
        # Only authors can be followed: a narrator has no "new release" of their
        # own, they appear on someone else's.
        #
        # Best effort on purpose. This is a catalogue page and the follow state
        # is a garnish on it, so an unreadable database costs the button its
        # highlight rather than costing the reader the whole page.
        watching = False
        if role == "author":
            try:
                watching = bool(get_audiobook_db().is_following(name))
            except Exception as exc:                        # noqa: BLE001
                logger.debug("Could not read the follow state for %s: %s", name, exc)
        profile["watching"] = watching

        owned = _owned_asins()
        _mark_owned(profile.get("standalone") or [], owned)
        _mark_owned(profile.get("highlights") or [], owned)
        for entry in profile.get("series") or []:
            _mark_owned(entry.get("books") or [], owned)

        return jsonify({"success": True, "profile": profile})

    # ------------------------------------------------------------------
    # Shelves
    # ------------------------------------------------------------------

    @bp.route("/browse", methods=["GET"])
    def browse():
        """One shelf: the catalog sorted, optionally narrowed to a genre.

        ``category`` is a genre NAME. ``category_id`` is still accepted so an
        older bookmark keeps working, but ids do not survive a storefront
        change, which is why the UI sends names.
        """
        category = (request.args.get("category")
                    or request.args.get("category_id") or "").strip()
        items = get_audiobook_client().browse(
            category=category or None,
            sort=_sort("bestsellers"),
            limit=_limit(),
            marketplace=_marketplace(),
            page=_page(),
        )
        return jsonify({
            "success": True,
            "category": category,
            "sort": _sort("bestsellers"),
            "page": _page(),
            "results": _items(items),
        })

    @bp.route("/bestsellers", methods=["GET"])
    def bestsellers():
        """Top sellers, overall or within one genre."""
        items = get_audiobook_client().get_bestsellers(
            category=(request.args.get("category")
                      or request.args.get("category_id") or "").strip() or None,
            limit=_limit(),
            marketplace=_marketplace(),
        )
        return jsonify({"success": True, "results": _items(items)})

    @bp.route("/new-releases", methods=["GET"])
    def new_releases():
        """Newest first, overall or within one genre."""
        items = get_audiobook_client().get_new_releases(
            category=(request.args.get("category")
                      or request.args.get("category_id") or "").strip() or None,
            limit=_limit(),
            marketplace=_marketplace(),
        )
        return jsonify({"success": True, "results": _items(items)})

    @bp.route("/categories", methods=["GET"])
    def categories():
        """The genre tree, for real navigation rather than hardcoded search terms."""
        return jsonify({
            "success": True,
            "categories": get_audiobook_client().get_categories(marketplace=_marketplace()),
        })

    @bp.route("/home", methods=["GET"])
    def home():
        """The whole browse page in one round trip.

        Six shelves fetched concurrently instead of six sequential requests from
        the browser. The first item of the bestseller shelf is handed back
        separately as the hero so the page has something to paint immediately.

        A shelf that fails comes back empty rather than failing the request —
        five shelves and a hero is a page, a 500 is not.
        """
        client = get_audiobook_client()
        marketplace = _marketplace()
        per_shelf = _limit(20)

        def fetch(shelf: Dict[str, str]) -> Dict[str, Any]:
            try:
                items = client.browse(
                    category=shelf["category"] or None,
                    sort=shelf["sort"],
                    limit=min(50, per_shelf * _HOME_OVERFETCH),
                    marketplace=marketplace,
                )
            except Exception as exc:                       # noqa: BLE001
                logger.warning("home shelf %s failed: %s", shelf["key"], exc)
                items = []
            return {
                "key": shelf["key"],
                "title": shelf["title"],
                "category": shelf["category"],
                "sort": shelf["sort"],
                "items": items,
            }

        with ThreadPoolExecutor(max_workers=_HOME_WORKERS) as pool:
            fetched = list(pool.map(fetch, _HOME_SHELVES))

        # Trimmed here rather than in each fetch: a title can only be de-duplicated
        # against the shelves that were filled before it.
        trimmed = dedupe_across_shelves(
            [shelf["items"] for shelf in fetched], per_shelf,
        )
        shelves = [
            {
                "key": shelf["key"],
                "title": shelf["title"],
                "category": shelf["category"],
                "sort": shelf["sort"],
                "results": _items(picked),
            }
            for shelf, picked in zip(fetched, trimmed, strict=True)
        ]

        hero: Optional[Dict[str, Any]] = None
        for shelf in shelves:
            if shelf["results"]:
                hero = shelf["results"][0]
                break
        return jsonify({"success": True, "hero": hero, "shelves": shelves})

    # ------------------------------------------------------------------
    # Wishlist
    # ------------------------------------------------------------------

    @bp.route("/wishlist", methods=["GET"])
    def wishlist():
        """Everything wanted, with counts and the state of the search worker.

        One request rather than a per-card lookup: a browse page renders well
        over a hundred covers, and asking "is this wishlisted" per card would be
        a hundred round trips to answer what one already answers.
        """
        db = get_audiobook_db()
        payload = {
            "success": True,
            "items": db.get_wishlist(),
            "counts": db.wishlist_counts(),
        }
        try:
            from core.audiobook_wishlist_worker import schedule_status
            payload["worker"] = schedule_status()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not read the wishlist automation status: %s", exc)
            payload["worker"] = None
        return jsonify(payload)

    @bp.route("/wishlist", methods=["POST"])
    def wishlist_add():
        """Want a book.

        Takes only an ASIN and resolves the metadata server-side, so a row can
        never be stored from a payload the browser assembled — the wishlist is
        searched from what is in it, and a half-filled row would search for the
        wrong thing forever.

        ``narrator_mode`` is the one real choice: "exact" holds the download to
        the reading this ASIN is, "any" accepts another narrator's edition. It
        is never a list — a book is always exactly one narrator.
        """
        body = request.get_json(silent=True) or {}
        asin = str(body.get("asin") or "").strip()
        if not asin:
            return jsonify({"success": False, "error": "asin is required"}), 400

        narrator_mode = str(body.get("narrator_mode") or "exact").strip().lower()
        if narrator_mode not in ("exact", "any"):
            narrator_mode = "exact"

        book = get_audiobook_client().get_book(asin, marketplace=_marketplace())
        if book is None:
            return jsonify({"success": False, "error": f"No audiobook found for {asin}"}), 404

        added = get_audiobook_db().add_to_wishlist(
            book.to_dict(), narrator_mode=narrator_mode,
        )
        # Already on the list is a success from the caller's point of view: the
        # book is wanted either way, and a 409 would make the button look broken.
        return jsonify({
            "success": True, "added": added, "wishlisted": True,
            "narrator_mode": narrator_mode,
        })

    @bp.route("/wishlist/<asin>", methods=["PATCH"])
    def wishlist_update(asin: str):
        """Change how strictly a wanted book must match its narrator.

        Lives here rather than on the add route because adding is idempotent —
        re-adding must not rewrite a choice already made, and changing the
        choice must not reset the book's retry backoff.
        """
        body = request.get_json(silent=True) or {}
        narrator_mode = str(body.get("narrator_mode") or "").strip().lower()
        if narrator_mode not in ("exact", "any"):
            return jsonify({
                "success": False, "error": "narrator_mode must be 'exact' or 'any'",
            }), 400

        changed = get_audiobook_db().set_narrator_mode(asin, narrator_mode)
        if not changed:
            return jsonify({"success": False, "error": f"{asin} is not on the wishlist"}), 404
        return jsonify({"success": True, "narrator_mode": narrator_mode})

    @bp.route("/wishlist/<asin>", methods=["DELETE"])
    def wishlist_remove(asin: str):
        """Stop wanting a book."""
        removed = get_audiobook_db().remove_from_wishlist(asin)
        return jsonify({"success": True, "removed": removed, "wishlisted": False})

    @bp.route("/wishlist/search", methods=["POST"])
    def wishlist_search():
        """Run a wishlist pass right now.

        The same code path the background worker runs, so the manual button and
        the timer cannot drift apart. It still honours each book's own backoff —
        pressing it repeatedly does not re-search anything recently tried, which
        is what stops it becoming a way to hammer the indexers by hand.
        """
        from core.audiobook_wishlist_worker import run_pass

        try:
            summary = run_pass()
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Manual audiobook wishlist pass failed: %s", exc, exc_info=True)
            return jsonify({"success": False, "error": str(exc)}), 500
        return jsonify({"success": True, "summary": summary})

    # ------------------------------------------------------------------
    # Watchlist — followed authors
    # ------------------------------------------------------------------

    @bp.route("/watchlist", methods=["GET"])
    def watchlist():
        """Authors being followed, newest release counts included."""
        return jsonify({"success": True, "authors": get_audiobook_db().get_watchlist()})

    @bp.route("/watchlist", methods=["POST"])
    def watchlist_follow():
        """Follow an author so their new releases get wishlisted.

        ``since`` is the cutoff and defaults to today: following an author means
        "tell me about the next one", not "queue the 88 they already wrote".
        Pass an earlier date deliberately to backfill.
        """
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "name is required"}), 400

        followed = get_audiobook_db().follow_author(
            name,
            cover_url=str(body.get("cover_url") or ""),
            since_date=str(body.get("since") or ""),
        )
        return jsonify({"success": True, "followed": followed, "watching": True})

    @bp.route("/watchlist/<path:name>", methods=["DELETE"])
    def watchlist_unfollow(name: str):
        removed = get_audiobook_db().unfollow_author(name)
        return jsonify({"success": True, "removed": removed, "watching": False})

    @bp.route("/watchlist/scan", methods=["POST"])
    def watchlist_scan():
        """Check followed authors right now.

        The same code path the daily automation runs, so the button and the
        schedule cannot drift apart. Each author's own daily spacing still
        applies, so pressing it repeatedly does not re-check anyone.
        """
        from core.audiobook_watchlist import run_scan

        try:
            summary = run_scan()
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Manual audiobook author scan failed: %s", exc, exc_info=True)
            return jsonify({"success": False, "error": str(exc)}), 500
        return jsonify({"success": True, "summary": summary})

    # ------------------------------------------------------------------
    # Releases
    # ------------------------------------------------------------------

    @bp.route("/releases/<asin>", methods=["GET"])
    def releases(asin: str):
        """What the configured indexers actually have for this book.

        Slow by nature — a real search fanning out to every indexer, paced by
        the shared throttle — so callers must treat it as a long request rather
        than a lookup.
        """
        from core.audiobook_release_search import search_all_sources

        book = get_audiobook_client().get_book(asin, marketplace=_marketplace())
        if book is None:
            return jsonify({"success": False, "error": f"No audiobook found for {asin}"}), 404

        narrator_mode = _narrator_mode_for(asin)

        try:
            found = search_all_sources(
                book.to_dict(), limit=_limit(25), narrator_mode=narrator_mode,
            )
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Audiobook release search failed for %s: %s", asin, exc)
            return jsonify({"success": False, "error": str(exc)}), 502
        return jsonify({
            "success": True,
            "asin": asin,
            "narrator_mode": narrator_mode,
            "narrators": book.narrator_names,
            "releases": [release.to_dict() for release in found],
        })

    def _narrator_mode_for(asin: str) -> str:
        """The listener's narrator choice for a book, honoured everywhere.

        A book already on the wishlist carries the choice made when they wished
        for it; asking again from the detail page must not quietly widen it.
        """
        requested = str(request.args.get("narrator_mode") or "").strip().lower()
        if requested in ("exact", "any"):
            return requested
        stored = next(
            (row for row in get_audiobook_db().get_wishlist() if row["asin"] == asin),
            None,
        )
        return (stored or {}).get("narrator_mode") or "exact"

    @bp.route("/releases/<asin>/start", methods=["POST"])
    def releases_start(asin: str):
        """Begin a search and hand back an id to poll.

        Same contract as the video side's /downloads/search/start: the modal
        renders what has arrived and polls for the rest, instead of sitting
        blank through three sequential fan-outs to every indexer.
        """
        from core.audiobook_search_job import start

        book = get_audiobook_client().get_book(asin, marketplace=_marketplace())
        if book is None:
            return jsonify({"success": False, "error": f"No audiobook found for {asin}"}), 404

        narrator_mode = _narrator_mode_for(asin)
        job_id = start(book.to_dict(), narrator_mode=narrator_mode, limit=_limit(25))
        return jsonify({
            "success": True,
            "id": job_id,
            "asin": asin,
            "narrator_mode": narrator_mode,
            "narrators": book.narrator_names,
            # What the client should wait between polls. Matches video's cadence.
            "poll_ms": 1200,
        })

    @bp.route("/releases/poll", methods=["GET"])
    def releases_poll():
        """The ranked pool so far for an in-flight search.

        Always the WHOLE list, never a delta: ranking is global, so a peer with
        the right narrator has to be able to land above a torrent found two
        queries earlier.
        """
        from core.audiobook_search_job import poll

        state = poll(request.args.get("id") or "")
        if state is None:
            # Expired or unknown. Not an error the user can act on — the client
            # just stops polling and keeps whatever it already rendered.
            return jsonify({"success": False, "expired": True}), 404
        return jsonify({"success": True, **state})

    @bp.route("/releases/poll", methods=["DELETE"])
    def releases_cancel():
        """Stop caring about a search the user walked away from."""
        from core.audiobook_search_job import forget

        return jsonify({"success": True, "dropped": forget(request.args.get("id") or "")})

    @bp.route("/grab", methods=["POST"])
    def grab():
        """Send one chosen release to the download client.

        The release is passed back as the client received it rather than being
        re-searched: re-running the search to find "the same" release would race
        against the indexer and could grab something else entirely.
        """
        from core.audiobook_grab import grab_release

        body = request.get_json(silent=True) or {}
        release = body.get("release")
        if not isinstance(release, dict):
            return jsonify({"success": False, "error": "release is required"}), 400

        # Resolved NOW, while the catalogue is answering, and stored with the
        # download. The import needs the series and narrator to shelve the book
        # and the runtime to check it is whole, and by then Audible may be
        # shedding load or the title may be gone.
        asin_for_book = str(body.get("asin") or "").strip()
        book_payload = None
        if asin_for_book:
            found = get_audiobook_client().get_book(asin_for_book, marketplace=_marketplace())
            if found is not None:
                book_payload = found.to_dict()

        # Already on disk. The library table is written on every import and was
        # never read back, so nothing stopped a book being fetched twice.
        if asin_for_book and get_audiobook_db().is_owned(asin_for_book):
            if not body.get("force"):
                return jsonify({
                    "success": False,
                    "owned": True,
                    "error": "That book is already in your library. Send force to grab it anyway.",
                }), 409

        result = grab_release(release)
        if not result.get("ok"):
            return jsonify({"success": False, "error": result.get("error") or "Grab failed"}), 502

        asin = str(body.get("asin") or "").strip()
        ref = str(result.get("ref") or "")
        # Torrents and NZBs are one job, so the handle IS the ref. A Soulseek
        # folder is many transfers and carries its own.
        client_ref = str(result.get("client_ref") or ref)
        db = get_audiobook_db()

        # Two records, deliberately. The audiobook database keeps the history
        # and the completeness bookkeeping; the shared runtime state puts the
        # book on the existing Downloads page with the existing cards, flagged
        # so is_music_batch() keeps the music engine off it.
        if ref:
            from core.audiobook_download_state import register_download

            book = book_payload or {}
            series_list = book.get("series") or []
            register_download(
                task_id=ref,
                title=str(book.get("title") or body.get("title")
                          or release.get("title") or ""),
                author=(book.get("author_names") or [str(body.get("author") or "")])[0],
                series=str((series_list[0] or {}).get("title") or "") if series_list else "",
                artwork_url=str(book.get("cover_url") or body.get("cover_url") or ""),
                protocol=str(release.get("protocol") or ""),
                size_bytes=int(release.get("size_bytes") or 0),
            )
            db.record_download(
                download_id=ref,
                asin=asin,
                title=str(body.get("title") or release.get("title") or ""),
                source=str(release.get("protocol") or ""),
                client_id=client_ref,
                release_title=str(release.get("title") or ""),
                indexer=str(release.get("indexer") or ""),
                author=str(body.get("author") or ""),
                bytes_total=int(release.get("size_bytes") or 0),
                book=book_payload,
            )

        if asin:
            from core.audiobook_database import STATUS_GRABBED
            # Only moves a row that already exists — grabbing something that was
            # never wishlisted must not silently add it.
            db.mark_wishlist_status(asin, STATUS_GRABBED)

        # Something is now downloading, so start watching for it to finish even
        # if the monitor was asleep at boot.
        try:
            from core.audiobook_download_monitor import ensure_started
            ensure_started(force=True)
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not wake the download monitor: %s", exc)
        return jsonify({"success": True, "ref": ref})

    @bp.route("/downloads", methods=["GET"])
    def downloads():
        """Everything grabbed, in flight or finished.

        Its own list, not the music Downloads page: an audiobook is one release
        that becomes a folder of chapters, which the music page's per-track view
        has nowhere sensible to put.
        """
        db = get_audiobook_db()
        payload = {
            "success": True,
            "downloads": db.get_downloads(
                active_only=request.args.get("active") in ("1", "true", "yes"),
            ),
        }
        try:
            from core.audiobook_download_monitor import get_monitor
            payload["monitor"] = get_monitor().status()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not read the download monitor status: %s", exc)
            payload["monitor"] = None
        return jsonify(payload)

    # ------------------------------------------------------------------
    # Sample proxy
    # ------------------------------------------------------------------

    @bp.route("/sample-proxy", methods=["GET"])
    def sample_proxy():
        """Stream an audio preview through the server, with range support.

        Samples are hosted on Audible and Apple CDNs that do not send CORS
        headers, so an <audio> element pointed straight at one can fail in the
        browser. This forwards the Range header so seeking within a preview
        still works.

        The target host is checked against the preview allowlist. Anything else
        is a 400 — this endpoint must never become a general-purpose fetcher
        reachable from the web UI.
        """
        target = (request.args.get("url") or "").strip()
        if not target:
            return jsonify({"success": False, "error": "url parameter is required"}), 400
        if not is_allowed_sample_url(target):
            logger.warning("sample-proxy refused a disallowed URL: %s", target[:120])
            return jsonify({"success": False, "error": "URL host is not an allowed preview host"}), 400

        headers = {"User-Agent": "SoulSync/1.0"}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]

        try:
            upstream = requests.get(
                target, headers=headers, stream=True,
                timeout=_PROXY_TIMEOUT, allow_redirects=False,
            )
        except Exception as exc:                            # noqa: BLE001
            logger.warning("sample-proxy fetch failed for %s: %s", target[:120], exc)
            return jsonify({"success": False, "error": "Failed to fetch sample"}), 502

        # Redirects are not followed: the allowlist checked the URL we were
        # given, and a 302 could point anywhere. The CDNs serve samples directly.
        if upstream.status_code in (301, 302, 303, 307, 308):
            upstream.close()
            return jsonify({"success": False, "error": "Sample host redirected"}), 502
        if upstream.status_code >= 400:
            status = upstream.status_code
            upstream.close()
            return jsonify({"success": False, "error": f"Sample host returned {status}"}), 502

        def stream():
            try:
                for chunk in upstream.iter_content(chunk_size=_PROXY_CHUNK):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        passthrough = {}
        for header in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if header in upstream.headers:
                passthrough[header] = upstream.headers[header]
        passthrough.setdefault("Content-Type", "audio/mpeg")
        passthrough.setdefault("Accept-Ranges", "bytes")
        passthrough["Cache-Control"] = "public, max-age=86400"

        return Response(stream(), status=upstream.status_code, headers=passthrough)

    return bp
