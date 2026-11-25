"""what the download tagger keeps from the file it was given.

``enhance_file_metadata`` clears a downloaded file's tags and writes its own.
that is on purpose: a file off the network arrives with an uploader's
comments, a ripper's advert, and the ids of whatever release THEY thought it
was, and stale ids beside fresh ones make a file that contradicts itself. so
everything that says what RELEASE this is goes, and SoulSync writes that
itself.

but a file also carries facts about the RECORDING: who wrote it, what key it
is in, how loud these very samples are, where the encoder's padding ends.
no metadata source SoulSync asks will supply those again, and clearing them
lost them for good:

* ReplayGain (and Opus R128 gain): the track played at a different loudness
  from its neighbours, and SoulSync's own ReplayGain pass is off unless
  switched on;
* gapless info on an M4A (iTunSMPB, pgap): a live album or a mix clicked
  between tracks;
* composer, lyricist, conductor, remixer, the performer credits, work and
  movement: a classical library is unusable without them;
* key and BPM, when no source SoulSync asked had one (a DJ's library).

``KEPT_FIELDS`` names them, by the one table of where a field lives in each
container (core/metadata/tagfields.py); ``_KEPT_RAW`` adds the few that are
a family of tags or have no single address. they are put back right after
the clear and BEFORE anything is written, so a value SoulSync does have (a
BPM from Deezer, a key from Beatport) replaces the kept one.

the compilation flag is here too: it was never written at all, and iTunes
and the players that follow it group a various-artists album by it.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("metadata.tag_keep")

_MP4_FREEFORM = "----:com.apple.iTunes:"
# (kind, key, payload) entries, opaque to the caller
KeptSnapshot = List[Tuple[str, str, Any]]

# facts about the recording, by their name in tagfields.FIELDS
KEPT_FIELDS = ("bpm", "key", "composer", "lyricist", "conductor", "remixer", "grouping", "work",
               "movement", "language", "encoder")

# and the ones with no single field: families of tags, credits lists, gapless
_KEPT_RAW = {
    # involved people / musician credits (2.4 and 2.3), movement number, relative volume
    "id3": ("TIPL", "TMCL", "IPLS", "MVIN", "RVA2"),
    "vorbis": ("performer", "arranger", "producer", "engineer", "mixer", "djmixer", "writer",
               "movement", "movementnumber", "movementtotal"),
    "mp4": ("pgap", "\xa9mvi", "\xa9mvc", "shwm",
            _MP4_FREEFORM + "iTunSMPB", _MP4_FREEFORM + "iTunNORM",
            _MP4_FREEFORM + "PRODUCER", _MP4_FREEFORM + "ENGINEER", _MP4_FREEFORM + "MIXER",
            _MP4_FREEFORM + "ARRANGER", _MP4_FREEFORM + "DJMIXER"),
}


def _is_gain_key(name: Any) -> bool:
    upper = str(name or "").upper()
    return upper.startswith("REPLAYGAIN_") or upper.startswith("R128_")


def snapshot_kept_tags(audio_file: Any, symbols: Any) -> KeptSnapshot:
    """the recording's own facts in this file, to put back after the clear.
    call BEFORE ``tags.clear()``. [] when it has none."""
    from core.metadata import tagfields
    snap: KeptSnapshot = []
    try:
        tags = getattr(audio_file, "tags", None)
        kind = tagfields.container(audio_file, symbols)
        if tags is None or kind is None:
            return snap
        fields = [tagfields.FIELDS[name] for name in KEPT_FIELDS]
        if kind == "id3":
            wanted_txxx = {f.id3[5:] for f in fields if f.id3.startswith("TXXX:")}
            for frame in tags.getall("TXXX"):
                desc = getattr(frame, "desc", "")
                if _is_gain_key(desc) or desc in wanted_txxx:
                    snap.append(("id3", desc, frame))
            for frame_id in [f.id3 for f in fields if ":" not in f.id3] + list(_KEPT_RAW["id3"]):
                for frame in tags.getall(frame_id):
                    snap.append(("id3", frame_id, frame))
        else:
            keys = [tagfields._mp4_key(f.mp4) for f in fields] if kind == "mp4" else [f.vorbis for f in fields]
            keys += list(_KEPT_RAW[kind])
            lowered = {str(k).lower(): k for k in tags.keys()}
            seen = set()
            for key in keys:
                # Vorbis names are case-blind; an M4A's freeform names are
                # matched the same way (replaygain_track_gain, REPLAYGAIN_...)
                actual = key if key in tags else lowered.get(str(key).lower())
                if actual is not None and actual not in seen:
                    seen.add(actual)
                    value = tags[actual]
                    snap.append((kind, actual, list(value) if isinstance(value, (list, tuple)) else value))
            for key in list(tags.keys()):
                name = key[len(_MP4_FREEFORM):] if kind == "mp4" and key.startswith(_MP4_FREEFORM) else \
                    (key if kind == "vorbis" else "")
                if _is_gain_key(name) and key not in seen:
                    seen.add(key)
                    snap.append((kind, key, list(tags[key])))
    except Exception as exc:  # noqa: BLE001 - keeping tags never fails a tag write
        logger.debug("snapshot_kept_tags failed: %s", exc)
    return snap


def restore_kept_tags(audio_file: Any, symbols: Any, snapshot: KeptSnapshot) -> int:
    """put the captured tags back. returns how many."""
    restored = 0
    for kind, key, payload in snapshot or []:
        try:
            if kind == "id3":
                audio_file.tags.add(payload)
            else:
                audio_file[key] = payload
            restored += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("restore_kept_tags (%s) failed: %s", key, exc)
    if restored:
        logger.info("Kept %d tag(s) the file came with (ReplayGain, credits, key, gapless).", restored)
    return restored


# the names these had when ReplayGain was all that was kept
snapshot_replaygain = snapshot_kept_tags
restore_replaygain = restore_kept_tags


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
    from core.metadata import tagfields
    try:
        return tagfields.write(audio_file, symbols, "compilation", True)
    except Exception as exc:  # noqa: BLE001 - a flag never fails the tag write
        logger.debug("write_compilation_flag failed: %s", exc)
        return False


__all__ = ["KEPT_FIELDS", "snapshot_kept_tags", "restore_kept_tags", "snapshot_replaygain",
           "restore_replaygain", "is_compilation", "write_compilation_flag"]
