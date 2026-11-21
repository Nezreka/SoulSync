"""where each kind of item keeps its bytes (one module per kind).

every module exposes ``locate(...) -> core.player.playable.Source`` and
raises ``Refused`` for anything the profile may not play or that has no
file. they are only called through core.player.playable.resolve, after
the profile and section checks.
"""

import mimetypes
import os

# what browsers expect for audio a stock mimetypes table gets wrong or misses
_AUDIO = {".flac": "audio/flac", ".m4a": "audio/mp4", ".m4b": "audio/mp4", ".mp3": "audio/mpeg",
          ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
          ".aac": "audio/aac", ".alac": "audio/mp4", ".aiff": "audio/aiff", ".aif": "audio/aiff",
          ".wma": "audio/x-ms-wma", ".webm": "audio/webm"}


def audio_mime(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    return _AUDIO.get(ext) or mimetypes.guess_type(path or "")[0] or "audio/mpeg"
