"""
Tag Writer — reads current file tags and writes DB metadata into audio file tags.
Supports MP3 (ID3v2.4), FLAC, OGG Vorbis, and MP4/M4A.
Reuses the same Mutagen patterns as _enhance_file_metadata in web_server.py.
"""

import os
import logging
from typing import Dict, Any, Optional, List, Tuple

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCON, TPE2, TPOS, TXXX, APIC, TBPM
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggvorbis import OggVorbis
from mutagen.apev2 import APEv2, APENoHeaderError

logger = logging.getLogger("tag_writer")

# Supported extensions
SUPPORTED_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.mp4'}


def read_file_tags(file_path: str) -> Dict[str, Any]:
    """
    Read current tags from an audio file. Returns a dict of tag values
    that can be compared against DB metadata.
    """
    result = {
        'title': None,
        'artist': None,
        'album_artist': None,
        'album': None,
        'year': None,
        'genre': None,
        'track_number': None,
        'disc_number': None,
        'bpm': None,
        'has_cover_art': False,
        'format': None,
        'error': None,
        # ReplayGain (None if not present in file)
        'replaygain_track_gain': None,
        'replaygain_track_peak': None,
        'replaygain_album_gain': None,
        'replaygain_album_peak': None,
        # SoulSync verification status ('verified'/'unverified'/'force_imported')
        'verification_status': None,
    }

    if not file_path or not os.path.exists(file_path):
        result['error'] = 'File not found'
        return result

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        result['error'] = f'Unsupported format: {ext}'
        return result

    try:
        audio = MutagenFile(file_path)
        if audio is None:
            result['error'] = 'Could not read file with Mutagen'
            return result

        result['format'] = ext.lstrip('.').upper()

        if isinstance(audio.tags, ID3):
            # MP3
            result['title'] = _id3_text(audio.tags, 'TIT2')
            result['artist'] = _id3_text(audio.tags, 'TPE1')
            result['album_artist'] = _id3_text(audio.tags, 'TPE2')
            result['album'] = _id3_text(audio.tags, 'TALB')
            result['year'] = _id3_text(audio.tags, 'TDRC')
            result['genre'] = _id3_text(audio.tags, 'TCON')
            result['track_number'] = _parse_track_num(_id3_text(audio.tags, 'TRCK'))
            result['disc_number'] = _parse_track_num(_id3_text(audio.tags, 'TPOS'))
            bpm_text = _id3_text(audio.tags, 'TBPM')
            if bpm_text:
                try:
                    result['bpm'] = float(bpm_text)
                except (ValueError, TypeError):
                    pass
            result['has_cover_art'] = bool(audio.tags.getall('APIC'))
            for fr in audio.tags.getall('TXXX'):
                if getattr(fr, 'desc', '') == 'SOULSYNC_VERIFICATION' and fr.text:
                    result['verification_status'] = str(fr.text[0])
                    break

        elif isinstance(audio, (FLAC, OggVorbis)) or type(audio).__name__ == 'OggOpus':
            # FLAC / OGG
            result['title'] = _vorbis_first(audio, 'title')
            result['artist'] = _vorbis_first(audio, 'artist')
            result['album_artist'] = _vorbis_first(audio, 'albumartist')
            result['album'] = _vorbis_first(audio, 'album')
            result['year'] = _vorbis_first(audio, 'date')
            result['genre'] = _vorbis_first(audio, 'genre')
            result['track_number'] = _parse_track_num(_vorbis_first(audio, 'tracknumber'))
            result['disc_number'] = _parse_track_num(_vorbis_first(audio, 'discnumber'))
            bpm_val = _vorbis_first(audio, 'bpm')
            if bpm_val:
                try:
                    result['bpm'] = float(bpm_val)
                except (ValueError, TypeError):
                    pass
            if isinstance(audio, FLAC):
                result['has_cover_art'] = bool(audio.pictures)
            else:
                # OGG doesn't have a standard picture field we can easily check
                result['has_cover_art'] = False
            result['verification_status'] = _vorbis_first(audio, 'soulsync_verification')

        elif isinstance(audio, MP4):
            # MP4 / M4A
            result['title'] = _mp4_first(audio, '\xa9nam')
            result['artist'] = _mp4_first(audio, '\xa9ART')
            result['album_artist'] = _mp4_first(audio, 'aART')
            result['album'] = _mp4_first(audio, '\xa9alb')
            result['year'] = _mp4_first(audio, '\xa9day')
            result['genre'] = _mp4_first(audio, '\xa9gen')
            trkn = audio.tags.get('trkn', []) if audio.tags else []
            if trkn:
                result['track_number'] = trkn[0][0] if isinstance(trkn[0], tuple) else None
            disk = audio.tags.get('disk', []) if audio.tags else []
            if disk:
                result['disc_number'] = disk[0][0] if isinstance(disk[0], tuple) else None
            result['has_cover_art'] = bool(audio.tags.get('covr', [])) if audio.tags else False
            vs = (audio.tags or {}).get('----:com.soulsync:VERIFICATION')
            if vs:
                raw = vs[0]
                result['verification_status'] = (
                    raw.decode('utf-8', 'ignore') if isinstance(raw, bytes) else str(raw)
                )

    except Exception as e:
        result['error'] = str(e)

    # Read existing ReplayGain tags (additive — never raises)
    try:
        from core.replaygain import read_replaygain_tags
        rg = read_replaygain_tags(file_path)
        result['replaygain_track_gain'] = rg.get('track_gain')
        result['replaygain_track_peak'] = rg.get('track_peak')
        result['replaygain_album_gain'] = rg.get('album_gain')
        result['replaygain_album_peak'] = rg.get('album_peak')
    except Exception as e:
        logger.debug("read replaygain tags failed: %s", e)

    return result


# Known placeholder / "we don't really know" metadata values. A match in the
# DB never warrants overwriting a real value already in the file (issue #800:
# a mis-grouped track sits under a "Various Artists" / "[Unknown Album]" record,
# and Write Tags would otherwise stamp that junk over the file's correct tags).
_PLACEHOLDER_META_VALUES = frozenset({
    'various artists', 'various artist',
    'unknown artist', 'unknown album', 'unknown',
    '[unknown album]', '[unknown artist]', '[unknown]',
    'untitled album',
})


def is_placeholder_meta(value: Any) -> bool:
    """True for empty or known-placeholder metadata strings (case-insensitive)."""
    if value is None:
        return True
    s = str(value).strip().lower()
    return s == '' or s in _PLACEHOLDER_META_VALUES


def write_verification_status(file_path: str, status: str) -> bool:
    """Embed the SoulSync verification status into the file's tags.

    Vorbis comment ``SOULSYNC_VERIFICATION`` (FLAC/OGG/Opus), ID3
    ``TXXX:SOULSYNC_VERIFICATION`` (MP3), MP4 freeform
    ``----:com.soulsync:VERIFICATION``. The tag travels with the file so the
    status survives DB resets; the AcoustID scan reads it back via
    ``read_file_tags`` to refresh the DB column and to mark force-imported
    fallbacks in its findings. Never raises; returns success.
    """
    if not status or not file_path or not os.path.exists(file_path):
        return False
    try:
        audio = MutagenFile(file_path)
        if audio is None:
            return False
        if getattr(audio, 'tags', None) is None and hasattr(audio, 'add_tags'):
            audio.add_tags()
        if isinstance(audio.tags, ID3):
            audio.tags.delall('TXXX:SOULSYNC_VERIFICATION')
            audio.tags.add(TXXX(encoding=3, desc='SOULSYNC_VERIFICATION', text=[status]))
        elif isinstance(audio, MP4):
            audio.tags['----:com.soulsync:VERIFICATION'] = [status.encode('utf-8')]
        else:
            # Vorbis-comment family (FLAC / OggVorbis / OggOpus)
            audio['SOULSYNC_VERIFICATION'] = [status]
        audio.save()
        return True
    except Exception as e:
        logger.debug("write_verification_status failed for %s: %s", file_path, e)
        return False


def guard_placeholder_overwrite(db_val: Any, file_val: Any) -> Any:
    """#800 guard: never replace a real file value with a placeholder.

    Returns ``None`` (skip the write → preserve the file's value) ONLY when the
    DB value is a placeholder/empty AND the file already holds a real,
    non-placeholder value. Otherwise returns ``db_val`` unchanged — so a
    legitimate value still writes, including a genuine ``Various Artists`` album
    artist on a real compilation (there the file has no conflicting real value,
    so the guard doesn't fire).
    """
    if is_placeholder_meta(db_val) and not is_placeholder_meta(file_val):
        return None
    return db_val


def build_tag_diff(file_tags: Dict[str, Any], db_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compare file tags against DB metadata. Returns a list of diffs:
    [{ field, file_value, db_value, changed, protected }]

    ``protected`` marks a field the #800 guard is holding back: the DB value is
    a placeholder and the file's real value would be preserved, so it's shown
    as no-change rather than a wrong overwrite.
    """
    fields = [
        ('title', 'title', 'Title'),
        ('artist', 'artist_name', 'Artist'),
        ('album', 'album_title', 'Album'),
        ('album_artist', 'artist_name', 'Album Artist'),
        ('year', 'year', 'Year'),
        ('genre', 'genres', 'Genre'),
        ('track_number', 'track_number', 'Track #'),
        ('disc_number', 'disc_number', 'Disc #'),
        ('bpm', 'bpm', 'BPM'),
    ]

    diffs = []
    for file_key, db_key, label in fields:
        file_val = file_tags.get(file_key)
        db_val = db_data.get(db_key)

        # Special: use per-track artist for Artist field when available (DJ mixes, compilations)
        if file_key == 'artist' and db_data.get('track_artist'):
            db_val = db_data['track_artist']

        # Normalize for comparison
        file_str = _normalize_for_compare(file_val)
        db_str = _normalize_for_compare(db_val)

        # Special: genres can be a list in DB
        if db_key == 'genres' and isinstance(db_val, list):
            db_str = ', '.join(db_val) if db_val else ''
            db_val = db_str if db_str else None

        # Special: year / release date (#824). Prefer the full release_date when
        # the DB has one — it's authoritative, compare it directly. Otherwise use
        # the year int, for which a MORE-specific file date with the same year is
        # preserved (not flagged as a change). DB year is int, file is string.
        if db_key == 'year':
            release_date = db_data.get('release_date')
            if release_date:
                db_val = str(release_date)
                db_str = str(release_date).strip()
            elif db_val is not None:
                db_str = str(db_val)
                db_val = str(db_val)
                if file_str and file_str[:4] == db_str:
                    file_str = db_str

        # Only mark as changed if DB has a value AND it differs from file
        # (writer skips fields where DB value is empty, so don't show them as diffs)
        changed = bool(db_str) and file_str != db_str

        # #800 — if the change would replace a real file value with a
        # placeholder (Various Artists / [Unknown Album] / …), hold it back:
        # the writer preserves the file's value, so show it as no-change.
        protected = False
        if changed and guard_placeholder_overwrite(db_val, file_val) is None:
            changed = False
            protected = True

        diffs.append({
            'field': label,
            'file_key': file_key,
            'file_value': str(file_val) if file_val is not None else '',
            'db_value': str(db_val) if db_val is not None else '',
            'changed': changed,
            'protected': protected,
        })

    # Cover art — special row
    diffs.append({
        'field': 'Cover Art',
        'file_key': 'cover_art',
        'file_value': 'Embedded' if file_tags.get('has_cover_art') else 'None',
        'db_value': 'Available' if db_data.get('thumb_url') else 'None',
        'changed': not file_tags.get('has_cover_art') and bool(db_data.get('thumb_url')),
    })

    return diffs


def download_cover_art(cover_url: str) -> Optional[Tuple[bytes, str]]:
    """
    Download cover art once. Returns (image_data, mime_type) or None on failure.
    Call this once per album, then pass the result to write_tags_to_file for each track.

    Delegates to ``core.metadata.artwork._fetch_art_bytes`` so the enhanced-
    library-view "Write Tags to File" feature embeds the same highest-
    resolution cover the auto post-process flow does — Spotify master
    (~2000px), iTunes 3000×3000, and Deezer 1900×1900 — with the same
    one-level fallback to the original size if the CDN refuses the upgrade.
    """
    if not cover_url:
        return None
    from core.metadata.artwork import _fetch_art_bytes

    image_data, mime_type = _fetch_art_bytes(cover_url)
    return (image_data, mime_type) if image_data else None


def write_tags_to_file(file_path: str, db_data: Dict[str, Any],
                       embed_cover: bool = True,
                       cover_url: Optional[str] = None,
                       cover_data: Optional[Tuple[bytes, str]] = None) -> Dict[str, Any]:
    """
    Write DB metadata into audio file tags. Only writes fields that have DB values.
    Returns { success, written_fields, error }

    For cover art, pass either:
    - cover_url: downloads art on-the-fly (fine for single track)
    - cover_data: (image_bytes, mime_type) tuple from download_cover_art() (preferred for batch)
    """
    if not file_path or not os.path.exists(file_path):
        return {'success': False, 'error': 'File not found'}

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return {'success': False, 'error': f'Unsupported format: {ext}'}

    try:
        audio = MutagenFile(file_path)
        if audio is None:
            return {'success': False, 'error': 'Could not open file with Mutagen'}

        # Ensure tags exist
        if audio.tags is None:
            audio.add_tags()

        written = []

        # Build metadata dict from DB data
        title = db_data.get('title')
        artist = db_data.get('track_artist') or db_data.get('artist_name')  # Per-track artist for compilations/DJ mixes
        album = db_data.get('album_title')
        album_artist = db_data.get('artist_name')  # Album artist stays as the album-level artist

        # #800 — never overwrite a real value already in the file with a
        # placeholder DB value (Various Artists / [Unknown Album] / …). A
        # mis-grouped track sits under such a record; writing it would destroy
        # the file's correct tags. Reads the file's current values and skips
        # only the placeholder-over-real fields (legit values, incl. a genuine
        # compilation's Various Artists, still write — see guard docstring).
        try:
            _current = read_file_tags(file_path)
        except Exception:
            _current = {}
        if not _current.get('error'):
            title = guard_placeholder_overwrite(title, _current.get('title'))
            artist = guard_placeholder_overwrite(artist, _current.get('artist'))
            album = guard_placeholder_overwrite(album, _current.get('album'))
            album_artist = guard_placeholder_overwrite(album_artist, _current.get('album_artist'))

        # Prefer the full release_date (e.g. 2023-09-01) when the DB has one;
        # fall back to the year-only int. _date_to_write() then writes the full
        # date and still preserves an equally-specific existing file date (#824).
        year = db_data.get('release_date') or db_data.get('year')
        genres = db_data.get('genres')
        track_num = db_data.get('track_number')
        total_tracks = db_data.get('track_count')
        disc_num = db_data.get('disc_number')
        bpm = db_data.get('bpm')

        # Genre: list → comma string
        genre_str = None
        if genres:
            if isinstance(genres, list):
                genre_str = ', '.join(genres) if genres else None
            elif isinstance(genres, str):
                genre_str = genres

        # Multi-value artist support — issue #587. Caller can pass
        # `artists_list` (per-track list of contributor names) and the
        # writer respects the user's `metadata_enhancement.tags.write_multi_artist`
        # config the same way the post-download enrichment pipeline does.
        # When the setting is on AND the list has >1 entry:
        #   - ID3 keeps TPE1 as the joined display string (already in `artist`)
        #     and writes a separate TXXX:Artists frame with the list
        #   - Vorbis writes an `artists` multi-value key alongside `artist`
        #   - MP4 writes \xa9ART as the list when on, single string when off
        # When OFF or the list is empty/single — same single-string write
        # as before. Backward compatible for callers that don't pass it.
        artists_list = _resolve_artists_list_for_write(db_data)

        if isinstance(audio.tags, ID3):
            written = _write_id3(audio, title, artist, album_artist, album,
                                 year, genre_str, track_num, total_tracks,
                                 disc_num, bpm, artists_list=artists_list)
        elif isinstance(audio, (FLAC, OggVorbis)) or type(audio).__name__ == 'OggOpus':
            written = _write_vorbis(audio, title, artist, album_artist, album,
                                    year, genre_str, track_num, total_tracks,
                                    disc_num, bpm, artists_list=artists_list)
        elif isinstance(audio, MP4):
            written = _write_mp4(audio, title, artist, album_artist, album,
                                 year, genre_str, track_num, total_tracks,
                                 disc_num, bpm, artists_list=artists_list)

        # Embed already-known source IDs (Spotify / iTunes / MusicBrainz) from
        # db_data, reusing the canonical import-time frame writer — no API
        # re-fetch. Only fires when db_data carries id keys, so the plain
        # "write the core tags" callers are unaffected.
        _src_meta = {k: db_data[k] for k in (
            'source', 'source_track_id', 'source_album_id', 'source_artist_id',
            'spotify_track_id', 'spotify_album_id', 'spotify_artist_id',
            'itunes_track_id', 'itunes_album_id', 'itunes_artist_id',
            'musicbrainz_recording_id', 'musicbrainz_release_id',
        ) if db_data.get(k)}
        if _src_meta:
            try:
                from core.metadata.source import embed_known_source_ids
                if embed_known_source_ids(audio, _src_meta):
                    written.append('source_ids')
            except Exception as e:
                logger.debug("source-id embed skipped for %s: %s", file_path, e)

        # Embed cover art if requested
        if embed_cover:
            art_ok = False
            if cover_data:
                # Use pre-downloaded art (batch mode)
                art_ok = _embed_cover_art_data(audio, cover_data[0], cover_data[1])
            elif cover_url:
                # Download on-the-fly (single track mode)
                art_ok = _embed_cover_art(audio, cover_url)
            if art_ok:
                written.append('cover_art')

        # Save — atomically (#819): write into a temp copy + atomic replace so an
        # interrupted/OOM-killed save can never truncate the user's file. Same
        # format kwargs as before, just routed through the shared atomic helper.
        from types import SimpleNamespace
        from core.metadata.common import save_audio_file
        save_audio_file(audio, SimpleNamespace(ID3=ID3, FLAC=FLAC, File=MutagenFile))

        return {'success': True, 'written_fields': written}

    except PermissionError:
        return {'success': False, 'error': 'Permission denied — file may be in use'}
    except Exception as e:
        logger.error(f"Error writing tags to {file_path}: {e}")
        return {'success': False, 'error': str(e)}


# ── Format-specific writers ──


def _resolve_artists_list_for_write(db_data: Dict[str, Any]) -> Optional[List[str]]:
    """Pull a multi-value artists list from db_data when caller supplied one.

    Accepts either ``artists_list`` (list of names) or ``artists`` (same
    shape — kept for symmetry with the post-process pipeline's
    ``_artists_list`` field). Drops empty / non-string entries. Returns
    ``None`` when no list was supplied so format writers can branch on
    "single-string only" vs "multi-value too".
    """
    raw = db_data.get('artists_list') or db_data.get('artists') or db_data.get('_artists_list')
    if not raw:
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    cleaned = []
    for entry in raw:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                cleaned.append(text)
    return cleaned or None


def _multi_artist_write_enabled() -> bool:
    """Read the same config flag the enrichment pipeline reads, so the
    repair-path retag respects the user's choice."""
    try:
        from config.settings import config_manager
        return bool(config_manager.get('metadata_enhancement.tags.write_multi_artist', False))
    except Exception:
        return False


def _date_to_write(existing: Optional[str], year) -> str:
    """Value to write for the date/year tag. Writes the DB year, BUT keeps an
    existing, MORE-specific file date (e.g. ``2023-11-03``) when its year already
    matches — so enrichment/retag never downgrades a real full release date to
    just the year (#824). When the years differ (a genuine correction) or the
    file has no date, the year is written as before."""
    year_str = str(year)
    if existing:
        existing = str(existing).strip()
        if len(existing) > 4 and existing[:4] == year_str:
            return existing
    return year_str


def _write_id3(audio, title, artist, album_artist, album, year, genre,
               track_num, total_tracks, disc_num, bpm,
               artists_list: Optional[List[str]] = None) -> List[str]:
    written = []
    if title:
        audio.tags.delall('TIT2')
        audio.tags.add(TIT2(encoding=3, text=[title]))
        written.append('title')
    if artist:
        audio.tags.delall('TPE1')
        audio.tags.add(TPE1(encoding=3, text=[artist]))
        written.append('artist')
        # TPE1 stays as the joined display string. When the caller
        # supplied a multi-value list AND the user has the
        # write_multi_artist setting on, ALSO write the per-artist
        # list to a TXXX:Artists frame (Picard convention). Mirrors
        # the post-download enrichment writer at
        # core/metadata/enrichment.py.
        if artists_list and len(artists_list) > 1 and _multi_artist_write_enabled():
            audio.tags.delall('TXXX:Artists')
            audio.tags.add(TXXX(encoding=3, desc='Artists', text=list(artists_list)))
            written.append('artists_multi')
    if album_artist:
        audio.tags.delall('TPE2')
        audio.tags.add(TPE2(encoding=3, text=[album_artist]))
        written.append('album_artist')
    if album:
        audio.tags.delall('TALB')
        audio.tags.add(TALB(encoding=3, text=[album]))
        written.append('album')
    if year is not None:
        existing_date = _id3_text(audio.tags, 'TDRC')
        audio.tags.delall('TDRC')
        audio.tags.add(TDRC(encoding=3, text=[_date_to_write(existing_date, year)]))
        written.append('year')
    if genre:
        audio.tags.delall('TCON')
        audio.tags.add(TCON(encoding=3, text=[genre]))
        written.append('genre')
    if track_num is not None:
        audio.tags.delall('TRCK')
        trk_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
        audio.tags.add(TRCK(encoding=3, text=[trk_str]))
        written.append('track_number')
    if disc_num is not None:
        audio.tags.delall('TPOS')
        audio.tags.add(TPOS(encoding=3, text=[str(disc_num)]))
        written.append('disc_number')
    if bpm is not None:
        audio.tags.delall('TBPM')
        audio.tags.add(TBPM(encoding=3, text=[str(int(bpm))]))
        written.append('bpm')
    return written


def _write_vorbis(audio, title, artist, album_artist, album, year, genre,
                  track_num, total_tracks, disc_num, bpm,
                  artists_list: Optional[List[str]] = None) -> List[str]:
    written = []
    if title:
        audio['title'] = [title]
        written.append('title')
    if artist:
        audio['artist'] = [artist]
        written.append('artist')
        # Vorbis-style multi-value: write the per-artist list to the
        # `artists` key (separate from `artist`, picard convention) when
        # the caller supplied a list AND the user has multi-value write
        # enabled. Mirrors enrichment.py.
        if artists_list and len(artists_list) > 1 and _multi_artist_write_enabled():
            audio['artists'] = list(artists_list)
            written.append('artists_multi')
    if album_artist:
        audio['albumartist'] = [album_artist]
        written.append('album_artist')
    if album:
        audio['album'] = [album]
        written.append('album')
    if year is not None:
        audio['date'] = [_date_to_write(_vorbis_first(audio, 'date'), year)]
        written.append('year')
    if genre:
        audio['genre'] = [genre]
        written.append('genre')
    if track_num is not None:
        trk_str = f"{track_num}/{total_tracks}" if total_tracks else str(track_num)
        audio['tracknumber'] = [trk_str]
        written.append('track_number')
    if disc_num is not None:
        audio['discnumber'] = [str(disc_num)]
        written.append('disc_number')
    if bpm is not None:
        audio['bpm'] = [str(int(bpm))]
        written.append('bpm')
    return written


def _write_mp4(audio, title, artist, album_artist, album, year, genre,
               track_num, total_tracks, disc_num, bpm,
               artists_list: Optional[List[str]] = None) -> List[str]:
    written = []
    if title:
        audio['\xa9nam'] = [title]
        written.append('title')
    if artist:
        # MP4 \xa9ART can carry a list directly. When caller supplied
        # a multi-value list AND user has multi-value write enabled,
        # write the list. Otherwise single-string. Mirrors enrichment.py
        # MP4 path.
        if artists_list and len(artists_list) > 1 and _multi_artist_write_enabled():
            audio['\xa9ART'] = list(artists_list)
            written.append('artist')
            written.append('artists_multi')
        else:
            audio['\xa9ART'] = [artist]
            written.append('artist')
    if album_artist:
        audio['aART'] = [album_artist]
        written.append('album_artist')
    if album:
        audio['\xa9alb'] = [album]
        written.append('album')
    if year is not None:
        audio['\xa9day'] = [_date_to_write(_mp4_first(audio, '\xa9day'), year)]
        written.append('year')
    if genre:
        audio['\xa9gen'] = [genre]
        written.append('genre')
    if track_num is not None:
        total = total_tracks or 0
        audio['trkn'] = [(track_num, total)]
        written.append('track_number')
    if disc_num is not None:
        audio['disk'] = [(disc_num, 0)]
        written.append('disc_number')
    if bpm is not None:
        audio['tmpo'] = [int(bpm)]
        written.append('bpm')
    return written


def _embed_cover_art(audio, cover_url: str) -> bool:
    """Download and embed cover art from URL (single-track convenience)."""
    try:
        result = download_cover_art(cover_url)
        if not result:
            return False
        return _embed_cover_art_data(audio, result[0], result[1])
    except Exception as e:
        logger.error(f"Error embedding cover art: {e}")
        return False


def _embed_cover_art_data(audio, image_data: bytes, mime_type: str) -> bool:
    """Embed pre-downloaded cover art bytes into an audio file object."""
    try:
        if not image_data:
            return False

        if isinstance(audio.tags, ID3):
            audio.tags.delall('APIC')
            audio.tags.add(APIC(encoding=3, mime=mime_type, type=3, desc='Cover', data=image_data))
        elif isinstance(audio, FLAC):
            audio.clear_pictures()
            picture = Picture()
            picture.data = image_data
            picture.type = 3
            picture.mime = mime_type
            picture.width = 640
            picture.height = 640
            picture.depth = 24
            audio.add_picture(picture)
        elif isinstance(audio, MP4):
            fmt = MP4Cover.FORMAT_JPEG if 'jpeg' in mime_type else MP4Cover.FORMAT_PNG
            audio['covr'] = [MP4Cover(image_data, imageformat=fmt)]

        return True
    except Exception as e:
        logger.error(f"Error embedding cover art data: {e}")
        return False


# ── Helpers ──

def _id3_text(tags, frame_id: str) -> Optional[str]:
    frames = tags.getall(frame_id)
    if frames and frames[0].text:
        return str(frames[0].text[0])
    return None


def _vorbis_first(audio, key: str) -> Optional[str]:
    vals = audio.get(key, [])
    return vals[0] if vals else None


def _mp4_first(audio, key: str) -> Optional[str]:
    vals = audio.tags.get(key, []) if audio.tags else []
    return str(vals[0]) if vals else None


def _parse_track_num(val) -> Optional[int]:
    """Parse track number from '3/12' or '3' format."""
    if val is None:
        return None
    try:
        return int(str(val).split('/')[0])
    except (ValueError, IndexError):
        return None


def _normalize_for_compare(val) -> str:
    """Normalize a value for comparison."""
    if val is None:
        return ''
    if isinstance(val, (list, tuple)):
        return ', '.join(str(v) for v in val) if val else ''
    # Normalize numeric types: 120.0 → '120', 120 → '120'
    if isinstance(val, float):
        return str(int(val)) if val == int(val) else str(val)
    if isinstance(val, int):
        return str(val)
    return str(val).strip()
