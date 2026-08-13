"""Can a BROWSER play this library file as-is?

Movie night streams the library file straight to a ``<video>`` element — no
transcoding, deliberately (v1 scope). That makes codec honesty the whole design
constraint: a party that silently plays nothing for half the room is worse than
one that says "your copy of this is HEVC + AC3, which browsers won't play"
before anybody clicks.

The scanner already stores everything needed to answer that — ``media_files``
carries ``video_codec`` and ``audio_codec``, and the container is the file's own
extension. Boulder's library is a good illustration of why the answer matters:
82,752 .mkv against 22,718 .mp4, and 42,000+ files carrying AC3/E-AC3 audio that
Chrome and Firefox refuse.

Three verdicts, because two would be a lie:
  - ``yes``   every part is something browsers play essentially everywhere
  - ``maybe`` it usually works but depends on the browser (Matroska containers,
              HEVC on Apple hardware, AC3 on Edge/Safari) — offer it, warn, and
              let the ``<video>`` element be the final judge
  - ``no``    a part no mainstream browser decodes; don't pretend

Pure (no DB, no filesystem, no network) so the rules are unit-tested directly,
and isolated — it imports nothing, from either side of the app.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

# Containers browsers demux natively. Matroska is the awkward one: Chromium
# plays .mkv when the streams inside are ones it knows, Firefox increasingly
# does, Safari does not — so it is a 'maybe' rather than a yes or a no, and the
# codecs inside still have to pass on their own.
_CONTAINER = {
    ".mp4": "yes", ".m4v": "yes", ".webm": "yes", ".ogv": "yes", ".ogg": "yes",
    ".mkv": "maybe", ".mov": "maybe",
}

# Video codecs. h264 is universal; vp8/vp9/av1 are broadly supported now; HEVC
# only decodes where the hardware and the OS both cooperate (Safari on Apple
# silicon, some Edge builds). The MPEG-4 Part 2 family (DivX/Xvid era .avi) is
# nowhere — those are the files that would fail silently.
_VIDEO = {
    "h264": "yes", "avc": "yes", "avc1": "yes", "x264": "yes",
    "vp8": "yes", "vp9": "yes", "av1": "yes",
    "hevc": "maybe", "h265": "maybe", "x265": "maybe",
    "mpeg4": "no", "msmpeg4v3": "no", "msmpeg4": "no", "divx": "no", "xvid": "no",
    "mpeg2video": "no", "mpeg1video": "no", "vc1": "no", "wmv3": "no", "theora": "maybe",
}

# Audio codecs. AC3/E-AC3 is the big one — Dolby's licence keeps it out of
# Chrome and Firefox, and it is the single most common reason a perfectly good
# 1080p WEB-DL plays as a silent picture.
_AUDIO = {
    "aac": "yes", "mp3": "yes", "opus": "yes", "vorbis": "yes", "flac": "yes",
    "ac3": "no", "eac3": "no", "dts": "no", "truehd": "no", "dtshd": "no",
    "pcm": "maybe", "alac": "maybe", "mp2": "maybe", "wmav2": "no",
}

_ORDER = {"yes": 0, "maybe": 1, "no": 2}


def _norm(value: Any) -> str:
    """Codec names arrive in several dialects across servers and probes
    ('H.264', 'V_MPEG4/ISO/AVC', 'x265'). Fold to bare alphanumerics and take
    the recognisable stem."""
    s = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    if not s:
        return ""
    # ORDER MATTERS, and the trap is Matroska's own codec ids: 'V_MPEG4/ISO/AVC'
    # is H.264, while 'V_MPEG4/ISO/ASP' is the DivX-era MPEG-4 Part 2. Both
    # contain 'mpeg4', so the modern stems have to be tested FIRST or every
    # Matroska H.264 file — the single most common shape in a real library —
    # reads as an unplayable codec.
    for known in ("hevc", "h265", "x265", "h264", "avc1", "avc", "x264",
                  "vp9", "vp8", "av1",
                  "msmpeg4v3", "mpeg4", "mpeg2video", "mpeg1video",
                  "eac3", "ac3", "aac", "mp3", "mp2", "opus", "vorbis", "flac",
                  "truehd", "dtshd", "dts", "alac", "pcm", "wmav2", "wmv3",
                  "vc1", "divx", "xvid", "theora"):
        if known in s:
            return known
    return s


def container_of(path: Any) -> str:
    """The file's container as a lowercase extension ('.mkv'), or ''."""
    return os.path.splitext(str(path or ""))[1].lower()


def direct_play_verdict(path: Any = None, video_codec: Any = None,
                        audio_codec: Any = None) -> Dict[str, Any]:
    """Judge one library file for in-browser playback.

    Returns ``{"verdict": "yes"|"maybe"|"no", "reasons": [...], "container": str,
    "video": str, "audio": str}``. The overall verdict is the WORST of the three
    parts — a perfect container cannot rescue AC3 audio — and ``reasons`` only
    carries the parts that are not a clean yes, so the UI can say exactly what
    is wrong instead of a shrug.

    Unknown parts are ``maybe``, never ``no``: refusing to offer a file because
    the scanner did not record its codec would make missing metadata look like a
    broken file, and the ``<video>`` element is a better judge than a guess."""
    ext = container_of(path)
    vid, aud = _norm(video_codec), _norm(audio_codec)

    c_v = _CONTAINER.get(ext, "no" if ext else "maybe")
    v_v = _VIDEO.get(vid, "maybe") if vid else "maybe"
    a_v = _AUDIO.get(aud, "maybe") if aud else "maybe"

    reasons: List[str] = []
    if c_v == "no":
        reasons.append("%s files don't play in a browser" % (ext or "these"))
    elif c_v == "maybe":
        reasons.append("%s support varies by browser" % (ext or "this container"))
    if v_v == "no":
        reasons.append("%s video isn't supported in browsers" % (vid or "this"))
    elif v_v == "maybe" and vid:
        reasons.append("%s video only plays on some browsers" % vid)
    if a_v == "no":
        reasons.append("%s audio isn't supported in browsers (you'd get a silent picture)" % (aud or "this"))
    elif a_v == "maybe" and aud:
        reasons.append("%s audio may not play" % aud)

    verdict = max((c_v, v_v, a_v), key=lambda v: _ORDER[v])
    return {"verdict": verdict, "reasons": reasons,
            "container": ext, "video": vid, "audio": aud}


def mime_for(path: Any) -> str:
    """The Content-Type to serve a library file with. Deliberately explicit
    rather than :mod:`mimetypes`, whose table disagrees with itself across
    platforms on Matroska (the container most of a real library is in)."""
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
        ".mkv": "video/x-matroska", ".webm": "video/webm", ".ogv": "video/ogg",
        ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".ts": "video/mp2t",
        ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".flv": "video/x-flv",
    }.get(container_of(path), "application/octet-stream")


__all__ = ["direct_play_verdict", "container_of", "mime_for"]
