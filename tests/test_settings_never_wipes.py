"""A settings field that is not on the page must not erase what is stored.

This is the destructive form of the invisible-settings bug. The save handler
sets config keys one at a time with NO deep merge, so whatever the page sends
for a key becomes that key's new value. The page read its fields like this:

    url: document.getElementById('prowlarr-url')?.value || ''

which looks careful and is the opposite. When the element is absent the `?.`
yields undefined, `|| ''` turns that into an empty string, and the empty string
is written over the stored setting.

It happened. A broken edit removed the Indexers / Torrent / Usenet block from
index.html for a few minutes. The Flask server reads that file per request, so
the settings page rendered without those fields and the next auto-save wiped
three URLs out of Boulder's database — prowlarr.url, torrent_client.url and
usenet_client.url. The Prowlarr API key survived only because the server
refuses to overwrite a stored secret with a blank, and torrent_client.type
survived as "qbittorrent" only because that field's fallback happened to match
what he already had. On Transmission it would have switched him silently.

Two guards here:

  * every id the save reads must exist in index.html, so a field that goes
    missing fails a test instead of eating data on a live install;
  * the save must read through the _cfg* helpers, which return undefined for a
    missing element — JSON.stringify drops undefined-valued keys, so the key
    never reaches the server and the stored value is left alone.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8", errors="ignore")


@pytest.fixture(scope="module")
def save_body() -> str:
    js = _read("webui/src/features/settings/shell.ts")
    body = js.split("async function saveSettings", 1)[1]
    for end in ("\n}\n", "\r\n}\r\n"):
        if end in body:
            return body[: body.index(end)]
    raise AssertionError("could not find the end of saveSettings")


@pytest.fixture(scope="module")
def index() -> str:
    return _read("webui/index.html")


def _ids_read(body: str) -> set:
    ids = set(re.findall(r"getElementById\('([^']+)'\)\s*\??\s*\.\s*(?:value|checked)", body))
    ids |= set(re.findall(r"_cfg(?:Str|Bool|Int|Float)\('([^']+)'", body))
    return ids


def test_every_field_the_save_reads_exists_on_the_page(save_body, index):
    """The check that would have caught the broken edit in seconds rather than
    after it had already emptied three settings on a live install."""
    missing = sorted(i for i in _ids_read(save_body) if f'id="{i}"' not in index)
    assert not missing, (
        "saveSettings reads these ids but index.html has no such element — on a "
        "live install the next auto-save writes over their stored values: "
        + ", ".join(missing)
    )


def test_the_save_reads_no_field_now(save_body):
    """Every field on the music side is React since SETTINGS_PORT.md step 10c,
    and saves only what changed: the page-wide save reads none off the page,
    so it cannot write over a stored value from a field that is not there.
    A field read here again would be a step backwards, and the check above
    would be looking at it."""
    assert _ids_read(save_body) == set()
    # the parse still sees the save: its body is there to read (the page's
    # shell is shell.ts since SETTINGS_PORT.md step V4b)
    assert "reactSaved = await host.flush();" in save_body


def test_no_payload_field_blanks_a_missing_element(save_body):
    """`?.value || ''` and friends. Each of these writes a concrete value —
    '', false, 0, or a hardcoded default — when the element is absent."""
    bad = re.findall(r"\b[a-z_0-9]+: [^\n]*getElementById\('[^']+'\)\?\.(?:value|checked)[^\n]*",
                     save_body)
    assert not bad, "these overwrite stored settings when their field is missing:\n" + "\n".join(bad)


def test_the_page_save_sends_no_value_at_all(save_body):
    """The _cfg* helpers made a missing field omit its key rather than blank
    it. They went with settings.js (SETTINGS_PORT.md step V4b): no field is
    read here any more, so the page's own post carries sections with nothing
    in them, and every value goes out from its React tab, only when changed."""
    js = _read("webui/src/features/settings/shell.ts")
    assert "_cfg" not in js
    sent = save_body.split("const settings = {", 1)[1].split("};", 1)[0]
    sections = re.findall(r"^\s*([a-z0-9_]+): (\{\}),?$", sent, re.M)
    assert len(sections) == 7, sent
    assert re.sub(r"^\s*[a-z0-9_]+: \{\},?$", "", sent, flags=re.M).strip() == "", sent


