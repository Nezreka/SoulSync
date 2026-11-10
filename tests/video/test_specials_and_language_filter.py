"""SeadogsBooty asked for two things: hide Specials (season 0) on a show page
and leave them out when grabbing a whole show, and filter search results by
audio language for dubbed anime. Specials stay in the data and can always be
picked: hiding is a view choice, unticked is only a default."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

DETAIL = Path("webui/static/video/video-detail.js")
VIEW = Path("webui/static/video/video-download-view.js")


def _node():
    for candidate in ("node", "/mnt/c/Program Files/nodejs/node.exe"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


def _fn(src, name):
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


_HARNESS = r"""
const SRC = %s;
eval(SRC);
const out = {};
let hideSpecials = false;
var data = { seasons: [{season_number: 1}, {season_number: 2}, {season_number: 0}] };
out.shown = navSeasons().map(s => s.season_number);
hideSpecials = true;
out.hidden = navSeasons().map(s => s.season_number);
data = { seasons: [{season_number: 0}] };
out.onlySpecials = navSeasons().map(s => s.season_number);
out.hideable = hasHideableSpecials(data);
out.hideableMixed = hasHideableSpecials({ seasons: [{season_number: 0}, {season_number: 1}] });
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_hiding_specials_drops_season_0_but_never_empties_the_nav():
    js = DETAIL.read_text(encoding="utf-8")
    src = _fn(js, "navSeasons") + "\n" + _fn(js, "hasHideableSpecials")
    out = subprocess.run([_node(), "-e", _HARNESS % json.dumps(src)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout.strip())
    assert d["shown"] == [1, 2, 0]
    assert d["hidden"] == [1, 2]
    assert d["onlySpecials"] == [0], "a show with only specials keeps them"
    assert d["hideable"] is False and d["hideableMixed"] is True


def test_every_season_nav_reads_the_filtered_list():
    js = DETAIL.read_text(encoding="utf-8")
    for name in ("railHTML", "timelineHTML", "pillsHTML", "dropdownHTML"):
        body = _fn(js, name)
        assert "navSeasons()" in body and "data.seasons" not in body, name


def test_the_toggle_is_on_the_page_and_remembered():
    html = Path("webui/index.html").read_text(encoding="utf-8")
    assert "data-vd-specials-toggle" in html
    js = DETAIL.read_text(encoding="utf-8")
    assert "soulsync.video.hideSpecials" in js


def test_specials_start_unticked_in_the_acquisition_plan():
    body = _fn(VIEW.read_text(encoding="utf-8"), "fillSeason")
    assert re.search(r"if \(sn !== 0\) st\.sel\.add\(", body)


def test_search_rows_carry_their_languages_for_the_filter():
    from core.video import search_filters as sf
    assert sf.FIELDS["language"] == "text"
    src = Path("api/video/downloads.py").read_text(encoding="utf-8")
    assert '"languages": list(parsed.get("languages") or [])' in src
    assert sf.normalize_filter({"name": "Dubbed", "conditions": [
        {"field": "language", "op": "contains", "value": "english"}]}) is not None
