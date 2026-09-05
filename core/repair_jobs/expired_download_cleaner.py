"""Expired Download Cleaner (Boulder) — retention-based cleanup of
origin-tracked downloads.

Watchlist- and playlist-sourced downloads (recorded by the Download Origins
provenance) get a per-origin retention window. Past it, a download is proposed
for deletion UNLESS it's still in an actively-mirrored playlist / watched
artist, or you've played it more than once. By default it creates findings to
review; flip ``auto_delete`` to true for hands-off cleanup.

The expiry decision is the pure core in core.library.expired_cleanup; this job
gathers the facts (play_count via DB, active-mirror/watch protection) and
deletes via the shared helper the Download Origins delete also conceptually
uses (resolve path → remove file → drop track row → drop history row).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from core.library.expired_cleanup import (
    RETENTION_OPTIONS,
    is_curated,
    parse_ts,
    path_suffix_key,
    select_expired,
)
from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.expired_download_cleaner")


def _deletion_rationale(entry, min_plays, curated_keys) -> str:
    """Why this download is being proposed for deletion, in the user's terms.

    A finding that says "past retention, not active, not replayed" when the job
    also checked curation and the rebuild stamp is telling the user something
    untrue about a decision to delete their file. This states what was actually
    examined, including whether curation was consulted at all — "nobody
    favourited it" and "we did not look" are very different claims.
    """
    plays = entry.get('play_count')
    bits = ['past retention', 'not in an active playlist or watchlist']
    if plays is None:
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
                db.delete_track_by_file_path(raw_path)
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
        'It only touches downloads recorded from the Download Origins feature '
        'forward — never your pre-existing or manually-added library, and never '
        'anything downloaded before your library was last rebuilt.\n\n'
        'Dry run is ON by default: it only creates findings for you to review '
        'and delete — nothing is deleted automatically. Turn Dry run OFF for '
        'hands-off auto-cleanup.\n\n'
        'Settings:\n'
        '- Watchlist retention / Playlist retention: off, or a window\n'
        '- Keep if played at least: play count that protects a track (default 2)\n'
        '- Use curation signals: keep anything favourited/rated/in a playlist\n'
        '- Curation min rating: stars that count as "keep this" (default 3)\n'
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
    }
    setting_options = {
        'watchlist_retention': RETENTION_OPTIONS,
        'playlist_retention': RETENTION_OPTIONS,
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

    def _get_settings(self, context: JobContext) -> dict:
        merged = dict(self.default_settings)
        if context.config_manager:
            cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) or {}
            merged.update(cfg)
        return merged

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        wl = (settings.get('watchlist_retention') or 'off')
        pl = (settings.get('playlist_retention') or 'off')
        if wl == 'off' and pl == 'off':
            return result  # nothing configured — no-op
        try:
            min_plays = int(settings.get('keep_if_played_at_least', 2))
        except (TypeError, ValueError):
            min_plays = 2
        dry_run = bool(settings.get('dry_run', True))

        candidates = context.db.get_origin_cleanup_candidates()
        if not candidates:
            return result

        # Build the "protected" set: still-mirrored playlists + still-watched
        # artists (by name — what origin_context stores). Case-folded.
        mirrored_names, watched_names = set(), set()
        try:
            for p in (context.db.get_mirrored_playlists() or []):
                n = (p.get('name') if isinstance(p, dict) else None) or ''
                if n:
                    mirrored_names.add(n.strip().casefold())
        except Exception as e:
            logger.debug("expired cleanup: mirrored-playlist lookup failed: %s", e)
        try:
            for a in (context.db.get_watchlist_artists() or []):
                n = getattr(a, 'artist_name', None) or ''
                if n:
                    watched_names.add(n.strip().casefold())
        except Exception as e:
            logger.debug("expired cleanup: watchlist lookup failed: %s", e)

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
            c['protected'] = bool(
                (origin == 'playlist' and ctx and ctx in mirrored_names) or
                (origin == 'watchlist' and ctx and ctx in watched_names))
            if signals_stale:
                c['curated'] = True
            elif curated_keys is not None:
                c['curated'] = path_suffix_key(c.get('file_path')) in curated_keys
            if rebuilt_at is not None:
                created = parse_ts(c.get('created_at'))
                # An unparseable date is already kept by the pure core; treat
                # it as grandfathered too so the reason reported is honest.
                c['grandfathered'] = created is None or created <= rebuilt_at

        expired = select_expired(candidates, watchlist_retention=wl,
                                 playlist_retention=pl, min_plays=min_plays)
        result.scanned = len(candidates)
        if context.update_progress:
            context.update_progress(0, len(expired))

        for i, entry in enumerate(expired):
            if context.check_stop():
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
                        entity_type='track',
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
                                     f'{_deletion_rationale(entry, min_plays, curated_keys)}.'),
                        details={
                            'history_id': entry.get('id'),
                            'file_path': entry.get('file_path'),
                            'title': entry.get('title'),
                            'artist': entry.get('artist_name'),
                            'origin': entry.get('origin'),
                            'origin_context': entry.get('origin_context'),
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
