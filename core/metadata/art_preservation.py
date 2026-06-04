"""Preserve embedded cover art across the metadata-enrichment rewrite.

Issue #764 (continuation of #755): imported files lost their album art.
``enhance_file_metadata`` rebuilds tags from scratch — for FLAC it calls
``clear_pictures()`` and for MP3/MP4 it clears the whole tag block — *before*
it has the replacement art in hand. It then saves the file regardless of
whether new art was actually embedded. So every failure mode downstream
destroyed the art that shipped with the download:

  - source-metadata extraction returns nothing -> early save, no embed
  - no album-art URL available / art download fails -> embed returns early
  - art rejected by the min-resolution guard -> embed returns early
  - art embedding disabled in config -> embed skipped entirely

In all of those the file was saved with the pictures already cleared and
nothing put back. This module captures the existing art up front (live
mutagen objects, so they re-apply verbatim) and restores it right before a
save *iff the file currently has none* — so the happy path (new art embedded)
is byte-for-byte unchanged, and the only behaviour change is that we never
end up with less art than we started with.

Scope mirrors ``embed_album_art_metadata``: FLAC ``Picture`` blocks, ID3
``APIC`` frames, MP4 ``covr`` atoms. OggOpus/OggVorbis store art inside the
Vorbis comment (no ``clear_pictures``), so the enrichment rewrite never
strips it and it needs no preservation here.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("metadata.art_preservation")

# Each snapshot entry is (kind, payload) where payload is a list of live
# mutagen objects captured before the tag rewrite.
ArtSnapshot = List[Tuple[str, list]]


def has_embedded_art(audio_file: Any, symbols: Any) -> bool:
    """True iff ``audio_file`` currently carries embedded cover art in any
    of the formats the enricher manages (FLAC pictures / ID3 APIC / MP4 covr)."""
    try:
        if getattr(audio_file, "pictures", None):
            return True
        tags = getattr(audio_file, "tags", None)
        if tags is not None and isinstance(tags, symbols.ID3) and tags.getall("APIC"):
            return True
        if isinstance(audio_file, symbols.MP4) and tags and tags.get("covr"):
            return True
    except Exception as exc:  # defensive: never let art-detection break a save
        logger.debug("has_embedded_art check failed: %s", exc)
    return False


def snapshot_embedded_art(audio_file: Any, symbols: Any) -> ArtSnapshot:
    """Capture existing embedded art so it can be restored if re-embedding
    fails. Returns a list of ``(kind, [objects])`` entries, or ``[]`` when the
    file has no art. Captures the live mutagen objects (Picture / APIC frame /
    MP4Cover) so they re-apply exactly as they were.

    Must be called BEFORE ``clear_pictures()`` / ``tags.clear()``."""
    snap: ArtSnapshot = []
    try:
        pictures = getattr(audio_file, "pictures", None)
        if pictures:
            snap.append(("flac", list(pictures)))
        tags = getattr(audio_file, "tags", None)
        if tags is not None and isinstance(tags, symbols.ID3):
            apics = tags.getall("APIC")
            if apics:
                snap.append(("id3", list(apics)))
        if isinstance(audio_file, symbols.MP4) and tags:
            covr = tags.get("covr")
            if covr:
                snap.append(("mp4", list(covr)))
    except Exception as exc:
        logger.debug("snapshot_embedded_art failed: %s", exc)
    return snap


def restore_embedded_art(audio_file: Any, symbols: Any, snapshot: ArtSnapshot) -> bool:
    """Re-apply captured art IFF the file currently has none. Returns True if
    it restored something.

    No-op (returns False) when the snapshot is empty or the file already has
    art — so calling this before a save never overwrites freshly-embedded art,
    it only puts back what the rewrite would otherwise have destroyed."""
    if not snapshot or has_embedded_art(audio_file, symbols):
        return False
    restored = False
    for kind, payload in snapshot:
        try:
            if kind == "flac" and hasattr(audio_file, "add_picture"):
                for pic in payload:
                    audio_file.add_picture(pic)
                restored = True
            elif kind == "id3":
                tags = getattr(audio_file, "tags", None)
                if tags is not None:
                    for frame in payload:
                        tags.add(frame)
                    restored = True
            elif kind == "mp4":
                audio_file["covr"] = payload
                restored = True
        except Exception as exc:
            logger.debug("restore_embedded_art (%s) failed: %s", kind, exc)
    if restored:
        logger.info("Preserved existing embedded cover art (re-embed produced none).")
    return restored


__all__ = ["has_embedded_art", "snapshot_embedded_art", "restore_embedded_art", "ArtSnapshot"]
