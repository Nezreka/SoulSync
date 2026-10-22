"""Playing time of an audio file that does not trust a lying MP3 header.

``mutagen`` reads the length of a constant-bitrate MP3 from the frame count in
its Xing/Info header. Some encoders and splitters copy that header from another
file, so it can claim a few minutes for a file that holds over an hour: a
192 kbps CBR file of 102 MB announced 17,054 frames (7.4 minutes) and played for
71. Everything that sums those lengths then sees a book that is a tenth of its
real size, for example the audiobook completeness check ("Only 7 of 71 minutes
present") and the library scan.

For CBR files the audio bytes divided by the bitrate is an independent
measurement. When the header says clearly less than the file can hold, the
byte-based figure is used; in every other case mutagen's own length is returned
unchanged.
"""
from __future__ import annotations

import os
from typing import Any

# The header may be off by a little (encoder delay, a trailing tag). Only a
# difference beyond this margin counts as a lying header.
_TOLERANCE = 1.05
_SLACK_SECONDS = 2.0


def id3v2_size(path: Any) -> int:
    """Bytes taken by a leading ID3v2 tag (header included), 0 when there is none."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(10)
    except OSError:
        return 0
    if len(head) < 10 or head[:3] != b"ID3":
        return 0
    size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    footer = 10 if head[5] & 0x10 else 0
    return 10 + size + footer


def reliable_length_seconds(audio: Any, path: Any) -> float:
    """``audio.info.length``, corrected when a CBR MP3 header understates it."""
    length = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
    try:
        from mutagen.mp3 import MP3, BitrateMode

        if not isinstance(audio, MP3):
            return length
        info = audio.info
        if getattr(info, "bitrate_mode", None) != BitrateMode.CBR or not info.bitrate:
            return length
        audio_bytes = os.path.getsize(path) - id3v2_size(path)
        if audio_bytes <= 0:
            return length
        estimate = audio_bytes * 8.0 / info.bitrate
    except Exception:                                       # noqa: BLE001
        return length
    if estimate > length * _TOLERANCE + _SLACK_SECONDS:
        return estimate
    return length
