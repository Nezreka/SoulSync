"""Complete owned albums through native monitoring and the existing Wishlist."""

import re
from contextlib import closing

from core.repair_jobs import register_job
from core.repair_jobs.base import (
    JobResult, RepairJob, artist_scoped_subjects,
    hand_tagged_path_keys, is_hand_tagged_path,
)
from utils.logging_config import get_logger

logger = get_logger('repair.album_completeness')


@register_job
class AlbumCompletenessJob(RepairJob):
    job_id = 'album_completeness'
    display_name = 'Album Completeness'
    description = 'Monitors missing tracks in albums you already collect.'
    help_text = (
        'Opt-in: checks albums with files in the job\'s library/artist scope, '
        'loads their tracklists with the same catalogue loader as the album page, '
        'and monitors only their missing tracks. Wanted, Wishlist and the existing '
        'automatic search handle acquisition with the effective quality profile. '
        'No separate download batches, replacement downloads or edition changes. '
        'Browsing-only releases and hand-tagged albums are left alone. '
        'Dry Run loads metadata but does not change monitoring. '
        'Min Tracks For Check, Min Completion % and Min Owned Tracks can narrow '
        'the selection. Min Owned Tracks defaults to 3: one or two playlist grabs '
        'are not an album you are collecting (#1572); albums by monitored artists '
        'are always checked.\n\n'
        'Review (the default) files one finding per incomplete album and monitors '
        'its missing tracks when you approve it. Automatic monitors them during '
        'the scan. The Complete Incomplete Albums automation and a single-album '
        'run always act directly.'
    )
    icon = 'repair-icon-completeness'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {
        'min_tracks_for_check': 0, 'min_completion_pct': 0,
        # one or two playlist grabs are not an album the user is collecting (#1572)
        'min_owned_tracks': 3, 'dry_run': False,
        # dev filed incomplete albums for review; acting during the scan is opt-in
        'mode': 'review',
    }
    setting_options = {'mode': ['review', 'automatic']}
    auto_fix = True
    supports_artist_scope = True
    supports_file_scope = True

    def scan(self, context):
        from core.library2.artist_aliases import resolve_alias_group
        from core.library2.completeness import load_album_catalogue
        from core.library2.editions import default_edition_id
        from core.library2.maintenance_subjects import active_file_subjects
        from core.library2.monitor_rules import PROVENANCE_USER, record_rules
        from core.library2.mirror_outbox import drain, enqueue_projected_tracks
        from core.library2.wanted import recompute_wanted

        result = JobResult()
        settings = dict(self.default_settings)
        if context.config_manager:
            raw = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
            if isinstance(raw, dict):
                settings.update(raw)
        scope = context.scope or {}
        target = str(scope.get('target_album_id') or '').removeprefix('lib2:')
        max_albums = scope.get('max_albums')
        # an automation or a one-album run is the user asking for this album
        # now; a scheduled sweep files findings unless set to automatic
        review = (not target and max_albums is None
                  and str(settings.get('mode') or 'review').strip().lower() != 'automatic')
        keys = hand_tagged_path_keys(context.db)
        subjects = active_file_subjects(context.db, context.config_manager, include_missing=True)
        selected = artist_scoped_subjects(context, subjects)
        albums = sorted({(int(s['album_id']), s.get('owner_profile_id')) for s in selected},
                        key=lambda item: (item[0], item[1] or 0))
        protected = {(int(s['album_id']), s.get('owner_profile_id')) for s in subjects
                     if is_hand_tagged_path(s.get('path'), keys)}
        loaded = set()
        completed = 0
        for album_id, owner in albums:
            if context.check_stop() or context.wait_if_paused():
                break
            if target and str(album_id) != target:
                continue
            if (album_id, owner) in protected:
                result.skipped += 1
                continue
            if max_albums is not None and completed >= int(max_albums):
                break
            profile_id = int(owner) if owner is not None else 1
            try:
                with closing(context.db._get_connection()) as conn:
                    if album_id not in loaded:
                        load_album_catalogue(context.db, context.config_manager, conn, album_id,
                                             inherit_monitoring=False)
                        loaded.add(album_id)
                    edition_id = default_edition_id(conn, album_id)
                    # upstream b12666102/bfe5cd21c: a track credited only to other
                    # artists (a compilation, a soundtrack) is filled only when this
                    # album was asked for; a guest on the artist's own track is fine,
                    # and so is a credit to one of the album artist's aliases
                    primary = conn.execute('SELECT primary_artist_id FROM lib2_albums WHERE id=?',
                                           (album_id,)).fetchone()[0]
                    own = resolve_alias_group(conn, primary) if primary is not None else []
                    tracks = conn.execute(
                        'SELECT t.id,t.title,'
                        'EXISTS(SELECT 1 FROM lib2_track_artists ta WHERE ta.track_id=t.id) AND NOT EXISTS('
                        'SELECT 1 FROM lib2_track_artists ta WHERE ta.track_id=t.id '
                        f"AND ta.artist_id IN ({','.join('?' * len(own)) or 'NULL'})) AS other_artist,"
                        'EXISTS(SELECT 1 FROM lib2_track_files f '
                        'WHERE f.track_id=t.id AND f.owner_profile_id IS ? '
                        "AND COALESCE(f.file_state,'active') NOT IN ('missing_confirmed','deleted') "
                        "AND COALESCE(f.import_status,'imported')='imported' "
                        "AND TRIM(COALESCE(f.path,''))<>'') AS owned "
                        'FROM lib2_tracks t WHERE t.album_id=? AND (? IS NULL OR EXISTS('
                        'SELECT 1 FROM lib2_release_tracks rt WHERE rt.track_id=t.id '
                        'AND rt.release_edition_id=?))',
                        (*own, owner, album_id, edition_id, edition_id),
                    ).fetchall()
                    owned = sum(bool(t['owned']) for t in tracks)
                    watched = bool(primary is not None and conn.execute(
                        'SELECT monitored FROM lib2_artists WHERE id=?', (primary,)).fetchone()[0])
                    if (not tracks or len(tracks) < int(settings['min_tracks_for_check'])
                            or (owned < int(settings['min_owned_tracks']) and not target and not watched)
                            or 100 * owned / len(tracks) < float(settings['min_completion_pct'])):
                        result.skipped += 1
                        continue
                    missing = [int(t['id']) for t in tracks if not t['owned']
                               and (target or not t['other_artist'])
                               and str(t['title'] or '').strip()
                               and not re.fullmatch(r'Track\s+\d+', t['title'], re.I)]
                    existing = {int(r[0]) for r in conn.execute(
                        "SELECT r.entity_id FROM lib2_monitor_rules r JOIN lib2_tracks t ON t.id=r.entity_id WHERE r.entity_type='track' "
                        'AND r.profile_id=? AND r.monitored=1 AND t.album_id=?', (profile_id, album_id))}
                    missing = [tid for tid in missing if tid not in existing]
                    if missing and review and settings.get('dry_run') is not True:
                        if context.create_finding:
                            title = conn.execute('SELECT al.title, ar.name FROM lib2_albums al '
                                                 'LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id '
                                                 'WHERE al.id=?', (album_id,)).fetchone()
                            created = context.create_finding(
                                job_id=self.job_id, finding_type='incomplete_album', severity='info',
                                entity_type='album', entity_id=f'lib2:{album_id}', file_path=None,
                                title=f"Incomplete album: {title[1] or 'Unknown'} - {title[0]} "
                                      f"({owned}/{len(tracks)})",
                                description=f'{len(missing)} missing track(s). Approve to monitor them; '
                                            'Wanted and the Wishlist take it from there.',
                                details={'lib2_album_id': album_id, 'track_ids': missing,
                                         'profile_id': profile_id, 'owned': owned,
                                         'total': len(tracks)})
                            if created:
                                result.findings_created += 1
                            else:
                                result.findings_skipped_dedup += 1
                        completed += 1
                    elif missing and settings.get('dry_run') is not True:
                        # This job is an explicit opt-in to collect missing siblings.
                        # The common projection preserves profiles, pins and ignore rules.
                        record_rules(conn, 'track', missing, True, PROVENANCE_USER,
                                     profile_id=profile_id)
                        if profile_id == 1:
                            conn.executemany('UPDATE lib2_tracks SET monitored=1 WHERE id=?',
                                             [(tid,) for tid in missing])
                        recompute_wanted(conn, profile_id=profile_id, track_ids=missing)
                        enqueue_projected_tracks(conn, missing, profile_id=profile_id)
                        conn.commit()
                        result.auto_fixed += len(missing)
                        completed += 1
                        if context.report_change:
                            context.report_change(
                                entity_type='album', entity_id=f'lib2:{album_id}',
                                action='missing_tracks_monitored',
                                details={'lib2_album_id': album_id, 'track_ids': missing,
                                         'profile_id': profile_id}, result={'success': True})
                    else:
                        result.skipped += 1
            except Exception as exc:
                logger.warning('Album completeness failed for %s in profile %s: %s',
                               album_id, profile_id, exc)
                result.errors += 1
            result.scanned += 1
            if context.update_progress:
                context.update_progress(result.scanned, len(albums))
        if result.auto_fixed:
            # Replay the same transactional Wishlist intents as the Monitor
            # button. Failed operations stay queued for normal reconciliation.
            drain(context.db)
        return result
