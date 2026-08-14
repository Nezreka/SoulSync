"""Movie night P2 — the room you actually enter.

Boulder: "when i click the movie night button it only seems to make me search for an
item, never makes me enter the movie night room. like i would enter the jukebox room."
Exactly right, and not a bug: phase 1 shipped the ballot (nominate → vote → start →
ownership probe → grab) and phase 2, the screen itself, was never built. This is that.

Four decisions are load-bearing and each is pinned below, because each one is invisible
until it is wrong:

1. **The screen lives outside the card.** ``renderWatch`` rebuilds ``[data-chat-watch]``
   innerHTML on every room event — several times a minute in a busy room. A ``<video>``
   inside it would be destroyed and re-created that often, restarting the film each
   time. The stage gets its own host that only changes when the showing does.
2. **Playback is opt-in.** Nothing plays until "Join party" — a real gesture, exactly
   like the jukebox's Tune in. A chat page that starts blasting a film because someone
   else pressed ▶ is a hostile page.
3. **Party XOR jukebox.** Two audio sources competing for the same ears is not a
   feature; joining a showing tunes you out of the jukebox.
4. **Position is derived, never published.** The party clock is a fold over the bus
   (started-at + pause/resume). The local element is corrected TOWARD it — a viewer
   scrubbing their own copy must never move the room.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """ONE function's body, bounded by the next top-level `function` in this
    IIFE — not a fixed character window. A window overruns into whatever
    follows, which turns "this function must not call X" into "nothing within
    2600 characters may call X" and fails the moment a neighbour grows."""
    start = _JS.index("function " + name + "(")
    nxt = _JS.find("\n    function ", start + 1)
    return _JS[start:nxt if nxt != -1 else len(_JS)]


# ── 1. the screen survives a re-render ───────────────────────────────────────

def test_the_stage_host_lives_outside_the_re_rendered_card():
    watch = _HTML.index("data-chat-watch hidden")
    stage = _HTML.index("data-chat-watch-stage")
    assert stage > watch, "the stage must be a SIBLING of the card, not inside it"
    # and the card's own renderer must not be what writes the video element
    render = _fn("renderWatch")
    assert "<video" not in render, (
        "a <video> built into renderWatch's innerHTML is destroyed on every room "
        "event — the film would restart several times a minute")


def test_the_stage_is_only_rebuilt_when_the_showing_changes():
    mount = _fn("_watchMountStage")
    assert "data-mounted" in mount, "the mount must be idempotent per showing"
    assert "if (host.getAttribute('data-mounted') === want)" in mount


def test_a_new_showing_supersedes_the_one_you_joined():
    render = _fn("renderWatch")
    assert "st.now.key !== state.watch.joined" in render
    assert "_watchTeardown()" in render


def test_leaving_the_room_takes_the_screen_down():
    """Both exits: the party ending, and navigating to another room."""
    assert "_watchTeardown();            // and the party you joined belongs to the old room" in _JS
    render = _fn("renderWatch")
    assert "if (state.watch.joined) _watchTeardown();" in render


# ── 2. opt-in ────────────────────────────────────────────────────────────────

def test_nothing_plays_without_a_join_gesture():
    stage = _fn("_watchMountStage")
    assert "autoplay" not in stage, "an autoplaying party is a hostile page"
    join = _fn("_watchJoinChip")
    assert "data-chat-watch-join" in join and "Join party" in join


def test_the_join_button_only_appears_when_this_box_has_the_file():
    join = _fn("_watchJoinChip")
    assert "state.watch.owned[now.key] !== true" in join, (
        "offering Join for a file you don't have would be a dead button")
    assert "ownedDenied" in join, "a music-only profile has no ownership UI at all"


def test_leaving_is_possible_and_does_not_end_the_party_for_others():
    chip = _fn("_watchJoinChip")
    assert "data-chat-watch-leave" in chip
    assert "the party carries on without you" in chip
    # Leave is local teardown only — it must not send an end carrier. Slice to
    # exactly this handler's own line, or the assertion reads the NEXT one.
    tail = _JS[_JS.index("data-chat-watch-leave]"):]
    handler = tail[:tail.index("return; }") + 9]
    assert "_watchTeardown" in handler
    assert "sendProtocol" not in handler, (
        "leaving is a local decision — publishing watch.end would stop the film "
        "for everyone else in the room")


# ── 3. one pair of ears ──────────────────────────────────────────────────────

def test_joining_a_party_tunes_you_out_of_the_jukebox():
    join = _fn("_watchJoin")
    assert "state.jukebox.tunedIn" in join and "_jbxTuneOut()" in join


# ── 4. the clock is the room's, not the viewer's ─────────────────────────────

def test_the_element_is_corrected_toward_the_fold_never_the_reverse():
    sync = _fn("_watchSync")
    assert "CP.watchPosition(st.now" in sync, "position comes from the shared fold"
    assert "sendProtocol" not in sync, (
        "the local player must never publish — one viewer scrubbing would drag "
        "the whole room with them")


def test_drift_correction_has_slack():
    """Seeking on every tick would stutter; a second of drift in a film is
    invisible. The threshold is what makes this watchable."""
    sync = _fn("_watchSync")
    assert "> 2" in sync and "v.currentTime" in sync


def test_pause_and_resume_follow_the_party_state():
    sync = _fn("_watchSync")
    assert "st.now.paused" in sync and "v.pause()" in sync


def test_the_sync_loop_is_cleared_on_teardown():
    """An interval left running after the party ends would keep poking a
    detached element forever."""
    down = _fn("_watchTeardown")
    assert "clearInterval(state.watch.drift)" in down and "state.watch.drift = null" in down


# ── codec honesty reaches the user ───────────────────────────────────────────

def test_the_playability_verdict_is_shown_before_it_bites():
    warn = _fn("_watchStageWarn")
    assert "verdict === 'maybe'" in warn and "reasons" in warn


def test_a_browser_refusal_is_reported_not_left_as_a_black_rectangle():
    mount = _fn("_watchMountStage")
    assert "addEventListener('error'" in mount
    assert "state.watch.err" in mount


def test_the_probe_failing_does_not_block_playback():
    """The verdict is an assist. The browser is the final authority, so a failed
    probe must not stop the element from trying."""
    probe = _fn("_watchProbePlayable")
    assert ".catch(" in probe and "state.watch.playFetching = ''" in probe


# ── the stream URL names a title, never a path ───────────────────────────────

def test_the_stream_url_carries_only_the_title_identity():
    url = _fn("_watchStreamUrl")
    assert "kd=" in url and "id=" in url and "&s=" in url and "&e=" in url
    assert "path" not in url


def test_the_stage_is_styled():
    for cls in (".chat-watch-stagehost", ".chat-watch-video", ".chat-watch-stage-warn"):
        assert cls in _CSS, cls


# ── the flow fixes: start, join, and the silent gap ──────────────────────────
# Boulder: "i click ply button and i see a pause and end button and a join party
# button". Right — starting a showing and watching it were two separate gestures,
# which is nonsense for the person who just pressed ▶.

def test_pressing_play_also_joins_the_showing():
    """▶ IS the opt-in gesture. Requiring a second click to watch your own pick
    was the wrong reading of "playback is opt-in"."""
    handler = _JS[_JS.index("data-chat-watch-start]"):][:600]
    assert "state.watch.autoJoin" in handler
    assert "sendProtocol('watch.start'" in handler


def test_the_auto_join_waits_for_the_fold_not_a_local_guess():
    """The showing only exists once the reducer says so — joining before that
    would race the bus and mount a screen for a party that never started."""
    render = _fn("renderWatch")
    assert "st.now.key === state.watch.autoJoin" in render


def test_the_auto_join_does_not_fire_for_a_file_this_box_lacks():
    """Otherwise starting a showing you don't own mounts an empty player
    instead of leaving the Grab path visible."""
    render = _fn("renderWatch")
    seg = render[render.index("state.watch.autoJoin && st.now"):]
    assert "state.watch.owned[st.now.key] === true" in seg


def test_start_now_nominates_and_starts_in_one_gesture():
    """Alone in a room, nominate → vote → start is three gestures of ceremony
    to watch your own film. The ballot is untouched for real parties."""
    nom = _fn("_watchNominate")
    assert "state.watch.autoStart" in nom
    assert "data-chat-watch-now=" in _JS and "data-chat-watch-nowshow=" in _JS


def test_the_pending_start_is_confirmed_against_the_fold():
    """We never fire watch.start from a locally-guessed key — renderWatch waits
    for the nomination to actually appear in the reducer's output first."""
    render = _fn("renderWatch")
    seg = render[render.index("state.watch.autoStart"):]
    assert "st.noms.filter" in seg
    assert "state.watch.autoStart = ''" in seg, "the intent must be consumed, not repeated"


def test_start_now_is_checked_before_the_card_click():
    """The button lives INSIDE the nominate card; matching the card first would
    swallow it and merely nominate."""
    assert _JS.index("data-chat-watch-now]") < _JS.index("data-chat-watch-nom]")


def test_the_ownership_probe_gap_is_named():
    """An empty action row while the probe is out is indistinguishable from
    'this is broken' — and that gap is exactly when the user is looking hardest
    for the play button."""
    chip = _fn("_watchJoinChip")
    assert "in state.watch.owned" in chip
    assert "checking your library" in chip


def test_the_waiting_chip_is_styled():
    assert ".chat-watch-own--wait" in _CSS


# ── the squooshed card ───────────────────────────────────────────────────────
# Boulder: "all squooshed together and overlapping".

def test_the_nomination_controls_are_grouped_so_they_can_wrap():
    """As bare flex siblings they shrank below their own text and collided.
    Grouping them is what lets them drop to a second line instead."""
    render = _fn("renderWatch")
    assert "chat-watch-row-acts" in render


def test_the_row_is_a_grid_with_the_controls_on_their_own_line():
    assert "grid-template-areas: \"thumb main\" \"acts acts\"" in _CSS


def test_controls_keep_their_intrinsic_width():
    """flex: 0 0 auto is the actual fix for the overlap — without it they
    compress below their content no matter what the container does."""
    assert ".chat-watch-row-acts > * { flex: 0 0 auto; }" in _CSS
    assert ".chat-watch-now-acts > * { flex: 0 0 auto; }" in _CSS


# ── the silent picture ───────────────────────────────────────────────────────
# Boulder played Austin Powers (1997) — h264 + AC3 in his library — and got a
# picture with no sound and no explanation. The verdict was computed correctly
# and then suppressed: only 'maybe' rendered, so 'no', the case that actually
# matters, said nothing.

def test_a_no_verdict_is_shown_not_swallowed():
    warn = _fn("_watchStageWarn")
    assert "p.verdict === 'no'" in warn, (
        "suppressing 'no' hides the one verdict that means it genuinely will "
        "not work — a verdict nobody sees is not a verdict")


def test_a_hard_verdict_reads_differently_from_a_soft_one():
    warn = _fn("_watchStageWarn")
    assert "chat-watch-stage-warn--hard" in warn
    assert ".chat-watch-stage-warn--hard" in _CSS


def test_the_verdict_is_probed_before_joining_not_after():
    """Finding out your copy is AC3 after you have joined is finding out too
    late — the Join button itself carries the answer."""
    chip = _fn("_watchJoinChip")
    assert "state.watch.playAhead" in chip
    # NB the apostrophe is backslash-escaped in the JS string literal, so match
    # around it rather than on the rendered text.
    assert "Join anyway" in chip and "play here" in chip


def test_the_pre_join_probe_cannot_refire_on_every_render():
    """renderWatch runs on every room event; an unguarded probe would be a
    request storm."""
    probe = _fn("_watchProbePlayable")
    assert "if (sig in state.watch.playAhead) return;" in probe
    assert "state.watch.playAhead[sig] = null;" in probe


# ── volume ───────────────────────────────────────────────────────────────────

def test_the_stage_has_its_own_volume_like_the_jukebox():
    mount = _fn("_watchMountStage")
    assert "data-chat-watch-vol" in mount
    assert "v.volume =" in mount, "the element must honour the stored level on mount"


def test_volume_is_local_and_persisted_and_never_published():
    handler = _JS[_JS.index("[data-chat-watch-vol]"):][:700]
    assert "localStorage.setItem('chat_watch_vol'" in handler
    assert "sendProtocol" not in handler, "one viewer's volume is not the room's"


def test_the_stored_volume_is_restored_at_startup():
    assert "localStorage.getItem('chat_watch_vol')" in _JS


# ── the layout the screenshots showed ────────────────────────────────────────

def test_the_header_cannot_wrap_or_clip():
    """'Movie night' was breaking across two lines and + Nominate was being cut
    off the panel edge."""
    assert ".chat-watch-headrow > b { white-space: nowrap; flex: 0 0 auto; }" in _CSS
    assert ".chat-watch-nombtn { white-space: nowrap; flex: 0 0 auto;" in _CSS


def test_the_showing_controls_are_compact_enough_to_share_a_row():
    """Pause / End / own-chip / Leave each took a whole line at full button
    size in a rail this narrow."""
    assert ".chat-watch-now-acts .chat-arc-btn" in _CSS
    assert "font-size: 11px" in _CSS.split(".chat-watch-now-acts .chat-arc-btn")[1][:200]
