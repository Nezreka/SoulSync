"""MBID Mismatch Detector — finds tracks with embedded MusicBrainz IDs that
don't match the track's actual title/artist.

When a wrong MBID is embedded, media servers like Navidrome use it to look up
metadata from MusicBrainz, overriding the file's correct title/artist tags.
This causes tracks to display with wrong names in the media server even though
SoulSync shows them correctly.
"""

import os
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Optional

from core.library.path_resolver import resolve_library_file_path
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.mbid_mismatch")

# Tag name → format mappings for the TRACK MBID (existing detection).
# Must match web_server.py write logic.
_MBID_TAG_KEYS = {
    # MP3 (ID3): UFID frame with owner 'http://musicbrainz.org'
    'mp3_ufid_owner': 'http://musicbrainz.org',
    # FLAC/OGG: Vorbis comment key
    'vorbis': 'MUSICBRAINZ_TRACKID',
    # MP4/M4A: freeform key
    'mp4': '----:com.apple.iTunes:MusicBrainz Track Id',
}

# Tag name → format mappings for the ALBUM MBID (new in this PR).
# Same Picard standards as `core/metadata/source.py:ID3_TAG_MAP` etc.
_ALBUM_MBID_TAG_KEYS = {
    'mp3_txxx_desc': 'MusicBrainz Album Id',           # ID3 TXXX frame description
    'vorbis': 'MUSICBRAINZ_ALBUMID',                    # FLAC/OGG vorbis comment
    'mp4': '----:com.apple.iTunes:MusicBrainz Album Id',
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


def _read_album_mbid_from_file(file_path: str) -> Optional[str]:
    """Read the embedded MusicBrainz Album Id from an audio file's tags.

    Mirrors `_read_file_tags` but for the ALBUM MBID. Returns None when
    the file has no album MBID tag, when the file is unreadable, or
    when the tag format is unsupported.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return None

        if isinstance(audio.tags, ID3):
            txxx_key = f'TXXX:{_ALBUM_MBID_TAG_KEYS["mp3_txxx_desc"]}'
            tag = audio.tags.get(txxx_key)
            if tag and tag.text:
                value = tag.text[0]
                return str(value).strip() if value else None
            # Some taggers use lowercase variant
            txxx_key_lower = 'TXXX:MUSICBRAINZ_ALBUMID'
            tag = audio.tags.get(txxx_key_lower)
            if tag and tag.text:
                value = tag.text[0]
                return str(value).strip() if value else None
            return None

        if isinstance(audio, (FLAC, OggVorbis)):
            for key in (_ALBUM_MBID_TAG_KEYS['vorbis'], 'musicbrainz_albumid'):
                vals = audio.get(key, [])
                if vals:
                    value = vals[0]
                    return str(value).strip() if value else None
            return None

        if isinstance(audio, MP4):
            mp4_key = _ALBUM_MBID_TAG_KEYS['mp4']
            vals = audio.get(mp4_key, [])
            if vals:
                raw = vals[0]
                value = raw.decode('utf-8', errors='ignore') if isinstance(raw, bytes) else str(raw)
                return value.strip() if value else None
            return None

        return None
    except Exception as e:
        logger.debug("Error reading album MBID from %s: %s", file_path, e)
        return None


def _write_album_mbid_to_file(file_path: str, new_mbid: str) -> bool:
    """Rewrite the embedded MusicBrainz Album Id tag.

    Used by the fix action to bring a track's album MBID in line with
    the consensus across other tracks of the same album. Returns True
    when the tag was written and the file saved.
    """
    if not new_mbid:
        return False
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TXXX
        from mutagen.flac import FLAC
        from mutagen.oggvorbis import OggVorbis
        from mutagen.mp4 import MP4

        audio = MutagenFile(file_path)
        if audio is None:
            return False

        if isinstance(audio.tags, ID3):
            # Wipe any conflicting variants first so we don't end up with
            # two TXXX frames pointing at different MBIDs.
            for key in ('TXXX:MUSICBRAINZ_ALBUMID',
                        f'TXXX:{_ALBUM_MBID_TAG_KEYS["mp3_txxx_desc"]}'):
                if key in audio.tags:
                    del audio.tags[key]
            audio.tags.add(TXXX(
                encoding=3,
                desc=_ALBUM_MBID_TAG_KEYS['mp3_txxx_desc'],
                text=[new_mbid],
            ))
            audio.save()
            return True

        if isinstance(audio, (FLAC, OggVorbis)):
            for key in ('musicbrainz_albumid',):
                if key in audio:
                    del audio[key]
            audio[_ALBUM_MBID_TAG_KEYS['vorbis']] = [new_mbid]
            audio.save()
            return True

        if isinstance(audio, MP4):
            audio[_ALBUM_MBID_TAG_KEYS['mp4']] = [new_mbid.encode('utf-8')]
            audio.save()
            return True

        return False
    except Exception as e:
        logger.error("Error writing album MBID to %s: %s", file_path, e)
        return False


def _resolve_file_path(file_path, transfer_folder, download_folder=None, config_manager=None):
    """Backwards-compat wrapper. Use ``resolve_library_file_path`` directly."""
    return resolve_library_file_path(
        file_path,
        transfer_folder=transfer_folder,
        download_folder=download_folder,
        config_manager=config_manager,
    )


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
            resolved = _resolve_file_path(file_path, context.transfer_folder, download_folder,
                                           config_manager=context.config_manager)
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

        logger.info("MBID mismatch scan (track-level): %d files scanned, %d with MBIDs verified, %d mismatches found",
                     total, checked, result.findings_created)

        # Phase 2: Album MBID consistency check.
        #
        # Tracks of the same album that carry different MUSICBRAINZ_ALBUMID
        # tags cause Navidrome (and other media servers grouping by album
        # MBID) to split the album into multiple entries. Reported by user
        # Samuel [KC]. Detection strategy: group tracks by DB album_id,
        # find the consensus (most-common) album MBID, flag the dissenters.
        # No MusicBrainz API calls — this is a pure consistency check, so
        # it doesn't compete with the rate-limited track scan above.
        track_findings_so_far = result.findings_created
        self._scan_album_mbid_consistency(context, result, download_folder)
        album_findings = result.findings_created - track_findings_so_far

        if context.report_progress:
            context.report_progress(
                scanned=total, total=total,
                phase='Complete',
                log_line=(
                    f'Verified {checked} track MBIDs ({track_findings_so_far} mismatches) — '
                    f'album consistency check found {album_findings} dissenters'
                ),
                log_type='success' if result.findings_created == 0 else 'warning'
            )

        return result

    def _scan_album_mbid_consistency(self, context: JobContext, result: JobResult,
                                     download_folder: str) -> None:
        """Group tracks by DB album, flag tracks whose embedded album
        MBID differs from the consensus across the album's other tracks."""
        # Pull tracks grouped by album. Singles (album NULL) skipped — they
        # can't have a consistency issue.
        rows = []
        conn = None
        try:
            conn = context.db._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.title, t.album_id, t.file_path,
                       ar.name AS artist_name, al.title AS album_title,
                       al.thumb_url AS album_thumb, ar.thumb_url AS artist_thumb
                FROM tracks t
                LEFT JOIN artists ar ON ar.id = t.artist_id
                LEFT JOIN albums al ON al.id = t.album_id
                WHERE t.file_path IS NOT NULL AND t.file_path != ''
                  AND t.album_id IS NOT NULL
            """)
            rows = cursor.fetchall()
        except Exception as e:
            logger.error("Album MBID consistency scan: DB fetch failed: %s", e, exc_info=True)
            return
        finally:
            if conn:
                conn.close()

        if not rows:
            return

        # Group by album_id. Read each track's embedded album MBID — only
        # include rows where the read succeeded (skips files that don't
        # have an album MBID at all; those don't break Navidrome since
        # there's no MBID for it to disagree on).
        by_album: dict = defaultdict(list)
        for row in rows:
            if context.check_stop():
                return
            track_id = row['id']
            album_id = row['album_id']
            file_path = row['file_path']

            resolved = _resolve_file_path(
                file_path, context.transfer_folder, download_folder,
                config_manager=context.config_manager,
            )
            if not resolved:
                continue
            album_mbid = _read_album_mbid_from_file(resolved)
            if not album_mbid:
                continue
            by_album[album_id].append({
                'track_id': track_id,
                'title': row['title'],
                'album_title': row['album_title'],
                'artist_name': row['artist_name'],
                'album_thumb': row['album_thumb'],
                'artist_thumb': row['artist_thumb'],
                'file_path': file_path,
                'resolved': resolved,
                'album_mbid': album_mbid,
            })

        if context.report_progress:
            context.report_progress(
                phase=f'Checking album MBID consistency across {len(by_album)} albums...',
                log_type='info',
            )

        for album_id, tracks_in_album in by_album.items():
            if context.check_stop():
                return
            # Need at least 2 tracks to detect a mismatch.
            if len(tracks_in_album) < 2:
                continue

            mbid_counts = Counter(t['album_mbid'] for t in tracks_in_album)
            if len(mbid_counts) == 1:
                continue  # All tracks agree → nothing to flag

            consensus_mbid, consensus_count = mbid_counts.most_common(1)[0]

            # Defensive: if no MBID has a clear plurality (e.g. 3 tracks,
            # 3 different MBIDs), skip rather than picking a random one.
            # Counter.most_common returns ties in arbitrary order; we don't
            # want to fix a track to a "consensus" that's really a 1/N tie.
            second_count = mbid_counts.most_common(2)[1][1] if len(mbid_counts) > 1 else 0
            if consensus_count == second_count:
                logger.info(
                    "Album %s has tied album MBID counts %s — no clear consensus, skipping",
                    album_id, dict(mbid_counts),
                )
                continue

            for track in tracks_in_album:
                if track['album_mbid'] == consensus_mbid:
                    continue
                self._create_album_mbid_mismatch_finding(
                    context, result, track,
                    consensus_mbid=consensus_mbid,
                    consensus_count=consensus_count,
                    total_tracks=len(tracks_in_album),
                )

    def _create_album_mbid_mismatch_finding(self, context: JobContext, result: JobResult,
                                            track: dict, consensus_mbid: str,
                                            consensus_count: int, total_tracks: int) -> None:
        """Create a finding for a track whose album MBID disagrees with
        the consensus across the album's other tracks."""
        title = track['title']
        artist_name = track['artist_name']
        album_title = track['album_title']
        if context.report_progress:
            context.report_progress(
                log_line=(
                    f'Album MBID mismatch: "{title}" — has {track["album_mbid"][:8]}…, '
                    f'consensus is {consensus_mbid[:8]}… ({consensus_count}/{total_tracks} tracks)'
                ),
                log_type='warning'
            )
        if context.create_finding:
            try:
                context.create_finding(
                    job_id=self.job_id,
                    finding_type='album_mbid_mismatch',
                    severity='warning',
                    entity_type='track',
                    entity_id=str(track['track_id']),
                    file_path=track['file_path'],
                    title=f'Album MBID mismatch: {title or "Unknown"}',
                    description=(
                        f'Track "{title}" by {artist_name or "Unknown"} on album '
                        f'"{album_title or "Unknown"}" has a different '
                        f'MusicBrainz Album Id than the album\'s other tracks. '
                        f'This causes media servers like Navidrome to split the album.'
                    ),
                    details={
                        'track_id': track['track_id'],
                        'title': title,
                        'artist': artist_name,
                        'album': album_title,
                        'file_path': track['file_path'],
                        'wrong_mbid': track['album_mbid'],
                        'consensus_mbid': consensus_mbid,
                        'consensus_count': consensus_count,
                        'total_tracks_with_mbid': total_tracks,
                        'reason': (
                            f'{consensus_count}/{total_tracks} tracks of this album use '
                            f'MBID {consensus_mbid}; this track uses {track["album_mbid"]}'
                        ),
                        'album_thumb_url': track['album_thumb'] or None,
                        'artist_thumb_url': track['artist_thumb'] or None,
                    }
                )
                result.findings_created += 1
            except Exception as e:
                logger.debug("Error creating album MBID mismatch finding for track %s: %s",
                              track['track_id'], e)
                result.errors += 1

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
