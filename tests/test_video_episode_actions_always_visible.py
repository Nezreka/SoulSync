"""Episode action buttons NEVER disappear (Boulder, Issue/1.png).

A grab finished this session painted the row `.vd-ep-get--done` with a
"✓ Downloaded" text — and the done/busy CSS hid ALL THREE action buttons
(auto search / manual search / add to wishlist) for the rest of the session.
The status must render BESIDE the buttons, not instead of them; during an
in-flight grab only the auto-grab button goes inert (no double-fire) while
manual search + wishlist stay live.

Source-contract pins on the CSS + JS — the states are painted by a poll loop
against live download data, so the contract is pinned where it lives.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")


def test_done_and_busy_states_never_hide_the_action_buttons():
    # The old rule: `.vd-ep-get--busy .vd-ep-getbtn, .vd-ep-get--done .vd-ep-getbtn { display: none; }`
    # No selector containing a busy/done state may set the getbtns to display:none.
    for rule in re.findall(r"([^{}]+)\{([^}]*)\}", _CSS):
        selector, body = rule
        if "vd-ep-getbtn" not in selector:
            continue
        if "--done" in selector or "--busy" in selector:
            assert "display: none" not in body and "display:none" not in body, (
                f"episode action buttons are hidden by state selector: {selector.strip()}"
            )


def test_busy_state_only_makes_the_auto_grab_inert():
    # Double-fire protection moved from hiding everything to making ONLY the
    # auto-grab button inert while a grab is in flight.
    assert re.search(r"\.vd-ep-get--busy\s+\.vd-ep-grab\s*\{[^}]*pointer-events:\s*none", _CSS)
    # Manual search + wishlist must not be inert in any busy/done rule.
    for rule in re.findall(r"([^{}]+)\{([^}]*)\}", _CSS):
        selector, body = rule
        if ("--busy" in selector or "--done" in selector) and (
                "vd-ep-search" in selector or "vd-ep-wish" in selector):
            assert "pointer-events: none" not in body and "display: none" not in body


def test_done_paint_reenables_the_grab_button():
    # grabEpisodeInline disables the button for its in-flight window; the
    # done-paint must hand it back or the row shows a dead button.
    done_branch = _JS[_JS.index("if (_dlDone[key])"):]
    done_branch = done_branch[:done_branch.index("} else if (_dlActive[key])")]
    assert "vd-ep-get--done" in done_branch
    assert "disabled = false" in done_branch


def test_downloaded_status_text_still_renders():
    # The fix removes the button-hiding, not the status itself.
    assert "✓ Downloaded" in _JS
    assert re.search(r"\.vd-ep-get--busy\s+\.vd-ep-dl,\s*\.vd-ep-get--done\s+\.vd-ep-dl\s*\{[^}]*inline-flex", _CSS)
