"""seerr in front, soulsync behind it in place of radarr and sonarr (#2324).

seerr stays where people ask for things. its webhook posts each request to
soulsync, and soulsync handles it as a request from the matching profile, with
that profile's permissions, quality and request limit, through its own
wishlist and grabs. no radarr or sonarr needed.

with a seerr url and api key in settings:
- the full request is read from seerr: who asked (matched to a profile by
  their plex account, jellyfin id or name, profiles imported from plex
  included), the seasons asked for, and 4k.
- what soulsync decides goes back: a request soulsync takes right away is
  approved in seerr, one it refuses is declined, one waiting on soulsync's
  Requests page is approved or declined there when an admin decides.
- when the files land, seerr's media is marked available, or partly
  available while asked-for seasons are still missing.

without them, the webhook still files the request from what it carries, the
way posting to /request always has.

every seerr request id is remembered (seerr_request_links), so a webhook sent
twice never asks twice, and the events seerr sends because soulsync changed
something (the approval, the decline, available) are known and ignored.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from core.seerr_compat.remote import SeerrRemote, SeerrRemoteError
from core.seerr_compat.service import SeerrError, norm_jellyfin_id
from utils.logging_config import get_logger

logger = get_logger("seerr_compat.bridge")

# seerr's notification types (server/lib/notifications/index.ts)
NEW_REQUEST = {"MEDIA_PENDING", "MEDIA_AUTO_APPROVED", "MEDIA_APPROVED", "MEDIA_AUTO_REQUESTED"}
DECLINED = "MEDIA_DECLINED"
TEST = "TEST_NOTIFICATION"

# seerr's MediaRequestStatus
SEERR_PENDING, SEERR_APPROVED, SEERR_DECLINED = 1, 2, 3

# outcome of a link
PROCESSING = "processing"   # being filed right now
ADDED = "added"             # on the wishlist (or approved in soulsync)
WAITING = "waiting"         # a soulsync request waiting for an admin
EXISTS = "exists"           # already here or already on its way
REFUSED = "refused"         # soulsync's rules said no, or an admin declined
GONE = "gone"               # withdrawn in soulsync, or declined in seerr

_MEDIA_RANK = {None: 0, "partial": 1, "available": 2}

# the table is in database/video_schema.sql (v78)
_FIELDS = ("seerr_media_id", "profile_id", "soulsync_request_id", "seasons", "all_seasons", "is4k",
           "outcome", "seerr_status", "pushed_decision", "pushed_media", "requester")


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class SeerrLinks:
    """seerr request id -> what soulsync made of it, in the video db."""

    def __init__(self, connect: Callable):
        self._connect = connect

    def _run(self, sql, args=(), *, fetch=False):
        conn = self._connect()
        try:
            cur = conn.execute(sql, args)
            if fetch:
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def claim(self, srid: int, kind: str, tmdb_id: int, notification_type: Optional[str]) -> bool:
        """True for the first call with this seerr request id, False after."""
        return self._run(
            "INSERT OR IGNORE INTO seerr_request_links (seerr_request_id, kind, tmdb_id, "
            "notification_type, outcome) VALUES (?, ?, ?, ?, ?)",
            (int(srid), kind, int(tmdb_id), notification_type, PROCESSING)) == 1

    def get(self, srid: int) -> Optional[dict]:
        rows = self._run("SELECT * FROM seerr_request_links WHERE seerr_request_id=?",
                         (int(srid),), fetch=True)
        return rows[0] if rows else None

    def update(self, srid: int, **fields) -> None:
        bad = set(fields) - set(_FIELDS)
        if bad:
            raise ValueError(sorted(bad))
        if not fields:
            return
        sets = ", ".join("%s=?" % k for k in fields)
        self._run("UPDATE seerr_request_links SET %s, updated_at=CURRENT_TIMESTAMP "
                  "WHERE seerr_request_id=?" % sets, (*fields.values(), int(srid)))

    def forget(self, srid: int) -> None:
        self._run("DELETE FROM seerr_request_links WHERE seerr_request_id=?", (int(srid),))

    def open_links(self, limit: int = 500) -> List[dict]:
        """links that may still have something to tell seerr."""
        return self._run(
            "SELECT * FROM seerr_request_links WHERE outcome IN (?, ?, ?) "
            "AND (pushed_media IS NULL OR pushed_media != 'available') "
            "ORDER BY seerr_request_id LIMIT ?", (ADDED, WAITING, EXISTS, int(limit)), fetch=True)


# ---- reading what seerr sent ------------------------------------------------------

def _dig(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return None


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _seasons_from(raw) -> Optional[List[int]]:
    """[1, 2] from a list of numbers, seerr's season objects, or the "1, 2"
    its webhook puts in extra; None for all of them or nothing said."""
    if raw is None or raw == "" or (isinstance(raw, str) and raw.strip().lower() == "all"):
        return None
    items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
    out = set()
    for it in items:
        n = _int(it.get("seasonNumber") if isinstance(it, dict) else it)
        if n and n > 0:
            out.add(n)
    return sorted(out) or None


def parse_webhook(body: Dict[str, Any]) -> Dict[str, Any]:
    """what a seerr webhook (its default template, or one shaped like a
    POST /request body) says. everything optional, strings or numbers."""
    media = body.get("media") if isinstance(body.get("media"), dict) else {}
    req = body.get("request") if isinstance(body.get("request"), dict) else {}
    ntype = _dig(body, "notification_type", "notificationType")
    srid = _int(_dig(body, "seerrRequestId", "requestId", "request_id") or _dig(req, "request_id", "id"))
    mtype = str(_dig(body, "mediaType", "media_type") or _dig(media, "media_type", "mediaType") or "").lower()
    tmdb = _int(_dig(body, "mediaId", "tmdbId") or _dig(media, "tmdbId", "tmdb_id"))
    seasons = body.get("seasons")
    if seasons is None:
        for ex in body.get("extra") or []:
            if isinstance(ex, dict) and "season" in str(ex.get("name") or "").lower():
                seasons = ex.get("value")
    by = body.get("requestedBy")
    if not isinstance(by, dict):
        by = {"username": by} if isinstance(by, str) and by else {}
    by = dict(by)
    for src, dst in (("requestedBy_username", "username"), ("requestedBy_email", "email"),
                     ("requestedBy_plexUsername", "plexUsername"), ("requestedBy_plexId", "plexId")):
        if req.get(src) and not by.get(dst):
            by[dst] = req.get(src)
    if "is4k" in body:
        four = _truthy(body.get("is4k"))
    else:
        # the default template has no is4k: a 4k request moves status4k, not status
        s, s4 = str(media.get("status") or "").upper(), str(media.get("status4k") or "").upper()
        four = s4 in ("PENDING", "PROCESSING") and s not in ("PENDING", "PROCESSING")
    return {"notification_type": str(ntype).upper() if ntype else None, "request_id": srid,
            "media_type": mtype, "tmdb_id": tmdb, "seasons": seasons, "is4k": four,
            "requested_by": by}


def from_remote(full: Dict[str, Any]) -> Dict[str, Any]:
    """the parts of seerr's GET /request/{id} answer the bridge reads."""
    media = full.get("media") if isinstance(full.get("media"), dict) else {}
    return {"media_type": str(full.get("type") or media.get("mediaType") or "").lower(),
            "tmdb_id": _int(media.get("tmdbId")), "media_id": _int(media.get("id")),
            "seasons": full.get("seasons"), "is4k": _truthy(full.get("is4k")),
            "status": _int(full.get("status")),
            "requested_by": full.get("requestedBy") if isinstance(full.get("requestedBy"), dict) else {}}


def match_profile(by: Dict[str, Any], profiles: List[dict],
                  plex_account: Callable[[str], Optional[dict]]) -> Optional[int]:
    """the profile a seerr user is: their plex account first (sign in with
    plex and imports from plex link it), then their jellyfin id, then their
    plex name, then a profile with their name. None when nothing matches, or
    a name fits more than one profile."""
    if not by:
        return None
    plex_id = by.get("plexId")
    if plex_id not in (None, "", 0):
        try:
            hit = plex_account(str(plex_id))
        except Exception:  # noqa: BLE001 - the other ways still match
            hit = None
        if hit and hit.get("id"):
            return int(hit["id"])
    jf = norm_jellyfin_id(by.get("jellyfinUserId"))
    if jf:
        for p in profiles:
            if norm_jellyfin_id(p.get("jellyfin_user_id")) == jf:
                return int(p["id"])

    def one(names, fields):
        names = {str(n).strip().lower() for n in names if n}
        if not names:
            return None
        hits = {int(p["id"]) for p in profiles
                if any(str(p.get(f) or "").strip().lower() in names for f in fields)}
        return hits.pop() if len(hits) == 1 else None

    return (one([by.get("plexUsername")], ("plex_username",))
            or one([by.get("jellyfinUsername"), by.get("username"), by.get("displayName"),
                    by.get("plexUsername")], ("name", "plex_username")))


def requester_name(by: Dict[str, Any]) -> str:
    return str(by.get("displayName") or by.get("username") or by.get("plexUsername")
               or by.get("jellyfinUsername") or by.get("email") or "")[:200]


# ---- the bridge -------------------------------------------------------------------

class SeerrBridge:
    def __init__(self, *, service, links: SeerrLinks,
                 remote: Callable[[], Optional[SeerrRemote]],
                 list_profiles: Callable[[], List[dict]],
                 plex_account: Callable[[str], Optional[dict]] = lambda _pid: None):
        self.svc = service
        self.links = links
        self._remote = remote
        self._list_profiles = list_profiles
        self._plex_account = plex_account
        self._sync_lock = threading.Lock()

    def remote(self) -> Optional[SeerrRemote]:
        try:
            r = self._remote()
        except Exception:  # noqa: BLE001 - a broken setting is no seerr
            logger.debug("seerr bridge: remote settings unreadable", exc_info=True)
            return None
        return r if r is not None and r.configured else None

    # ---- in: a seerr webhook --------------------------------------------------------

    def handle(self, body: Dict[str, Any], api_user=None):
        """(answer, http status) for a webhook or a POST /request carrying a
        seerr request id."""
        w = parse_webhook(body)
        ntype, srid = w["notification_type"], w["request_id"]
        if ntype == TEST:
            return {"message": "SoulSync got the test."}, 200
        if ntype == DECLINED:
            return self._declined_in_seerr(srid), 200
        if ntype and ntype not in NEW_REQUEST:
            # available, failed, issues, and the events soulsync's own pushes cause
            return {"message": "Ignored.", "ignored": ntype}, 200
        if srid is None:
            # no seerr request id: a plain request, exactly as before
            if "mediaType" not in body and w["media_type"]:
                body = {"mediaType": w["media_type"], "mediaId": w["tmdb_id"], "is4k": w["is4k"],
                        "seasons": _seasons_from(w["seasons"]) or "all"}
            return self.svc.create_request(api_user, body), 201

        remote = self.remote()
        full = None
        if remote is not None:
            try:
                full = from_remote(remote.get_request(srid))
            except SeerrRemoteError as e:
                logger.warning("seerr bridge: could not read seerr request %s: %s", srid, e)
        media_type = (full or {}).get("media_type") or w["media_type"]
        tmdb_id = (full or {}).get("tmdb_id") or w["tmdb_id"]
        from core.seerr_compat import shapes
        kind = shapes.soulsync_kind(media_type)
        if not kind or not tmdb_id or tmdb_id <= 0:
            raise SeerrError(400, "mediaType (movie or tv) and mediaId are required.")

        if not self.links.claim(srid, kind, tmdb_id, ntype):
            # the same seerr request again, or seerr telling us about our own approval
            link = self.links.get(srid) or {}
            return {"message": "Already handled.", "duplicate": True,
                    "outcome": link.get("outcome")}, 200

        by = (full or {}).get("requested_by") or w["requested_by"]
        seasons = _seasons_from((full or {}).get("seasons") if full else w["seasons"]) \
            if kind == "show" else None
        is4k = (full or {}).get("is4k") if full else w["is4k"]
        seerr_status = (full or {}).get("status") or (
            SEERR_PENDING if ntype == "MEDIA_PENDING" else SEERR_APPROVED if ntype else None)
        pid = match_profile(by, self._safe_profiles(), self._plex_account)
        acting = pid if pid is not None else api_user
        req_body = {"mediaType": media_type, "mediaId": tmdb_id, "is4k": bool(is4k)}
        if kind == "show":
            req_body["seasons"] = seasons if seasons else "all"
        for k in ("profileId", "serverId", "rootFolder"):
            if k in body:
                req_body[k] = body[k]
        self.links.update(srid, seerr_media_id=(full or {}).get("media_id"),
                          seasons=json.dumps(seasons) if seasons else None,
                          is4k=1 if is4k else 0, seerr_status=seerr_status,
                          requester=requester_name(by) or None)

        try:
            created = self.svc.create_request(acting, req_body)
        except SeerrError as e:
            return self._refused(srid, e, seerr_status, remote, kind, tmdb_id)
        except Exception:
            self.links.forget(srid)       # nothing was filed: a resend may try again
            raise

        rid = _int(created.get("id"), 0) or None
        waiting = created.get("status") == shapes.REQUEST_PENDING
        self.links.update(srid, profile_id=_int((created.get("requestedBy") or {}).get("id")),
                          soulsync_request_id=rid, outcome=WAITING if waiting else ADDED,
                          all_seasons=1 if self._all_seasons(kind, tmdb_id, seasons) else 0)
        if not waiting:
            self._push_decision(srid, True)
        self.sync([srid])
        created["seerrRequestId"] = srid
        return created, 201

    def _safe_profiles(self) -> List[dict]:
        try:
            return list(self._list_profiles() or [])
        except Exception:  # noqa: BLE001 - nobody matched, the fallback answers
            logger.debug("seerr bridge: profiles unreadable", exc_info=True)
            return []

    def _all_seasons(self, kind, tmdb_id, seasons) -> bool:
        if kind != "show" or not seasons:
            return True
        try:
            detail = self.svc._detail("show", tmdb_id) or {}
        except Exception:  # noqa: BLE001
            return False
        real = {_int(s.get("season_number"), 0) for s in detail.get("seasons") or []} - {0, None}
        return bool(real) and real <= set(seasons)

    def _refused(self, srid, err: SeerrError, seerr_status, remote, kind, tmdb_id):
        from core.seerr_compat.service import NOT_LINKED, TURNED_OFF
        if err.status == 409:
            # already here or already on its way: seerr hears when it lands
            self.links.update(srid, outcome=EXISTS)
            self.sync([srid])
            return {"message": str(err), "outcome": EXISTS, "seerrRequestId": srid}, 200
        if err.status == 403 and str(err) not in (NOT_LINKED, TURNED_OFF):
            # soulsync's rules say no (can't request this kind, rating limit,
            # request limit): seerr's request is declined
            self.links.update(srid, outcome=REFUSED)
            self._push_decision(srid, False)
            return {"message": str(err), "outcome": REFUSED, "seerrRequestId": srid}, 200
        # nobody to act for, or something broke: nothing filed, a resend may try again
        self.links.forget(srid)
        raise err

    def _declined_in_seerr(self, srid):
        """seerr declined a request. our own decline comes back here too and
        is ignored; one an admin made in seerr withdraws the soulsync request
        still waiting for it."""
        link = self.links.get(srid) if srid is not None else None
        if not link:
            return {"message": "Ignored.", "ignored": DECLINED}
        if link.get("pushed_decision") == "declined":
            return {"message": "Ignored, SoulSync declined it.", "ignored": DECLINED}
        rid = _int(link.get("soulsync_request_id"))
        if rid and link.get("outcome") == WAITING:
            db = self.svc._db()
            row = db.get_video_request(rid)
            if row and row.get("status") == "pending":
                db.delete_video_request(rid)
        self.links.update(srid, outcome=GONE, seerr_status=SEERR_DECLINED)
        return {"message": "Declined in Seerr.", "outcome": GONE}

    # ---- out: what soulsync tells seerr ------------------------------------------------

    def _push_decision(self, srid: int, approve: bool) -> bool:
        """approve or decline seerr's request once, and only while seerr still
        waits on it. the decision is written down before the call, so the
        webhook seerr fires for it is recognised as ours."""
        link = self.links.get(srid)
        remote = self.remote()
        if not link or remote is None or link.get("pushed_decision"):
            return False
        if _int(link.get("seerr_status")) not in (None, SEERR_PENDING):
            return False
        word = "approved" if approve else "declined"
        self.links.update(srid, pushed_decision=word)
        try:
            remote.decide(srid, approve)
        except SeerrRemoteError as e:
            logger.warning("seerr bridge: could not mark seerr request %s %s: %s", srid, word, e)
            self.links.update(srid, pushed_decision=None)
            return False
        self.links.update(srid, seerr_status=SEERR_APPROVED if approve else SEERR_DECLINED)
        return True

    def _media_state(self, link: dict) -> Optional[str]:
        """available | partial | None for what the library has of the request."""
        from core.video import title_status as ts
        svc = self.svc
        kind, tmdb = link["kind"], int(link["tmdb_id"])
        got = ts.batch(svc._db(), svc.store, kind, [tmdb], server=svc._server(),
                       today=svc._today()).get(tmdb) or {}
        if kind == "movie":
            return "available" if got.get("owned") else None
        cov = got.get("coverage") or {}
        try:
            wanted = json.loads(link.get("seasons") or "null")
        except (TypeError, ValueError):
            wanted = None
        if wanted:
            cov = {s: v for s, v in cov.items() if int(s) in set(wanted)}
        state = ts.coverage_status(cov)
        if wanted and state == ts.AVAILABLE and len(cov) < len(set(wanted)):
            state = ts.PARTIAL      # an asked-for season with nothing aired here yet
        if state == ts.AVAILABLE:
            return "available" if int(link.get("all_seasons") or 0) else "partial"
        return "partial" if state == ts.PARTIAL else None

    def _sync_one(self, link: dict, remote: SeerrRemote) -> None:
        srid = int(link["seerr_request_id"])
        if link["outcome"] == ADDED and not link.get("pushed_decision"):
            self._push_decision(srid, True)       # a push that failed before
        if link["outcome"] == WAITING:
            rid = _int(link.get("soulsync_request_id"))
            row = self.svc._db().get_video_request(rid) if rid else None
            if row is None:
                self.links.update(srid, outcome=GONE)       # withdrawn in soulsync
                return
            if row.get("status") == "pending":
                return
            approved = row.get("status") == "approved"
            self.links.update(srid, outcome=ADDED if approved else REFUSED)
            self._push_decision(srid, approved)
            if not approved:
                return
        state = self._media_state(link)
        if _MEDIA_RANK.get(state, 0) <= _MEDIA_RANK.get(link.get("pushed_media"), 0):
            return
        media_id = _int(link.get("seerr_media_id"))
        if not media_id:
            try:
                media_id = from_remote(remote.get_request(srid)).get("media_id")
            except SeerrRemoteError as e:
                logger.debug("seerr bridge: request %s unreadable: %s", srid, e)
                return
            if not media_id:
                return
            self.links.update(srid, seerr_media_id=media_id)
        try:
            remote.media_status(media_id, state, bool(link.get("is4k")))
        except SeerrRemoteError as e:
            logger.warning("seerr bridge: could not mark seerr media %s %s: %s", media_id, state, e)
            return
        self.links.update(srid, pushed_media=state)

    def sync(self, only: Optional[List[int]] = None) -> int:
        """tell seerr what changed: decisions made on soulsync's Requests page
        and titles that landed. each is pushed once. returns links looked at."""
        remote = self.remote()
        if remote is None:
            return 0
        with self._sync_lock:
            rows = self.links.open_links()
            if only is not None:
                keep = {int(x) for x in only}
                rows = [r for r in rows if int(r["seerr_request_id"]) in keep]
            for link in rows:
                try:
                    self._sync_one(link, remote)
                except Exception:  # noqa: BLE001 - one link never stops the rest
                    logger.exception("seerr bridge: sync of request %s failed",
                                     link.get("seerr_request_id"))
            return len(rows)


# events after which seerr may have something new to hear
SYNC_EVENTS = frozenset({"video_request_approved", "video_request_denied", "video_request_available",
                         "video_download_completed", "video_batch_complete"})
