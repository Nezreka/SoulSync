"""The /label-detail/ link contract between React search cards and the shell.

React label cards render plain ``/label-detail/<id>`` anchors and rely on the
shell's capture-phase click handler to keep the navigation in-app. When the
handler doesn't claim the path, the browser performs a FULL page reload — app
reboot, in-memory search state gone — and ``navigateToLabelDetail`` never runs,
so the label page's Back button loses its return target. The artist-detail
path has the same contract and the same handler; this pins the label half so
neither side can drift alone (the search port shipped without it once).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHELL_BRIDGE = (REPO / "webui" / "static" / "shell-bridge.js").read_text(encoding="utf-8")
INIT_JS = (REPO / "webui" / "static" / "init.js").read_text(encoding="utf-8")
SEARCH_HELPERS = (
    REPO / "webui" / "src" / "routes" / "search" / "-search.helpers.ts"
).read_text(encoding="utf-8")


def test_react_still_emits_label_detail_hrefs():
    assert "/label-detail/" in SEARCH_HELPERS, (
        "labelDetailPath no longer emits /label-detail/ hrefs — update the "
        "shell-bridge claim to whatever it emits now"
    )


def test_shell_bridge_claims_the_label_detail_path():
    assert "pathname.startsWith('/label-detail/')" in SHELL_BRIDGE, (
        "shell-bridge no longer claims /label-detail/ clicks — label cards "
        "will full-page-reload the app"
    )
    assert "_handleLabelDetailLinkClick" in SHELL_BRIDGE


def test_the_claim_routes_through_the_return_to_navigator():
    # navigateToLabelDetail is what records _labelDetailReturnTo for the label
    # page's Back button; navigating any other way silently breaks Back.
    assert "navigateToLabelDetail(" in SHELL_BRIDGE
    assert "function navigateToLabelDetail(" in INIT_JS
