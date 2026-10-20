"""playlists the user makes inside soulsync.

a user playlist is a mirrored playlist with no upstream: source 'soulsync',
a random source_playlist_id, owned by the profile that made it. everything
else (identify, sync to the server, auto-sync, download missing) is the
mirrored machinery as-is, so there is no second playlist system to keep
in step.

tracks are just artist + title, like a listenbrainz list. album and
duration ride along when the page had them because identify uses them to
pick the right version.

every edit rewrites the whole track list through mirror_playlist. that path
keeps each track's discovery data (keyed on source_track_id), so a match
survives an add, a remove or a reorder. the lock keeps two quick adds from
reading the same list and one of them losing its track.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("playlists.user_playlists")

USER_PLAYLIST_SOURCE = 'soulsync'

# a profile's Liked Songs is a user playlist with this fixed source id, so
# there is one per profile and it can always be found again. it is made the
# first time something is liked.
LIKED_SOURCE_ID = 'liked'
LIKED_NAME = 'Liked Songs'

# sources with nothing upstream to pull from. refresh skips them; the
# pipeline still runs identify / sync / download on them. beatport isn't one:
# a beatport mirror keyed by its list ref refreshes like any other source,
# only an old random-hash mirror or a release doesn't (has_upstream).
NO_UPSTREAM_SOURCES = ('file', USER_PLAYLIST_SOURCE)

# sources the pipeline won't run at all (it 400s them). user playlists are
# NOT in here, they get the whole pipeline. beatport is decided per mirror
# (pipeline_runs): a live list gets the whole pipeline, an old random-hash
# mirror stays out like it always was, so an all-playlists run never starts
# syncing and downloading a chart frozen months ago.
PIPELINE_SKIPPED_SOURCES = ('file',)


def has_upstream(playlist) -> bool:
    """whether a mirror has a source refresh can pull it from again."""
    source = (playlist or {}).get('source', '')
    if source in NO_UPSTREAM_SOURCES:
        return False
    if source == 'beatport':
        from core.playlists.sources.beatport import is_refreshable_mirror
        return is_refreshable_mirror(playlist)
    return True


def pipeline_runs(playlist) -> bool:
    """whether the playlist pipeline takes this mirror at all."""
    source = (playlist or {}).get('source', '')
    if source in PIPELINE_SKIPPED_SOURCES:
        return False
    if source == 'beatport':
        # a live list, or a dated snapshot you chose to keep: it never
        # refreshes, but identify / sync / download are why you took it
        from core.playlists.sources.beatport import is_refreshable_mirror, is_snapshot_mirror
        return is_refreshable_mirror(playlist) or is_snapshot_mirror(playlist)
    return True


MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 1000
MAX_COVER_URL_LENGTH = 2000

# uploaded covers are re-encoded to a square webp, like profile avatars:
# nothing but pixels is kept (no metadata, no svg, no scripts).
COVER_MAX_BYTES = 3 * 1024 * 1024
COVER_SIDE = 640

_edit_lock = threading.Lock()


class UserPlaylistError(ValueError):
    """a bad request: the message is safe to show the user."""


def is_user_playlist(playlist: Optional[Dict[str, Any]]) -> bool:
    return bool(playlist) and playlist.get('source') == USER_PLAYLIST_SOURCE


def is_liked_playlist(playlist: Optional[Dict[str, Any]]) -> bool:
    return is_user_playlist(playlist) and playlist.get('source_playlist_id') == LIKED_SOURCE_ID


def clean_name(name: Any) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise UserPlaylistError('Give the playlist a name')
    return cleaned[:MAX_NAME_LENGTH]


def clean_track(raw: Any) -> Optional[Dict[str, Any]]:
    """artist + title, plus album / duration / art when present. None when
    there's no title or no artist, since identify can't do anything with it."""
    if not isinstance(raw, dict):
        return None
    title = ' '.join(str(raw.get('track_name') or raw.get('title') or raw.get('name') or '').split())
    artist = ' '.join(str(raw.get('artist_name') or raw.get('artist') or '').split())
    if not title or not artist:
        return None
    album = ' '.join(str(raw.get('album_name') or raw.get('album') or '').split())
    try:
        duration_ms = max(0, int(raw.get('duration_ms') or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    image = raw.get('image_url')
    track = {
        'track_name': title,
        'artist_name': artist,
        'album_name': album,
        'duration_ms': duration_ms,
        'image_url': image if isinstance(image, str) and image.startswith(('http://', 'https://', '/')) else None,
    }
    # where the page got it, when it knows: identify fetches that exact track
    # by id instead of searching for it (core/playlists/track_origin)
    from core.playlists.track_origin import clean_origin
    origin = clean_origin(raw.get('source'), raw.get('source_id'))
    if origin:
        track['extra_data'] = {'origin': origin}
    return track


_BRACKETED = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]')
_DASH_SUFFIX = re.compile(r'\s+-\s+.*$')
_FEAT = re.compile(r'\s+(feat\.?|ft\.?|featuring|with)\s+.*$', re.I)
_ARTIST_SPLIT = re.compile(r'\s*(,|&|\bx\b|\band\b|;|/)\s*', re.I)
_NON_WORD = re.compile(r'[^\w\s]')


def _plain(text: str) -> str:
    folded = unicodedata.normalize('NFKD', text)
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    return ' '.join(_NON_WORD.sub(' ', folded.lower()).split())


def track_key(track: Dict[str, Any]) -> Tuple[str, str]:
    """what counts as LIKELY the same song for the already-in-this-playlist
    check. loose on purpose: "Alright", "Alright (Remastered 2015)" and
    "Alright - Radio Edit" by "Kendrick Lamar, Zacari" all land on one key.
    the user is asked, never blocked, so a false match costs a click."""
    title = str(track.get('track_name') or '')
    stripped = _FEAT.sub('', _DASH_SUFFIX.sub('', _BRACKETED.sub('', title)))
    # a title that is ALL brackets ("(Interlude)") keeps them rather than
    # collapsing to nothing and matching every other such title
    title_key = _plain(stripped) or _plain(title)
    artist = str(track.get('artist_name') or '')
    main = _ARTIST_SPLIT.split(_FEAT.sub('', artist))[0]
    artist_key = _plain(main) or _plain(artist)
    if artist_key.startswith('the '):
        artist_key = artist_key[4:]
    return (artist_key, title_key)


def _stored_tracks(db: Any, playlist_id: int) -> List[Dict[str, Any]]:
    """the current rows in mirror_playlist's input shape, discovery data kept."""
    out = []
    for row in db.get_mirrored_playlist_tracks(playlist_id):
        extra = row.get('extra_data')
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = None
        out.append({
            'track_name': row.get('track_name') or '',
            'artist_name': row.get('artist_name') or '',
            'album_name': row.get('album_name') or '',
            'duration_ms': row.get('duration_ms') or 0,
            'image_url': row.get('image_url'),
            'source_track_id': row.get('source_track_id'),
            'extra_data': extra or None,
            # carried explicitly so a rewrite keeps each track's date added,
            # even a duplicate that shares another row's source_track_id
            'added_at': row.get('added_at'),
        })
    return out


def _write(
    db: Any,
    playlist: Dict[str, Any],
    tracks: List[Dict[str, Any]],
    *,
    image_url: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    # the mirror upsert keeps the row's image_url / description when these are
    # not passed, so a cover or description set on the playlist survives every
    # add, remove and reorder. duplicate_playlist passes them for the new row.
    saved = db.mirror_playlist(
        source=USER_PLAYLIST_SOURCE,
        source_playlist_id=playlist['source_playlist_id'],
        name=playlist['name'],
        tracks=tracks,
        profile_id=int(playlist.get('profile_id') or 1),
        image_url=image_url,
        description=description,
    )
    if saved is None:
        raise RuntimeError('could not save the playlist')


def create_playlist(db: Any, profile_id: int, name: Any, is_request_list: bool = False) -> int:
    cleaned = clean_name(name)
    playlist_id = db.mirror_playlist(
        source=USER_PLAYLIST_SOURCE,
        source_playlist_id=uuid.uuid4().hex,
        name=cleaned,
        tracks=[],
        profile_id=int(profile_id),
        is_request_list=1 if is_request_list else 0,
    )
    if playlist_id is None:
        raise RuntimeError('could not create the playlist')
    kind = 'request list' if is_request_list else 'user playlist'
    logger.info("created %s '%s' (%s) for profile %s", kind, cleaned, playlist_id, profile_id)
    return int(playlist_id)


def list_playlists(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    """this profile's user playlists, newest edit first, for the picker."""
    from core.playlists.naming import effective_mirrored_name
    rows = [p for p in db.get_mirrored_playlists(profile_id=int(profile_id)) if is_user_playlist(p)]
    rows.sort(key=lambda p: str(p.get('updated_at') or p.get('mirrored_at') or ''), reverse=True)
    # Liked Songs is pinned first, like every music app pins it
    rows.sort(key=lambda p: not is_liked_playlist(p))
    return [{
        'id': p['id'],
        'name': effective_mirrored_name(p),
        'track_count': int(p.get('track_count') or 0),
        'image_url': p.get('image_url'),
        # the collage the playlists page falls back to when there's no cover of
        # its own. without it the picker drew a letter where the page drew art.
        'cover_tiles': _cover_tiles(p.get('cover_tiles')),
        'liked': is_liked_playlist(p),
    } for p in rows]


def _cover_tiles(raw: Any) -> List[str]:
    """the cover_tiles column (json text, or NULL until the backfill runs)."""
    if isinstance(raw, list):
        tiles = raw
    else:
        try:
            tiles = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []
    return [t for t in tiles if isinstance(t, str) and t] if isinstance(tiles, list) else []


def add_tracks(
    db: Any,
    playlist: Dict[str, Any],
    raw_tracks: Iterable[Any],
    *,
    allow_duplicates: bool = False,
    prepend: bool = False,
) -> Dict[str, Any]:
    """append tracks. a song already in the playlist is skipped and reported
    unless allow_duplicates, so the ui can ask "add anyway?". prepend puts
    them at the top instead (Liked Songs: newest first)."""
    incoming = [t for t in (clean_track(r) for r in raw_tracks or []) if t]
    if not incoming:
        raise UserPlaylistError('No track to add')
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        # key -> the copy already there, so the prompt can name it
        have = {track_key(t): t for t in current}
        added: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []
        for track in incoming:
            key = track_key(track)
            if key in have and not allow_duplicates:
                duplicates.append({'track': track, 'existing': have[key]})
                continue
            have.setdefault(key, track)
            added.append(track)
        if added:
            _write(db, playlist, added + current if prepend else current + added)
    return {
        'added': len(added),
        'duplicates': [{
            'track_name': d['track']['track_name'],
            'artist_name': d['track']['artist_name'],
            # the copy it matched, which may be spelled differently
            'existing_track_name': d['existing']['track_name'],
            'existing_artist_name': d['existing']['artist_name'],
        } for d in duplicates],
        'track_count': len(current) + len(added),
    }


def remove_track(db: Any, playlist: Dict[str, Any], position: int) -> int:
    """drop the track at a 1-based position; returns the new count."""
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        if not 1 <= int(position) <= len(current):
            raise UserPlaylistError('That track is not in the playlist')
        del current[int(position) - 1]
        _write(db, playlist, current)
        return len(current)


def reorder_tracks(db: Any, playlist: Dict[str, Any], order: Iterable[Any]) -> None:
    """order is every current 1-based position, each once, in the new order.
    a stale list (someone added a track since) is refused, not guessed at."""
    try:
        positions = [int(p) for p in order]
    except (TypeError, ValueError):
        raise UserPlaylistError('Bad track order') from None
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        if sorted(positions) != list(range(1, len(current) + 1)):
            raise UserPlaylistError('The playlist changed, reload it and try again')
        _write(db, playlist, [current[p - 1] for p in positions])


def remove_matching(db: Any, playlist: Dict[str, Any], raw_tracks: Iterable[Any]) -> Dict[str, Any]:
    """drop every row that is LIKELY one of these songs (track_key), for the
    picker's untick and for unliking. returns how many went."""
    keys = {track_key(t) for t in (clean_track(r) for r in raw_tracks or []) if t}
    if not keys:
        raise UserPlaylistError('No track to remove')
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        kept = [t for t in current if track_key(t) not in keys]
        removed = len(current) - len(kept)
        if removed:
            _write(db, playlist, kept)
    return {'removed': removed, 'track_count': len(kept)}


def membership(db: Any, profile_id: int, raw_tracks: Iterable[Any]) -> Dict[str, Any]:
    """for the picker's ticks: how many of these songs each of the profile's
    playlists already has. only playlists that have at least one are listed."""
    keys = {track_key(t) for t in (clean_track(r) for r in raw_tracks or []) if t}
    counts: Dict[int, int] = {}
    if keys:
        for p in db.get_mirrored_playlists(profile_id=int(profile_id)):
            if not is_user_playlist(p):
                continue
            have = {track_key(t) for t in _stored_tracks(db, p['id'])}
            hit = len(keys & have)
            if hit:
                counts[int(p['id'])] = hit
    return {'total': len(keys), 'playlists': counts}


def liked_playlist(db: Any, profile_id: int, *, create: bool = False) -> Optional[Dict[str, Any]]:
    """the profile's Liked Songs, made on first use when create is set."""
    for p in db.get_mirrored_playlists(profile_id=int(profile_id)):
        if is_liked_playlist(p):
            return p
    if not create:
        return None
    playlist_id = db.mirror_playlist(
        source=USER_PLAYLIST_SOURCE,
        source_playlist_id=LIKED_SOURCE_ID,
        name=LIKED_NAME,
        tracks=[],
        profile_id=int(profile_id),
        description='Songs you liked, newest first.',
    )
    if playlist_id is None:
        raise RuntimeError('could not make Liked Songs')
    logger.info("made Liked Songs (%s) for profile %s", playlist_id, profile_id)
    return db.get_mirrored_playlist(playlist_id, profile_id=int(profile_id))


def like(db: Any, profile_id: int, raw_tracks: Iterable[Any]) -> Dict[str, Any]:
    """heart these songs. one already liked stays where it is."""
    playlist = liked_playlist(db, profile_id, create=True)
    result = add_tracks(db, playlist, raw_tracks, prepend=True)
    return {'id': int(playlist['id']), 'added': result['added'], 'track_count': result['track_count']}


def unlike(db: Any, profile_id: int, raw_tracks: Iterable[Any]) -> Dict[str, Any]:
    playlist = liked_playlist(db, profile_id)
    if not playlist:
        return {'id': None, 'removed': 0, 'track_count': 0}
    result = remove_matching(db, playlist, raw_tracks)
    return {'id': int(playlist['id']), **result}


def copy_mirror(db: Any, mirror: Dict[str, Any], profile_id: int, name: Any = None) -> Dict[str, Any]:
    """make a mirrored playlist one of yours: a new user playlist with its
    tracks (identify matches and dates added kept), cover and description.
    the mirror stays as it is, still syncing from its source."""
    from core.playlists.naming import effective_mirrored_name
    new_name = clean_name(name if name is not None else effective_mirrored_name(mirror) or 'Playlist')
    image = mirror.get('image_url')
    # a remote poster copies over; anything else (a media-server path) would
    # not load as a cover, and the collage covers it anyway
    image = image if isinstance(image, str) and image.startswith(('http://', 'https://')) else None
    with _edit_lock:
        new_id = create_playlist(db, int(profile_id), new_name)
        new_playlist = db.get_mirrored_playlist(new_id, profile_id=int(profile_id))
        if not new_playlist:
            raise RuntimeError('could not copy the playlist')
        _write(
            db, new_playlist, _stored_tracks(db, mirror['id']),
            image_url=image,
            description=mirror.get('description'),
        )
    logger.info("copied mirror '%s' (%s) into user playlist '%s' (%s)",
                mirror.get('name'), mirror['id'], new_name, new_id)
    return {'id': int(new_id), 'name': new_name}


# ── cover, description, duplicate ─────────────────────────────────────────


def cover_dir(base_dir: Optional[str] = None, *, mkdir: bool = True) -> str:
    """where uploaded playlist covers live. the env var is the docker escape
    hatch; base_dir is soulsync's data dir in production."""
    d = os.environ.get("SOULSYNC_PLAYLIST_COVER_DIR")
    if not d:
        d = os.path.join(str(base_dir) if base_dir else ".", "storage", "playlist_covers")
    if mkdir:
        os.makedirs(d, exist_ok=True)
    return d


def cover_path(playlist_id: int, base_dir: Optional[str] = None, *, mkdir: bool = True) -> str:
    # fixed name, no user input in the path: nothing to traverse.
    return os.path.join(cover_dir(base_dir, mkdir=mkdir), f"user-{int(playlist_id)}.webp")


def cover_url(playlist_id: int) -> str:
    # cache-buster so a re-uploaded cover shows up without a hard refresh.
    return f"/api/user-playlists/{int(playlist_id)}/cover?v={int(time.time())}"


def _encode_cover(raw: bytes) -> bytes:
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = ImageOps.fit(img.convert("RGBA"), (COVER_SIDE, COVER_SIDE), method=Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "WEBP", quality=88)
        return out.getvalue()
    except Exception:
        raise UserPlaylistError("That file isn't an image we can read") from None


def set_cover_upload(db: Any, playlist: Dict[str, Any], file_storage: Any,
                     *, base_dir: Optional[str] = None) -> str:
    """multipart upload -> square webp on disk, row points at the serve endpoint."""
    raw = file_storage.read(COVER_MAX_BYTES + 1)
    if len(raw) > COVER_MAX_BYTES:
        raise UserPlaylistError("That image is over 3 MB")
    if not raw:
        raise UserPlaylistError("No image")
    data = _encode_cover(raw)
    path = cover_path(playlist['id'], base_dir)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    url = cover_url(playlist['id'])
    if not db.set_mirrored_playlist_image_url(
        playlist['id'], url, profile_id=playlist.get('profile_id'),
    ):
        raise RuntimeError('could not save the cover')
    return url


def set_cover_url(db: Any, playlist: Dict[str, Any], url: Any) -> str:
    """point the cover at a remote image. http(s) only: nothing local, nothing
    scriptable, and the <img> tag was never going to load it anyway."""
    cleaned = str(url or '').strip()
    if len(cleaned) > MAX_COVER_URL_LENGTH:
        raise UserPlaylistError('That URL is too long')
    if not cleaned.startswith(('http://', 'https://')):
        raise UserPlaylistError('Cover needs an http(s) image URL')
    if not db.set_mirrored_playlist_image_url(
        playlist['id'], cleaned, profile_id=playlist.get('profile_id'),
    ):
        raise RuntimeError('could not save the cover')
    return cleaned


def set_cover_from_track(db: Any, playlist: Dict[str, Any], position: Any) -> str:
    """borrow a track's artwork for the cover. position is 1-based, as the
    detail payload numbers them."""
    try:
        pos = int(position)
    except (TypeError, ValueError):
        raise UserPlaylistError('Pick a track from the playlist') from None
    tracks = _stored_tracks(db, playlist['id'])
    if not 1 <= pos <= len(tracks):
        raise UserPlaylistError('Pick a track from the playlist')
    art = tracks[pos - 1].get('image_url')
    if not art:
        raise UserPlaylistError('That track has no artwork')
    if not db.set_mirrored_playlist_image_url(
        playlist['id'], art, profile_id=playlist.get('profile_id'),
    ):
        raise RuntimeError('could not save the cover')
    return art


def remove_cover_file(playlist_id: int, base_dir: Optional[str] = None) -> None:
    """best-effort delete of an uploaded cover file. only user playlists ever
    have one, so after any mirror delete this is a no-op for the rest."""
    try:
        os.remove(cover_path(playlist_id, base_dir))
    except OSError:
        pass


def clear_cover(db: Any, playlist: Dict[str, Any], *, base_dir: Optional[str] = None) -> None:
    """back to no cover: the views fall back to track art. the uploaded file,
    if there ever was one, is deleted best-effort."""
    try:
        os.remove(cover_path(playlist['id'], base_dir))
    except OSError:
        pass
    if not db.set_mirrored_playlist_image_url(
        playlist['id'], None, profile_id=playlist.get('profile_id'),
    ):
        raise RuntimeError('could not clear the cover')


def set_description(db: Any, playlist: Dict[str, Any], description: Any) -> str:
    """one short blurb on the playlist. blank clears it."""
    cleaned = re.sub(r'\n{3,}', '\n\n', str(description or '').strip())[:MAX_DESCRIPTION_LENGTH]
    if not db.set_mirrored_playlist_description(
        playlist['id'], cleaned or None, profile_id=playlist.get('profile_id'),
    ):
        raise RuntimeError('could not save the description')
    return cleaned


def duplicate_playlist(db: Any, playlist: Dict[str, Any], name: Any = None,
                       *, base_dir: Optional[str] = None) -> Dict[str, Any]:
    """a full copy under a new id: tracks (with their identify matches),
    cover and description. the copy is independent from here on."""
    if name is not None:
        new_name = clean_name(name)
    else:
        new_name = str(playlist.get('name') or 'Playlist')
        if not new_name.endswith(' (copy)'):
            new_name = f"{new_name} (copy)"
        new_name = ' '.join(new_name.split())[:MAX_NAME_LENGTH]
    with _edit_lock:
        new_id = create_playlist(db, int(playlist.get('profile_id') or 1), new_name)
        new_playlist = db.get_mirrored_playlist(new_id, profile_id=playlist.get('profile_id'))
        if not new_playlist:
            raise RuntimeError('could not copy the playlist')
        # an uploaded cover points at the old playlist's serve endpoint, so
        # the copy gets its own file; a remote URL or track art is just copied.
        src_image = playlist.get('image_url')
        new_image_url = src_image
        old_prefix = f"/api/user-playlists/{int(playlist['id'])}/cover"
        if src_image and src_image.startswith(old_prefix):
            src_file = cover_path(playlist['id'], base_dir)
            if os.path.isfile(src_file):
                shutil.copyfile(src_file, cover_path(new_id, base_dir))
                new_image_url = cover_url(new_id)
            else:
                new_image_url = None  # file's gone; don't point the copy at a 404
        _write(
            db, new_playlist, _stored_tracks(db, playlist['id']),
            image_url=new_image_url,
            description=playlist.get('description'),
        )
    logger.info("duplicated user playlist '%s' (%s) -> '%s' (%s)",
                playlist.get('name'), playlist['id'], new_name, new_id)
    return {'id': int(new_id), 'name': new_name}
