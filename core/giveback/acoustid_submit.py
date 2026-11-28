"""send fingerprints of files soulsync is sure of to acoustid (#2335).

the same submission picard makes (https://acoustid.org/webservice, "submit"):
the fingerprint, the length and the musicbrainz recording id, under
soulsync's application key and the person's own acoustid user key, gzipped
as acoustid asks. a round sends one request with at most ``BATCH`` files, so
acoustid sees one call every few minutes from an install.

a file that is sent, or that is ruled out for good (its tags name another
recording, it failed a fingerprint check), is written down and never looked
at again for that recording. a file that is only unsure for now (no length,
no fingerprint yet) is left for the next walk.
"""

from __future__ import annotations

import gzip
import os
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from core import acoustid_account
from core.giveback import common, confidence, store, walk
from utils.logging_config import get_logger

logger = get_logger("giveback.acoustid")

KIND = 'acoustid'
SUBMIT_URL = f'{acoustid_account.API_BASE}/submit'
# files per request, and rows looked at per round
BATCH = 20
SCAN = 200
TIMEOUT = 60

# a ruling that won't change for this file and recording: write it down
_FINAL_REASONS = ("the file's tags name another recording", 'the file failed its fingerprint check')


def read_tags(path: str) -> Dict[str, Any]:
    from core.library.file_tags import read_embedded_tags
    return read_embedded_tags(path)


def fingerprint(path: str) -> Optional[Tuple[float, str]]:
    """(seconds, fingerprint) by chromaprint, or None. a file with more than
    two channels is skipped: chromaprint crashes on it."""
    try:
        from mutagen import File as MutagenFile
        info = getattr(MutagenFile(path), 'info', None)
        if info is not None and (getattr(info, 'channels', 2) or 2) > 2:
            return None
    except Exception as e:  # noqa: BLE001 - let chromaprint decide
        logger.debug("channel check failed for %s: %s", path, e)
    try:
        import core.acoustid_client  # noqa: F401 - finds or fetches fpcalc
        import acoustid
        seconds, fp = acoustid.fingerprint_file(path)
    except Exception as e:  # noqa: BLE001
        logger.debug("fingerprint failed for %s: %s", path, e)
        return None
    if isinstance(fp, bytes):
        fp = fp.decode('ascii', 'ignore')
    return (float(seconds), str(fp)) if fp else None


def resolve(path: str, cfg=None) -> Optional[str]:
    from core.library2.paths import resolve_lib2_path
    return resolve_lib2_path(path, cfg)


def build_params(entries: List[Dict[str, Any]], app: str) -> Dict[str, Any]:
    """the form acoustid's submit takes: one numbered set per file"""
    params: Dict[str, Any] = {
        'format': 'json',
        'client': app,
        'clientversion': common.app_version(),
    }
    for i, e in enumerate(entries):
        params[f'duration.{i}'] = str(int(round(e['duration'])))
        params[f'fingerprint.{i}'] = e['fingerprint']
        params[f'mbid.{i}'] = e['mbid']
        if e.get('fileformat'):
            params[f'fileformat.{i}'] = e['fileformat']
    return params


def post(params: Dict[str, Any], user: str) -> Dict[str, Any]:
    """one gzipped submit. answers acoustid's json, or an error dict shaped
    like acoustid's when the network or the body failed (code 0)."""
    import requests
    common.ACOUSTID_PACE.wait()
    body = gzip.compress(urlencode(params).encode('utf-8'))
    headers = {
        'Content-Encoding': 'gzip',
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': common.user_agent(),
        **acoustid_account.headers(user),
    }
    try:
        resp = requests.post(SUBMIT_URL, data=body, headers=headers, timeout=TIMEOUT)
    except requests.exceptions.RequestException as e:
        return {'status': 'error', 'error': {'code': 0, 'message': f'could not reach AcoustID: {type(e).__name__}'}}
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {'status': 'error', 'error': {'code': 0, 'message': f'AcoustID answered HTTP {resp.status_code}'}}
    return data


def ready(cfg=None) -> Tuple[bool, str]:
    if not common.enabled(common.ACOUSTID_SETTING, cfg):
        return False, 'off'
    if not acoustid_account.app_key(cfg):
        return False, 'SoulSync has no AcoustID application key'
    if not acoustid_account.user_key(cfg):
        return False, 'link your AcoustID account first (Settings, Connections, AcoustID)'
    return True, ''


def run_round(connect: Callable[[], Any], cfg=None, *,
              read_tags_fn: Callable[[str], Dict[str, Any]] = read_tags,
              fingerprint_fn: Callable[[str], Optional[Tuple[float, str]]] = fingerprint,
              resolve_fn: Callable[[str, Any], Optional[str]] = resolve,
              post_fn: Callable[[Dict[str, Any], str], Dict[str, Any]] = post,
              batch: int = BATCH, scan: int = SCAN) -> Dict[str, Any]:
    """one round: look at up to ``scan`` files, send up to ``batch``.
    answers {'sent', 'skipped', 'looked', 'error'}."""
    ok, why = ready(cfg)
    if not ok:
        return {'sent': 0, 'skipped': 0, 'looked': 0, 'error': '' if why == 'off' else why}
    app = acoustid_account.app_key(cfg)
    user = acoustid_account.user_key(cfg)
    entries: List[Dict[str, Any]] = []
    skipped = 0
    with closing(connect()) as conn:
        rows = walk.next_rows(conn, KIND, scan)
        upto = 0
        for row in rows:
            if len(entries) >= batch:
                break
            upto = row['file_id']
            path = resolve_fn(row['path'], cfg)
            if not path or not os.path.isfile(path):
                continue
            read = read_tags_fn(path) or {}
            tags = read.get('tags') if read.get('available') else {}
            # the cheap check first: a file ruled out by its tags or its length
            # in the tags is never fingerprinted
            measured = read.get('duration') or None
            early = confidence.judge(row, tags or {}, measured, check_length=bool(measured))
            if not early.ok:
                if early.reason in _FINAL_REASONS:
                    store.record(conn, KIND, store.file_subject(row['file_id']),
                                 common.mbid(row['mbid']), store.SKIPPED,
                                 detail=early.reason, track_id=row['track_id'])
                    skipped += 1
                continue
            fp = fingerprint_fn(path)
            if not fp:
                continue
            seconds, print_ = fp
            verdict = confidence.judge(row, tags or {}, seconds)
            if not verdict.ok:
                continue
            entries.append({
                'file_id': row['file_id'], 'track_id': row['track_id'],
                'mbid': common.mbid(row['mbid']), 'duration': seconds,
                'fingerprint': print_, 'fileformat': (row.get('format') or '').upper() or None,
            })
        conn.commit()
        if not entries:
            walk.advance(conn, KIND, rows, scan, upto)
            conn.commit()
            return {'sent': 0, 'skipped': skipped, 'looked': len(rows), 'error': ''}

        answer = post_fn(build_params(entries, app), user)
        if answer.get('status') != 'ok':
            code, message = acoustid_account.error_of(answer)
            told = acoustid_account.explain(code, message, cfg)
            logger.warning("AcoustID submission refused: %s (%s)", told['message'], code)
            # nothing written: the same files go again next round. the
            # cursor stays put too
            return {'sent': 0, 'skipped': skipped, 'looked': len(rows), 'error': told['message']}
        remote = {}
        for sub in answer.get('submissions') or []:
            if isinstance(sub, dict) and isinstance(sub.get('index'), int):
                remote[sub['index']] = sub.get('id')
        for i, e in enumerate(entries):
            store.record(conn, KIND, store.file_subject(e['file_id']), e['mbid'], store.SUBMITTED,
                         remote_id=str(remote[i]) if remote.get(i) is not None else None,
                         track_id=e['track_id'])
        walk.advance(conn, KIND, rows, scan, upto)
        conn.commit()
    logger.info("Gave %d fingerprints to AcoustID", len(entries))
    return {'sent': len(entries), 'skipped': skipped, 'looked': len(rows), 'error': ''}
