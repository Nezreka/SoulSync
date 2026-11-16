"""the primary metadata source dropdown has to be somewhere a person can reach.

the connections tab rebuild hid both legacy "API Configuration" groups with
display:none and gave every service panel a tile that lifts it into a modal. the
metadata source panel was never a service, so it got no tile and sat hidden in
the legacy group, taking the library discography source (injected next to it)
down with it. discord: "i can no longer find the option to specify the primary
metadata server".

the picker is React now (SETTINGS_PORT.md step 10a): index.html holds its slot,
and connections-metadata.tsx renders the group into it.
"""

from html.parser import HTMLParser
from pathlib import Path

_INDEX = Path(__file__).resolve().parents[1] / "webui" / "index.html"
_TSX = (
    Path(__file__).resolve().parents[1]
    / "webui" / "src" / "features" / "settings" / "tabs" / "connections-metadata.tsx"
)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Ancestors(HTMLParser):
    def __init__(self, match):
        super().__init__()
        self.match = match
        self.stack = []
        self.found = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.match(attrs):
            self.found.append(list(self.stack))
        if tag not in _VOID:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def _slot_ancestors():
    p = _Ancestors(
        lambda a: a.get("data-stg-react") == "connections" and a.get("data-stg-part") == "metadata"
    )
    p.feed(_INDEX.read_text(encoding="utf-8"))
    assert p.found, "the metadata source slot is gone from index.html"
    return p.found


def _group():
    src = _TSX.read_text(encoding="utf-8")
    start = src.index("export function MetadataSourceGroup(")
    return src[start:src.index("\n}\n", start)]


def test_metadata_source_is_not_inside_the_hidden_legacy_group():
    for tag, attrs in (pair for found in _slot_ancestors() for pair in found):
        assert "data-svc-legacy" not in attrs, (
            "the metadata source slot is inside a data-svc-legacy group, "
            "which style.css hides outright"
        )


def test_metadata_source_still_lives_on_the_connections_tab_music_side():
    group = _group()
    assert '<div className="settings-group" data-stg="connections" data-music-only="">' in group
    assert "<MetadataPicker" in group
    # the library discography source sits with it
    assert "<DiscographyPicker" in group
    assert 'id="metadata-fallback-source"' in _TSX.read_text(encoding="utf-8")
