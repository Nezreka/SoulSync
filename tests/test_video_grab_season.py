"""Grab season asks for a PACK first, and asks every source.

What it did before: collected the season's missing episodes and fired one ordinary
auto-grab per episode — 22 searches for a 22-episode season — against
``(st.sources||[]).filter(...)[0]``, the FIRST configured source only. A show that
Soulseek had and Prowlarr didn't came back as unavailable without Soulseek ever
being asked.

And a season pack from a torrent indexer rendered as a plain release row, because
the pack card was gated on ``r.files.length > 1`` and only slskd returns a file
list. You could grab it; nothing told you it was a whole season, and (before
917e89e5f) nothing imported it either.

The regex is the part worth guarding. A torrent has only its NAME to go on, and
the naive form of this test — /S\\d/ — also matches the '5' in an audio tag like
``DTS5.1``, which files a MOVIE as a season pack and sends it down the folder
mapper. Those cases are pinned below by name.

The JS is exercised through node where it is available, and asserted structurally
otherwise, so the contract holds on machines without it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SRC = Path("webui/static/video/video-download-view.js")


def _js() -> str:
    return _SRC.read_text(encoding="utf-8")


def _node():
    for candidate in ("node", "/mnt/c/Program Files/nodejs/node.exe"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


# ── the classifier ───────────────────────────────────────────────────────────

_PACKS = [
    "Some.Show.S03.1080p.WEB-DL-GROUP",
    "Some.Show.Season.2.COMPLETE.720p",
    "Show S3 Complete 1080p",
    "[Group] Some.Show.S01.1080p",
]
_SINGLES = [
    "Some.Show.S03E07.1080p.WEB-DL",
    "Some.Show.S03E07E08.1080p",          # multi-episode file, still not a pack
    "The.Daily.Thing.2026.07.08.1080p",   # a dated daily
    "Some.Movie.2019.1080p.BluRay",
    "Some.Movie.2019.1080p.DTS5.1.x264",  # the audio-tag trap
    "Movie.2020.AAC5.1.H264",
    "Movie.1080p.HDTS.x264",
    "S.W.A.T.2017.S02E05.1080p",
]


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_classifier_agrees_on_every_shape():
    """Run the real function rather than re-implementing it here — a Python copy
    of the regex would drift from the one that actually ships."""
    src = _js()
    body = src[src.index("function _isSeasonPack"):src.index("function resultCardHTML")]
    script = (
        "const f = (function(){ %s; return _isSeasonPack; })();\n"
        "const packs = %s, singles = %s;\n"
        "const bad = [];\n"
        "for (const t of packs) if (f({title: t}) !== true) bad.push(['want pack', t]);\n"
        "for (const t of singles) if (f({title: t}) !== false) bad.push(['want single', t]);\n"
        "console.log(JSON.stringify(bad));"
    ) % (body, json.dumps(_PACKS), json.dumps(_SINGLES))
    out = subprocess.run([_node(), "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == [], out.stdout


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_a_soulseek_folder_is_a_pack_and_a_lone_file_is_not():
    src = _js()
    body = src[src.index("function _isSeasonPack"):src.index("function resultCardHTML")]
    script = (
        "const f=(function(){ %s; return _isSeasonPack; })();\n"
        "console.log(JSON.stringify([\n"
        "  f({files:[{filename:'a.mkv'},{filename:'b.mkv'}], title:'whatever'}),\n"
        "  f({files:[{filename:'a.mkv'}], title:'Some.Movie.2019'}),\n"
        "  f({}), f(null)]));" % body)
    out = subprocess.run([_node(), "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == [True, False, False, False]


def test_the_word_boundary_guard_is_present():
    """Asserted structurally too, so the reason survives on a machine with no node.
    Losing it re-files every movie with a DTS5.1 tag as a season pack."""
    src = _js()
    fn = src[src.index("function _isSeasonPack"):src.index("function resultCardHTML")]
    assert "(?:^|[\\s._\\-[(])" in fn, "the season token must start a word"
    assert "DTS5.1" in fn or "audio tag" in fn, "keep the reason next to the regex"


# ── routing + grabbing ───────────────────────────────────────────────────────

def test_torrent_packs_reach_the_pack_card():
    """The gate used to be r.files.length > 1, which only slskd ever satisfies."""
    src = _js()
    assert "if (_isSeasonPack(r)) return _packCardHTML(r, i);" in src
    assert "if (r.files && r.files.length > 1) return _packCardHTML" not in src


def test_a_torrent_pack_is_grabbed_as_one_season_scoped_row():
    """Soulseek can fan out at grab time because it lists the folder first. A
    torrent's files don't exist until it finishes, so it goes as ONE row scoped to
    the season and the monitor maps the folder afterwards."""
    src = _js()
    fn = src[src.index("function _grabPack"):src.index("// Shared expandable-pack")]
    assert "'/api/video/downloads/grab-pack'" in fn, "soulseek keeps its fan-out"
    assert "payload.search_ctx.scope = 'season'" in fn
    assert "payload.search_ctx.episode = null" in fn, "a pack names no single episode"


def test_the_two_grab_paths_answer_the_same_shape():
    """grab-pack returns {started, skipped}; the ordinary grab returns {id}. The
    button must not have to know which one ran."""
    src = _js()
    fn = src[src.index("function _grabPack"):src.index("// Shared expandable-pack")]
    assert "started: 1" in fn


# ── the season button ────────────────────────────────────────────────────────

def _grab_season() -> str:
    src = _js()
    start = src.index("function grabSeason(")
    return src[start:src.index("\n    // On (re)open", start)]


def test_every_configured_source_is_used():
    """`[0]` meant one dead or incomplete indexer decided the whole season."""
    fn = _grab_season()
    assert "var sources = (st.sources || []).filter" in fn
    assert ".filter(function (s) { return SRC_META[s]; })[0]" not in fn


def test_grab_season_grabs_each_episode_and_never_a_pack():
    """SeadogsBooty: the button says one at a time, and The Office S1 went to
    sabnzbd as one season pack with all six rows sharing its progress. a pack is
    the season search's job now, Grab season never looks for one."""
    fn = _grab_season()
    assert fn.count("perEpisode()") == 2, "defined once, called once"
    assert "scope: 'season'" not in fn and "_trySeasonPack" not in _js()
    assert "Looking for a season pack" not in fn


def test_the_per_episode_fallback_survives():
    """A half-owned season has no useful pack — the episode path must still run."""
    fn = _grab_season()
    assert "autoGrabEpisode(container, st, sn," in fn
    assert "perEpisode();" in fn, "no pack found → fall back, not give up"


def test_the_fallback_spreads_episodes_across_sources():
    fn = _grab_season()
    assert "sources[n++ % sources.length]" in fn


def test_the_season_search_button_still_finds_packs():
    """the way to get a pack on purpose: the season's search, scoped to the
    season so a grab lands as a folder mapped onto episodes"""
    src = _js()
    assert "data-vdl-season-search" in src
    assert "scope: 'season'" in src


def test_the_button_is_re_enabled_when_the_season_is_done():
    fn = _grab_season()
    assert fn.count("btn.textContent = 'Grab season'") >= 1


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_whole_file_still_parses():
    out = subprocess.run([_node(), "--check", str(_SRC)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


# ── the OTHER Grab season (video-grab.js, used by the detail page) ───────────
# Boulder found this the honest way: the modal's button showed "Looking for a
# season pack…" and the detail page's identical-looking button still searched
# three episodes at a time. They are two separate implementations — video-grab.js
# says so in its own header ("Kept separate for now so wiring the detail page
# can't regress the working modal") — and the first pass only fixed the modal.
#
# So this half is driven END TO END in node against a stubbed backend rather than
# grepped, because grepping is what missed it.

_GRAB = Path("webui/static/video/video-grab.js")

_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync('webui/static/video/video-grab.js', 'utf8');
function run(hasPack) {
  const calls = { search: [], grab: [] };
  global.window = {}; global.document = { dispatchEvent() {} };
  global.CustomEvent = function () {};
  global.fetch = (url, opt) => {
    const body = opt && opt.body ? JSON.parse(opt.body) : null;
    if (url.indexOf('/downloads/config') >= 0)
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        { download_mode: 'hybrid', hybrid_order: ['soulseek', 'torrent'] }) });
    if (url.indexOf('/search/start') >= 0) {
      calls.search.push(body.scope + ':' + body.source);
      const hit = [{ accepted: true, username: 'idx', title: 'Show.S03' }];
      const want = hasPack ? 'season' : 'episode';
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        { results: body.scope === want ? hit : [] }) });
    }
    if (url.indexOf('/downloads/grab') >= 0) {
      calls.grab.push(body.search_ctx.scope);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, id: 99 }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
  eval(src);
  return { VideoGrab: global.window.VideoGrab, calls };
}
(async () => {
  const out = {};
  let { VideoGrab, calls } = run(true);
  out.pack = await VideoGrab.season({ title: 'Show', season: 3, episodes: [1, 2, 3, 4, 5] });
  out.packSearches = calls.search; out.packGrabs = calls.grab;
  ({ VideoGrab, calls } = run(false));
  out.eps = await VideoGrab.season({ title: 'Show', season: 3, episodes: [1, 2, 3] });
  out.epSearches = calls.search; out.epGrabs = calls.grab;
  console.log(JSON.stringify(out));
})();
"""


def _drive():
    out = subprocess.run([_node(), "-e", _HARNESS], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_detail_page_helper_never_grabs_a_pack_even_when_one_exists():
    d = _drive()
    assert not d["pack"].get("pack")
    assert "season" not in d["packGrabs"]
    assert not [s for s in d["packSearches"] if s.startswith("season:")]
    assert all(s.startswith("episode:") for s in d["packSearches"])


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_no_pack_still_falls_back_to_per_episode():
    d = _drive()
    assert d["eps"]["grabbed"] == 3 and not d["eps"].get("pack")
    assert d["epGrabs"] == ["episode", "episode", "episode"]


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_episode_fallback_spreads_across_sources():
    """`pump()` used to call launch(eps[idx++]) with one argument; adding a source
    index without threading it through would have made every episode use
    sources[undefined % n] — silently NaN, and one source again."""
    d = _drive()
    eps = [s for s in d["epSearches"] if s.startswith("episode:")]
    assert eps == ["episode:soulseek", "episode:torrent", "episode:soulseek"]


def test_pick_source_still_returns_one_for_callers_that_want_one():
    js = _GRAB.read_text(encoding="utf-8")
    assert "function pickSource()" in js and "function allSources()" in js


def test_the_detail_page_no_longer_pins_the_season_to_one_source():
    """Passing pickSource() into season() would restore the single-source bug even
    with everything else correct."""
    js = Path("webui/static/video/video-detail.js").read_text(encoding="utf-8")
    fn = js[js.index("function grabSeasonInline("):js.index("function wishSeasonInline(")]
    assert "VideoGrab.pickSource()" not in fn
    assert "Looking for a season pack" not in fn and "res.pack" not in fn


# ── a search that ends badly still ends (SeadogsBooty, MobLand S1) ───────────
# Grab season showed "Grabbing 10 episodes", every row sat on Searching, and
# nothing reached SABnzbd. searchInto only called its done callback on
# success: an error answer or a closed panel returned without it, so each
# episode's promise never settled and the 3-wide pump never freed a slot.

_SEARCH_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync('webui/static/video/video-download-view.js', 'utf8');
const body = src.slice(src.indexOf('function searchInto('), src.indexOf('function _pollSearch('));
function el(connected) {
  return { isConnected: connected, hidden: true, innerHTML: '', classList: { remove() {}, toggle() {} } };
}
async function run(answer, connected) {
  const env = {
    postJSON: () => Promise.resolve(answer),
    withIdentity: (c, r, p) => p, _setScanning() {}, esc: (x) => String(x),
    scopeWord: () => 'season', renderResults() {}, _pollSearch() {},
  };
  const f = new Function(...Object.keys(env), body + '; return searchInto;')(...Object.values(env));
  const res = el(connected);
  let done = 0;
  f({}, res, { scope: 'episode', source: 'usenet' }, [], () => { done++; });
  await new Promise((r) => setTimeout(r, 20));
  return { done, error: res._error || null, html: res.innerHTML };
}
(async () => {
  console.log(JSON.stringify({
    error: await run({ error: 'Prowlarr: 401' }, true),
    closed: await run({ results: [] }, false),
    ok: await run({ results: [] }, true),
  }));
})();
"""


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_a_search_that_errors_or_closes_still_calls_done():
    out = subprocess.run([_node(), "-e", _SEARCH_HARNESS], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout.strip())
    assert d["error"]["done"] == 1, "an indexer error must settle the episode"
    assert d["error"]["error"] == "Prowlarr: 401", "and the row gets the reason"
    assert d["closed"]["done"] == 1
    assert d["ok"]["done"] == 1


def test_the_episode_row_says_why_it_was_not_grabbed():
    js = _js()
    assert "epNoRelease(epEl, r.error || panel._error)" in js
    assert "Not grabbed: " in js


def test_season_and_show_pack_searches_name_a_source():
    """they sent none, and the server answered with DEMO DATA placeholders."""
    js = _js()
    assert "{ scope: 'season', title: st.title, season: +sn, source: packSrc }" in js
    assert "season_end: st.maxSeason || 5, source: showSrc }" in js
    fn = js[js.index("function _packSource("):js.index("function _noSourceInto(")]
    assert "'usenet'" in fn and "'torrent'" in fn


# ── an errored search gets one more try (SeadogsBooty, Dexter S1) ────────────
# three episode searches at once backed Prowlarr up past its 75s timeout and
# every episode after the first few came back "Not grabbed: Prowlarr did not
# answer within 75s". an errored search is not "nothing found": it goes to the
# back of the queue once, one search at a time from then on.

_RETRY_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync('webui/static/video/video-grab.js', 'utf8');
const calls = { search: [], grab: [], states: {} };
const seen = {};
global.window = {}; global.document = { dispatchEvent() {} };
global.CustomEvent = function () {};
global.fetch = (url, opt) => {
  const body = opt && opt.body ? JSON.parse(opt.body) : null;
  const reply = (x) => Promise.resolve({ ok: true, json: () => Promise.resolve(x) });
  if (url.indexOf('/downloads/config') >= 0) return reply({ download_mode: 'usenet' });
  if (url.indexOf('/search/start') >= 0) {
    if (body.scope === 'season') return reply({ results: [] });
    const ep = body.episode;
    seen[ep] = (seen[ep] || 0) + 1;
    calls.search.push(ep);
    // ep 2 times out once, ep 3 every time, ep 4 simply has nothing
    if ((ep === 2 && seen[ep] === 1) || ep === 3)
      return reply({ error: 'Prowlarr: Prowlarr did not answer within 75s' });
    if (ep === 4) return reply({ results: [] });
    return reply({ results: [{ accepted: true, username: 'idx', title: 'Show.S01E0' + ep }] });
  }
  if (url.indexOf('/downloads/grab') >= 0) {
    calls.grab.push(body.search_ctx.episode);
    return reply({ ok: true, id: 7 });
  }
  return reply({});
};
eval(src);
(async () => {
  const res = await global.window.VideoGrab.season(
    { title: 'Show', season: 1, episodes: [1, 2, 3, 4] },
    (en, state) => { calls.states[en] = state; });
  console.log(JSON.stringify({ res, calls }));
})();
"""


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_an_errored_episode_search_is_tried_once_more():
    out = subprocess.run([_node(), "-e", _RETRY_HARNESS], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout.strip())
    assert sorted(d["calls"]["grab"]) == [1, 2], "the timed-out episode came back on its retry"
    assert d["res"]["grabbed"] == 2
    assert d["calls"]["search"].count(2) == 2 and d["calls"]["search"].count(3) == 2
    assert d["calls"]["search"].count(4) == 1, "nothing found is an answer, not an error"
    assert d["calls"]["states"]["3"] == "none" and d["calls"]["states"]["4"] == "none"


def test_the_modal_retries_an_errored_episode_once():
    js = _js()
    fn = js[js.index("function grabSeason("):js.index("function perEpisode()") + 2000]
    assert "queue.push([en, 1])" in fn and "MAX = 1" in fn
    assert "errored: !!panel._error" in js
