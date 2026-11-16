"""soulsync video data in the shapes seerr's api answers with (#2151).

pure: no flask, no database. everything here turns the tmdb engine's items,
the video library state and video request rows into the json a seerr client
reads (camelCase tmdb fields, mediaInfo, MediaRequest, User, pageInfo).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# seerr's enums (server/constants/media.ts, server/lib/permissions.ts)
STATUS_UNKNOWN = 1
STATUS_PENDING = 2
STATUS_PROCESSING = 3
STATUS_PARTIAL = 4
STATUS_AVAILABLE = 5

REQUEST_PENDING = 1
REQUEST_APPROVED = 2
REQUEST_DECLINED = 3

PERM_REQUEST = 32
PERM_AUTO_APPROVE = 128

# jellyfin user, the type seerr gives accounts imported from jellyfin
USER_TYPE_JELLYFIN = 3

VERSION = "2.7.3"

# tmdb caps every list at 500 pages
TMDB_PAGE_CAP = 500
PAGE_SIZE = 20

_TMDB_IMG = re.compile(r"image\.tmdb\.org/t/p/[^/]+(/[^?#]+)")


def tmdb_path(url: Optional[str]) -> Optional[str]:
    """'/abc.jpg' out of a full tmdb image url. seerr clients build their own
    image urls from the path, so art that isn't on tmdb has no path."""
    if not url or not isinstance(url, str):
        return None
    m = _TMDB_IMG.search(url)
    return m.group(1) if m else None


def seerr_type(kind: str) -> str:
    return "tv" if kind == "show" else "movie"


def soulsync_kind(media_type: str) -> Optional[str]:
    mt = str(media_type or "").lower()
    if mt == "movie":
        return "movie"
    if mt in ("tv", "show"):
        return "show"
    return None


def _date(item: Dict[str, Any]) -> Optional[str]:
    """the full date when the engine kept it, else the bare year."""
    d = item.get("date") or item.get("release_date") or item.get("first_air_date")
    if d:
        return str(d)
    y = item.get("year")
    return str(y) if y else None


# ---- media status -------------------------------------------------------------

def show_status_from_coverage(seasons: Dict[int, tuple]) -> Optional[int]:
    """available when every aired episode is in the library, partial when
    some are, None when none are. ``seasons`` is {season: (aired, owned)}."""
    aired = sum(a for a, _o in seasons.values())
    owned = sum(o for _a, o in seasons.values())
    if not aired or not owned:
        return None
    return STATUS_AVAILABLE if owned >= aired else STATUS_PARTIAL


def media_status(kind: str, *, owned: bool, coverage: Optional[Dict[int, tuple]] = None,
                 state: Optional[Dict[str, bool]] = None) -> Optional[int]:
    """the one status seerr shows on a card. library first, then whether
    soulsync is getting it (wishlist, a followed show, an approved request),
    then a request still waiting. None: nobody asked for it."""
    state = state or {}
    if owned:
        if kind == "movie":
            return STATUS_AVAILABLE
        if coverage:
            st = show_status_from_coverage(coverage)
            if st is not None:
                return st
        else:
            # owned but no aired episode known yet: trust the library row
            return STATUS_AVAILABLE
    if state.get("wished") or state.get("approved") or state.get("followed"):
        return STATUS_PROCESSING
    if state.get("pending"):
        return STATUS_PENDING
    return None


def season_statuses(coverage: Dict[int, tuple], wished_seasons: Iterable[int] = ()) -> List[Dict[str, int]]:
    """mediaInfo.seasons: per season what the library has, or processing when
    its episodes are on a wishlist."""
    out: Dict[int, int] = {}
    for sn, (aired, owned) in (coverage or {}).items():
        if sn and aired and owned:
            out[int(sn)] = STATUS_AVAILABLE if owned >= aired else STATUS_PARTIAL
    for sn in wished_seasons or ():
        if sn and int(sn) not in out:
            out[int(sn)] = STATUS_PROCESSING
    return [{"id": sn, "seasonNumber": sn, "status": st, "status4k": STATUS_UNKNOWN}
            for sn, st in sorted(out.items())]


def media_info(kind: str, tmdb_id: int, status: Optional[int], *, seasons=None,
               requests=None) -> Optional[Dict[str, Any]]:
    if status is None and not requests:
        return None
    return {"id": int(tmdb_id), "tmdbId": int(tmdb_id), "tvdbId": None,
            "mediaType": seerr_type(kind), "status": status or STATUS_UNKNOWN,
            "status4k": STATUS_UNKNOWN, "requests": list(requests or []),
            "seasons": list(seasons or [])}


# ---- list results -------------------------------------------------------------

def result(item: Dict[str, Any], info: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """one engine item (search / discover / trending) as a seerr search result."""
    kind = item.get("kind")
    tid = item.get("tmdb_id")
    if not tid:
        return None
    if kind == "person":
        return {"id": int(tid), "mediaType": "person", "name": item.get("title") or "",
                "profilePath": tmdb_path(item.get("poster")), "adult": False,
                "knownFor": [], "knownForDepartment": item.get("department") or None,
                "popularity": item.get("popularity") or 0}
    if kind not in ("movie", "show"):
        return None
    out = {
        "id": int(tid), "mediaType": seerr_type(kind),
        "overview": item.get("overview") or "",
        "posterPath": tmdb_path(item.get("poster") or item.get("poster_url")),
        "backdropPath": tmdb_path(item.get("backdrop") or item.get("backdrop_url")),
        "voteAverage": item.get("rating") or 0, "voteCount": 0,
        "popularity": item.get("popularity") or 0,
        "originalLanguage": item.get("original_language") or None,
        "genreIds": [], "adult": False,
        "mediaInfo": info,
    }
    if kind == "movie":
        out.update({"title": item.get("title") or "", "originalTitle": item.get("title") or "",
                    "releaseDate": _date(item), "video": False})
    else:
        out.update({"name": item.get("title") or "", "originalName": item.get("title") or "",
                    "firstAirDate": _date(item), "originCountry": []})
    return out


def page(results: List[Dict[str, Any]], page_no: int, *, total_pages: Optional[int] = None) -> Dict[str, Any]:
    """seerr's paged list. the engine doesn't say how many pages tmdb has, so a
    full page says there's more (tmdb's own cap) and a short one is the last."""
    page_no = max(1, int(page_no or 1))
    if total_pages is None:
        total_pages = TMDB_PAGE_CAP if len(results) >= PAGE_SIZE else page_no
    total_pages = max(1, int(total_pages))
    if total_pages > page_no:
        total_results = total_pages * PAGE_SIZE
    else:
        total_results = (page_no - 1) * PAGE_SIZE + len(results)
    return {"page": page_no, "totalPages": total_pages, "totalResults": total_results,
            "results": results}


# ---- details ------------------------------------------------------------------

def _people(cast) -> List[Dict[str, Any]]:
    out = []
    for i, c in enumerate(cast or []):
        if not c.get("name"):
            continue
        out.append({"id": c.get("tmdb_id") or 0, "castId": i, "creditId": "",
                    "name": c["name"], "character": c.get("character") or "",
                    "order": i, "gender": None,
                    "profilePath": tmdb_path(c.get("photo"))})
    return out


def _crew(crew) -> List[Dict[str, Any]]:
    return [{"id": c.get("tmdb_id") or 0, "creditId": "", "name": c["name"],
             "job": c.get("job") or "", "department": "", "gender": None, "profilePath": None}
            for c in crew or [] if c.get("name")]


def _videos(videos, trailer) -> List[Dict[str, Any]]:
    out = []
    for v in videos or ([trailer] if trailer else []):
        if v and v.get("key"):
            out.append({"site": "YouTube", "key": v["key"], "name": v.get("name") or "",
                        "type": v.get("type") or "Trailer", "size": 1080,
                        "url": "https://www.youtube.com/watch?v=" + v["key"]})
    return out


def detail(kind: str, d: Dict[str, Any], info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """a tmdb detail from the engine as seerr's MovieDetails / TvDetails."""
    tid = int(d.get("tmdb_id") or d.get("id"))
    facts = d.get("facts") or {}
    cast = d.get("cast_full") or d.get("cast") or []
    out = {
        "id": tid, "mediaType": seerr_type(kind),
        "overview": d.get("overview") or "", "tagline": d.get("tagline") or "",
        "posterPath": tmdb_path(d.get("poster_url")),
        "backdropPath": tmdb_path(d.get("backdrop_url")),
        "voteAverage": d.get("rating") or 0, "voteCount": 0, "popularity": 0,
        "status": d.get("status") or "",
        "genres": [{"id": 0, "name": g} for g in d.get("genres") or [] if g],
        "productionCompanies": [{"id": s.get("tmdb_id") or 0, "name": s.get("name") or "",
                                 "logoPath": tmdb_path(s.get("logo"))}
                                for s in d.get("studios") or [] if isinstance(s, dict)],
        "keywords": [{"id": 0, "name": k} for k in d.get("keywords") or [] if k],
        "credits": {"cast": _people(cast), "crew": _crew(d.get("crew"))},
        "relatedVideos": _videos(d.get("videos"), d.get("trailer")),
        "certification": d.get("content_rating") or None,
        "originalLanguage": facts.get("original_language"),
        "externalIds": {"imdbId": d.get("imdb_id"), "tvdbId": d.get("tvdb_id")},
        "watchProviders": [],
        "mediaInfo": info,
    }
    if kind == "movie":
        coll = d.get("collection") or None
        out.update({
            "title": d.get("title") or "", "originalTitle": d.get("title") or "",
            "releaseDate": d.get("release_date") or (str(d["year"]) if d.get("year") else None),
            "runtime": d.get("runtime_minutes") or 0,
            "budget": facts.get("budget") or 0, "revenue": facts.get("revenue") or 0,
            "imdbId": d.get("imdb_id"),
            "collection": {"id": coll["id"], "name": coll.get("name") or "",
                           "posterPath": tmdb_path(coll.get("poster"))}
            if isinstance(coll, dict) and coll.get("id") else None,
        })
    else:
        seasons = [s for s in d.get("seasons") or [] if s.get("season_number") is not None]
        out.update({
            "name": d.get("title") or "", "originalName": d.get("title") or "",
            "firstAirDate": d.get("first_air_date") or (str(d["year"]) if d.get("year") else None),
            "lastAirDate": d.get("last_air_date") or None,
            "episodeRunTime": [d["runtime_minutes"]] if d.get("runtime_minutes") else [],
            "networks": [{"id": 0, "name": n} for n in d.get("networks") or [] if n],
            "createdBy": [{"id": c.get("tmdb_id") or 0, "name": c["name"]}
                          for c in d.get("crew") or [] if c.get("job") == "Creator" and c.get("name")],
            "numberOfSeasons": len([s for s in seasons if int(s["season_number"]) > 0]),
            "numberOfEpisodes": sum(int(s.get("episode_total") or s.get("episode_count") or 0)
                                    for s in seasons if int(s["season_number"]) > 0),
            "seasons": [{"id": int(s["season_number"]), "seasonNumber": int(s["season_number"]),
                         "name": s.get("title") or "Season %s" % s["season_number"],
                         "episodeCount": int(s.get("episode_total") or s.get("episode_count") or 0),
                         "posterPath": tmdb_path(s.get("poster_url")), "overview": "",
                         "airDate": None}
                        for s in seasons],
            "inProduction": (d.get("status") or "") in ("Returning Series", "In Production"),
        })
    return out


def person(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": p.get("tmdb_id"), "name": p.get("name") or "",
            "biography": p.get("biography") or "", "birthday": p.get("birthday"),
            "deathday": p.get("deathday"), "placeOfBirth": p.get("place_of_birth"),
            "knownForDepartment": p.get("known_for") or "",
            "alsoKnownAs": p.get("also_known_as") or [], "gender": None, "adult": False,
            "imdbId": None, "popularity": 0, "profilePath": tmdb_path(p.get("photo"))}


def combined_credits(p: Dict[str, Any], infos: Dict[tuple, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    cast, crew = [], []
    for c in p.get("credits") or []:
        r = result(c, infos.get((c.get("kind"), c.get("tmdb_id"))))
        if not r:
            continue
        if c.get("department") == "Acting":
            r["character"] = c.get("role") or ""
            cast.append(r)
        else:
            r["job"] = c.get("role") or ""
            r["department"] = c.get("department") or ""
            crew.append(r)
    return {"id": p.get("tmdb_id"), "cast": cast, "crew": crew}


# ---- users and requests -------------------------------------------------------

def user(uid: int, name: str, *, permissions: int, jellyfin_id: Optional[str] = None,
         jellyfin_name: Optional[str] = None, request_count: int = 0) -> Dict[str, Any]:
    return {"id": int(uid), "email": "", "username": name or "",
            "displayName": jellyfin_name or name or "",
            "jellyfinUsername": jellyfin_name or name or "",
            "jellyfinUserId": jellyfin_id or None, "plexUsername": None,
            "userType": USER_TYPE_JELLYFIN, "permissions": int(permissions),
            "avatar": "", "requestCount": int(request_count),
            "movieQuotaLimit": None, "movieQuotaDays": None,
            "tvQuotaLimit": None, "tvQuotaDays": None,
            "createdAt": None, "updatedAt": None}


def page_info(results: list, page_no: int, page_size: int, total: int) -> Dict[str, Any]:
    pages = max(1, -(-int(total) // max(1, int(page_size))))
    return {"pageInfo": {"pages": pages, "pageSize": int(page_size), "results": int(total),
                         "page": int(page_no)},
            "results": results}


def request_row(row: Dict[str, Any], requested_by: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """a video_requests row as seerr's MediaRequest. episode requests count as
    tv for their show; youtube requests have no tmdb title and are left out."""
    kind = row.get("kind")
    if kind not in ("movie", "show", "episode") or not row.get("tmdb_id"):
        return None
    kind = "show" if kind == "episode" else kind
    status = row.get("status")
    if status == "pending":
        rstatus, mstatus = REQUEST_PENDING, STATUS_PENDING
    elif status == "approved":
        rstatus = REQUEST_APPROVED
        mstatus = STATUS_AVAILABLE if row.get("in_library") else STATUS_PROCESSING
    else:
        rstatus, mstatus = REQUEST_DECLINED, STATUS_UNKNOWN
    seasons = []
    if row.get("season_number") is not None:
        seasons = [{"id": int(row["season_number"]), "seasonNumber": int(row["season_number"]),
                    "status": rstatus}]
    tid = int(row["tmdb_id"])
    return {
        "id": int(row["id"]), "status": rstatus, "type": seerr_type(kind), "is4k": False,
        "createdAt": row.get("created_at"), "updatedAt": row.get("resolved_at") or row.get("created_at"),
        "media": {"id": tid, "tmdbId": tid, "tvdbId": None, "mediaType": seerr_type(kind),
                  "status": mstatus, "status4k": STATUS_UNKNOWN},
        "requestedBy": requested_by, "modifiedBy": None, "seasons": seasons,
        "serverId": None, "profileId": None, "rootFolder": None,
    }
