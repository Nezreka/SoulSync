"""Where a library album's files belong, and moving them there.

A reorganize applies the current file-organization template to files the user
ALREADY OWNS. That is the whole job: compute a destination, move the file,
update the catalogue row.

It used to be something else. Each file was copied into a staging folder and
pushed through ``_post_process_matched_download`` — the DOWNLOAD pipeline, an
ACCEPTANCE check for files of unknown origin. Post-processing does know how to
pick a destination and write tags, so the reuse looked free. It was not:

* the acceptance check kept rejecting the library. Four opt-outs accumulated in
  the context builder, one per report — ``is_local_import`` (#804) for the
  integrity leg's duration disagreement, ``_skip_quarantine_check: 'acoustid'``
  (#1182) for a file quarantined over its OWN fingerprint (Sawano Hiroyuki
  fingerprints as 澤野弘之), ``_no_album_folder_reuse`` (#829) because reuse
  resolved to the folder the album was being moved OUT of.
* it re-tagged. That is the Library Re-tag job's work, from a source it can
  show you first.
* it copied. ~800MB of I/O for a 20-track FLAC album, and a failure left ~40MB
  a track in quarantine.

And the tracklist came from a live provider call, which is why an album with no
stored source id could not be reorganized at all, why a preview took seconds,
and why the library's own values had to be pulled back in one exception at a
time (``_keep_user_casing`` twice, ``_keep_user_year``).

So: the plan comes from the catalogue (:func:`_plan_from_catalogue`) and the
executor moves (:func:`reorganize_album_rename_only`). Identity is the AcoustID
Scanner's question and tags are the re-tag job's; neither of them moves anyone's
audio to answer.

The destination is still built by ``core.imports.paths.build_final_path_for_track``
through the same context shape post-processing uses, so a reorganize destination
and a fresh download's destination cannot drift apart.
"""

import errno
import os
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


from utils.logging_config import get_logger

logger = get_logger("library_reorganize")


def load_album_and_tracks(db, album_id):
    """Load the album row + all its track rows from the local DB.

    Returns ``(album_dict | None, tracks_list)``. ``album_dict`` is None
    when the album doesn't exist; tracks_list is empty when the album
    has no tracks. The caller decides what status to surface for each
    state.
    """
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT al.*, ar.name as artist_name
            FROM albums al
            JOIN artists ar ON al.artist_id = ar.id
            WHERE al.id = ?
            """,
            (str(album_id),),
        )
        album_row = cursor.fetchone()
        if not album_row:
            return None, []
        album_data = dict(album_row)

        cursor.execute(
            """
            SELECT t.*, ar.name as artist_name
            FROM tracks t
            JOIN artists ar ON t.artist_id = ar.id
            WHERE t.album_id = ?
            ORDER BY t.track_number
            """,
            (str(album_id),),
        )
        tracks = [dict(r) for r in cursor.fetchall()]
        return album_data, tracks
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass


def _plan_from_catalogue(album_data: dict, tracks: List[dict]) -> dict:
    """Catalogue planner: the album's own library rows ARE the tracklist.

    Reorganize moves files the user already owns. Where they belong is a
    question about the album in the library, so the names come from the library
    — the same values the Library page shows, hand-corrected titles included.

    The old planner asked a provider and then pulled the library's values back
    in one exception at a time: ``_keep_user_casing`` for the album name
    (#1078), again for the track title (#1078), ``_keep_user_year`` for the year
    (#1080). Three patches, each added after a report, each saying the same
    thing. Reading the catalogue makes all three true by construction.

    Consequences, all intended:

    * An album with no stored source id is reorganizable. Refusing it
      (``status: 'no_source_id'``) was a provider requirement imposed on an
      operation that needs no provider.
    * The plan is offline — no per-preview API call, and no ``Invalid base62
      id`` 400s from candidate ids that were never Spotify's to begin with.
    * ``total_discs`` is the layout the catalogue knows, not one a live
      tracklist decides differently on each call.

    A track the library cannot name comes back ``matched=False`` with a reason
    rather than being dropped, so the preview can say which one and why.
    """
    artist_name = album_data.get('artist_name') or ''
    api_album = {
        'id': '',
        'name': album_data.get('title') or '',
        'release_date': album_data.get('release_date') or album_data.get('year') or '',
        'total_tracks': album_data.get('track_count') or len(tracks),
        'image_url': album_data.get('image_url') or '',
    }

    items: List[dict] = []
    max_disc = 1
    for track in tracks:
        title = (track.get('title') or '').strip()
        if not title:
            items.append({
                'track': track, 'api_track': None, 'matched': False,
                'reason': 'The library has no title for this track — there is '
                          'nothing to name the file after.',
            })
            continue
        try:
            disc = int(track.get('disc_number') or 1)
        except (TypeError, ValueError):
            disc = 1
        if disc > max_disc:
            max_disc = disc
        try:
            number = int(track.get('track_number') or 0)
        except (TypeError, ValueError):
            number = 0
        items.append({
            'track': track,
            'api_track': {
                'id': '',
                'name': title,
                'track_number': number or 1,
                'disc_number': disc,
                'duration_ms': track.get('duration') or 0,
                'artists': [{'name': track.get('artist_name') or artist_name}],
            },
            'matched': True,
            'reason': None,
        })

    return {
        'status': 'planned',
        'source': 'catalogue',
        'api_album': api_album,
        'total_discs': max_disc,
        'items': items,
    }


def plan_album_reorganize(
    album_data: dict,
    tracks: List[dict],
) -> dict:
    """Compute the offline, catalogue-driven per-track plan.

    This intentionally has no source/mode argument. Reorganize has one truth:
    the values held by the library. Provider refresh belongs to Retag and file
    tags are an output of Retag, not a second path authority.
    """
    if not tracks:
        return {
            'status': 'no_tracks', 'source': None, 'api_album': None,
            'total_discs': 1, 'items': [],
        }

    return _plan_from_catalogue(album_data, tracks)


def _build_post_process_context(
    api_album: dict,
    api_track: dict,
    artist_name: str,
    album_title: str,
    total_discs: int,
) -> dict:
    """Build the download-shaped context consumed by the shared path builder."""
    track_number = int(api_track.get('track_number') or 1)
    disc_number = int(api_track.get('disc_number') or 1)
    track_artists = api_track.get('artists') or [artist_name]
    normalized_artists = [
        ({'name': a} if isinstance(a, str) else a) for a in track_artists
    ]

    api_album_id = api_album.get('id') or api_album.get('album_id') or ''
    api_album_name = api_album.get('name') or api_album.get('title') or album_title
    api_album_release = (
        api_album.get('release_date')
        or api_album.get('releaseDate')
        or ''
    )
    api_album_total_tracks = (
        api_album.get('total_tracks')
        or api_album.get('totalTracks')
        or 0
    )
    # Spotify shape: {'images': [{'url': ...}, ...]}.
    # Deezer shape: {'image_url': '...'}.
    api_album_image = api_album.get('image_url') or ''
    if not api_album_image:
        images = api_album.get('images')
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                api_album_image = first.get('url') or ''

    track_name = api_track.get('name') or api_track.get('title') or ''

    return {
        'spotify_artist': {
            'name': artist_name,
            'id': '',
            'genres': [],
        },
        'spotify_album': {
            'id': api_album_id,
            'name': api_album_name,
            'release_date': api_album_release,
            'total_tracks': api_album_total_tracks,
            'total_discs': total_discs,
            # Reorganize is the caller that really knows: it counted the discs
            # off the source tracklist it just resolved, so the path builder must
            # not go and ask a provider again (that lookup's success or failure
            # would decide the destination).
            'total_discs_declared': True,
            'image_url': api_album_image,
        },
        'track_info': {
            'name': track_name,
            'id': api_track.get('id', ''),
            'track_number': track_number,
            'disc_number': disc_number,
            'duration_ms': api_track.get('duration_ms', 0),
            'artists': normalized_artists,
            'uri': api_track.get('uri', ''),
        },
        'original_search_result': {
            'title': track_name,
            'artist': artist_name,
            'album': api_album_name,
            'track_number': track_number,
            'disc_number': disc_number,
            'spotify_clean_title': track_name,
            'spotify_clean_album': api_album_name,
            'artists': normalized_artists,
        },
        'is_album_download': True,
        'has_clean_spotify_data': True,
        'has_full_spotify_metadata': True,
        # `is_local_import` (#804) and `_skip_quarantine_check: 'acoustid'`
        # (#1182) used to sit here. Both were opt-outs FROM the download
        # post-process: a reorganize staged a copy of a file the user already
        # owns and pushed it through an acceptance check for files of unknown
        # origin, where the integrity leg quarantined it over a duration the
        # provider disagreed with and the AcoustID leg quarantined it over its
        # own fingerprint (Sawano Hiroyuki fingerprints as 澤野弘之).
        #
        # A reorganize does not post-process any more, so there is nothing left
        # to opt out of. This context now exists for ONE purpose: handing the
        # shared path builder the same shape a download hands it, so the two
        # cannot drift apart.
        #
        # Reorganize destinations must come from the CURRENT template alone.
        # The #829 existing-folder reuse would resolve to the folder the album
        # already lives in — the very folder reorganize is trying to move it
        # out of — so preview computed "unchanged" for every already-together
        # album and both the Tools job and Reorganize All silently no-opped
        # after a template change.
        '_no_album_folder_reuse': True,
    }


def preview_album_reorganize(
    *,
    album_id: str,
    db,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    build_final_path_fn: Callable,
) -> dict:
    """Compute the planned destination paths for a reorganize WITHOUT
    moving any files. The preview UI uses this to show users what the
    "Apply" run would do.

    Critically: the destination per track comes from
    ``build_final_path_fn(context, spotify_artist, None, file_ext)`` —
    the same shared helper post-processing uses. So the preview is
    guaranteed to match what the orchestrator would actually produce.

    Args:
        album_id: Library album ID.
        db: Database object exposing ``_get_connection()``.
        transfer_dir: Configured transfer directory (for trimming the
            display-relative current-path string).
        resolve_file_path_fn: Resolves a DB-stored file path to the
            actual on-disk path (or ``None`` if missing).
        build_final_path_fn: ``_build_final_path_for_track`` from
            web_server. Signature is
            ``(context, spotify_artist, album_info_or_none, file_ext) -> (path, ok)``.
            Injected so this module stays Flask-free.
    Returns:
        ``{
            'success': bool,
            'status': str,  # 'planned' | 'no_album' | 'no_tracks'
            'source': str | None,
            'album': str,
            'artist': str,
            'transfer_dir': str,
            'tracks': [
                {'track_id', 'title', 'track_number', 'current_path',
                 'new_path', 'file_exists', 'unchanged', 'collision',
                 'matched', 'reason', 'disc_number'},
                ...
            ],
        }``
    """
    album_data, tracks = load_album_and_tracks(db, album_id)
    if album_data is None:
        return {'success': False, 'status': 'no_album', 'tracks': []}

    if not tracks:
        return {
            'success': False, 'status': 'no_tracks',
            'album': album_data.get('title', ''),
            'artist': album_data.get('artist_name', ''),
            'tracks': [],
        }

    plan = plan_album_reorganize(album_data, tracks)
    artist_name = album_data.get('artist_name') or 'Unknown Artist'
    album_title = album_data.get('title') or 'Unknown Album'

    common = {
        'album': album_title,
        'artist': artist_name,
        'transfer_dir': transfer_dir,
        'source': plan['source'],
    }

    total_discs = plan['total_discs']
    api_album = plan['api_album'] or {}
    preview_tracks = []

    for plan_item in plan['items']:
        track = plan_item['track']
        title = track.get('title', '')
        db_path = track.get('file_path')
        resolved = resolve_file_path_fn(db_path) if db_path else None
        file_ext = os.path.splitext(resolved or db_path or '.flac')[1] or '.flac'

        item = {
            'track_id': track.get('id'),
            'title': title,
            'track_number': track.get('track_number', 0),
            'current_path': _trim_to_transfer(db_path, resolved, transfer_dir),
            'new_path': '',
            # Absolute on-disk paths (additive). `current_path`/`new_path` above are
            # display-trimmed; these carry the real paths so the rename-only executor
            # acts on EXACTLY what the preview computed — no separate path logic that
            # could drift from what the user saw (#875).
            'current_path_abs': resolved or '',
            'new_path_abs': '',
            'file_exists': resolved is not None,
            'unchanged': False,
            'collision': False,
            'matched': plan_item['matched'],
            'reason': plan_item.get('reason'),
            'disc_number': None,
        }

        # #746: never reorganize files sitting in the duplicate-cleaner
        # quarantine (<transfer>/deleted). Surface as a non-matched skip so
        # the preview shows WHY and apply leaves them put. Checked before the
        # matched branch so a quarantined track that happens to match the API
        # tracklist is still skipped.
        if _is_in_deleted_quarantine(resolved, transfer_dir):
            item['matched'] = False
            item['reason'] = 'In deleted/quarantine folder — skipped'
            preview_tracks.append(item)
            continue

        if not plan_item['matched']:
            preview_tracks.append(item)
            continue

        api_track = plan_item['api_track']
        item['disc_number'] = int(api_track.get('disc_number') or 1)
        # Build the download-shaped context consumed by the shared path
        # builder. The values themselves are all from the catalogue.
        context = _build_post_process_context(
            api_album, api_track, artist_name, album_title, total_discs,
        )
        # `_build_final_path_for_track` switches between ALBUM and SINGLE
        # modes based on `album_info.get('is_album')` — must be passed,
        # not None, otherwise multi-disc deluxes degrade to single-track
        # folders (the exact bug winecountrygames hit).
        album_info = _build_album_info(context)
        try:
            spotify_artist = context['spotify_artist']
            # Dry run: compute the destination path WITHOUT creating the folder.
            # Previously this physically created the album dir during preview,
            # leaving empty folders all over the library (#767).
            new_full, _ok = build_final_path_fn(
                context, spotify_artist, album_info, file_ext, create_dirs=False
            )
            item['new_path_abs'] = new_full or ''
            item['new_path'] = _display_relative_to_root(new_full, transfer_dir)
            if resolved and new_full and os.path.normpath(resolved) == os.path.normpath(new_full):
                item['unchanged'] = True
        except Exception as e:
            item['reason'] = f"Couldn't compute destination path: {e}"

        preview_tracks.append(item)

    # Collision detection: multiple matched tracks mapping to the same
    # destination would overwrite each other on apply.
    seen = {}
    for it in preview_tracks:
        if not it['matched'] or it['unchanged'] or not it['new_path']:
            continue
        norm = os.path.normpath(it['new_path'])
        if norm in seen:
            it['collision'] = True
            seen[norm]['collision'] = True
        else:
            seen[norm] = it

    return {
        'success': True, 'status': 'planned',
        **common,
        'tracks': preview_tracks,
    }


def _is_in_deleted_quarantine(resolved_path, transfer_dir) -> bool:
    """True when ``resolved_path`` lives inside the duplicate-cleaner
    quarantine folder (``<transfer_dir>/.deleted/...``, or the legacy bare
    ``deleted`` spelling older installs still carry).

    The Duplicate Cleaner (``core/library/duplicate_cleaner.py``) moves
    de-duplicated files into the quarantine. If the user's
    media server scans the transfer folder (e.g. a ``/music`` root that
    contains both the library and the transfer dir), those quarantined
    files get real rows in SoulSync's DB — and Reorganize, being purely
    DB-driven, would otherwise dutifully move them back OUT of /deleted
    to the template location. This guard makes Reorganize skip them so
    the quarantine stays quarantined (#746).

    Anchored to the transfer dir specifically so a legitimately
    named artist/album like "Deleted" elsewhere in the library is NOT
    skipped. When ``transfer_dir`` is unavailable we fall back to an exact
    ``deleted`` path-SEGMENT match (mirrors the cleaner's own
    ``if 'deleted' in dirs`` skip) — never a substring, so "Undeleted"
    or "deleted scenes" stay safe.
    """
    if not resolved_path:
        return False

    def _norm(p):
        # normpath collapses redundant separators / '..'; normcase applies
        # the platform's case rule (lowercases on Windows, no-op on posix);
        # fold to '/' so the segment/prefix checks are separator-agnostic.
        return os.path.normcase(os.path.normpath(p)).replace('\\', '/')

    norm = _norm(resolved_path)
    if transfer_dir:
        for name in ('.deleted', 'deleted'):     # hidden spelling + legacy installs
            quarantine = _norm(os.path.join(transfer_dir, name))
            if norm == quarantine or norm.startswith(quarantine + '/'):
                return True
        return False
    return any(seg in ('.deleted', 'deleted') for seg in norm.split('/'))


def _display_relative_to_root(path, root):
    """``path`` shown relative to ``root`` when it lives inside it, else whole.

    A raw ``startswith`` was wrong twice over. It matched a SIBLING root
    (``/music/Transfer2`` starts with ``/music/Transfer``), and it compared two
    strings that different code paths had spelled differently — the proposed
    path came from the path builder rooted at the config value, the current
    path from the resolver (absolute, symlinks resolved). With a relative root
    configured the two never shared a prefix, so the preview trimmed one column
    and printed the raw stored value in the other, for the very same file.
    """
    if not path or not root:
        return path or ''
    p = os.path.normpath(str(path))
    r = os.path.normpath(str(root))
    if p == r:
        return ''
    if p.startswith(r + os.sep) or (os.altsep and p.startswith(r + os.altsep)):
        return p[len(r):].lstrip(os.sep).lstrip('/')
    return path


def _trim_to_transfer(db_path, resolved, transfer_dir):
    """Compose the user-facing 'current path' string — relative to the
    transfer dir if the file lives there, else the raw DB value."""
    if resolved and transfer_dir:
        trimmed = _display_relative_to_root(resolved, transfer_dir)
        if trimmed != resolved:
            return trimmed
    return db_path or 'No file'


def _build_album_info(context: dict) -> dict:
    """Build the ``album_info`` dict that ``_build_final_path_for_track``
    consumes to enter ALBUM MODE. Without this (passing None) the path
    builder falls through to SINGLE MODE and produces per-track folders
    named after each track title — the exact bug we're fixing.

    Mirrors the shape the download path produces at write time.
    """
    spotify_album = context.get('spotify_album', {}) or {}
    track_info = context.get('track_info', {}) or {}
    return {
        'is_album': True,
        'album_name': spotify_album.get('name') or 'Unknown Album',
        'clean_track_name': track_info.get('name') or 'Unknown Track',
        'track_number': track_info.get('track_number') or 1,
        'disc_number': track_info.get('disc_number') or 1,
        'album_image_url': spotify_album.get('image_url') or '',
        'spotify_album_id': spotify_album.get('id') or '',
    }


def _rename_track_in_place(current_abs: str, new_abs: str) -> Tuple[bool, Optional[str]]:
    """Move ONE file from ``current_abs`` to ``new_abs`` in place — no copy, no re-tag,
    no post-processing. Creates the destination folder, carries sibling-format files
    (e.g. a lossy ``.opus`` alongside the ``.flac``) along with the renamed stem, and
    falls back to a cross-device move when the rename crosses a filesystem boundary.

    Refuses to overwrite a DIFFERENT existing file at the destination (returns an error
    instead) — never silent data loss. Returns ``(ok, error_message)``.
    """
    try:
        if current_abs and not os.path.exists(current_abs):
            return False, 'source file no longer on disk'
        same = os.path.normpath(current_abs) == os.path.normpath(new_abs)
        if os.path.exists(new_abs) and not same:
            return False, 'destination already exists'
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        # Carry sibling-format audio to the same destination with the renamed stem —
        # mirrors _finalize_track so lossy-copy pairs don't get orphaned.
        for sibling_src in _find_sibling_audio_files(current_abs):
            _move_sibling_to_destination(sibling_src, new_abs)
        try:
            os.rename(current_abs, new_abs)
        except OSError as e:
            if getattr(e, 'errno', None) == errno.EXDEV:
                shutil.move(current_abs, new_abs)  # crosses a filesystem boundary
            else:
                raise
        # Only once the audio has landed: a failed move must leave the whole
        # track — sidecars included — where it was.
        _move_track_sidecars(current_abs, new_abs)
        return True, None
    except Exception as e:
        return False, str(e)


def _move_track_sidecars(current_abs: str, new_abs: str) -> None:
    """Carry a track's own sidecars (.lrc/.nfo/.txt/.cue/.json) to the new stem.

    The full-mode reorganize could DELETE these at the source, because
    post-processing re-created them at the destination from the provider. A move
    has no such second half — leaving them behind loses hand-written lyrics.

    Best-effort and never destructive: a sidecar already at the destination is
    left exactly as it is.
    """
    src_dir = os.path.dirname(current_abs)
    dst_dir = os.path.dirname(new_abs)
    src_stem = os.path.splitext(os.path.basename(current_abs))[0]
    dst_stem = os.path.splitext(os.path.basename(new_abs))[0]
    for ext in _TRACK_SIDECAR_EXTS:
        src_side = os.path.join(src_dir, src_stem + ext)
        if not os.path.isfile(src_side):
            continue
        dst_side = os.path.join(dst_dir, dst_stem + ext)
        if os.path.exists(dst_side):
            continue
        try:
            os.rename(src_side, dst_side)
        except OSError as e:
            if getattr(e, 'errno', None) == errno.EXDEV:
                try:
                    shutil.move(src_side, dst_side)
                except Exception as move_err:
                    logger.debug("[Reorganize] sidecar %s not moved: %s", src_side, move_err)
            else:
                logger.debug("[Reorganize] sidecar %s not moved: %s", src_side, e)


def _album_dir_for_track_dir(directory: str) -> str:
    """Return the album root for a track directory.

    In a ``Disc N`` layout the artwork normally lives one level above the
    audio. Otherwise the track directory itself is the album root.
    """
    directory = os.path.normpath(directory)
    if _DISC_DIR_RE.match(os.path.basename(directory)):
        return os.path.dirname(directory)
    return directory


def _tree_has_audio(directory: str) -> bool:
    """Whether ``directory`` or any descendant still contains audio."""
    try:
        for _root, _dirs, files in os.walk(directory):
            if any(os.path.splitext(name)[1].lower() in _AUDIO_EXTS for name in files):
                return True
    except OSError:
        return True  # uncertainty must preserve data
    return False


def _move_album_sidecars(source_dir: str, destination_dir: str) -> int:
    """Move cover art and album sidecars after ALL source audio has left.

    Existing destination files are never overwritten. Unrecognised real
    content (for example a PDF booklet) stays at the source. OS junk stays too;
    moving ``.DS_Store`` would add no value and can only create collisions.
    """
    if not source_dir or not destination_dir:
        return 0
    source_dir = os.path.normpath(source_dir)
    destination_dir = os.path.normpath(destination_dir)
    if source_dir == destination_dir or _tree_has_audio(source_dir):
        return 0

    from core.library.residual_files import is_image, is_sidecar

    moved = 0
    try:
        names = os.listdir(source_dir)
    except OSError:
        return 0
    for name in names:
        if not (is_image(name) or is_sidecar(name)):
            continue
        source = os.path.join(source_dir, name)
        destination = os.path.join(destination_dir, name)
        if not os.path.isfile(source) or os.path.exists(destination):
            continue
        try:
            os.makedirs(destination_dir, exist_ok=True)
            try:
                os.rename(source, destination)
            except OSError as exc:
                if getattr(exc, 'errno', None) != errno.EXDEV:
                    raise
                shutil.move(source, destination)
            moved += 1
        except Exception as exc:  # best effort; never risk the audio move
            logger.debug("[Reorganize] album sidecar %s not moved: %s", source, exc)
    return moved


def reorganize_album_rename_only(
    *,
    album_id: str,
    db,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    build_final_path_fn: Callable,
    update_track_path_fn: Optional[Callable[[object, str], None]] = None,
    cleanup_empty_dir_fn: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    stop_check: Optional[Callable[[], bool]] = None,
    preview_fn: Optional[Callable] = None,
) -> dict:
    """RENAME-ONLY reorganize (#875): move each track's file to the path the current
    naming scheme dictates, and nothing else — no copy-to-staging, no re-tag, no
    quality/AcoustID checks.

    It acts on EXACTLY what :func:`preview_album_reorganize` computed (injected via
    ``preview_fn`` for testability), so the apply can never disagree with what the user
    saw, and ONLY files whose path actually changes are touched — files marked
    ``unchanged`` are skipped, which is what keeps a rename from rewriting the whole
    album (the #875 complaint). Tags and audio are left byte-for-byte alone.

    Returns the same summary shape as :func:`reorganize_album`.
    """
    preview_fn = preview_fn or preview_album_reorganize
    summary = {
        'status': 'completed', 'source': None, 'total': 0,
        'moved': 0, 'skipped': 0, 'failed': 0, 'errors': [],
    }

    def _emit(**updates):
        if on_progress is None:
            return
        try:
            on_progress(updates)
        except Exception as e:
            logger.debug("[Reorganize/rename] progress emit failed: %s", e)

    preview = preview_fn(
        album_id=album_id, db=db, transfer_dir=transfer_dir,
        resolve_file_path_fn=resolve_file_path_fn,
        build_final_path_fn=build_final_path_fn,
    )
    summary['source'] = preview.get('source')
    if not preview.get('success'):
        summary['status'] = preview.get('status', 'error')
        return summary

    tracks = preview.get('tracks', [])
    summary['total'] = len(tracks)
    src_dirs_touched: Set[str] = set()
    album_dir_moves: Dict[str, str] = {}

    for t in tracks:
        if stop_check and stop_check():
            break
        title = t.get('title', 'Unknown')
        _emit(current_track=title)

        # Skip anything that isn't a real, changing move. `unchanged` is the key one —
        # it's why a rename no longer rewrites files whose name didn't change.
        # `current_path_abs` is the other one: the preview leaves it empty when the
        # stored path resolves to nothing on disk (`file_exists` False). Passing that
        # to the mover fails on os.rename — but only AFTER os.makedirs has built the
        # destination tree, so an unresolvable track used to litter the library with
        # empty folders, which is exactly what the preview avoids with create_dirs=False.
        if (not t.get('matched') or t.get('unchanged')
                or t.get('collision') or not t.get('new_path_abs')
                or not t.get('current_path_abs')):
            summary['skipped'] += 1
            _emit(skipped=summary['skipped'])
            continue

        current_abs = t.get('current_path_abs')
        new_abs = t.get('new_path_abs')
        ok, err = _rename_track_in_place(current_abs, new_abs)
        if not ok:
            summary['failed'] += 1
            summary['errors'].append({
                'track_id': t.get('track_id'), 'title': title,
                'error': err or 'rename failed',
            })
            _emit(failed=summary['failed'], errors=list(summary['errors']))
            continue

        # File is at its new home — update the DB directly (authoritative; no need to
        # round-trip through a server scan to learn what we just did).
        #
        # A rename MOVES the only copy, so a failed catalogue update is NOT
        # recoverable by "a scan will reconcile": the file sits at a path nothing
        # points at, the track reads as MISSING, and the wishlist re-downloads
        # something the user already owns. Reported against a fresh library — songs
        # downloaded, Reorganize run while the import still held the write lock.
        # Put the file back and fail the track loudly instead.
        if update_track_path_fn:
            try:
                update_track_path_fn(t.get('track_id'), new_abs)
            except Exception as db_err:
                undone, undo_err = _rename_track_in_place(new_abs, current_abs)
                if undone:
                    detail = f'{db_err} (move undone)'
                    logger.warning(
                        "[Reorganize/rename] catalogue update failed for %s: %s "
                        "— moved %s back to %s",
                        t.get('track_id'), db_err, new_abs, current_abs,
                    )
                else:
                    detail = (f'{db_err} — and the file could not be moved back '
                              f'to {current_abs}: {undo_err}')
                    logger.error(
                        "[Reorganize/rename] catalogue update failed for %s (%s) AND "
                        "the rollback failed (%s): the file is at %s while the "
                        "catalogue still names %s",
                        t.get('track_id'), db_err, undo_err, new_abs, current_abs,
                    )
                summary['failed'] += 1
                summary['errors'].append({
                    'track_id': t.get('track_id'), 'title': title, 'error': detail,
                })
                _emit(failed=summary['failed'], errors=list(summary['errors']))
                continue
        if current_abs:
            source_track_dir = os.path.dirname(current_abs)
            destination_track_dir = os.path.dirname(new_abs)
            src_dirs_touched.add(source_track_dir)
            album_dir_moves[_album_dir_for_track_dir(source_track_dir)] = (
                _album_dir_for_track_dir(destination_track_dir)
            )
        summary['moved'] += 1
        _emit(moved=summary['moved'],
              processed=summary['moved'] + summary['skipped'] + summary['failed'])

    # Only after all successful track moves: carry the album's cover/scan art,
    # NFO, cue and playlist files. The helper refuses while ANY source audio
    # remains, so a partial or failed run never splits an album's sidecars away
    # from tracks that stayed behind.
    for source_album_dir, destination_album_dir in album_dir_moves.items():
        _move_album_sidecars(source_album_dir, destination_album_dir)

    for src_dir in src_dirs_touched:
        if cleanup_empty_dir_fn:
            try:
                cleanup_empty_dir_fn(src_dir)
            except Exception as e:
                logger.debug("[Reorganize/rename] cleanup of %s failed: %s", src_dir, e)
        try:
            _prune_empty_source_dirs(src_dir)   # #985: library-safe prune (transfer-dir-independent)
        except Exception as e:
            logger.debug("[Reorganize/rename] source prune of %s failed: %s", src_dir, e)

    return summary


_DISC_DIR_RE = re.compile(r'^(disc|disk|cd|vol|volume)\s*\.?\s*\d+$', re.IGNORECASE)


def _rmdir_if_empty(dir_path: str) -> bool:
    """rmdir ``dir_path`` if it holds no non-hidden entries (clearing hidden junk like
    .DS_Store first) and isn't a protected/configured root. Returns True if removed."""
    try:
        from core.imports.file_ops import protected_root_dirs
        if os.path.normpath(dir_path) in protected_root_dirs():
            return False
    except Exception as e:
        logger.debug("[Reorganize] protected-root check failed for %s: %s", dir_path, e)
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return False
    if any(not e.startswith('.') for e in entries):
        return False  # real content remains
    try:
        for hidden in entries:
            try:
                os.remove(os.path.join(dir_path, hidden))
            except OSError:
                pass
        os.rmdir(dir_path)
        logger.info(f"[Reorganize] Pruned empty source dir: {dir_path}")
        return True
    except OSError:
        return False


def _prune_empty_source_dirs(start_dir: str) -> None:
    """Prune the emptied SOURCE folders after a reorganize move.

    #985: the injected empty-dir cleanup is bounded by the transfer/download folder,
    but a Library Reorganize moves files that live in the MEDIA library — usually a
    different path — so that pruner's ``startswith(transfer_dir)`` guard never matches
    and the emptied source folders (``Album/Disc 1`` and the old ``Album`` dir) are
    left behind.

    Removes the moved file's own dir, AND the album dir directly above it IF that dir
    was a disc subfolder (``Disc N``/``CD N``). It deliberately NEVER climbs to the
    artist dir or the library root — bounded to the album level — so it can only ever
    delete the album/disc folders the reorganize actually emptied, never a root
    (which isn't in the protected set when the library lives outside the transfer
    folder — exactly this bug's setup).
    """
    d = os.path.normpath(start_dir)
    was_disc = bool(_DISC_DIR_RE.match(os.path.basename(d)))
    if not _rmdir_if_empty(d):
        return
    if was_disc:                          # the parent is the album dir — prune if now empty
        _rmdir_if_empty(os.path.dirname(d))


# Sidecar / cleanup helpers --------------------------------------------------

# Sidecars that live alongside ONE audio file (same filename stem).
_TRACK_SIDECAR_EXTS = ('.lrc', '.nfo', '.txt', '.cue', '.json')

# Album-level leftovers (cover images, .lrc, etc.) are classified by the shared
# `core.library.residual_files.is_disposable` predicate — see `_delete_album_sidecars`.

# Audio extensions used to decide whether a source directory still has
# tracks the user might care about (i.e. a per-track failure left audio
# behind that we shouldn't strip the cover art from).
_AUDIO_EXTS = frozenset(
    {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma', '.mp4'}
)


def _find_sibling_audio_files(audio_path: str) -> list:
    """Find OTHER audio files at the same source directory that share
    the canonical file's stem.

    Discord report (Foxxify): users with the lossy-copy feature
    enabled end up with `track.flac` AND `track.opus` side-by-side.
    Reorganize is DB-driven and only knows about ONE file per track
    (the lossy copy in library), so the other format gets left behind
    in the old location while the canonical moves to the new
    destination. Cleanup never fires because the source dir still has
    audio.

    This helper returns the orphan-format paths so the caller can
    move them alongside the canonical to the new destination dir.
    Same stem + audio extension + NOT the canonical itself.

    Returns empty list when source dir doesn't exist or read fails
    (defensive — never raises).
    """
    src_dir = os.path.dirname(audio_path)
    if not os.path.isdir(src_dir):
        return []
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    canonical_basename = os.path.basename(audio_path)
    siblings = []
    try:
        entries = os.listdir(src_dir)
    except OSError:
        return []
    for name in entries:
        if name == canonical_basename:
            continue
        sibling_stem, ext = os.path.splitext(name)
        if sibling_stem != stem:
            continue
        if ext.lower() not in _AUDIO_EXTS:
            continue
        full = os.path.join(src_dir, name)
        if os.path.isfile(full):
            siblings.append(full)
    return siblings


def _move_sibling_to_destination(sibling_src: str, canonical_dst: str) -> Optional[str]:
    """Move a sibling-format audio file to the same destination
    directory as the canonical, preserving its extension.

    Example: canonical at ``/library/Artist/Album/01 Track.opus`` +
    sibling source ``/old/01 Track.flac`` → destination ``/library/
    Artist/Album/01 Track.flac``. The destination filename uses the
    canonical's stem (post-template-rename) + the sibling's original
    extension — so a renamed canonical gets matching siblings.

    Returns the destination path on success, None on failure (logged
    at warning, doesn't raise — sibling moves are best-effort).
    """
    dst_dir = os.path.dirname(canonical_dst)
    canonical_stem = os.path.splitext(os.path.basename(canonical_dst))[0]
    _, sibling_ext = os.path.splitext(sibling_src)
    sibling_dst = os.path.join(dst_dir, canonical_stem + sibling_ext)
    if os.path.normpath(sibling_src) == os.path.normpath(sibling_dst):
        return sibling_dst  # already at the right place
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.move(sibling_src, sibling_dst)
        return sibling_dst
    except OSError as e:
        logger.warning(
            "[Reorganize] Couldn't move sibling-format file %s → %s: %s",
            sibling_src, sibling_dst, e,
        )
        return None
