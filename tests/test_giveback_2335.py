"""#2335: give back to acoustid and musicbrainz.

no network anywhere: every request goes to a recorded fake, and each test
checks the request's shape against what the services document
(acoustid.org/webservice, musicbrainz.org/doc/MusicBrainz_API,
musicbrainz.org/doc/Development/OAuth2, .../Release_Editor_Seeding).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import sqlite3
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import pytest

from core.giveback import (acoustid_submit, common, confidence, isrc_submit, musicbrainz_oauth,
                           release_seed, store)

MBID = '8f3471b5-7e6a-48da-86a9-c1c07a0f47ae'
OTHER = '11111111-2222-3333-4444-555555555555'
ISRC = 'GBAYE0601498'


class Cfg:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _on(**extra):
    base = {
        common.ACOUSTID_SETTING: True,
        common.ISRC_SETTING: True,
        common.RELEASES_SETTING: True,
        'acoustid.user_key': 'user-key',
        'acoustid.api_key': 'app-key',
        musicbrainz_oauth.CLIENT_ID_SETTING: 'app-id',
        musicbrainz_oauth.CLIENT_SECRET_SETTING: 'app-secret',
        musicbrainz_oauth.AUTH_SETTING: {'access_token': 'tok', 'refresh_token': 'ref',
                                         'expires_at': 9e12},
    }
    base.update(extra)
    return Cfg(**base)


@pytest.fixture(autouse=True)
def _no_env_overrides(monkeypatch):
    monkeypatch.delenv(musicbrainz_oauth.CLIENT_ID_ENV, raising=False)
    monkeypatch.delenv(musicbrainz_oauth.CLIENT_SECRET_ENV, raising=False)
    monkeypatch.setattr(musicbrainz_oauth, 'SOULSYNC_MB_CLIENT_ID', '')
    monkeypatch.setattr(musicbrainz_oauth, 'SOULSYNC_MB_CLIENT_SECRET', '')
    common.set_app_version('3.7.0+abc1234')


@pytest.fixture
def db(tmp_path):
    from core.library2.schema import ensure_library_v2_schema
    path = tmp_path / 'giveback.db'
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    store.ensure_schema(conn.cursor())
    store.ensure_schema(conn.cursor())  # idempotent
    conn.execute("INSERT INTO lib2_artists (id, name, sort_name, musicbrainz_id) "
                 "VALUES (7, 'Artist', 'Artist', ?)", (OTHER,))
    conn.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 7, 'Album')")
    conn.commit()
    conn.close()

    def connect():
        c = sqlite3.connect(str(path))
        c.row_factory = sqlite3.Row
        return c

    connect.path = path
    return connect


def _track(connect, tmp_path, n, *, mbid=MBID, status='pass', fp_mbids=(MBID,), duration=200000,
           verification=None):
    audio = tmp_path / f'{n}.flac'
    audio.write_bytes(b'audio')
    with connect() as c:
        c.execute("INSERT INTO lib2_tracks (id, album_id, title, track_number, duration, musicbrainz_id) "
                  "VALUES (?, 1, ?, ?, ?, ?)", (n, f'Song {n}', n, duration, mbid))
        c.execute("INSERT INTO lib2_track_files (id, track_id, path, format, is_primary, acoustid_status, "
                  "verification_status, pipeline_result_json) VALUES (?, ?, ?, 'flac', 1, ?, ?, ?)",
                  (n, n, str(audio), status, verification,
                   json.dumps({'acoustid_recording_mbids': list(fp_mbids)})))
    return str(audio)


def _row(**over):
    row = {'mbid': MBID, 'acoustid_status': 'pass', 'verification_status': 'verified',
           'pipeline_result_json': json.dumps({'acoustid_recording_mbids': [MBID]}),
           'duration': 200000}
    row.update(over)
    return row


# ── the confidence rule ──

def test_fingerprint_match_with_agreeing_length_is_sure():
    v = confidence.judge(_row(), {}, 201.5)
    assert v.ok and v.evidence == 'fingerprint'


def test_tags_match_is_sure_without_a_fingerprint():
    v = confidence.judge(_row(acoustid_status=None, pipeline_result_json='{}'),
                         {'musicbrainz_trackid': MBID.upper()}, 199)
    assert v.ok and v.evidence == 'tags'


def test_a_name_guess_is_never_enough():
    v = confidence.judge(_row(acoustid_status=None, pipeline_result_json='{}'), {}, 200)
    assert not v.ok and 'name only' in v.reason


def test_a_fingerprint_pass_for_another_recording_is_not_enough():
    v = confidence.judge(_row(pipeline_result_json=json.dumps({'acoustid_recording_mbids': [OTHER]})),
                         {}, 200)
    assert not v.ok


def test_tags_naming_another_recording_rule_it_out():
    v = confidence.judge(_row(), {'musicbrainz_trackid': OTHER}, 200)
    assert not v.ok and v.reason == "the file's tags name another recording"


@pytest.mark.parametrize('over', [{'acoustid_status': 'fail'},
                                  {'verification_status': 'force_imported'}])
def test_a_failed_fingerprint_check_rules_it_out(over):
    assert not confidence.judge(_row(**over), {'musicbrainz_trackid': MBID}, 200).ok


def test_lengths_must_agree_within_three_seconds():
    assert confidence.judge(_row(), {}, 203.0).ok
    v = confidence.judge(_row(), {}, 204.0)
    assert not v.ok and 'lengths differ' in v.reason
    assert not confidence.judge(_row(duration=None), {}, 200).ok
    assert not confidence.judge(_row(), {}, None).ok
    # the cheap first look skips the length
    assert confidence.judge(_row(), {}, None, check_length=False).ok


def test_ids_are_shape_checked():
    assert common.mbid('nope') == ''
    assert common.isrc('gb-aye-06-01498') == ISRC
    assert common.isrc('GBAYE060149') == ''


# ── the pace ──

def test_pacer_keeps_calls_apart():
    now = [100.0]
    slept: List[float] = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    pace = common.Pacer(1.1, clock=lambda: now[0], sleep=sleep)
    pace.wait()
    pace.wait()
    now[0] += 0.5
    pace.wait()
    now[0] += 5
    pace.wait()
    assert slept == [pytest.approx(1.1), pytest.approx(0.6)]


def test_user_agent_and_client_follow_musicbrainz_rules():
    assert common.user_agent() == 'SoulSync/3.7.0+abc1234 ( https://github.com/Nezreka/SoulSync )'
    # "application-version", and the version may not hold a dash
    common.set_app_version('3.7.0-beta+abc')
    assert common.client_param() == 'soulsync-3.7.0.beta'


# ── acoustid ──

def _fakes(tags=None, seconds=200.0):
    sent: List[Dict[str, Any]] = []

    def post(params, user):
        sent.append({'params': params, 'user': user})
        n = sum(1 for k in params if k.startswith('fingerprint.'))
        return {'status': 'ok', 'submissions': [{'index': i, 'status': 'pending', 'id': 500 + i}
                                                for i in range(n)]}

    kw = dict(
        read_tags_fn=lambda p: {'available': True, 'duration': seconds, 'tags': dict(tags or {})},
        fingerprint_fn=lambda p: (seconds, 'AQAAFP' + p[-7:]),
        resolve_fn=lambda p, cfg: p,
        post_fn=post,
    )
    return sent, kw


def test_acoustid_round_sends_a_batch_and_never_twice(db, tmp_path):
    for n in (1, 2, 3):
        _track(db, tmp_path, n)
    sent, kw = _fakes()
    out = acoustid_submit.run_round(db, _on(), batch=2, **kw)
    assert out['sent'] == 2 and len(sent) == 1
    params = sent[0]['params']
    assert params['client'] == acoustid_submit.acoustid_account.app_key(_on())
    assert params['clientversion'] == '3.7.0+abc1234'
    assert params['format'] == 'json'
    assert params['mbid.0'] == MBID and params['duration.0'] == '200'
    assert params['fingerprint.1'].startswith('AQAAFP') and params['fileformat.0'] == 'FLAC'
    assert 'fingerprint.2' not in params
    assert sent[0]['user'] == 'user-key'

    out = acoustid_submit.run_round(db, _on(), batch=2, **kw)
    assert out['sent'] == 1
    assert sent[1]['params']['fingerprint.0'].endswith('3.flac')

    # nothing left: a third round sends nothing, ever
    out = acoustid_submit.run_round(db, _on(), batch=2, **kw)
    assert out['sent'] == 0 and len(sent) == 2
    # a day later the walk starts again from the top: still nothing resent
    with db() as c:
        c.execute("DELETE FROM giveback_cursor")
    out = acoustid_submit.run_round(db, _on(), batch=2, **kw)
    assert out['sent'] == 0 and len(sent) == 2
    with db() as c:
        counts = store.counts(c)
        rows = c.execute("SELECT subject, remote_id FROM giveback_submissions ORDER BY id").fetchall()
    assert counts['acoustid']['submitted'] == 3
    assert [r[0] for r in rows] == ['file:1', 'file:2', 'file:3']
    assert rows[0][1] == '500'


def test_acoustid_round_skips_unsure_files(db, tmp_path):
    _track(db, tmp_path, 1, status=None, fp_mbids=())        # a name guess
    _track(db, tmp_path, 2, fp_mbids=(OTHER,))               # fingerprint says another
    _track(db, tmp_path, 3, duration=100000)                 # length disagrees
    _track(db, tmp_path, 4, status='fail')                   # failed check
    sent, kw = _fakes()
    out = acoustid_submit.run_round(db, _on(), **kw)
    assert out['sent'] == 0 and sent == []


def test_acoustid_writes_down_a_final_ruling(db, tmp_path):
    _track(db, tmp_path, 1)
    sent, kw = _fakes(tags={'musicbrainz_trackid': OTHER})
    acoustid_submit.run_round(db, _on(), **kw)
    with db() as c:
        row = c.execute("SELECT status, detail FROM giveback_submissions").fetchone()
    assert tuple(row) == ('skipped', "the file's tags name another recording")
    assert sent == []


def test_acoustid_refusal_writes_nothing_and_retries(db, tmp_path):
    _track(db, tmp_path, 1)
    _, kw = _fakes()
    kw['post_fn'] = lambda params, user: {'status': 'error', 'error': {'code': 6, 'message': 'invalid user API key'}}
    out = acoustid_submit.run_round(db, _on(), **kw)
    assert out['sent'] == 0 and out['error']
    with db() as c:
        assert store.counts(c)['acoustid']['submitted'] == 0
    sent, kw = _fakes()
    assert acoustid_submit.run_round(db, _on(), **kw)['sent'] == 1


def test_acoustid_off_or_unlinked_does_nothing(db, tmp_path):
    _track(db, tmp_path, 1)
    sent, kw = _fakes()
    assert acoustid_submit.run_round(db, _on(**{common.ACOUSTID_SETTING: False}), **kw)['sent'] == 0
    out = acoustid_submit.run_round(db, _on(**{'acoustid.user_key': ''}), **kw)
    assert out['sent'] == 0 and 'link your AcoustID account' in out['error']
    assert sent == []


def test_a_finished_walk_rests(db, tmp_path):
    _track(db, tmp_path, 1, status=None, fp_mbids=())
    sent, kw = _fakes()
    looked = []
    real = kw['read_tags_fn']
    kw['read_tags_fn'] = lambda p: looked.append(p) or real(p)
    acoustid_submit.run_round(db, _on(), **kw)
    acoustid_submit.run_round(db, _on(), **kw)
    assert len(looked) == 1


def test_acoustid_post_is_gzipped_with_the_user_key_in_a_header(monkeypatch):
    import requests
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {'status': 'ok', 'submissions': []}

    def fake_post(url, data=None, headers=None, timeout=None):
        seen.update(url=url, data=data, headers=headers)
        return Resp()

    monkeypatch.setattr(requests, 'post', fake_post)
    monkeypatch.setattr(common.ACOUSTID_PACE, 'wait', lambda: None)
    params = acoustid_submit.build_params(
        [{'duration': 201.4, 'fingerprint': 'AQAD', 'mbid': MBID, 'fileformat': 'FLAC'}], 'app')
    acoustid_submit.post(params, 'secret-user')
    assert seen['url'] == 'https://api.acoustid.org/v2/submit'
    assert seen['headers']['Content-Encoding'] == 'gzip'
    assert seen['headers']['Acoustid-User-Key'] == 'secret-user'
    assert seen['headers']['User-Agent'].startswith('SoulSync/')
    body = parse_qs(gzip.decompress(seen['data']).decode())
    assert body['client'] == ['app'] and body['duration.0'] == ['201']
    assert body['mbid.0'] == [MBID] and body['fingerprint.0'] == ['AQAD']
    assert 'user' not in body


# ── isrcs ──

def test_isrc_xml_matches_the_documented_shape():
    xml = isrc_submit.build_xml([(MBID, ISRC), (MBID, 'USRC17607839'), (OTHER, ISRC), (MBID, ISRC)])
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?><metadata xmlns="http://musicbrainz.org/ns/mmd-2.0#">')
    assert (f'<recording id="{MBID}"><isrc-list count="2"><isrc id="{ISRC}"/>'
            f'<isrc id="USRC17607839"/></isrc-list></recording>') in xml
    assert f'<recording id="{OTHER}"><isrc-list count="1">' in xml
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    assert root.tag == '{http://musicbrainz.org/ns/mmd-2.0#}metadata'


class FakeWS:
    def __init__(self, have=None, codes=None):
        self.have = have or {}
        self.codes = list(codes or [])
        self.looked: List[str] = []
        self.sent: List[List] = []

    def recording_isrcs(self, recording):
        self.looked.append(recording)
        return self.have.get(recording, [])

    def submit(self, pairs):
        self.sent.append(list(pairs))
        return self.codes.pop(0) if self.codes else 200


def _isrc_kw(tags_by_path):
    return dict(read_tags_fn=lambda p: {'available': True, 'duration': 200.0,
                                        'tags': tags_by_path.get(p, {})},
                resolve_fn=lambda p, cfg: p)


def test_isrc_round_sends_missing_and_skips_present(db, tmp_path):
    a = _track(db, tmp_path, 1)
    b = _track(db, tmp_path, 2, mbid=OTHER, fp_mbids=(OTHER,))
    c = _track(db, tmp_path, 3)
    tags = {a: {'isrc': ISRC}, b: {'isrc': 'US-RC1-76-07839'}, c: {}}
    ws = FakeWS(have={OTHER: ['USRC17607839']})
    out = isrc_submit.run_round(db, _on(), ws=ws, **_isrc_kw(tags))
    assert out['sent'] == 1 and out['present'] == 1
    assert ws.sent == [[(MBID, ISRC)]]
    ws2 = FakeWS()
    with db() as conn:
        conn.execute("DELETE FROM giveback_cursor")
    out = isrc_submit.run_round(db, _on(), ws=ws2, **_isrc_kw(tags))
    assert ws2.sent == [] and ws2.looked == []


def test_isrc_batch_refused_goes_one_by_one(db, tmp_path):
    a = _track(db, tmp_path, 1)
    b = _track(db, tmp_path, 2, mbid=OTHER, fp_mbids=(OTHER,))
    tags = {a: {'isrc': ISRC}, b: {'isrc': 'USRC17607839'}}
    ws = FakeWS(codes=[400, 200, 400])
    out = isrc_submit.run_round(db, _on(), ws=ws, **_isrc_kw(tags))
    assert len(ws.sent) == 3 and out['sent'] == 1
    with db() as conn:
        statuses = dict(conn.execute("SELECT subject, status FROM giveback_submissions").fetchall())
    assert statuses == {ISRC: 'submitted', 'USRC17607839': 'rejected'}


def test_isrc_auth_failure_writes_nothing(db, tmp_path):
    a = _track(db, tmp_path, 1)
    ws = FakeWS(codes=[401])
    out = isrc_submit.run_round(db, _on(), ws=ws, **_isrc_kw({a: {'isrc': ISRC}}))
    assert out['sent'] == 0 and 'connect it again' in out['error']
    with db() as conn:
        assert store.counts(conn)['isrc']['submitted'] == 0


def test_isrc_needs_a_linked_account(db, tmp_path):
    _track(db, tmp_path, 1)
    out = isrc_submit.run_round(db, _on(**{musicbrainz_oauth.AUTH_SETTING: {}}), ws=FakeWS())
    assert 'connect your MusicBrainz account' in out['error']
    out = isrc_submit.run_round(db, _on(**{musicbrainz_oauth.CLIENT_ID_SETTING: ''}), ws=FakeWS())
    assert out['error'] == musicbrainz_oauth.UNAVAILABLE_MESSAGE


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: List[Dict[str, Any]] = []

    def _answer(self, **call):
        self.calls.append(call)
        return self.answers.pop(0)

    def get(self, url, **kw):
        return self._answer(method='GET', url=url, **kw)

    def post(self, url, **kw):
        return self._answer(method='POST', url=url, **kw)


def test_musicbrainz_ws_requests_have_the_documented_shape():
    http = FakeHttp(Resp(200, {'isrcs': [ISRC, 'junk']}), Resp(200))
    ws = isrc_submit.MusicBrainzWS('tok', http=http, pace=common.Pacer(0))
    assert ws.recording_isrcs(MBID) == [ISRC]
    assert ws.submit([(MBID, ISRC)]) == 200
    get, post = http.calls
    assert get['url'] == f'https://musicbrainz.org/ws/2/recording/{MBID}'
    assert get['params'] == {'inc': 'isrcs', 'fmt': 'json'}
    assert get['headers']['User-Agent'] == common.user_agent()
    assert 'Authorization' not in get['headers']
    assert post['url'] == 'https://musicbrainz.org/ws/2/recording/'
    assert post['params'] == {'client': 'soulsync-3.7.0'}
    assert post['headers']['Content-Type'] == 'application/xml; charset=utf-8'
    assert post['headers']['Authorization'] == 'Bearer tok'
    assert post['headers']['User-Agent'] == common.user_agent()
    assert f'<isrc id="{ISRC}"/>'.encode() in post['data']


def test_musicbrainz_ws_404_means_no_recording():
    ws = isrc_submit.MusicBrainzWS('tok', http=FakeHttp(Resp(404)), pace=common.Pacer(0))
    assert ws.recording_isrcs(MBID) is None


# ── oauth ──

def test_oauth_start_uses_pkce_and_the_installed_app_redirect():
    cfg = _on()
    res = musicbrainz_oauth.start(cfg, now=1000)
    url = urlparse(res['url'])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f'{url.scheme}://{url.netloc}{url.path}' == 'https://musicbrainz.org/oauth2/authorize'
    assert q['response_type'] == 'code' and q['client_id'] == 'app-id'
    assert q['redirect_uri'] == 'urn:ietf:wg:oauth:2.0:oob'
    assert q['scope'] == 'submit_isrc profile'
    assert q['code_challenge_method'] == 'S256'
    verifier = cfg.values[musicbrainz_oauth.PENDING_SETTING]['verifier']
    expect = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    assert q['code_challenge'] == expect
    assert 'app-secret' not in res['url']


def test_oauth_finish_trades_the_code_and_keeps_the_tokens():
    cfg = _on(**{musicbrainz_oauth.AUTH_SETTING: {}})
    musicbrainz_oauth.start(cfg, now=1000)
    http = FakeHttp(Resp(200, {'access_token': 'A', 'refresh_token': 'R', 'expires_in': 3600,
                               'token_type': 'Bearer'}),
                    Resp(200, {'sub': 'someone'}))
    res = musicbrainz_oauth.finish(' the-code ', cfg, http=http, now=1100)
    assert res == {'ok': True, 'username': 'someone'}
    token, info = http.calls
    assert token['url'] == 'https://musicbrainz.org/oauth2/token'
    assert token['data']['grant_type'] == 'authorization_code'
    assert token['data']['code'] == 'the-code'
    assert token['data']['redirect_uri'] == 'urn:ietf:wg:oauth:2.0:oob'
    assert token['data']['code_verifier']
    assert info['headers']['Authorization'] == 'Bearer A'
    auth = cfg.values[musicbrainz_oauth.AUTH_SETTING]
    assert auth['refresh_token'] == 'R' and auth['expires_at'] == 4700
    assert cfg.values[musicbrainz_oauth.PENDING_SETTING] == {}
    st = musicbrainz_oauth.status(cfg)
    assert st['connected'] and st['username'] == 'someone' and 'R' not in json.dumps(st)


def test_oauth_finish_refuses_a_stale_link():
    cfg = _on()
    musicbrainz_oauth.start(cfg, now=1000)
    res = musicbrainz_oauth.finish('code', cfg, http=FakeHttp(), now=1000 + 7200)
    assert 'ran out' in res['error']


def test_oauth_refreshes_an_expiring_token():
    cfg = _on(**{musicbrainz_oauth.AUTH_SETTING: {'access_token': 'old', 'refresh_token': 'R',
                                                    'expires_at': 1030}})
    http = FakeHttp(Resp(200, {'access_token': 'new', 'expires_in': 3600}))
    assert musicbrainz_oauth.access_token(cfg, http=http, now=1000) == 'new'
    assert http.calls[0]['data'] == {'grant_type': 'refresh_token', 'refresh_token': 'R',
                                     'client_id': 'app-id', 'client_secret': 'app-secret'}
    # a refresh answer without a refresh token keeps the old one
    assert cfg.values[musicbrainz_oauth.AUTH_SETTING]['refresh_token'] == 'R'
    assert musicbrainz_oauth.access_token(cfg, http=FakeHttp(), now=1000) == 'new'


def test_oauth_unavailable_without_a_registered_app():
    cfg = Cfg()
    assert musicbrainz_oauth.start(cfg)['error'] == musicbrainz_oauth.UNAVAILABLE_MESSAGE
    assert musicbrainz_oauth.status(cfg)['available'] is False


def test_oauth_disconnect_revokes_and_forgets():
    cfg = _on()
    http = FakeHttp(Resp(200))
    musicbrainz_oauth.disconnect(cfg, http=http)
    assert http.calls[0]['url'] == 'https://musicbrainz.org/oauth2/revoke'
    assert http.calls[0]['data']['token'] == 'ref'
    assert musicbrainz_oauth.status(cfg)['connected'] is False


# ── release seeding ──

def _missing_album(connect, *, mbid='', status='not_found', spotify='4aawyAB9vmqN3uQ7FjRGTy'):
    with connect() as c:
        c.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, release_date, label, upc, "
                  "album_type, spotify_id, musicbrainz_id, tracklist_json) VALUES "
                  "(5, 7, 'New Record', '2024-03-08', 'Indie Label', '0602455123456', 'ep', ?, ?, ?)",
                  (spotify, mbid, json.dumps([
                      {'title': 'Two', 'track_number': 2, 'disc_number': 1, 'duration_ms': 181000},
                      {'title': 'One', 'track_number': 1, 'disc_number': 1, 'duration_ms': 200500,
                       'artists': [{'name': 'Artist'}, {'name': 'Guest'}]},
                      {'title': 'Bonus', 'track_number': 1, 'disc_number': 2},
                  ])))
        c.execute("INSERT INTO lib2_provider_attempts (entity_type, entity_id, service, status) "
                  "VALUES ('album', 5, 'musicbrainz', ?)", (status,))


def test_missing_releases_lists_albums_musicbrainz_lacks(db):
    _missing_album(db)
    with db() as c:
        found = release_seed.missing_releases(c)
    assert found == [{'id': 5, 'title': 'New Record', 'artist': 'Artist', 'year': '2024',
                      'source': 'Spotify', 'source_url': 'https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy'}]


@pytest.mark.parametrize('kw', [{'mbid': MBID}, {'status': 'matched'}, {'spotify': None}])
def test_missing_releases_leaves_out_the_rest(db, kw):
    _missing_album(db, **kw)
    with db() as c:
        assert release_seed.missing_releases(c) == []


def test_seed_fields_fill_the_release_editor(db):
    _missing_album(db)
    with db() as c:
        seed = release_seed.seed_fields(c, 5)
    assert seed['action'] == 'https://musicbrainz.org/release/add'
    f = seed['fields']
    d = {}
    for k, v in f:
        d.setdefault(k, []).append(v)
    assert d['name'] == ['New Record']
    assert d['artist_credit.names.0.name'] == ['Artist']
    assert d['artist_credit.names.0.mbid'] == [OTHER]
    assert d['type'] == ['ep'] and d['status'] == ['official']
    assert d['events.0.date.year'] == ['2024'] and d['events.0.date.month'] == ['3']
    assert d['events.0.date.day'] == ['8']
    assert d['labels.0.name'] == ['Indie Label'] and d['barcode'] == ['0602455123456']
    assert d['mediums.0.format'] == ['Digital Media'] and d['mediums.1.format'] == ['Digital Media']
    # sorted by number, lengths in milliseconds
    assert d['mediums.0.track.0.name'] == ['One'] and d['mediums.0.track.0.length'] == ['200500']
    assert d['mediums.0.track.0.number'] == ['1']
    assert d['mediums.0.track.0.artist_credit.names.0.name'] == ['Artist, Guest']
    assert d['mediums.0.track.1.name'] == ['Two'] and d['mediums.0.track.1.length'] == ['181000']
    assert 'mediums.0.track.1.artist_credit.names.0.name' not in d
    assert d['mediums.1.track.0.name'] == ['Bonus'] and 'mediums.1.track.0.length' not in d
    assert d['urls.0.url'] == ['https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy']
    assert d['urls.0.link_type'] == ['85']
    assert 'SoulSync' in d['edit_note'][0] and 'open.spotify.com' in d['edit_note'][0]
    # every field name is one the seeding page documents
    import re
    allowed = re.compile(r'^(name|type|status|barcode|edit_note|artist_credit\.names\.\d+\.(name|mbid)'
                         r'|events\.\d+\.date\.(year|month|day)|labels\.\d+\.name'
                         r'|mediums\.\d+\.format|mediums\.\d+\.track\.\d+\.(name|number|length'
                         r'|artist_credit\.names\.\d+\.name)|urls\.\d+\.(url|link_type))$')
    assert all(allowed.match(k) for k, _ in f), [k for k, _ in f if not allowed.match(k)]


def test_seed_falls_back_to_owned_tracks_and_other_sources(db):
    with db() as c:
        c.execute("INSERT INTO lib2_albums (id, primary_artist_id, title, album_type, external_ids) "
                  "VALUES (6, 7, 'Deep Cut', 'compilation', ?)", (json.dumps({'deezer': '302127'}),))
        c.execute("INSERT INTO lib2_tracks (album_id, title, track_number, duration) VALUES (6, 'A', 1, 1000)")
    with db() as c:
        seed = release_seed.seed_fields(c, 6)
        assert release_seed.seed_fields(c, 1) is None  # no source
    d = dict((k, v) for k, v in seed['fields'] if k != 'type')
    assert [v for k, v in seed['fields'] if k == 'type'] == ['album', 'compilation']
    assert d['urls.0.url'] == 'https://www.deezer.com/album/302127'
    assert d['mediums.0.track.0.name'] == 'A' and d['mediums.0.track.0.length'] == '1000'


def test_a_source_id_that_isnt_an_id_is_not_linked():
    assert release_seed.source_of({'spotify_id': 'x/../evil'}) is None
    assert release_seed.source_of({'external_ids': json.dumps({'bandcamp': 'https://a.bandcamp.com/album/b'})})[
        'url'] == 'https://a.bandcamp.com/album/b'


# ── routes ──

@pytest.fixture
def client(db, monkeypatch):
    from flask import Flask

    from api import giveback_routes
    from core.giveback import worker
    cfg = _on(**{common.ACOUSTID_SETTING: False})

    class DB:
        def _get_connection(self):
            return db()

    giveback_routes.configure(config_manager_=cfg, get_database_=lambda: DB())
    from core import profile_context
    monkeypatch.setattr(profile_context, 'is_admin_request', lambda: True)
    nudged = []
    monkeypatch.setattr(worker, 'nudge', lambda: nudged.append(1))
    app = Flask(__name__)
    app.register_blueprint(giveback_routes.create_blueprint())
    c = app.test_client()
    c.cfg, c.nudged = cfg, nudged
    return c


def test_routes_declare_admin_access():
    from flask import Flask

    from api import giveback_routes
    from core.permissions import declared_access
    app = Flask(__name__)
    app.register_blueprint(giveback_routes.create_blueprint())
    rules = [r for r in app.url_map.iter_rules() if r.endpoint != 'static']
    assert len(rules) == 8
    for rule in rules:
        assert declared_access(app.view_functions[rule.endpoint]) == ('admin', None), rule.rule


def test_routes_refuse_a_member(client, monkeypatch):
    from core import profile_context
    monkeypatch.setattr(profile_context, 'is_admin_request', lambda: False)
    assert client.get('/api/giveback').status_code == 403
    assert client.put('/api/giveback/settings', json={'acoustid': True}).status_code == 403
    assert client.cfg.values[common.ACOUSTID_SETTING] is False


def test_overview_never_carries_a_token(client):
    r = client.get('/api/giveback')
    data = r.get_json()
    assert r.status_code == 200 and data['switches'] == {'acoustid': False, 'isrc': True, 'releases': True}
    assert data['musicbrainz']['connected'] is True
    assert data['counts']['acoustid']['submitted'] == 0
    for secret in ('tok', 'ref', 'app-secret', 'user-key'):
        assert f'"{secret}"' not in r.get_data(as_text=True)


def test_switches_save_and_nudge(client):
    r = client.put('/api/giveback/settings', json={'acoustid': True, 'releases': False})
    assert r.get_json()['switches']['acoustid'] is True
    assert client.cfg.values[common.RELEASES_SETTING] is False and client.nudged == [1]
    assert client.put('/api/giveback/settings', json={'isrc': 'yes'}).status_code == 400


def test_release_routes(client, db):
    _missing_album(db)
    assert [a['id'] for a in client.get('/api/giveback/releases').get_json()['releases']] == [5]
    seed = client.get('/api/giveback/releases/5/seed').get_json()
    assert seed['action'] == release_seed.RELEASE_ADD_URL and ['name', 'New Record'] in seed['fields']
    assert client.get('/api/giveback/releases/1/seed').status_code == 404
    client.put('/api/giveback/settings', json={'releases': False})
    assert client.get('/api/giveback/releases').get_json()['releases'] == []


def test_musicbrainz_routes(client):
    r = client.post('/api/giveback/musicbrainz/connect')
    assert r.get_json()['url'].startswith('https://musicbrainz.org/oauth2/authorize?')
    assert client.post('/api/giveback/musicbrainz/code', json={'code': ''}).status_code == 400
