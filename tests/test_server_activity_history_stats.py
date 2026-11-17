"""server activity history and stats, built from the play log: grouped rows,
filters, the per-play timeline, every stats number, and plex's own history
imported once so day one isn't empty.
"""

from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from core.server_activity import history, stats, store
from tests.sa_helpers import FakePlex, sa_env  # noqa: F401 - fixture

UTC = timezone.utc
# monday 2026-11-02 00:00 utc
MON = datetime(2026, 11, 2, tzinfo=UTC).timestamp()


def P(key, start, played_min, *, kind="movie", user="maya", method="direct_play", group=None, title=None,
      parent=None, gp=None, remote=0, location=None, product="Plex for Apple TV", reasons=None, hw=None,
      season=None, episode=None, sent_kb=0, lossless=None, book_percent=None, source="live", duration_ms=None,
      chapter=None, rk=None, stopped=True, segments=None, **kw):
    end = start + played_min * 60
    row = {c: None for c in store.PLAY_COLUMNS}
    row.update(play_key=key, group_key=group or key, source=source, server="plex", server_name="Basement",
               kind=kind, title=title or key, parent_title=parent, grandparent_title=gp, user=user,
               rating_key=rk or key, started_at=start, stopped_at=end if stopped else None, last_seen=end,
               played_ms=int(played_min * 60_000) if source == "live" else 0, paused_ms=0, buffering_ms=0,
               pause_count=0, buffer_count=0, method=method, reasons=reasons or [], hw=hw, remote=remote,
               location=location, player_product=product, season=season, episode=episode, sent_kb=sent_kb,
               lossless=lossless, book_percent=book_percent, duration_ms=duration_ms, chapter=chapter,
               segments=segments or [["playing", start, end]], percent=100)
    row.update(kw)
    return row


def seed(rows):
    store.apply_ops([("play", r) for r in rows])


# ── history ─────────────────────────────────────────────────────────────────

def test_back_to_back_episodes_fold_into_one_row(sa_env):
    seed([P("e4", MON, 50, kind="episode", group="g", gp="Andor", season=2, episode=4, title="One Way Out"),
          P("e5", MON + 3100, 50, kind="episode", group="g", gp="Andor", season=2, episode=5),
          P("e6", MON + 6200, 51, kind="episode", group="g", gp="Andor", season=2, episode=6),
          P("dune", MON + 20000, 120, title="Dune: Part Two", year=2024)])
    out = history.get_history(tz="UTC", now=MON + 86400)
    assert [g["title"] for g in out["groups"]] == ["Dune: Part Two", "Andor"]
    andor = out["groups"][1]
    assert andor["count"] == 3 and andor["subtitle"] == "S2 E4 – E6 · back to back"
    assert andor["played_ms"] == 151 * 60_000 and andor["percent"] == 100
    assert out["groups"][0]["subtitle"] == "2024 · Movie"
    assert out["summary"]["plays"] == 4 and out["summary"]["people"] == 1
    assert out["days"] == [{"date": "2026-11-02", "plays": 4, "played_ms": 271 * 60_000}]
    assert out["facets"]["users"][0]["user"] == "maya"


def test_history_filters(sa_env):
    seed([P("a", MON, 30, kind="track", user="sam", title="Dreams", parent="Rumours", gp="Fleetwood Mac"),
          P("b", MON + 100, 30, method="transcode", remote=1, location="Chicago, IL", title="The Wild Robot"),
          P("c", MON + 200, 30, kind="audiobook", user="ruth", title="Project Hail Mary"),
          P("d", MON - 10 * 86400, 30, title="Old")])
    now = MON + 86400
    titles = lambda **f: [g["title"] for g in history.get_history(now=now, tz="UTC", **f)["groups"]]  # noqa: E731
    assert titles(kind="music") == ["Dreams"]
    assert titles(kind="audiobooks") == ["Project Hail Mary"]
    assert titles(user="sam") == ["Dreams"]
    assert titles(method="transcode") == ["The Wild Robot"]
    assert titles(q="chicago") == ["The Wild Robot"]
    assert titles(q="_") == [] and titles(q="%") == []      # like wildcards are literal
    assert "Old" not in titles(days=7) and "Old" in titles()
    s = history.get_history(now=now, tz="UTC", days=7)["summary"]
    assert s["converted_pct"] == 33 and s["away_places"] == 1


def test_history_pages_by_group(sa_env):
    seed([P(f"m{i}", MON + i * 1000, 10) for i in range(5)])
    p0 = history.get_history(per_page=2, page=0, now=MON + 86400, tz="UTC")
    p2 = history.get_history(per_page=2, page=2, now=MON + 86400, tz="UTC")
    assert [g["title"] for g in p0["groups"]] == ["m4", "m3"] and p0["has_more"] is True
    assert [g["title"] for g in p2["groups"]] == ["m0"] and p2["has_more"] is False
    assert p2["facets"] is None


def test_an_album_run_and_a_chapter_run_read_well():
    album = history.build_group([P(f"t{i}", MON + i * 240, 4, kind="track", parent="Rumours", gp="Fleetwood Mac",
                                   lossless=1, quality="FLAC 16-bit · 44.1 kHz") for i in range(11)])
    assert (album["title"], album["subtitle"], album["count_label"]) == ("Rumours", "Fleetwood Mac", "11 tracks")
    book = history.build_group([P(f"c{i}", MON + i * 1500, 24, kind="audiobook", title="Project Hail Mary",
                                  gp="Andy Weir", chapter=f'{{"index": {i}, "count": 32}}', book_percent=30 + i)
                                for i in (11, 12, 13)])
    assert book["subtitle"] == "Chapters 11 – 13 · Andy Weir" and book["book_percent"] == 43


def test_the_group_picks_the_worst_method_and_its_change():
    g = history.build_group([P("a", MON, 50, kind="episode", method="direct_play", change=None),
                             P("b", MON + 3100, 50, kind="episode", method="transcode", change="Subtitles burned in",
                               reasons=["subtitle_burn_image"])])
    assert g["method"] == "transcode" and g["change"] == "Subtitles burned in"
    assert g["reasons"] == [{"code": "subtitle_burn_image", "label": "Image subtitles burned in"}]


def test_the_timeline_shows_pauses_and_the_time_between_plays(sa_env):
    seed([P("e4", MON, 50, kind="episode", group="g",
            segments=[["playing", MON, MON + 1200], ["paused", MON + 1200, MON + 1500],
                      ["playing", MON + 1500, MON + 3000]]),
          P("e5", MON + 3480, 40, kind="episode", group="g")])
    g = history.get_history_group("g")
    states = [(b["state"], b["end"] - b["start"]) for b in g["timeline"]]
    assert states == [("playing", 1200), ("paused", 300), ("playing", 1500), ("between", 480), ("playing", 2400)]
    assert history.get_history_group("nope") == {"ok": False, "reason": "not_found"}


# ── stats ───────────────────────────────────────────────────────────────────

def _compute(rows, days=7, **kw):
    return stats.compute(rows, start=MON, end=MON + days * 86400, days=days, tz=UTC, **kw)


def test_hours_are_spread_over_the_hours_they_covered():
    # saturday 21:30 for 2 h
    sat = MON + 5 * 86400 + 21.5 * 3600
    out = _compute([P("a", sat, 120)])
    cells = out["heatmap"]["cells"]
    assert cells[5][21] == 0.5 and cells[5][22] == 1.0 and cells[5][23] == 0.5
    assert out["heatmap"]["busiest"] == {"weekday": 5, "hour_start": 21, "hour_end": 23}
    assert out["heatmap"]["busiest_daypart"] == "night"          # 1.5 h after 10 pm
    day = next(d for d in out["per_day"] if d["date"] == "2026-11-07")
    assert day["hours"] == 2.0 and day["weekend"] is True and day["plays"] == 1


def test_a_play_across_midnight_counts_on_both_days():
    out = _compute([P("a", MON + 23 * 3600, 120)])
    by = {d["date"]: d["hours"] for d in out["per_day"]}
    assert by["2026-11-02"] == 1.0 and by["2026-11-03"] == 1.0


def test_tiles_methods_and_reasons():
    rows = [P("a", MON, 60, method="direct_play"), P("b", MON + 4000, 60, method="direct_stream"),
            P("c", MON + 8000, 60, method="transcode", reasons=["over_remote_limit", "hdr"], hw=1, remote=1,
              user="maya", sent_kb=5_400_000),
            P("d", MON + 9000, 60, method="transcode", reasons=["over_remote_limit"], hw=0, remote=1,
              user="maya", sent_kb=600_000),
            P("e", MON + 9500, 30, kind="track", method=None, user="sam")]
    out = _compute(rows, prev_plays=4, minutes=[{"minute": int((MON + 9100) // 60), "streams": 3,
                                                 "remote_kbps": 34_000}])
    t = out["tiles"]
    assert t["plays"] == {"value": 5, "prev": 4, "change_pct": 25}
    assert t["hours"]["value"] == 4.5 and t["hours"]["by_kind"]["films"] == 4.0 and t["hours"]["by_kind"]["music"] == 0.5
    assert t["played_as_is"] == {"pct": 50, "plays": 2, "known": 4}
    assert t["most_at_once"] == {"value": 3, "at": int((MON + 9100) // 60) * 60}
    assert t["sent_remotely"]["gb"] == 6.0 and t["sent_remotely"]["peak_kbps"] == 34_000
    assert out["methods"]["transcode"] == {"plays": 2, "pct": 50}
    assert out["reasons"][0] == {"code": "over_remote_limit", "label": "Over the player's remote quality", "plays": 2}
    assert out["hardware"] == {"hw": 1, "total": 2}
    h = out["headline"]
    assert h["converted_one_in"] == 2 and h["top_converted_user"] == "maya" and h["top_converted_user_away_pct"] == 100
    assert out["unknown_method_plays"] == 1


def test_most_at_once_falls_back_to_overlapping_plays():
    out = _compute([P("a", MON, 60), P("b", MON + 600, 60), P("c", MON + 1200, 60), P("d", MON + 7200, 10)])
    assert out["tiles"]["most_at_once"] == {"value": 3, "at": MON + 1200}


def test_people_cards():
    rows = [P("a", MON, 60, user="maya", remote=1, location="Chicago, IL", title="Shōgun ep", kind="episode",
              gp="Shōgun", product="Plex for Apple TV"),
            P("b", MON + 4000, 60, user="maya", remote=0, kind="episode", gp="Shōgun", product="Plex for iOS"),
            P("c", MON + 8000, 10, user="sam", kind="track", gp="Radiohead", lossless=1, product="Plexamp"),
            P("d", MON + 9000, 10, user="sam", kind="track", gp="Radiohead", lossless=0, product="Plexamp")]
    out = _compute(rows, live_users=["sam"])
    maya, sam = out["people"]
    assert maya["user"] == "maya" and maya["hours"] == 2.0 and maya["plays"] == 2
    assert maya["mostly"] == {"title": "Shōgun", "kind": "episode"} and maya["away_pct"] == 50
    assert maya["top_location"] == "Chicago, IL" and set(maya["devices"]) == {"Plex for Apple TV", "Plex for iOS"}
    assert len(maya["spark"]) == 14 and maya["live"] is False
    assert sam["lossless_pct"] == 50 and sam["live"] is True and sam["away_pct"] == 0


def test_shelves():
    rows = [P("o1", MON, 180, title="Oppenheimer", year=2023, user="dan"),
            P("o2", MON + 20000, 180, title="Oppenheimer", year=2023, user="maya"),
            P("s1", MON + 40000, 60, kind="episode", gp="Shōgun", rk="e1"),
            P("s2", MON + 44000, 60, kind="episode", gp="Shōgun", rk="e2"),
            P("s3", MON + 48000, 60, kind="episode", gp="Shōgun", rk="e2"),
            P("r1", MON + 60000, 4, kind="track", gp="Radiohead"),
            P("b1", MON + 70000, 60, kind="audiobook", title="Project Hail Mary", user="ruth", book_percent=44),
            P("p1", MON + 80000, 30, kind="podcast", title="ep 1", parent="Hard Fork", rk="p1"),
            P("p2", MON + 90000, 30, kind="podcast", title="ep 2", parent="Hard Fork", rk="p2")]
    top = _compute(rows)["top"]
    assert top["films"][0]["title"] == "Oppenheimer" and top["films"][0]["people"] == 2 and top["films"][0]["hours"] == 6.0
    assert top["shows"][0] == dict(top["shows"][0], title="Shōgun", episodes=2, hours=3.0)
    assert top["artists"][0]["tracks"] == 1
    books = {b["title"]: b for b in top["books"]}
    assert books["Project Hail Mary"]["percent"] == 44 and books["Project Hail Mary"]["user"] == "ruth"
    assert books["Project Hail Mary"]["finished"] is False
    assert books["Hard Fork"]["kind"] == "podcast" and books["Hard Fork"]["episodes"] == 2


def test_players_and_places():
    rows = [P("a", MON, 10, product="Plexamp"), P("b", MON + 1000, 10, product="Plexamp"),
            P("c", MON + 2000, 10, product="Shield", remote=1, location="Leeds, UK"),
            P("d", MON + 3000, 10, product="Shield", remote=1, location=None)]
    out = _compute(rows)
    assert [p["product"] for p in out["players"]] == ["Plexamp", "Shield"]
    labels = {p["label"]: p for p in out["locations"]}
    assert labels["Home network"]["plays"] == 2 and labels["Home network"]["home"] is True
    assert labels["Leeds, UK"]["plays"] == 1 and "Away, place unknown" in labels


def test_plex_history_rows_count_as_plays_and_their_time_as_an_estimate():
    rows = [P("h", MON, 0, source="plex_history", duration_ms=3_600_000, method=None),
            P("l", MON + 7200, 30)]
    t = _compute(rows)["tiles"]
    assert t["plays"]["value"] == 2 and t["hours"]["value"] == 1.5 and t["hours"]["estimated_hours"] == 1.0
    assert t["played_as_is"]["known"] == 1


def test_get_stats_snaps_the_range_and_caches(sa_env):
    seed([P("a", MON + 3600, 60)])
    out = stats.get_stats(days=29, tz="UTC", now=MON + 86400)
    assert out["days"] == 30 and out["tiles"]["plays"]["value"] == 1
    seed([P("b", MON + 7200, 60)])
    assert stats.get_stats(days=30, tz="UTC", now=MON + 86400 + 5)["tiles"]["plays"]["value"] == 1
    store.clear_play_log()
    assert stats.get_stats(days=30, tz="UTC", now=MON + 86400 + 6)["tiles"]["plays"]["value"] == 0


# ── plex history import ─────────────────────────────────────────────────────

def _hist(rk, viewed, acct="7341", typ="episode", **a):
    return ET.Element("Video", ratingKey=rk, viewedAt=str(int(viewed)), accountID=acct, type=typ,
                      title=f"t{rk}", grandparentTitle="Andor", grandparentRatingKey="g1", parentIndex="1",
                      index=rk, librarySectionID="2", historyKey=f"/status/sessions/history/{rk}", **a)


def test_plex_history_fills_the_time_before_the_log_once(sa_env):
    plex = sa_env.use_plex()
    plex.accounts = [{"id": "7341", "name": "maya", "thumb": "https://plex.tv/users/x/avatar"}]
    plex.history = [_hist("3", MON + 7000, duration="3000000"), _hist("2", MON + 3500, duration="3000000"),
                    _hist("1", MON, duration="3000000"), _hist("9", MON + 900000)]
    seed([P("live", MON + 800000, 30)])               # the log began here
    assert history.backfill_plex_history(now=MON + 10 * 86400) == 3
    rows = store.query("SELECT * FROM server_plays WHERE source = 'plex_history' ORDER BY started_at")
    assert [r["title"] for r in rows] == ["t1", "t2", "t3"]
    assert len({r["group_key"] for r in rows}) == 1 and rows[0]["user"] == "maya"
    assert rows[0]["started_at"] == MON - 3000 and rows[0]["played_ms"] == 0 and rows[0]["method"] is None
    assert history.backfill_plex_history(now=MON + 10 * 86400) == 0          # once
    calls = [c for c in plex.calls if c[0] == "/status/sessions/history/all"]
    assert calls[0][1]["viewedAt>"] == int(MON + 10 * 86400 - 365 * 86400)


def test_retention_runs_from_the_sampler_maintenance(sa_env, monkeypatch):
    from core.server_activity import live
    import time as _time
    now = _time.time()
    seed([P("old", now - 100 * 86400, 30), P("new", now - 86400, 30)])
    sa_env.settings["retention_days"] = 30
    monkeypatch.setattr(history, "backfill_plex_history", lambda: 0)
    live._maint["running"] = True
    live._maintenance()
    assert [r["play_key"] for r in store.query("SELECT play_key FROM server_plays")] == ["new"]
