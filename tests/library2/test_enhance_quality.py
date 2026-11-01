"""Enhance Quality on the one upgrade path (#1062 review, point 2).

``GET /api/library/v2/<entity>/<id>/enhance`` lists the files its quality
profile would still upgrade, monitored or not; POSTed picks are monitored and
searched by the scoped Automatic Search, nothing else in the scope.
"""

from __future__ import annotations

import json

import pytest

from tests.library2.test_scoped_search_endpoint import _await_job, _build_api

pytest.importorskip("flask")


def _enhance_api(tmp_path, *, policy="until_cutoff"):
    dispatched = []
    client, db, ids = _build_api(
        tmp_path, dispatcher=lambda search_ids, profile: (
            dispatched.append(sorted(search_ids)) or {"success": True, "batch_id": "b1"}))
    with db._get_connection() as conn:
        profile = conn.execute(
            "INSERT INTO quality_profiles(name,ranked_targets,upgrade_policy,upgrade_cutoff_index)"
            " VALUES('Lossless',?,?,0)",
            (json.dumps([{"label": "FLAC 16-bit", "format": "flac", "bit_depth": 16,
                          "min_sample_rate": 44100}]), policy),
        ).lastrowid
        conn.execute("UPDATE lib2_artists SET quality_profile_id=?, quality_profile_explicit=1",
                     (profile,))
        conn.execute("UPDATE lib2_track_files SET format='mp3', bitrate=320, sample_rate=44100")
        conn.commit()
    return client, db, ids, dispatched


def test_lists_an_unmonitored_file_below_its_profile(tmp_path):
    client, _db, ids, _ = _enhance_api(tmp_path)

    body = client.get(f"/api/library/v2/artists/{ids['artist']}/enhance").get_json()

    # the missing EP track has no file to enhance
    assert [(t["track_id"], t["monitored"], t["current"], t["target"]) for t in body["tracks"]] == [
        (ids["album_track"], False, "MP3 320kbps", "FLAC 16-bit")]


def test_a_profile_without_upgrades_lists_nothing(tmp_path):
    client, _db, ids, _ = _enhance_api(tmp_path, policy="none")

    assert client.get(f"/api/library/v2/artists/{ids['artist']}/enhance").get_json()["tracks"] == []


def test_a_pick_is_monitored_and_only_it_is_searched(tmp_path):
    client, db, ids, dispatched = _enhance_api(tmp_path)

    response = client.post(f"/api/library/v2/artists/{ids['artist']}/enhance",
                           json={"track_ids": [ids["album_track"], ids["ep_track"]]})
    status = _await_job(client, response.get_json()["job_id"])

    assert status["error"] is None
    assert status["result"]["searching"] == 1
    assert dispatched == [["sp-t1"]]
    assert {a["id"] for a in db.wishlist_adds} == {"sp-t1"}
    with db._get_connection() as conn:
        wanted = dict(conn.execute("SELECT track_id, wanted FROM lib2_wanted_tracks").fetchall())
    # the EP track was not an upgrade pick: its monitoring stays untouched
    assert wanted[ids["album_track"]] == 1
    assert not wanted.get(ids["ep_track"])


@pytest.mark.parametrize("body", [{"track_ids": []}, {"track_ids": "1"}, {}])
def test_a_pick_without_an_upgrade_is_refused(tmp_path, body):
    client, _db, ids, dispatched = _enhance_api(tmp_path)

    assert client.post(f"/api/library/v2/artists/{ids['artist']}/enhance",
                       json=body).status_code == 400
    assert dispatched == []


def test_only_artists_and_albums_have_an_enhance_scope(tmp_path):
    client, _db, ids, _ = _enhance_api(tmp_path)

    assert client.get(f"/api/library/v2/tracks/{ids['album_track']}/enhance").status_code == 400
