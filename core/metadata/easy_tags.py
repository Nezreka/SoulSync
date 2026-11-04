"""Mutagen's easy view, for the files it has no easy view for (#1919).

``mutagen.File(path, easy=True)`` maps ID3 to plain keys only for MP3. AIFF
and WAV carry the same ID3 frames, but come back with the raw ID3 object, so
``tags.get('title')`` found nothing and the scanner filed every AIFF under
Unknown Artist with its filename as the title. ``open_easy`` hands those the
same keys, read from the frames. For reading only: nothing here writes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_FRAMES = {
    'title': 'TIT2', 'artist': 'TPE1', 'albumartist': 'TPE2', 'album': 'TALB',
    'genre': 'TCON', 'date': 'TDRC', 'originaldate': 'TDOR', 'tracknumber': 'TRCK',
    'discnumber': 'TPOS', 'isrc': 'TSRC', 'bpm': 'TBPM', 'composer': 'TCOM',
    'organization': 'TPUB', 'copyright': 'TCOP',
}
# EasyID3's TXXX keys for the MusicBrainz and release fields readers ask for
_TXXX = {
    'musicbrainz_albumid': 'MusicBrainz Album Id',
    'musicbrainz_artistid': 'MusicBrainz Artist Id',
    'musicbrainz_albumartistid': 'MusicBrainz Album Artist Id',
    'musicbrainz_releasegroupid': 'MusicBrainz Release Group Id',
    'musicbrainz_releasetrackid': 'MusicBrainz Release Track Id',
    'musicbrainz_albumcomment': 'MusicBrainz Album Comment',
    'releasetype': 'MusicBrainz Album Type',
    'releasestatus': 'MusicBrainz Album Status',
    'releasecountry': 'MusicBrainz Album Release Country',
    'barcode': 'BARCODE',
}


class _EasyFrames(dict):
    """The easy keys of a raw ID3 tag: ``{key: [text, ...]}``, like EasyID3."""


def easy_frames(id3: Any) -> Dict[str, List[str]]:
    out = _EasyFrames()
    for key, frame_id in _FRAMES.items():
        frame = id3.get(frame_id)
        # TCON's .genres turns id3v1 codes ("(17)") into names (#1920)
        values = getattr(frame, 'genres' if frame_id == 'TCON' else 'text', None) or []
        texts = [str(t).strip() for t in values if str(t).strip()]
        if texts:
            out[key] = texts
    for frame in id3.getall('TXXX'):
        for key, desc in _TXXX.items():
            if getattr(frame, 'desc', '') == desc and frame.text:
                out[key] = [str(t) for t in frame.text]
    for frame in id3.getall('UFID'):
        if getattr(frame, 'owner', '') == 'http://musicbrainz.org' and frame.data:
            out['musicbrainz_trackid'] = [frame.data.decode('ascii', 'ignore')]
    return out


def open_easy(file_path: str) -> Optional[Any]:
    """``mutagen.File(file_path, easy=True)``, with an easy view of raw ID3
    tags (AIFF, WAV). Returns None when mutagen can't read the file."""
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3

    audio = MutagenFile(file_path, easy=True)
    if audio is not None and isinstance(getattr(audio, 'tags', None), ID3):
        audio.tags = easy_frames(audio.tags)
    return audio
