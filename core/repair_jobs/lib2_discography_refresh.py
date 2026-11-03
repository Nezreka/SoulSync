"""Periodic Library-v2 discography re-expansion (monitor_new_items enforcement,
and 3.6.1's Discography Backfill on the catalogue).

``monitor_new_items`` promises that newly discovered ('all') or genuinely newer
('new') releases of a monitored artist become wanted automatically — but the enforcement lives in the
discography *re-expansion* path, which used to run only when the user pressed
"Update Discography". This job runs that same re-expansion on a schedule for
every monitored artist, so the promise holds without manual clicks — including
an artist that was only ever imported/watchlisted and never had its provider
catalog fetched (review A5): a monitored artist whose first fetch happens to
land on this sweep instead of a manual click is not a special case downstream.
``_expand_artist_discography``'s own ``eligible_reexpansion`` gate (it requires
``had_discography`` — i.e. NOT this artist's first-ever fetch) already keeps a
first expansion from auto-monitoring the whole back catalog, first-fetched-by-
sweep or first-fetched-by-click alike, so this job doesn't need its own
first-expansion guard on top of that.

Scope rules:
- ``artists='watchlist'`` (default): only monitored artists with
  ``monitor_new_items`` != 'none'.
- ``artists='library'``: also every artist the library holds a release of,
  the reach 3.6.1's Discography Backfill had (#977: a release of their own on
  disk, not a guest spot; #1572: ``min_album_tracks_owned`` on one album, a
  watchlist artist always counts). Judged over the §40 alias group, because
  linked rows keep their own albums. Their releases with nothing on disk become
  review findings, filtered by release type and content. A back catalogue is
  never wishlisted by itself: approving the finding does that.
- Newly discovered releases are materialized + mirrored via the same shared
  helper the API endpoint uses (``discography.auto_monitor_releases``), so the
  two paths cannot drift.

Ownership is the catalogue's: a release is yours when a live file of it is in
a library, whichever media server scanned it or none did (#1952). The 3.6.1
job asked the fuzzy matcher scoped to the active server, so an album SoulSync
imported that the server hadn't mapped read as missing and was wished for again.

Scheduled runs have no request context; wishlist mirrors are scoped to the
admin profile (1), matching every other scheduled acquisition path. A back
catalogue finding names the one profile whose library holds the artist
(#1504), and approving it wishes for that profile. No-op when the native
Library-v2 catalogue is off. Never touches files.
"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair.monitored_discography_refresh")

_REACHES = ("watchlist", "library")
_RELEASE_FILTER_KEYS = (
    "include_albums", "include_eps", "include_singles", "include_live",
    "include_remixes", "include_acoustic", "include_compilations",
    "include_instrumentals",
)
_LIVE_FILE = ("f.path IS NOT NULL AND TRIM(f.path) <> '' "
              "AND COALESCE(f.file_state, 'active') = 'active'")


def _release_kind(album_type: Any, track_count: Any) -> str:
    """3.6.1's backfill rule: an explicit album / ep / compilation is trusted,
    anything else goes by track count (1-3 a single, 4-6 an EP, 7+ an album).
    Spotify files EPs and true singles both under 'single'."""
    kind = str(album_type or "").strip().lower()
    if kind in ("album", "ep", "compilation"):
        return kind
    try:
        count = int(track_count or 0)
    except (TypeError, ValueError):
        count = 0
    if count >= 7:
        return "album"
    if count >= 4:
        return "ep"
    if count >= 1 or kind == "single":
        return "single"
    return "album"


def _release_included(album: Dict[str, Any], filters: Dict[str, bool]) -> bool:
    from core.library2.discography import _track_content_excluded
    from core.watchlist_scanner import is_compilation_album

    title = str(album.get("title") or "")
    kind = _release_kind(album.get("album_type"), album.get("track_count"))
    if kind == "compilation" or is_compilation_album(title):
        if not filters.get("include_compilations", False):
            return False
    elif not filters.get({"album": "include_albums", "ep": "include_eps",
                          "single": "include_singles"}[kind], True):
        return False
    return not _track_content_excluded(title, title, filters)


@register_job
class Lib2DiscographyRefreshJob(RepairJob):
    job_id = "monitored_discography_refresh"
    display_name = "Discography Refresh"
    description = ("Re-fetch artists' provider catalogs: new releases go on the Wishlist, "
                   "and with Artists set to library, releases you don't have become findings.")
    help_text = (
        "Re-expands the provider discography of every watchlist "
        "artist, including one never manually expanded before — its first "
        "fetch happening here instead of an 'Update Discography' click "
        "changes nothing (an artist's very first fetch never wishlists "
        "its back catalog either way). Releases discovered since the last "
        "expansion are wishlisted when the artist's new-releases "
        "setting is 'all'; 'new' only accepts a dated release newer than the "
        "previously newest known release. Their tracklist is materialized "
        "and mirrored into the Wishlist, so the normal download pipeline "
        "picks them up. Does nothing when the new library feature is off.\n\n"
        "Artists set to library also walks every artist you have a release of "
        "(the old Discography Backfill): their releases with nothing on disk "
        "show up as findings, and Fix puts that release on the Wishlist. A "
        "release counts as yours as soon as a file of it is in the library, "
        "whether a media server has scanned it or not.\n\n"
        "Settings:\n"
        "- Mode: automatic wishlists new releases, review makes findings first\n"
        "- Artists: watchlist, or every library artist\n"
        "- Max Artists Per Run: 0 checks everyone each run; a limit spreads the "
        "artists over successive runs\n"
        "- Min Album Tracks Owned: a library artist needs this many tracks on one "
        "album, so a few playlist songs don't pull in a whole discography. "
        "Watchlist artists always count\n"
        "- Include ...: release types and content for artists on no watchlist; a "
        "watchlist artist uses its own watchlist settings\n\n"
        "A run that gets stopped (or a restart) picks up at the artist it "
        "stopped on next time, then goes back to the top once it reaches the end."
    )
    icon = "refresh-cw"
    default_enabled = False
    default_interval_hours = 168  # weekly
    # Order matters: the UI renders these in dict-insertion order, and the
    # `_section_` keys are group headers, not settings.
    default_settings = {
        "mode": "automatic",
        "artists": "watchlist",
        "max_artists_per_run": 0,
        "min_album_tracks_owned": 3,
        "_section_release_types": "Release Types",
        "include_albums": True,
        "include_eps": True,
        "include_singles": True,
        "_section_content_filters": "Content Filters",
        "include_live": False,
        "include_remixes": False,
        "include_acoustic": False,
        "include_compilations": False,
        "include_instrumentals": False,
    }
    setting_options = {"mode": ["automatic", "review"], "artists": list(_REACHES)}
    auto_fix = True

    def _settings(self, context: JobContext) -> Dict[str, Any]:
        merged = {k: v for k, v in self.default_settings.items() if not k.startswith("_section_")}
        try:
            saved = context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {}) or {}
        except Exception:  # noqa: BLE001
            saved = {}
        if isinstance(saved, dict):
            merged.update(saved)
        return merged

    def _mode(self, context: JobContext) -> str:
        run_mode = str((context.scope or {}).get("mode", "")).strip().lower()
        if run_mode in {"automatic", "review"}:
            return run_mode
        try:
            settings = context.config_manager.get(
                f"repair.jobs.{self.job_id}.settings", {}) or {}
            mode = str(settings.get("mode", "automatic")).strip().lower()
        except Exception:  # noqa: BLE001
            mode = "automatic"
        return mode if mode in {"automatic", "review"} else "automatic"

    @staticmethod
    def _reach(settings: Dict[str, Any]) -> str:
        reach = str(settings.get("artists") or "watchlist").strip().lower()
        return reach if reach in _REACHES else "watchlist"

    @staticmethod
    def _int_setting(settings: Dict[str, Any], key: str, default: int) -> int:
        try:
            return max(0, int(settings.get(key, default)))
        except (TypeError, ValueError, OverflowError):
            return default

    def _create_review_findings(self, context: JobContext, album_ids: list[int],
                                result: JobResult) -> None:
        if not context.create_finding or not album_ids:
            return
        with closing(context.db._get_connection()) as conn:
            for album_id in album_ids:
                row = conn.execute(
                    """SELECT al.id, al.title, ar.name AS artist_name,
                              al.release_date, al.year
                         FROM lib2_albums al
                    LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                        WHERE al.id=?""",
                    (int(album_id),),
                ).fetchone()
                if not row:
                    continue
                inserted = context.create_finding(
                    job_id=self.job_id,
                    finding_type="missing_discography_release",
                    severity="info",
                    entity_type="album",
                    entity_id=f"lib2:{row['id']}",
                    file_path=None,
                    title=f"New release: {row['title']} — {row['artist_name'] or 'Unknown'}",
                    description=(
                        "This release was discovered by monitor-new-items. "
                        "Approve it to materialize its tracklist and add wanted tracks."
                    ),
                    details={
                        "lib2_album_id": int(row["id"]),
                        "artist_name": row["artist_name"],
                        "release_date": row["release_date"],
                        "year": row["year"],
                    },
                )
                if inserted:
                    result.findings_created += 1

    # -- the back catalogue (artists='library') ---------------------------

    @staticmethod
    def _library_artist_ids(conn, min_album_tracks: int = 0) -> List[int]:
        """Sweep roots the library holds a release of, alias group included.

        §40 links rows that keep their own albums, so the files can sit on the
        alias row while the canonical one is the root. 3.6.1's port only asked
        the canonical row and skipped such an artist entirely.
        """
        from core.library2.sql_util import ANY_OWNER, owned_sql

        owners = {int(r[0]) for r in conn.execute(
            "SELECT DISTINCT COALESCE(m.canonical_artist_id, m.id) FROM lib2_artists m "
            f"WHERE {owned_sql('release_artist', 'm', scope=ANY_OWNER)}")}
        if min_album_tracks > 0:
            # one or two playlist grabs are not "this artist is in my library"
            owners &= {int(r[0]) for r in conn.execute(
                f"""SELECT COALESCE(ar.canonical_artist_id, ar.id)
                      FROM lib2_albums al
                      JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                      JOIN lib2_tracks t ON t.album_id = al.id
                     WHERE EXISTS (SELECT 1 FROM lib2_track_files f
                                    WHERE f.track_id = t.id AND {_LIVE_FILE})
                     GROUP BY al.id
                    HAVING COUNT(DISTINCT t.id) >= ?""", (int(min_album_tracks),))}
        if not owners:
            return []
        return [int(r[0]) for r in conn.execute(
            """SELECT id FROM lib2_artists
                WHERE canonical_artist_id IS NULL
                  AND id IN (SELECT value FROM json_each(?))
                ORDER BY id""", (json.dumps(sorted(owners)),))]

    @staticmethod
    def _artist_owner(conn, group: Iterable[int]) -> Optional[int]:
        """The profile whose library holds the artist's files, when exactly one
        does (#1504). None = the shared library."""
        rows = conn.execute(
            f"""SELECT DISTINCT f.owner_profile_id FROM lib2_albums al
                  JOIN lib2_tracks t ON t.album_id = al.id
                  JOIN lib2_track_files f ON f.track_id = t.id
                 WHERE al.primary_artist_id IN (SELECT value FROM json_each(?))
                   AND {_LIVE_FILE}
                 LIMIT 2""", (json.dumps(list(group)),)).fetchall()
        if len(rows) == 1 and rows[0][0]:
            return int(rows[0][0])
        return None

    def _release_filters(self, conn, artist_id: int, settings: Dict[str, Any],
                         profile_id: int) -> Dict[str, bool]:
        """A watchlist artist keeps its own release settings, the same ones its
        new releases go by. One on no watchlist gets this job's."""
        from core.library2.artist_settings import watching_profile_ids
        from core.library2.discography import _CONTENT_FILTER_DEFAULTS, _artist_content_filters

        if watching_profile_ids(conn, artist_id):
            return _artist_content_filters(conn, artist_id, profile_id)
        filters = dict(_CONTENT_FILTER_DEFAULTS)
        filters.update({key: bool(settings.get(key, filters.get(key, False)))
                        for key in _RELEASE_FILTER_KEYS})
        return filters

    @staticmethod
    def _missing_releases(conn, group: List[int]) -> List[Dict[str, Any]]:
        """The group's releases with nothing on disk that nobody asked about.

        Not on disk in any library, not monitored, no one's rule (a no is a
        user's answer too), nothing wanted, and at least one song that isn't
        on disk under another release either: the single of an album track
        you have is not missing. A release whose tracklist isn't fetched yet
        has no songs to ask, a single of one is checked by its title.
        """
        from core.library2.recording_links import normalize_title, owned_by_recording_sql
        from core.library2.sql_util import ANY_OWNER, owned_sql

        members = json.dumps(group)
        rows = [dict(r) for r in conn.execute(
            f"""SELECT al.id, al.title, al.album_type, al.release_date, al.year,
                       COALESCE(al.expected_track_count, al.track_count) AS track_count,
                       ar.name AS artist_name,
                       (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS fetched
                  FROM lib2_albums al
             LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                 WHERE al.primary_artist_id IN (SELECT value FROM json_each(?))
                   AND COALESCE(al.monitored, 0) = 0
                   AND NOT {owned_sql('album', 'al', scope=ANY_OWNER)}
                   AND NOT EXISTS (SELECT 1 FROM lib2_monitor_rules r
                                    WHERE r.entity_type = 'album' AND r.entity_id = al.id)
                   AND NOT EXISTS (SELECT 1 FROM lib2_tracks t
                                     JOIN lib2_wanted_tracks w ON w.track_id = t.id
                                    WHERE t.album_id = al.id AND w.wanted = 1)
                   AND (NOT EXISTS (SELECT 1 FROM lib2_tracks t WHERE t.album_id = al.id)
                        OR EXISTS (SELECT 1 FROM lib2_tracks t
                                    WHERE t.album_id = al.id
                                      AND NOT {owned_by_recording_sql(conn, 't')}))
                 ORDER BY al.id""", (members,))]
        if not any(not r["fetched"] for r in rows):
            return rows
        owned_titles = {normalize_title(r[0]) for r in conn.execute(
            f"""SELECT DISTINCT t.title FROM lib2_tracks t
                  JOIN lib2_albums al ON al.id = t.album_id
                 WHERE (al.primary_artist_id IN (SELECT value FROM json_each(?))
                        OR EXISTS (SELECT 1 FROM lib2_track_artists ta
                                    WHERE ta.track_id = t.id
                                      AND ta.artist_id IN (SELECT value FROM json_each(?))))
                   AND EXISTS (SELECT 1 FROM lib2_track_files f
                                WHERE f.track_id = t.id AND {_LIVE_FILE})""",
            (members, members))}
        return [r for r in rows
                if r["fetched"]
                or _release_kind(r["album_type"], r["track_count"]) != "single"
                or normalize_title(r["title"]) not in owned_titles]

    def _create_backfill_findings(self, context: JobContext, artist_id: int,
                                  settings: Dict[str, Any], result: JobResult,
                                  *, skip: Iterable[int] = ()) -> None:
        if not context.create_finding:
            return
        from core.library2 import ADMIN_PROFILE_ID
        from core.library2.artist_aliases import resolve_alias_group

        skip = {int(i) for i in skip}
        with closing(context.db._get_connection()) as conn:
            group = [int(i) for i in resolve_alias_group(conn, artist_id)]
            owner = self._artist_owner(conn, group)
            filters = self._release_filters(conn, artist_id, settings, owner or ADMIN_PROFILE_ID)
            releases = [r for r in self._missing_releases(conn, group)
                        if int(r["id"]) not in skip and _release_included(r, filters)]
        for release in releases:
            inserted = context.create_finding(
                job_id=self.job_id,
                finding_type="missing_discography_release",
                severity="info",
                entity_type="album",
                entity_id=f"lib2:{release['id']}",
                file_path=None,
                title=f"Missing release: {release['title']} — {release['artist_name'] or 'Unknown'}",
                description=(
                    "You have other releases by this artist, this one has nothing on disk. "
                    "Approve it to materialize its tracklist and add its tracks to the Wishlist."
                ),
                details={
                    "lib2_album_id": int(release["id"]),
                    "artist_name": release["artist_name"],
                    "release_date": release["release_date"],
                    "year": release["year"],
                    "reason": "back_catalogue",
                    # #1504: approving wishes for the library that holds the artist
                    "owner_profile_id": owner,
                },
            )
            if inserted:
                result.findings_created += 1

    # -- the sweep --------------------------------------------------------

    def _artist_ids(self, conn) -> list:
        # §40: alias-member rows (canonical_artist_id set) are skipped as
        # sweep roots — refresh_artist_discography fans out across the whole
        # alias group when it processes the CANONICAL row, so an alias row
        # would otherwise get its group refreshed again on its own turn too
        # (an N-member group would cost N^2 fetches per sweep instead of N).
        return [r[0] for r in conn.execute(
            """SELECT id FROM lib2_artists
                WHERE monitored = 1
                  AND COALESCE(monitor_new_items, 'all') <> 'none'
                  AND canonical_artist_id IS NULL
                ORDER BY id"""
        )]

    def _sweep_ids(self, conn, settings: Dict[str, Any]) -> list:
        artist_ids = self._artist_ids(conn)
        if self._reach(settings) != "library":
            return artist_ids
        library = self._library_artist_ids(
            conn, self._int_setting(settings, "min_album_tracks_owned", 3))
        return sorted(set(artist_ids) | set(library))

    def estimate_scope(self, context: JobContext) -> int:
        try:
            settings = self._settings(context)
            with closing(context.db._get_connection()) as conn:
                total = len(self._sweep_ids(conn, settings))
            limit = self._int_setting(settings, "max_artists_per_run", 0)
            return min(total, limit) if limit else total
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        from core.library2.discography import refresh_artist_discography

        settings = self._settings(context)
        reach = self._reach(settings)
        with closing(context.db._get_connection()) as conn:
            artist_ids = self._sweep_ids(conn, settings)

        total = len(artist_ids)
        mode = self._mode(context)
        # a checkpoint of one reach means nothing to the other
        scan_key = "" if reach == "watchlist" else reach
        limit = self._int_setting(settings, "max_artists_per_run", 0)
        discovered = 0
        mirrored = 0
        # upstream #1709/873401364: a stopped sweep resumes after the last
        # artist it finished instead of re-fetching the same first artists,
        # and says where it starts
        start = self.resume_index(context, artist_ids, scan_key=scan_key)
        end = min(total, start + limit) if limit else total
        if start:
            logger.info("lib2 discography refresh: resuming at artist %d of %d", start + 1, total)
            if context.report_progress:
                context.report_progress(phase=f"Resuming at artist {start + 1} of {total}",
                                        scanned=start, total=total)
        finished = True
        for index in range(start, end):
            artist_id = artist_ids[index]
            stopping = context.check_stop() or context.wait_if_paused()
            if index > start and (stopping or (index - start) % 10 == 0):
                self.save_checkpoint(context, artist_ids[index - 1], scan_key=scan_key)
            if stopping:
                finished = False
                break
            try:
                stats, artist_mirrored = refresh_artist_discography(
                    context.db,
                    artist_id,
                    context.config_manager,
                    wishlist_profile_id=1,
                    auto_monitor=(mode == "automatic"),
                )
                auto_ids = stats.get("auto_monitor_album_ids") or []
                discovered += len(auto_ids)
                mirrored += artist_mirrored
                if mode == "review":
                    self._create_review_findings(context, auto_ids, result)
                if reach == "library":
                    self._create_backfill_findings(context, artist_id, settings, result,
                                                   skip=auto_ids)
            except Exception as e:  # noqa: BLE001
                logger.debug("discography refresh failed (artist %s): %s", artist_id, e)
                result.errors += 1
            result.scanned += 1
            if context.update_progress:
                context.update_progress(index + 1, total)
            if reach == "library" and index + 1 < end:
                # 3.6.1's pacing between artists: a library-wide walk is
                # hundreds of discography fetches back to back (fe5e0e585)
                pause = getattr(context, "sleep_or_stop", None)
                if callable(pause) and pause(1.0):
                    self.save_checkpoint(context, artist_id, scan_key=scan_key)
                    finished = False
                    break

        if finished:
            # a per-run limit leaves the rest of the pass for the next run
            self.save_checkpoint(context, artist_ids[end - 1] if end < total else None,
                                 scan_key=scan_key)
        result.auto_fixed = discovered if mode == "automatic" else 0
        if total:
            logger.info("lib2 discography refresh (%s, %s): %d artists re-expanded, "
                        "%d new releases discovered (%d tracks mirrored, %d findings)",
                        mode, reach, result.scanned, discovered, mirrored,
                        result.findings_created)
        return result
