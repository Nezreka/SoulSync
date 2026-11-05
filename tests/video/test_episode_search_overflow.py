"""An episode's manual search results stay inside the panel (SeadogsBooty).

``.vdl-ep-search`` is a one-column grid (it animates open on its rows). a
bare track grows to the longest release name, since titles are nowrap, so a
card measured 858px inside an 840px panel; the panel is overflow: hidden,
which still scrolls, and scrolling the grabbed card into view shifted it
sideways and cut off both edges. measured in chromium with the real css:
with minmax(0, 1fr) the card is 784px and nothing scrolls.
"""

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "webui" / "static" / "video" / "video-side.css"


def test_the_episode_search_column_can_shrink_below_a_release_name():
    css = CSS.read_text(encoding="utf-8")
    body = re.search(r"^\.vdl-ep-search\s*\{([^}]*)\}", css, re.M)
    assert body, ".vdl-ep-search rule missing"
    assert re.search(r"grid-template-columns:\s*minmax\(0,\s*1fr\)", body.group(1))
