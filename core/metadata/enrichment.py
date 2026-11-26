"""Compatibility facade and orchestration for metadata enrichment."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

from core.metadata.art_preservation import (
    restore_embedded_art,
    snapshot_embedded_art,
)
from core.metadata.artwork import embed_album_art_metadata
from core.metadata.common import (
    get_config_manager,
    get_file_lock,
    get_mutagen_symbols,
    is_vorbis_like,
    save_audio_file,
    strip_all_non_audio_tags,
    verify_metadata_written,
)
from core.metadata.source import embed_source_ids, extract_source_metadata
from core.metadata.tag_keep import (
    restore_kept_tags,
    snapshot_kept_tags,
    write_compilation_flag,
)
from utils.logging_config import get_logger as _create_logger


__all__ = [
    "build_metadata_enrichment_runtime",
    "enhance_file_metadata",
    "extract_source_metadata",
    "embed_source_ids",
]


logger = _create_logger("metadata.enrichment")


def build_metadata_enrichment_runtime(
    *,
    mb_worker: Any | None = None,
    deezer_worker: Any | None = None,
    audiodb_worker: Any | None = None,
    tidal_client: Any | None = None,
    hifi_client: Any | None = None,
    qobuz_enrichment_worker: Any | None = None,
    lastfm_worker: Any | None = None,
    genius_worker: Any | None = None,
    bandcamp_worker: Any | None = None,
    spotify_enrichment_worker: Any | None = None,
    itunes_enrichment_worker: Any | None = None,
    jiosaavn_worker: Any | None = None,
) -> SimpleNamespace:
    """Build the runtime object consumed by core.metadata.enrichment/source."""
    return SimpleNamespace(
        mb_worker=mb_worker,
        deezer_worker=deezer_worker,
        audiodb_worker=audiodb_worker,
        jiosaavn_worker=jiosaavn_worker,
        tidal_client=tidal_client,
        hifi_client=hifi_client,
        qobuz_enrichment_worker=qobuz_enrichment_worker,
        lastfm_worker=lastfm_worker,
        genius_worker=genius_worker,
        bandcamp_worker=bandcamp_worker,
        spotify_enrichment_worker=spotify_enrichment_worker,
        itunes_enrichment_worker=itunes_enrichment_worker,
    )


def enhance_file_metadata(file_path: str, context: dict, artist: dict, album_info: dict, runtime=None) -> bool:
    if context.get('_preserve_embedded_tags'):
        # "Import from own tags" must not clear composer/remixer/custom
        # frames, embedded art, or rewrite metadata the user already verified.
        # The import pipeline records the manual lock in the database.
        return True
    cfg = get_config_manager()
    if cfg.get("metadata_enhancement.enabled", True) is False:
        logger.warning("Metadata enhancement disabled in config.")
        return True

    if album_info is None:
        album_info = {}

    symbols = get_mutagen_symbols()
    if not symbols:
        logger.error("Mutagen is unavailable, cannot enhance metadata.")
        return False

    file_lock = get_file_lock(file_path)
    with file_lock:
        logger.info("Enhancing metadata for: %s", os.path.basename(file_path))
        art_snapshot = []  # defined up front so the except handler can restore
        audio_file = None
        try:
            # Snapshot the file's own album tag BEFORE anything clears it.
            # Tracks whose context carries no real album (the 'Unknown
            # Album' placeholder from album-less playlist entries) fall
            # back to it in extract_source_metadata instead of having the
            # placeholder stamped over a correct tag.
            try:
                from core.imports.track_number import (
                    read_embedded_album,
                    read_embedded_album_artist,
                )
                _file_album_tag = read_embedded_album(file_path)
                _file_album_artist_tag = read_embedded_album_artist(file_path)
            except Exception:
                _file_album_tag = None
                _file_album_artist_tag = None
            if isinstance(context, dict):
                if _file_album_tag:
                    context["_file_album_tag"] = _file_album_tag
                else:
                    # Context dicts are reused across a task's candidate
                    # attempts — never let one file's snapshot ride into
                    # the next file's tagging.
                    context.pop("_file_album_tag", None)
                if _file_album_artist_tag:
                    context["_file_album_artist_tag"] = _file_album_artist_tag
                else:
                    context.pop("_file_album_artist_tag", None)

            # the metadata first: it needs nothing from the file, and when
            # there is none the file must be left exactly as it came. the APE
            # strip below rewrites the file on disk, and it used to run before
            # this, so "original tags retained" was said of a file that had
            # just lost some of them.
            metadata = extract_source_metadata(context, artist, album_info)
            if not metadata:
                logger.error("Could not extract source metadata; original tags retained.")
                return False

            strip_all_non_audio_tags(file_path)
            audio_file = symbols.File(file_path)
            if audio_file is None:
                logger.error("Could not load audio file with Mutagen: %s", file_path)
                return False

            # Capture any embedded cover art BEFORE we clear it. The rewrite
            # below clears pictures/tags up front, but new art isn't fetched
            # until much later (and may fail / be unavailable / be disabled).
            # Without this snapshot, every such failure saves the file with
            # the art destroyed and nothing put back — issue #764.
            art_snapshot = snapshot_embedded_art(audio_file, symbols)
            # what the file says about the RECORDING (its loudness, key,
            # composer, gapless padding) is kept across the clear; what it says
            # about the release is not (core/metadata/tag_keep.py)
            kept_snapshot = snapshot_kept_tags(audio_file, symbols)

            if hasattr(audio_file, "clear_pictures"):
                audio_file.clear_pictures()

            if audio_file.tags is not None:
                if len(audio_file.tags) > 0:
                    tag_keys = list(audio_file.tags.keys())[:15]
                    logger.info("Clearing %s existing tags: %s", len(audio_file.tags), ", ".join(str(k) for k in tag_keys))
                audio_file.tags.clear()
            else:
                audio_file.add_tags()
            restore_kept_tags(audio_file, symbols, kept_snapshot)

            from core.audiobook_routing import routed_root_for_context
            from core.imports.track_number import normalize_disc_number
            from core.metadata.multi_value import genre_values
            from core.metadata.track_number_format import track_number_known
            write_multi = cfg.get("metadata_enhancement.tags.write_multi_artist", False)

            from core.metadata import tagfields
            _core_written = []

            def _write_core_tags():
                """the core tags, from `metadata` as it stands when this runs:
                after every source has had its say (embed_source_ids calls it
                between looking and writing). once per file."""
                if _core_written:
                    return
                _core_written.append(True)
                # the artist lists are read here, not before the lookups: a
                # source that corrects an artist replaces its list too
                artists_list = metadata.get("_artists_list", [])
                album_artists_list = metadata.get("_album_artists_list", [])
                # streaming services tag a children's audio play "Rock"; a
                # routed album is an audiobook, whatever the source says
                genre = "Audiobook" if routed_root_for_context(context) else metadata.get("genre")
                genres_out = genre_values(genre, bool(write_multi))
                # the import marks a position it couldn't work out as 0, and
                # that was written as "0/14". an unknown number is left out, so
                # the file says nothing rather than something false. (a missing
                # number, None, still defaults to 1 as it always has; a total
                # of 0 means unknown and is left off: "6", not "6/0".)
                _track_unknown = metadata.get('track_number') is not None \
                    and not track_number_known(metadata.get('track_number'))
                # the disc number is always written, floored to 1: a track
                # whose disc came back empty floated ungrouped above the disc
                # sections in Jellyfin and Plex (Sokhi)
                _disc_num = normalize_disc_number(metadata.get('disc_number'))
                # the core tags, each written at its address for this container
                # (core/metadata/tagfields.py: the one table of where a field
                # lives in an MP3, a FLAC or Ogg file, and an M4A)
                _put = lambda name, value: tagfields.write(audio_file, symbols, name, value)  # noqa: E731
                _put("title", metadata.get("title"))
                if metadata.get("artist"):
                    # the artist tag is the DISPLAY artist: already joined by
                    # source.py with the configured separator, or the primary alone
                    # when feat_in_title is on. with write_multi_artist the
                    # per-artist list goes beside it (TXXX:Artists, ARTISTS: the
                    # Picard convention), so the display string keeps its
                    # separator and media servers still get each artist.
                    _multi = write_multi and len(artists_list) > 1
                    _display = [metadata["artist"]]
                    if _multi and tagfields.container(audio_file, symbols) == "mp4" \
                            and not cfg.get("metadata_enhancement.tags.feat_in_title", False):
                        # an M4A's artist atom holds the list itself: Navidrome and
                        # Jellyfin read it as several artists (#587). not with
                        # feat_in_title, where the guests are already in the title
                        # and the list here put each of them in both.
                        _display = list(artists_list)
                    _put("artist", _display)
                    if _multi:
                        _put("artists", list(artists_list))
                if metadata.get("album_artist"):
                    _put("album_artist", metadata["album_artist"])
                    if write_multi and len(album_artists_list) > 1:
                        _put("album_artists", list(album_artists_list))
                _put("album", metadata.get("album"))
                _put("date", metadata.get("date"))
                _put("genre", genres_out)
                tagfields.write_track_position(
                    audio_file, symbols, metadata.get("track_number"), metadata.get("total_tracks"),
                    known=not _track_unknown)
                tagfields.write_disc_position(audio_file, symbols, _disc_num, metadata.get("total_discs"))

            from core.metadata.manual import is_manual_context, manual_cover_path, write_manual_marker
            if is_manual_context(context):
                # the user typed this release in. no name-based id lookups:
                # they'd embed a studio release's ids and replace the user's
                # date with a musicbrainz one
                metadata['_manual'] = True
                metadata['_manual_cover_path'] = manual_cover_path(context)
                write_manual_marker(audio_file, symbols)
                _write_core_tags()
            else:
                # the sources are asked first and the core tags written from
                # what they leave in `metadata`: #2259's corrected album artist
                # (the release is 12th Planet's though this track is DJ Evol's)
                # is simply the album artist by the time it is written
                embed_source_ids(audio_file, metadata, context, runtime=runtime,
                                 on_resolved=_write_core_tags)
                _write_core_tags()   # (a no-op unless the lookups never got that far)

            # a various-artists album is flagged a compilation, after the album
            # artist is final (core/metadata/tag_keep.py)
            write_compilation_flag(audio_file, symbols, metadata)

            if album_info is not None and metadata.get("musicbrainz_release_id"):
                album_info["musicbrainz_release_id"] = metadata["musicbrainz_release_id"]

            art_ok = not cfg.get("metadata_enhancement.embed_album_art", True)
            if not art_ok:
                art_ok = bool(embed_album_art_metadata(audio_file, metadata) or art_snapshot)

            quality = context.get("_audio_quality", "")
            if quality and cfg.get("metadata_enhancement.tags.quality_tag", True) is not False:
                tagfields.write(audio_file, symbols, "quality", quality)

            # If art embedding was skipped/disabled or produced nothing (no
            # URL, download failed, rejected by the min-resolution guard),
            # the file still has the pictures we cleared above. Restore the
            # original so import never strips existing art (#764). No-op when
            # new art was embedded — that path is unchanged.
            restore_embedded_art(audio_file, symbols, art_snapshot)

            if not save_audio_file(audio_file, symbols):
                return False

            verified = verify_metadata_written(file_path)
            if verified:
                logger.info("Metadata enhanced successfully.")
            else:
                logger.info("Metadata saved but verification found issues (see above).")
            return bool(verified and art_ok)
        except Exception as exc:
            import traceback

            logger.error("Error enhancing metadata for %s: %s", file_path, exc)
            logger.error("[Metadata Debug] Exception type: %s", type(exc).__name__)
            logger.info("[Metadata Debug] File exists: %s", os.path.exists(file_path))
            logger.warning("[Metadata Debug] Artist: %s", artist.get("name", "MISSING") if artist else "None")
            logger.warning("[Metadata Debug] Album info: %s", album_info.get("album_name", "MISSING") if album_info else "None")
            logger.error("[Metadata Debug] Traceback:\n%s", traceback.format_exc())
            # Keep available core tags and original art after a partial failure.
            # The caller still receives failure and validates the final file.
            try:
                if audio_file is not None:
                    if art_snapshot:
                        restore_embedded_art(audio_file, symbols, art_snapshot)
                    save_audio_file(audio_file, symbols)
                    logger.info("Persisted core tags (and restored art) after enrichment error.")
            except Exception as restore_exc:
                logger.debug("Tag/art persist after error failed: %s", restore_exc)
            # a crash before any tag was built persists an empty file, which the
            # media server files under Unknown Album. put the basics back.
            from core.metadata.core_tag_guard import ensure_core_tags
            ensure_core_tags(file_path, context, artist, album_info)
            return False
