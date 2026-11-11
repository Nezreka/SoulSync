"""Expired Download Cleaner (Boulder) — retention-based cleanup of
origin-tracked downloads.

Watchlist- and playlist-sourced downloads (recorded by the Download Origins
provenance) get a per-origin retention window. Past it, a download is proposed
for deletion UNLESS it's still in an actively-mirrored playlist / watched
artist, or you've played it more than once. By default it creates findings to
review; flip ``auto_delete`` to true for hands-off cleanup.

A playlist marked rotating (#2039) skips the retention window: its downloads
go once the track has left the playlist, every other keep rule still applies.

The expiry decision is the pure core in core.library.expired_cleanup; this job
gathers the facts (play_count via DB, active-mirror/watch protection) and
deletes via the shared helper the Download Origins delete also conceptually
uses (resolve path → remove file → drop track row → drop history row).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from core.library.expired_cleanup import (
    RETENTION_OPTIONS,
    ROTATION_GRACE_OPTIONS,
    is_curated,
    is_expired,
    normalize_track_key,
    parse_ts,
    path_suffix_key,
    rotation_grace_passed,
    select_expired,
)
from core.downloads.origin import is_hand_picked
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, stop_or_pause
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.expired_download_cleaner")


# id keys live in the same set as the artist|title keys. a title key always
# has a '|', an id key never does, so the two can't collide.
_ID_KEY = 'id:'


def _mirror_track_ids(track: dict) -> list:
    """every provider id a mirror track is known by: its own source id plus
    the ids discovery matched it to. the download records the MATCHED id, so
    matching on artist|title alone misses a track whose artist the matched
    provider credits differently."""
    ids = []
    sid = str(track.get('source_track_id') or '').strip()
    if sid:
        ids.append(sid)
    raw = track.get('extra_data')
    try:
        extra = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        extra = None
    if isinstance(extra, dict):
        for k in ('matched_data', 'spotify_hint'):
            sub = extra.get(k)
            if isinstance(sub, dict):
                mid = str(sub.get('id') or '').strip()
                if mid:
                    ids.append(mid)
    return ids


def _rotation_keeps_played(settings) -> bool:
    """does a play keep a song that left a rotating playlist? (#2047)"""
    return bool((settings or {}).get('rotating_keep_played', True))


def _deletion_rationale(entry, min_plays, curated_keys, keeps_played=True) -> str:
    """Why this download is being proposed for deletion, in the user's terms.

    A finding that says "past retention, not active, not replayed" when the job
    also checked curation and the rebuild stamp is telling the user something
    untrue about a decision to delete their file. This states what was actually
    examined, including whether curation was consulted at all — "nobody
    favourited it" and "we did not look" are very different claims.
    """
    plays = entry.get('play_count')
    bits = ['left its rotating playlist' if entry.get('rotated_out') else 'past retention',
            'not in an active playlist or watchlist']
    if entry.get('rotated_out') and not keeps_played:
        bits.append('plays do not keep a rotating song')
    elif plays is None:
        bits.append('play count unknown')
    else:
        bits.append(f'played {plays}x (keep at {min_plays})')
    if curated_keys is None:
        bits.append('curation signals not in use')
    else:
        bits.append('not favourited, rated or playlisted by anyone')
    return ', '.join(bits)


def _kept_summary(candidates, expired) -> str:
    """One line saying what was SPARED and why — the reassurance a job that
    deletes files owes the person reading its log."""
    expired_ids = {id(e) for e in expired}
    reasons = {'curated': 0, 'protected': 0, 'grandfathered': 0, 'played': 0}
    for c in candidates:
        if id(c) in expired_ids:
            continue
        if c.get('protected'):
            reasons['protected'] += 1
        elif c.get('curated'):
            reasons['curated'] += 1
        elif c.get('grandfathered'):
            reasons['grandfathered'] += 1
        else:
            reasons['played'] += 1
    parts = [f'{n} {name}' for name, n in reasons.items() if n]
    return ', '.join(parts) if parts else 'none'


def _path_mapping_hint(config_manager) -> str:
    try:
        active = config_manager.get_active_media_server()
    except Exception:
        active = None
    if str(active or '').lower() == 'navidrome':
        return (
            'Navidrome may be reporting virtual paths. Open Profile -> Players -> '
            'SoulSync, enable "Report Real Path", then run a full database refresh.'
        )
    return (
        'Check Settings -> Library -> Music Paths so SoulSync can map media-server '
        'paths to the real files on disk.'
    )


def _should_treat_unresolved_as_mapping_error(raw_path, config_manager) -> bool:
    if not raw_path:
        return False
    try:
        active = config_manager.get_active_media_server()
    except Exception:
        active = None
    if str(active or '').lower() == 'navidrome':
        return True
    return not os.path.isabs(raw_path)


def delete_origin_download(db, entry, config_manager, transfer_folder=None) -> dict:
    """Delete one origin-tracked download: the file on disk (resolved through
    the shared resolver), its catalogue row, and the history entry. A file
    that refuses deletion keeps its history row and reports the error. Returns
    {removed, file_deleted, error, library_v2}.

    The Library-v2 subjects are captured BEFORE the file goes, because the
    catalogue is resolved from the path — once the file is gone there is
    nothing left to resolve. A capture or a sync that fails leaves the history
    row in place so the next run retries the whole deletion rather than
    orphaning a catalogue row nobody will ever revisit.
    """
    raw_path = entry.get('file_path') or ''
    file_deleted = False
    error = None
    sync_result = None
    try:
        from core.library2.maintenance_sync import annotate_finding_details

        sync_details = annotate_finding_details(
            db,
            config_manager,
            entity_type='track',
            entity_id=None,
            file_path=raw_path,
            details={
                'history_id': entry.get('id'),
                'file_path': raw_path,
                'origin': entry.get('origin'),
                'origin_context': entry.get('origin_context'),
            },
        )
    except Exception as e:  # do not delete when V2 subjects cannot be captured
        return {
            'removed': 0,
            'file_deleted': False,
            'error': f'Library-v2 delete preparation failed: {e}',
            'library_v2': None,
        }
    if raw_path:
        resolved = resolve_library_file_path(
            raw_path,
            transfer_folder=transfer_folder,
            config_manager=config_manager,
        )
        if resolved and os.path.isfile(resolved):
            try:
                os.remove(resolved)
                file_deleted = True
            except OSError as e:
                error = str(e)
        elif resolved is None and _should_treat_unresolved_as_mapping_error(raw_path, config_manager):
            error = f'Could not locate file: {raw_path}. {_path_mapping_hint(config_manager)}'
        # File gone or deleted → clean up the catalogue row either way.
        if error is None:
            try:
                # nobody played it, so don't queue it again either
                db.delete_track_by_file_path(raw_path, release_wish=True)
            except Exception as e:
                logger.debug("expired cleanup: track row delete failed: %s", e)
    if error is None:
        try:
            from core.library2.maintenance_sync import sync_repair_change

            sync_result = sync_repair_change(
                db,
                config_manager,
                job_id='expired_download_cleaner',
                finding_type='expired_download',
                action='deleted_file',
                entity_type='track',
                entity_id=None,
                file_path=raw_path,
                details=sync_details,
                result={'library_v2_file_deleted': True},
            )
        except Exception as e:  # preserve history row so the job can retry
            error = f'Library-v2 delete synchronization failed: {e}'
    removed = 0
    if error is None:
        removed = db.delete_library_history_rows([entry['id']])
    return {
        'removed': removed,
        'file_deleted': file_deleted,
        'error': error,
        'library_v2': sync_result,
    }


def _retype_old_findings(db) -> None:
    """review 4 R4-C-4: findings filed before G08-3 kept entity_type 'track'
    for good (the pending refresh never touches it); they name a download"""
    try:
        conn = db._get_connection()
        try:
            conn.execute("UPDATE repair_findings SET entity_type='download' "
                         "WHERE finding_type='expired_download' AND entity_type='track'")
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - the next run retries
        logger.debug("expired-download findings not retyped: %s", exc)


@register_job
class ExpiredDownloadCleanerJob(RepairJob):
    job_id = 'expired_download_cleaner'
    display_name = 'Expired Download Cleaner'
    description = 'Deletes watchlist/playlist downloads past a retention window (keeps active + played ones)'
    help_text = (
        'Cleans up downloads that came in via the watchlist or playlist sync '
        '(tracked by Download Origins) once they pass a retention window you set '
        'per origin.\n\n'
        'A download is only ever proposed for deletion when ALL are true: it is '
        'older than its origin\'s retention, it is NOT still in a playlist you '
        'actively mirror (or an artist you still watch), NOBODY on your media '
        'server has favourited it, rated it, or put it in a playlist, and you '
        'have played it fewer than the keep-threshold (default: played more '
        'than once is kept). '
        'For playlist downloads, "still mirrored" is checked per track against '
        'the playlist\'s current track list — songs rotated out of a refreshed '
        'playlist become eligible once they pass retention, instead of staying '
        'protected forever because the playlist itself still exists. '
        'If a mirrored playlist\'s track list cannot be read (or is empty), '
        'protection falls back to the playlist name, so emptying a playlist '
        'without removing its mirror keeps the old behavior. '
        'It only touches downloads recorded from the Download Origins feature '
        'forward — never your pre-existing or manually-added library, and never '
        'anything downloaded before your library was last rebuilt.\n\n'
        'Rotating playlists: turn on "Rotating" in a playlist\'s Settings and '
        'its downloads are cleaned up once the song leaves the playlist, '
        'without waiting for a retention window. A song that is still in any '
        'other playlist you mirror is kept, and so is anything played, '
        'favourited or rated. Turn "Rotating: keep played songs" off and a '
        'play no longer keeps a rotated song; a favourite, a rating or '
        'another playlist still does.\n\n'
        'Dry run is ON by default: it only creates findings for you to review '
        'and delete — nothing is deleted automatically. Turn Dry run OFF for '
        'hands-off auto-cleanup.\n\n'
        'Settings:\n'
        '- Watchlist retention / Playlist retention: off, or a window\n'
        '- Keep if played at least: play count that protects a track (default 2)\n'
        '- Use curation signals: keep anything favourited/rated/in a playlist\n'
        '- Curation min rating: stars that count as "keep this" (default 3)\n'
        '- Rotating: keep played songs: ON (default) keeps a rotated song you '
        'have played; OFF removes it however often it was played\n'
        '- Rotating playlist wait: how long a song must be gone from a rotating '
        'playlist before its download is removed (default 1 day)\n'
        '- Dry run: ON = findings only (default); OFF = delete automatically'
    )
    icon = 'repair-icon-cleanup'
    default_enabled = False
    default_interval_hours = 24
    default_settings = {
        'watchlist_retention': 'off',
        'playlist_retention': 'off',
        'keep_if_played_at_least': 2,
        'dry_run': True,
        # Keep anything a user favourited, rated, or put in a playlist.
        'use_curation_signals': True,
        'curation_min_rating': 3,
        # Older than this and the signals are treated as unknown, which keeps
        # everything. Two days covers a normal sweep cadence plus a server
        # being down overnight.
        'curation_max_age_hours': 48,
        # rotating playlists (#2039): one day covers a sync that came back
        # short, the next sync puts the tracks back before anything is removed
        'rotation_grace': '1d',
        # #2047: off = listening to a rotating playlist's song does not keep it
        'rotating_keep_played': True,
    }
    setting_options = {
        'watchlist_retention': RETENTION_OPTIONS,
        'playlist_retention': RETENTION_OPTIONS,
        'rotation_grace': ROTATION_GRACE_OPTIONS,
        'rotating_keep_played': [True, False],
        'dry_run': [True, False],
        'use_curation_signals': [True, False],
        'curation_min_rating': [1, 2, 3, 4, 5],
    }
    # Has an auto mode (dry_run off → deletes in-scan). auto_fix is a UI/metadata
    # flag only — the worker never auto-applies from it; scan() self-manages the
    # dry_run vs delete decision. Setting True surfaces the Scan → Dry Run /
    # Auto-fix flow badge (without it the job mislabels as "Scan Only").
    auto_fix = True

    def _curation_lookup(self, context: JobContext, settings: dict):
        """``(curated_track_keys, stale)`` from the stored media-server signals.

        Three outcomes, and the difference between them is the whole safety
        contract (L2-001):

        * ``(None, False)`` — curation cannot apply here: the user switched it
          off, the database predates it, or no configured server can report it.
          The job behaves exactly as it did before the feature existed.
        * ``(set(), True)`` — curation applies but we do not have a trustworthy
          picture: never swept, swept incompletely, swept too long ago, or the
          signals could not be read. Nothing is deleted.
        * ``(keys, False)`` — a complete, fresh snapshot. These keys are
          protected; everything else is judged on retention and play count.

        The middle case is the one that matters. A sweep that quietly broke —
        server down, credentials rotated, a failed write for one user — used to
        be indistinguishable from "nobody likes any of these", and deleting on
        that basis is the exact failure this job exists to avoid.
        """
        if not bool(settings.get('use_curation_signals', True)):
            return None, False
        try:
            max_age = float(settings.get('curation_max_age_hours', 48) or 48)
        except (TypeError, ValueError):
            max_age = 48.0

        reader = getattr(context.db, 'get_curation_sync_at', None)
        if not callable(reader):
            # Database predates the feature — no signals to consult, and no
            # claim to make about staleness.
            return None, False

        status, synced_at = None, None
        try:
            status_reader = getattr(context.db, 'get_curation_status', None)
            if callable(status_reader):
                status = status_reader()
            synced_at = parse_ts(reader())
        except Exception as e:
            logger.warning("expired cleanup: curation sync state unreadable (%s) "
                           "— keeping everything this run", e)
            return set(), True

        if status is not None:
            if not status.get('expected_servers') and status.get('complete'):
                # The sweep ran and found no server that can report curation.
                # Nothing to protect, so the job runs on retention + play count
                # exactly as it did before the feature existed.
                return None, False
            if not status.get('complete'):
                logger.warning(
                    "[Expired Cleaner] the last curation sweep was incomplete (%s) "
                    "— keeping everything this run",
                    ", ".join(status.get('failed') or []) or "reason unrecorded")
                return set(), True
            synced_at = parse_ts(status.get('at')) or synced_at

        if synced_at is None:
            # Curation is switched on and no sweep has ever completed, so we
            # have no idea what anyone favourited. That is not the same as
            # "nobody favourited anything", and this job deletes files. The
            # sweep is scheduled by the same settings that enabled this job, so
            # this state resolves itself on the next media-server poll; an
            # install with no media server should switch use_curation_signals
            # off, which restores the pre-feature behaviour immediately.
            logger.warning(
                "[Expired Cleaner] curation signals are enabled but no sweep has "
                "completed yet — keeping everything this run. Switch off "
                "'use_curation_signals' if this install has no media server.")
            return set(), True
        age_hours = (datetime.now(timezone.utc) - synced_at).total_seconds() / 3600.0
        if age_hours > max_age:
            logger.warning("[Expired Cleaner] curation signals are %.1fh old (max %.1fh) "
                           "— keeping everything this run", age_hours, max_age)
            return set(), True

        try:
            grouped = context.db.get_curation_signals_by_track_key() or {}
        except Exception as e:
            logger.warning("expired cleanup: curation signals unreadable (%s) "
                           "— keeping everything this run", e)
            return set(), True

        min_rating = settings.get('curation_min_rating', 3)
        return (
            {key for key, signals in grouped.items()
             if is_curated(signals, min_rating=min_rating)},
            False,
        )

    @staticmethod
    def _mirror_track_keys(context: JobContext, playlist_id, profile_id=None) -> Optional[set]:
        """Normalized track keys for a mirror's CURRENT tracks, or None when
        they cannot be read. None means "membership unknown" — the caller
        keeps name-level protection rather than exposing the download."""
        reader = getattr(context.db, 'get_mirrored_playlist_tracks', None)
        if not callable(reader):
            return None
        try:
            # playlist ids are global, so the unscoped call returns the same
            # rows; the keyword is only passed when a profile was given.
            kwargs = {} if profile_id is None else {'profile_id': int(profile_id)}
            tracks = reader(int(playlist_id), **kwargs) or []
        except Exception as e:
            logger.debug("expired cleanup: mirror track list unreadable for %s: %s",
                         playlist_id, e)
            return None
        keys = set()
        for t in tracks:
            if not isinstance(t, dict):
                continue
            key = normalize_track_key(t.get('artist_name'), t.get('track_name'))
            if key:
                keys.add(key)
            keys.update(_ID_KEY + tid for tid in _mirror_track_ids(t))
        return keys

    @staticmethod
    def _hand_picked_names(context: JobContext) -> Optional[set]:
        """Case-folded names history shows were hand-picked downloads (an album
        picked by hand and retried through the wishlist was recorded under its
        own name as a "playlist", #1932), or None when that can't be read.

        Those are kept. Every other download of a playlist that is no longer
        mirrored ages out, as in 3.6.1. This used to ask the other way round
        ("only if a sync ran under that name"), and sync history keeps 100
        rows, so every playlist synced long enough ago was kept forever.
        """
        try:
            with context.db._get_connection() as conn:
                rows = conn.execute(
                    "SELECT playlist_id, playlist_name, is_album_download FROM sync_history"
                ).fetchall()
        except Exception as e:
            logger.debug("expired cleanup: sync history unreadable: %s", e)
            return None
        return {
            str(name).strip().casefold()
            for pid, name, album in rows
            if name and is_hand_picked(pid, name, album)
        }

    def _protection_facts(self, context: JobContext):
        """(playlist_membership, watched artist names).

        ``playlist_membership`` maps every case-folded playlist name that
        keeps downloads to ``{'unreadable': bool, 'keys': set(), 'rotating':
        bool}``: the normalized track keys of the mirror's CURRENT track list.
        ``rotating`` is true only when EVERY mirror under that name is marked
        rotating (#2039), so one profile's toggle can't clean up under another
        profile's same-named playlist. request lists never rotate: they drop a
        track as soon as it downloads.
        ``unreadable`` means the membership could not be verified (no track
        reader, no usable id, a failed read, an empty track list, or a server
        sync name with no mirror row behind it) — those names keep the old
        name-level protection.

        every profile's, not just the admin's: these defaulted to profile 1,
        so another profile's still-mirrored playlist protected nothing and its
        downloads aged out from under it (#1416). a download records the name
        the sync ran under, which is the upstream name, the user's rename, or
        a collision-suffixed sync name, so all three count.

        membership is per TRACK, not per playlist: a rotating discovery
        playlist (Listening Mix and friends) is always still mirrored, so
        name-level protection kept every download it ever produced forever and
        the cleaner could never fire for exactly the installs it was built
        for. ``mirrored_playlist_tracks`` is replaced on every sync, so it is
        the current membership.
        """
        db = context.db
        try:
            pids = [p.get('id') for p in (db.get_all_profiles() or []) if p.get('id') is not None]
        except Exception as e:
            logger.debug("expired cleanup: profile list failed: %s", e)
            pids = []
        pids = pids or [1]

        membership = {}
        watched = set()

        def _entry(name):
            n = str(name or '').strip().casefold()
            if not n:
                return None
            return membership.setdefault(
                n, {'unreadable': False, 'keys': set(), 'rotating': None})

        def _add(target, name):
            n = str(name or '').strip().casefold()
            if n:
                target.add(n)

        # server-side sync name per mirror id (a download records the name
        # the sync ran under: upstream name, user rename, or collision-
        # suffixed sync name). resolved once — mirror ids are global.
        sync_names = {}
        try:
            from core.playlists.sync_names import all_sync_names
            server = context.config_manager.get_active_media_server() if context.config_manager else None
            sync_names = all_sync_names(db, server) or {}
        except Exception as e:
            logger.debug("expired cleanup: sync names unavailable: %s", e)

        seen_mirror_ids = set()
        for pid in pids:
            try:
                mirrors = db.get_mirrored_playlists(profile_id=pid) or []
            except Exception as e:
                logger.debug("expired cleanup: mirrored lookup failed for %s: %s", pid, e)
                continue
            for p in mirrors:
                if not isinstance(p, dict):
                    continue
                mid = p.get('id')
                seen_mirror_ids.add(mid)
                keys = self._mirror_track_keys(context, mid, profile_id=pid)
                names = [p.get('name'), p.get('custom_name')]
                sname = sync_names.get(mid)
                if sname:
                    names.append(sname)
                rotating = bool(p.get('rotating')) and not p.get('is_request_list')
                for name in names:
                    entry = _entry(name)
                    if entry is None:
                        continue
                    entry['rotating'] = rotating and entry['rotating'] is not False
                    if not keys:
                        # None (unreadable) or empty (no verifiable membership):
                        # latch name-level protection. an empty track list is
                        # ambiguous — emptied playlist vs a sync that never
                        # populated it — and this job deletes files, so the
                        # ambiguous case keeps the old behavior (#1416).
                        entry['unreadable'] = True
                    else:
                        entry['keys'] |= keys
            try:
                for a in (db.get_watchlist_artists(profile_id=pid) or []):
                    _add(watched, getattr(a, 'artist_name', None))
            except Exception as e:
                logger.debug("expired cleanup: watchlist lookup failed for %s: %s", pid, e)
        for mid, sname in sync_names.items():
            if mid in seen_mirror_ids:
                continue
            # a server sync name with no mirror row read behind it: no
            # membership to check, so it keeps name-level protection.
            entry = _entry(sname)
            if entry is not None:
                if entry['keys'] and not entry['unreadable']:
                    # a stale sync name colliding with a readable mirror's
                    # local name: the name-level latch wins (fail safe), which
                    # silently neuters per-track protection for that playlist.
                    logger.debug("expired cleanup: sync name %r latches name-level "
                                 "protection over a readable mirror", sname)
                entry['unreadable'] = True
                entry['rotating'] = False
        for entry in membership.values():
            entry['rotating'] = bool(entry['rotating'])
        return membership, watched

    def _annotate(self, context: JobContext, settings: dict, candidates,
                  playlist_membership, watched_names, *,
                  record_exits: bool = True, assume_rotating=(), now=None):
        """Stamp every candidate with the facts the pure core decides on
        (protected, curated, grandfathered, rotated_out). Returns
        ``(curated_keys, signals_stale)``.

        ``record_exits`` False is the read-only preview: nothing is written.
        ``assume_rotating`` (case-folded names) treats those playlists as
        rotating, so the preview can answer before the toggle is on.
        """
        now = now or datetime.now(timezone.utc)
        # a track still in ANY mirror is kept, not only its origin playlist
        # (#2039): the same song in two playlists is one file on disk
        all_keys = set()
        for facts in playlist_membership.values():
            all_keys |= facts['keys']
        left = []
        hand_picked = None
        hand_picked_read = False

        # Anything downloaded before the library database was last rebuilt is
        # permanently out of scope. A rebuild wipes tracks/albums/artists —
        # destroying play_count, the only per-track protection — while
        # library_history keeps the original created_at, so without this a
        # much-played track reads as "old and never played" and gets deleted.
        # No stamp (never rebuilt) means nothing is grandfathered.
        # A database that doesn't track rebuilds at all has nothing to
        # grandfather (normal operation). A read that FAILS is different: we
        # cannot tell whether a rebuild happened, so grandfather everything
        # rather than risk deleting across one we couldn't see.
        rebuilt_at = None
        _read_stamp = getattr(context.db, 'get_preference', None)
        if callable(_read_stamp):
            try:
                rebuilt_at = parse_ts(_read_stamp('library_rebuilt_at'))
            except Exception as e:
                logger.warning(
                    "expired cleanup: could not read the library-rebuild stamp "
                    "(%s) — treating every download as pre-rebuild and keeping it", e)
                rebuilt_at = datetime.now(timezone.utc)

        # Per-user curation signals off the media server: what people
        # deliberately CHOSE about a track. `stale` means we have no fresh
        # picture of those choices, so nothing may be deleted this run — a
        # sweep that silently stopped working must not quietly turn into
        # "nobody likes anything".
        curated_keys, signals_stale = self._curation_lookup(context, settings)

        for c in candidates:
            ctx = (c.get('origin_context') or '').strip().casefold()
            origin = (c.get('origin') or '').strip().lower()
            if origin == 'playlist' and ctx:
                entry = playlist_membership.get(ctx)
                key = normalize_track_key(c.get('artist_name'), c.get('title'))
                tid = str(c.get('source_track_id') or '').strip()
                elsewhere = bool((key and key in all_keys)
                                 or (tid and _ID_KEY + tid in all_keys))
                if entry is None:
                    # origin playlist is not mirrored anywhere: nothing keeps
                    # it, unless history shows the name was a hand-picked
                    # download (unreadable history keeps it, to be safe)
                    if not hand_picked_read:
                        hand_picked = self._hand_picked_names(context)
                        hand_picked_read = True
                    c['protected'] = (hand_picked is None or ctx in hand_picked
                                      or is_hand_picked(None, ctx) or elsewhere)
                else:
                    in_origin = bool(
                        not key  # unidentifiable track — cannot prove it left
                        or entry['unreadable']  # membership unknown — keep old behavior
                        or key in entry['keys']  # still in the playlist's track list
                        # same track by id: the provider can credit the artist
                        # differently ("GTA" vs "Good Times Ahead")
                        or (tid and _ID_KEY + tid in entry['keys'])
                    )
                    c['protected'] = in_origin or elsewhere
                    if not in_origin:
                        c['left_playlist'] = True
                        c['kept_elsewhere'] = elsewhere
                        if entry.get('rotating') or ctx in assume_rotating:
                            left.append(c)
            else:
                c['protected'] = bool(
                    origin == 'watchlist' and ctx and ctx in watched_names)
            if signals_stale:
                c['curated'] = True
            elif curated_keys is not None:
                c['curated'] = path_suffix_key(c.get('file_path')) in curated_keys
            if rebuilt_at is not None:
                created = parse_ts(c.get('created_at'))
                # An unparseable date is already kept by the pure core; treat
                # it as grandfathered too so the reason reported is honest.
                c['grandfathered'] = created is None or created <= rebuilt_at

        self._mark_rotated_out(context, settings, candidates, left,
                               record_exits=record_exits, now=now)
        return curated_keys, signals_stale

    @staticmethod
    def _mark_rotated_out(context: JobContext, settings: dict, candidates, left, *,
                          record_exits: bool, now) -> None:
        """Set ``rotated_out`` on downloads that left a rotating playlist long
        enough ago, and keep the record of when each one left.

        the clock starts the first time a run sees the track gone. if that
        record can't be read or written nothing is marked: not knowing when a
        track left is not the same as it having left long ago.
        """
        reader = getattr(context.db, 'get_rotation_exits', None)
        if not callable(reader):
            return
        try:
            exits = dict(reader() or {})
        except Exception as e:
            logger.warning("expired cleanup: rotation record unreadable (%s) "
                           "— keeping every rotated track this run", e)
            return
        grace = str(settings.get('rotation_grace') or '1d')
        fresh = []
        left_ids = set()
        for c in left:
            cid = c.get('id')
            if cid is None:
                continue
            left_ids.add(cid)
            left_at = exits.get(cid)
            if left_at is None:
                fresh.append(c)
                left_at = now
            c['left_at'] = left_at
            c['rotated_out'] = rotation_grace_passed(left_at, grace, now)
        if not record_exits:
            return
        if fresh:
            try:
                context.db.record_rotation_exits(
                    [(c['id'], c.get('origin_context')) for c in fresh])
            except Exception as e:
                logger.warning("expired cleanup: could not record rotation exits (%s) "
                               "— keeping them this run", e)
                for c in fresh:
                    c['rotated_out'] = False
        # back in the playlist, or the playlist stopped rotating: the clock
        # resets so a later exit waits the full time again
        seen = {c.get('id') for c in candidates}
        back = [hid for hid in exits if hid in seen and hid not in left_ids]
        if back:
            try:
                context.db.clear_rotation_exits(back)
            except Exception as e:
                logger.debug("expired cleanup: rotation exits not cleared: %s", e)

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) or {}
            merged.update(cfg)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        _retype_old_findings(context.db)
        settings = self._get_settings(context)
        wl = (settings.get('watchlist_retention') or 'off')
        pl = (settings.get('playlist_retention') or 'off')
        playlist_membership = watched_names = None
        if wl == 'off' and pl == 'off':
            # nothing on a timer. a rotating playlist (#2039) still needs the run
            playlist_membership, watched_names = self._protection_facts(context)
            if not any(e.get('rotating') for e in playlist_membership.values()):
                return result  # nothing configured — no-op
        try:
            min_plays = int(settings.get('keep_if_played_at_least', 2))
        except (TypeError, ValueError):
            min_plays = 2
        dry_run = bool(settings.get('dry_run', True))

        candidates = context.db.get_origin_cleanup_candidates()
        if not candidates:
            return result

        if playlist_membership is None:
            playlist_membership, watched_names = self._protection_facts(context)
        curated_keys, _stale = self._annotate(
            context, settings, candidates, playlist_membership, watched_names)

        keeps_played = _rotation_keeps_played(settings)
        expired = select_expired(candidates, watchlist_retention=wl,
                                 playlist_retention=pl, min_plays=min_plays,
                                 rotation_keeps_played=keeps_played)
        result.scanned = len(candidates)
        if context.update_progress:
            context.update_progress(0, len(expired))

        for i, entry in enumerate(expired):
            if stop_or_pause(context):
                return result
            if not dry_run:
                try:
                    res = delete_origin_download(
                        context.db, entry, context.config_manager,
                        transfer_folder=context.transfer_folder)
                    # A Library-v2 capture/sync failure is REPORTED, not raised
                    # (the history row is deliberately kept so the next run
                    # retries) — without counting it the run would claim a
                    # clean sweep while nothing was deleted.
                    if res.get('error'):
                        logger.error("expired auto-delete failed for %s: %s",
                                     entry.get('title'), res['error'])
                        result.errors += 1
                    elif res.get('removed') or res.get('file_deleted'):
                        result.auto_fixed += 1
                except Exception as e:
                    logger.error("expired auto-delete failed for %s: %s", entry.get('title'), e)
                    result.errors += 1
            elif context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='expired_download',
                        severity='info',
                        # G08-3: a download-history id, not a track. as
                        # 'track' the annotation read it as a 3.6.1 track id
                        # and named an unrelated song; the path names the file
                        entity_type='download',
                        entity_id=str(entry.get('id')),
                        file_path=entry.get('file_path'),
                        title=f'Expired: {entry.get("title") or "Unknown"}',
                        # Say what was actually CHECKED, not a fixed phrase.
                        # The old wording ("past retention, not active, not
                        # replayed") predates the curation and rebuild checks
                        # and no longer described the real criteria — which
                        # matters on a finding whose whole job is to justify
                        # deleting someone's file.
                        description=(f'"{entry.get("title")}" by {entry.get("artist_name") or "Unknown"} '
                                     f'— via {entry.get("origin")} ({entry.get("origin_context") or "?"}), '
                                     f'{_deletion_rationale(entry, min_plays, curated_keys, keeps_played)}.'),
                        details={
                            'history_id': entry.get('id'),
                            'file_path': entry.get('file_path'),
                            'title': entry.get('title'),
                            'artist': entry.get('artist_name'),
                            'origin': entry.get('origin'),
                            'origin_context': entry.get('origin_context'),
                            'rotated_out': bool(entry.get('rotated_out')),
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("expired finding create failed: %s", e)
                    result.errors += 1
            if context.update_progress and (i + 1) % 5 == 0:
                context.update_progress(i + 1, len(expired))

        # exits whose download is gone (deleted here or from a finding)
        _prune = getattr(context.db, 'clear_rotation_exits', None)
        if callable(_prune):
            try:
                _prune()
            except Exception as e:
                logger.debug("expired cleanup: rotation exits not pruned: %s", e)

        logger.info("[Expired Cleaner] %d candidates, %d expired (%s) — kept: %s",
                    len(candidates), len(expired),
                    "findings created (dry run)" if dry_run else "auto-deleted",
                    _kept_summary(candidates, expired))
        return result

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(context.db.get_origin_cleanup_candidates())
        except Exception:
            return 0


JOB_ID = 'expired_download_cleaner'


def cleaner_state(config_manager) -> dict:
    """whether the cleaner will run at all, read the way the repair worker
    reads it: maintenance defaults on, this job defaults off."""
    state = {'cleaner_enabled': False, 'maintenance_enabled': True,
             'dry_run': True, 'rotation_grace': '1d', 'keep_played': True}
    if config_manager is None:
        return state
    try:
        state['maintenance_enabled'] = bool(config_manager.get('repair.master_enabled', True))
        job = config_manager.get(f'repair.jobs.{JOB_ID}', {})
        job = job if isinstance(job, dict) else {}
        state['cleaner_enabled'] = bool(job.get('enabled', False))
        settings = job.get('settings')
        settings = settings if isinstance(settings, dict) else {}
        state['dry_run'] = bool(settings.get('dry_run', True))
        state['rotation_grace'] = str(settings.get('rotation_grace') or '1d')
        state['keep_played'] = _rotation_keeps_played(settings)
    except Exception as e:
        logger.debug("cleaner state unreadable: %s", e)
    return state


def rotation_preview(db, config_manager, playlist: dict) -> dict:
    """What making this playlist rotating does right now (#2039), read-only.

    ``left`` is its downloads that are no longer in its track list.
    ``removing`` of those pass every keep rule, ``waiting`` of those are still
    inside the wait. the rest are kept, with the reason counted. answers the
    same whether or not the toggle is on yet, so the settings sheet can show
    it before anyone commits.
    """
    job = ExpiredDownloadCleanerJob()
    context = JobContext(db=db, transfer_folder='', config_manager=config_manager)
    settings = job._get_settings(context)
    out = dict(cleaner_state(config_manager))
    out.update({'membership_known': False, 'downloads': 0, 'left': 0,
                'removing': 0, 'waiting': 0, 'kept': 0, 'kept_reasons': {}})

    names = [playlist.get('name'), playlist.get('custom_name')]
    try:
        from core.playlists.sync_names import all_sync_names
        server = config_manager.get_active_media_server() if config_manager else None
        names.append((all_sync_names(db, server) or {}).get(playlist.get('id')))
    except Exception as e:
        logger.debug("rotation preview: sync name unavailable: %s", e)
    wanted = {str(n).strip().casefold() for n in names if n and str(n).strip()}
    if not wanted:
        return out

    membership, watched = job._protection_facts(context)
    facts = [membership[n] for n in wanted if n in membership]
    out['membership_known'] = bool(facts) and not any(f['unreadable'] for f in facts)
    candidates = db.get_origin_cleanup_candidates(contexts=wanted) or []
    candidates = [c for c in candidates
                  if (c.get('origin') or '').strip().lower() == 'playlist']
    out['downloads'] = len(candidates)
    if not candidates:
        return out

    now = datetime.now(timezone.utc)
    _keys, stale = job._annotate(context, settings, candidates, membership, watched,
                                 record_exits=False, assume_rotating=wanted, now=now)
    try:
        min_plays = int(settings.get('keep_if_played_at_least', 2))
    except (TypeError, ValueError):
        min_plays = 2
    reasons = {}
    for c in candidates:
        if not c.get('left_playlist'):
            continue
        out['left'] += 1
        # the wait is the only thing time changes, so ask as if it had passed
        if is_expired(dict(c, rotated_out=True), watchlist_retention='off',
                      playlist_retention='off', min_plays=min_plays, now=now,
                      rotation_keeps_played=_rotation_keeps_played(settings)):
            out['removing'] += 1
            if not c.get('rotated_out'):
                out['waiting'] += 1
            continue
        if c.get('kept_elsewhere'):
            why = 'other_playlist'
        elif c.get('curated'):
            # stale signals keep everything, which is not the same as liked
            why = 'unverified' if stale else 'curated'
        elif c.get('grandfathered'):
            why = 'older'
        elif c.get('play_count') is None:
            why = 'unknown'
        else:
            why = 'played'
        reasons[why] = reasons.get(why, 0) + 1
    out['kept'] = sum(reasons.values())
    out['kept_reasons'] = reasons
    return out


# approving findings one by one (or Fix All) asks the same question for every
# row, and the answer costs a pass over every mirror. one answer serves a burst
_RECHECK_TTL = 60.0
_recheck_lock = threading.Lock()
_recheck = {'at': None, 'db': None, 'ids': frozenset()}


def rotation_still_due(db, config_manager, history_id) -> bool:
    """is this download still due for removal by rotation, right now?

    a finding can sit for days. by the time it is approved the song may be
    back in the playlist, played, favourited or added to another playlist, and
    the finding would delete a file the cleaner itself would now keep. anything
    that can't be answered is a no.
    """
    try:
        wanted = int(history_id)
    except (TypeError, ValueError):
        return False
    with _recheck_lock:
        now = time.monotonic()
        fresh = (_recheck['at'] is not None and _recheck['db'] == id(db)
                 and now - _recheck['at'] < _RECHECK_TTL)
        if not fresh:
            try:
                ids = _rotation_due_ids(db, config_manager)
            except Exception as e:
                logger.warning("expired cleanup: rotation recheck failed (%s) "
                               "— not deleting", e)
                return False
            _recheck.update({'at': now, 'db': id(db), 'ids': ids})
        return wanted in _recheck['ids']


def _rotation_due_ids(db, config_manager) -> frozenset:
    job = ExpiredDownloadCleanerJob()
    context = JobContext(db=db, transfer_folder='', config_manager=config_manager)
    settings = job._get_settings(context)
    candidates = db.get_origin_cleanup_candidates() or []
    if not candidates:
        return frozenset()
    membership, watched = job._protection_facts(context)
    job._annotate(context, settings, candidates, membership, watched, record_exits=False)
    try:
        min_plays = int(settings.get('keep_if_played_at_least', 2))
    except (TypeError, ValueError):
        min_plays = 2
    # retention off on purpose: only what rotation alone would remove
    due = select_expired(candidates, watchlist_retention='off',
                         playlist_retention='off', min_plays=min_plays,
                         rotation_keeps_played=_rotation_keeps_played(settings))
    return frozenset(int(c['id']) for c in due if c.get('id') is not None)
