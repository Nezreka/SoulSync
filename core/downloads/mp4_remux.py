"""Turn a streamed AAC download into a plain MP4.

Tidal (and the HiFi instances in front of it) hand AAC back as stream
segments: a fragmented MP4, or raw ADTS. Written back to back that plays, but
its header says 0 seconds, so players that trust the header (Navidrome,
Feishin) show 0:00 and can't seek. A stream copy into an ordinary MP4 fixes
it without touching the audio. The import writes the tags afterwards, so
nothing is lost.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("downloads.mp4_remux")


def _remux_into(src: Path, dst: Path, ffmpeg) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(dst)],
        check=True, capture_output=True, text=True, timeout=300)
    if dst.stat().st_size <= 0:
        raise RuntimeError("empty output")


def remux_to_plain_mp4(path, ffmpeg) -> bool:
    """Rewrite ``path`` in place as a plain MP4 (moov up front). False, with
    the file left as it was, when ffmpeg is missing or fails."""
    path = Path(path)
    if not ffmpeg or not path.is_file():
        return False
    tmp = path.with_name(path.stem + ".remux" + path.suffix)
    try:
        _remux_into(path, tmp, ffmpeg)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - the streamed file still plays, keep it
        detail = getattr(exc, "stderr", "") or exc
        logger.warning("Could not remux %s into a plain MP4 (duration may read 0:00): %s",
                       path.name, detail)
        tmp.unlink(missing_ok=True)
        return False


def reads_zero_length(path) -> bool:
    """an MP4 whose header says it has no length (a streamed download saved as
    it came): it plays, but shows 0:00 and won't seek."""
    try:
        from mutagen.mp4 import MP4
        info = MP4(str(path)).info
    except Exception:  # noqa: BLE001 - not an MP4 mutagen can read: not ours to fix
        return False
    return not (getattr(info, 'length', 0) or 0) > 0


def repair_streamed_mp4(path, ffmpeg) -> bool:
    """fix a file already in the library: remux it into a plain MP4 and put
    every tag back as it was (cover, MusicBrainz ids, custom fields), since the
    stream copy keeps the audio but not all of them. all of it happens on a
    copy; the original is only replaced once the copy has its tags and reads
    its real length. True when the file was fixed."""
    from mutagen.mp4 import MP4
    path = Path(path)
    if not ffmpeg or not path.is_file():
        return False
    try:
        saved = dict(MP4(str(path)).tags or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Can't read %s before fixing it: %s", path.name, exc)
        return False
    tmp = path.with_name(path.stem + ".fix" + path.suffix)
    try:
        _remux_into(path, tmp, ffmpeg)
        fixed = MP4(str(tmp))
        if fixed.tags is None:
            fixed.add_tags()
        fixed.tags.clear()
        fixed.tags.update(saved)
        fixed.save()
        if not (MP4(str(tmp)).info.length or 0) > 0:
            raise RuntimeError("still reads no length after the remux")
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - the original stays untouched
        detail = getattr(exc, "stderr", "") or exc
        logger.warning("Could not fix the length of %s: %s", path.name, detail)
        tmp.unlink(missing_ok=True)
        return False


__all__ = ["reads_zero_length", "remux_to_plain_mp4", "repair_streamed_mp4"]
