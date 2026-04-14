"""MBID Mismatch Detector — finds tracks with embedded MusicBrainz IDs that
don't match the track's actual title/artist.

When a wrong MBID is embedded, media servers like Navidrome use it to look up
metadata from MusicBrainz, overriding the file's correct title/artist tags.
This causes tracks to display with wrong names in the media server even though
SoulSync shows them correctly.
"""

import os
from difflib import SequenceMatcher

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.mbid_mismatch")

# Tag name → format mappings (must match web_server.py write logic)
_MBID_TAG_KEYS = {
    # MP3 (ID3): UFID frame with owner 'http://musicbrainz.org'
    'mp3_ufid_owner': 'http://musicbrainz.org',
    # FLAC/OGG: Vorbis comment key
    'vorbis': 'MUSICBRAINZ_TRACKID',
    # MP4/M4A: freeform key
    'mp4': '----:com.apple.iTunes:MusicBrainz Track Id',
}

TITLE_SIMILARITY_THRESHOLD = 0.55


def _normalize(s):
    """Lowercase, strip whitespace and common suffixes for comparison."""
    if not s:
        return ''
    import re
    s = s.lower().strip()
    # Strip parentheticals like (Live), (Remastered), (feat. X)
    s = re.sub(r'\s*\(.*?\)\s*', ' ', s)
    # Strip brackets like [Deluxe Edition]
    s = re.sub(r'\s*\[.*?\]\s*', ' ', s)
    return s.strip()


def _title_matches(file_title, mb_title):
    """Check if two titles are similar enough to be the same track."""
    a = _normalize(file_title)
    b = _normalize(mb_title)
    if not a or not b:
        return True  # Can't compare, assume OK
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= TITLE_SIMILARITY_THRESHOLD


def _read_file_tags(file_path):
    """Read the MusicBrainz recording MBID and embedded title from an audio file's tags.

    Returns (mbid_string, embedded_title, format_name) or (None, None, None) if not readable.
    The embedded_title may be None if no TITLE tag is present.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None, None, None

        if isinstance(audio.tags, ID3):
            # MP3: UFID frame for MBID
            mbid = None
            ufid_key = f'UFID:{_MBID_TAG_KEYS["mp3_ufid_owner"]}'
            ufid = audio.tags.get(ufid_key)
            if ufid and ufid.data:
                mbid = ufid.data.decode('ascii', errors='ignore')
            else:
                # Also check TXXX fallback (some taggers use this)
                for key in ['TXXX:MusicBrainz Track Id', 'TXXX:MUSICBRAINZ_TRACKID']:
                    txxx = audio.tags.get(key)
                    if txxx and txxx.text:
                        mbid = txxx.text[0]
                        break
            # Embedded title from TIT2 frame
            tit2 = audio.tags.get('TIT2')
            embedded_title = tit2.text[0] if tit2 and tit2.text else None
            return mbid, embedded_title, 'mp3' if mbid else None

        elif isinstance(audio, (FLAC, OggVorbis)):
            vals = audio.get(_MBID_TAG_KEYS['vorbis'], [])
            if not vals:
                vals = audio.get('musicbrainz_trackid', [])
            mbid = vals[0] if vals else None
            title_vals = audio.get('title', [])
            embedded_title = title_vals[0] if title_vals else None
            fmt = 'flac' if isinstance(audio, FLAC) else 'ogg'
            return mbid, embedded_title, fmt if mbid else None

        elif isinstance(audio, MP4):
            vals = audio.get(_MBID_TAG_KEYS['mp4'], [])
            mbid = None
            if vals:
                raw = vals[0]
                mbid = raw.decode('utf-8', errors='ignore') if isinstance(raw, bytes) else str(raw)
            title_vals = audio.get('\xa9nam', [])
            embedded_title = title_vals[0] if title_vals else None
            return mbid, embedded_title, 'mp4' if mbid else None

        return None, None, None
    except Exception as e:
        logger.debug("Error reading tags from %s: %s", file_path, e)
        return None, None, None


def _remove_mbid_from_file(file_path):
    """Remove the MusicBrainz recording MBID tag from an audio file.

    Returns True if tag was removed and file saved, False otherwise.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return False

        removed = False

        if isinstance(audio.tags, ID3):
            ufid_key = f'UFID:{_MBID_TAG_KEYS["mp3_ufid_owner"]}'
            if ufid_key in audio.tags:
                del audio.tags[ufid_key]
                removed = True
            for key in ['TXXX:MusicBrainz Track Id', 'TXXX:MUSICBRAINZ_TRACKID']:
                if key in audio.tags:
                    del audio.tags[key]
                    removed = True

        elif isinstance(audio, (FLAC, OggVorbis)):
            for key in [_MBID_TAG_KEYS['vorbis'], 'musicbrainz_trackid']:
                if key in audio:
                    del audio[key]
                    removed = True

        elif isinstance(audio, MP4):
            mp4_key = _MBID_TAG_KEYS['mp4']
            if mp4_key in audio:
                del audio[mp4_key]
                removed = True

        if removed:
            audio.save()
        return removed

    except Exception as e:
        logger.error("Error removing MBID from %s: %s", file_path, e)
        return False


def _resolve_file_path(file_path, transfer_folder, download_folder=None):
    """Resolve a stored DB path to an actual file on disk."""
    if not file_path:
        return None
    if os.path.exists(file_path):
        return file_path

    path_parts = file_path.replace('\\', '/').split('/')
    for base_dir in [transfer_folder, download_folder]:
        if not base_dir or not os.path.isdir(base_dir):
            continue
        for i in range(1, len(path_parts)):
            candidate = os.path.join(base_dir, *path_parts[i:])
            if os.path.exists(candidate):
                return candidate
    return None


@register_job
class MbidMismatchDetectorJob(RepairJob):
    job_id = 'mbid_mismatch_detector'
    display_name = 'MBID Mismatch Detector'
    description = 'Finds tracks with wrong MusicBrainz IDs that cause media server mismatches'
    help_text = (
        'Scans your library for tracks that have an embedded MusicBrainz recording ID '
        '(MBID) that doesn\'t match the track\'s actual title.\n\n'
        'When a wrong MBID is embedded in an audio file, media servers like Navidrome '
        'use it to look up metadata from MusicBrainz, overriding the file\'s correct '
        'title and artist tags. This causes tracks to display with wrong names in the '
        'media server even though SoulSync shows them correctly.\n\n'
        'The fix action removes the bad MBID tag from the audio file, allowing the media '
        'server to fall back to the file\'s actual title/artist tags.\n\n'
        'This job reads each audio file\'s tags and queries MusicBrainz to verify the '
        'embedded MBID points to the correct recording. Rate-limited to avoid overloading '
        'the MusicBrainz API.'
    )
    icon = 'repair-icon-mbid'
    default_enabled = False
    default_interval_hours = 168  # weekly
    default_settings = {
        'similarity_threshold': 0.55,
    }
    auto_fix = False

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        # Get all tracks with file paths
        tracks = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, ar.name, al.title, t.file_path,
                       al.thumb_url, ar.thumb_url
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
            """)
            tracks = cursor.fetchall()
        except Exception as e:
            logger.error("Error fetching tracks: %s", e, exc_info=True)
            result.errors += 1
            return result
        finally:
            if conn:
                conn.close()

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Scanning {total} tracks for MBID mismatches...', total=total)

        download_folder = None
        if context.config_manager:
            download_folder = context.config_manager.get('soulseek.download_path', '')

        # We need a MusicBrainz client for MBID lookups
        mb_client = None
        if context.mb_client:
            mb_client = context.mb_client
        else:
            try:
                from core.musicbrainz_client import MusicBrainzClient
                mb_client = MusicBrainzClient()
            except Exception:
                pass

        if not mb_client:
            logger.warning("MusicBrainz client not available, skipping MBID mismatch scan")
            if context.report_progress:
                context.report_progress(
                    log_line='MusicBrainz client not available — cannot verify MBIDs',
                    log_type='error'
                )
            return result

        checked = 0
        import time

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 100 == 0 and context.wait_if_paused():
                return result

            track_id, title, artist_name, album_title, file_path, album_thumb, artist_thumb = row

            if context.update_progress and (i + 1) % 50 == 0:
                context.update_progress(i + 1, total)

            # Resolve the file path
            resolved = _resolve_file_path(file_path, context.transfer_folder, download_folder)
            if not resolved:
                result.scanned += 1
                continue

            # Read MBID and embedded title from file tags
            mbid, embedded_title, fmt = _read_file_tags(resolved)
            if not mbid:
                result.scanned += 1
                continue

            # Use the embedded TITLE tag for comparison; fall back to DB title only if absent
            file_title = embedded_title if embedded_title else title

            # Validate the MBID against MusicBrainz
            checked += 1

            if context.report_progress and checked % 10 == 0:
                context.report_progress(
                    scanned=i + 1, total=total,
                    phase=f'Verifying MBIDs ({checked} checked, {i + 1}/{total} files)',
                    log_line=f'Checking: {title or "Unknown"} — {artist_name or "Unknown"}',
                    log_type='info'
                )

            try:
                # Rate limit: MusicBrainz allows ~1 req/sec
                if context.sleep_or_stop(1.1):
                    return result

                recording = mb_client.get_recording(mbid, includes=['artist-credits'])
                if not recording:
                    # MBID doesn't exist — definitely wrong
                    self._create_mismatch_finding(
                        context, result, track_id, title, artist_name, album_title,
                        resolved, album_thumb, artist_thumb, mbid,
                        mb_title='[MBID not found]', mb_artist='[Unknown]',
                        reason='MBID does not exist in MusicBrainz'
                    )
                    result.scanned += 1
                    continue

                mb_title = recording.get('title', '')
                mb_artists = recording.get('artist-credit', [])
                mb_artist = ''
                if mb_artists:
                    for credit in mb_artists:
                        if isinstance(credit, dict) and 'artist' in credit:
                            mb_artist = credit['artist'].get('name', '')
                            break

                # Compare: does the MBID's title match the file's embedded title?
                if not _title_matches(file_title, mb_title):
                    self._create_mismatch_finding(
                        context, result, track_id, title, artist_name, album_title,
                        resolved, album_thumb, artist_thumb, mbid,
                        mb_title=mb_title, mb_artist=mb_artist,
                        reason=f'MBID points to "{mb_title}" by {mb_artist}, expected "{file_title}"'
                    )

            except Exception as e:
                logger.debug("Error verifying MBID %s for track %s: %s", mbid, track_id, e)
                # Don't count as error — could be transient network issue

            result.scanned += 1

        if context.update_progress:
            context.update_progress(total, total)

        logger.info("MBID mismatch scan: %d files scanned, %d with MBIDs verified, %d mismatches found",
                     total, checked, result.findings_created)

        if context.report_progress:
            context.report_progress(
                scanned=total, total=total,
                phase='Complete',
                log_line=f'Verified {checked} MBIDs — {result.findings_created} mismatches found',
                log_type='success' if result.findings_created == 0 else 'warning'
            )

        return result

    def _create_mismatch_finding(self, context, result, track_id, title, artist_name,
                                  album_title, file_path, album_thumb, artist_thumb,
                                  mbid, mb_title, mb_artist, reason):
        """Create a finding for a mismatched MBID."""
        if context.report_progress:
            context.report_progress(
                log_line=f'Mismatch: "{title}" has MBID for "{mb_title}"',
                log_type='error'
            )
        if context.create_finding:
            try:
                context.create_finding(
                    job_id=self.job_id,
                    finding_type='mbid_mismatch',
                    severity='warning',
                    entity_type='track',
                    entity_id=str(track_id),
                    file_path=file_path,
                    title=f'MBID mismatch: {title or "Unknown"}',
                    description=(
                        f'Track "{title}" by {artist_name or "Unknown"} has an embedded '
                        f'MusicBrainz ID that points to "{mb_title}" by {mb_artist}. '
                        f'This causes media servers like Navidrome to display the wrong track name.'
                    ),
                    details={
                        'track_id': track_id,
                        'title': title,
                        'artist': artist_name,
                        'album': album_title,
                        'file_path': file_path,
                        'mbid': mbid,
                        'mb_title': mb_title,
                        'mb_artist': mb_artist,
                        'reason': reason,
                        'album_thumb_url': album_thumb or None,
                        'artist_thumb_url': artist_thumb or None,
                    }
                )
                result.findings_created += 1
            except Exception as e:
                logger.debug("Error creating MBID mismatch finding for track %s: %s", track_id, e)
                result.errors += 1

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tracks WHERE file_path IS NOT NULL AND file_path != ''")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            if conn:
                conn.close()
