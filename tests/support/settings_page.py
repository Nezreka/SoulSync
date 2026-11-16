"""index.html with the Settings page in it, for the tests that read the page.

The Settings page is React since SETTINGS_PORT.md step V4c: index.html keeps a
comment where its markup was, and the page's shell is kept rendered in
webui/src/features/settings/__fixtures__/settings-page.html (a frontend test,
settings-page-markup.test.tsx, fails when the page changes and the file does
not). This puts it back where it was, so a test of the page reads it in place.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
MARKER = ("<!-- Settings is React: src/features/settings/settings-page.tsx "
          "(SETTINGS_PORT.md step V4c) -->")
SNAPSHOT = _ROOT / "webui" / "src" / "features" / "settings" / "__fixtures__" / "settings-page.html"


def index_with_settings_page() -> str:
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert index.count(MARKER) == 1, "the Settings page's place in index.html moved"
    return index.replace(MARKER, SNAPSHOT.read_text(encoding="utf-8"))
