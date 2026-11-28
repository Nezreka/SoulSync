"""ai subtitles: guards, shaping, language choice, naming, the human-beats-ai
rule, the queue, the player's cache key and the saved delay.

the whisper run itself is faked here (``runner=``); a real faster-whisper
run on a generated clip lives at the bottom and skips without the package,
ffmpeg or a model.
"""

from __future__ import annotations

import os
import shutil

import pytest

from core.video.subtitles.ai import engine, guards, language, naming, shaping
from core.video.subtitles.ai import queue as aiq
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seg(start, end, text, lp=-0.3, nsp=0.01, cr=1.3, words=None):
    return {"start": start, "end": end, "text": text, "avg_logprob": lp,
            "no_speech_prob": nsp, "compression_ratio": cr, "words": words or []}


def _words(text, start, per=0.35, gap=0.05):
    out, t = [], start
    for w in text.split():
        out.append({"start": round(t, 3), "end": round(t + per, 3), "word": " " + w})
        t += per + gap
    return out


# ── guards ─────────────────────────────────────────────────────────────────

def test_guards_drop_no_speech_low_logprob_loops_music_and_stock_lines():
    segs = [
        _seg(0, 2, "Real words here.", lp=-0.2),
        _seg(2, 4, "Ghost text", lp=-1.2, nsp=0.8),       # whisper's own skip rule
        _seg(4, 6, "Very unsure", lp=-1.9),                # under the hard floor
        _seg(6, 8, "la la la la la la la la", cr=3.1),     # a loop
        _seg(8, 10, "♪♪"),
        _seg(10, 12, "[MUSIC]"),
        _seg(12, 14, "Thanks for watching!"),
        _seg(14, 16, "Subtitles by the Amara.org community"),
        _seg(16, 18, "Back to the story."),
    ]
    kept, why = guards.filter_segments(segs)
    assert [s["text"] for s in kept] == ["Real words here.", "Back to the story."]
    assert why == {"no_speech": 1, "low_logprob": 1, "loop": 1, "music": 2, "stock": 2, "repeat": 0}


def test_guards_keep_a_sound_cue_with_words_and_a_line_that_mentions_thanks():
    kept, _ = guards.filter_segments([_seg(0, 2, "[door slams] Who's there?"),
                                      _seg(2, 4, "Thanks for watching my dog last week.")])
    assert len(kept) == 2


def test_repeated_lines_collapse_into_one_spanning_the_run():
    segs = [_seg(0, 2, "I'm sorry."), _seg(2, 4, "I'm sorry."), _seg(4, 6, "I'm sorry!"),
            _seg(6, 8, "Fine.")]
    kept, why = guards.filter_segments(segs)
    assert [s["text"] for s in kept] == ["I'm sorry.", "Fine."]
    assert kept[0]["end"] == 6 and why["repeat"] == 2


def test_repeats_inside_one_segment_collapse():
    assert guards.collapse_repeats_in_text("no no no no no no") == "no no"
    assert guards.collapse_repeats_in_text("ano no no no") == "ano no no"
    assert guards.collapse_repeats_in_text("go away. go away. go away. go away.") == "go away. go away."
    assert guards.collapse_repeats_in_text("hello there") == "hello there"


def test_confidence_and_verdict():
    good = [_seg(i * 3, i * 3 + 2.5, "words %d" % i, lp=-0.15) for i in range(20)]
    kept, _ = guards.filter_segments(good)
    conf = guards.confidence(good, kept, speech_seconds=50, duration=60)
    assert conf > guards.LOW_CONFIDENCE and guards.verdict(conf) == "ok"
    junk = [_seg(i * 3, i * 3 + 2, "Thanks for watching!", lp=-1.4, cr=2.8) for i in range(20)]
    kept_j, _ = guards.filter_segments(junk)
    assert kept_j == []
    assert guards.verdict(guards.confidence(junk, kept_j, 2, 3600)) == "discard"
    shaky = [_seg(i * 3, i * 3 + 2.5, "words %d" % i, lp=-1.05) for i in range(20)]
    kept_s, _ = guards.filter_segments(shaky)
    assert guards.verdict(guards.confidence(shaky, kept_s, 50, 60)) == "low"


# ── shaping ────────────────────────────────────────────────────────────────

LONG = ("Well I never thought we would get here, not after everything that happened "
        "last winter in the mountains with the whole family watching us. But here we are, "
        "standing at the door and waiting for someone to open it.")


def test_shaping_respects_two_lines_line_length_and_durations():
    cues = shaping.shape([_seg(0.5, 30, LONG, words=_words(LONG, 0.5))])
    assert len(cues) >= 3
    for start, end, text in cues:
        lines = text.split("\n")
        assert len(lines) <= shaping.MAX_LINES
        assert all(len(line) <= shaping.MAX_LINE for line in lines)
        assert end - start <= shaping.MAX_DURATION + 1e-6
        assert end - start >= 0.2
    for (s1, e1, _t1), (s2, _e2, _t2) in zip(cues, cues[1:]):
        assert s2 - e1 >= shaping.MIN_GAP - 1e-6
    # no word is lost or split
    assert " ".join(t.replace("\n", " ") for _s, _e, t in cues).split() == LONG.split()


def test_shaping_breaks_on_a_pause_and_a_sentence_end():
    w = _words("Get down now.", 0.0) + _words("Somebody is at the window.", 5.0)
    cues = shaping.shape([_seg(0, 8, "", words=w)])
    assert [c[2] for c in cues] == ["Get down now.", "Somebody is at the window."]


def test_short_cue_lasts_long_enough_to_read_but_never_overlaps_the_next():
    w = _words("Hi.", 1.0, per=0.2) + _words("This is a much longer line that needs time.", 1.9)
    cues = shaping.time_cues(shaping.build_cues(shaping.words_of([_seg(1, 6, "", words=w)])))
    assert cues[0][2] == "Hi."
    assert cues[0][1] <= cues[1][0] - shaping.MIN_GAP + 1e-6
    lone = shaping.shape([_seg(10, 10.3, "Run!", words=_words("Run!", 10.0, per=0.3))])
    assert lone[0][1] - lone[0][0] >= shaping.MIN_DURATION - 1e-6


def test_reading_speed_stretches_into_the_silence_after():
    text = "That is a lot of words to read in a very short moment."
    cues = shaping.shape([_seg(0, 1.2, text, words=_words(text, 0.0, per=0.08, gap=0.02))])
    start, end, shown = cues[0]
    assert len(shown.replace("\n", " ")) / (end - start) <= shaping.MAX_CPS + 0.01


def test_segments_without_word_timings_still_shape():
    cues = shaping.shape([_seg(0, 12, LONG)])
    assert cues and all(len(c[2].split("\n")) <= 2 for c in cues)
    srt = shaping.cues_to_srt(cues)
    assert srt.startswith("1\n00:00:00,000 --> ") and "\n\n2\n" in srt


def test_split_lines_balances_and_prefers_punctuation():
    assert shaping.split_lines("short line") == ["short line"]
    a, b = shaping.split_lines("I told you, we should have left before the storm came in")
    assert a.endswith(",") or abs(len(a) - len(b)) < 12
    assert len(a) <= 42 and len(b) <= 42


# ── language ───────────────────────────────────────────────────────────────

def test_choose_audio_prefers_the_wanted_language_and_skips_commentary():
    streams = [{"index": 0, "language": "eng", "title": "Director's Commentary", "default": False},
               {"index": 1, "language": "jpn", "title": "", "default": True},
               {"index": 2, "language": "eng", "title": "English", "default": False}]
    assert language.choose_audio(streams, "en") == (2, "en")
    assert language.choose_audio(streams, "fr") == (1, "ja")


def test_choose_audio_falls_back_to_the_stored_language_only_when_unambiguous():
    untagged = [{"index": 0, "language": None, "title": "", "default": True}]
    assert language.choose_audio(untagged, "en", ["ja"]) == (0, "ja")
    assert language.choose_audio(untagged, "en", ["ja", "en"]) == (0, None)
    assert language.choose_audio(untagged, "en", None) == (0, None)
    two = untagged + [{"index": 1, "language": None, "title": "", "default": False}]
    assert language.choose_audio(two, "en", ["ja"]) == (0, None)


def test_detection_votes_across_windows_weighted_by_speech():
    # the cold open is music (barely counts), the rest is spanish
    lang, share = language.aggregate_detection(
        [("en", 0.9, 0.05), ("es", 0.8, 0.9), ("es", 0.95, 0.8), ("es", 0.7, 0.6)])
    assert lang == "es" and share > 0.9
    assert language.aggregate_detection([]) == (None, 0.0)


def test_detection_windows_spread_over_the_runtime_and_skip_the_edges():
    w = language.detection_windows(5400)
    assert len(w) == 5 and w[0] >= 5400 * 0.08 and w[-1] + 30 <= 5400 * 0.92 + 1
    assert language.detection_windows(20) == [0.0]


def test_plan_task():
    assert language.plan_task("en", "en") == "transcribe"
    assert language.plan_task("ja", "en") == "translate"
    assert language.plan_task("ja", "fr") is None
    assert language.plan_task(None, "en") is None
    assert language.plan_task("pt", "pt-br") == "transcribe"


# ── naming: plex/jellyfin/emby/kodi read the language last ─────────────────

def test_ai_sidecar_name_keeps_the_language_last():
    assert naming.ai_srt_name("/m/Movie (2020).mkv", "en") == "Movie (2020).ai.en.srt"
    assert naming.ai_srt_name("/m/Movie.mkv", "pt-br", hi=True) == "Movie.ai.pt-br.hi.srt"


def test_our_parsers_read_the_ai_name_as_its_language():
    from core.player import tracks as K
    from core.video.subtitles import existing
    assert existing.parse_sidecar_tokens(["ai", "en"]) == ("en", False, False)
    t = existing.external_tracks("/m/Movie.mkv", names=["Movie.mkv", "Movie.ai.en.srt", "Movie.en.srt"],
                                 read_text=lambda p: None)
    assert sorted((x.language, x.ai) for x in t) == [("en", False), ("en", True)]
    assert K._LANG_IN_NAME.search(".ai.en").group(1) == "en"


def test_player_sidecars_flag_ai(tmp_path):
    from core.player import tracks as K
    video = tmp_path / "Show S01E01.mkv"
    video.write_bytes(b"x")
    for n in ("Show S01E01.ai.en.srt", "Show S01E01.es.srt", "Show S01E01.hi.srt"):
        (tmp_path / n).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    subs = {s["path"].rsplit(os.sep, 1)[-1]: s for s in K.sidecars(str(video))}
    assert subs["Show S01E01.ai.en.srt"]["ai"] is True
    assert subs["Show S01E01.ai.en.srt"]["language"] == "en"
    assert subs["Show S01E01.es.srt"]["ai"] is False
    # movie.hi.srt is hindi, not hearing impaired
    assert subs["Show S01E01.hi.srt"]["language"] == "hi" and not subs["Show S01E01.hi.srt"]["sdh"]


def test_ai_sidecars_travel_with_a_rename(tmp_path):
    from core.video.file_ops import move_sidecars
    old = tmp_path / "Old.mkv"
    old.write_bytes(b"x")
    (tmp_path / "Old.ai.en.srt").write_text("x")
    new = tmp_path / "New.mkv"
    assert move_sidecars(str(old), str(new)) == 1
    assert (tmp_path / "New.ai.en.srt").exists()


def test_retire_ai_sidecars_only_takes_that_language(tmp_path):
    video = tmp_path / "Ai.mkv"  # a film called "Ai" stays a human subtitle
    video.write_bytes(b"x")
    for n in ("Ai.ai.en.srt", "Ai.ai.es.srt", "Ai.en.srt"):
        (tmp_path / n).write_text("x")
    gone = []
    assert naming.retire_ai_sidecars(str(video), "en", discard=gone.append) == 1
    assert [os.path.basename(p) for p in gone] == ["Ai.ai.en.srt"]
    assert naming.retire_ai_sidecars(str(video), "es") == 1
    assert not (tmp_path / "Ai.ai.es.srt").exists() and (tmp_path / "Ai.en.srt").exists()


# ── ai loses to any human subtitle ─────────────────────────────────────────

from tests.test_subtitle_upgrade import (  # noqa: E402
    FakeFS, ScriptedProvider, _cand, _dl, _patch, _settings, _video_file,
)


def test_upgrade_baseline_is_zero_for_ai(db, tmp_path):
    from core.video.subtitles import worker
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    row = {"video_kind": "download", "video_id": dl_id, "language": "en"}
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded", provider="whisper", score=100.0)
    assert worker._upgrade_baseline(db, row, dest, os.listdir(tmp_path)) == 0.0
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded", provider="scripted", score=60.0)
    assert worker._upgrade_baseline(db, row, dest, os.listdir(tmp_path)) == 60.0
    # an old-style row with no history but an ai sidecar on disk: still 0
    dl2 = _dl(db, dest)
    stem = os.path.splitext(dest)[0]
    open(stem + ".ai.en.srt", "w").write("x")
    row2 = {"video_kind": "download", "video_id": dl2, "language": "en"}
    assert worker._upgrade_baseline(db, row2, dest, os.listdir(tmp_path)) == 0.0


def test_any_human_sub_that_clears_the_gate_replaces_ai(db, tmp_path, monkeypatch):
    from core.video.subtitles import worker
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    ai_file = os.path.splitext(dest)[0] + ".ai.en.srt"
    open(ai_file, "w").write("1\n00:00:01,000 --> 00:00:02,000\nmachine\n")
    db.subtitle_want("download", dl_id, "en")
    db.subtitle_mark("download", dl_id, "en", "have")
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded", provider="whisper", score=0.0)
    # a weak human sub (30: clears the 24 gate, would never beat a 100 hash match)
    prov = ScriptedProvider([_cand("human")], texts={"human": "1\n00:00:01,000 --> 00:00:02,000\nhuman\n"})
    _patch(monkeypatch, prov, {"human": 30.0})
    monkeypatch.setattr("core.video.subtitles.ai.naming.default_discarder", lambda db: None)
    fs = FakeFS()
    stats = worker.run_upgrade_cycle(db, _settings(), fs=fs)
    assert stats["upgraded"] == 1
    assert any(p.endswith(".en.srt") and ".ai." not in p for p in fs.texts)
    assert not os.path.exists(ai_file)


def test_below_gate_human_does_not_replace_ai(db, tmp_path, monkeypatch):
    from core.video.subtitles import worker
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    ai_file = os.path.splitext(dest)[0] + ".ai.en.srt"
    open(ai_file, "w").write("x")
    db.subtitle_want("download", dl_id, "en")
    db.subtitle_mark("download", dl_id, "en", "have")
    db.subtitle_log_fetch("download", dl_id, "en", "downloaded", provider="whisper", score=0.0)
    prov = ScriptedProvider([_cand("junk")], texts={"junk": "x"})
    _patch(monkeypatch, prov, {"junk": 10.0})
    stats = worker.run_upgrade_cycle(db, _settings(), fs=FakeFS())
    assert stats["upgraded"] == 0 and os.path.exists(ai_file)


def test_retry_pass_never_runs_whisper_inline(db, tmp_path, monkeypatch):
    from core.video.subtitles import worker
    from core.video.subtitles.providers import whisper as W
    assert W.WhisperProvider().search(object()) == []
    called = {}
    import core.video.subtitles.providers as pkg
    real = pkg.fetch_subtitle_detailed

    def spy(*a, **k):
        called.update(k)
        return real(*a, **k)
    monkeypatch.setattr(pkg, "fetch_subtitle_detailed", spy)
    dest = _video_file(tmp_path)
    dl_id = _dl(db, dest)
    db.subtitle_want("download", dl_id, "en")
    prov = ScriptedProvider([])
    _patch(monkeypatch, prov, {})
    worker.run_cycle(db, _settings(), fs=FakeFS())
    assert called.get("skip_background") is True


def test_a_miss_queues_ai_only_after_the_wait(db, tmp_path, monkeypatch):
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    row = {"video_kind": "movie", "video_id": 7, "language": "en", "forced": 0,
           "created_at": "2099-01-01 00:00:00"}
    on = {"whisper_enabled": True, "ai_subs_wait_hours": 24}
    assert aiq.queue_after_miss(db, on, row) is False                    # too fresh
    old = dict(row, created_at="2020-01-01 00:00:00")
    assert aiq.queue_after_miss(db, {"whisper_enabled": False}, old) is False  # switched off
    assert aiq.queue_after_miss(db, on, dict(old, forced=1)) is False    # never forced
    assert aiq.queue_after_miss(db, on, old) is True
    assert aiq.queue_after_miss(db, on, old) is False                    # already waiting
    jobs = db.ai_job_list()
    assert len(jobs) == 1 and jobs[0]["source"] == "auto" and jobs[0]["priority"] == aiq.PRIORITY["auto"]


# ── the queue ──────────────────────────────────────────────────────────────

def test_enqueue_merges_and_raises_priority(db, monkeypatch):
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    a = aiq.enqueue(db, "movie", 1, "en", origin="auto")
    b = aiq.enqueue(db, "movie", 1, "en", origin="player", profile_id=3)
    assert a["id"] == b["id"] and b["priority"] == 100 and b["source"] == "on_demand"
    c = aiq.enqueue(db, "movie", 1, "en", origin="library")   # less urgent: no change
    assert c["priority"] == 100
    with pytest.raises(ValueError):
        aiq.enqueue(db, "movie", 1, "../x")
    monkeypatch.setattr(aiq, "availability", lambda s: "No model.")
    with pytest.raises(aiq.NotAvailable):
        aiq.enqueue(db, "movie", 2, "en")


def test_queue_order_and_position(db, monkeypatch):
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    lib = aiq.enqueue(db, "episode", 1, "en", origin="library")
    auto = aiq.enqueue(db, "episode", 2, "en", origin="auto")
    player = aiq.enqueue(db, "episode", 3, "en", origin="player")
    detail = aiq.enqueue(db, "episode", 4, "en", origin="detail")
    assert db.ai_job_next()["id"] == player["id"]
    assert [db.ai_job_position(j["id"]) for j in (player, detail, lib, auto)] == [1, 2, 3, 4]
    # someone is watching: only on-demand jobs may run
    assert db.ai_job_next(background_ok=False)["id"] == player["id"]
    db.ai_job_cancel(player["id"])
    db.ai_job_cancel(detail["id"])
    assert db.ai_job_next(background_ok=False) is None
    assert db.ai_job_position(player["id"]) is None


def test_restart_requeues_a_running_job(db, monkeypatch):
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    j = aiq.enqueue(db, "movie", 1, "en")
    db.ai_job_update(j["id"], state="running")
    assert db.ai_job_requeue_running() == 1
    assert db.ai_job_get(j["id"])["state"] == "queued"


def _movie_with_file(db, tmp_path, monkeypatch, name="Film.mkv"):
    video = tmp_path / name
    video.write_bytes(b"x" * 1000)
    monkeypatch.setattr("core.video.subtitles.worker._resolve_file",
                        lambda db, row: (str(video), {}))
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    monkeypatch.setattr(aiq, "pick_model", lambda s: "tiny")
    monkeypatch.setattr("core.player.transcode.ffmpeg_path", lambda: "/bin/ffmpeg")
    return video


SRT = "1\n00:00:01,000 --> 00:00:02,000\nhello\n"


def test_run_job_writes_the_ai_file_marks_have_and_logs_score_zero(db, tmp_path, monkeypatch):
    video = _movie_with_file(db, tmp_path, monkeypatch)
    db.subtitle_want("movie", 5, "en")
    db.subtitle_mark("movie", 5, "en", "failed")
    job = aiq.enqueue(db, "movie", 5, "en", origin="auto")
    seen = {}

    def runner(job, **kw):
        seen.update(kw)
        return {"status": "done", "srt": SRT, "language": "en", "task": "transcribe",
                "confidence": 0.81, "verdict": "ok", "device": "cpu"}
    row = aiq.run_job(db, job, watching=lambda: False, runner=runner)
    assert row["state"] == "done" and row["confidence"] == 0.81
    out = tmp_path / "Film.ai.en.srt"
    assert out.read_text() == SRT and row["output_path"] == str(out)
    assert seen["want"] == "en" and seen["video"] == str(video)
    assert db.subtitle_get_for_video("movie", 5)[0]["status"] == "have"
    latest = db.subtitle_latest_download("movie", 5, "en")
    assert latest == {"score": 0.0, "provider": "whisper"}
    assert db.ai_job_for_output(str(out))["id"] == job["id"]


def test_an_automatic_job_steps_aside_for_a_human_sub(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path, monkeypatch)
    (tmp_path / "Film.en.srt").write_text(SRT)
    job = aiq.enqueue(db, "movie", 5, "en", origin="auto")
    row = aiq.run_job(db, job, runner=lambda job, **kw: pytest.fail("must not transcribe"))
    assert row["state"] == "done" and not (tmp_path / "Film.ai.en.srt").exists()
    # someone who asked still gets theirs
    job2 = aiq.enqueue(db, "movie", 5, "en", origin="player")
    row2 = aiq.run_job(db, job2, runner=lambda job, **kw: {"status": "done", "srt": SRT, "confidence": 0.9})
    assert row2["state"] == "done" and (tmp_path / "Film.ai.en.srt").exists()


@pytest.mark.parametrize("raised,state", [(engine.Cancelled, "cancelled"), (engine.Paused, "paused"),
                                          (RuntimeError, "failed")])
def test_run_job_end_states(db, tmp_path, monkeypatch, raised, state):
    _movie_with_file(db, tmp_path, monkeypatch)
    job = aiq.enqueue(db, "movie", 5, "en", origin="player")

    def runner(job, **kw):
        raise raised("x") if raised is RuntimeError else raised()
    row = aiq.run_job(db, job, runner=runner)
    assert row["state"] == state
    if state == "paused":
        assert db.ai_job_next()["id"] == job["id"]   # waits its turn again
    else:
        assert db.ai_job_next() is None


def test_run_job_no_match_and_discard_write_nothing(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path, monkeypatch)
    for status in ("no_match", "discarded"):
        job = aiq.enqueue(db, "movie", 5, "fr", origin="player")
        row = aiq.run_job(db, job, runner=lambda job, **kw: {"status": status, "why": "because"})
        assert row["state"] == status and row["error"] == "because"
    assert not list(tmp_path.glob("*.ai.*"))


def test_cancel_reaches_a_running_job(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path, monkeypatch)
    job = aiq.enqueue(db, "movie", 5, "en", origin="player")

    def runner(job, control=None, **kw):
        db.ai_job_cancel(job["id"])
        control._checked = 0.0
        assert control.cancelled()
        raise engine.Cancelled()
    assert aiq.run_job(db, job, runner=runner)["state"] == "cancelled"


def test_pause_while_watching_holds_background_cpu_jobs_only(db, tmp_path, monkeypatch):
    _movie_with_file(db, tmp_path, monkeypatch)
    monkeypatch.setattr("core.video.subtitles.ai.hardware.detect", lambda refresh=False: {"cuda": False})
    bg = aiq.enqueue(db, "movie", 5, "en", origin="library")
    assert aiq.run_once(db, watching=lambda: True, runner=lambda *a, **k: pytest.fail("ran")) is None
    controls = []

    def runner(job, control=None, **kw):
        controls.append(control)
        return {"status": "done", "srt": SRT, "confidence": 0.9}
    mine = aiq.enqueue(db, "movie", 6, "en", origin="player")
    assert aiq.run_once(db, watching=lambda: True, runner=runner)["id"] == mine["id"]
    assert controls[0].paused() is False     # someone's own ask never pauses
    row = aiq.run_once(db, watching=lambda: False, runner=runner)
    assert row["id"] == bg["id"]
    c = aiq._JobControl(db, dict(bg), "cpu", True, lambda: True)
    assert c.paused() is True
    assert aiq._JobControl(db, dict(bg), "cuda", True, lambda: True).paused() is False


def test_engine_resumes_from_its_checkpoint(tmp_path, monkeypatch):
    """a restart mid-film starts at the checkpoint, not at 0."""
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(tmp_path / "v.db"))
    import wave
    wav = engine.work_dir()
    wav.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav / "9.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 16000 * 700)    # 700 s
    engine.save_checkpoint(wav / "9.json", {"audio_index": 0, "spoken": "en", "offset": 600.0,
                                            "segments": [_seg(1, 2, "earlier words")],
                                            "speech_seconds": 100.0})
    asked = []

    class FakeInfo:
        duration_after_vad = 50.0

    class Seg:
        def __init__(self, s, e, t):
            self.start, self.end, self.text = s, e, t
            self.avg_logprob, self.no_speech_prob, self.compression_ratio, self.words = -0.2, 0.0, 1.2, []

    class FakeModel:
        def transcribe(self, audio, **kw):
            asked.append(len(audio) / 16000.0)
            return iter([Seg(10, 12, "the last words here")]), FakeInfo()

    res = engine.run({"id": 9}, video="x", model_dir="m", ffmpeg="f", ffprobe="p", want="en",
                     model_loader=lambda *a: (FakeModel(), "cpu"))
    assert asked == [100.0]                  # only the last 100 s were transcribed
    assert res["status"] == "done" and "earlier words" in res["srt"] and "the last words here" in res["srt"]
    engine.cleanup(9)


# ── the player's subtitle cache and the saved delay ────────────────────────

def test_sidecar_cache_key_changes_when_the_file_is_rewritten(tmp_path):
    from core.player import tracks as K
    p = tmp_path / "M.en.srt"
    p.write_text(SRT)
    k1 = K.sidecar_key(str(p))
    p.write_text(SRT + "\n2\n00:00:03,000 --> 00:00:04,000\nmore\n")
    k2 = K.sidecar_key(str(p))
    assert k1 != k2 and k1[1] == k2[1]


def test_to_vtt_serves_the_new_file_after_a_resync(tmp_path, monkeypatch):
    from core.player import tracks as K
    calls = []

    class Res:
        returncode = 0
        stderr = b""

        def __init__(self, text):
            self.stdout = text.encode()

    def fake_run(cmd, **kw):
        src = cmd[cmd.index("-i") + 1]
        calls.append(src)
        return Res("WEBVTT\n\n" + open(src).read())
    monkeypatch.setattr(K.subprocess, "run", fake_run)
    p = tmp_path / "M.en.srt"
    p.write_text("first")
    a = K.to_vtt("ffmpeg", str(p), None, K.sidecar_key(str(p)), out_dir=tmp_path / "c")
    assert "first" in a
    assert K.to_vtt("ffmpeg", str(p), None, K.sidecar_key(str(p)), out_dir=tmp_path / "c") == a
    p.write_text("second, resynced")
    os.utime(p, None)
    b = K.to_vtt("ffmpeg", str(p), None, K.sidecar_key(str(p)), out_dir=tmp_path / "c")
    assert "second" in b and len(calls) == 2


def test_subtitle_delay_is_per_profile_per_file_and_resets_on_a_new_file(db):
    assert db.subtitle_delay_get(1, "/m/F.mkv", "F.en.srt") == 0
    assert db.subtitle_delay_set(1, "/m/F.mkv", "F.en.srt", 1200, "10:1") == 1200
    assert db.subtitle_delay_get(1, "/m/F.mkv", "F.en.srt", "10:1") == 1200
    assert db.subtitle_delay_get(2, "/m/F.mkv", "F.en.srt", "10:1") == 0          # another profile
    assert db.subtitle_delay_get(1, "/m/F.mkv", "F.es.srt") == 0                  # another subtitle
    assert db.subtitle_delay_get(1, "/m/F.mkv", "F.en.srt", "11:2") == 0          # the file changed
    assert db.subtitle_delay_set(1, "/m/F.mkv", "s0", -99999) == -30000           # clamped
    assert db.subtitle_delays_for(1, "/m/F.mkv") == {"F.en.srt": (1200, "10:1"), "s0": (-30000, "")}
    db.subtitle_delay_set(1, "/m/F.mkv", "F.en.srt", 0)                           # 0 clears
    assert "F.en.srt" not in db.subtitle_delays_for(1, "/m/F.mkv")


def test_tracks_answer_carries_delay_and_ai_flags(db, tmp_path, monkeypatch):
    import api.player_tracks as PT
    import api.video as V
    video = tmp_path / "F.mkv"
    video.write_bytes(b"x")
    ai = tmp_path / "F.ai.en.srt"
    ai.write_text(SRT)
    monkeypatch.setattr(V, "get_video_db", lambda: db)
    monkeypatch.setattr(PT, "_profile", lambda: 4)

    class Src:
        mode, path, url, extra = "file", str(video), None, {}
    from core.player import tracks as K
    subs = K.sidecars(str(video))
    db.subtitle_delay_set(4, str(video), "F.ai.en.srt", -700, K.sidecar_sig(str(ai)))
    job = db.ai_job_enqueue("movie", 1, "en")
    db.ai_job_update(job["id"], state="done", output_path=str(ai), confidence=0.4)
    PT._decorate(Src, subs)
    assert subs[0]["delay_ms"] == -700 and subs[0]["ai"] is True and subs[0]["low_confidence"] is True


# ── settings + models ──────────────────────────────────────────────────────

def test_model_lineup_and_settings_normalize():
    from core.video import organization
    from core.video.subtitles.providers import whisper as W
    for name in ("tiny", "base", "small", "medium", "large-v3-turbo", "distil-large-v3"):
        assert W.valid_model_name(name) == name
    d = organization.normalize({"whisper_model": "large-v3-turbo", "whisper_device": "cuda",
                                "ai_subs_wait_hours": 9999, "ai_subs_threads": -3,
                                "ai_subs_pause_while_watching": False})
    assert d["whisper_model"] == "large-v3-turbo" and d["whisper_device"] == "cuda"
    assert d["ai_subs_wait_hours"] == 720 and d["ai_subs_threads"] == 0
    assert d["ai_subs_pause_while_watching"] is False
    assert organization.normalize({"whisper_device": "tpu"})["whisper_device"] == "auto"


def test_several_models_stay_installed_and_delete_one(tmp_path):
    from core.video.subtitles.providers import whisper as W
    W.set_models_dir_override(tmp_path)
    try:
        for n in ("tiny", "small"):
            (tmp_path / n).mkdir()
            (tmp_path / n / "model.bin").write_bytes(b"x" * 10)
        assert W.installed_models() == ["tiny", "small"]
        assert W.model_disk_bytes("small") == 10
        assert W.delete_model("tiny") is True
        assert W.installed_models() == ["small"]
        with pytest.raises(ValueError):
            W.delete_model("../etc")
    finally:
        W.set_models_dir_override(None)


def test_hardware_recommendation():
    from core.video.subtitles.ai import hardware as H
    assert H.recommend({"cuda": True, "cores": 4}) == "large-v3-turbo"
    assert H.recommend({"cores": 4, "arch": "aarch64", "memory_bytes": 8e9}) == "base"
    assert H.recommend({"cores": 4, "arch": "x86_64", "memory_bytes": 8e9}) == "small"
    assert H.recommend({"cores": 16, "arch": "x86_64", "memory_bytes": 32e9}) == "medium"
    # a measured quick test wins: the biggest model at 3x real time or better
    assert H.recommend({"cores": 4}, {"small": {"speed": 4.1}, "medium": {"speed": 1.2}}) == "small"
    assert H.estimate_minutes(2700, 3.0) == 10.5
    assert "CUDA" in H.summary({"cuda": True, "gpu": "RTX 3060"})


# ── a real whisper run (skips without faster-whisper, ffmpeg or a model) ───

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ai_subtitles", "speech.wav")


def _ffmpeg():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "tools", "ffmpeg"), shutil.which("ffmpeg") or ""):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def test_real_faster_whisper_on_a_generated_clip(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    model_dir = os.environ.get("SOULSYNC_TEST_WHISPER_MODEL")
    if not model_dir or not os.path.isfile(os.path.join(model_dir, "model.bin")):
        pytest.skip("set SOULSYNC_TEST_WHISPER_MODEL to a faster-whisper tiny model folder")
    ff = _ffmpeg()
    if not ff:
        pytest.skip("no ffmpeg")
    import subprocess
    clip = tmp_path / "clip.mkv"
    # 4 s of silence, the spoken fixture, 4 s of a tone: vad must keep only the speech
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "aevalsrc=0:d=4:s=16000", "-i", FIXTURE,
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=16000",
                    "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]", "-map", "[a]",
                    "-c:a", "aac", "-metadata:s:a:0", "language=eng", str(clip)], check=True, timeout=120)
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(tmp_path / "v.db"))
    probe = os.path.join(os.path.dirname(ff), os.path.basename(ff).replace("ffmpeg", "ffprobe"))
    res = engine.run({"id": "real"}, video=str(clip), model_dir=model_dir, ffmpeg=ff,
                     ffprobe=probe if os.path.isfile(probe) else "ffprobe", want="en")
    engine.cleanup("real")
    assert res["status"] == "done", res
    text = res["srt"].lower()
    assert "weather" in text or "beach" in text
    first = res["srt"].split("\n")[1]
    assert first >= "00:00:03,500"          # nothing made up over the leading silence
    assert "thank" not in text


# ── the api ────────────────────────────────────────────────────────────────

from tests.test_subtitle_api import _db, _movie, client, client_non_admin  # noqa: E402,F401


def test_api_enqueue_list_and_cancel(client, monkeypatch):
    monkeypatch.setattr(aiq, "availability", lambda s: None)
    _movie(_db(), 42)
    r = client.post("/api/video/subtitles/ai/jobs",
                    json={"kind": "movie", "item_id": 42, "lang": "en", "origin": "player"})
    assert r.status_code == 202
    job = r.get_json()["job"]
    assert job["state"] == "queued" and job["priority"] == 100 and job["position"] == 1
    assert "output_path" not in job
    listed = client.get("/api/video/subtitles/ai/jobs?kind=movie&item_id=42&active=1").get_json()
    assert [j["id"] for j in listed["jobs"]] == [job["id"]] and listed["available"] is True
    assert client.post("/api/video/subtitles/ai/jobs/%d/cancel" % job["id"]).get_json() == {"ok": True}
    assert client.get("/api/video/subtitles/ai/jobs?active=1").get_json()["jobs"] == []


def test_api_refuses_in_plain_words(client, monkeypatch):
    _movie(_db(), 42)
    monkeypatch.setattr(aiq, "availability", lambda s: "No AI subtitle model is installed yet.")
    r = client.post("/api/video/subtitles/ai/jobs", json={"kind": "movie", "item_id": 42, "lang": "en"})
    assert r.status_code == 503 and r.get_json()["message"] == "No AI subtitle model is installed yet."
    assert client.post("/api/video/subtitles/ai/jobs",
                       json={"kind": "show", "item_id": 42, "lang": "en"}).status_code == 400
    assert client.post("/api/video/subtitles/ai/jobs",
                       json={"kind": "movie", "item_id": 999, "lang": "en"}).status_code == 404


def test_api_model_delete_and_cancel_download(client, tmp_path):
    from core.video.subtitles.providers import whisper as W
    W.set_models_dir_override(tmp_path / "models")
    try:
        (tmp_path / "models" / "tiny").mkdir(parents=True)
        (tmp_path / "models" / "tiny" / "model.bin").write_bytes(b"x")
        assert client.delete("/api/video/subtitles/whisper/models/../x").status_code in (400, 404)
        assert client.delete("/api/video/subtitles/whisper/models/tiny").get_json()["removed"] is True
        assert W.installed_models() == []
        assert client.post("/api/video/subtitles/whisper/download/cancel").get_json() == {
            "ok": True, "cancelled": False}
        st = client.get("/api/video/subtitles/whisper/status").get_json()
        assert set(st["models"]) >= {"tiny", "large-v3-turbo"} and st["installed"] == []
        assert "summary" in st["hardware"] and st["recommended"] in st["models"]
    finally:
        W.set_models_dir_override(None)


def test_route_access_for_the_new_routes():
    from api.video.access import route_access
    assert route_access("/api/video/subtitles/ai/jobs", "POST") == ("perm", ("movies.get", "tv.get"))
    assert route_access("/api/video/subtitles/ai/jobs/3/cancel", "POST") == ("perm", ("movies.get", "tv.get"))
    assert route_access("/api/video/subtitles/ai/bench", "POST") == ("admin", None)
    assert route_access("/api/video/subtitles/whisper/models/tiny", "DELETE") == ("admin", None)
    assert route_access("/api/video/subtitles/whisper/download/cancel", "POST") == ("admin", None)
    assert route_access("/api/video/subtitles/ai/jobs", "GET")[0] == "perm"
