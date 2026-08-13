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


def test_a_pack_is_tried_before_any_per_episode_search():
    """Assert the GUARD, not where the text sits. perEpisode is defined near the
    top and called at the bottom, so comparing string offsets would 'prove' the
    opposite of the truth — the contract is that the episode path runs only once
    every source has been asked for a pack and come back empty."""
    fn = _grab_season()
    assert "if (i >= sources.length) { perEpisode(); return; }" in fn
    assert fn.count("perEpisode()") == 2, "defined once, called from exactly one place"
    assert "Looking for a season pack" in fn


def test_the_per_episode_fallback_survives():
    """A half-owned season has no useful pack — the episode path must still run."""
    fn = _grab_season()
    assert "autoGrabEpisode(container, st, sn," in fn
    assert "perEpisode();" in fn, "no pack found → fall back, not give up"


def test_the_fallback_spreads_episodes_across_sources():
    fn = _grab_season()
    assert "sources[idx % sources.length]" in fn


def test_the_pack_search_is_scoped_to_the_season():
    """scope 'season' is what rides into search_ctx and tells the monitor this
    lands as a folder — without it the pack is judged as one episode."""
    src = _js()
    fn = src[src.index("function _trySeasonPack"):src.index("function grabSeason(")]
    assert "scope: 'season'" in fn


def test_the_button_is_re_enabled_on_both_outcomes():
    """Leaving it disabled after a successful pack grab strands the control."""
    fn = _grab_season()
    assert fn.count("btn.textContent = 'Grab season'") >= 2


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_whole_file_still_parses():
    out = subprocess.run([_node(), "--check", str(_SRC)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
