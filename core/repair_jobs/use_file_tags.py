"""Use File Tags, library-wide (#2074).

#2070 put "Use file tags" on one album and one artist at a time. This is the
same thing as a job: it reads every album's files, and where the tags say
something the Library does not (a title, a track number, a year, a genre, the
album a track is on) it files one finding for that album. Approving it runs
exactly what the album's own "Use file tags" runs.

Findings and not a silent rewrite, on purpose: it changes what the Library
says about someone's music from whatever is in the files, and a library with
bad tags would be made worse. The findings page can approve them all at once.
"""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob, artist_scoped_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_job.use_file_tags")

#: changes named in a finding's description before "and N more"
SHOWN = 4


def describe_plan(plan: dict) -> list:
    """A plan as short lines a person can read: what would change, in the
    order it matters (the album first, then moves, then track fields)."""
    lines = []
    for album in plan.get("albums") or []:
        if album.get("rename"):
            lines.append(f'album title becomes "{album["rename"]}"')
        for change in album.get("changes") or []:
            lines.append(f'{str(change.get("label") or change.get("field")).lower()} '
                         f'becomes "{_shown(change.get("file"))}"')
    moves = {}
    for track in plan.get("tracks") or []:
        if track.get("move"):
            title = track["move"].get("album_title")
            moves[title] = moves.get(title, 0) + 1
    for title, count in moves.items():
        lines.append(f'{count} track moves to "{title}"' if count == 1
                     else f'{count} tracks move to "{title}"')
    for track in plan.get("tracks") or []:
        for change in track.get("changes") or []:
            lines.append(f'"{track.get("title")}": '
                         f'{str(change.get("label") or change.get("field")).lower()} '
                         f'becomes "{_shown(change.get("file"))}"')
    return lines


def _shown(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value if value is not None else "")


@register_job
class UseFileTagsJob(RepairJob):
    job_id = 'use_file_tags'
    display_name = 'Use File Tags'
    description = 'Finds albums whose file tags say something the Library does not, so the Library can follow the files'
    help_text = (
        'For when you fixed your tags outside SoulSync and want the Library to say '
        'what the files say. Reads the tags of every file and compares them with the '
        'Library: track title, track and disc number, the album a track is on, and the '
        'album\'s year, release date and genre.\n\n'
        'Each album that differs becomes one finding listing what would change. '
        'Approving it saves those values as manual edits, the same as the Edit dialogs, '
        'so enrichment does not put the old value back, and moves a track whose tag '
        'names another album. Nothing on disk is touched and no tag is written. '
        'Artist names are left alone.\n\n'
        'It is the album menu\'s "Use file tags", for the whole library. Off by '
        'default: only run it when your files are the ones you trust.'
    )
    icon = 'repair-icon-consistency'
    default_enabled = False
    default_interval_hours = 168
    auto_fix = False
    supports_artist_scope = True

    def _albums(self, context):
        from core.library2.maintenance_subjects import active_file_subjects
        albums = {}
        for subject in artist_scoped_subjects(
                context, active_file_subjects(context.db, context.config_manager)):
            albums.setdefault(int(subject['album_id']), []).append(subject)
        return albums

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._albums(context))
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        from core.library2 import tags_to_library
        result = JobResult()
        try:
            albums = self._albums(context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Use File Tags could not list the library's files: %s", exc)
            result.errors += 1
            return result
        total = len(albums)
        if context.update_progress:
            context.update_progress(0, total)
        for index, (album_id, subjects) in enumerate(sorted(albums.items())):
            if context.check_stop() or context.wait_if_paused():
                break
            result.scanned += 1
            if context.update_progress:
                context.update_progress(index + 1, total)
            first = subjects[0]
            if context.report_progress and index % 20 == 0:
                context.report_progress(
                    scanned=index + 1, total=total, phase=f'Reading tags {index + 1} / {total}',
                    log_line=f"{first.get('album_artist_name') or first.get('artist_name')} "
                             f"— {first.get('album_title')}",
                    log_type='info')
            try:
                track_ids = sorted({int(s['track_id']) for s in subjects})
                plan = tags_to_library.plan(context.db, track_ids)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Use File Tags failed on album %s: %s", album_id, exc, exc_info=True)
                result.errors += 1
                continue
            lines = describe_plan(plan)
            if not lines or not context.create_finding:
                result.skipped += 1
                continue
            album_title = first.get('album_title')
            artist_name = first.get('album_artist_name') or first.get('artist_name')
            more = len(lines) - SHOWN
            description = '; '.join(lines[:SHOWN]) + (f'; and {more} more' if more > 0 else '')
            inserted = context.create_finding(
                job_id=self.job_id, finding_type='file_tags_differ', severity='info',
                entity_type='album', entity_id=f'lib2:{album_id}',
                file_path=next((s.get('path') for s in subjects if s.get('path')), None),
                title=f'File tags differ: {album_title} by {artist_name}',
                description=description[:1].upper() + description[1:] + '.',
                details={
                    'album_id': f'lib2:{album_id}', 'album_title': album_title,
                    'artist_name': artist_name, 'changes': lines,
                    'counts': plan.get('counts') or {},
                    'library_owner_id': first.get('owner_profile_id') or 1,
                    'library_v2_native': True,
                    'library_v2': {
                        'artist_id': first.get('artist_id'), 'album_id': album_id,
                        'album_ids': [album_id], 'track_ids': track_ids,
                        'file_ids': sorted({s['file_id'] for s in subjects}),
                    },
                })
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1
        return result
