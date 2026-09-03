"""Everything about one stuck row, in one call.

Twice in a day, answering "why isn't this downloading?" meant searching
Boulder's live Prowlarr by hand, because the stored evidence stopped at a
summary line: "15 results, none were this release". It could not say which
releases those were, why each lost, where the file would land, which external
ids the search was keyed on, or whether something was already downloading.

Both times my reasoning from that summary was wrong. This is the evidence that
would have answered it without the guesswork.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask

from core.automation.handlers.video_process_wishlist import (
    AUDIT_SAMPLE_LIMIT,
    audit_samples,
    source_outcome,
)
from database.video_database import VideoDatabase


# ── the per-release receipts ─────────────────────────────────────────────────
def _cand(title, accepted=False, rejected="Wrong episode", **kw):
    return dict({"title": title, "accepted": accepted, "rejected": rejected,
                 "quality_label": "1080p · WEB", "source": "torrent"}, **kw)


def test_a_source_now_records_the_individual_releases():
    o = source_outcome([_cand("Big Brother US S28E25 1080p"),
                        _cand("Big Brother US S28E24 1080p")])
    assert o["results"] == 2 and o["accepted"] == 0
    assert [s["title"] for s in o["samples"]] == [
        "Big Brother US S28E25 1080p", "Big Brother US S28E24 1080p"]
    assert o["samples"][0]["rejected"] == "Wrong episode"


def test_the_receipts_are_bounded():
    """A busy search returns 180 hits. Storing them all, per row, per hour, would
    be a database of torrent names."""
    o = source_outcome([_cand("R%d" % i) for i in range(200)])
    assert o["results"] == 200, "the COUNT is still the truth"
    assert len(o["samples"]) == AUDIT_SAMPLE_LIMIT


def test_a_source_that_could_not_run_has_no_receipts_to_give():
    o = source_outcome(None, err="slskd is unreachable")
    assert o["ran"] is False and o["samples"] == []


def test_long_release_names_are_clipped_not_dropped():
    o = source_outcome([_cand("X" * 500)])
    assert len(o["samples"][0]["title"]) == 200


def test_an_accepted_release_is_recorded_too():
    """The receipt for what WORKED is as useful as the receipts for what didn't."""
    s = audit_samples([_cand("Good release", accepted=True, rejected=None)])
    assert s[0]["accepted"] is True and s[0]["rejected"] is None


def test_junk_in_the_candidate_list_is_skipped_not_fatal():
    assert audit_samples([None, "nonsense", _cand("Real")]) == audit_samples([_cand("Real")])


# ── the drawer's one call ────────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), videoapi._video_db
    videoapi._video_db = None


def _seed_row(db, snapshot=None):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Big Brother (US)",
                                 "tmdb_id": 10160})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET tvdb_id=1234, imdb_id='tt0123456' WHERE tmdb_id=10160")
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, "
                 "episode_number, status, search_attempts, last_search_at, last_refusal, "
                 "search_snapshot) VALUES ('episode',10160,'Big Brother (US)',28,27,'wanted',9,"
                 "datetime('now'),'15 results for this title, but not this episode yet',?)",
                 (json.dumps(snapshot) if snapshot else None,))
    conn.commit(); conn.close()
    db.set_setting("tv_path", "/media/TV")


def test_the_drawer_gets_the_row_ids_target_and_receipts_in_one_call(client):
    c, db = client
    _seed_row(db, {"chain": ["torrent"], "sources": {"torrent": source_outcome(
        [_cand("Big Brother US S28E25 1080p")])}})
    body = c.get("/api/video/wishlist/diagnostics?kind=episode&tmdb_id=10160"
                 "&season_number=28&episode_number=27").get_json()
    assert body["success"] is True
    # the row itself
    assert body["row"]["search_attempts"] == 9
    assert "not this episode yet" in body["row"]["last_refusal"]
    # the ids the search was keyed on - a row stuck for want of a tvdb id looks
    # exactly like one nobody seeds, until you can see this
    assert body["ids"]["tvdb_id"] == 1234 and body["ids"]["imdb_id"] == "tt0123456"
    # where the file would land
    assert body["target_dir"] == "/media/TV"
    # and WHY an individual release lost
    sample = body["row"]["search_snapshot"]["sources"]["torrent"]["samples"][0]
    assert sample["title"] == "Big Brother US S28E25 1080p"
    assert sample["rejected"] == "Wrong episode"


def test_a_row_mid_download_says_so_rather_than_looking_stuck(client):
    c, db = client
    _seed_row(db)
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, media_id, status, progress, release_title) "
                 "VALUES ('episode','10160','downloading',42.0,'Big Brother US S28E27')")
    # ...and a finished one must not be mistaken for live activity
    conn.execute("INSERT INTO video_downloads (kind, media_id, status, release_title) "
                 "VALUES ('episode','10160','completed','older grab')")
    conn.commit(); conn.close()
    body = c.get("/api/video/wishlist/diagnostics?kind=episode&tmdb_id=10160"
                 "&season_number=28&episode_number=27").get_json()
    assert [d["status"] for d in body["downloads"]] == ["downloading"]
    assert body["downloads"][0]["progress"] == 42.0


def test_an_unreadable_snapshot_does_not_break_the_drawer(client):
    c, db = client
    _seed_row(db)
    conn = db._get_connection()
    conn.execute("UPDATE video_wishlist SET search_snapshot='not json'")
    conn.commit(); conn.close()
    body = c.get("/api/video/wishlist/diagnostics?kind=episode&tmdb_id=10160"
                 "&season_number=28&episode_number=27").get_json()
    assert body["success"] is True and body["row"]["search_snapshot"] is None


def test_bad_requests_are_refused(client):
    c, db = client
    _seed_row(db)
    assert c.get("/api/video/wishlist/diagnostics?kind=bogus&tmdb_id=1").status_code == 400
    assert c.get("/api/video/wishlist/diagnostics?kind=episode").status_code == 400
    assert c.get("/api/video/wishlist/diagnostics?kind=episode&tmdb_id=999999").status_code == 404
