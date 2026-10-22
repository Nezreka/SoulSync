"""the json lidarr clients expect, built from our rows. no io in here."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional

# what system/status reports. clients only check that it answers with a version
LIDARR_VERSION = "2.9.6.4552"

PRIMARY_TYPES = ("Album", "EP", "Single", "Broadcast", "Other")
SECONDARY_TYPES = ("Studio", "Compilation", "Soundtrack", "Spokenword", "Interview",
                   "Audiobook", "Audio drama", "Live", "Remix", "DJ-mix", "Mixtape/Street",
                   "Demo", "Field recording")
RELEASE_STATUSES = ("Official", "Promotion", "Bootleg", "Pseudo-Release")

# soulsync's own quality profile decides formats, so there is one to pick
QUALITY_PROFILES = [{"id": 1, "name": "SoulSync", "upgradeAllowed": False, "cutoff": 0,
                     "items": []}]

_MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _profile(pid, name, primary, secondary):
    return {
        "id": pid,
        "name": name,
        "primaryAlbumTypes": [{"albumType": {"id": i, "name": n}, "allowed": n in primary}
                              for i, n in enumerate(PRIMARY_TYPES)],
        "secondaryAlbumTypes": [{"albumType": {"id": i, "name": n}, "allowed": n in secondary}
                                for i, n in enumerate(SECONDARY_TYPES)],
        "releaseStatuses": [{"releaseStatus": {"id": i, "name": n}, "allowed": n == "Official"}
                            for i, n in enumerate(RELEASE_STATUSES)],
    }


# lidarr's own defaults. "standard" is studio albums only, which is what
# "monitor all" on an artist request should mean: not every single and live set
DEFAULT_METADATA_PROFILES = [
    _profile(1, "Standard", {"Album"}, {"Studio"}),
    _profile(2, "Everything", set(PRIMARY_TYPES), set(SECONDARY_TYPES)),
]


def metadata_profiles(saved: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """the defaults, with any profile a client saved over them."""
    out = []
    for prof in DEFAULT_METADATA_PROFILES:
        override = (saved or {}).get(str(prof["id"]))
        out.append(merge_metadata_profile(prof, override) if isinstance(override, dict)
                   else copy.deepcopy(prof))
    return out


def merge_metadata_profile(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """take only the allowed flags from what a client PUT. ids and names stay ours."""
    out = copy.deepcopy(base)
    for key, inner in (("primaryAlbumTypes", "albumType"), ("secondaryAlbumTypes", "albumType"),
                       ("releaseStatuses", "releaseStatus")):
        allowed = {}
        for item in incoming.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get(inner), dict):
                allowed[str(item[inner].get("name") or "")] = bool(item.get("allowed"))
        for item in out[key]:
            name = item[inner]["name"]
            if name in allowed:
                item["allowed"] = allowed[name]
    return out


def album_allowed(profile: Dict[str, Any], album_type: str, secondary_types: Iterable[str]) -> bool:
    """would lidarr keep this release group under this metadata profile."""
    primary = {i["albumType"]["name"].lower() for i in profile.get("primaryAlbumTypes", []) if i.get("allowed")}
    secondary = {i["albumType"]["name"].lower() for i in profile.get("secondaryAlbumTypes", []) if i.get("allowed")}
    if (album_type or "Other").lower() not in primary:
        return False
    # no secondary type on musicbrainz means a plain studio release
    wanted = [s.lower() for s in (secondary_types or [])] or ["studio"]
    return all(s in secondary for s in wanted)


def parse_term(term: str) -> Dict[str, str]:
    """lookup terms: "lidarr:<mbid>", "mbid:<mbid>", a bare mbid, or a name."""
    term = (term or "").strip()
    head, sep, rest = term.partition(":")
    if sep and head.lower() in ("lidarr", "mbid", "lidarrid"):
        found = _MBID.search(rest)
        if found:
            return {"mbid": found.group(0).lower()}
    if _MBID.fullmatch(term):
        return {"mbid": term.lower()}
    return {"name": term}


def iso_date(value: str) -> Optional[str]:
    """musicbrainz gives "1997", "1997-05" or "1997-05-21". lidarr wants a timestamp."""
    parts = (value or "").strip().split("-")
    if not parts or not parts[0].isdigit() or len(parts[0]) != 4:
        return None
    year = parts[0]
    month = parts[1] if len(parts) > 1 and parts[1].isdigit() else "01"
    day = parts[2] if len(parts) > 2 and parts[2].isdigit() else "01"
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}T00:00:00Z"


def _stamp(ts) -> Optional[str]:
    if not ts:
        return None
    import datetime
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def album_cover(mbid: str) -> List[Dict[str, str]]:
    url = f"https://coverartarchive.org/release-group/{mbid}/front-500"
    return [{"url": url, "remoteUrl": url, "coverType": "cover", "extension": ".jpg"}]


def statistics(owned: int, total: Optional[int]) -> Dict[str, Any]:
    owned = max(0, int(owned or 0))
    total = int(total or 0)
    if total and owned > total:
        owned = total
    pct = round(owned * 100.0 / total, 1) if total else 0.0
    return {"trackFileCount": owned, "trackCount": total, "totalTrackCount": total,
            "sizeOnDisk": 0, "percentOfTracks": pct}


def artist_resource(row: Dict[str, Any], albums: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """one artist. a row without an id is a lookup result nobody added yet."""
    name = row.get("name") or ""
    mbid = row.get("mbid") or ""
    # like lidarr, the counts only cover the albums being tracked
    wanted = [a for a in albums or [] if a.get("monitored")]
    owned = sum(int(a.get("owned_count") or 0) for a in wanted)
    total = sum(int(a.get("track_count") or 0) for a in wanted)
    stats = statistics(owned, total)
    stats["albumCount"] = len(albums or [])
    folder = (row.get("root_folder") or "").rstrip("/\\")
    out = {
        "artistName": name,
        "foreignArtistId": mbid,
        "mbId": mbid,
        "status": "continuing",
        "ended": False,
        "overview": "",
        "artistType": row.get("artist_type") or "",
        "disambiguation": row.get("disambiguation") or "",
        "links": [{"url": f"https://musicbrainz.org/artist/{mbid}", "name": "musicbrainz"}] if mbid else [],
        "images": [],
        "path": f"{folder}/{name}" if folder else name,
        "qualityProfileId": int(row.get("quality_profile_id") or 1),
        "metadataProfileId": int(row.get("metadata_profile_id") or 1),
        "monitored": bool(row.get("monitored")),
        "monitorNewItems": row.get("monitor_new_items") or "none",
        "rootFolderPath": row.get("root_folder") or "",
        "genres": [],
        "cleanName": clean_name(name),
        "sortName": name.lower(),
        "tags": [],
        "added": _stamp(row.get("added_at")) or "0001-01-01T00:00:00Z",
        "ratings": {"votes": 0, "value": 0.0},
        "statistics": stats,
    }
    if row.get("id"):
        out["id"] = int(row["id"])
    return out


def album_resource(row: Dict[str, Any], artist: Dict[str, Any]) -> Dict[str, Any]:
    """one album. foreignAlbumId is the musicbrainz release group, same as lidarr."""
    mbid = row.get("mbid") or ""
    stats = statistics(row.get("owned_count") or 0, row.get("track_count"))
    searched = bool(row.get("searched_at"))
    out = {
        "title": row.get("title") or "",
        "disambiguation": row.get("disambiguation") or "",
        "overview": "",
        "artistId": int(artist.get("id") or 0),
        "foreignAlbumId": mbid,
        "monitored": bool(row.get("monitored")),
        "anyReleaseOk": bool(row.get("any_release_ok", True)),
        "profileId": int(artist.get("quality_profile_id") or 1),
        "duration": 0,
        "albumType": row.get("album_type") or "Other",
        "secondaryTypes": list(row.get("secondary_types") or []),
        "mediumCount": 1,
        "ratings": {"votes": 0, "value": 0.0},
        "releaseDate": iso_date(row.get("release_date") or ""),
        "releases": [],
        "genres": [],
        "media": [],
        "artist": artist_resource(artist),
        "images": album_cover(mbid) if mbid else [],
        "links": [{"url": f"https://musicbrainz.org/release-group/{mbid}", "name": "musicbrainz"}] if mbid else [],
        "statistics": stats,
        "added": _stamp(row.get("added_at")) or "0001-01-01T00:00:00Z",
        # "grabbed" is how clients tell searching from done
        "grabbed": searched and stats["percentOfTracks"] < 100,
    }
    if row.get("id"):
        out["id"] = int(row["id"])
    return out


def paged(records: List[Dict[str, Any]], page=1, page_size=10, sort_key="date") -> Dict[str, Any]:
    """queue, history and wanted are paged in lidarr."""
    try:
        page = max(1, int(page))
        page_size = max(1, int(page_size))
    except (TypeError, ValueError):
        page, page_size = 1, 10
    start = (page - 1) * page_size
    return {"page": page, "pageSize": page_size, "sortKey": sort_key, "sortDirection": "descending",
            "totalRecords": len(records), "records": records[start:start + page_size]}


def system_status() -> Dict[str, Any]:
    return {"appName": "Lidarr", "instanceName": "SoulSync", "version": LIDARR_VERSION,
            "isProduction": True, "isDebug": False, "isDocker": True, "urlBase": "/lidarr",
            "authentication": "external", "branch": "master", "osName": "linux",
            "runtimeName": "SoulSync"}


def root_folder(path: str) -> Dict[str, Any]:
    return {"id": 1, "name": "SoulSync", "path": path, "accessible": True, "freeSpace": 0,
            "totalSpace": 0, "defaultMetadataProfileId": 1, "defaultQualityProfileId": 1,
            "defaultMonitorOption": "none", "defaultNewItemMonitorOption": "none",
            "defaultTags": [], "unmappedFolders": []}
