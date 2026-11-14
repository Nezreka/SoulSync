"""Artist Merge Review (#2073).

The duplicate repair merges artist rows by itself, at startup and after an
import. Until now nobody was shown what it merged. This job is the place to
look, and it files three kinds of finding:

- **Merged automatically.** One per merge in the journal: who was folded into
  whom, why, and how many albums moved. Approving it UNDOES the merge;
  dismissing it says the merge was right.
- **Same artist?** A pair the repair would not merge by itself: they share one
  catalogue id and nothing else says they are one artist. Approving it merges
  them; dismissing it leaves them as two.
- **Merged by mistake.** For merges made before the journal existed. An artist
  with albums filed under its own name and other albums filed, and tagged,
  under another name is two artists. Approving it moves those albums to an
  artist of that name.

It reads and reports. Nothing changes until a finding is approved.
"""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.artist_merge_review")

SHOWN = 4


def _listed(titles, shown: int = SHOWN) -> str:
    titles = [str(t) for t in titles if t]
    more = len(titles) - shown
    return ', '.join(titles[:shown]) + (f' and {more} more' if more > 0 else '')


def _album_titles(conn, artist_id, limit: int = 12):
    return [row[0] for row in conn.execute(
        "SELECT title FROM lib2_albums WHERE primary_artist_id=? ORDER BY title LIMIT ?",
        (int(artist_id), limit))]


@register_job
class ArtistMergeReviewJob(RepairJob):
    job_id = 'artist_merge_review'
    display_name = 'Artist Merge Review'
    description = 'Shows every artist merge SoulSync made so it can be undone, asks about doubtful pairs, and finds artists merged by mistake'
    help_text = (
        'SoulSync merges duplicate artist rows by itself. This is where you see what it '
        'merged and put right anything it got wrong.\n\n'
        '- Merged automatically: each merge it made, with the reason and how many albums '
        'moved. Approve to undo it; dismiss if the merge was right.\n'
        '- Same artist?: two artists that share one catalogue id and nothing else. '
        'SoulSync will not merge those by itself. Approve to merge them; dismiss to keep '
        'them as two.\n'
        '- Merged by mistake: an artist whose albums sit, and are tagged, under two '
        'different names. Approve to move those albums to an artist of that name. This '
        'is how a wrong merge from before this job existed is repaired.\n\n'
        'An undone merge or a split is remembered: the two artists are never merged again. '
        'Nothing on disk is touched and no tag is written.'
    )
    icon = 'repair-icon-consistency'
    default_enabled = True
    default_interval_hours = 24
    auto_fix = False

    def estimate_scope(self, context: JobContext) -> int:
        try:
            from contextlib import closing
            with closing(context.db._get_connection()) as conn:
                return int(conn.execute("SELECT COUNT(*) FROM lib2_artists").fetchone()[0])
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        from contextlib import closing

        from core.library2 import artist_merges
        result = JobResult()

        def file(**finding) -> None:
            if not context.create_finding:
                return
            if context.create_finding(job_id=self.job_id, severity='info', file_path=None, **finding):
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1

        try:
            with closing(context.db._get_connection()) as conn:
                merges = artist_merges.list_merges(conn)
                doubts = artist_merges.doubts(conn)
                doubt_albums = {
                    artist_id: _album_titles(conn, artist_id)
                    for doubt in doubts for artist_id in (doubt['low_id'], doubt['high_id'])}
                conn.commit()    # the journal tables, made on first look
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artist Merge Review could not read the journal: %s", exc)
            result.errors += 1
            return result

        for merge in merges:
            result.scanned += 1
            albums = int(merge.get('albums_moved') or 0)
            file(finding_type='artist_merge_made', entity_type='artist',
                 entity_id=f"lib2:{merge['duplicate_id']}",
                 title=f"Merged automatically: {merge['duplicate_name']} into {merge['survivor_name']}",
                 description=(
                     f"SoulSync folded \"{merge['duplicate_name']}\" into \"{merge['survivor_name']}\" "
                     f"({merge.get('reason') or 'duplicate'}), moving {albums} album"
                     f"{'' if albums == 1 else 's'}. Approve to undo the merge; dismiss if they are one artist."),
                 details={'merge_id': merge['id'], 'survivor_id': merge['survivor_id'],
                          'survivor_name': merge['survivor_name'],
                          'duplicate_id': merge['duplicate_id'],
                          'duplicate_name': merge['duplicate_name'],
                          'reason': merge.get('reason'), 'albums_moved': albums,
                          'merged_at': str(merge.get('merged_at') or ''),
                          'library_v2_native': True,
                          'library_v2': {'artist_id': merge['survivor_id']}})

        for doubt in doubts:
            if context.check_stop():
                return result
            result.scanned += 1
            low, high = doubt['low_id'], doubt['high_id']
            file(finding_type='artist_merge_doubt', entity_type='artist', entity_id=f"lib2:{high}",
                 title=f"Same artist? {doubt['low_name']} and {doubt['high_name']}",
                 description=(
                     f"Both carry the same {doubt['source']} id and nothing else says they are one "
                     f"artist, so they were left as two. \"{doubt['low_name']}\" has "
                     f"{_listed(doubt_albums.get(low)) or 'no albums'}; \"{doubt['high_name']}\" has "
                     f"{_listed(doubt_albums.get(high)) or 'no albums'}. Approve to merge them."),
                 details={'artist_ids': [low, high], 'names': [doubt['low_name'], doubt['high_name']],
                          'source': doubt['source'], 'value': doubt['value'],
                          'library_v2_native': True, 'library_v2': {'artist_id': low}})

        if context.check_stop():
            return result
        if context.report_progress:
            context.report_progress(phase='Looking for artists merged by mistake',
                                    log_line='Comparing each artist with its folders and tags',
                                    log_type='info')
        try:
            from core.library2.paths import resolve_lib2_path
            from core.tag_writer import read_file_tags

            def album_artist(path):
                try:
                    resolved = resolve_lib2_path(path, config_manager=context.config_manager) or path
                    return (read_file_tags(resolved) or {}).get('album_artist')
                except Exception:  # noqa: BLE001 - an unreadable file confirms nothing
                    return None

            with closing(context.db._get_connection()) as conn:
                suspects = artist_merges.merged_by_mistake(conn, album_artist)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Artist Merge Review could not compare folders: %s", exc, exc_info=True)
            result.errors += 1
            return result

        for suspect in suspects:
            result.scanned += 1
            count = len(suspect['album_ids'])
            file(finding_type='artist_merged_by_mistake', entity_type='artist',
                 entity_id=f"lib2:{suspect['artist_id']}",
                 title=f"Two artists in one: {suspect['artist_name']} and {suspect['other_name']}",
                 description=(
                     f"{count} album{'' if count == 1 else 's'} under \"{suspect['artist_name']}\" "
                     f"{'is' if count == 1 else 'are'} filed and tagged as \"{suspect['other_name']}\": "
                     f"{_listed(suspect['album_titles'])}. Approve to move "
                     f"{'it' if count == 1 else 'them'} to an artist of that name; dismiss if "
                     f"the two names are one artist."),
                 details={'artist_id': suspect['artist_id'], 'artist_name': suspect['artist_name'],
                          'other_name': suspect['other_name'], 'album_ids': suspect['album_ids'],
                          'album_titles': suspect['album_titles'], 'library_v2_native': True,
                          'library_v2': {'artist_id': suspect['artist_id'],
                                         'album_ids': suspect['album_ids']}})
        if not context.check_stop():
            self._close_settled(context, suspects)
        return result

    def _close_settled(self, context: JobContext, suspects) -> int:
        """Close the pending "Two artists in one" findings this pass no longer
        makes: a pair it now reads as one artist written two ways, or albums
        that have since been moved. Only after a pass that ran to its end."""
        import json
        from contextlib import closing

        still = {(f"lib2:{s['artist_id']}", str(s['other_name'])) for s in suspects}
        closed = 0
        try:
            with closing(context.db._get_connection()) as conn:
                rows = conn.execute(
                    "SELECT id, entity_id, details_json FROM repair_findings "
                    "WHERE job_id=? AND finding_type='artist_merged_by_mistake' "
                    "AND status='pending'", (self.job_id,)).fetchall()
                for row in rows:
                    try:
                        other = str((json.loads(row[2] or '{}') or {}).get('other_name') or '')
                    except (TypeError, ValueError):
                        continue
                    if (str(row[1]), other) in still:
                        continue
                    conn.execute(
                        "UPDATE repair_findings SET status='resolved', user_action='obsolete', "
                        "resolved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (row[0],))
                    closed += 1
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - a finding left open is not a failed scan
            logger.debug("Artist Merge Review: settled findings not closed: %s", exc)
        if closed:
            logger.info("Artist Merge Review: %d finding(s) no longer apply and were closed", closed)
        return closed
