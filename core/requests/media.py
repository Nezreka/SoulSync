"""audiobook and podcast episode requests (#2188).

a profile on Ask first for books (books.get) or episodes (podcasts.get) asks
instead of downloading. the ask is one row in media_requests (the music
database), listed on the same Requests page as music and music videos:

- an audiobook ask holds the book. approving puts it on the requester's own
  audiobook wishlist, and the wishlist pass grabs it even though the owner
  asks first, because an admin said yes to that one book (may_get_book).
- an episode ask holds the episode. approving starts its download, the same
  call the episode's download button makes.

declining ends it; the requester hears either way. the rules here take the
database as an argument so they test without the app.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from utils.logging_config import get_logger

logger = get_logger("requests.media")

AUDIOBOOK = "audiobook"
PODCAST = "podcast"
KIND_PERMISSION = {AUDIOBOOK: "books.get", PODCAST: "podcasts.get"}
KIND_WORDS = {AUDIOBOOK: "Audiobook", PODCAST: "Podcast episode"}

# what an episode ask keeps: exactly what /api/podcasts/download reads
EPISODE_FIELDS = ("enclosure_url", "title", "guid", "show_title", "author", "pub_date", "artwork_url",
                  "duration_seconds", "feed_url")


def describe(req: Mapping[str, Any]) -> str:
    """"Wind and Truth by Brandon Sanderson" / "Episode 12 from Some Show"."""
    title = str(req.get("title") or "your request")
    sub = str(req.get("subtitle") or "")
    if not sub:
        return title
    return "%s %s %s" % (title, "from" if req.get("kind") == PODCAST else "by", sub)


def episode_payload(data: Mapping[str, Any]) -> Dict[str, Any]:
    """the episode fields an ask keeps, nothing else from the body."""
    out: Dict[str, Any] = {}
    for key in EPISODE_FIELDS:
        value = data.get(key)
        if value is None:
            continue
        if key == "duration_seconds":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value).strip()[:2000]
    return out


def episode_key(data: Mapping[str, Any]) -> str:
    """one episode, whichever id the feed gave it."""
    return str(data.get("guid") or data.get("enclosure_url") or "").strip()[:2000]


def approved_book(db, profile_id, asin) -> bool:
    """has an admin approved this profile's ask for this book? False when it
    can't be read (a db without the table, a read error): never grab on a
    guess."""
    if not asin or not hasattr(db, "media_request_approved"):
        return False
    try:
        return bool(db.media_request_approved(int(profile_id), AUDIOBOOK, str(asin)))
    except Exception:  # noqa: BLE001 - see docstring
        logger.debug("approved book read failed for profile %s", profile_id, exc_info=True)
        return False


def may_get_book(profile_id, asin, db=None) -> bool:
    """may the wishlist pass grab ``asin`` for its owner now? yes when the
    owner gets books right away; for an owner who asks first, only a book an
    admin approved. an owner who can't request books gets nothing, approved
    or not: turning it off after the ask counts."""
    from core.permissions import owner_may_get, owner_mode
    if owner_may_get(profile_id, "books.get", db=db):
        return True
    if db is None:
        try:
            from database.music_database import get_database
            db = get_database()
        except Exception:  # noqa: BLE001 - no db, no grab
            return False
    if owner_mode(profile_id, "books.get", db=db) != "ask":
        return False
    return approved_book(db, profile_id, asin)


def _music_db(db=None):
    if db is not None:
        return db
    from database.music_database import get_database
    return get_database()


def requested_books(profile_id, db=None) -> list:
    """the asins this profile asked for and is still waiting on. the audiobook
    side reads its asks through here: it never opens the music database
    itself (tests/test_audiobooks_isolation.py)."""
    rows = _music_db(db).list_media_requests(profile_id=int(profile_id), status="pending", kind=AUDIOBOOK)
    return [str(r.get("item_key") or "") for r in rows]


def file_book_request(profile_id, book: Mapping[str, Any], narrator_mode: str, db=None):
    """file one book ask. (request id, None) or (None, "already") when the
    same book is already waiting, (None, "failed") when it couldn't be saved."""
    db = _music_db(db)
    asin = str(book.get("asin") or "")
    me = db.get_profile(int(profile_id)) or {}
    authors = book.get("authors") or []
    first = authors[0] if authors else ""
    author = first.get("name") if isinstance(first, dict) else str(first or "")
    rid = db.add_media_request(
        profile_id=int(profile_id), requester_name=me.get("name") or "", kind=AUDIOBOOK, item_key=asin,
        title=str(book.get("title") or asin), subtitle=author or None,
        image_url=str(book.get("cover_url") or book.get("cover_url_large") or "") or None,
        payload={"book": dict(book), "narrator_mode": narrator_mode})
    if rid:
        return rid, None
    return None, ("already" if asin in requested_books(profile_id, db) else "failed")


def book_in_library(asin) -> bool:
    try:
        from core.audiobook_database import get_audiobook_db
        return bool(get_audiobook_db().is_owned(str(asin)))
    except Exception:  # noqa: BLE001 - unknown is not there yet
        return False


def episode_on_disk(db, payload: Mapping[str, Any]) -> bool:
    feed = str(payload.get("feed_url") or "")
    if not feed or not hasattr(db, "get_downloaded_podcast_episodes"):
        return False
    keys = {str(payload.get("enclosure_url") or ""), str(payload.get("guid") or "")} - {""}
    try:
        rows = db.get_downloaded_podcast_episodes(feed_url=feed, unpruned_only=True)
    except Exception:  # noqa: BLE001
        return False
    return any(r.get("file_path") and ({str(r.get("enclosure_url") or ""), str(r.get("guid") or "")} & keys)
               for r in rows or [])


def start_approved(req: Mapping[str, Any]) -> Optional[str]:
    """do what an approval means. None when it's on its way (or already
    there), else why it couldn't start."""
    payload = req.get("payload") or {}
    owner = int(req["profile_id"])
    if req.get("kind") == AUDIOBOOK:
        book = payload.get("book") or {}
        if not book.get("asin"):
            return "This request has no book in it"
        try:
            from core.audiobook_database import get_audiobook_db
            get_audiobook_db().add_to_wishlist(book, narrator_mode=payload.get("narrator_mode") or "exact",
                                               profile_id=owner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("approved book %s couldn't reach the wishlist: %s", book.get("asin"), exc)
            return "Couldn't add the book to their wishlist"
        return None
    if req.get("kind") == PODCAST:
        if not payload.get("enclosure_url"):
            return "This request has no episode in it"
        from api.podcasts import queue_podcast_download
        res = queue_podcast_download(dict(payload))
        if not res.get("success"):
            return res.get("error") or "Couldn't start the episode"
        return None
    return "Unknown request"


__all__ = ["AUDIOBOOK", "PODCAST", "KIND_PERMISSION", "KIND_WORDS", "describe", "episode_payload",
           "requested_books", "file_book_request",
           "episode_key", "approved_book", "may_get_book", "book_in_library", "episode_on_disk",
           "start_approved"]
