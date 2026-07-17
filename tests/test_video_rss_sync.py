"""RSS-speed grabbing (arr-parity P1) — recent releases vs the wishlist.

Sonarr's biggest speed edge was RSS sync: grab a wanted release the moment it
hits the indexer, no searching. ``core/video/rss_sync.rss_pass`` pulls the
indexers' latest (Newznab empty-query via Prowlarr, one aggregate call) and
routes matches through the drain's OWN seams — gated wishlist queries,
upgrade-until-cutoff, the ranker (profile + blocklist + scope validation),
pick_best, _default_enqueue, active-key dedupe. These tests drive the pass
with an injected feed and recorded enqueues; nothing touches the network.
"""

from __future__ import annotations

import pytest

import core.video.rss_sync as rss
from core.automation.handlers import video_process_wishlist as vpw
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


def _feed_hit(title, *, proto="torrent", size=4_000_000_000, seeders=50):
    return {"title": title, "size_bytes": size, "seeders": seeders, "peers": 5,
            "username": "indexer", "availability": seeders, "filename": title,
            "files": [], "file_count": 0, "folder_size_bytes": size,
            "download_url": "http://idx/dl/" + title.replace(" ", "."),
            "protocol": proto, "indexer_id": 1, "guid": "g-" + title}


@pytest.fixture()
def seams(monkeypatch):
    """Record enqueues; pin target dir; start with no active downloads."""
    grabs = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/" + mt)
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, root: grabs.append(
                            {"item": item, "best": best, "cands": cands,
                             "media_type": mt, "root": root}) or True)
    return grabs


def _enable_torrent(db):
    from core.video.download_config import save
    save(db, {"download_mode": "torrent"})


# ---------------------------------------------------------------------------

def test_skips_when_prowlarr_unconfigured(db, seams):
    out = rss.rss_pass(fetch=lambda: None)
    assert out["status"] == "skipped" and out["reason"] == "prowlarr_not_configured"
    assert seams == []


def test_skips_when_no_indexer_source_in_download_mode(db, seams):
    # default mode is soulseek-only — RSS must never hand a torrent to that user
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")])
    assert out["status"] == "skipped" and out["reason"] == "no_indexer_source_enabled"
    assert seams == []


def test_matching_release_is_grabbed_instantly(db, seams):
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    feed = [_feed_hit("Totally Unrelated Show S01E01 720p"),
            _feed_hit("Heat 1995 1080p BluRay x264-GRP")]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["status"] == "completed"
    assert out["grabbed"] == 1 and out["matched_items"] == 1
    assert len(seams) == 1
    g = seams[0]
    assert g["media_type"] == "movie" and g["root"] == "/media/movie"
    assert g["best"]["source"] == "torrent"
    assert "Heat" in g["best"]["title"]


def test_episode_release_matches_by_sxxexx(db, seams):
    _enable_torrent(db)
    db.add_episodes_to_wishlist(500, "Severance", [
        {"season_number": 2, "episode_number": 7, "air_date": "2026-07-01"}])
    feed = [_feed_hit("Severance S02E07 1080p WEB h264-NTb"),
            _feed_hit("Severance S02E06 1080p WEB h264-NTb")]   # wrong ep must not match
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 1
    assert seams[0]["media_type"] == "episode"
    assert "S02E07" in seams[0]["best"]["title"]


def test_active_download_is_never_double_grabbed(db, seams, monkeypatch):
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: {("movie", "1")})
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")])
    assert out["grabbed"] == 0 and seams == []


def test_wrong_protocol_hits_are_filtered(db, seams):
    _enable_torrent(db)   # torrent-only chain
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP", proto="usenet")])
    assert out["grabbed"] == 0 and seams == []


def test_owned_item_only_accepts_strictly_better(db, seams, monkeypatch):
    """Upgrade-until-cutoff rides along: owned at 1080p (cutoff 2160p in the
    profile default? no — pin the cutoff via annotate seam) — a same-quality
    release must not re-grab."""
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    # simulate the drain's annotation outcome: owned at 1080p → _min_rank set
    real_annotate = vpw.annotate_upgrades
    orig_fetch = db.movie_wishlist_to_download

    def fake_items():
        items = orig_fetch()
        for it in items:
            it["owned"] = 1
            it["owned_resolutions"] = "1080p"
        return items

    monkeypatch.setattr(db, "movie_wishlist_to_download", fake_items)
    monkeypatch.setattr(vpw, "_default_cutoff_rank", lambda: 999)   # cutoff far above → stays a watch
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")])
    assert out["grabbed"] == 0, "a same-resolution release must never re-grab an owned copy"
    assert real_annotate is vpw.annotate_upgrades


def test_overlap_guard_skips_concurrent_tick(db, seams):
    rss._running = True
    try:
        out = rss.rss_pass(fetch=lambda: [])
        assert out["status"] == "skipped" and out["reason"] == "already_running"
    finally:
        rss._running = False


def test_running_flag_always_released_even_on_error(db):
    with pytest.raises(RuntimeError):
        rss.rss_pass(fetch=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rss.is_running() is False


def test_handler_block_and_seed_are_registered():
    import core.automation.blocks as blocks_mod
    import core.automation_engine as eng_mod
    src_blocks = open(blocks_mod.__file__, encoding="utf-8").read()
    src_eng = open(eng_mod.__file__, encoding="utf-8").read()
    assert '"type": "video_rss_sync"' in src_blocks
    assert "'action_type': 'video_rss_sync'" in src_eng
    assert "'unit': 'minutes'" in src_eng
    import core.automation.handlers.registration as reg
    src_reg = open(reg.__file__, encoding="utf-8").read()
    assert "'video_rss_sync'" in src_reg


# ── skip-reason logging (Boulder: prove a quiet "0 grabbed") ──────────────────

def test_skip_reason_reports_scope_or_quality_rejection(db, seams):
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    # a namesake with a mismatched YEAR — passes the loose prescreen, fails the ranker
    logs = []
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 2049 1080p BluRay x264-GRP")],
                       log=logs.append)
    assert out["grabbed"] == 0
    assert any(l.startswith("RSS skip: Heat") and "none accepted" in l for l in logs), logs


def test_skip_reason_reports_upgrade_only_for_owned(db, seams, monkeypatch):
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    orig = db.movie_wishlist_to_download

    def owned_1080():
        items = orig()
        for it in items:
            it["owned"] = 1
            it["owned_resolutions"] = "1080p"
        return items

    monkeypatch.setattr(db, "movie_wishlist_to_download", owned_1080)
    monkeypatch.setattr(vpw, "_default_cutoff_rank", lambda: 999)
    logs = []
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")],
                       log=logs.append)
    assert out["grabbed"] == 0
    assert any("upgrade-only" in l and "1080p" in l for l in logs), logs


def test_grab_still_logs_the_grab_line(db, seams):
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    logs = []
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")],
                       log=logs.append)
    assert out["grabbed"] == 1
    assert any(l.startswith("RSS grab: Heat") for l in logs), logs


# ── prescreen precision (the flood Boulder's live logs exposed) ───────────────

@pytest.mark.parametrize("title,release,keep", [
    # false positives from the live run — must be excluded
    ("The Oval", "Lee Cronin's The Mummy 2025 1080p WEB", False),
    ("Love Island USA", "Muppet Treasure Island 1996 1080p", False),
    ("RuPaul's Drag Race All Stars", "Cornwall A Year by the Sea S01E01", False),
    ("One Piece", "Normal 2023 1080p WEB", False),
    ("The Smurfs", "Lee Cronin's The Mummy", False),
    # real matches must still pass the prescreen
    ("The Oval", "Tyler.Perry.The.Oval.S07E09.1080p.WEB-GRP", True),
    ("One Piece", "One.Piece.S23E1170.1080p.WEB", True),
    ("Love Island USA", "Love.Island.USA.S08E36.720p.WEB", True),
    ("The Daily Show", "The.Daily.Show.2026.07.08.1080p.WEB", True),
])
def test_prescreen_is_word_level_and_stopword_aware(title, release, keep):
    kept = rss._prescreen([{"title": release}], [title])
    assert (len(kept) == 1) is keep, (title, release)


def test_prescreen_substring_no_longer_false_matches():
    # 'all' (Stars) must not match inside 'Cornwall'; 'the' alone never qualifies
    assert rss._prescreen([{"title": "Cornwall Documentary 1080p"}], ["All Stars"]) == []
    assert rss._prescreen([{"title": "The Anything Show 1080p"}], ["The Oval"]) == []
