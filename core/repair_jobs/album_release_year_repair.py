"""Album Release Year Alignment Job — aligns album and track release years to canonical release dates.

Scans the library for albums where the release year in the database, the parent folder name,
or track tags (ORIGINALDATE, ORIGINALYEAR, DATE) do not match the canonical original release date
from MusicBrainz (or metadata source).

In dry-run mode (default), generates reviewable findings detailing:
- The current year vs. canonical release year
- Tracks affected
- Proposed folder renames

In live mode (or when applying a finding):
- Writes ORIGINALDATE, ORIGINALYEAR (and optionally DATE) to audio file tags atomically
- Updates lib2_albums.year in the catalogue
- Renames the parent folder if it contains the old year and repoints the
  album's lib2_track_files paths.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from contextlib import closing
from typing import Any, Dict, List, Optional, Tuple

from core.library.path_resolver import resolve_library_file_path
from core.metadata.common import get_mutagen_symbols, save_audio_file
from core.repair_jobs import register_job
from core.library2.maintenance_subjects import (
    active_album_subjects, active_file_subjects, subject_details,
)
from core.repair_jobs.base import JobContext, JobResult, RepairJob, drop_hand_tagged
from utils.logging_config import get_logger

logger = get_logger("repair_job.album_release_year_repair")

_YEAR_RE = re.compile(r'\b(19\d\d|20\d\d)\b')
_DISC_SUBDIR_RE = re.compile(r'^(?:cd|disc|disk)\s*\d+$', re.IGNORECASE)


def extract_year(val: Any) -> Optional[str]:
    """Extract a 4-digit year (1900-2099) from a string or number."""
    if val is None:
        return None
    s = str(val).strip()
    m = _YEAR_RE.search(s)
    return m.group(1) if m else None


def compute_new_folder_name(
    folder_name: str,
    album_title: str,
    old_year: Optional[str],
    new_year: str,
) -> Optional[str]:
    """Compute the new folder name if folder_name contains an incorrect year.

    Preserves years that are legitimately part of the album title (e.g. '1984' or '1999').
    Returns the new folder name, or None if no rename is needed.
    """
    new_year_str = str(new_year).strip()
    if not new_year_str or len(new_year_str) != 4 or not new_year_str.isdigit():
        return None

    matches = list(_YEAR_RE.finditer(folder_name))
    if not matches:
        return None

    # Check if album title itself contains a 4-digit year to avoid replacing title numbers
    album_title_lower = (album_title or '').lower()
    folder_lower = folder_name.lower()
    title_start = folder_lower.find(album_title_lower) if album_title_lower else -1

    target_match = None
    old_year_str = str(old_year).strip() if old_year else None

    for m in matches:
        year_str = m.group(1)
        if year_str == new_year_str:
            # Folder already has the target year
            continue

        # If this year is part of the album title, skip it
        if title_start != -1 and year_str in album_title_lower:
            expected_pos = title_start + album_title_lower.find(year_str)
            if m.start() == expected_pos:
                continue

        # If old_year matches, this is our primary candidate
        if old_year_str and year_str == old_year_str:
            target_match = m
            break

        # Fallback candidate if no old_year specified
        if target_match is None:
            target_match = m

    if target_match is None:
        return None

    start, end = target_match.span()
    return folder_name[:start] + new_year_str + folder_name[end:]


def find_album_folder(track_paths: List[str]) -> Optional[str]:
    """Find the common album root directory from a list of resolved track paths."""
    valid_paths = [os.path.abspath(p) for p in track_paths if p]
    if not valid_paths:
        return None
    try:
        common = os.path.commonpath(valid_paths)
        if os.path.isfile(common):
            common = os.path.dirname(common)
        # If common path is a disc folder (CD 1, Disc 2), ascend one level
        base = os.path.basename(common)
        if _DISC_SUBDIR_RE.match(base):
            common = os.path.dirname(common)
        return common
    except Exception as e:
        logger.debug("Failed to determine common album directory: %s", e)
        return None


def is_folder_exclusive_to_album(
    db: Any,
    folder_path: str,
    album_id: int,
    transfer_folder: Optional[str] = None,
    artist_name: Optional[str] = None,
) -> bool:
    """True if folder_path is safely exclusive to album_id and not a protected directory."""
    if not db or not folder_path or not os.path.isdir(folder_path):
        return False

    norm_folder = os.path.normpath(folder_path)

    # 1. Never rename filesystem roots or parents
    if norm_folder == os.path.dirname(norm_folder):
        return False

    # 2. Never rename configured transfer folder or internal staging/deleted folders
    if transfer_folder:
        norm_transfer = os.path.normpath(transfer_folder)
        if norm_folder == norm_transfer:
            return False
        from core.repair_jobs.base import is_internal_transfer_dir
        if is_internal_transfer_dir(norm_folder, norm_transfer):
            return False

    # 3. Never rename if folder name equals artist name
    folder_base = os.path.basename(norm_folder)
    if artist_name and folder_base.lower() == str(artist_name).strip().lower():
        return False

    try:
        pattern1 = norm_folder + os.sep + '%'
        pattern2 = norm_folder.replace('\\', '/') + '/%'
        pattern_base1 = '%/' + folder_base + '/%'
        pattern_base2 = '%\\' + folder_base + '\\%'
        with closing(db._get_connection()) as conn:
            row = conn.execute("""
                SELECT COUNT(DISTINCT t.album_id)
                FROM lib2_track_files f
                JOIN lib2_tracks t ON t.id = f.track_id
                WHERE t.album_id != ?
                  AND COALESCE(f.file_state, 'active') <> 'deleted'
                  AND (f.path LIKE ? OR f.path LIKE ? OR f.path LIKE ? OR f.path LIKE ?)
            """, (album_id, pattern1, pattern2, pattern_base1, pattern_base2)).fetchone()
        return (row[0] if row else 0) == 0
    except Exception as e:
        logger.debug("is_folder_exclusive_to_album error: %s", e)
        return False


def read_file_year_tags(file_path: str) -> Dict[str, Optional[str]]:
    """Read date and original release year tags from an audio file."""
    res = {'date': None, 'original_date': None, 'year': None}
    if not file_path or not os.path.isfile(file_path):
        return res

    symbols = get_mutagen_symbols()
    if not symbols:
        return res

    try:
        audio = symbols.File(file_path)
        if audio is None:
            return res

        # ID3 (MP3, AIFF)
        if hasattr(audio, 'tags') and isinstance(audio.tags, symbols.ID3):
            # Date / Recording Time
            tdrc = audio.tags.get('TDRC')
            if tdrc:
                res['date'] = str(tdrc)
            tyer = audio.tags.get('TYER')
            if tyer:
                res['year'] = str(tyer)
            # Original Date / Year
            tdor = audio.tags.get('TDOR')
            if tdor:
                res['original_date'] = str(tdor)
            for k in audio.tags:
                if k.startswith('TXXX:'):
                    desc = k[5:].strip().lower()
                    if desc in ('originaldate', 'original date', 'original release date'):
                        res['original_date'] = str(audio.tags[k])
                    elif desc in ('originalyear', 'original year'):
                        res['original_year'] = str(audio.tags[k])

        # Vorbis / FLAC / OGG
        elif hasattr(audio, 'get'):
            for date_key in ('DATE', 'date'):
                v = audio.get(date_key)
                if v:
                    res['date'] = str(v[0])
                    break
            for yr_key in ('YEAR', 'year'):
                v = audio.get(yr_key)
                if v:
                    res['year'] = str(v[0])
                    break
            for orig_key in ('ORIGINALDATE', 'originaldate', 'ORIGINAL_DATE'):
                v = audio.get(orig_key)
                if v:
                    res['original_date'] = str(v[0])
                    break
            for orig_yr_key in ('ORIGINALYEAR', 'originalyear', 'ORIGINAL_YEAR'):
                v = audio.get(orig_yr_key)
                if v:
                    res['original_year'] = str(v[0])
                    break

        # MP4 / M4A
        if hasattr(audio, 'get') and hasattr(symbols, 'MP4') and isinstance(audio, symbols.MP4):
            day = audio.get('\xa9day')
            if day:
                res['date'] = str(day[0])
            orig = audio.get('----:com.apple.iTunes:originaldate')
            if orig:
                val = orig[0]
                res['original_date'] = val.decode('utf-8') if isinstance(val, bytes) else str(val)
            orig_yr = audio.get('----:com.apple.iTunes:originalyear')
            if orig_yr:
                val = orig_yr[0]
                res['original_year'] = val.decode('utf-8') if isinstance(val, bytes) else str(val)

    except Exception as e:
        logger.debug("Failed to read year tags from %s: %s", file_path, e)

    return res


def write_release_year_tags(
    file_path: str,
    canonical_year: str,
    canonical_date: Optional[str] = None,
    update_date_tag: bool = True,
) -> bool:
    """Atomically write canonical original release year/date tags to an audio file."""
    if not file_path or not os.path.isfile(file_path):
        return False

    symbols = get_mutagen_symbols()
    if not symbols:
        return False

    try:
        audio = symbols.File(file_path)
        if audio is None:
            return False

        full_date = canonical_date or canonical_year
        year_str = str(canonical_year)

        # ID3 tags
        if hasattr(audio, 'tags') and isinstance(audio.tags, symbols.ID3):
            from mutagen.id3 import TDOR, TDRC, TXXX, TYER

            # Original date frames
            try:
                audio.tags.setall('TDOR', [TDOR(encoding=3, text=[full_date])])
            except Exception as e:
                logger.debug("Failed setting TDOR frame: %s", e)
            audio.tags.add(TXXX(encoding=3, desc='originaldate', text=[full_date]))
            audio.tags.add(TXXX(encoding=3, desc='originalyear', text=[year_str]))

            if update_date_tag:
                try:
                    audio.tags.setall('TDRC', [TDRC(encoding=3, text=[full_date])])
                    audio.tags.setall('TYER', [TYER(encoding=3, text=[year_str])])
                except Exception as e:
                    logger.debug("Failed setting TDRC/TYER frames: %s", e)

        # MP4 tags
        elif hasattr(symbols, 'MP4') and isinstance(audio, symbols.MP4):
            audio['----:com.apple.iTunes:originaldate'] = [symbols.MP4FreeForm(full_date.encode('utf-8'))]
            audio['----:com.apple.iTunes:originalyear'] = [symbols.MP4FreeForm(year_str.encode('utf-8'))]
            if update_date_tag:
                audio['\xa9day'] = [full_date]

        # Vorbis / FLAC / OGG tags
        elif hasattr(audio, '__setitem__'):
            audio['ORIGINALDATE'] = [full_date]
            audio['ORIGINALYEAR'] = [year_str]
            if update_date_tag:
                audio['DATE'] = [full_date]
                audio['YEAR'] = [year_str]

        return save_audio_file(audio, symbols)
    except Exception as e:
        logger.error("Failed to write year tags to %s: %s", file_path, e)
        return False


def rename_album_folder(
    db: Any,
    old_folder: str,
    new_folder_name: str,
    album_id: int,
) -> Optional[str]:
    """Safely rename the album directory and update track file paths in the database."""
    if not old_folder or not os.path.isdir(old_folder) or not new_folder_name:
        return None

    parent_dir = os.path.dirname(old_folder)
    new_folder = os.path.join(parent_dir, new_folder_name)

    if os.path.normpath(old_folder) == os.path.normpath(new_folder):
        return old_folder

    if os.path.exists(new_folder):
        logger.warning(
            "Cannot rename folder '%s' to '%s': destination already exists",
            old_folder, new_folder,
        )
        return None

    try:
        os.rename(old_folder, new_folder)
        logger.info("Renamed album folder: '%s' -> '%s'", old_folder, new_folder)
    except Exception as e:
        logger.error("Failed to rename album folder '%s' to '%s': %s", old_folder, new_folder, e)
        return None

    # Repoint the album's file rows. Every file of the album, not only the
    # ones the finding listed: the whole folder moved.
    if db:
        conn = None
        try:
            with closing(db._get_connection()) as conn:
                rows = conn.execute("""
                    SELECT f.id, f.path FROM lib2_track_files f
                    JOIN lib2_tracks t ON t.id = f.track_id
                    WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') <> 'deleted'
                """, (album_id,)).fetchall()
                for file_id, old_fp in rows:
                    new_fp = _renamed_path(old_fp, old_folder, new_folder, new_folder_name)
                    if new_fp and new_fp != old_fp:
                        conn.execute(
                            "UPDATE lib2_track_files SET path = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = ?", (new_fp, file_id))
                conn.commit()
        except Exception as e:
            # The catalogue still points into the old folder: put it back, or
            # every file of the album reads as missing on the next scan.
            logger.error("Error updating file paths in the catalogue after folder rename: %s", e)
            try:
                os.rename(new_folder, old_folder)
            except OSError as undo:
                logger.error("Could not restore '%s' after the failed update: %s", old_folder, undo)
            return None

    return new_folder


def _renamed_path(old_fp: Optional[str], old_folder: str, new_folder: str,
                  new_folder_name: str) -> Optional[str]:
    """``old_fp`` as it reads after ``old_folder`` became ``new_folder``.

    Exact prefix first (host or relative paths); otherwise the folder name as a
    whole path segment, for Docker / media-server paths that share no prefix.
    """
    if not old_fp:
        return None
    old_norm = os.path.normpath(old_folder)
    old_basename = os.path.basename(old_folder)
    fp_norm = os.path.normpath(old_fp)
    new_fp = None
    if fp_norm.startswith(old_norm + os.sep):
        new_fp = os.path.join(new_folder, fp_norm[len(old_norm):].lstrip(os.sep))
    elif old_basename in old_fp:
        pattern = re.compile(rf'(?<=[\/\\]){re.escape(old_basename)}(?=[\/\\])')
        if pattern.search(old_fp):
            new_fp = pattern.sub(lambda _m: new_folder_name, old_fp)
        elif old_fp.startswith(old_basename + '/') or old_fp.startswith(old_basename + '\\'):
            new_fp = new_folder_name + old_fp[len(old_basename):]
    if new_fp and '/' in old_fp and '\\' not in old_fp:
        new_fp = new_fp.replace('\\', '/')
    return new_fp


def apply_album_year_fix(
    db: Any,
    album_id: int,
    canonical_year: str,
    canonical_date: Optional[str] = None,
    tracks: Optional[List[Dict[str, Any]]] = None,
    transfer_folder: Optional[str] = None,
    config_manager: Optional[Any] = None,
    rename_folders: bool = True,
    update_date_tag: bool = True,
    folder_path: Optional[str] = None,
    new_folder_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the full year alignment: retag audio files, update DB, and rename folder."""
    result = {'success': False, 'fixed_files': 0, 'errors': 0, 'changes': [], 'renamed_folder': None}
    download_folder = config_manager.get('soulseek.download_path', '') if config_manager else ''

    year_int = int(canonical_year) if canonical_year and canonical_year.isdigit() else None
    if not year_int:
        return {'success': False, 'error': f'Invalid canonical year: {canonical_year}'}

    # 1. Retag audio files
    for t in (tracks or []):
        fp = t.get('file_path')
        if not fp:
            continue
        resolved = resolve_library_file_path(
            fp,
            transfer_folder=transfer_folder,
            download_folder=download_folder,
            config_manager=config_manager,
        )
        if not resolved or not os.path.isfile(resolved):
            continue

        if write_release_year_tags(resolved, str(year_int), canonical_date, update_date_tag=update_date_tag):
            result['fixed_files'] += 1
            result['changes'].append(f"Retagged {os.path.basename(resolved)} with year {year_int}")
        else:
            result['errors'] += 1

    # 2. Update the album's year in the catalogue
    if db:
        try:
            with closing(db._get_connection()) as conn:
                conn.execute(
                    "UPDATE lib2_albums SET year = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (year_int, album_id),
                )
                conn.commit()
            result['changes'].append(f"Updated the album year to {year_int}")
        except Exception as e:
            logger.error("Failed to update database year for album %s: %s", album_id, e)
            result['errors'] += 1

    # 3. Rename parent folder if requested and exclusive
    if rename_folders and folder_path and new_folder_name:
        if is_folder_exclusive_to_album(db, folder_path, album_id, transfer_folder=transfer_folder):
            renamed = rename_album_folder(db, folder_path, new_folder_name, album_id)
            if renamed:
                result['renamed_folder'] = renamed
                result['changes'].append(f"Renamed folder to '{new_folder_name}'")
        else:
            logger.info("Folder '%s' contains tracks from other albums; skipping rename", folder_path)

    result['success'] = result['fixed_files'] > 0 or len(result['changes']) > 0
    return result


def _same_title(a: Any, b: Any) -> bool:
    return str(a or '').strip().casefold() == str(b or '').strip().casefold()


def release_group_holding_tracks(
    mb_client: Any,
    album_title: str,
    artist_name: str,
    track_titles: Optional[List[str]],
    max_titles: int = 2,
) -> Optional[Tuple[str, Optional[str]]]:
    """the one release group, titled like the album, whose releases carry the
    library's tracks. (rg_mbid, a release mbid in it) or None when the tracks
    don't settle it.

    weezer has eight albums called "Weezer". the title alone picked the red
    album (2008) for buddy holly, which is on the blue album (1994) (#1609).
    """
    hits: Dict[str, int] = {}
    a_release: Dict[str, Optional[str]] = {}
    for title in [t for t in (track_titles or []) if t][:max_titles]:
        try:
            recordings = mb_client.search_recording(title, artist_name, limit=25)
        except Exception as e:
            logger.debug("recording search for '%s' failed: %s", title, e)
            continue
        if not isinstance(recordings, list):
            continue
        groups = set()
        for rec in recordings:
            if not isinstance(rec, dict) or not _same_title(rec.get('title'), title):
                continue
            for rel in rec.get('releases') or []:
                if not isinstance(rel, dict) or not _same_title(rel.get('title'), album_title):
                    continue
                rg_id = (rel.get('release-group') or {}).get('id')
                if rg_id:
                    groups.add(rg_id)
                    a_release.setdefault(rg_id, rel.get('id'))
        for rg_id in groups:
            hits[rg_id] = hits.get(rg_id, 0) + 1
        ranked = sorted(hits.items(), key=lambda kv: -kv[1])
        # one clear winner, else let the next track break the tie
        if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            return ranked[0][0], a_release.get(ranked[0][0])
    return None


def resolve_canonical_album_year(
    mb_client: Any,
    album_title: str,
    artist_name: str,
    musicbrainz_release_id: Optional[str] = None,
    barcode: Optional[str] = None,
    track_count: int = 0,
    prefer_original_year: bool = True,
    memo: Optional[Dict[str, Any]] = None,
    track_titles: Optional[List[str]] = None,
) -> Optional[Tuple[str, Optional[str], Optional[str], Optional[str]]]:
    """Resolve the canonical release year from MusicBrainz.

    ``track_titles`` (the library's tracks on this album) settle which album
    is meant when the artist has several with the same title (#1609).

    Returns:
        (canonical_year, canonical_date, release_mbid, release_group_mbid) or None.
    """
    if mb_client is None:
        return None

    memo_key = (
        (musicbrainz_release_id or '').strip(),
        (barcode or '').strip(),
        (album_title or '').strip().lower(),
        (artist_name or '').strip().lower(),
        # two same-titled albums in one library are different answers
        tuple(sorted(str(t).casefold() for t in (track_titles or []))),
    )
    if memo is not None and memo_key in memo:
        return memo[memo_key]

    def _cache_and_return(val):
        if memo is not None:
            memo[memo_key] = val
        return val

    def _from_track_group(found):
        """the year of the release group the tracks are on."""
        rg_id, rel_id = found
        try:
            rg = mb_client.get_release_group(rg_id) or {}
        except Exception as e:
            logger.debug("release group %s lookup failed: %s", rg_id, e)
            return None
        rg_date = rg.get('first-release-date')
        year = extract_year(rg_date)
        if not year:
            return None
        return (year, rg_date, rel_id, rg_id)

    def _tracks_say_otherwise(rg_id):
        """a release group picked by title alone, checked against the tracks.
        a better answer when the tracks sit on a different same-titled album,
        else None."""
        if not track_titles:
            return None
        found = release_group_holding_tracks(mb_client, album_title, artist_name, track_titles)
        if not found or found[0] == rg_id:
            return None
        return _from_track_group(found)

    # 1. Pinned or known MusicBrainz release ID
    if musicbrainz_release_id:
        try:
            rel = mb_client.get_release(musicbrainz_release_id, includes=['release-groups'])
            if rel:
                rg = rel.get('release-group') or {}
                # a stored id can come from a title-only match too. a
                # disambiguated group ("Red Album") means same-titled albums
                # exist, so check the tracks really are on it
                if rg.get('disambiguation') and prefer_original_year:
                    better = _tracks_say_otherwise(rg.get('id'))
                    if better:
                        return _cache_and_return(better)
                rg_date = rg.get('first-release-date')
                rel_date = rel.get('date')
                chosen_date = (rg_date if prefer_original_year and rg_date else (rel_date or rg_date))
                year = extract_year(chosen_date)
                if year:
                    return _cache_and_return((year, chosen_date, musicbrainz_release_id, rg.get('id')))
        except Exception as e:
            logger.debug("MusicBrainz lookup by release ID failed: %s", e)

    # 2. Barcode search
    if barcode:
        try:
            barcode_results = mb_client.search_release_by_barcode(barcode, limit=5)
            if barcode_results:
                best = barcode_results[0]
                rg = best.get('release-group') or {}
                rg_date = rg.get('first-release-date')
                rel_date = best.get('date')
                # If first-release-date is missing on search hit, fetch full release details
                if prefer_original_year and not rg_date and best.get('id'):
                    try:
                        full_rel = mb_client.get_release(best['id'], includes=['release-groups'])
                        if full_rel:
                            rg = full_rel.get('release-group') or rg
                            rg_date = rg.get('first-release-date')
                            rel_date = full_rel.get('date') or rel_date
                    except Exception as e:
                        logger.debug("Failed fetching full release details for barcode hit: %s", e)
                chosen_date = (rg_date if prefer_original_year and rg_date else (rel_date or rg_date))
                year = extract_year(chosen_date)
                if year:
                    return _cache_and_return((year, chosen_date, best.get('id'), rg.get('id')))
        except Exception as e:
            logger.debug("MusicBrainz lookup by barcode failed: %s", e)

    # 3. Search by title and artist
    if album_title and artist_name:
        try:
            search_results = mb_client.search_release(album_title, artist_name, limit=5)
            same_titled_groups = {
                (r.get('release-group') or {}).get('id')
                for r in (search_results or []) if isinstance(r, dict)
            } - {None}
            if len(same_titled_groups) > 1 and prefer_original_year:
                # several albums answer to this title. the first hit is a
                # coin toss, let the tracks decide or don't guess
                found = release_group_holding_tracks(mb_client, album_title, artist_name, track_titles)
                return _cache_and_return(_from_track_group(found) if found else None)
            if search_results:
                best = search_results[0]
                rg = best.get('release-group') or {}
                rg_date = rg.get('first-release-date')
                rel_date = best.get('date')
                if prefer_original_year and not rg_date and best.get('id'):
                    try:
                        full_rel = mb_client.get_release(best['id'], includes=['release-groups'])
                        if full_rel:
                            rg = full_rel.get('release-group') or rg
                            rg_date = rg.get('first-release-date')
                            rel_date = full_rel.get('date') or rel_date
                    except Exception as e:
                        logger.debug("Failed fetching full release details for search hit: %s", e)
                chosen_date = (rg_date if prefer_original_year and rg_date else (rel_date or rg_date))
                year = extract_year(chosen_date)
                if year:
                    return _cache_and_return((year, chosen_date, best.get('id'), rg.get('id')))
        except Exception as e:
            logger.debug("MusicBrainz lookup by release search failed: %s", e)

    return _cache_and_return(None)


@register_job
class AlbumReleaseYearRepairJob(RepairJob):
    job_id = 'album_release_year_repair'
    display_name = 'Album Release Year Alignment'
    description = 'Aligns album and track release years to canonical release dates and corrects folder years'
    help_text = (
        'Scans your library for albums where the release year in the database, audio file tags, '
        'or parent folder names do not match the canonical original release date from MusicBrainz.\n\n'
        'Often reissues, remasters, or modern digital drops get tagged with their reissue year '
        '(e.g. 2011 instead of 1978 for Queen\'s "Jazz"), scattering albums across your chronology '
        'and creating mismatched folder names.\n\n'
        'This tool resolves the canonical release group date on MusicBrainz (using stored MBIDs, '
        'commercial barcodes, or title matching with shared rate limiting), and if a discrepancy is detected:\n'
        '- Retags all audio files with ORIGINALDATE, ORIGINALYEAR, and DATE\n'
        '- Updates albums.year in the database\n'
        '- Renames the album folder if it contains the old year (e.g. "Jazz (2011)" -> "Jazz (1978)") '
        'and updates track paths in the database.\n\n'
        'Settings:\n'
        '- Dry Run: When enabled (default), reports proposed corrections without modifying files\n'
        '- Rename Folders: When enabled, updates parent folders that contain the old year\n'
        '- Update Date Tag: When enabled, writes canonical year to DATE/YEAR tags as well as ORIGINALDATE/ORIGINALYEAR\n'
        '- Prefer Original Year: When enabled, prefers Release Group original release date over edition date'
    )
    icon = 'repair-icon-consistency'
    default_enabled = False
    default_interval_hours = 168  # Weekly
    default_settings = {
        'dry_run': True,
        'rename_folders': True,
        'update_date_tag': True,
        'prefer_original_year': True,
    }
    auto_fix = True
    writes_library_files = True

    def _get_setting(self, context: JobContext, key: str, default: Any) -> Any:
        cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {}) if context.config_manager else {}
        if isinstance(cfg, dict) and key in cfg:
            return cfg[key]
        return self.default_settings.get(key, default)

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(active_album_subjects(context.db, context.config_manager))
        except Exception as e:
            logger.debug("estimate_scope query failed: %s", e)
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        dry_run = self._get_setting(context, 'dry_run', True)
        rename_folders = self._get_setting(context, 'rename_folders', True)
        update_date_tag = self._get_setting(context, 'update_date_tag', True)
        prefer_original_year = self._get_setting(context, 'prefer_original_year', True)

        mb_client = context.mb_client
        if mb_client is None:
            try:
                from core.musicbrainz_client import MusicBrainzClient
                mb_client = MusicBrainzClient()
            except Exception as e:
                logger.warning("No MusicBrainzClient available for release year alignment: %s", e)

        download_folder = context.config_manager.get('soulseek.download_path', '') if context.config_manager else ''

        try:
            files_by_album: Dict[int, List[Dict[str, Any]]] = {}
            for subject in drop_hand_tagged(context, active_file_subjects(
                    context.db, context.config_manager)):
                files_by_album.setdefault(int(subject['album_id']), []).append(subject)
            albums = [a for a in active_album_subjects(context.db, context.config_manager)
                      if int(a['album_id']) in files_by_album]
            albums.sort(key=lambda a: (str(a.get('artist_name') or '').casefold(),
                                       str(a.get('title') or '').casefold()))
            total = len(albums)

            if context.report_progress:
                mode_label = 'DRY RUN' if dry_run else 'LIVE'
                context.report_progress(
                    phase=f'Auditing release years for {total} albums ({mode_label})...',
                    log_line=f'Starting scan in {mode_label} mode ({total} albums)',
                    log_type='info',
                    scanned=0,
                    total=total,
                )

            memo: Dict[str, Any] = {}

            for idx, al in enumerate(albums):
                if context.check_stop():
                    break
                if idx % 10 == 0 and context.wait_if_paused():
                    break

                result.scanned += 1
                album_id = int(al['album_id'])
                album_title = al['title']
                artist_name = al['artist_name']
                db_year = str(al['album_year']).strip() if al.get('album_year') else None
                mb_release_id = (al.get('album_source_ids') or {}).get('musicbrainz')
                barcode = str(al.get('album_upc') or '').strip() or None

                # Hand-tagged files were dropped above; an album left without
                # files was dropped with them.
                eligible_tracks = [
                    {'id': f['track_id'], 'file_id': f['file_id'], 'file_path': f['path'],
                     'title': f['title']}
                    for f in files_by_album[album_id]
                ]
                tracks = eligible_tracks

                # Resolve file paths on disk
                resolved_paths = []
                for t in eligible_tracks:
                    res_p = resolve_library_file_path(
                        t['file_path'],
                        transfer_folder=context.transfer_folder,
                        download_folder=download_folder,
                        config_manager=context.config_manager,
                    )
                    if res_p and os.path.isfile(res_p):
                        resolved_paths.append(res_p)

                if not resolved_paths:
                    continue

                # Read existing tags on first file
                tag_meta = read_file_year_tags(resolved_paths[0])
                file_year = extract_year(tag_meta.get('original_date') or tag_meta.get('year') or tag_meta.get('date'))

                # Resolve canonical release year
                canonical = resolve_canonical_album_year(
                    mb_client=mb_client,
                    album_title=album_title,
                    artist_name=artist_name,
                    musicbrainz_release_id=mb_release_id,
                    barcode=barcode,
                    track_count=len(tracks),
                    prefer_original_year=prefer_original_year,
                    memo=memo,
                    track_titles=[t['title'] for t in eligible_tracks if t['title']],
                )

                if not canonical:
                    continue

                canonical_year, canonical_date, res_mbid, rg_mbid = canonical

                # Check parent folder year
                album_folder = find_album_folder(resolved_paths)
                folder_name = os.path.basename(album_folder) if album_folder else ''
                new_folder_name = compute_new_folder_name(
                    folder_name,
                    album_title=album_title,
                    old_year=db_year or file_year,
                    new_year=canonical_year,
                ) if folder_name else None

                needs_folder_rename = bool(
                    rename_folders
                    and new_folder_name
                    and new_folder_name != folder_name
                    and album_folder
                    and is_folder_exclusive_to_album(
                        context.db,
                        album_folder,
                        album_id,
                        transfer_folder=context.transfer_folder,
                        artist_name=artist_name,
                    )
                )

                # Check discrepancies
                year_discrepancy = bool(
                    (db_year and db_year != canonical_year)
                    or (file_year and file_year != canonical_year)
                    or (not tag_meta.get('original_date') and canonical_date)
                )

                if not year_discrepancy and not needs_folder_rename:
                    continue

                current_display_year = db_year or file_year or 'unknown'
                desc_parts = [f"Current year: {current_display_year} → Canonical: {canonical_year}"]
                if needs_folder_rename and new_folder_name:
                    desc_parts.append(f"Folder rename: '{folder_name}' → '{new_folder_name}'")

                track_items = eligible_tracks

                if dry_run:
                    if context.create_finding:
                        inserted = context.create_finding(
                            job_id=self.job_id,
                            finding_type='album_release_year_mismatch',
                            severity='info',
                            entity_type='album',
                            entity_id=f'lib2:{album_id}',
                            file_path=resolved_paths[0],
                            title=f"Release year mismatch: {album_title} by {artist_name}",
                            description=f"{len(eligible_tracks)} track(s) affected. " + "; ".join(desc_parts),
                            details={
                                **subject_details({'album_id': album_id,
                                                   'artist_id': al.get('artist_id')}),
                                'album_id': album_id,
                                'album_title': album_title,
                                'artist_name': artist_name,
                                'current_year': current_display_year,
                                'canonical_year': canonical_year,
                                'canonical_date': canonical_date,
                                'release_mbid': res_mbid,
                                'release_group_mbid': rg_mbid,
                                'tracks': track_items,
                                'folder_path': album_folder,
                                'new_folder_name': new_folder_name if needs_folder_rename else None,
                            },
                        )
                        if inserted:
                            result.findings_created += 1
                        else:
                            result.findings_skipped_dedup += 1

                    if context.report_progress:
                        context.report_progress(
                            log_line=f"Found: {album_title} ({current_display_year} -> {canonical_year})",
                            log_type='warning',
                        )
                else:
                    # Live fix
                    fix_res = apply_album_year_fix(
                        db=context.db,
                        album_id=album_id,
                        canonical_year=canonical_year,
                        canonical_date=canonical_date,
                        tracks=track_items,
                        transfer_folder=context.transfer_folder,
                        config_manager=context.config_manager,
                        rename_folders=rename_folders,
                        update_date_tag=update_date_tag,
                        folder_path=album_folder,
                        new_folder_name=new_folder_name if needs_folder_rename else None,
                    )
                    if fix_res.get('success'):
                        result.auto_fixed += 1
                        if context.report_progress:
                            context.report_progress(
                                log_line=f"Fixed: {album_title} → year {canonical_year}",
                                log_type='info',
                            )
                    else:
                        result.errors += 1

                if context.report_progress and (idx + 1) % 15 == 0:
                    context.report_progress(
                        scanned=idx + 1,
                        total=total,
                        phase=f"Auditing release years ({idx + 1}/{total})...",
                    )

        except Exception as e:
            logger.error("Error during album release year audit: %s", e, exc_info=True)
            result.errors += 1

        return result
