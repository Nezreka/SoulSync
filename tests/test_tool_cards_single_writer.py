"""The Tools page cards have ONE writer: React.

The db updater's progress line flipped between "39 / 208 tracks" and
"39 / 208 artists" every second during a full refresh (SeadogsBooty, Oct
2026). React rendered the backend's unit; core.js's socket handler still
called the vanilla updateDbProgressUI, which wrote a hardcoded "artists"
into the same React-owned element id. The duplicate cleaner and metadata
updater cards had the same second writer. Pinned at the source because the
seam is a vanilla script writing into React DOM, which no single test
harness renders together.
"""
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "webui" / "static" / "core.js"

# vanilla functions that write into the React tool cards' element ids
VANILLA_CARD_WRITERS = (
    "updateDbProgressFromData",
    "updateDuplicateCleanProgressFromData",
    "updateMetadataStatusFromData",
)


def _handler(src, event):
    m = re.search(r"socket\.on\('" + re.escape(event) + r"',\s*\(data\)\s*=>\s*\{(.*?)\}\);", src)
    assert m, f"socket handler for {event} not found"
    return m.group(1)


def test_tool_socket_handlers_do_not_write_into_react_cards():
    src = CORE.read_text(encoding="utf-8")
    for event in ("tool:db-update", "tool:duplicate-cleaner", "tool:metadata"):
        body = _handler(src, event)
        for writer in VANILLA_CARD_WRITERS:
            assert writer not in body, f"{event} still calls {writer} (a second writer on a React card)"
        # the quick-access busy signal still rides the socket
        assert "qaSignal('tools')" in body
