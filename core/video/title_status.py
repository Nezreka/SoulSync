"""one status per movie or show (#2160): what the library has, what's being
fetched, what's waiting on an admin. the seerr api (#2151) and the video pages
both read it from here, so a poster and a seerr client can never disagree.

the status is global, not per profile: a title on anyone's wishlist is on the
way, a request from anyone that's still waiting is pending.

    available    in the library (a show: every aired episode)
    partial      a show with some aired episodes in the library
    downloading  a grab for it is in the download client
    searching    a grab for it is looking for a release
    failed       still wanted, and the last grab for it failed
    processing   wanted: on a wishlist, a followed show, an approved request
    pending      a request waiting for an admin
    None         nobody asked for it
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

AVAILABLE = "available"
PARTIAL = "partial"
DOWNLOADING = "downloading"
SEARCHING = "searching"
FAILED = "failed"
PROCESSING = "processing"
PENDING = "pending"

# seerr's MediaStatus: pending 2, processing 3, partially available 4, available 5.
# seerr has no searching / downloading / failed: those are still processing there.
SEERR_CODE = {AVAILABLE: 5, PARTIAL: 4, DOWNLOADING: 3, SEARCHING: 3, FAILED: 3,
              PROCESSING: 3, PENDING: 2}

# video_downloads statuses (core/video/stats.py)
_ACTIVE = ("queued", "searching", "downloading", "importing")
_FAILED = ("failed", "import_failed")
_CHUNK = 400


# ---- the rule ------------------------------------------------------------------

def coverage_status(seasons: Optional[Dict[int, tuple]]) -> Optional[str]:
    """available when every aired episode is in the library, partial when
    some are, None when none are. ``seasons`` is {season: (aired, owned)}."""
    seasons = seasons or {}
    aired = sum(a for a, _o in seasons.values())
    owned = sum(o for _a, o in seasons.values())
    if not aired or not owned:
        return None
    return AVAILABLE if owned >= aired else PARTIAL


def title_status(kind: str, *, owned: bool, coverage: Optional[Dict[int, tuple]] = None,
                 state: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """library first, then a grab in flight, then a failed grab on something
    still wanted, then wanted, then a request still waiting."""
    state = state or {}
    if owned:
        if kind == "movie":
            return AVAILABLE
        if coverage:
            st = coverage_status(coverage)
            if st is not None:
                return st
        else:
            # owned but no aired episode known yet: trust the library row
            return AVAILABLE
    if state.get("downloading"):
        return DOWNLOADING
    if state.get("searching"):
        return SEARCHING
    wanted = state.get("wished") or state.get("approved") or state.get("followed")
    # a failed grab on something nobody wants any more is just history
    if state.get("failed") and (state.get("wished") or state.get("approved")):
        return FAILED
    if wanted:
        return PROCESSING
    if state.get("pending"):
        return PENDING
    return None


def processing_reason(state: Optional[Dict[str, Any]]) -> Optional[str]:
    """why a title is on the way, for the badge's words."""
    state = state or {}
    for key in ("approved", "wished", "followed"):
        if state.get(key):
            return key
    return None


def seerr_code(status: Optional[str]) -> Optional[int]:
    return SEERR_CODE.get(status) if status else None


def season_states(coverage: Optional[Dict[int, tuple]],
                  wished_seasons: Iterable[int] = ()) -> List[Dict[str, int]]:
    """per real season: what the library has, or processing when its episodes
    are on a wishlist. [{season, status, aired, owned}] in season order."""
    out: Dict[int, Dict[str, Any]] = {}
    for sn, (aired, owned) in (coverage or {}).items():
        if sn and aired and owned:
            out[int(sn)] = {"season": int(sn), "aired": int(aired), "owned": int(owned),
                            "status": AVAILABLE if owned >= aired else PARTIAL}
    for sn in wished_seasons or ():
        if sn and int(sn) not in out:
            aired, owned = (coverage or {}).get(int(sn), (0, 0))
            out[int(sn)] = {"season": int(sn), "aired": int(aired), "owned": int(owned),
                            "status": PROCESSING}
    return [out[k] for k in sorted(out)]


def blocks_request(kind: str, status: Optional[str], state: Optional[Dict[str, Any]] = None) -> bool:
    """a new request would be a duplicate: the title is already on its way.
    a show the library has part of stays requestable (the rest is a fair ask),
    and so does a failed grab (asking again is how a member says try again).
    a followed show only gets what airs next, so it stays requestable too."""
    if status in (DOWNLOADING, SEARCHING):
        return True
    if status != PROCESSING:
        return False
    reason = processing_reason(state)
    return kind == "movie" or reason in ("approved", "wished")


def request_state(kind: str, status: Optional[str], state: Optional[Dict[str, Any]],
                  requests: Iterable[dict], viewer_id: int) -> str:
    """what the request button should be for this viewer:
    'available' nothing to ask for, 'mine' they already asked, 'on_the_way'
    it's coming anyway, 'others' someone else asked (asking too adds them to
    that request), 'open' ask away."""
    pending = [r for r in requests or () if r.get("status") == "pending"]
    if status == AVAILABLE:
        return "available"
    if any(int(r.get("profile_id") or 0) == int(viewer_id) for r in pending):
        return "mine"
    if blocks_request(kind, status, state):
        return "on_the_way"
    if pending:
        return "others"
    return "open"


def public_requests(rows: Iterable[dict], viewer_id: int, is_admin: bool) -> List[dict]:
    """request rows as the viewer may see them: an admin sees who asked, a
    member sees their own as 'you' and nobody else's name."""
    out = []
    for r in rows or ():
        mine = int(r.get("profile_id") or 0) == int(viewer_id)
        who = None
        if is_admin:
            who = r.get("requester_name") or None
        out.append({"mine": mine, "who": who, "status": r.get("status"),
                    "created_at": r.get("created_at"), "kind": r.get("kind"),
                    "season": r.get("season_number"), "episode": r.get("episode_number")})
    return out


# ---- reading it out of the video db --------------------------------------------

def _ids(tmdb_ids: Iterable) -> List[int]:
    out = []
    for t in tmdb_ids or ():
        try:
            out.append(int(t))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _chunks(ids: List[int]):
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i:i + _CHUNK]
        yield chunk, ",".join("?" * len(chunk))


class TitleStatusStore:
    """read only, one query per question per batch."""

    def __init__(self, connect: Callable):
        self._connect = connect

    def title_states(self, kind: str, tmdb_ids) -> Dict[int, Dict[str, Any]]:
        """{tmdb_id: {wished, followed, pending, approved, searching,
        downloading, failed, progress}} for the titles that have any of them.
        approved means approved and not in the library yet."""
        ids = _ids(tmdb_ids)
        out: Dict[int, Dict[str, Any]] = {}
        if not ids or kind not in ("movie", "show"):
            return out

        def mark(tid, key, value=True):
            out.setdefault(int(tid), {})[key] = value

        wish_kind = "movie" if kind == "movie" else "episode"
        req_kinds = ("movie",) if kind == "movie" else ("show", "episode")
        dl_kinds = ("movie",) if kind == "movie" else ("show", "episode")
        rk = ",".join("?" * len(req_kinds))
        dk = ",".join("?" * len(dl_kinds))
        conn = self._connect()
        try:
            for chunk, ph in _chunks(ids):
                for r in conn.execute(
                        "SELECT tmdb_id, SUM(status <> 'failed'), SUM(status = 'failed') "
                        f"FROM video_wishlist WHERE kind=? AND tmdb_id IN ({ph}) GROUP BY tmdb_id",
                        [wish_kind] + chunk):
                    mark(r[0], "wished")
                    if not r[1] and r[2]:
                        mark(r[0], "failed")
                if kind == "show":
                    for r in conn.execute(
                            "SELECT DISTINCT tmdb_id FROM video_watchlist WHERE kind='show' "
                            f"AND state='follow' AND tmdb_id IN ({ph})", chunk):
                        mark(r[0], "followed")
                for r in conn.execute(
                        f"SELECT tmdb_id, status FROM video_requests WHERE kind IN ({rk}) "
                        f"AND tmdb_id IN ({ph}) AND (status='pending' OR "
                        "(status='approved' AND available_at IS NULL))",
                        list(req_kinds) + chunk):
                    mark(r[0], "pending" if r[1] == "pending" else "approved")
                # media_id is TEXT (a tmdb id or a youtube id), so match it as text
                sids = [str(t) for t in chunk]
                for r in conn.execute(
                        "SELECT media_id, status, progress FROM video_downloads "
                        f"WHERE media_source='tmdb' AND kind IN ({dk}) "
                        f"AND status IN ({','.join('?' * len(_ACTIVE))}) AND media_id IN ({ph})",
                        list(dl_kinds) + list(_ACTIVE) + sids):
                    tid = int(r[0])
                    if r[1] == "searching":
                        mark(tid, "searching")
                        continue
                    mark(tid, "downloading")
                    pct = float(r[2] or 0)
                    if pct > float(out[tid].get("progress") or 0):
                        mark(tid, "progress", round(pct, 1))
                # a grab that failed after the last one that worked
                st = list(_FAILED) + ["completed"]
                for r in conn.execute(
                        "SELECT media_id, "
                        f"MAX(CASE WHEN status IN ({','.join('?' * len(_FAILED))}) THEN id END), "
                        "MAX(CASE WHEN status='completed' THEN id END) FROM video_downloads "
                        f"WHERE media_source='tmdb' AND kind IN ({dk}) "
                        f"AND status IN ({','.join('?' * len(st))}) AND media_id IN ({ph}) "
                        "GROUP BY media_id",
                        list(_FAILED) + list(dl_kinds) + st + sids):
                    if r[1] and (r[2] is None or r[1] > r[2]):
                        mark(int(r[0]), "failed")
        finally:
            conn.close()
        return out

    def show_coverage(self, tmdb_ids, today: str, server_source=None) -> Dict[int, Dict[int, tuple]]:
        """{tmdb_id: {season: (aired, owned)}} over real seasons (>= 1). an
        episode counts as owned when any library copy of the show has its file."""
        ids = _ids(tmdb_ids)
        out: Dict[int, Dict[int, tuple]] = {}
        if not ids:
            return out
        conn = self._connect()
        try:
            for chunk, ph in _chunks(ids):
                srv_sql, srv_args = ("", [])
                if server_source:
                    srv_sql, srv_args = (" AND s.server_source=?", [server_source])
                rows = conn.execute(
                    "SELECT tmdb_id, season_number, COUNT(*), SUM(hf) FROM ("
                    "  SELECT s.tmdb_id AS tmdb_id, e.season_number AS season_number, "
                    "         e.episode_number, MAX(e.has_file) AS hf "
                    "  FROM shows s JOIN episodes e ON e.show_id = s.id "
                    f"  WHERE s.tmdb_id IN ({ph}){srv_sql} AND e.season_number > 0 "
                    "    AND e.air_date IS NOT NULL AND e.air_date <> '' AND e.air_date <= ? "
                    "  GROUP BY s.tmdb_id, e.season_number, e.episode_number"
                    ") GROUP BY tmdb_id, season_number",
                    chunk + srv_args + [today]).fetchall()
                for r in rows:
                    out.setdefault(int(r[0]), {})[int(r[1])] = (int(r[2] or 0), int(r[3] or 0))
        finally:
            conn.close()
        return out

    def wished_seasons(self, tmdb_id) -> List[int]:
        """seasons of a show with episodes on anyone's wishlist."""
        conn = self._connect()
        try:
            return [int(r[0]) for r in conn.execute(
                "SELECT DISTINCT season_number FROM video_wishlist WHERE kind='episode' "
                "AND tmdb_id=? AND season_number IS NOT NULL ORDER BY 1", (int(tmdb_id),))]
        finally:
            conn.close()

    def title_requests(self, kind: str, tmdb_ids) -> Dict[int, List[dict]]:
        """{tmdb_id: [request rows]} still open: waiting, or approved and not
        arrived. oldest first, so the first name is whoever asked first."""
        ids = _ids(tmdb_ids)
        out: Dict[int, List[dict]] = {}
        if not ids or kind not in ("movie", "show"):
            return out
        kinds = ("movie",) if kind == "movie" else ("show", "episode")
        rk = ",".join("?" * len(kinds))
        conn = self._connect()
        try:
            for chunk, ph in _chunks(ids):
                cur = conn.execute(
                    "SELECT id, profile_id, requester_name, kind, tmdb_id, status, created_at, "
                    "season_number, episode_number FROM video_requests "
                    f"WHERE kind IN ({rk}) AND tmdb_id IN ({ph}) AND (status='pending' OR "
                    "(status='approved' AND available_at IS NULL)) ORDER BY created_at, id",
                    list(kinds) + chunk)
                cols = [c[0] for c in cur.description]
                for r in cur.fetchall():
                    row = dict(zip(cols, r, strict=True))
                    out.setdefault(int(row["tmdb_id"]), []).append(row)
        finally:
            conn.close()
        return out


def batch(db, store: TitleStatusStore, kind: str, tmdb_ids, *, server=None, today: str,
          owned_hint: Optional[Dict[int, bool]] = None) -> Dict[int, Dict[str, Any]]:
    """{tmdb_id: {status, state, coverage, owned}} for a page of titles of one
    kind. ``owned_hint`` carries ownership a caller already knows (the engine
    stamps library_id on its items); the rest is looked up in one query."""
    ids = _ids(tmdb_ids)
    if not ids or kind not in ("movie", "show"):
        return {}
    hint = {int(k): bool(v) for k, v in (owned_hint or {}).items()}
    owned = {t for t in ids if hint.get(t)}
    unknown = [t for t in ids if t not in hint]
    if unknown:
        owned |= {int(t) for t in (db.library_ids_for_tmdb(kind, unknown, server) or {})}
    states = store.title_states(kind, ids)
    coverage = store.show_coverage(sorted(owned), today, server) if kind == "show" and owned else {}
    out = {}
    for tid in ids:
        st = states.get(tid) or {}
        cov = coverage.get(tid)
        out[tid] = {"status": title_status(kind, owned=tid in owned, coverage=cov, state=st),
                    "state": st, "coverage": cov, "owned": tid in owned}
    return out
