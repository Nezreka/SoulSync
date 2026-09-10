"""The settings page must not be able to send a section the server drops.

The save handler walks a hard-coded list of section names and writes each one
key by key. A section missing from that list is accepted, answered with
"Settings saved successfully", and silently discarded. Nothing complains: the
GET returns the whole config, so the page reloads showing the old value and the
user reads it as "my toggle keeps resetting".

That is not a hypothetical. It has now happened twice — the audiobook block for
its first week, and ``hifi`` (the HiFi metadata-embed toggle and its per-tag
checkboxes) for considerably longer. This test closes the class rather than the
two instances: every top-level section the page sends has to be one the server
agrees to persist.
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _persisted_sections() -> set:
    """The section names web_server actually writes on a settings POST."""
    source = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="ignore")
    line = next(ln for ln in source.splitlines() if "for service in [" in ln)
    return set(re.findall(r"'([a-z0-9_]+)'", line))


def _sent_sections() -> set:
    """The top-level keys of the object saveSettings() POSTs.

    Read from the literal rather than the network: the sections are written at
    a fixed indent inside one `const settings = {` block, and anything deeper
    is a key within a section, not a section.
    """
    source = (_ROOT / "webui/static/settings.js").read_text(encoding="utf-8", errors="ignore")
    body = source.split("async function saveSettings", 1)[1]
    literal = body.split("const settings = {", 1)[1]
    sections = set()
    for line in literal.splitlines():
        if line.startswith("    };"):
            break
        match = re.match(r"^ {8}([a-z0-9_]+): \{$", line)
        if match:
            sections.add(match.group(1))
    return sections


def test_the_page_sends_something_at_all():
    # A parse that quietly finds nothing would make every check below pass.
    sent = _sent_sections()
    assert len(sent) > 30, sent
    assert "spotify" in sent


def test_every_section_the_page_sends_is_persisted():
    dropped = sorted(_sent_sections() - _persisted_sections())
    assert not dropped, (
        "settings.js POSTs these sections but web_server discards them, so the "
        "controls in them silently revert on reload: " + ", ".join(dropped)
    )


def test_the_hifi_metadata_toggles_persist():
    """Regression for the specific one. ``hifi.embed_tags`` and ``hifi.tags.*``
    are read by the tagging pipeline on every download, the settings page has
    checkboxes for them, and the save dropped the lot."""
    assert "hifi" in _persisted_sections()
    assert "hifi" in _sent_sections()
