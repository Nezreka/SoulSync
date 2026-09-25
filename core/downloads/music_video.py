"""Music video downloads: work out who and what it is, file it, fetch it.

every video is its own batch on the Downloads page, made the moment it is
clicked, so it shows while it is still being matched. the batch is flagged
managed_externally (see direct_download_state) so the music engine never
touches it, and this module ends it and cleans it up, because the music
batch healer skips it by design.

matching is strict on purpose. a wrong match files the video under the wrong
artist or song, which is worse than the plain name parsed off the video. so
the result has to agree with BOTH the parsed artist and the parsed title, and
a cover or a live cut never borrows the studio song's name.
"""

from __future__ import annotations

import os
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import direct_download_state
from utils.logging_config import get_logger

logger = get_logger("downloads.music_video")

BATCH_TYPE = "music_video"
DEFAULT_TEMPLATE = "$artist/$title-video"
VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".m4v", ".mov")
# how long a finished video stays on the Downloads page, same as music batches
CLEANUP_AFTER_SECONDS = 300

# words that make a video a different recording from the studio song
_VERSION_RE = re.compile(
    r"\b(live|cover|remix|acoustic|unplugged|karaoke|instrumental|demo|"
    r"reaction|slowed|sped\s*up|8d|nightcore|rehearsal)\b",
    re.IGNORECASE,
)

# the status endpoint the search page polls. same shape as ever:
# {video_id: {status, progress, path, error, artist, title}}
state: Dict[str, Dict[str, Any]] = {}
_state_lock = threading.Lock()
_IN_FLIGHT = ("searching", "matching", "downloading")


# ── naming ──────────────────────────────────────────────────────────────────

def clean_title(raw_title: str) -> str:
    """Strip YouTube noise from a video title for metadata search + filing.

    Handles the suffix both parenthesized, "(Official Music Video)", and bare
    at the end ("... Fat Official Music Video", the shape fan uploads use).
    bare stripping only takes unambiguous multi-word forms, so a song really
    called "Video" or "Video Games" is never eaten."""
    s = re.sub(
        r'\s*[\(\[](official\s*(music\s*)?video|official\s*lyric\s*video|official\s*audio'
        r'|official\s*hd|hd|4k|remastered|lyric\s*video|visualizer|audio)[\)\]]',
        '', raw_title or '', flags=re.IGNORECASE).strip()
    s = re.sub(
        r'[\s\-–—|]*\b(official\s+(music\s+|lyric\s+)?(video|audio)'
        r'|music\s+video|lyric\s+video|visualizer)\s*$',
        '', s, flags=re.IGNORECASE).strip()
    return re.sub(r'\s*-\s*$', '', s).strip()


def clean_channel(raw_channel: str) -> str:
    """'RadioheadVEVO' / 'Radiohead - Topic' / 'Radiohead Official' -> 'Radiohead'."""
    s = (raw_channel or '').strip()
    s = re.sub(r'\s*-\s*topic$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*vevo$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+official$', '', s, flags=re.IGNORECASE)
    return s.strip() or (raw_channel or '').strip()


def _drop_noise_brackets(title: str) -> str:
    """drop (feat. x), [HD] and friends, keep the ones that name a version:
    "Creep (Live at Glastonbury)" stays a live recording."""
    def _keep(match: re.Match) -> str:
        return match.group(0) if _VERSION_RE.search(match.group(0)) else ''
    return re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', _keep, title).strip()


def parse_artist_title(raw_title: str, raw_channel: str) -> Tuple[str, str]:
    """Artist/title for filing a music video, from the video's own name.

    'Artist - Title' (hyphen, en or em dash) parses to the real artist. the
    uploader's channel is only the last resort for titles with no separator,
    because fan-channel uploads are the norm and filing under them scatters
    one artist's videos across folders."""
    for sep in (' - ', ' – ', ' — '):
        if sep in (raw_title or ''):
            artist, title = raw_title.split(sep, 1)
            title = clean_title(title) or title
            title = _drop_noise_brackets(title) or title
            return artist.strip(), title
    title = clean_title(raw_title) or raw_title
    return clean_channel(raw_channel), (_drop_noise_brackets(title) or title)


def _norm(value: str) -> str:
    s = unicodedata.normalize('NFKD', value or '')
    s = ''.join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', ' ', s)
    s = re.sub(r'\b(feat|ft|featuring)\b.*$', ' ', s)
    s = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _versions(value: str) -> set:
    return {m.group(1).lower().replace(' ', '') for m in _VERSION_RE.finditer(value or '')}


def _similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Match:
    artist: str
    title: str
    year: str
    score: float


def pick_match(results: List[Any], artist: str, title: str,
               raw_title: str = '') -> Optional[Match]:
    """The metadata result this video really is, or None.

    both halves have to agree: a title match alone happily files a cover under
    the band it covered, and an artist match alone picks their biggest hit.
    and the version has to agree: a live video never takes the studio name."""
    wanted_versions = _versions(raw_title or title)
    best: Optional[Match] = None
    for r in results or []:
        name = getattr(r, 'name', '') or ''
        artists = [a for a in (getattr(r, 'artists', None) or []) if a]
        if not name or not artists:
            continue
        if _versions(name) != wanted_versions:
            continue
        t = _similar(title, name)
        a = max(_similar(artist, x) for x in artists)
        if t < 0.85 or a < 0.8:
            continue
        score = (t + a) / 2
        if best is None or score > best.score:
            release = str(getattr(r, 'release_date', '') or '')
            best = Match(artist=artists[0], title=name, year=release[:4], score=score)
    return best


# ── filing ──────────────────────────────────────────────────────────────────

def _sanitize(value: str) -> str:
    # '"Weird Al" Yankovic' reads right as 'Weird Al' in a folder name, not _Weird Al_
    value = (value or '').replace('"', "'")
    return re.sub(r'[<>:/\\|?*]', '_', value).strip().rstrip('.').strip()


def render_path(template: str, artist: str, title: str, year: str,
                artist_letter: Callable[[str], str]) -> Tuple[str, str]:
    """(folder, file stem) under the music videos root, from the video template.

    a missing $year used to leave "Title ()" or "Title - " behind; empty
    brackets and dangling separators come out with it now."""
    template = (template or '').strip() or DEFAULT_TEMPLATE
    safe_artist = _sanitize(artist) or 'Unknown Artist'
    safe_title = _sanitize(title) or 'Unknown Title'
    path = template.replace('$artistletter', artist_letter(safe_artist) if safe_artist else 'A')
    path = path.replace('$artist', safe_artist)
    path = path.replace('$title', safe_title)
    path = path.replace('$year', str(year or ''))

    parts = []
    for segment in path.replace('\\', '/').split('/'):
        segment = re.sub(r'\s*[\(\[]\s*[\)\]]', '', segment)
        segment = re.sub(r'^[\s\-–—.]+|[\s\-–—]+$', '', segment)
        segment = re.sub(r'\s{2,}', ' ', segment).strip()
        if segment:
            parts.append(segment)
    if not parts:
        parts = [f'{safe_title}-video']
    return '/'.join(parts[:-1]), parts[-1]


def existing_video(output_stem: str) -> Optional[str]:
    """a finished video already sitting where this one would go."""
    for suffix in VIDEO_SUFFIXES:
        candidate = output_stem + suffix
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def remove_partials(output_stem: str) -> None:
    """yt-dlp's leftovers from a cancelled or failed fetch."""
    folder = os.path.dirname(output_stem) or '.'
    stem = os.path.basename(output_stem)
    try:
        for name in os.listdir(folder):
            if not name.startswith(stem + '.'):
                continue
            if name.endswith(('.part', '.ytdl', '.temp')) or '.part-Frag' in name or re.search(r'\.f\d+\.\w+$', name):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
    except OSError:
        pass


def tag_video(path: str, artist: str, title: str, year: str) -> bool:
    """title, artist and year in the mp4 itself, so a media server that reads
    tags names it right instead of guessing from the filename. best effort."""
    if Path(path).suffix.lower() not in ('.mp4', '.m4v'):
        return False
    try:
        from mutagen.mp4 import MP4
        video = MP4(path)
        video['\xa9nam'] = [title]
        video['\xa9ART'] = [artist]
        if year:
            video['\xa9day'] = [year]
        video.save()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Music Video] could not tag %s: %s", path, exc)
        return False


# ── the job ─────────────────────────────────────────────────────────────────

@dataclass
class MusicVideoDeps:
    search_tracks: Callable[[str, int], List[Any]]
    # (url, output_stem, progress_cb, should_cancel) -> final path or None
    download: Callable[..., Optional[str]]
    video_template: Callable[[], str]
    artist_letter: Callable[[str], str]
    add_activity: Callable[[str, str, str], None]
    record_history: Callable[..., Any]
    start_thread: Callable[[Callable[[], None], str], None] = None  # type: ignore[assignment]
    schedule_cleanup: Callable[[Callable[[], None], float], None] = None  # type: ignore[assignment]


def _default_start_thread(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, daemon=True, name=name).start()


def _default_schedule(fn: Callable[[], None], delay: float) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


def _set_state(video_id: str, **fields: Any) -> None:
    with _state_lock:
        state.setdefault(video_id, {'status': 'searching', 'progress': 0, 'path': None, 'error': None})
        state[video_id].update(fields)


def is_in_flight(video_id: str) -> bool:
    with _state_lock:
        return (state.get(video_id) or {}).get('status') in _IN_FLIGHT


def start(video: Dict[str, Any], root: str, deps: MusicVideoDeps) -> Dict[str, Any]:
    """Put the video on the Downloads page and fetch it in the background.

    returns {'success': True, 'video_id', 'batch_id'} or {'error', 'code'}."""
    video_id = str(video.get('video_id') or '').strip()
    url = str(video.get('url') or '').strip()
    if not video_id or not url:
        return {'error': 'Missing video_id or url', 'code': 400}

    with _state_lock:
        # the check and the claim in one step: a double click must not start two
        if (state.get(video_id) or {}).get('status') in _IN_FLIGHT:
            return {'error': 'Already downloading', 'code': 409}
        state[video_id] = {'status': 'searching', 'progress': 0, 'path': None, 'error': None}

    raw_title = str(video.get('title') or '')
    raw_channel = str(video.get('channel') or '')
    artist, title = parse_artist_title(raw_title, raw_channel)
    batch_id = f'music_video_{uuid.uuid4().hex[:12]}'
    task_id = f'{batch_id}_0'

    direct_download_state.register(
        batch_id, task_id,
        title=title or raw_title, artist=artist, album='Music Video',
        artwork_url=str(video.get('thumbnail') or ''),
        source_label='YouTube',
        status='searching',
        batch_name=f'{artist} - {title}' if artist and title else (raw_title or 'Music video'),
        batch_type=BATCH_TYPE,
        source_page='Search',
        phase='analysis',
    )

    start_thread = deps.start_thread or _default_start_thread
    start_thread(lambda: run(video_id, task_id, batch_id, url, raw_title, raw_channel,
                             str(video.get('thumbnail') or ''), root, deps),
                 f'music-video-{video_id}')
    return {'success': True, 'video_id': video_id, 'batch_id': batch_id}


def resolve(raw_title: str, raw_channel: str, deps: MusicVideoDeps) -> Match:
    """who and what the video is: a strict metadata match, else the parse."""
    artist, title = parse_artist_title(raw_title, raw_channel)
    try:
        results = deps.search_tracks(f'{artist} {title}'.strip(), 8) or []
        match = pick_match(results, artist, title, raw_title)
        if match:
            logger.info("[Music Video] Matched to: %s - %s (%.2f)", match.artist, match.title, match.score)
            return match
    except Exception as exc:  # noqa: BLE001
        logger.error("[Music Video] Metadata lookup failed: %s", exc)
    logger.info("[Music Video] No confident match, filing as parsed: %s - %s", artist, title)
    return Match(artist=artist, title=title, year='', score=0.0)


def run(video_id: str, task_id: str, batch_id: str, url: str, raw_title: str,
        raw_channel: str, thumbnail: str, root: str, deps: MusicVideoDeps) -> None:
    """match, file, fetch, tag, record. every exit ends the card."""
    output_stem = ''
    try:
        _set_state(video_id, status='matching')
        match = resolve(raw_title, raw_channel, deps)
        _set_state(video_id, artist=match.artist, title=match.title)
        direct_download_state.update_info(
            task_id, title=match.title, artist=match.artist,
            batch_name=f'{match.artist} - {match.title}',
        )

        folder, stem = render_path(deps.video_template(), match.artist, match.title,
                                   match.year, deps.artist_letter)
        output_dir = os.path.join(root, folder) if folder else root
        os.makedirs(output_dir, exist_ok=True)
        output_stem = os.path.join(output_dir, stem)

        if direct_download_state.is_cancelled(task_id):
            _finish(video_id, task_id, batch_id, 'cancelled', deps)
            return

        already = existing_video(output_stem)
        if already:
            logger.info("[Music Video] Already have it: %s", already)
            _set_state(video_id, status='completed', progress=100, path=already)
            direct_download_state.mark_status(task_id, 'completed', file_path=already)
            _finish(video_id, task_id, batch_id, 'completed', deps)
            return

        _set_state(video_id, status='downloading')
        direct_download_state.mark_status(task_id, 'downloading')
        direct_download_state.set_phase(batch_id, 'downloading')

        def _progress(pct: float) -> None:
            _set_state(video_id, progress=round(pct, 1))
            direct_download_state.update_progress(task_id, percent=pct)

        final_path = deps.download(url, output_stem, _progress,
                                   lambda: direct_download_state.is_cancelled(task_id))

        if direct_download_state.is_cancelled(task_id):
            remove_partials(output_stem)
            _finish(video_id, task_id, batch_id, 'cancelled', deps)
            return

        if not final_path or not os.path.exists(final_path):
            remove_partials(output_stem)
            _fail(video_id, task_id, batch_id, 'Download failed, no file came back', deps)
            return

        direct_download_state.mark_status(task_id, 'post_processing')
        tag_video(final_path, match.artist, match.title, match.year)
        _set_state(video_id, status='completed', progress=100, path=final_path)
        direct_download_state.mark_status(task_id, 'completed', file_path=final_path)
        try:
            deps.record_history(
                event_type='download', title=match.title, artist_name=match.artist,
                album_name='Music Video', file_path=final_path, thumb_url=thumbnail,
                download_source='YouTube', source_track_id=video_id,
                source_track_title=raw_title, source_artist=raw_channel,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Music Video] history record failed: %s", exc)
        deps.add_activity('', 'Music Video Downloaded', f'{match.artist} - {match.title}')
        logger.info("[Music Video] Downloaded: %s - %s -> %s", match.artist, match.title, final_path)
        _finish(video_id, task_id, batch_id, 'completed', deps)
    except Exception as exc:  # noqa: BLE001
        # a card left saying 'downloading' after the thread died is worse than
        # no card: the page would show it running forever
        logger.error("[Music Video] %s", exc)
        if output_stem:
            remove_partials(output_stem)
        _fail(video_id, task_id, batch_id, str(exc), deps)


def _fail(video_id: str, task_id: str, batch_id: str, error: str, deps: MusicVideoDeps) -> None:
    _set_state(video_id, status='error', error=error)
    direct_download_state.mark_status(task_id, 'failed', error=error)
    _finish(video_id, task_id, batch_id, 'failed', deps)


def _finish(video_id: str, task_id: str, batch_id: str, outcome: str, deps: MusicVideoDeps) -> None:
    """end the batch and drop it from the page after a while, like music batches."""
    if outcome == 'cancelled':
        # the search page polls this and only knows completed/error
        _set_state(video_id, status='error', error='Cancelled')
        direct_download_state.mark_status(task_id, 'cancelled')
    direct_download_state.set_phase(batch_id, {
        'completed': 'complete', 'cancelled': 'cancelled',
    }.get(outcome, 'error'))
    schedule = deps.schedule_cleanup or _default_schedule
    schedule(lambda: direct_download_state.forget(task_id), CLEANUP_AFTER_SECONDS)
