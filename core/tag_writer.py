"""
Tag Writer — reads current file tags and writes DB metadata into audio file tags.
Supports MP3 (ID3v2.4), FLAC, OGG Vorbis, and MP4/M4A.
Reuses the same Mutagen patterns as _enhance_file_metadata in web_server.py.
"""

import os
import logging
import urllib.request
from typing import Dict, Any, Optional, List, Tuple

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCON, TPE2, TPOS, TXXX, APIC, TBPM
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggvorbis import OggVorbis
from mutagen.apev2 import APEv2, APENoHeaderError

logger = logging.getLogger("newmusic.tag_writer")

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
    except Exception:
        pass

    return result


def build_tag_diff(file_tags: Dict[str, Any], db_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compare file tags against DB metadata. Returns a list of diffs:
    [{ field, file_value, db_value, changed }]
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

        # Special: year — DB stores int, file stores string
        if db_key == 'year' and db_val is not None:
            db_str = str(db_val)
            db_val = str(db_val)

        # Only mark as changed if DB has a value AND it differs from file
        # (writer skips fields where DB value is empty, so don't show them as diffs)
        changed = bool(db_str) and file_str != db_str
        diffs.append({
            'field': label,
            'file_key': file_key,
            'file_value': str(file_val) if file_val is not None else '',
            'db_value': str(db_val) if db_val is not None else '',
            'changed': changed,
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
    """
    if not cover_url:
        return None
    try:
        with urllib.request.urlopen(cover_url, timeout=15) as response:
            image_data = response.read()
            mime_type = response.info().get_content_type() or 'image/jpeg'
        if image_data:
            return (image_data, mime_type)
    except Exception as e:
        logger.error(f"Error downloading cover art from {cover_url}: {e}")
    return None


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
        year = db_data.get('year')
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

        if isinstance(audio.tags, ID3):
            written = _write_id3(audio, title, artist, album_artist, album,
                                 year, genre_str, track_num, total_tracks,
                                 disc_num, bpm)
        elif isinstance(audio, (FLAC, OggVorbis)) or type(audio).__name__ == 'OggOpus':
            written = _write_vorbis(audio, title, artist, album_artist, album,
                                    year, genre_str, track_num, total_tracks,
                                    disc_num, bpm)
        elif isinstance(audio, MP4):
            written = _write_mp4(audio, title, artist, album_artist, album,
                                 year, genre_str, track_num, total_tracks,
                                 disc_num, bpm)

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

        # Save
        if isinstance(audio.tags, ID3):
            audio.save(v1=0, v2_version=4)
        elif isinstance(audio, FLAC):
            audio.save(deleteid3=True)
        else:
            audio.save()

        return {'success': True, 'written_fields': written}

    except PermissionError:
        return {'success': False, 'error': 'Permission denied — file may be in use'}
    except Exception as e:
        logger.error(f"Error writing tags to {file_path}: {e}")
        return {'success': False, 'error': str(e)}


# ── Format-specific writers ──

def _write_id3(audio, title, artist, album_artist, album, year, genre,
               track_num, total_tracks, disc_num, bpm) -> List[str]:
    written = []
    if title:
        audio.tags.delall('TIT2')
        audio.tags.add(TIT2(encoding=3, text=[title]))
        written.append('title')
    if artist:
        audio.tags.delall('TPE1')
        audio.tags.add(TPE1(encoding=3, text=[artist]))
        written.append('artist')
    if album_artist:
        audio.tags.delall('TPE2')
        audio.tags.add(TPE2(encoding=3, text=[album_artist]))
        written.append('album_artist')
    if album:
        audio.tags.delall('TALB')
        audio.tags.add(TALB(encoding=3, text=[album]))
        written.append('album')
    if year is not None:
        audio.tags.delall('TDRC')
        audio.tags.add(TDRC(encoding=3, text=[str(year)]))
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
                  track_num, total_tracks, disc_num, bpm) -> List[str]:
    written = []
    if title:
        audio['title'] = [title]
        written.append('title')
    if artist:
        audio['artist'] = [artist]
        written.append('artist')
    if album_artist:
        audio['albumartist'] = [album_artist]
        written.append('album_artist')
    if album:
        audio['album'] = [album]
        written.append('album')
    if year is not None:
        audio['date'] = [str(year)]
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
               track_num, total_tracks, disc_num, bpm) -> List[str]:
    written = []
    if title:
        audio['\xa9nam'] = [title]
        written.append('title')
    if artist:
        audio['\xa9ART'] = [artist]
        written.append('artist')
    if album_artist:
        audio['aART'] = [album_artist]
        written.append('album_artist')
    if album:
        audio['\xa9alb'] = [album]
        written.append('album')
    if year is not None:
        audio['\xa9day'] = [str(year)]
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
