"""Which musicbrainz release a file belongs to, read off its own tags.

two releases can share a title, artist and release group (#1299). the file's
MUSICBRAINZ_ALBUMID says which one it is, and MUSICBRAINZ_ALBUMCOMMENT carries
the release's disambiguation ("baby punk version"). picard and beets write that
same comment tag, so this also works on files soulsync never touched.

never raises. anything unreadable comes back as empty strings, which callers
treat as "unknown".
"""

from __future__ import annotations

from typing import Tuple

from utils.logging_config import get_logger

logger = get_logger("library.release_identity")

_ALBUM_ID = "MusicBrainz Album Id"
_ALBUM_COMMENT = "MusicBrainz Album Comment"


def _first_text(values) -> str:
    for value in values or []:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def identity_from_tags(tags) -> Tuple[str, str]:
    """``(release mbid, disambiguation)`` from a loaded mutagen tag object."""
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags

    if tags is None:
        return "", ""
    if isinstance(tags, ID3):
        found = {frame.desc: _first_text(frame.text) for frame in tags.getall("TXXX")}
        return found.get(_ALBUM_ID, ""), found.get(_ALBUM_COMMENT, "")
    if isinstance(tags, MP4Tags):
        prefix = "----:com.apple.iTunes:"
        return (_first_text(tags.get(prefix + _ALBUM_ID)),
                _first_text(tags.get(prefix + _ALBUM_COMMENT)))
    # vorbis comments (flac, ogg, opus) look keys up case-insensitively
    return (_first_text(tags.get("musicbrainz_albumid")),
            _first_text(tags.get("musicbrainz_albumcomment")))


def read_release_identity(file_path: str) -> Tuple[str, str]:
    """``(release mbid, disambiguation)`` from a file's tags, "" where absent."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(file_path)
        return identity_from_tags(getattr(audio, "tags", None) if audio is not None else None)
    except Exception as e:
        logger.debug("release identity read failed for %s: %s", file_path, e)
        return "", ""


__all__ = ["identity_from_tags", "read_release_identity"]
