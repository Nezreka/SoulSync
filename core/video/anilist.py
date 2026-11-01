"""AniList, read by id: the anime facts TMDB and TVDB don't carry.

Free and keyless (GraphQL at graphql.anilist.co). Up to 50 entries come back
in one request, so a show with five seasons is one call. A title search is
kept only for an animated show the id map doesn't know, with the strict
exact-title + year guard the old worker had.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

BASE = "https://graphql.anilist.co"

_FIELDS = (
    "id idMal format status episodes seasonYear averageScore popularity siteUrl "
    "title{romaji english native} synonyms "
    "startDate{year} "
    "studios(isMain:true){nodes{name}} "
    "nextAiringEpisode{episode airingAt} "
    "coverImage{large} bannerImage"
)
BY_IDS = "query($ids:[Int]){Page(perPage:50){media(id_in:$ids,type:ANIME){" + _FIELDS + "}}}"
BY_TITLE = "query($search:String){Media(search:$search,type:ANIME){" + _FIELDS + "}}"


def _norm_title(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def media_row(m: Any) -> Optional[Dict[str, Any]]:
    """one AniList Media as the facts we keep, or None. pure."""
    if not isinstance(m, dict) or not m.get("id"):
        return None
    t = m.get("title") or {}
    score = m.get("averageScore")
    nxt = m.get("nextAiringEpisode") or {}
    studios = [n.get("name") for n in ((m.get("studios") or {}).get("nodes") or [])
               if isinstance(n, dict) and n.get("name")]
    return {
        "anilist_id": int(m["id"]),
        "mal_id": m.get("idMal"),
        "format": m.get("format"),
        "status": m.get("status"),
        "episodes": m.get("episodes"),
        "season_year": m.get("seasonYear") or (m.get("startDate") or {}).get("year"),
        "score": score if isinstance(score, int) and 0 < score <= 100 else None,
        "popularity": m.get("popularity"),
        "site_url": m.get("siteUrl"),
        "title_romaji": t.get("romaji"),
        "title_english": t.get("english"),
        "title_native": t.get("native"),
        "synonyms": [s for s in (m.get("synonyms") or []) if isinstance(s, str) and s.strip()],
        "studios": studios,
        "next_episode": nxt.get("episode"),
        "next_airing_at": nxt.get("airingAt"),
        "cover_url": (m.get("coverImage") or {}).get("large"),
        "banner_url": m.get("bannerImage"),
    }


def by_ids(post, ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """{anilist_id: facts} for up to 50 ids in one request. raises on a call
    failure so the worker records an error, not 'no data'."""
    ids = [int(i) for i in ids if i][:50]
    if not ids:
        return {}
    j = post(BASE, {"query": BY_IDS, "variables": {"ids": ids}})
    media = (((j or {}).get("data") or {}).get("Page") or {}).get("media") or []
    out = {}
    for m in media:
        row = media_row(m)
        if row:
            out[row["anilist_id"]] = row
    return out


def by_title(post, title: Any, year: Any = None) -> Optional[Dict[str, Any]]:
    """the AniList entry for an animated show the id map doesn't know, only on
    an exact title match (and the year within one when both know it): a looser
    search scored Arrow, Angel and The Bear off same-named anime."""
    if not title:
        return None
    j = post(BASE, {"query": BY_TITLE, "variables": {"search": str(title)}})
    m = ((j or {}).get("data") or {}).get("Media") if isinstance(j, dict) else None
    row = media_row(m)
    if not row:
        return None
    want = _norm_title(title)
    got = {_norm_title(row["title_romaji"]), _norm_title(row["title_english"])}
    if not want or want not in got:
        return None
    start = row.get("season_year")
    if isinstance(year, int) and isinstance(start, int) and abs(year - start) > 1:
        return None
    return row


def search_titles(rows: List[Dict[str, Any]]) -> List[str]:
    """the names a release of this anime may carry: romaji, english and the
    synonyms, latin script only and long enough to mean something. pure."""
    out: List[str] = []
    seen = set()
    for r in rows or []:
        for name in [r.get("title_romaji"), r.get("title_english"), *(r.get("synonyms") or [])]:
            text = re.sub(r"\s+", " ", str(name or "")).strip()
            if len(text) < 4 or not re.search(r"[A-Za-z]{3}", text):
                continue
            if any(ord(ch) > 0x24F for ch in text):   # kana / kanji / hangul: no release uses them
                continue
            if text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
    return out
