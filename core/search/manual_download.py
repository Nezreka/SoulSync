"""Tag it yourself: the user typed the release, build the tasks from that.

no metadata provider is involved. every id is empty and there is no `source`,
so nothing downstream can embed a fake id or look the release up by id. the
`_manual_metadata` flag on track_info is what tells tagging, cover art,
acoustid and the enrichment lock to stand down (core/metadata/manual.py).
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("search.manual_download")

# what SoulSync's album_type understands. live is an album that happens to be live
_TYPE_TO_ALBUM_TYPE = {'album': 'album', 'live': 'album', 'ep': 'ep', 'single': 'single', 'compilation': 'compilation'}

MAX_COVER_BYTES = 12 * 1024 * 1024
_DATA_URL = re.compile(r'^data:(image/(?:jpeg|jpg|png|webp));base64,(.+)$', re.S)


class ManualInputError(ValueError):
    """something the user typed can't be used. the message is shown to them"""


def _clean(value: Any, limit: int = 300) -> str:
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def save_cover(data_url: str, *, directory: Optional[str] = None) -> str:
    """an uploaded cover (a data: url) written to disk for post-processing to
    read when the download lands. returns the path"""
    match = _DATA_URL.match(data_url or '')
    if not match:
        raise ManualInputError("That cover isn't a JPEG, PNG or WebP image.")
    try:
        data = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ManualInputError("That cover couldn't be read.") from exc
    if not data:
        raise ManualInputError("That cover is empty.")
    if len(data) > MAX_COVER_BYTES:
        raise ManualInputError('That cover is over 12 MB. Try a smaller one.')
    ext = {'image/png': '.png', 'image/webp': '.webp'}.get(match.group(1), '.jpg')
    directory = directory or os.path.join(tempfile.gettempdir(), 'soulsync_manual_covers')
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f'{uuid.uuid4().hex}{ext}')
    with open(path, 'wb') as handle:
        handle.write(data)
    return path


def build_manual_tasks(
    files: List[Dict[str, Any]],
    album: Dict[str, Any],
    tracks: List[Dict[str, Any]],
    *,
    cover_path: Optional[str] = None,
    file_key,
) -> Tuple[Dict[str, Any], List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
    """(album_context, [(file, track_info)]) from what the user typed.
    raises ManualInputError with a sentence for the user when it can't."""
    name = _clean(album.get('name'))
    artist = _clean(album.get('artist'))
    if not name:
        raise ManualInputError('Give the album a name.')
    if not artist:
        raise ManualInputError('Who made it? Add the album artist.')

    by_key = {file_key(f): f for f in files}
    rows = []
    for row in tracks:
        file = by_key.get(row.get('file_key'))
        if file is None:
            continue
        title = _clean(row.get('title'))
        if not title:
            raise ManualInputError('Every track needs a title.')
        rows.append((file, title, max(1, int(row.get('track_number') or 1)),
                     max(1, int(row.get('disc_number') or 1)),
                     _clean(row.get('artist')) or artist))
    if not rows:
        raise ManualInputError('No files to download.')

    kind = _clean(album.get('type')).lower() or 'album'
    image_url = _clean(album.get('image_url'), 2000)
    if image_url and not image_url.lower().startswith(('http://', 'https://')):
        raise ManualInputError('The cover link has to start with http:// or https://.')
    genre = _clean(album.get('genre'), 100)

    album_ctx = {
        'id': '',
        'name': name,
        'release_date': _clean(album.get('date'), 20),
        'image_url': image_url,
        'images': [{'url': image_url}] if image_url else [],
        'album_type': _TYPE_TO_ALBUM_TYPE.get(kind, 'album'),
        'total_tracks': len(rows),
        'total_discs': max(r[3] for r in rows),
        'artists': [{'name': artist}],
    }
    artist_ctx = {'name': artist, 'id': '', 'genres': [genre] if genre else []}

    out = []
    for file, title, number, disc, track_artist in rows:
        info = {
            'id': '',
            'name': title,
            'artists': [{'name': track_artist}],
            'album': album_ctx,
            'duration_ms': file.get('duration') or 0,
            'track_number': number,
            'disc_number': disc,
            # always an album context: it files the tracks under the name the
            # user typed instead of guessing an album from the tags
            '_is_explicit_album_download': True,
            '_explicit_album_context': album_ctx,
            '_explicit_artist_context': artist_ctx,
            '_manual_metadata': True,
        }
        if cover_path:
            info['_manual_cover_path'] = cover_path
        out.append((file, info))
    return album_ctx, out
