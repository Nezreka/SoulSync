"""Small parseable audio files and controlled fingerprint responses."""
import struct
from mutagen.flac import FLAC

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

