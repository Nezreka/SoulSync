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
    js = _read("webui/static/settings.js")
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


def test_the_audit_is_actually_looking_at_something(save_body):
    # A parse that silently found nothing would make the check above pass.
    ids = _ids_read(save_body)
    assert len(ids) > 150, f"only found {len(ids)} fields; the parse is wrong"
    assert "prowlarr-url" in ids


def test_no_payload_field_blanks_a_missing_element(save_body):
    """`?.value || ''` and friends. Each of these writes a concrete value —
    '', false, 0, or a hardcoded default — when the element is absent."""
    bad = re.findall(r"\b[a-z_0-9]+: [^\n]*getElementById\('[^']+'\)\?\.(?:value|checked)[^\n]*",
                     save_body)
    assert not bad, "these overwrite stored settings when their field is missing:\n" + "\n".join(bad)


def test_the_helpers_omit_rather_than_blank():
    """undefined is the point: JSON.stringify drops undefined-valued keys, so
    the key never reaches the server and the stored value survives."""
    js = _read("webui/static/settings.js")
    for name in ("_cfgStr", "_cfgBool", "_cfgInt", "_cfgFloat"):
        fn = js.split(f"function {name}(", 1)[1].split("\n}", 1)[0]
        assert "if (!el) return undefined" in fn or "el ? " in fn, f"{name} does not omit"


def test_an_empty_string_the_user_typed_is_still_saved():
    """Clearing a field on purpose has to keep working — the guard is about a
    field that is not THERE, not a field that is empty."""
    js = _read("webui/static/settings.js")
    fn = js.split("function _cfgStr(", 1)[1].split("\n}", 1)[0]
    assert "fallback !== undefined" in fn, "an empty value must still be sent"


def test_the_helpers_are_used_widely(save_body):
    # If most of the payload bypassed them the guard above would be hollow.
    assert save_body.count("_cfgStr(") + save_body.count("_cfgBool(") > 40
