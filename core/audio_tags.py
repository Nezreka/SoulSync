"""Read-only normalization for containers without Mutagen's easy tag interface."""

from typing import Any


def easy_tags(audio: Any):
    """Return easy-style tags, including ID3 embedded in AIFF and WAV.

    ``mutagen.File(..., easy=True)`` still returns raw ID3 for these
    containers. Keep other formats' tag mappings unchanged, and never
    modify the loaded audio object or write to the source file.
    """
    from mutagen.id3 import ID3

    tags = getattr(audio, 'tags', audio)
    if not isinstance(tags, ID3):
        return tags or {}

    frames = {
        'title': 'TIT2', 'artist': 'TPE1', 'albumartist': 'TPE2',
        'album': 'TALB', 'date': 'TDRC', 'genre': 'TCON',
        'tracknumber': 'TRCK', 'discnumber': 'TPOS', 'isrc': 'TSRC',
        'musicbrainz_albumid': 'TXXX:MusicBrainz Album Id',
        'musicbrainz_albumcomment': 'TXXX:MusicBrainz Album Comment',
        'musicbrainz_trackid': 'TXXX:MusicBrainz Track Id',
    }
    result = {}
    for key, frame_id in frames.items():
        frame = tags.get(frame_id)
        values = getattr(frame, 'genres' if key == 'genre' else 'text', None)
        if not isinstance(values, (list, tuple)):
            continue
        # Missing/empty/malformed frames must not prevent the remaining
        # fields (or stream duration/bitrate) from being read.
        text = [str(value).strip() for value in values if value is not None]
        text = [value for value in text if value]
        if text:
            result[key] = text

    # Picard stores the recording ID in UFID rather than a text frame.
    ufid = tags.get('UFID:http://musicbrainz.org')
    data = getattr(ufid, 'data', b'')
    if isinstance(data, bytes):
        recording_id = data.decode('ascii', errors='ignore').strip()
        if recording_id:
            result['musicbrainz_trackid'] = [recording_id]
    return result
