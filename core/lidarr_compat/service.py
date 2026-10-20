"""what each lidarr call does on our side.

the flask layer (api/lidarr_compat.py) only parses requests and hands them
here. the io this needs comes in as arguments so tests can stand in for it:

- mb: musicbrainz, see MusicBrainzSource for the methods
- queue_album(album, artist_row) -> how many tracks went on the wishlist
- ownership(title, artist_name, track_count) -> (owned, total)
- metadata_profile(id) -> the profile dict, for "monitor all"
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

from . import resources
from .commands import CommandRunner

logger = get_logger("lidarr_compat")

# how long an album's "do we have it" answer is good for
STATS_TTL_SECONDS = 300
# a full album list only re-checks this many stale albums per call
STATS_REFRESH_PER_CALL = 25
# album lookups remembered so the artist index can't miss them
RECENT_LOOKUPS = 200


class LidarrError(Exception):
    """a client mistake. the api answers 400 / 404 with the message."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class MusicBrainzSource:
    """the musicbrainz calls this needs, flattened to plain dicts."""

    def __init__(self, client=None, search_client=None):
        if client is None:
            from core.musicbrainz_client import MusicBrainzClient
            client = MusicBrainzClient("SoulSync", "2")
        self._client = client
        self._search = search_client

    @staticmethod
    def _artist(raw):
        if not raw or not raw.get("id"):
            return None
        return {"mbid": raw["id"], "name": raw.get("name") or "",
                "disambiguation": raw.get("disambiguation") or "",
                "artist_type": raw.get("type") or ""}

    @staticmethod
    def _release_group(raw):
        if not raw or not raw.get("id"):
            return None
        credit = [c for c in raw.get("artist-credit") or [] if isinstance(c, dict)]
        first = (credit[0].get("artist") or {}) if credit else {}
        return {"mbid": raw["id"], "title": raw.get("title") or "",
                "disambiguation": raw.get("disambiguation") or "",
                "album_type": raw.get("primary-type") or "Other",
                "secondary_types": [s for s in raw.get("secondary-types") or [] if s],
                "release_date": raw.get("first-release-date") or "",
                "artist_mbid": first.get("id") or "",
                "artist_name": first.get("name") or (credit[0].get("name") if credit else "") or ""}

    def artist(self, mbid):
        return self._artist(self._client.get_artist(mbid))

    def search_artists(self, name, limit=10):
        return [a for a in (self._artist(r) for r in self._client.search_artist(name, limit=limit, strict=False)) if a]

    def release_group(self, mbid):
        return self._release_group(self._client.get_release_group(mbid, includes=["artist-credits"]))

    def release_groups(self, artist_mbid, cap=500):
        out, offset = [], 0
        while len(out) < cap:
            page = self._client.browse_artist_release_groups(artist_mbid, release_types=None,
                                                             limit=100, offset=offset)
            if not page:
                break
            out.extend(g for g in (self._release_group(r) for r in page) if g)
            if len(page) < 100:
                break
            offset += 100
        return out[:cap]

    def album(self, mbid):
        """the full album with a tracklist, picked the same way the album modal picks it."""
        search = self._search
        if search is None:
            from core.metadata.registry import get_musicbrainz_client
            search = get_musicbrainz_client()
        return search.get_album(mbid, include_tracks=True)


class LidarrCompat:
    def __init__(self, store, mb, *, queue_album: Callable, ownership: Callable,
                 metadata_profile: Callable[[int], Dict[str, Any]],
                 root_folder: Callable[[], str], commands: Optional[CommandRunner] = None,
                 now=time.time):
        self.store = store
        self.mb = mb
        self._queue_album = queue_album
        self._ownership = ownership
        self._metadata_profile = metadata_profile
        self._root_folder = root_folder
        self.commands = commands or CommandRunner()
        self._now = now
        self._recent = OrderedDict()
        self._recent_lock = threading.Lock()

    # artists

    def artist_json(self, row):
        return resources.artist_resource(row, self.store.albums(artist_id=row["id"]))

    def lookup_artists(self, term) -> List[Dict[str, Any]]:
        parsed = resources.parse_term(term)
        if parsed.get("mbid"):
            known = self.store.artist_by_mbid(parsed["mbid"])
            if known:
                return [self.artist_json(known)]
            found = self.mb.artist(parsed["mbid"])
            return [resources.artist_resource(found)] if found else []
        if not parsed.get("name"):
            return []
        out = []
        for found in self.mb.search_artists(parsed["name"]):
            known = self.store.artist_by_mbid(found["mbid"])
            out.append(self.artist_json(known) if known else resources.artist_resource(found))
        return out

    def get_artist(self, artist_id):
        row = self.store.artist(artist_id)
        if not row:
            raise LidarrError("Artist not found", 404)
        return row

    def add_artist(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """POST /artist. an artist already here comes back as it is, like lidarr's 400 but friendlier."""
        mbid = resources.parse_term(str(payload.get("foreignArtistId") or payload.get("mbId") or "")).get("mbid")
        if not mbid:
            raise LidarrError("foreignArtistId must be a MusicBrainz artist id")
        existing = self.store.artist_by_mbid(mbid)
        if existing:
            return existing, False
        found = self.mb.artist(mbid)
        if not found:
            raise LidarrError("Artist not found on MusicBrainz", 404)
        options = payload.get("addOptions") or {}
        row = self.store.add_artist(
            mbid, name=found["name"] or payload.get("artistName") or "",
            disambiguation=found["disambiguation"], artist_type=found["artist_type"],
            monitored=bool(payload.get("monitored", True)),
            monitor_new_items=str(payload.get("monitorNewItems") or "none"),
            quality_profile_id=int(payload.get("qualityProfileId") or 1),
            metadata_profile_id=int(payload.get("metadataProfileId") or 1),
            root_folder=str(payload.get("rootFolderPath") or self._root_folder() or ""))
        # index right away: clients look for the album a second after adding
        self.refresh_artist(row["id"])
        picked = self._monitor_option(row, str(options.get("monitor") or "none"))
        if picked:
            for album in picked:
                self.store.update_album(album["id"], monitored=True)
            if options.get("searchForMissingAlbums"):
                ids = [a["id"] for a in picked]
                self.commands.submit("AlbumSearch", {"albumIds": ids}, lambda: self.search_albums(ids))
        return self.store.artist(row["id"]), True

    def _monitor_option(self, artist, option) -> List[Dict[str, Any]]:
        """which albums lidarr's addOptions.monitor would turn on."""
        option = option.lower()
        if option in ("none", "future", "unknown", ""):
            return []
        profile = self._metadata_profile(int(artist.get("metadata_profile_id") or 1))
        allowed = [a for a in self.store.albums(artist_id=artist["id"])
                   if resources.album_allowed(profile, a["album_type"], a["secondary_types"])]
        allowed.sort(key=lambda a: a.get("release_date") or "")
        if option == "first":
            return allowed[:1]
        if option == "latest":
            return allowed[-1:]
        return allowed  # all, missing, existing

    def refresh_artist(self, artist_id) -> int:
        """pull the artist's release groups in. never deletes, only adds."""
        artist = self.get_artist(artist_id)
        groups = list(self.mb.release_groups(artist["mbid"]))
        # the browse stops at a cap and big artists have thousands of
        # bootleg groups. clients look the album up first, so take that one too
        with self._recent_lock:
            groups += [g for g in self._recent.values() if g.get("artist_mbid") == artist["mbid"]]
        added = 0
        for group in groups:
            if self.store.album_by_mbid(group["mbid"]):
                continue
            self.store.add_album(group["mbid"], artist["id"], title=group["title"],
                                 disambiguation=group["disambiguation"], album_type=group["album_type"],
                                 secondary_types=group["secondary_types"],
                                 release_date=group["release_date"], monitored=False)
            added += 1
        self.store.update_artist(artist["id"], refreshed_at=self._now())
        return added

    def update_artist(self, artist_id, payload):
        self.get_artist(artist_id)
        fields = {}
        if "monitored" in payload:
            fields["monitored"] = bool(payload["monitored"])
        if payload.get("monitorNewItems"):
            fields["monitor_new_items"] = str(payload["monitorNewItems"])
        for key, col in (("qualityProfileId", "quality_profile_id"), ("metadataProfileId", "metadata_profile_id")):
            if payload.get(key):
                fields[col] = int(payload[key])
        if payload.get("rootFolderPath"):
            fields["root_folder"] = str(payload["rootFolderPath"])
        return self.store.update_artist(artist_id, **fields)

    def edit_artists(self, payload):
        """PUT /artist/editor: the same change on several artists."""
        out = []
        for aid in payload.get("artistIds") or []:
            if self.store.artist(aid):
                out.append(self.update_artist(aid, payload))
        return out

    def delete_artist(self, artist_id):
        """forgets the artist. files are never touched, whatever deleteFiles says."""
        self.get_artist(artist_id)
        self.store.delete_artist(artist_id)

    # albums

    def album_json(self, row, artist=None):
        artist = artist or self.store.artist(row["artist_id"]) or {"id": row["artist_id"]}
        return resources.album_resource(row, artist)

    def lookup_albums(self, term) -> List[Dict[str, Any]]:
        parsed = resources.parse_term(term)
        if not parsed.get("mbid"):
            # lidarr's name search needs its own metadata server. every client
            # we know of looks albums up by id
            return []
        known = self.store.album_by_mbid(parsed["mbid"])
        if known:
            return [self.album_json(known)]
        group = self.mb.release_group(parsed["mbid"])
        if not group:
            return []
        with self._recent_lock:
            self._recent[group["mbid"]] = group
            while len(self._recent) > RECENT_LOOKUPS:
                self._recent.popitem(last=False)
        artist = self.store.artist_by_mbid(group["artist_mbid"]) or {
            "mbid": group["artist_mbid"], "name": group["artist_name"]}
        return [resources.album_resource(dict(group, monitored=False), artist)]

    def get_album(self, album_id):
        row = self.store.album(album_id)
        if not row:
            raise LidarrError("Album not found", 404)
        return row

    def list_albums(self, *, artist_id=None, mbid=None, ids=None) -> List[Dict[str, Any]]:
        rows = self.store.albums(artist_id=artist_id, mbid=mbid, ids=ids)
        budget = [STATS_REFRESH_PER_CALL]
        artists = {}
        out = []
        for row in rows:
            artist = artists.get(row["artist_id"])
            if artist is None:
                artist = artists[row["artist_id"]] = self.store.artist(row["artist_id"]) or {"id": row["artist_id"]}
            out.append(self.album_json(self.refresh_stats(row, artist, budget=budget), artist))
        return out

    def refresh_stats(self, row, artist, *, force=False, budget=None):
        """re-check what we own of an album someone asked for. cheap when fresh."""
        if not (row.get("monitored") or row.get("searched_at")):
            return row
        fresh = row.get("checked_at") and self._now() - float(row["checked_at"]) < STATS_TTL_SECONDS
        if fresh and not force:
            return row
        if budget is not None:
            if budget[0] <= 0:
                return row
            budget[0] -= 1
        try:
            owned, total = self._ownership(row["title"], artist.get("name") or "", row.get("track_count"))
        except Exception as e:  # noqa: BLE001 - a status check never fails the list
            logger.debug("lidarr ownership check failed for %s: %s", row.get("title"), e)
            return row
        fields = {"owned_count": int(owned or 0), "checked_at": self._now()}
        if not row.get("track_count") and total:
            fields["track_count"] = int(total)
        return self.store.update_album(row["id"], **fields) or row

    def _resolve_album_artist(self, payload, group):
        artist_id = payload.get("artistId")
        if artist_id and self.store.artist(artist_id):
            return self.store.artist(artist_id)
        nested = payload.get("artist") or {}
        mbid = nested.get("foreignArtistId") or nested.get("mbId") or (group or {}).get("artist_mbid")
        if not mbid:
            raise LidarrError("album has no artist")
        row = self.store.artist_by_mbid(mbid)
        if row:
            return row
        # adding an album for a new artist adds the artist, quietly, like lidarr
        row, _ = self.add_artist({"foreignArtistId": mbid, "monitored": True,
                                  "qualityProfileId": nested.get("qualityProfileId") or 1,
                                  "metadataProfileId": nested.get("metadataProfileId") or 1,
                                  "rootFolderPath": nested.get("rootFolderPath") or "",
                                  "addOptions": {"monitor": "none"}})
        return row

    def add_album(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /album. an album already here is updated and returned, not refused."""
        mbid = resources.parse_term(str(payload.get("foreignAlbumId") or "")).get("mbid")
        if not mbid:
            raise LidarrError("foreignAlbumId must be a MusicBrainz release group id")
        row = self.store.album_by_mbid(mbid)
        if not row:
            group = self.mb.release_group(mbid)
            if not group:
                raise LidarrError("Album not found on MusicBrainz", 404)
            artist = self._resolve_album_artist(payload, group)
            row = self.store.album_by_mbid(mbid) or self.store.add_album(
                mbid, artist["id"], title=group["title"], disambiguation=group["disambiguation"],
                album_type=group["album_type"], secondary_types=group["secondary_types"],
                release_date=group["release_date"], monitored=False)
        monitored = bool(payload.get("monitored", True))
        row = self.store.update_album(row["id"], monitored=monitored,
                                      any_release_ok=bool(payload.get("anyReleaseOk", True)))
        options = payload.get("addOptions") or {}
        if monitored and options.get("searchForNewAlbum"):
            ids = [row["id"]]
            self.commands.submit("AlbumSearch", {"albumIds": ids}, lambda: self.search_albums(ids))
        return row

    def update_album(self, album_id, payload):
        self.get_album(album_id)
        fields = {}
        if "monitored" in payload:
            fields["monitored"] = bool(payload["monitored"])
        if "anyReleaseOk" in payload:
            fields["any_release_ok"] = bool(payload["anyReleaseOk"])
        return self.store.update_album(album_id, **fields)

    def set_monitored(self, album_ids: Iterable[int], monitored: bool) -> List[Dict[str, Any]]:
        out = []
        for aid in album_ids or []:
            if self.store.album(aid):
                out.append(self.store.update_album(aid, monitored=bool(monitored)))
        return out

    def delete_album(self, album_id):
        self.get_album(album_id)
        self.store.delete_album(album_id)

    # searching

    def search_albums(self, album_ids: Iterable[int]) -> str:
        """AlbumSearch: put each album's tracks on the wishlist."""
        queued = 0
        for aid in album_ids or []:
            row = self.store.album(aid)
            if not row:
                continue
            artist = self.store.artist(row["artist_id"]) or {"id": row["artist_id"], "name": ""}
            queued += self._search_one(row, artist)
        return f"{queued} tracks queued"

    def _search_one(self, row, artist) -> int:
        album = self.mb.album(row["mbid"])
        tracks = (album or {}).get("tracks") or []
        if isinstance(tracks, dict):
            tracks = tracks.get("items") or []
        if not tracks:
            logger.warning("lidarr search: no tracklist on musicbrainz for %s (%s)", row["title"], row["mbid"])
            return 0
        album = dict(album, tracks=tracks)
        queued = int(self._queue_album(album, artist) or 0)
        self.store.update_album(row["id"], searched_at=self._now(), track_count=len(tracks),
                                queued_count=queued, checked_at=None)
        logger.info("lidarr search: %s by %s, %d of %d tracks queued", row["title"],
                    artist.get("name"), queued, len(tracks))
        return queued

    def search_artist(self, artist_id) -> str:
        """ArtistSearch: every monitored album of the artist."""
        ids = [a["id"] for a in self.store.albums(artist_id=artist_id) if a["monitored"]]
        return self.search_albums(ids)

    def run_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "")
        key = name.lower()
        if key == "albumsearch":
            ids = [int(i) for i in payload.get("albumIds") or []]
            work = lambda: self.search_albums(ids)  # noqa: E731
        elif key == "artistsearch":
            aid = payload.get("artistId")
            work = (lambda: self.search_artist(aid)) if aid else None
        elif key == "refreshartist":
            ids = payload.get("artistIds") or ([payload["artistId"]] if payload.get("artistId") else
                                               [a["id"] for a in self.store.artists()])
            work = lambda: sum(self.refresh_artist(i) for i in ids if self.store.artist(i))  # noqa: E731
        else:
            work = None  # rescans, rss sync and the rest: nothing to do here
        return self.commands.submit(name, payload, work)
