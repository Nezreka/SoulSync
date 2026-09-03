"""Per-indexer seed goals (Sonarr/Radarr parity).

One global seed goal can't serve a private tracker that wants ratio 2 and a
public one that wants nothing. arr hangs Seed Ratio / Seed Time off the
INDEXER, and Sonarr gives season packs their own longer window. These walk the
grid both directions so a dead switch (a rule the resolver can never return)
shows up, same as test_video_release_gates does for quality tiers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.downloads import seed_rules
from core.video import seeding

_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def _cfg(**kw):
    base = {"seed_ratio_goal": 0.0, "seed_time_goal_hours": 0, "seed_overrides": {}}
    base.update(kw)
    return base


# ── normalize ────────────────────────────────────────────────────────────────
def test_blank_and_zero_are_different_things():
    """Blank = inherit the global goal. 0 = this tracker is exempt from it.
    Collapsing them would make 'exempt one tracker' impossible to express."""
    assert seed_rules.normalize_rule({"ratio": ""}) == {}
    assert seed_rules.normalize_rule({"ratio": 0}) == {"ratio": 0.0}
    assert seed_rules.normalize_rule({"hours": None}) == {}
    assert seed_rules.normalize_rule({"hours": 0}) == {"hours": 0}


def test_normalize_clamps_the_top_and_drops_junk():
    r = seed_rules.normalize_rule({"ratio": "999", "pack_hours": "abc"})
    assert r == {"ratio": 100.0}


def test_a_negative_is_junk_not_an_exemption():
    """Clamping -5 up to 0 would land it on the EXEMPT value, so a typo would
    silently switch that tracker's goal off. Negatives must read as unset."""
    assert seed_rules.normalize_rule({"hours": "-5"}) == {}
    assert seed_rules.normalize_rule({"ratio": -1}) == {}
    cfg = _cfg(seed_time_goal_hours=48, seed_overrides={"7": {"hours": -5}})
    assert seed_rules.effective_goal(cfg, 7) == (0.0, 48)      # inherited, not exempt


def test_normalize_overrides_accepts_json_or_dict():
    raw = {"7": {"ratio": 2}, "bad": {"ratio": 1}, "9": {}}
    out = seed_rules.normalize_overrides(raw)
    assert out == {"7": {"ratio": 2.0}}          # junk key dropped, empty rule dropped
    assert seed_rules.normalize_overrides(json.dumps(raw)) == out
    assert seed_rules.normalize_overrides("not json") == {}
    assert seed_rules.normalize_overrides(None) == {}


# ── resolution ───────────────────────────────────────────────────────────────
def test_no_override_falls_back_to_the_global_goal():
    cfg = _cfg(seed_ratio_goal=1.5, seed_time_goal_hours=48)
    assert seed_rules.effective_goal(cfg, 7) == (1.5, 48)
    assert seed_rules.effective_goal(cfg, None) == (1.5, 48)


def test_an_override_wins_per_criterion():
    """A rule that sets only a ratio still inherits the global time goal."""
    cfg = _cfg(seed_ratio_goal=1.0, seed_time_goal_hours=48,
               seed_overrides={"7": {"ratio": 3.0}})
    assert seed_rules.effective_goal(cfg, 7) == (3.0, 48)
    assert seed_rules.effective_goal(cfg, 8) == (1.0, 48)      # other indexers untouched


def test_zero_exempts_one_tracker_from_a_global_goal():
    cfg = _cfg(seed_ratio_goal=2.0, seed_time_goal_hours=72,
               seed_overrides={"7": {"ratio": 0, "hours": 0}})
    assert seed_rules.effective_goal(cfg, 7) == (0.0, 0)
    assert seed_rules.effective_goal(cfg, 8) == (2.0, 72)


def test_season_packs_get_their_own_window():
    cfg = _cfg(seed_time_goal_hours=24,
               seed_overrides={"7": {"hours": 48, "pack_hours": 336}})
    assert seed_rules.effective_goal(cfg, 7, is_pack=False) == (0.0, 48)
    assert seed_rules.effective_goal(cfg, 7, is_pack=True) == (0.0, 336)


def test_a_pack_without_a_pack_rule_uses_the_normal_hours():
    cfg = _cfg(seed_time_goal_hours=24, seed_overrides={"7": {"hours": 48}})
    assert seed_rules.effective_goal(cfg, 7, is_pack=True) == (0.0, 48)
    assert seed_rules.effective_goal(cfg, 9, is_pack=True) == (0.0, 24)


@pytest.mark.parametrize("indexer_id", ["7", 7, 7.0])
def test_indexer_id_type_does_not_matter(indexer_id):
    """The id arrives as a string from json and an int from the DB."""
    cfg = _cfg(seed_overrides={"7": {"ratio": 3.0}})
    assert seed_rules.effective_goal(cfg, indexer_id)[0] == 3.0


def test_every_rule_field_is_reachable():
    """Walk the other direction: each field must be able to change the answer,
    or it's a dead switch in the UI."""
    for field, is_pack, slot in (("ratio", False, 0), ("hours", False, 1),
                                 ("pack_hours", True, 1)):
        cfg = _cfg(seed_overrides={"7": {field: 5}})
        assert seed_rules.effective_goal(cfg, 7, is_pack=is_pack)[slot] == 5, field


# ── the sweep gate ───────────────────────────────────────────────────────────
def test_per_indexer_rules_alone_are_enough_to_run_the_sweep():
    """Set a rule on one tracker, leave the globals blank: the sweep must
    still run. Gating on the globals only would silently ignore the rules."""
    assert not seed_rules.any_goal_set(_cfg())
    assert seed_rules.any_goal_set(_cfg(seed_overrides={"7": {"hours": 48}}))
    assert seed_rules.any_goal_set(_cfg(seed_ratio_goal=1.0))
    # an all-zero (exempt) rule is not a goal
    assert not seed_rules.any_goal_set(_cfg(seed_overrides={"7": {"ratio": 0, "hours": 0}}))


def test_the_gate_coerces_instead_of_trusting_truthiness():
    """video_settings stores these as TEXT. bool("0.0") is True, so a plain
    truthiness check would report a goal where there is none."""
    assert not seed_rules.any_goal_set({"seed_ratio_goal": "0.0", "seed_time_goal_hours": "0"})
    assert seed_rules.any_goal_set({"seed_ratio_goal": "1.5", "seed_time_goal_hours": "0"})


# ── seeding.py uses them ─────────────────────────────────────────────────────
class _Status:
    def __init__(self, ratio=None, seeding_time=None):
        self.ratio = ratio
        self.seeding_time = seeding_time


def _dl(**kw):
    row = {"id": 1, "title": "Heat", "client_ref": "abcd", "indexer_id": 7,
           "completed_at": "2020-01-01T00:00:00+00:00"}
    row.update(kw)
    return row


def test_goals_met_judges_against_the_rows_indexer():
    cfg = _cfg(seed_ratio_goal=1.0, seed_overrides={"7": {"ratio": 5.0}})
    # ratio 2 clears the global goal but NOT this tracker's stricter one
    assert seeding.goals_met(_Status(ratio=2.0), _dl(indexer_id=7), cfg) is None
    assert seeding.goals_met(_Status(ratio=2.0), _dl(indexer_id=8), cfg)


def test_pack_detection_uses_the_one_canonical_definition():
    """seeding must NOT carry its own idea of what a pack is. The first draft
    did and drifted at once: it missed scope 'pack' and wrongly counted scope
    'show', which season_pack settles off ``kind``."""
    from core.video import season_pack
    cases = [
        {"search_ctx": json.dumps({"scope": "season"})},
        {"search_ctx": json.dumps({"scope": "series"})},
        {"search_ctx": json.dumps({"scope": "pack"})},
        {"search_ctx": json.dumps({"scope": "episode"})},
        {"search_ctx": json.dumps({"scope": "movie"})},
        {"kind": "show", "search_ctx": json.dumps({"season": 2})},
        {"kind": "show", "search_ctx": json.dumps({"season": 2, "episode": 4})},
        {"search_ctx": "not json"},
        {},
    ]
    for dl in cases:
        assert seeding.is_pack(dl) == season_pack.is_pack_download(dl), dl
    # and it really does say yes to a pack and no to an episode
    assert seeding.is_pack({"search_ctx": json.dumps({"scope": "season"})})
    assert not seeding.is_pack({"search_ctx": json.dumps({"scope": "episode"})})


def test_a_pack_is_held_longer_than_an_episode():
    cfg = _cfg(seed_overrides={"7": {"hours": 1, "pack_hours": 1000}})
    ep = _dl(search_ctx=json.dumps({"scope": "episode"}))
    pack = _dl(search_ctx=json.dumps({"scope": "season"}))
    st = _Status(seeding_time=10 * 3600)
    assert seeding.goals_met(st, ep, cfg)          # 10h > 1h goal
    assert seeding.goals_met(st, pack, cfg) is None   # 10h < 1000h pack goal


# ── plumbing ─────────────────────────────────────────────────────────────────
def test_config_round_trips_the_overrides(tmp_path):
    from core.video import download_config
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    out = download_config.save(db, {"seed_overrides": {"7": {"ratio": "2.5", "hours": "72"},
                                                       "junk": {"ratio": 1}}})
    assert out["seed_overrides"] == {"7": {"ratio": 2.5, "hours": 72}}
    assert download_config.load(db)["seed_overrides"] == {"7": {"ratio": 2.5, "hours": 72}}


def test_the_grab_row_records_which_indexer_served_it():
    """Without the id the sweep has only the indexer NAME, which isn't a stable
    key and can collide."""
    from core.automation.handlers.video_process_wishlist import build_download_record
    rec = build_download_record(
        {"tmdb_id": 1, "title": "Heat", "year": 1995},
        {"source": "torrent", "title": "Heat.1995.1080p", "indexer_id": 7,
         "username": "SomeTracker", "_client_ref": "abcd", "size_bytes": 1},
        [], media_type="movie", target_dir="/movies", query="heat")
    assert rec["indexer_id"] == 7


def test_the_download_table_can_store_it(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    dl_id = db.add_video_download({"kind": "movie", "title": "Heat", "source": "torrent",
                                   "client_ref": "abcd", "indexer_id": 7, "status": "completed"})
    row = next(r for r in db.list_video_downloads() if r["id"] == dl_id)
    assert row["indexer_id"] == 7


def test_settings_ui_exposes_the_per_indexer_rules():
    for frag in ('id="video-seed-idx-rows"', 'id="video-seed-idx-refresh"'):
        assert frag in _INDEX, frag
    for frag in ("seed_overrides", "data-vseed-idx", "data-vseed-field", "pack_hours"):
        assert frag in _SETTINGS_JS, frag


# ── the sweep loop itself ────────────────────────────────────────────────────
class _FakeDB:
    """Just enough of VideoDatabase for _sweep_inner."""

    def __init__(self, rows):
        self._rows = rows
        self.released = []
        self.settings = {}

    def torrents_awaiting_seed_release(self, limit=100):
        return self._rows[:limit]

    def update_video_download(self, dl_id, **kw):
        if kw.get("seed_released"):
            self.released.append(dl_id)

    def get_setting(self, key):
        return self.settings.get(key)


def _sweep_with(monkeypatch, rows, cfg):
    """Run _sweep_inner against fake rows + config, with no client at all."""
    # _sweep_inner imports these lazily inside the function, so patch them where
    # they live, not on the seeding module.
    from core.video import download_config, seeding as sd
    db = _FakeDB(rows)
    monkeypatch.setattr("api.video.get_video_db", lambda: db, raising=False)
    monkeypatch.setattr(download_config, "load", lambda _db: cfg)
    import core.video.client_download as cd
    monkeypatch.setattr(cd, "_get_status", lambda src, ref: None)
    return sd._sweep_inner(), db


def test_an_exempt_indexer_is_left_alone(monkeypatch):
    """A tracker with an all-zero rule keeps seeding forever, and is never
    released — 'no goal' means SoulSync doesn't manage it."""
    rows = [{"id": 1, "client_ref": "a", "indexer_id": 7, "title": "X"}]
    cfg = _cfg(seed_time_goal_hours=48, seed_overrides={"7": {"ratio": 0, "hours": 0}})
    res, db = _sweep_with(monkeypatch, rows, cfg)
    assert res["exempt"] == 1 and res["released"] == 0
    assert db.released == []


def test_exempt_rows_cannot_starve_the_sweep(monkeypatch):
    """The regression this guards: an exempt row is never released, so it sits
    inside `ORDER BY id LIMIT n` forever. Pile up more than the poll budget and
    no newer torrent would ever be swept again."""
    from core.video import seeding as sd
    exempt = [{"id": i, "client_ref": "e%d" % i, "indexer_id": 7, "title": "old"}
              for i in range(1, sd.MAX_POLLS_PER_SWEEP + 200)]
    real = [{"id": 99999, "client_ref": "new", "indexer_id": 8, "title": "new"}]
    cfg = _cfg(seed_time_goal_hours=48, seed_overrides={"7": {"ratio": 0, "hours": 0}})
    res, db = _sweep_with(monkeypatch, exempt + real, cfg)
    assert res["exempt"] == len(exempt)
    # the newer row still got looked at (client forgot it → released)
    assert 99999 in db.released, "a real row was starved out by exempt rows"


def test_a_bounded_pass_says_what_it_deferred(monkeypatch):
    """No silent caps: if the sweep stops early it must report the remainder,
    or a partial pass reads as 'checked everything'."""
    from core.video import seeding as sd
    rows = [{"id": i, "client_ref": "r%d" % i, "indexer_id": 8, "title": "t"}
            for i in range(1, sd.MAX_POLLS_PER_SWEEP + 51)]
    res, _db = _sweep_with(monkeypatch, rows, _cfg(seed_time_goal_hours=48))
    assert res["deferred"] == 50
    assert res["released"] == sd.MAX_POLLS_PER_SWEEP


def test_the_sweep_runs_on_per_indexer_rules_alone(monkeypatch):
    """Globals blank, one tracker configured: the sweep must not skip."""
    rows = [{"id": 1, "client_ref": "a", "indexer_id": 7, "title": "X"}]
    cfg = _cfg(seed_overrides={"7": {"hours": 1}})
    res, _db = _sweep_with(monkeypatch, rows, cfg)
    assert res["status"] == "completed"


def test_the_sweep_skips_when_nothing_is_configured(monkeypatch):
    rows = [{"id": 1, "client_ref": "a", "indexer_id": 7, "title": "X"}]
    res, _db = _sweep_with(monkeypatch, rows, _cfg())
    assert res["status"] == "skipped" and res["reason"] == "no_goals_set"


# ── the manual grab path carries the id too ─────────────────────────────────
def test_a_soulseek_grab_carries_no_indexer_id():
    """Soulseek has no indexers. The branch split must stay deliberate."""
    from core.automation.handlers.video_process_wishlist import build_download_record
    rec = build_download_record(
        {"tmdb_id": 1, "title": "Heat", "year": 1995},
        {"source": "soulseek", "filename": "Heat.mkv", "username": "bob", "size_bytes": 1},
        [], media_type="movie", target_dir="/movies", query="heat")
    assert "indexer_id" not in rec


def test_the_manual_grab_records_the_indexer_too(tmp_path, monkeypatch):
    """The drain isn't the only way a torrent lands — the UI grab button is the
    common one, and it was only covered by the drain's test."""
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    videoapi._video_db = db
    try:
        import core.video.client_grab as cg
        monkeypatch.setattr(cg, "grab", lambda *a, **k: {"ok": True, "ref": "hash123"})
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        client = app.test_client()
        r = client.post("/api/video/downloads/grab", json={
            "source": "torrent", "kind": "movie", "title": "Heat", "year": 1995,
            "release_title": "Heat.1995.1080p", "download_url": "http://x/t.torrent",
            "username": "SomeTracker", "indexer_id": 7,
            "media_id": "1", "media_source": "tmdb", "size_bytes": 1})
        assert r.status_code == 200 and r.get_json().get("ok"), r.get_json()
        row = db.list_video_downloads()[0]
        assert row["indexer_id"] == 7, "the UI grab path dropped the indexer id"
    finally:
        videoapi._video_db = None



def test_extto_is_a_valid_video_hybrid_source():
    from core.video import download_config

    class DB:
        def __init__(self):
            self.settings = {}

        def get_setting(self, key):
            return self.settings.get(key)

        def set_setting(self, key, value):
            self.settings[key] = value

    db = DB()
    out = download_config.save(db, {"download_mode": "hybrid",
                                    "hybrid_order": ["torrent", "extto", "soulseek", "extto"]})
    assert out["hybrid_order"] == ["torrent", "extto", "soulseek"]
    assert download_config.load(db)["hybrid_order"] == ["torrent", "extto", "soulseek"]
