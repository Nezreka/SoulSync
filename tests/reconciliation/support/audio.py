"""Small parseable audio files and controlled fingerprint responses."""
import struct
import wave

from mutagen.flac import FLAC
from mutagen.id3 import APIC, TIT2, TPE1, TXXX, UFID
from mutagen.wave import WAVE


class _Acoustid:
    def __init__(self, matches):
        self.matches = matches
        self.fingerprints = 0
        self.lookups = 0

    def is_available(self):
        return True, ''

    def fingerprint_audio_file(self, path):
        self.fingerprints += 1
        return {'status': 'ok', 'duration_s': 200,
                'fingerprint': path.rsplit('/', 1)[-1]}

    def lookup_fingerprint_with_status(self, fingerprint, duration):
        self.lookups += 1
        match = self.matches[fingerprint]
        return {'status': 'ok', 'recordings': match if isinstance(match, list) else [match]}


def _minimal_flac(path, tags):
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0, 0, 34]) + bytes(streaminfo))
    audio = FLAC(str(path))
    for key, value in tags.items():
        audio[key] = [value]
    audio.save()


def version_pair():
    slots = [{'title': title, 'artist': 'Artist', 'artist_names': ['Artist'],
              'recording_mbid': f'recording-{i}', 'source_track_id': f'recording-{i}',
              'track_number': i, 'disc_number': 1, 'duration_ms': 200000}
             for i, title in enumerate(('Song', 'Song [DLX]'), 1)]
    files = [{'path': f"/album/{slot['track_number']:02} - {slot['title']}.flac",
              'tags': {'title': slot['title'], 'artist': 'Artist',
                       'mbid': slot['recording_mbid'], 'track_number': slot['track_number'],
                       'disc_number': 1, 'duration_ms': 200000},
              'acoustid': {'status': 'ok', 'recordings': [
                  {'mbid': 'recording-1', 'title': 'Song', 'artist': 'Artist', 'score': .99}]}}
             for slot in slots]
    return files, slots


def audio_file(path, title='Local title'):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b'\0\0' * 8000)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TPE1(encoding=3, text=['Noah Kahan', 'Hozier']))
    audio.tags.add(TXXX(encoding=3, desc='custom user tag', text=['one', 'two']))
    audio.tags.add(UFID(owner='http://musicbrainz.org', data=b'93ef0bf8-5478-43a7-ad00-1650069bd721'))
    audio.tags.add(APIC(encoding=3, mime='image/png', type=3, desc='Cover', data=b'picture-bytes'))
    audio.save()


def _file(title, path, duration_ms, recording=None):
    return {'path': path, 'tags': {'title': title, 'duration_ms': duration_ms},
            'acoustid': {'status': 'ok', 'recordings': [recording]} if recording else
                        {'status': 'no_match', 'recordings': []}}
