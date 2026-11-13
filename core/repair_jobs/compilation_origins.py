"""Compilation songs back to their original albums (#2077).

A greatest hits album has one release year and its songs have a dozen. A
player that filters by the ALBUM's year (Plexamp does, and has no filter for a
track's own) counts a 1978 song on a 1996 best-of as a 90s song. #2053 put
each song's own year in its original-date tags, which that filter never reads.

This job finds, for each song an artist's own compilation holds, the release it
first belonged to, and files one finding per compilation listing where every
song would go. Approving it retags those files (album, album artist, date,
track and disc number, MusicBrainz ids) and moves the tracks in the Library.
The compilation keeps whatever was not placed, or goes when nothing is left.

Where a song goes: the studio album when the SAME recording is on one,
otherwise the single or EP it came out on. The same recording, not the same
title: a best-of often carries a single mix that never was on the album, and
that mix is filed under its single. A song that is only ever on compilations
stays where it is.

Kept narrow on purpose:
- an artist's own compilation only. On a various-artists compilation each song
  belongs to another artist, and re-crediting is a different tool.
- a compilation the Library holds a MusicBrainz release id for. The release's
  own track list names the exact recordings; a search by song name was tried
  for #2053 and was wrong more often than right.
- nothing is done without an approved finding. Off by default.
"""

from typing import Any, Dict, List, Optional

from core.repair_jobs import register_job
from core.repair_jobs.album_release_year_repair import _fold_title, extract_year, song_title
from core.repair_jobs.base import JobContext, JobResult, RepairJob, artist_scoped_subjects
from utils.logging_config import get_logger

logger = get_logger("repair_job.compilation_origins")

VARIOUS_ARTISTS_MBID = '89ad4ac3-39f7-470e-963a-56509c546377'
#: the studio album first, then the EP, then the single
_TIER = {'album': 0, 'ep': 1, 'single': 2}
#: pages of a recording's releases read before giving up (100 a page)
MAX_PAGES = 5


def _credit(entity: Any) -> str:
    return ''.join(str(c.get('name') or (c.get('artist') or {}).get('name') or '')
                   + str(c.get('joinphrase') or '')
                   for c in (entity or {}).get('artist-credit') or [] if isinstance(c, dict)).strip()


def _credit_ids(entity: Any) -> set:
    return {str((c.get('artist') or {}).get('id') or '')
            for c in (entity or {}).get('artist-credit') or [] if isinstance(c, dict)} - {''}


#: editions whose track order is the one people mean by "track 6"
_STANDARD_FORMATS = ('cd', 'digital media')


def _edition_rank(release: Dict[str, Any]) -> tuple:
    """Which pressing of an album to read the track number from: one disc, on
    CD or digital, the earliest of those. A double 12-inch or a cassette puts
    the same song at another number."""
    media = [m for m in (release.get('media') or []) if isinstance(m, dict)]
    one_disc = len(media) == 1
    standard = one_disc and str(media[0].get('format') or '').lower() in _STANDARD_FORMATS
    return (0 if standard else 1 if one_disc else 2,
            str(release.get('date') or '9999'), str(release.get('id') or ''))


#: a lookup of a release group lists at most this many of its releases
_GROUP_LISTING_CAP = 25
#: the share of an album's pressings a recording has to be on to be the album's
_ALBUM_SHARE = 0.5


def pick_original(releases: List[Dict[str, Any]], *, not_group: Optional[str] = None,
                  group_size: Any = None) -> Optional[Dict[str, Any]]:
    """The release a recording first belonged to, from every release it is on.

    Official releases whose group is a plain album, EP or single: a group with
    any secondary type (compilation, live, soundtrack, remix) is somewhere the
    song was put later. The studio album wins over an EP, an EP over a single,
    and within one kind the earliest group. None when the recording is only on
    compilations.

    An album only counts when the song was on it from the start. A non-album
    single added as a bonus track to the remaster twenty years on is still a
    single: "Shake the Disease" is on Black Celebration only from the reissues,
    and the live service showed it filed there as track 16 of 23. So an album
    group is passed over unless a pressing from its first year holds the song.

    And it has to be on the album proper, not on one odd edition filed with
    it. A best-of's "Enjoy the Silence" is the single mix, which sits on a
    single 8-track pressing in the Violator group and on none of the others;
    the live service had it down as Violator, track 1 of 8. ``group_size``
    answers how many pressings an album has (None when unknown), and the song
    must be on half of them.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for release in releases or []:
        if not isinstance(release, dict):
            continue
        if str(release.get('status') or 'Official').lower() != 'official':
            continue
        group = release.get('release-group') or {}
        if group.get('secondary-types') or not group.get('id') or group.get('id') == not_group:
            continue
        tier = _TIER.get(str(group.get('primary-type') or '').lower())
        date = str(group.get('first-release-date') or release.get('date') or '')
        if tier is None or not extract_year(date[:4]):
            continue
        entry = groups.setdefault(group['id'], {'tier': tier, 'date': date, 'group': group, 'releases': []})
        entry['releases'].append(release)

    best = None
    for entry in groups.values():
        first_year = extract_year(entry['date'][:4])
        years = [y for y in (extract_year(str(r.get('date') or '')[:4]) for r in entry['releases']) if y]
        if entry['tier'] == _TIER['album'] and years and int(min(years)) > int(first_year):
            continue    # only on later pressings: a bonus track, not the album's own
        if entry['tier'] == _TIER['album'] and group_size is not None:
            size = group_size(entry['group']['id'])
            if size and len(entry['releases']) < min(size, _GROUP_LISTING_CAP) * _ALBUM_SHARE:
                continue    # on an odd edition or two, not on the album itself
        # the track number is read off a pressing from the album's own year
        early = [r for r in entry['releases']
                 if extract_year(str(r.get('date') or '')[:4]) in (None, first_year)] or entry['releases']
        key = (entry['tier'], entry['date'], entry['group']['id'])
        if best is None or key < best[0]:
            best = (key, min(early, key=_edition_rank), entry['group'], entry['date'])
    if best is None:
        return None
    _key, release, group, date = best
    return {
        'kind': str(group.get('primary-type') or '').lower(),
        'release_group_id': group.get('id'),
        'release_id': release.get('id'),
        'album_title': str(group.get('title') or release.get('title') or ''),
        'date': date,
        'year': extract_year(date[:4]),
        'album_artist': _credit(release),
    }


def recording_releases(mb_client: Any, recording_id: str, memo: Dict[Any, Any]) -> List[Dict[str, Any]]:
    """Every release a recording is on. A lookup of the recording itself hands
    back 25 of them, and a hit's hundred compilations crowd the album out, so
    this pages through the browse."""
    key = ('recording_releases', recording_id)
    if key in memo:
        return memo[key]
    found: List[Dict[str, Any]] = []
    for page in range(MAX_PAGES):
        batch = mb_client.browse_recording_releases(recording_id, limit=100, offset=page * 100)
        found.extend(batch or [])
        if len(batch or []) < 100:
            break
    memo[key] = found
    return found


def release_group_size(mb_client: Any, group_id: str, memo: Dict[Any, Any]) -> Optional[int]:
    """How many pressings an album has (as many as a lookup lists), or None."""
    key = ('group_size', group_id)
    if key not in memo:
        try:
            group = mb_client.get_release_group(group_id, includes=['releases'])
            memo[key] = len((group or {}).get('releases') or []) or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("release group %s lookup failed: %s", group_id, exc)
            memo[key] = None
    return memo[key]


def track_place(mb_client: Any, release_id: str, recording_id: str,
                memo: Dict[Any, Any]) -> Optional[Dict[str, int]]:
    """Where a recording sits on a release: disc, track, and the totals."""
    key = ('release_tracks', release_id)
    if key not in memo:
        try:
            memo[key] = mb_client.get_release(release_id, includes=['recordings'])
        except Exception as exc:  # noqa: BLE001
            logger.debug("track list lookup for release %s failed: %s", release_id, exc)
            memo[key] = None
    release = memo[key]
    media = [m for m in ((release or {}).get('media') or []) if isinstance(m, dict)]
    for index, medium in enumerate(media):
        for track in medium.get('tracks') or []:
            if isinstance(track, dict) and (track.get('recording') or {}).get('id') == recording_id:
                position = track.get('position')
                if not isinstance(position, int) or position <= 0:
                    return None
                return {'disc_number': int(medium.get('position') or index + 1),
                        'track_number': position,
                        'total_tracks': int(medium.get('track-count') or len(medium.get('tracks') or [])),
                        'total_discs': len(media)}
    return None


def plan_compilation(mb_client: Any, release_id: str, tracks: List[Dict[str, Any]],
                     memo: Dict[Any, Any]) -> Dict[str, Any]:
    """Where each held song of one compilation would go.

    ``tracks`` are the Library's tracks of the album (``title`` at least).
    Returns ``{'reason': str}`` when the album is not one this job handles,
    else ``{'moves': [...], 'staying': [...]}``."""
    try:
        release = mb_client.get_release(
            release_id, includes=['recordings', 'release-groups', 'artist-credits'])
    except Exception as exc:  # noqa: BLE001
        logger.debug("compilation lookup for release %s failed: %s", release_id, exc)
        release = None
    if not isinstance(release, dict):
        return {'reason': 'lookup_failed'}
    group = release.get('release-group') or {}
    if not any(str(t).lower() == 'compilation' for t in group.get('secondary-types') or []):
        return {'reason': 'not_a_compilation'}
    if VARIOUS_ARTISTS_MBID in _credit_ids(release):
        return {'reason': 'various_artists'}
    album_artists = _credit_ids(release)

    by_title: Dict[str, Any] = {}
    clash = set()
    for medium in release.get('media') or []:
        for track in (medium.get('tracks') or []) if isinstance(medium, dict) else []:
            recording = (track or {}).get('recording') or {}
            title = _fold_title(song_title(track.get('title') or recording.get('title')))
            if not title or not recording.get('id'):
                continue
            # a title on the release twice (the song and a remix of it): nothing
            # here says which one a file is
            if title in by_title and by_title[title]['id'] != recording['id']:
                clash.add(title)
            by_title.setdefault(title, recording)

    moves, staying = [], []
    for track in tracks:
        title = _fold_title(song_title(track.get('title')))
        recording = by_title.get(title)
        if recording is None or title in clash:
            staying.append({'title': track.get('title'), 'why': 'not on the release by that name'})
            continue
        # a guest's song on an artist's best-of belongs to the guest
        credited = _credit_ids(recording)
        if credited and album_artists and not (credited & album_artists):
            staying.append({'title': track.get('title'), 'why': 'credited to another artist'})
            continue
        origin = pick_original(recording_releases(mb_client, recording['id'], memo),
                               not_group=group.get('id'),
                               group_size=lambda gid: release_group_size(mb_client, gid, memo))
        if origin is None:
            staying.append({'title': track.get('title'), 'why': 'only ever on compilations'})
            continue
        place = track_place(mb_client, origin['release_id'], recording['id'], memo)
        if place is None:
            staying.append({'title': track.get('title'), 'why': 'no track number on the original'})
            continue
        moves.append({**{k: track.get(k) for k in ('track_id', 'file_id', 'title', 'file_path')},
                      'recording_id': recording['id'], 'to': {**origin, **place}})
    return {'moves': moves, 'staying': staying,
            'album_artist': _credit(release), 'compilation_year': extract_year(str(release.get('date') or '')[:4])}


def describe_moves(moves: List[Dict[str, Any]], shown: int = 4) -> str:
    lines = [f'"{m["title"]}" to {m["to"]["album_title"]} ({m["to"]["year"]})' for m in moves[:shown]]
    more = len(moves) - shown
    return '; '.join(lines) + (f'; and {more} more' if more > 0 else '')


@register_job
class CompilationOriginsJob(RepairJob):
    job_id = 'compilation_origins'
    display_name = 'Compilation Songs to Original Albums'
    description = 'Finds where each song on a greatest hits or best-of album first came out, so it can be filed under that album'
    help_text = (
        'A greatest hits album has one release year, and a player that filters by the '
        'album\'s year (Plexamp does) counts every song on it as that year: a 1978 song on '
        'a 1996 best-of is a 90s song.\n\n'
        'For each of an artist\'s own compilations, this looks up where every song first '
        'came out: the studio album when the same recording is on one, otherwise the single '
        'or EP. Each compilation becomes one finding listing where its songs would go. '
        'Approving it retags those files (album, album artist, date, track and disc number) '
        'and moves the tracks in the Library; run Reorganize afterwards to move the files. '
        'The compilation keeps the songs that could not be placed.\n\n'
        'It breaks compilations up, so it is off by default and changes nothing until you '
        'approve a finding. It only handles compilations the Library has a MusicBrainz '
        'release id for, and leaves various-artists compilations alone. Each song is a '
        'MusicBrainz lookup, so a large library takes a while.'
    )
    icon = 'repair-icon-consistency'
    default_enabled = False
    default_interval_hours = 720
    auto_fix = False
    supports_artist_scope = True

    def _albums(self, context: JobContext) -> Dict[int, List[Dict[str, Any]]]:
        from core.library2.maintenance_subjects import active_file_subjects
        albums: Dict[int, List[Dict[str, Any]]] = {}
        for subject in artist_scoped_subjects(
                context, active_file_subjects(context.db, context.config_manager)):
            if subject.get('album_musicbrainz_id') or (subject.get('album_source_ids') or {}).get('musicbrainz'):
                albums.setdefault(int(subject['album_id']), []).append(subject)
        return albums

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._albums(context))
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        mb_client = getattr(context, 'mb_client', None)
        if mb_client is None:
            if context.report_progress:
                context.report_progress(log_line='MusicBrainz is not available', log_type='warning')
            result.errors += 1
            return result
        try:
            albums = self._albums(context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Compilation origins could not list the library's files: %s", exc)
            result.errors += 1
            return result
        memo: Dict[Any, Any] = {}
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
            release_id = (first.get('album_musicbrainz_id')
                          or (first.get('album_source_ids') or {}).get('musicbrainz'))
            tracks = [{'track_id': f"lib2:{s['track_id']}", 'file_id': s['file_id'],
                       'title': s.get('title'), 'file_path': s.get('path')} for s in subjects]
            try:
                plan = plan_compilation(mb_client, str(release_id), tracks, memo)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Compilation origins failed on album %s: %s", album_id, exc, exc_info=True)
                result.errors += 1
                continue
            moves = plan.get('moves') or []
            if not moves or not context.create_finding:
                result.skipped += 1
                continue
            album_title = first.get('album_title')
            artist_name = first.get('album_artist_name') or first.get('artist_name')
            staying = plan.get('staying') or []
            if context.report_progress:
                context.report_progress(
                    log_line=f'{artist_name} — {album_title}: {len(moves)} of {len(tracks)} songs placed',
                    log_type='info')
            inserted = context.create_finding(
                job_id=self.job_id, finding_type='compilation_track_origin', severity='info',
                entity_type='album', entity_id=f'lib2:{album_id}',
                file_path=first.get('path'),
                title=f'Original albums found: {album_title} by {artist_name}',
                description=(
                    f'{len(moves)} of {len(tracks)} songs can go to the album they first came out on: '
                    f'{describe_moves(moves)}.'
                    + (f' {len(staying)} stay on the compilation.' if staying else '')),
                details={
                    'album_id': f'lib2:{album_id}', 'album_title': album_title,
                    'artist_name': artist_name, 'moves': moves, 'staying': staying,
                    'library_owner_id': first.get('owner_profile_id') or 1,
                    'library_v2_native': True,
                    'library_v2': {
                        'artist_id': first.get('artist_id'), 'album_id': album_id,
                        'album_ids': [album_id],
                        'track_ids': sorted({s['track_id'] for s in subjects}),
                        'file_ids': sorted({s['file_id'] for s in subjects}),
                    },
                })
            if inserted:
                result.findings_created += 1
            else:
                result.findings_skipped_dedup += 1
        return result
