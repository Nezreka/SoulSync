"""Detail BIC P3 — enriched-but-never-surfaced facts + the watched toggle UI.

Awards / mediastinger / digital_release_date were already enriched (for
overlays) but never reached the detail payloads; episodes now carry added_at
for the NEW badge; set_watch_state on a show mirrors markPlayed down to the
per-episode rows so the page's checkmarks agree instantly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _ep(e, *, plays=0, offset=0):
    return {"server_id": "e%d" % e, "season_number": 1, "episode_number": e,
            "title": "E%d" % e, "air_date": "2026-07-%02d" % e,
            "play_count": plays, "view_offset_ms": offset,
            "added_at": "2026-07-1%d 10:00:00" % (e % 10),
            "file": {"relative_path": "/tv/s1e%d.mkv" % e, "size_bytes": 100}}


def _tree(eps):
    return {"server_id": "sh", "title": "Show",
            "seasons": [{"season_number": 1, "episodes": eps}]}


class TestPayloadSurfacing:
    def test_movie_exposes_awards_stinger_digital_date(self, db):
        mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Film", "year": 2020,
                                       "tmdb_id": 7})
        with db.connect() as c:
            c.execute("UPDATE movies SET awards=?, mediastinger=1, digital_release_date=? "
                      "WHERE id=?", ("Won 2 Oscars. 154 wins total.", "2020-06-01", mid))
            c.commit()
        d = db.movie_detail(mid)
        assert d["awards"] == "Won 2 Oscars. 154 wins total."
        assert d["mediastinger"] is True
        assert d["digital_release_date"] == "2020-06-01"

    def test_show_exposes_awards_and_stinger(self, db):
        sid = db.upsert_show_tree("plex", _tree([_ep(1)]))
        with db.connect() as c:
            c.execute("UPDATE shows SET awards=?, mediastinger=1 WHERE id=?",
                      ("Won 3 Primetime Emmys.", sid))
            c.commit()
        d = db.show_detail(sid)
        assert d["awards"] == "Won 3 Primetime Emmys."
        assert d["mediastinger"] is True

    def test_episodes_carry_added_at_for_the_new_badge(self, db):
        sid = db.upsert_show_tree("plex", _tree([_ep(1)]))
        eps = db.show_detail(sid)["seasons"][0]["episodes"]
        assert eps[0]["added_at"] == "2026-07-11 10:00:00"


class TestWatchedToggleMirror:
    def test_show_toggle_flips_every_episode_row(self, db):
        sid = db.upsert_show_tree("plex", _tree([
            _ep(1, plays=1), _ep(2, offset=500_000), _ep(3)]))
        assert db.set_watch_state("show", sid, True) is True
        d = db.show_detail(sid)
        assert d["watched"] is True
        assert all(e["watched"] and e["view_offset_ms"] == 0
                   for e in d["seasons"][0]["episodes"])
        assert d["next_up"] is None            # nothing left to continue
        # ...and back: unwatch clears the rows too
        assert db.set_watch_state("show", sid, False) is True
        d = db.show_detail(sid)
        assert d["watched"] is False
        assert not any(e["watched"] for e in d["seasons"][0]["episodes"])

    def test_movie_toggle_clears_the_resume_offset(self, db):
        mid = db.upsert_movie("plex", {"server_id": "m1", "title": "F", "year": 2020,
                                       "tmdb_id": 7, "view_offset_ms": 900_000})
        assert db.set_watch_state("movie", mid, True) is True
        d = db.movie_detail(mid)
        assert d["watched"] is True and d["view_offset_ms"] == 0


class TestUiPins:
    def test_detail_js_wires_the_toggle_and_facts(self):
        js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(
            encoding="utf-8", errors="replace")
        assert "data-vd-act=\"watched-toggle\"" in js
        assert "which === 'watched-toggle') toggleWatchedState(act)" in js
        assert "'/watched'" in js                       # hits the existing API
        assert 'function renderAwards(d)' in js
        assert js.count('renderAwards(d)') >= 3         # def + main + youtube-path clear
        assert 'vd-ep-new-chip' in js
        assert 'After-credits scene' in js
        assert "rows.push(['Digital release', d.digital_release_date])" in js
        assert "f.quality || mediaRes(f.resolution)" in js

    def test_hero_html_has_awards_anchor_in_both_blocks(self):
        html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
        assert html.count('data-vd-awards') == 2        # show + movie heroes
