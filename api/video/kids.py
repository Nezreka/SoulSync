"""kids profiles on the video side: the route-level helpers.

the rules live in core/content_filter.py. this is the glue that looks up a
title's rating (library row first, the tmdb detail for anything we don't
own) and answers "may this profile see / play it".

every helper takes the cap up front and does nothing when it is None, so an
admin or unrestricted profile never pays for an extra query.
"""

from __future__ import annotations

from flask import jsonify

from core.content_filter import current_restrictions, strictest, title_allowed
from utils.logging_config import get_logger

logger = get_logger("video_api.kids")


class VideoCap:
    """what a profile may see on the video side, per kind (#2166): a rating
    cap age for movies and one for shows (None = no cap), and the kinds it
    can't see at all (movies.access / tv.access off). every helper here
    takes one of these or the old plain int cap (one age for both kinds).

    ``hidden``: the libraries an admin hid from this profile in the player
    (core/player/libraries.py). a title in one of them is not this
    profile's to see or play, whatever its rating."""

    __slots__ = ("movie", "show", "blocked", "hidden")

    def __init__(self, movie=None, show=None, blocked=(), hidden=()):
        self.movie, self.show, self.blocked = movie, show, frozenset(blocked)
        self.hidden = frozenset(int(x) for x in hidden)

    def __repr__(self):  # pragma: no cover - debugging
        return "VideoCap(movie=%r, show=%r, blocked=%r, hidden=%r)" % (
            self.movie, self.show, sorted(self.blocked), sorted(self.hidden))

    def __eq__(self, other):
        return isinstance(other, VideoCap) and (self.movie, self.show, self.blocked, self.hidden) == (
            other.movie, other.show, other.blocked, other.hidden)

    __hash__ = None

    def sees(self, kind) -> bool:
        k = _kind(kind)
        return k is not None and k not in self.blocked

    def allows(self, kind, rating) -> bool:
        k = _kind(kind)
        if k is None or k in self.blocked:
            return False
        return title_allowed(rating, self.movie if k == "movie" else self.show)


def kind_allows(cap, kind, rating) -> bool:
    """may a title of this kind and rating be shown under ``cap`` (a VideoCap,
    an int age, or None)."""
    if cap is None:
        return True
    if isinstance(cap, VideoCap):
        return cap.allows(kind, rating)
    return title_allowed(rating, cap)


def hidden_libraries(cap) -> frozenset:
    """the library ids hidden under ``cap`` (empty for none)"""
    return cap.hidden if isinstance(cap, VideoCap) else frozenset()


def _in_hidden_library(db, cap, k, ids) -> set:
    """the library title ids of kind ``k`` that sit in a library hidden
    under ``cap``. empty when nothing is hidden (no query). a lookup that
    fails hides them all: can't vouch, so no."""
    hidden = hidden_libraries(cap)
    ids = [i for i in ids if i is not None]
    if not hidden or not ids:
        return set()
    try:
        from core.player.libraries import library_ids_for
        from database.player_database import get_player_database
        where = library_ids_for(db, get_player_database(), k, ids)
    except Exception:  # noqa: BLE001
        logger.exception("hidden libraries: lookup failed for %s", k)
        return {int(i) for i in ids}
    return {int(i) for i in ids if where.get(int(i)) in hidden}


def kind_visible(cap, kind) -> bool:
    """is this kind of title visible at all under ``cap`` (ratings aside)?"""
    return not isinstance(cap, VideoCap) or cap.sees(kind)


def cap_from_perms(perms, *, is_admin=False):
    """a VideoCap for an effective permissions map, None when nothing is
    restricted. a map that couldn't be built sees nothing."""
    from core.content_filter import cap_age
    from core.permissions import FOLLOW
    if is_admin:
        return None
    if not isinstance(perms, dict):
        return VideoCap(0, 0, ("movie", "show"))

    def _age(v):
        return None if v in (None, "any") else cap_age(v)

    movie_rating = perms.get("movies.max_rating", "any")
    tv_rating = perms.get("tv.max_rating", FOLLOW)
    if tv_rating == FOLLOW:
        tv_rating = movie_rating
    blocked = tuple(k for k, row in (("movie", "movies"), ("show", "tv"))
                    if not perms.get("%s.access" % row))
    movie, show = _age(movie_rating), _age(tv_rating)
    if movie is None and show is None and not blocked:
        return None
    return VideoCap(movie, show, blocked)


def video_cap():
    """the current profile's video cap (a VideoCap), None when it has none.

    read from the effective permissions (g.perms) when web_server put them
    there: per-kind access and a rating per kind (tv follows the movie one
    unless it has its own). a request without them (tests, edge callers)
    reads the old profile column through content_filter, one age for both."""
    from flask import g, has_request_context
    if not has_request_context():
        return None
    if hasattr(g, "_video_cap"):
        return g._video_cap
    from .access import ctx, stamped
    if stamped():
        c = ctx()
        cap = cap_from_perms(c.get("perms"), is_admin=bool(c.get("is_admin")))
    else:
        age = current_restrictions().get("cap")
        c = ctx()
        perms = c.get("perms") if isinstance(c.get("perms"), dict) else {}
        blocked = tuple(k for k, row in (("movie", "movies"), ("show", "tv"))
                        if not c.get("is_admin") and not perms.get("%s.access" % row))
        cap = None if (age is None and not blocked) else VideoCap(age, age, blocked)
    cap = _with_hidden_libraries(cap, c)
    g._video_cap = cap
    return cap


def _with_hidden_libraries(cap, c):
    """``cap`` with the libraries hidden from this profile in the player.
    an admin sees every library. a read that fails is said out loud and
    hides nothing: a broken player.db must not lock a household out of its
    video library (the rating cap, which is the safety gate, is untouched)."""
    if c.get("is_admin") or c.get("profile_id") is None:
        return cap
    try:
        from core.player.libraries import hidden_for
        from database.player_database import get_player_database
        hidden = hidden_for(get_player_database(), c.get("profile_id"))
    except Exception:  # noqa: BLE001
        logger.exception("hidden libraries: couldn't read them for profile %s", c.get("profile_id"))
        return cap
    if not hidden:
        return cap
    if isinstance(cap, VideoCap):
        return VideoCap(cap.movie, cap.show, cap.blocked, hidden)
    # an old plain int cap is one age for both kinds
    return VideoCap(cap, cap, (), hidden)


def feed_cap(profile):
    """(cap, sees the video side) for a profile row off-request (the tokened
    calendar feed). its effective permissions, under the policy; a map that
    can't be built sees nothing."""
    from core.permissions import is_admin_row
    if is_admin_row(profile):
        return None, True
    try:
        from core.video.quality_access import effective_for_row
        perms = effective_for_row(profile)
    except Exception:  # noqa: BLE001 - can't vouch for the profile: nothing
        logger.warning("calendar feed: no permissions for profile %s", (profile or {}).get("id"))
        return VideoCap(0, 0, ("movie", "show")), False
    cap = cap_from_perms(perms)
    sees = bool(perms.get("movies.access")) or bool(perms.get("tv.access"))
    return cap, sees


def restricted_response(kind=None):
    """403 for a title this profile can't have: the calm "not part of your
    profile" when its whole kind is off, else the kids "restricted"."""
    cap = video_cap()
    if kind is not None and isinstance(cap, VideoCap) and not cap.sees(kind):
        from .access import kind_refusal
        return kind_refusal(kind)
    return jsonify({"success": False, "error": "restricted", "restricted": True}), 403


def _kind(k):
    k = str(k or "").lower()
    if k in ("movie", "movies", "m"):
        return "movie"
    if k in ("show", "shows", "tv", "t", "episode"):
        return "show"
    return None


def library_title_allowed(db, kind, library_id, cap) -> bool:
    if cap is None:
        return True
    k = _kind(kind)
    if not k or not kind_visible(cap, k):
        return False
    if _in_hidden_library(db, cap, k, [library_id]):
        return False
    try:
        rating = db.content_ratings_by_id(k, [library_id]).get(int(library_id))
    except Exception:  # noqa: BLE001 - can't vouch, so no
        logger.exception("kids: rating lookup failed for %s %s", kind, library_id)
        return False
    return kind_allows(cap, k, rating)


def tmdb_title_rating(db, kind, tmdb_id):
    """the rating for a tmdb title: the library's (strictest across servers),
    else the certification on the tmdb detail. None when neither knows."""
    k = _kind(kind)
    if not k:
        return None
    try:
        rows = db.content_ratings_by_tmdb(k, [tmdb_id]).get(int(tmdb_id)) or []
    except Exception:  # noqa: BLE001
        logger.exception("kids: library rating lookup failed for %s %s", kind, tmdb_id)
        rows = []
    known = strictest(rows)
    if known is not None:
        return known
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        d = get_video_enrichment_engine().tmdb_detail(k, int(tmdb_id)) or {}
    except Exception:  # noqa: BLE001 - no detail, no rating, blocked under a cap
        logger.debug("kids: tmdb detail lookup failed for %s %s", kind, tmdb_id, exc_info=True)
        return None
    return d.get("content_rating")


def tmdb_title_allowed(db, kind, tmdb_id, cap) -> bool:
    if cap is None:
        return True
    if not kind_visible(cap, _kind(kind)) or _kind(kind) is None:
        return False
    if isinstance(cap, VideoCap) and (cap.movie if _kind(kind) == "movie" else cap.show) is None:
        return True        # this kind has no rating cap: no lookup needed
    return kind_allows(cap, kind, tmdb_title_rating(db, kind, tmdb_id))


def _needs_rating(cap, k) -> bool:
    """does a title of kind k need its rating looked up under this cap?"""
    if isinstance(cap, VideoCap):
        return k not in cap.blocked and (cap.movie if k == "movie" else cap.show) is not None
    return cap is not None


def rating_capped(cap, kind) -> bool:
    """is this kind held to a rating cap (its titles need a rating lookup)?"""
    k = _kind(kind)
    return k is not None and _needs_rating(cap, k)


def rating_allowed(cap, kind, rating) -> bool:
    """a title whose rating the caller already has (a detail payload)."""
    return kind_allows(cap, kind, rating)


def filter_tmdb_items(db, items, cap, kind_key="kind", id_key="tmdb_id"):
    """drop tmdb-keyed cards over the cap. library ratings only (one query per
    kind), no tmdb round trip per card: a card we can't rate is dropped.
    non-title cards (people, studios) pass."""
    if cap is None or not items:
        return items
    want: dict = {"movie": set(), "show": set()}
    for it in items:
        k = _kind(it.get(kind_key)) if isinstance(it, dict) else None
        if k and it.get(id_key) is not None and _needs_rating(cap, k):
            want[k].add(it.get(id_key))
    ratings = {}
    for k, ids in want.items():
        if ids:
            try:
                ratings[k] = db.content_ratings_by_tmdb(k, ids)
            except Exception:  # noqa: BLE001
                logger.exception("kids: rating lookup failed")
                ratings[k] = {}
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        raw_kind = str(it.get(kind_key) or "").lower()
        k = _kind(raw_kind)
        if k is None and raw_kind in ("person", "studio", "collection", "company", "channel"):
            out.append(it)
            continue
        if k is None:
            # not a movie or a show (a youtube video): no rating to hold it
            # to, so only a rating cap leaves it out (kids fail closed)
            if isinstance(cap, VideoCap) and cap.movie is None and cap.show is None:
                out.append(it)
            continue
        if not kind_visible(cap, k):
            continue
        if not _needs_rating(cap, k):
            out.append(it)
            continue
        try:
            rows = ratings.get(k, {}).get(int(it.get(id_key))) or []
        except (TypeError, ValueError):
            rows = []
        if kind_allows(cap, k, strictest(rows)):
            out.append(it)
    return out


def filter_library_items(db, items, cap, kind, id_key="id"):
    """drop library-keyed rows (one kind) over the cap, one query."""
    if cap is None or not items:
        return items
    k = _kind(kind)
    if k is None or not kind_visible(cap, k):
        return []
    rated = _needs_rating(cap, k)
    if not rated and not hidden_libraries(cap):
        return items
    ids = [it.get(id_key) for it in items if isinstance(it, dict) and it.get(id_key) is not None]
    gone = _in_hidden_library(db, cap, k, ids)
    ratings = {}
    if rated:
        try:
            ratings = db.content_ratings_by_id(k, ids) if k else {}
        except Exception:  # noqa: BLE001
            logger.exception("kids: rating lookup failed")
    out = []
    for it in items:
        try:
            rid = int(it.get(id_key))
        except (TypeError, ValueError):
            continue
        if rid in gone:
            continue
        if not rated or kind_allows(cap, k, ratings.get(rid)):
            out.append(it)
    return out


def filter_mixed_library_items(db, items, cap, kind_key="kind", id_key="id", show_id_key=None):
    """drop rows that mix movies and shows by library id (dashboard rails).
    ``show_id_key`` reads a show row's show id from another key (continue
    watching puts the episode id in ``id``)."""
    if cap is None or not items:
        return items
    ids: dict = {"movie": [], "show": []}

    def _ref(it):
        k = _kind(it.get(kind_key))
        key = show_id_key if (k == "show" and show_id_key) else id_key
        return k, it.get(key)

    every: dict = {"movie": [], "show": []}
    for it in items:
        k, rid = _ref(it)
        if k and rid is not None:
            every[k].append(rid)
            if _needs_rating(cap, k):
                ids[k].append(rid)
    ratings = {}
    for k, v in ids.items():
        try:
            ratings[k] = db.content_ratings_by_id(k, v) if v else {}
        except Exception:  # noqa: BLE001
            logger.exception("kids: rating lookup failed")
            ratings[k] = {}
    gone = {k: _in_hidden_library(db, cap, k, v) for k, v in every.items()}
    out = []
    for it in items:
        k, rid = _ref(it)
        if not k or rid is None or not kind_visible(cap, k):
            continue
        try:
            if int(rid) in gone[k]:
                continue
        except (TypeError, ValueError):
            continue
        if not _needs_rating(cap, k) or kind_allows(cap, k, ratings.get(k, {}).get(int(rid))):
            out.append(it)
    return out
