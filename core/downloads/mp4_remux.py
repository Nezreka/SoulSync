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


def remux_to_plain_mp4(path, ffmpeg) -> bool:
    """Rewrite ``path`` in place as a plain MP4 (moov up front). False, with
    the file left as it was, when ffmpeg is missing or fails."""
    path = Path(path)
    if not ffmpeg or not path.is_file():
        return False
    tmp = path.with_name(path.stem + ".remux" + path.suffix)
    try:
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)],
            check=True, capture_output=True, text=True, timeout=300)
        if tmp.stat().st_size <= 0:
            raise RuntimeError("empty output")
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - the streamed file still plays, keep it
        detail = getattr(exc, "stderr", "") or exc
        logger.warning("Could not remux %s into a plain MP4 (duration may read 0:00): %s",
                       path.name, detail)
        tmp.unlink(missing_ok=True)
        return False


__all__ = ["remux_to_plain_mp4"]
