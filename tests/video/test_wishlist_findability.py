"""why the episode wishlist couldn't find popular episodes (oct 16 2026).

boulder: "so many undownloaded episodes like it couldnt find them, even popular
ones like dark matter". read from his live video db, the releases were there
and the drain threw them away:

- 'Dark.Matter.2024.S02E08.1080p.WEB.H264-CAKES' was found and rejected as
  "Wrong year (2024, wanted 2026)": the show-identity gate compared the show's
  year in the release name to the EPISODE's air year.
- every 'Big.Brother.2023.S03Exx' was "Wrong title": the release parses as
  "Big Brother" with the year cut off, the show is "Big Brother (2023)".
- airing seasons were folded into one season-pack search. no pack exists for a
  season still airing, every single-episode release was "Not a full-season
  pack", that verdict was stamped on every member and they were never searched
  one by one.
- 'Show.S15E14.HDTV.x264' and a DVD remux had no tier: "Unknown quality".
"""

import pytest

import core.automation.handlers.video_process_wishlist as wl
from core.video.quality_eval import evaluate_release, tier_key, tv_year_mismatch
from core.video.quality_profile import TIERS, default_profile, normalize
from core.video.release_parse import parse_release, titles_match
from tests.test_video_process_wishlist import _cand, _run

DARK_MATTER = {"show_tmdb_id": 196322, "show_title": "Dark Matter (2024)", "season_number": 2,
               "episode_number": 8, "air_date": "2026-10-15", "show_year": 2024, "tvdb_id": 393159}


@pytest.fixture(autouse=True)
def _no_alias_lookup(monkeypatch):
    # aliases come from tmdb and the db; these tests are about the gates
    monkeypatch.setattr(wl, "_acceptable_titles", lambda primary, kind, tmdb_id: [primary] if primary else [])


# -- the year ------------------------------------------------------------------

def test_show_year_is_the_shows_not_the_episodes():
    assert wl.show_year_for(DARK_MATTER) == "2024"
    assert wl.show_year_for({"show_title": "Dark Matter (2024)"}) == "2024"
    assert wl.show_year_for({"show_title": "Silo", "air_date": "2026-01-01"}) is None
    assert wl.show_year_for({"show_title": "Silo", "show_year": "2023-05-05"}) == "2023"
    assert wl.show_year_for({"show_title": "x", "show_year": "junk"}) is None


def test_episode_search_context_carries_the_show_year():
    ctx = wl.search_context(dict(DARK_MATTER), "episode")
    assert ctx["year"] == "2024"
    assert ctx["air_date"] == "2026-10-15"
    # a show with no known year gets no year, never its air year
    ctx = wl.search_context({"show_tmdb_id": 1, "show_title": "Silo", "season_number": 2,
                             "episode_number": 1, "air_date": "2026-02-01"}, "episode")
    assert ctx["year"] is None


def _judge(release, item):
    ctx = wl.search_context(dict(item), "episode")
    return evaluate_release(parse_release(release), default_profile(), scope=ctx["scope"],
                            want_season=ctx["season"], want_episode=ctx["episode"],
                            want_year=ctx["year"], want_title=ctx.get("titles") or ctx["title"],
                            want_date=ctx.get("air_date"))


def test_dark_matter_s02e08_is_accepted():
    """the exact release from boulder's soulseek results"""
    v = _judge("Dark.Matter.2024.S02E08.1080p.WEB.H264-CAKES", DARK_MATTER)
    assert v["accepted"], v["rejected"]


def test_the_other_dark_matter_is_still_refused():
    v = _judge("Dark.Matter.2015.S02E08.1080p.WEB.H264-GRP", DARK_MATTER)
    assert not v["accepted"]
    # and with a bare "Dark Matter" alias (tmdb has one) it's the year that says no
    item = dict(DARK_MATTER, show_title="Dark Matter")
    v = _judge("Dark.Matter.2015.S02E08.1080p.WEB.H264-GRP", item)
    assert not v["accepted"] and "year" in v["rejected"].lower()
    assert _judge("Dark.Matter.2024.S02E08.1080p.WEB.H264-CAKES", item)["accepted"]


def test_tv_year_rule():
    assert tv_year_mismatch(2015, 2024)
    assert not tv_year_mismatch(2024, 2024)
    assert not tv_year_mismatch(2003, 2004)     # battlestar galactica's miniseries year
    assert not tv_year_mismatch(2025, 2024)
    assert not tv_year_mismatch(None, 2024) and not tv_year_mismatch(2024, None)


def test_the_importer_uses_the_same_rule(monkeypatch):
    """the importer had its own copy of the year check and rejected the same
    files at import"""
    import inspect
    import core.video.importer as imp
    assert "tv_year_mismatch(rel_year, want_year)" in inspect.getsource(imp)


# -- the title -----------------------------------------------------------------

BB = ["Big Brother (2023)", "Big Brother 2023 - The Ultimate Social Experiment", "Big Brother UK"]


def test_a_year_qualified_show_matches_its_year_tagged_releases():
    assert titles_match("Big Brother 2023 S03E16 1080p ITVX WEB-DL AAC2 0 H 264-RAWR", BB)
    assert titles_match("Big.Brother.2024.S03E16.720p", BB)          # one year off is the same show


def test_but_not_a_release_without_the_year_or_with_another_year():
    assert not titles_match("Big Brother S03E16 1080p", BB)            # could be any big brother
    assert not titles_match("Big.Brother.2000.S27E01.1080p", BB)        # the us one
    assert not titles_match("Avatar.The.Last.Airbender.S01E01.1080p", "Avatar: The Last Airbender (2024)")
    assert titles_match("Avatar.The.Last.Airbender.2024.S01E01.1080p", "Avatar: The Last Airbender (2024)")


def test_titles_that_are_or_contain_years_keep_working():
    assert titles_match("1917.2019.1080p.BluRay", "1917")
    assert titles_match("Blade.Runner.2049.2017.1080p", "Blade Runner 2049")
    assert titles_match("Blade.Runner.2049.1080p", "Blade Runner 2049")
    assert not titles_match("Blade.Runner.1982.1080p", "Blade Runner 2049")


# -- season packs ----------------------------------------------------------------

def _season(n, air_dates, **extra):
    return [dict({"show_tmdb_id": 7, "show_title": "60 Minutes", "season_number": n,
                  "episode_number": i + 1, "air_date": d}, **extra) for i, d in enumerate(air_dates)]


def test_a_season_still_airing_gets_no_pack_search():
    airing = _season(59, ["2026-09-14", "2026-09-21", "2026-10-12"])
    assert wl.season_pack_requests(airing, active=set(), today="2026-10-16") == []
    done = _season(58, ["2025-09-07", "2025-12-14", "2026-05-17"])
    packs = wl.season_pack_requests(done, active=set(), today="2026-10-16")
    assert len(packs) == 1 and len(packs[0]["_pack_rows"]) == 3


def test_the_librarys_newest_air_date_counts_too():
    """you can miss the early episodes of a season that aired an episode yesterday"""
    rows = _season(4, ["2026-01-01", "2026-01-08", "2026-01-15"], season_last_air="2026-10-15")
    assert wl.season_pack_requests(rows, active=set(), today="2026-10-16") == []


def test_season_still_airing():
    assert wl.season_still_airing([{"air_date": "2026-10-10"}], today="2026-10-16")
    assert not wl.season_still_airing([{"air_date": "2026-09-01"}], today="2026-10-16")
    assert not wl.season_still_airing([{"air_date": None}], today="2026-10-16")
    assert wl.season_still_airing([{"air_date": "2026-10-20"}], today="2026-10-16")


def test_a_pack_that_isnt_there_falls_back_to_the_episodes():
    items = _season(58, ["2025-09-07", "2025-12-14", "2026-05-17"])
    records = []
    searches = {
        ("season", "7", 58): [_cand("60.Minutes.S58E02.1080p", accepted=False)],   # no pack
        ("episode", "7", 58, 1): [dict(_cand("60.Minutes.S58E01.1080p"), source="torrent")],
        ("episode", "7", 58, 2): [dict(_cand("60.Minutes.S58E02.1080p"), source="torrent")],
        ("episode", "7", 58, 3): [],
    }
    seen, enq = [], []

    def search(item, mt):
        seen.append(wl.item_key(item, mt))
        return searches.get(wl.item_key(item, mt), [])

    def enqueue(item, best, cands, mt, target):
        enq.append(wl.item_key(item, mt))
        return True

    from tests.test_video_process_wishlist import _Deps
    res = wl.auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1, "max_simultaneous_downloads": 5}, _Deps(),
        media_type="episode", fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: "/tv", search=search, enqueue=enqueue,
        record_outcome=lambda it, mt, ok, refusal=None: records.append((wl.item_key(it, mt), ok)),
        record_note=lambda *a, **k: None)
    assert seen == [("season", "7", 58), ("episode", "7", 58, 1),
                    ("episode", "7", 58, 2), ("episode", "7", 58, 3)]
    assert enq == [("episode", "7", 58, 1), ("episode", "7", 58, 2)]
    assert res["grabbed"] == 2
    # the pack's "not a full-season pack" is never stamped on the season; each
    # episode records its own verdict
    assert ("season", "7", 58) not in [r[0] for r in records]
    assert (("episode", "7", 58, 3), False) in records


def test_a_pack_that_lands_doesnt_search_the_episodes():
    items = _season(58, ["2025-09-07", "2025-12-14", "2026-05-17"])
    res, enq, seen, _ = _run(items, root="/tv", media_type="episode", searches={
        ("season", "7", 58): [dict(_cand("60.Minutes.S58.1080p"), source="torrent")]})
    assert seen == [("season", "7", 58)] and res["grabbed"] == 1


# -- qualities that had no tier -----------------------------------------------

def test_hdtv_without_a_resolution_is_sdtv():
    assert tier_key("hdtv", None) == "sdtv"
    prof = normalize({**default_profile(), "tiers": [{"key": k, "enabled": k == "sdtv"} for k in TIERS]})
    v = evaluate_release(parse_release("The.Voice.AU.S15E14.HDTV.x264-NGP"), prof,
                         scope="season", want_season=15)
    assert v["tier"] == "sdtv"


def test_a_dvd_remux_is_dvd():
    r = parse_release("Duckman S01 NTSC DVD REMUX DD 2.0-Spark")
    assert r["source"] == "dvd" and tier_key(r["source"], r["resolution"]) == "dvd"
    # a real blu-ray remux is untouched
    r = parse_release("Movie.2019.1080p.BluRay.REMUX.AVC")
    assert r["source"] == "remux" and tier_key(r["source"], r["resolution"]) == "remux-1080p"


# -- the drain's rows carry what the fixes need -----------------------------------

def test_the_drain_rows_carry_the_show_year_and_the_seasons_last_air(tmp_path):
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    conn = db._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shows)")}
        show_cols = ["title", "tmdb_id", "year", "first_air_date"]
        values = ["Dark Matter (2024)", 196322, 2024, "2024-05-07"]
        for col, default in (("monitored", 1), ("added_at", "2026-01-01"), ("updated_at", "2026-01-01"),
                             ("episodes_synced", 1), ("ratings_synced", 1), ("details_synced", 1)):
            if col in cols:
                show_cols.append(col)
                values.append(default)
        conn.execute("INSERT INTO shows (%s) VALUES (%s)" % (", ".join(show_cols), ", ".join("?" * len(values))), values)
        show_id = conn.execute("SELECT id FROM shows WHERE tmdb_id=196322").fetchone()[0]
        season_cols = {r[1] for r in conn.execute("PRAGMA table_info(seasons)")}
        s_cols, s_vals = ["show_id", "season_number"], [show_id, 2]
        for col, default in (("added_at", "2026-01-01"), ("updated_at", "2026-01-01"), ("monitored", 1)):
            if col in season_cols:
                s_cols.append(col)
                s_vals.append(default)
        conn.execute("INSERT INTO seasons (%s) VALUES (%s)" % (", ".join(s_cols), ", ".join("?" * len(s_vals))), s_vals)
        season_id = conn.execute("SELECT id FROM seasons WHERE show_id=?", (show_id,)).fetchone()[0]
        for ep, air in ((7, "2026-10-08"), (8, "2026-10-15")):
            conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                         "monitored, has_file, air_date) VALUES (?, ?, 2, ?, 1, 0, ?)",
                         (show_id, season_id, ep, air))
        conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, episode_number, "
                     "air_date, status) VALUES ('episode', 196322, 'Dark Matter (2024)', 2, 7, '2026-10-08', 'wanted')")
        conn.commit()
    finally:
        conn.close()
    row = db.episode_wishlist_to_download(due_only=False)[0]
    assert row["show_year"] == 2024
    assert row["season_last_air"] == "2026-10-15"
    manual = db.wishlist_manual_search_items("episode", 196322, 2, 7)[0]
    assert manual["show_year"] == 2024
    assert wl.search_context(row, "episode")["year"] == "2024"


def test_only_a_year_in_the_title_names_the_show():
    """'Show S03E03 2025 1080p' puts the AIR year after the numbering: that's not
    the show's year and must not be judged, or a 2023 show's 2025 episode is
    refused as the wrong show"""
    item = {"show_tmdb_id": 3, "show_title": "Natural High", "season_number": 3,
            "episode_number": 3, "air_date": "2025-06-01", "show_year": 2023}
    assert _judge("Natural High S03E03 2025 1080p WEB-DL H 265 AAC-ADWeb", item)["accepted"]
    # a year before the numbering still says which show it is
    assert not _judge("Natural.High.2015.S03E03.1080p.WEB-DL", item)["accepted"]
