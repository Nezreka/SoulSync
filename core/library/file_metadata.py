"""Read native embedded metadata for inspection without rewriting the file."""
from datetime import datetime, timezone
from pathlib import Path

import mutagen
from mutagen.id3 import Frame


def _value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {'binary_bytes': len(value)}
    if isinstance(value, (list, tuple)):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    # ID3 frames carry descriptions, languages and multiple text values in
    # addition to their printable text. Keep them all, omitting binary blobs.
    if isinstance(value, Frame):
        return {key: _value(item) for key, item in vars(value).items() if not key.startswith('_')}
    return str(value)


def read_file_metadata(path: str) -> dict:
    audio = mutagen.File(path, easy=False)
    if audio is None:
        raise ValueError('The file format could not be read')
    info = audio.info
    result = {
        'File': Path(path).name,
        'Folder': str(Path(path).parent),
        'Format': type(audio).__name__,
        'Read at': datetime.now(timezone.utc).isoformat(),
    }
    for attribute, name in (('length', 'Duration (seconds)'), ('bitrate', 'Bitrate (bits/second)'),
                            ('sample_rate', 'Sample rate (Hz)'), ('channels', 'Channels'),
                            ('bits_per_sample', 'Bits per sample'), ('codec', 'Codec')):
        value = getattr(info, attribute, None)
        if value is not None:
            result[name] = _value(value)
    result.update({f'Tag: {key}': _value(value) for key, value in (audio.tags or {}).items()})
    pictures = getattr(audio, 'pictures', None)
    if pictures:
        result['Embedded pictures'] = [
            {key: _value(getattr(picture, key, None))
             for key in ('type', 'mime', 'desc', 'width', 'height', 'depth', 'data')}
            for picture in pictures]
    return result
