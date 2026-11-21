"""the subsonic-response envelope (json, jsonp, xml) and who a request is
(core/subsonic/response.py, auth.py)."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET

import pytest

from core.subsonic import auth as A
from core.subsonic import response as R

NS = "{http://subsonic.org/restapi}"


def test_json_envelope_drops_none_and_keeps_types():
    body = R.ok({"album": {"id": "al-1", "name": "X", "year": None, "song": [{"id": "tr-1", "isDir": False}]}})
    out = json.loads(R.to_json(body))["subsonic-response"]
    assert out["status"] == "ok" and out["version"] == R.API_VERSION and out["openSubsonic"] is True
    assert out["type"] == "soulsync" and "serverVersion" in out
    assert out["album"] == {"id": "al-1", "name": "X", "song": [{"id": "tr-1", "isDir": False}]}


def test_xml_attributes_children_lists_and_text():
    body = R.ok({"genres": {"genre": [{"value": "Rock & Roll", "songCount": 2}, {"value": "Pop", "songCount": 1}]},
                 "artistInfo": {"biography": R.Text("a <b> bio"), "similarArtist": [{"id": "ar-2", "name": "Y"}]}})
    root = ET.fromstring(R.to_xml(body))
    assert root.tag == NS + "subsonic-response" and root.get("status") == "ok" and root.get("openSubsonic") == "true"
    genres = root.find(NS + "genres").findall(NS + "genre")
    assert [(g.text, g.get("songCount")) for g in genres] == [("Rock & Roll", "2"), ("Pop", "1")]
    info = root.find(NS + "artistInfo")
    assert info.find(NS + "biography").text == "a <b> bio"
    assert info.find(NS + "similarArtist").get("name") == "Y"


def test_errors_and_formats():
    body = R.failed(R.NOT_FOUND, "Song not found")
    text, mime = R.render(body, "json")
    assert mime == "application/json"
    assert json.loads(text)["subsonic-response"]["error"] == {"code": 70, "message": "Song not found"}
    text, mime = R.render(body, "jsonp", "cb_1")
    assert mime == "application/javascript" and text.startswith("cb_1(") and text.endswith(");")
    # a callback that isn't a name is never echoed back
    text, _ = R.render(body, "jsonp", "alert(1)//")
    assert not text.startswith("alert")
    text, mime = R.render(body, "")
    assert mime == "text/xml"
    err = ET.fromstring(text).find(NS + "error")
    assert err.get("code") == "70"


# ── auth ─────────────────────────────────────────────────────────────────

DEVICE = {"id": 7, "profile_id": 2, "password_enc": "ENC:secretpass", "revoked_at": None}


def _resolve(params, *, devices=(DEVICE,), token_row=None):
    return A.resolve(params, find_token=lambda tok: token_row if tok == "sdt_good" else None,
                     profiles_named=lambda u: [2] if u.lower() == "mags" else [],
                     devices_for=lambda pids: [d for d in devices if d["profile_id"] in pids],
                     decrypt=lambda v: v[4:] if v.startswith("ENC:") else None)


def test_api_key_alone():
    row, refusal = _resolve({"apiKey": "sdt_good"}, token_row={"id": 9})
    assert row == {"id": 9} and refusal is None
    assert _resolve({"apiKey": "sdt_bad"})[1][0] == R.INVALID_API_KEY
    # an api key with a username or password is two ways of signing in
    assert _resolve({"apiKey": "sdt_good", "u": "mags"}, token_row={"id": 9})[1][0] == R.CONFLICTING_AUTH
    assert _resolve({"apiKey": "sdt_good", "p": "x"}, token_row={"id": 9})[1][0] == R.CONFLICTING_AUTH


def test_salted_token():
    t = hashlib.md5(b"secretpassabc123").hexdigest()
    row, refusal = _resolve({"u": "Mags", "t": t, "s": "abc123"})
    assert refusal is None and row["id"] == 7 and "password_enc" not in row
    assert _resolve({"u": "Mags", "t": t.upper(), "s": "abc123"})[0]["id"] == 7
    assert _resolve({"u": "Mags", "t": t, "s": "other"})[1][0] == R.WRONG_CREDENTIALS
    assert _resolve({"u": "Someone", "t": t, "s": "abc123"})[1][0] == R.WRONG_CREDENTIALS


def test_plain_and_hex_passwords():
    assert _resolve({"u": "mags", "p": "secretpass"})[0]["id"] == 7
    assert _resolve({"u": "mags", "p": "enc:" + b"secretpass".hex()})[0]["id"] == 7
    assert _resolve({"u": "mags", "p": "enc:zz"})[1][0] == R.WRONG_CREDENTIALS
    assert _resolve({"u": "mags", "p": "wrong"})[1][0] == R.WRONG_CREDENTIALS


def test_missing_pieces_are_code_10():
    assert _resolve({})[1][0] == R.MISSING_PARAM
    assert _resolve({"u": "mags"})[1][0] == R.MISSING_PARAM
    assert _resolve({"u": "mags", "t": "x"})[1] == (R.MISSING_PARAM, "Required parameter is missing: s")


def test_a_password_that_wont_decrypt_matches_nothing():
    broken = dict(DEVICE, password_enc="garbage")
    assert _resolve({"u": "mags", "p": "garbage"}, devices=(broken,))[1][0] == R.WRONG_CREDENTIALS
    assert _resolve({"u": "mags", "p": ""}, devices=(broken,))[1][0] == R.MISSING_PARAM


@pytest.mark.parametrize("code,want", [("device_token_invalid", R.INVALID_API_KEY),
                                       ("device_pick_expired", R.INVALID_API_KEY),
                                       ("device_pick_first", R.NOT_AUTHORIZED),
                                       ("device_profile_not_allowed", R.NOT_AUTHORIZED),
                                       ("device_not_allowed", R.NOT_AUTHORIZED)])
def test_gate_refusals_in_subsonic_codes(code, want):
    assert A.refusal_for(code)[0] == want
