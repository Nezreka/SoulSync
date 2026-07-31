"""The React release classifier must stay identical to the vanilla one.

`_classifyReleaseContent` in library.js is deliberately SHARED between the
artist-detail page and the Download Discography modal (#877) so the two can
never disagree about what counts as live / compilation / featured. The React
port adds a third consumer, so "identical" now spans two languages.

Why this test exists: the first version of the port invented its own contract.
It read `release.is_live` / `is_compilation` / `is_featured` — fields the
backend never sends — instead of classifying by title. Every release came back
unclassified, so the Live / Compilations / Featured toggles would have done
nothing off MusicBrainz. The unit tests passed because they asserted the
invented contract rather than the real one. A test comparing the two sources
directly is the only kind that could have caught it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_VANILLA = (_ROOT / "webui" / "static" / "library.js").read_text(encoding="utf-8")
_PORT = (
    _ROOT / "webui" / "src" / "routes" / "artist-detail" / "-artist-detail.filters.ts"
).read_text(encoding="utf-8")

_PAIRS = [
    ("livePattern", "LIVE_PATTERN"),
    ("compilationPattern", "COMPILATION_PATTERN"),
    ("featuredPattern", "FEATURED_PATTERN"),
]


def _vanilla_classifier() -> str:
    start = _VANILLA.index("function _classifyReleaseContent")
    return _VANILLA[start : _VANILLA.index("\nfunction ", start + 5)]


def _regexes(source: str) -> dict[str, str]:
    return dict(re.findall(r"const (\w+) = (/.*/i);", source))


def test_vanilla_classifier_still_exists():
    """If it is renamed or inlined, everything below silently stops checking."""
    fn = _vanilla_classifier()
    assert "livePattern" in fn and "compilationPattern" in fn and "featuredPattern" in fn


@pytest.mark.parametrize("vanilla_name,port_name", _PAIRS)
def test_regex_is_byte_identical(vanilla_name, port_name):
    vanilla = _regexes(_vanilla_classifier())
    port = _regexes(_PORT)
    assert vanilla_name in vanilla, f"{vanilla_name} vanished from library.js"
    assert port_name in port, f"{port_name} vanished from the React port"
    assert vanilla[vanilla_name] == port[port_name], (
        f"classifier drift: library.js has {vanilla[vanilla_name]}, "
        f"the React port has {port[port_name]}"
    )


def test_compilation_also_keys_off_album_type():
    """The one rule that is not a regex — a release is a compilation if its
    album_type says so, REGARDLESS of title."""
    fn = _vanilla_classifier()
    assert "album_type === 'compilation'" in fn
    assert "album_type === 'compilation'" in _PORT


def test_port_does_not_invent_backend_fields():
    """The specific mistake this file was written for. The backend sends no
    is_live/is_compilation/is_featured; reading them classifies nothing."""
    for field in ("is_live", "is_compilation", "is_featured"):
        assert f"release.{field}" not in _PORT, (
            f"the port reads release.{field}, which the backend never sends — "
            "classification is title-based, see _classifyReleaseContent"
        )


def test_title_and_name_are_both_read():
    """artist detail passes `title`, the download modal passes `name`."""
    fn = _vanilla_classifier()
    assert "release.title || release.name" in fn
    assert "release.title ?? release.name" in _PORT
