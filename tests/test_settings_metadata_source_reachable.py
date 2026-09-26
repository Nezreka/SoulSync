"""the primary metadata source dropdown has to be somewhere a person can reach.

the connections tab rebuild hid both legacy "API Configuration" groups with
display:none and gave every service panel a tile that lifts it into a modal. the
metadata source panel was never a service, so it got no tile and sat hidden in
the legacy group, taking the library discography source (injected next to it)
down with it. discord: "i can no longer find the option to specify the primary
metadata server".
"""

from html.parser import HTMLParser
from pathlib import Path

_INDEX = Path(__file__).resolve().parents[1] / "webui" / "index.html"
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Ancestors(HTMLParser):
    def __init__(self, target_id):
        super().__init__()
        self.target_id = target_id
        self.stack = []
        self.found = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == self.target_id and self.found is None:
            self.found = list(self.stack)
        if tag not in _VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def _ancestors(element_id):
    p = _Ancestors(element_id)
    p.feed(_INDEX.read_text(encoding="utf-8"))
    assert p.found is not None, f"#{element_id} is gone from index.html"
    return p.found


def test_metadata_source_is_not_inside_the_hidden_legacy_group():
    for tag, attrs in _ancestors("metadata-fallback-source"):
        assert "data-svc-legacy" not in attrs, (
            "the metadata source dropdown is inside a data-svc-legacy group, "
            "which style.css hides outright"
        )


def test_metadata_source_still_lives_on_the_connections_tab_music_side():
    groups = [a for t, a in _ancestors("metadata-fallback-source") if "settings-group" in (a.get("class") or "")]
    assert groups and groups[-1].get("data-stg") == "connections"
    assert "data-music-only" in groups[-1]
