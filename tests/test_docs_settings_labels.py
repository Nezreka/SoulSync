"""Docs-vs-UI settings label drift guard (SoulSync PR #1310 review item).

The Help & Docs name specific settings labels (buttons, fields, section
headings, toggles). If a label is renamed in the UI without the docs being
updated (or vice versa), users following the docs can't find the control.
This module fails loudly when a docs-named settings label no longer matches
anything the settings UI can render.

How the label list was built (deterministic, reviewable):
  1. Regex pass over webui/static/docs-content/*.js for quoted strings
     ("...", '...') and **bold** phrases, 4-70 chars, near settings topics.
  2. Each candidate was normalized (see _norm) and checked against the UI
     corpus; only candidates with a verbatim normalized match in the corpus
     AND in their docs source file made LABELS. Paraphrases (docs wording
     that does not match the UI verbatim) were deliberately excluded — they
     are documented in EXCLUDED below with the reason, so a reviewer can
     tell "checked and excluded" apart from "never checked".
  3. Tab-name assertions come from the settings nav rendered in index.html
     (class="stg-tab" buttons), which the docs' Settings pages describe.

Corpus (where settings labels reach the DOM):
  - webui/index.html — static settings markup (labels, headings, buttons).
  - webui/static/settings.js — renders some labels dynamically (e.g. the
    "Incremental Vacuum" button text), so it is part of the corpus.
  Settings labels do NOT live in webui/src (that tree is the newer
  shell; the settings page itself is still index.html + static/settings.js).

Robustness:
  - Comparison is case-insensitive with punctuation/whitespace collapsed.
  - Labels shorter than 4 chars are rejected from LABELS (too ambiguous).
  - Each failure names the label AND the docs file it came from.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_DOCS_DIR = _ROOT / "webui" / "static" / "docs-content"
# The Tools page is React-rendered (webui/src); its card titles and button
# labels don't reach the static corpus above.
_REACT_SERVER_CARDS = (
    _ROOT / "webui" / "src" / "routes" / "tools" / "-ui" / "server-cards.tsx"
).read_text(encoding="utf-8")

# Where settings labels can appear in the rendered UI.
_CORPUS = "\n".join((_INDEX, _SETTINGS_JS))


def _norm(text: str) -> str:
    """Case/whitespace/punctuation-insensitive comparison key."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)  # HTML entities
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_CORPUS_N = _norm(_CORPUS)
_DOCS_N = {p.name: _norm(p.read_text(encoding="utf-8")) for p in sorted(_DOCS_DIR.glob("*.js"))}

# (label as named in the docs, docs file it comes from)
LABELS: list[tuple[str, str]] = [
    # Field labels
    ("Import Folder", "10-import-player.js"),
    ("Download Folder (input)", "11-settings.js"),
    ("Music Library Folder (output)", "11-settings.js"),
    ("Plex Server URL", "11-settings.js"),
    ("Link to Plex (OAuth)", "11-settings.js"),
    ("Jellyfin Server URL", "11-settings.js"),
    ("Navidrome Server URL", "11-settings.js"),
    ("API Key", "11-settings.js"),
    # Section headings / features
    ("Listening Stats", "06-discover.js"),
    ("Additional Music Libraries", "11-settings.js"),
    ("Auto-save M3U file when downloading playlists", "05-sync.js"),
    # Buttons
    ("Test Connection", "13-troubleshooting.js"),
    ("Compact Database (VACUUM)", "11-settings.js"),
    ("Enable Incremental Vacuum", "11-settings.js"),
    # Processing & organization settings
    ("AcoustID verification", "11-settings.js"),
    ("Path templates", "11-settings.js"),
    ("Multi-Disc Folder Label", "11-settings.js"),
    ("Search timeout", "11-settings.js"),
    ("Replace lower quality files on import", "11-settings.js"),
    # Miscellaneous
    ("Accent color", "11-settings.js"),
    ("Log level", "11-settings.js"),
    # Database maintenance
    ("VACUUM", "11-settings.js"),
    ("Incremental vacuum", "11-settings.js"),
    # Quality
    ("Accept off-list quality when nothing in the list is available", "04-search.js"),
    # Discover
    ("Enable listening stats collection from media server", "06-discover.js"),
    # Service rows on the Connections tab (Service Credentials page)
    ("Spotify", "11-settings.js"),
    ("slskd", "11-settings.js"),
    ("Tidal", "11-settings.js"),
    ("Last.fm", "11-settings.js"),
    ("Genius", "11-settings.js"),
    ("Qobuz", "11-settings.js"),
    ("HiFi", "11-settings.js"),
    ("Deezer", "11-settings.js"),
    ("Discogs", "11-settings.js"),
    ("AcoustID", "11-settings.js"),
    ("ListenBrainz", "11-settings.js"),
    ("YouTube Browser Cookies", "11-settings.js"),
    ("Paste cookies.txt", "11-settings.js"),
    ("Create lossy copy of downloaded FLAC files", "11-settings.js"),
    ("Lookback Period", "11-settings.js"),
    ("Storefront Country", "11-settings.js"),
    ("Upgrade until", "11-settings.js"),
]

# (visible tab label, data-tab slug) — the settings nav in index.html.
# The docs' Settings pages (11-settings.js) describe these tabs' contents;
# the Library tab is additionally named by the docs breadcrumb
# "Settings → Library → Folders" (01-getting-started.js).
TABS: list[tuple[str, str]] = [
    ("Connections", "connections"),
    ("Sources", "sources"),
    ("Downloads", "downloads"),
    ("Quality", "quality"),
    ("Library", "library"),
    ("Appearance", "appearance"),
    ("Advanced", "advanced"),
    ("Logs", "logs"),
]

# Labels that exist ONLY at runtime (rendered from server data, never
# present in the static corpus). Entries here are (label, reason). Checked
# but intentionally not asserted against the corpus.
#
# Currently empty: none of the docs-named settings labels are dynamic-only.
# Examples of what would belong here: per-profile display names, or
# media-server instance names discovered at runtime.
ALLOWLIST: dict[str, str] = {}

# Docs phrases that were checked against the corpus and deliberately NOT
# asserted, with the reason. This documents "checked and excluded" so a
# reviewer doesn't re-litigate them. Most are docs paraphrases of the UI
# wording — real drift candidates for a future docs copy pass, but not
# verbatim labels, so asserting them would be flaky by construction.
EXCLUDED: dict[str, str] = {
    "YouTube cookies": "short prose name in docs; the exact UI labels ('YouTube Browser Cookies' / 'Paste cookies.txt') are quoted in the same sentence and asserted in LABELS",
    "Multi-disc labels": "docs paraphrase kept alongside the exact label; UI label is 'Multi-Disc Folder Label:' (asserted in LABELS)",
    "Auto-Backup Database": "verified real: the system automation name in core/automation_engine.py (music + video variants, every 3 days) — not a UI label, so not asserted against the corpus",
}


def test_label_list_is_meaningful():
    """The curated list must be non-trivial and well-formed."""
    assert len(LABELS) >= 20, f"expected >= 20 curated labels, got {len(LABELS)}"
    labels = [label for label, _ in LABELS]
    assert len(set(labels)) == len(labels), "duplicate labels in LABELS"
    for label, docs_file in LABELS:
        assert len(_norm(label)) >= 4, f"label too short/ambiguous: {label!r}"
        assert (_DOCS_DIR / docs_file).is_file(), f"docs file missing: {docs_file}"
        assert label not in ALLOWLIST, f"{label!r} is both in LABELS and ALLOWLIST"
        assert label not in EXCLUDED, f"{label!r} is both in LABELS and EXCLUDED"


@pytest.mark.parametrize("label,docs_file", LABELS, ids=[label for label, _ in LABELS])
def test_docs_settings_label_exists_in_ui(label: str, docs_file: str):
    """Each docs-named settings label must exist in the settings UI corpus."""
    key = _norm(label)
    assert key in _DOCS_N[docs_file], (
        f"docs drift: label {label!r} is no longer named in {docs_file} — "
        "update LABELS in this test (the docs were edited without it)"
    )
    assert key in _CORPUS_N, (
        f"docs-vs-UI drift: settings label {label!r} (named in docs file "
        f"{docs_file}) was not found in webui/index.html or "
        "webui/static/settings.js — the UI label was renamed/removed without "
        "updating the docs, or the docs name a label that never existed"
    )


@pytest.mark.parametrize("tab_label,slug", TABS, ids=[label for label, _ in TABS])
def test_settings_nav_tab_present(tab_label: str, slug: str):
    """Each settings tab button must render with its label in index.html."""
    assert f'data-tab="{slug}"' in _INDEX, (
        f"settings tab {tab_label!r} (data-tab={slug!r}) missing from index.html"
    )
    pattern = re.compile(
        r'class="stg-tab-label">\s*' + re.escape(tab_label) + r"\s*<"
    )
    assert pattern.search(_INDEX), (
        f"settings tab {tab_label!r} has no visible stg-tab-label in index.html — "
        "the docs' Settings pages describe this tab"
    )


def test_tools_media_server_scan_card_button():
    """The docs' troubleshooting page names the Tools page's Media Server Scan
    card and its Scan Library button (13-troubleshooting.js). That card is
    React-rendered, so the static settings corpus can't see it — assert
    against the React source directly. (The old LABELS entry only passed
    because the *video* Library Scan button in index.html happens to share
    the label.)"""
    docs = _DOCS_N["13-troubleshooting.js"]
    assert "scan library" in docs, (
        "docs no longer name the Scan Library button — update this test"
    )
    assert "media server scan" in docs, (
        "docs no longer name the Media Server Scan card — update this test"
    )
    assert 'title="Media Server Scan"' in _REACT_SERVER_CARDS, (
        "the Media Server Scan card is gone from the React Tools page — "
        "update the docs"
    )
    assert ">Scan Library</span>" in _REACT_SERVER_CARDS, (
        "the Media Server Scan card's button is no longer labelled "
        "'Scan Library' — update the docs"
    )
