"""Picard-style Album Consistency — after all tracks in an album batch finish
post-processing, pick ONE MusicBrainz release and overwrite album-level tags
on every file so they're consistent. Prevents media server album splits.
"""

import os
import threading
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TPE2, TXXX
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.oggvorbis import OggVorbis

from utils.logging_config import get_logger

logger = get_logger("album_consistency")

# Tags written to EVERY file (album-level, same value)
_ALBUM_LEVEL_TAGS = [
    'MUSICBRAINZ_RELEASE_ID',
    'MUSICBRAINZ_RELEASEGROUPID',
    'MUSICBRAINZ_ALBUMARTISTID',
    'RELEASETYPE',
    'RELEASESTATUS',
    'RELEASECOUNTRY',
    'ORIGINALDATE',
    'BARCODE',
    'MEDIA',
    'TOTALDISCS',
    'CATALOGNUMBER',
    'SCRIPT',
    'ASIN',
]

# Vorbis comment keys (FLAC/OGG) — same as _ALBUM_LEVEL_TAGS (uppercase)
# ID3 TXXX desc mapping
_ID3_TXXX_MAP = {
    'MUSICBRAINZ_RELEASE_ID': 'MusicBrainz Album Id',
    'MUSICBRAINZ_RELEASEGROUPID': 'MusicBrainz Release Group Id',
    'MUSICBRAINZ_ALBUMARTISTID': 'MusicBrainz Album Artist Id',
    'MUSICBRAINZ_RELEASETRACKID': 'MusicBrainz Release Track Id',
    'RELEASETYPE': 'MusicBrainz Album Type',
    'RELEASESTATUS': 'MusicBrainz Album Status',
    'RELEASECOUNTRY': 'MusicBrainz Album Release Country',
    'ORIGINALDATE': 'ORIGINALDATE',
    'BARCODE': 'BARCODE',
    'MEDIA': 'MEDIA',
    'TOTALDISCS': 'TOTALDISCS',
    'CATALOGNUMBER': 'CATALOGNUMBER',
    'SCRIPT': 'SCRIPT',
    'ASIN': 'ASIN',
}

# MP4 freeform keys
_MP4_KEY_PREFIX = '----:com.apple.iTunes:'


def _normalize_title(s):
    """Normalize a title for comparison."""
    import re
    if not s:
        return ''
    s = s.lower().strip()
    s = re.sub(r'\s*[\(\[].*?[\)\]]\s*', ' ', s)  # Strip parentheticals/brackets
    s = re.sub(r'[^\w\s]', '', s)  # Strip punctuation
    return ' '.join(s.split())


def _find_best_release(album_name, artist_name, track_count, mb_service):
    """Search MusicBrainz for the best release matching this album.
    Prefers releases where track count matches the download."""
    try:
        # First try our existing match_release (uses version qualifier scoring)
        match = mb_service.match_release(album_name, artist_name)
        if not match or not match.get('mbid'):
            # Try stripping edition qualifiers — Spotify uses "Album (Super Deluxe)"
            # but MusicBrainz just calls it "Album"
            import re
            stripped = re.sub(
                r'\s*[\(\[]'
                r'[^)\]]*'
                r'(?:deluxe|expanded|remaster(?:ed)?|anniversary|special|collector|'
                r'limited|bonus|platinum|gold|super\s*deluxe|standard|edition)'
                r'[^)\]]*'
                r'[\)\]]',
                '', album_name, flags=re.IGNORECASE
            ).strip()
            # Also strip trailing bare editions
            stripped = re.sub(
                r'\s+(?:-\s+)?(?:deluxe|expanded|remaster(?:ed)?|anniversary|special|collector|'
                r'limited|bonus|platinum|gold|super\s*deluxe|standard)'
                r'(?:\s+(?:edition|version))?\s*$',
                '', stripped, flags=re.IGNORECASE
            ).strip()

            if stripped and stripped.lower() != album_name.lower():
                logger.info(f"Retrying MB search with stripped name: '{stripped}' (was '{album_name}')")
                match = mb_service.match_release(stripped, artist_name)

            if not match or not match.get('mbid'):
                # Final fallback: direct API search with stripped name
                search_name = stripped or album_name
                logger.info(f"No cached MB release — trying direct search for '{search_name}'")
                search_results = mb_service.mb_client.search_release(search_name, artist_name, limit=5)
                if not search_results:
                    logger.info(f"No MB release found for '{album_name}' by '{artist_name}'")
                    return None
                mbid = search_results[0].get('id', '')
                if not mbid:
                    return None
                logger.info(f"Direct search found: {search_results[0].get('title', '')} ({mbid[:8]}...)")
            else:
                mbid = match['mbid']
        else:
            mbid = match['mbid']

        # Fetch full release with tracklist
        release = mb_service.mb_client.get_release(
            mbid, includes=['recordings', 'release-groups', 'labels', 'media', 'artist-credits']
        )
        if not release:
            return None

        # Check track count match
        mb_track_count = sum(len(m.get('tracks') or m.get('track-list', [])) for m in release.get('media', []))
        if abs(mb_track_count - track_count) <= 2:
            logger.info(f"Accepted release '{release.get('title')}' ({mbid[:8]}...) — "
                        f"{mb_track_count} tracks (downloaded {track_count})")
            return release

        # Track count mismatch — try searching for a better release
        # Use stripped name for search (MB often doesn't include edition suffixes)
        import re
        _search_name = re.sub(
            r'\s*[\(\[][^)\]]*(?:deluxe|expanded|remaster|anniversary|special|collector|'
            r'limited|bonus|platinum|gold|super\s*deluxe|standard|edition)[^)\]]*[\)\]]',
            '', album_name, flags=re.IGNORECASE
        ).strip() or album_name
        logger.info(f"Release '{release.get('title')}' has {mb_track_count} tracks but we have {track_count} — "
                     f"searching for better match with '{_search_name}'")
        search_results = mb_service.mb_client.search_release(_search_name, artist_name, limit=5)
        if not search_results:
            # Fall back to the first match even if count doesn't match perfectly
            return release

        best_release = release
        best_diff = abs(mb_track_count - track_count)

        for sr in search_results:
            sr_mbid = sr.get('id', '')
            if not sr_mbid or sr_mbid == mbid:
                continue
            try:
                candidate = mb_service.mb_client.get_release(
                    sr_mbid, includes=['recordings', 'release-groups', 'labels', 'media', 'artist-credits']
                )
                if not candidate:
                    continue
                cand_count = sum(len(m.get('tracks') or m.get('track-list', [])) for m in candidate.get('media', []))
                cand_diff = abs(cand_count - track_count)
                if cand_diff < best_diff:
                    best_diff = cand_diff
                    best_release = candidate
                    if cand_diff == 0:
                        break  # Perfect match
            except Exception:
                continue

        logger.info(f"Best release: '{best_release.get('title')}' ({best_release.get('id', '')[:8]}...) — "
                     f"track count diff: {best_diff}")
        return best_release

    except Exception as e:
        logger.error(f"Error finding best release for '{album_name}': {e}")
        return None


def _match_files_to_tracklist(file_infos, release):
    """Match downloaded files to MB release tracklist entries.
    Returns {file_path: mb_track_entry} for matched files."""
    # Build MB tracklist lookup: (disc, track) -> track entry
    mb_lookup = {}
    for medium in release.get('media', []):
        disc_num = medium.get('position', 1)
        for track in (medium.get('tracks') or medium.get('track-list', [])):
            pos = track.get('position', track.get('number', 0))
            try:
                pos = int(pos)
            except (ValueError, TypeError):
                continue
            mb_lookup[(disc_num, pos)] = track

    matched = {}
    unmatched = []

    # Pass 1: exact disc+track number match
    for fi in file_infos:
        key = (fi.get('disc_number', 1), fi.get('track_number', 1))
        if key in mb_lookup:
            matched[fi['path']] = mb_lookup[key]
        else:
            unmatched.append(fi)

    # Pass 2: title similarity for unmatched
    remaining_mb = {k: v for k, v in mb_lookup.items() if v not in matched.values()}
    for fi in unmatched:
        norm_title = _normalize_title(fi.get('title', ''))
        best_score = 0
        best_entry = None
        for _key, mb_track in remaining_mb.items():
            recording = mb_track.get('recording', {})
            mb_title = _normalize_title(recording.get('title', ''))
            if not mb_title:
                continue
            score = SequenceMatcher(None, norm_title, mb_title).ratio()
            if score > best_score:
                best_score = score
                best_entry = mb_track
        if best_entry and best_score >= 0.70:
            matched[fi['path']] = best_entry
            # Remove from remaining so it's not double-matched
            remaining_mb = {k: v for k, v in remaining_mb.items() if v is not best_entry}

    return matched


def _write_tag_to_file(audio, tag_key, value):
    """Write a single custom tag to an audio file (Mutagen object)."""
    if value is None:
        return
    value = str(value)

    try:
        if isinstance(audio.tags, ID3):
            desc = _ID3_TXXX_MAP.get(tag_key, tag_key)
            # Remove existing TXXX with this desc
            to_remove = [k for k in audio.tags if k.startswith('TXXX:') and desc in k]
            for k in to_remove:
                del audio.tags[k]
            audio.tags.add(TXXX(encoding=3, desc=desc, text=[value]))
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio[tag_key] = [value]
        elif isinstance(audio, MP4):
            key = _MP4_KEY_PREFIX + _ID3_TXXX_MAP.get(tag_key, tag_key)
            audio[key] = [MP4FreeForm(value.encode('utf-8'))]
    except Exception as e:
        logger.debug(f"Failed to write {tag_key}: {e}")


def _write_standard_tag(audio, tag_name, value):
    """Write album/albumartist standard tags."""
    if value is None:
        return
    try:
        if isinstance(audio.tags, ID3):
            if tag_name == 'album':
                audio.tags.delall('TALB')
                audio.tags.add(TALB(encoding=3, text=[value]))
            elif tag_name == 'albumartist':
                audio.tags.delall('TPE2')
                audio.tags.add(TPE2(encoding=3, text=[value]))
        elif isinstance(audio, (FLAC, OggVorbis)):
            audio[tag_name.upper()] = [value]
        elif isinstance(audio, MP4):
            tag_map = {'album': '\xa9alb', 'albumartist': 'aART'}
            key = tag_map.get(tag_name)
            if key:
                audio[key] = [value]
    except Exception as e:
        logger.debug(f"Failed to write standard tag {tag_name}: {e}")


def run_album_consistency(
    file_infos: List[Dict[str, Any]],
    album_name: str,
    artist_name: str,
    mb_service: Any,
    total_discs: int = 1,
    file_lock_fn=None,
) -> Dict[str, Any]:
    """
    Picard-style album consistency: pick ONE MusicBrainz release for the album,
    then overwrite album-level tags on all files to match.

    Args:
        file_infos: List of {path, track_number, disc_number, title}
        album_name: Album name from download context
        artist_name: Artist name from download context
        mb_service: MusicBrainzService instance
        total_discs: Number of discs in the album
        file_lock_fn: Optional function(path) -> context manager for thread-safe writes

    Returns:
        {success, release_mbid, matched_tracks, total_files, tags_written, error}
    """
    result = {
        'success': False,
        'release_mbid': None,
        'matched_tracks': 0,
        'total_files': len(file_infos),
        'tags_written': 0,
        'error': None,
    }

    if not file_infos:
        result['error'] = 'No files provided'
        return result

    if not mb_service:
        result['error'] = 'MusicBrainz service not available'
        return result

    # Step 1: Find the best release
    release = _find_best_release(album_name, artist_name, len(file_infos), mb_service)
    if not release:
        result['error'] = f'No MusicBrainz release found for "{album_name}"'
        return result

    release_mbid = release.get('id', '')
    result['release_mbid'] = release_mbid

    # Step 2: Match files to tracklist
    matched = _match_files_to_tracklist(file_infos, release)
    result['matched_tracks'] = len(matched)

    if len(matched) < len(file_infos) * 0.5:
        result['error'] = (f'Only {len(matched)}/{len(file_infos)} tracks matched the release — '
                          f'aborting to avoid incorrect tagging')
        return result

    # Step 3: Build album-level tags (same for all files)
    album_tags = {}
    album_tags['MUSICBRAINZ_RELEASE_ID'] = release_mbid

    rg = release.get('release-group', {})
    if rg.get('id'):
        album_tags['MUSICBRAINZ_RELEASEGROUPID'] = rg['id']
    if rg.get('primary-type'):
        album_tags['RELEASETYPE'] = rg['primary-type']
    if rg.get('first-release-date'):
        album_tags['ORIGINALDATE'] = rg['first-release-date']

    ac = release.get('artist-credit', [])
    if ac and isinstance(ac[0], dict):
        aa = ac[0].get('artist', {})
        if aa.get('id'):
            album_tags['MUSICBRAINZ_ALBUMARTISTID'] = aa['id']

    if release.get('status'):
        album_tags['RELEASESTATUS'] = release['status']
    if release.get('country'):
        album_tags['RELEASECOUNTRY'] = release['country']
    if release.get('barcode'):
        album_tags['BARCODE'] = release['barcode']

    media_list = release.get('media', [])
    if media_list:
        fmt = media_list[0].get('format', '')
        if fmt:
            album_tags['MEDIA'] = fmt
        album_tags['TOTALDISCS'] = str(len(media_list))

    label_info = release.get('label-info', [])
    if label_info and isinstance(label_info[0], dict):
        cat = label_info[0].get('catalog-number', '')
        if cat:
            album_tags['CATALOGNUMBER'] = cat

    text_rep = release.get('text-representation', {})
    if isinstance(text_rep, dict) and text_rep.get('script'):
        album_tags['SCRIPT'] = text_rep['script']

    if release.get('asin'):
        album_tags['ASIN'] = release['asin']

    # Album name and artist from the release (canonical MB values)
    release_album_name = release.get('title', album_name)
    release_artist_name = artist_name
    if ac:
        # Build full artist credit string
        parts = []
        for credit in ac:
            if isinstance(credit, dict):
                parts.append(credit.get('artist', {}).get('name', ''))
                parts.append(credit.get('joinphrase', ''))
            elif isinstance(credit, str):
                parts.append(credit)
        full_credit = ''.join(parts).strip()
        if full_credit:
            release_artist_name = full_credit

    # Step 4: Write tags to matched files only (unmatched files keep their existing tags)
    tags_written = 0
    for fi in file_infos:
        file_path = fi['path']
        mb_track = matched.get(file_path)

        # Only write to files that matched the tracklist — avoids corrupting
        # bonus tracks or files from a different edition
        if not mb_track:
            continue

        if not os.path.exists(file_path):
            continue

        try:
            if file_lock_fn:
                lock = file_lock_fn(file_path)
            else:
                lock = _DummyLock()

            with lock:
                audio = MutagenFile(file_path, easy=False)
                if audio is None:
                    continue

                # Write album-level tags
                for tag_key, value in album_tags.items():
                    _write_tag_to_file(audio, tag_key, value)

                # Write standard album/albumartist tags
                _write_standard_tag(audio, 'album', release_album_name)
                _write_standard_tag(audio, 'albumartist', release_artist_name)

                # Write per-track tag (release track ID) if matched
                if mb_track and mb_track.get('id'):
                    _write_tag_to_file(audio, 'MUSICBRAINZ_RELEASETRACKID', mb_track['id'])

                audio.save()
                tags_written += 1

        except Exception as e:
            logger.error(f"Error writing consistency tags to {file_path}: {e}")

    result['tags_written'] = tags_written
    result['success'] = tags_written > 0
    logger.info(f"Album consistency complete: {tags_written}/{len(file_infos)} files tagged "
                f"with release '{release_album_name}' ({release_mbid[:8]}...)")
    return result


class _DummyLock:
    """No-op context manager when no file lock is provided."""
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
