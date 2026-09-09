"""Follow an author, and wishlist their new releases as they appear.

The audiobook equivalent of following an artist on the music side, and the same
shape as the podcast watchlist scan: check the things you follow on a schedule,
and queue whatever is new.

What "new" means is the whole design. Following an author must NOT dump their
back catalogue into the wishlist — Brandon Sanderson has 88 titles, and someone
who follows him wants the next one, not all of them. So a follow records the day
it was made, and only books published after that are picked up. Someone who
genuinely wants the backlog can set the cutoff back.

Everything after "this is new" is the existing pipeline: the book is wishlisted
at `exact` on its own narrator, and from there the ordinary wishlist pass
searches for it, ranks releases, checks the download is whole and files it. This
module adds a way in, not a second way through.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_watchlist")

# How many of an author's titles to look at per scan. Their newest are first, so
# a shorter list still catches everything published since yesterday.
DEFAULT_LOOKBACK = 20

# Authors checked per pass. Daily per author, so this only bounds a single run.
DEFAULT_BATCH = 10


def is_newer_than(release_date: Optional[str], since: Optional[str]) -> bool:
    """Whether a book came out after a follow was made.

    Both are ISO dates, which compare correctly as strings. A book with no
    release date is treated as NOT new: a missing date is usually an
    unreleased placeholder or bad metadata, and wishlisting on a guess would
    queue things nobody asked for.
    """
    book_date = str(release_date or "").strip()[:10]
    cutoff = str(since or "").strip()[:10]
    if not book_date:
        return False
    if not cutoff:
        return True
    return book_date > cutoff


def new_books_for(
    books: List[Any],
    since: Optional[str],
    is_known: Any,
) -> List[Dict[str, Any]]:
    """The books worth wishlisting out of an author's catalogue.

    Drops anything published before the follow, and anything already wanted or
    already owned — ``is_known(asin)`` answers both, so a re-scan does not queue
    the same book every day.
    """
    found: List[Dict[str, Any]] = []
    for book in books:
        payload = book.to_dict() if hasattr(book, "to_dict") else dict(book)
        asin = str(payload.get("asin") or "")
        if not asin or not is_newer_than(payload.get("release_date"), since):
            continue
        try:
            if is_known(asin):
                continue
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not check whether %s is known: %s", asin, exc)
            continue
        found.append(payload)
    return found


def scan_author(row: Dict[str, Any], db: Any = None, client: Any = None) -> Dict[str, Any]:
    """Check one followed author and wishlist anything new.

    Returns ``{name, found, wishlisted, error}``. Never raises: one author whose
    lookup fails must not stop the rest of the pass.
    """
    from core.audiobook_database import get_audiobook_db

    database = db if db is not None else get_audiobook_db()
    name = str(row.get("name") or "").strip()
    outcome: Dict[str, Any] = {"name": name, "found": 0, "wishlisted": 0, "error": ""}
    if not name:
        return outcome

    try:
        if client is None:
            from core.audiobook_client import get_audiobook_client

            client = get_audiobook_client()
        # Newest first, so a short lookback still sees everything published
        # since the last scan.
        books = client.get_by_author(name, limit=DEFAULT_LOOKBACK, sort="newest")
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not check %s for new releases: %s", name, exc)
        outcome["error"] = str(exc)
        database.mark_author_scanned(name, error=str(exc))
        return outcome

    def is_known(asin: str) -> bool:
        return bool(database.is_wishlisted(asin) or database.is_owned(asin))

    fresh = new_books_for(books, row.get("since_date"), is_known)
    outcome["found"] = len(fresh)

    # Following without auto-wishlist is "tell me, do not fetch". The releases
    # are still counted and reported, so the card can say what turned up.
    if not row.get("auto_wishlist", 1):
        outcome["wishlisted"] = 0
        database.mark_author_scanned(name, found=outcome["found"])
        if outcome["found"]:
            logger.info("Followed author %s: %d new, not wishlisted (auto-wishlist off)",
                        name, outcome["found"])
        return outcome

    # The narrator rule was answered once, when the author was followed: an
    # auto-wishlisted book is never seen by anyone before it is queued, so
    # there is no modal to ask. Defaults to `exact`, the standard every other
    # route into the wishlist uses.
    narrator_mode = str(row.get("narrator_mode") or "exact").strip().lower()
    if narrator_mode not in ("exact", "any"):
        narrator_mode = "exact"

    for payload in fresh:
        if database.add_to_wishlist(payload, narrator_mode=narrator_mode):
            outcome["wishlisted"] += 1
            logger.info("Followed author %s: wishlisted %s (%s narrator)",
                        name, payload.get("title"), narrator_mode)

    database.mark_author_scanned(name, found=outcome["wishlisted"])
    return outcome


def run_scan(db: Any = None, limit: Optional[int] = None) -> Dict[str, int]:
    """One pass over the authors due a look.

    Safe to call by hand — the "Check now" button runs exactly this, so the
    manual and scheduled paths cannot drift apart.
    """
    from core.audiobook_database import get_audiobook_db

    database = db if db is not None else get_audiobook_db()
    summary = {"authors": 0, "found": 0, "wishlisted": 0, "errors": 0}

    try:
        # Every profile that follows anyone. Without this a second person's
        # followed authors were never checked, so their new releases never
        # reached their wishlist.
        try:
            profiles = database.profiles_with_rows()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not list audiobook profiles: %s", exc)
            profiles = [1]

        due = []
        for profile_id in profiles:
            due.extend(database.get_watchlist_due(
                profile_id=profile_id, limit=limit or DEFAULT_BATCH))
    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not read the audiobook watchlist: %s", exc)
        return summary

    for row in due:
        result = scan_author(row, db=database)
        summary["authors"] += 1
        summary["found"] += result["found"]
        summary["wishlisted"] += result["wishlisted"]
        summary["errors"] += 1 if result["error"] else 0

    if summary["authors"]:
        logger.info("Audiobook watchlist scan: %s", summary)
    return summary
