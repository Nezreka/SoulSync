"""when soulsync is sure enough of a file's recording to tell anyone else.

a file is sure when all of these hold:

1. its track has a musicbrainz recording id.
2. it is the track's active, primary, master file, and it never failed a
   fingerprint check (no 'fail', no force import past one).
3. something besides a name search put that id there:
   - fingerprint: acoustid passed the file at import and named this
     recording among its matches, or
   - tags: the file's own musicbrainz track id tag (picard's, or soulsync's
     from musicbrainz) is this recording.
   a tag naming a different recording rules it out, whatever else agrees.
4. the file's length agrees with the track's length to within three seconds.

a name guess alone (an enrichment worker matching on title and artist) is
never enough: that is how a live take gets filed under the studio recording.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.giveback.common import mbid as as_mbid

# the most the file and the track may differ in length, in seconds
DURATION_TOLERANCE = 3.0


@dataclass
class Verdict:
    ok: bool
    reason: str = ''
    evidence: str = ''  # 'fingerprint' | 'tags'


def _fingerprint_mbids(pipeline_result_json: Any) -> List[str]:
    try:
        data = json.loads(pipeline_result_json or '{}')
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    found = data.get('acoustid_recording_mbids') or []
    return [m for m in (as_mbid(x) for x in found if isinstance(x, str)) if m]


def first_tag(tags: Dict[str, Any], key: str) -> str:
    """the first of a tag's values (the reader joins several with ', ')"""
    value = tags.get(key) if isinstance(tags, dict) else None
    if value is None:
        return ''
    return str(value).split(',')[0].strip()


def judge(row: Dict[str, Any], tags: Dict[str, Any], measured_seconds: Optional[float],
          *, check_length: bool = True) -> Verdict:
    """``row``: a candidate from :func:`candidates`. ``tags``: the file's tags
    as core.library.file_tags reads them. ``measured_seconds``: the file's
    length as fpcalc or mutagen measured it. ``check_length=False`` is the
    cheap first look, before a file is fingerprinted."""
    recording = as_mbid(row.get('mbid'))
    if not recording:
        return Verdict(False, 'no musicbrainz recording id')
    if row.get('verification_status') == 'force_imported' or row.get('acoustid_status') == 'fail':
        return Verdict(False, 'the file failed its fingerprint check')
    tagged = as_mbid(first_tag(tags or {}, 'musicbrainz_trackid'))
    if tagged and tagged != recording:
        return Verdict(False, "the file's tags name another recording")
    by_fingerprint = (row.get('acoustid_status') == 'pass'
                      and recording in _fingerprint_mbids(row.get('pipeline_result_json')))
    by_tags = tagged == recording
    if not (by_fingerprint or by_tags):
        return Verdict(False, 'identified by name only, not by fingerprint or tags')
    evidence = 'fingerprint' if by_fingerprint else 'tags'
    if not check_length:
        return Verdict(True, '', evidence)
    duration_ms = row.get('duration')
    try:
        duration_ms = float(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration_ms = None
    if not duration_ms or duration_ms <= 0:
        return Verdict(False, 'no track length to compare')
    if measured_seconds is None or measured_seconds <= 0:
        return Verdict(False, 'could not measure the file')
    gap = abs(float(measured_seconds) - duration_ms / 1000.0)
    if gap > DURATION_TOLERANCE:
        return Verdict(False, f'lengths differ by {gap:.0f} seconds')
    return Verdict(True, '', evidence)


_CANDIDATE_SQL = """
SELECT f.id AS file_id, f.path, f.acoustid_status, f.verification_status,
       f.pipeline_result_json, f.format,
       t.id AS track_id, t.musicbrainz_id AS mbid, t.duration, t.isrc AS track_isrc,
       t.title, t.track_number, t.disc_number,
       al.title AS album, al.release_date, ar.name AS artist,
       COALESCE(NULLIF(t.track_artist, ''), ar.name) AS track_artist
  FROM lib2_track_files f
  JOIN lib2_tracks t ON t.id = f.track_id
  JOIN lib2_albums al ON al.id = t.album_id
  JOIN lib2_artists ar ON ar.id = al.primary_artist_id
 WHERE f.id > ?
   AND f.file_state = 'active'
   AND f.is_primary = 1
   AND f.file_role = 'master'
   AND t.musicbrainz_id IS NOT NULL AND t.musicbrainz_id <> ''
   AND COALESCE(f.acoustid_status, '') <> 'fail'
   AND COALESCE(f.verification_status, '') <> 'force_imported'
   {extra}
 ORDER BY f.id
 LIMIT ?
"""


def candidates(conn: Any, after_id: int, limit: int, *, kind: str) -> List[Dict[str, Any]]:
    """the next files to look at, by file id, past ``after_id``. for acoustid
    a file already sent (or ruled out) for its current recording is left out
    here; for isrcs that is checked per isrc once the tags are read."""
    if kind == 'acoustid':
        extra = ("AND NOT EXISTS (SELECT 1 FROM giveback_submissions s WHERE s.kind='acoustid' "
                 "AND s.subject = 'file:' || f.id AND s.mbid = lower(t.musicbrainz_id))")
    else:
        extra = ""
    rows = conn.execute(_CANDIDATE_SQL.format(extra=extra), (int(after_id), int(limit))).fetchall()
    cols = ('file_id', 'path', 'acoustid_status', 'verification_status', 'pipeline_result_json',
            'format', 'track_id', 'mbid', 'duration', 'track_isrc', 'title', 'track_number',
            'disc_number', 'album', 'release_date', 'artist', 'track_artist')
    return [dict(zip(cols, tuple(r), strict=True)) for r in rows]
