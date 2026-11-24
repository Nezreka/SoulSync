"""what the download tagger keeps, and the few core tags it writes late.

``enhance_file_metadata`` clears a downloaded file's tags and writes its own,
so an uploader's junk never lingers. three things were lost to that which
shouldn't have been:

* **ReplayGain.** it describes the audio stream, not the release: whoever
  tagged the file measured these very samples. clearing it left the track
  playing at a different loudness from its neighbours, and SoulSync's own
  ReplayGain pass (which would write fresh values) is off unless switched on.
  the Opus R128 gains are the same thing by another name.
* **the compilation flag.** never written at all. iTunes, and the players
  that follow it, group a various-artists album by it; without it the album
  is scattered across its contributors.
* **a corrected album artist.** MusicBrainz enrichment can replace the album
  artist after the album artist tag was written (#2259): the correction
  stayed in memory and the file kept the wrong one.

all three are per container: ID3 frames, Vorbis comments, MP4 atoms.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("metadata.tag_keep")

_MP4_FREEFORM = "----:com.apple.iTunes:"
# (kind, key, payload) entries, opaque to the caller
ReplayGainSnapshot = List[Tuple[str, str, Any]]


def _is_gain_key(name: Any) -> bool:
    upper = str(name or "").upper()
    return upper.startswith("REPLAYGAIN_") or upper.startswith("R128_")


def snapshot_replaygain(audio_file: Any, symbols: Any) -> ReplayGainSnapshot:
    """the file's ReplayGain (and R128) tags, to put back after the clear.
    call BEFORE ``tags.clear()``. [] when it has none."""
    snap: ReplayGainSnapshot = []
    try:
        tags = getattr(audio_file, "tags", None)
        if tags is None:
            return snap
        if isinstance(tags, symbols.ID3):
            for frame in tags.getall("TXXX"):
                if _is_gain_key(getattr(frame, "desc", "")):
                    snap.append(("id3", frame.desc, frame))
            for frame in tags.getall("RVA2"):
                snap.append(("id3", "RVA2", frame))
        elif isinstance(audio_file, symbols.MP4):
            for key in list(tags.keys()):
                if key.startswith(_MP4_FREEFORM) and _is_gain_key(key[len(_MP4_FREEFORM):]):
                    snap.append(("mp4", key, list(tags[key])))
        else:
            for key in list(tags.keys()):
                if _is_gain_key(key):
                    snap.append(("vorbis", key, list(tags[key])))
    except Exception as exc:  # noqa: BLE001 - keeping gain tags never fails a tag write
        logger.debug("snapshot_replaygain failed: %s", exc)
    return snap


def restore_replaygain(audio_file: Any, symbols: Any, snapshot: ReplayGainSnapshot) -> int:
    """put the captured gain tags back. returns how many."""
    restored = 0
    for kind, key, payload in snapshot or []:
        try:
            if kind == "id3":
                audio_file.tags.add(payload)
            else:
                audio_file[key] = payload
            restored += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("restore_replaygain (%s) failed: %s", key, exc)
    if restored:
        logger.info("Kept the file's ReplayGain tags (%d).", restored)
    return restored


def is_compilation(metadata: dict) -> bool:
    """a various-artists album: the one case the compilation flag is for.

    NOT "the source calls it a compilation": to Spotify a band's own
    greatest-hits is a compilation, and flagging that would move it off the
    band's page and into Compilations in every player that honours the flag."""
    from core.imports.compilation import is_various_artists_name
    return is_various_artists_name((metadata or {}).get("album_artist"))


def write_compilation_flag(audio_file: Any, symbols: Any, metadata: dict) -> bool:
    """TCMP / COMPILATION / cpil = 1 on a various-artists album"""
    if not is_compilation(metadata):
        return False
    from core.metadata.common import is_vorbis_like
    try:
        if isinstance(audio_file.tags, symbols.ID3):
            from mutagen.id3 import TCMP
            audio_file.tags.add(TCMP(encoding=3, text=["1"]))
        elif isinstance(audio_file, symbols.MP4):
            audio_file["cpil"] = True
        elif is_vorbis_like(audio_file, symbols):
            audio_file["compilation"] = ["1"]
        else:
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - a flag never fails the tag write
        logger.debug("write_compilation_flag failed: %s", exc)
        return False


def write_album_artist(audio_file: Any, symbols: Any, album_artist: str) -> bool:
    """the album artist tag, written again (see the module doc)"""
    name = str(album_artist or "").strip()
    if not name:
        return False
    from core.metadata.common import is_vorbis_like
    try:
        if isinstance(audio_file.tags, symbols.ID3):
            audio_file.tags.delall("TPE2")
            audio_file.tags.add(symbols.TPE2(encoding=3, text=[name]))
        elif isinstance(audio_file, symbols.MP4):
            audio_file["aART"] = [name]
        elif is_vorbis_like(audio_file, symbols):
            audio_file["albumartist"] = [name]
        else:
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("write_album_artist failed: %s", exc)
        return False


__all__ = ["snapshot_replaygain", "restore_replaygain", "is_compilation",
           "write_compilation_flag", "write_album_artist"]
