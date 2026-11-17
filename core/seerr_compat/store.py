"""what the video db knows about a batch of tmdb titles, for seerr's media
status (#2151). read only, one query per question per batch.

seerr's status is global, not per user: a title on anyone's wishlist is
being processed, a request from anyone that's still waiting is pending.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List

_CHUNK = 400


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


class SeerrStore:
    def __init__(self, connect: Callable):
        self._connect = connect

    def title_states(self, kind: str, tmdb_ids) -> Dict[int, Dict[str, bool]]:
        """{tmdb_id: {wished, followed, pending, approved}} for the titles that
        have any of them. approved means approved and not in the library yet."""
        ids = _ids(tmdb_ids)
        out: Dict[int, Dict[str, bool]] = {}
        if not ids or kind not in ("movie", "show"):
            return out

        def mark(tid, key):
            out.setdefault(int(tid), {})[key] = True

        wish_kind = "movie" if kind == "movie" else "episode"
        req_kinds = ("movie",) if kind == "movie" else ("show", "episode")
        rk = ",".join("?" * len(req_kinds))
        conn = self._connect()
        try:
            for chunk, ph in _chunks(ids):
                for r in conn.execute(
                        f"SELECT DISTINCT tmdb_id FROM video_wishlist WHERE kind=? AND tmdb_id IN ({ph})",
                        [wish_kind] + chunk):
                    mark(r[0], "wished")
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
