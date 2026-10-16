"""keep a download from landing untagged when enrichment fails.

enhance_file_metadata clears every tag and saves before it builds the new
ones. a crash in between (an int album id hitting .strip(), oct 2026) left
files with no title/artist/album, and the media server filed them under
Unknown Album even though the folders were right. when enrichment fails,
this writes the basics back from the same data the folder path came from.
"""

from __future__ import annotations

from typing import Any, Dict, List

from utils.logging_config import get_logger

logger = get_logger("metadata.core_tag_guard")

CORE_FIELDS = ('title', 'artist', 'album')

# never write these as a fallback, a blank tag is easier to spot and repair
_PLACEHOLDERS = {'', 'unknown', 'unknown artist', 'unknown album', 'unknown track'}


def _real(value: Any) -> str:
    text = str(value or '').strip()
    return '' if text.strip('[]').strip().casefold() in _PLACEHOLDERS else text


def missing_core_tags(file_path: str) -> List[str]:
    """the core fields the file has no value for. empty when the file can't be read."""
    from core.tag_writer import read_file_tags
    tags = read_file_tags(file_path)
    if tags.get('error'):
        return []
    return [f for f in CORE_FIELDS if not str(tags.get(f) or '').strip()]


def fallback_core_tags(context: dict, artist: Any, album_info: dict) -> Dict[str, Any]:
    """title/artist/album/track/disc from the import context, in write_tags_to_file's shape.

    plain field reads only, nothing that can go wrong the way enrichment did."""
    from core.imports.context import extract_artist_name, get_import_clean_title

    album_info = album_info if isinstance(album_info, dict) else {}
    data: Dict[str, Any] = {}

    try:
        title = _real(get_import_clean_title(context, album_info=album_info, default=''))
    except Exception:  # noqa: BLE001 - a fallback must not raise
        title = ''
    if title:
        data['title'] = title

    try:
        name = artist.get('name') if isinstance(artist, dict) else extract_artist_name(artist)
    except Exception:  # noqa: BLE001
        name = ''
    name = _real(name)
    if name:
        data['artist_name'] = name

    album = _real(album_info.get('album_name'))
    if album:
        data['album_title'] = album

    for key in ('track_number', 'disc_number'):
        try:
            num = int(album_info.get(key) or 0)
        except (TypeError, ValueError):
            num = 0
        if num > 0:
            data[key] = num
    return data


def ensure_core_tags(file_path: str, context: dict, artist: Any, album_info: dict) -> List[str]:
    """after a failed enrichment: write fallback tags if title/artist/album are gone.

    only fills what's missing, so a file that kept its tags is left alone.
    returns the fields written."""
    try:
        missing = missing_core_tags(file_path)
        if not missing:
            return []
        data = fallback_core_tags(context, artist, album_info)
        if not data:
            logger.error("file left with no %s and nothing to fall back on: %s",
                         '/'.join(missing), file_path)
            return []
        from core.tag_writer import write_tags_to_file
        result = write_tags_to_file(file_path, data, embed_cover=False)
        if not result.get('success'):
            logger.error("fallback tag write failed for %s: %s", file_path, result.get('error'))
            return []
        written = result.get('written_fields') or []
        logger.error("enrichment failed, wrote fallback tags (%s) to %s",
                     ', '.join(written), file_path)
        return written
    except Exception as exc:  # noqa: BLE001 - never mask the original failure
        logger.error("fallback tag guard failed for %s: %s", file_path, exc)
        return []
