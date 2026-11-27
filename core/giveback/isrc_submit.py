"""send isrcs from file tags to musicbrainz recordings that lack them (#2335).

musicbrainz takes isrcs from an app through its xml web service under the
person's own account (https://musicbrainz.org/doc/MusicBrainz_API, "ISRC
submission"). a round:

1. reads the tags of files soulsync is sure of (core.giveback.confidence)
   and keeps the ones with an isrc in them, at most ``BATCH``.
2. asks musicbrainz for each recording's isrcs, one request a second, and
   drops the ones it already has.
3. sends the rest in one POST to /ws/2/recording. if musicbrainz refuses the
   batch, each one goes alone so one bad isrc can't hold back the others.

every request carries soulsync's user agent and goes through the one second
pace. a pair (isrc, recording) is written down once it is sent, found
already there or refused, and never sent again.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

from core.giveback import common, confidence, musicbrainz_oauth, store, walk
from core.giveback.acoustid_submit import _FINAL_REASONS, read_tags, resolve
from utils.logging_config import get_logger

logger = get_logger("giveback.isrc")

KIND = 'isrc'
WS = 'https://musicbrainz.org/ws/2'
BATCH = 25
SCAN = 200
TIMEOUT = 30
XML_NS = 'http://musicbrainz.org/ns/mmd-2.0#'


def build_xml(pairs: List[Tuple[str, str]]) -> str:
    """the body musicbrainz's isrc submission takes: one recording element per
    recording, its isrcs inside"""
    by_recording: Dict[str, List[str]] = {}
    for recording, code in pairs:
        codes = by_recording.setdefault(recording, [])
        if code not in codes:
            codes.append(code)
    parts = [f'<metadata xmlns={quoteattr(XML_NS)}>', '<recording-list>']
    for recording, codes in by_recording.items():
        parts.append(f'<recording id={quoteattr(recording)}>')
        parts.append(f'<isrc-list count="{len(codes)}">')
        parts.extend(f'<isrc id={quoteattr(c)}/>' for c in codes)
        parts.append('</isrc-list></recording>')
    parts.append('</recording-list></metadata>')
    return '<?xml version="1.0" encoding="UTF-8"?>' + ''.join(parts)


class MusicBrainzWS:
    """the two calls a round makes, paced and with soulsync's user agent"""

    def __init__(self, token: str, http=None, pace: Optional[common.Pacer] = None):
        self.token = token
        self.http = http
        self.pace = pace or common.MUSICBRAINZ_PACE

    def _h(self):
        if self.http is None:
            import requests
            self.http = requests
        return self.http

    def recording_isrcs(self, recording: str) -> Optional[List[str]]:
        """the recording's isrcs; None when musicbrainz has no such recording;
        raises on a network failure"""
        self.pace.wait()
        resp = self._h().get(f'{WS}/recording/{recording}', params={'inc': 'isrcs', 'fmt': 'json'},
                             headers={'User-Agent': common.user_agent(), 'Accept': 'application/json'},
                             timeout=TIMEOUT)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f'MusicBrainz answered HTTP {resp.status_code}')
        data = resp.json()
        return [common.isrc(x) for x in (data.get('isrcs') or []) if common.isrc(x)]

    def submit(self, pairs: List[Tuple[str, str]]) -> int:
        """POST the isrcs; answers the http status"""
        self.pace.wait()
        resp = self._h().post(
            f'{WS}/recording/', params={'client': common.client_param()},
            data=build_xml(pairs).encode('utf-8'),
            headers={'User-Agent': common.user_agent(),
                     'Content-Type': 'application/xml; charset=utf-8',
                     'Authorization': f'Bearer {self.token}'},
            timeout=TIMEOUT)
        return int(resp.status_code)


def ready(cfg=None) -> Tuple[bool, str]:
    if not common.enabled(common.ISRC_SETTING, cfg):
        return False, 'off'
    state = musicbrainz_oauth.status(cfg)
    if not state['available']:
        return False, state['message']
    if not state['connected']:
        return False, 'connect your MusicBrainz account first'
    return True, ''


def run_round(connect: Callable[[], Any], cfg=None, *,
              read_tags_fn: Callable[[str], Dict[str, Any]] = read_tags,
              resolve_fn: Callable[[str, Any], Optional[str]] = resolve,
              ws: Optional[MusicBrainzWS] = None,
              batch: int = BATCH, scan: int = SCAN) -> Dict[str, Any]:
    """answers {'sent', 'present', 'skipped', 'looked', 'error'}"""
    out = {'sent': 0, 'present': 0, 'skipped': 0, 'looked': 0, 'error': ''}
    ok, why = ready(cfg)
    if not ok:
        out['error'] = '' if why == 'off' else why
        return out
    if ws is None:
        token = musicbrainz_oauth.access_token(cfg)
        if not token:
            out['error'] = 'MusicBrainz did not accept the linked account; connect it again'
            return out
        ws = MusicBrainzWS(token)

    with closing(connect()) as conn:
        rows = walk.next_rows(conn, KIND, scan)
        out['looked'] = len(rows)
        found: List[Dict[str, Any]] = []
        keys = set()
        upto = 0
        for row in rows:
            if len(found) >= batch:
                break
            upto = row['file_id']
            recording = common.mbid(row['mbid'])
            path = resolve_fn(row['path'], cfg)
            if not path or not os.path.isfile(path):
                continue
            read = read_tags_fn(path) or {}
            tags = (read.get('tags') if read.get('available') else {}) or {}
            code = common.isrc(confidence.first_tag(tags, 'isrc'))
            if not code or (recording, code) in keys or store.seen(conn, KIND, code, recording):
                continue
            verdict = confidence.judge(row, tags, read.get('duration') or None)
            if not verdict.ok:
                if verdict.reason in _FINAL_REASONS:
                    store.record(conn, KIND, code, recording, store.SKIPPED,
                                 detail=verdict.reason, track_id=row['track_id'])
                    out['skipped'] += 1
                continue
            keys.add((recording, code))
            found.append({'mbid': recording, 'isrc': code, 'track_id': row['track_id']})
        conn.commit()

        to_send: List[Dict[str, Any]] = []
        try:
            known: Dict[str, Optional[List[str]]] = {}
            for item in found:
                if item['mbid'] not in known:
                    known[item['mbid']] = ws.recording_isrcs(item['mbid'])
                have = known[item['mbid']]
                if have is None:
                    store.record(conn, KIND, item['isrc'], item['mbid'], store.REJECTED,
                                 detail='MusicBrainz has no such recording', track_id=item['track_id'])
                elif item['isrc'] in have:
                    store.record(conn, KIND, item['isrc'], item['mbid'], store.PRESENT,
                                 track_id=item['track_id'])
                    out['present'] += 1
                else:
                    to_send.append(item)
            conn.commit()
            if to_send:
                _send(conn, ws, to_send, out)
        except Exception as e:  # noqa: BLE001 - nothing more written: next round retries
            logger.warning("ISRC give back stopped: %s", e)
            out['error'] = 'Could not reach MusicBrainz'
            conn.commit()
            return out
        if not out['error']:
            walk.advance(conn, KIND, rows, scan, upto)
        conn.commit()
    if out['sent']:
        logger.info("Gave %d ISRCs to MusicBrainz", out['sent'])
    return out


def _send(conn, ws: MusicBrainzWS, items: List[Dict[str, Any]], out: Dict[str, Any]) -> None:
    code = ws.submit([(i['mbid'], i['isrc']) for i in items])
    if code == 200:
        for i in items:
            store.record(conn, KIND, i['isrc'], i['mbid'], store.SUBMITTED, track_id=i['track_id'])
        out['sent'] += len(items)
        return
    if code in (401, 403):
        out['error'] = 'MusicBrainz did not accept the linked account; connect it again'
        return
    if code == 400 and len(items) > 1:
        # one bad isrc refuses the whole batch: try each alone
        for i in items:
            _send(conn, ws, [i], out)
            if out['error']:
                return
        return
    if code == 400:
        i = items[0]
        store.record(conn, KIND, i['isrc'], i['mbid'], store.REJECTED,
                     detail='MusicBrainz refused it', track_id=i['track_id'])
        return
    # 503 (slow down) or anything else: nothing written, next round retries
    out['error'] = f'MusicBrainz answered HTTP {code}; trying again later'
