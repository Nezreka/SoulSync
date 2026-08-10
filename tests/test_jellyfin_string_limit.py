"""A string `Limit` param must not crash the Jellyfin request layer.

Reported as "Unable to start initial Jellyfin library sync": the video library
scan died before reading a single item, with

    File "core/jellyfin_client.py", line 482, in _make_request
      is_bulk_operation = params and params.get('Limit', 0) > 1000
    TypeError: '>' not supported between instances of 'str' and 'int'

`_make_request` picks a longer timeout for bulk calls by comparing Limit to
1000. Jellyfin accepts numeric params as either ints or strings, and
`core/video/sources.py::_paged` sends them as strings — `{"Limit": "500"}` —
so the comparison blew up on every paged video call. Every Jellyfin video
library scan hit this; it is deterministic, not environmental.

The request layer takes params from many callers and cannot dictate their
types, so it normalises before comparing.
"""

from __future__ import annotations

import pytest

from core.jellyfin_client import _as_int


# ── _as_int ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("value", "expected"), [
    ("500", 500),        # what _paged actually sends
    (500, 500),          # what the music-side callers send
    ("0", 0),
    (0, 0),
    ("2000", 2000),
])
def test_numeric_values_normalise(value, expected):
    assert _as_int(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", [], {}, object()])
def test_junk_falls_back_instead_of_raising(value):
    assert _as_int(value) == 0


def test_explicit_default_is_honoured():
    assert _as_int(None, 7) == 7


# ── the comparison that crashed ──────────────────────────────────────────────

@pytest.mark.parametrize("limit", ["500", 500, "1000", 1000, "0", 0, None, "abc"])
def test_bulk_check_never_raises_on_any_limit_type(limit):
    """The exact expression from _make_request, over every shape a caller sends."""
    params = {"Limit": limit} if limit is not None else {}
    assert isinstance(_as_int(params.get("Limit")) > 1000, bool)


@pytest.mark.parametrize(("limit", "is_bulk"), [
    ("5000", True),      # a string over the threshold must still read as bulk...
    (5000, True),
    ("500", False),      # ...and one under it must not
    (500, False),
    ("1000", False),     # boundary: strictly greater than
    (1000, False),
])
def test_string_and_int_limits_agree_on_bulk(limit, is_bulk):
    """Normalising must not change the ANSWER, only stop the crash — a string
    Limit has to select the same timeout an equivalent int would."""
    assert (_as_int(limit) > 1000) is is_bulk


def test_the_reported_traceback_no_longer_reproduces():
    """`params.get('Limit', 0) > 1000` with _paged's real params."""
    params = {"ParentId": "abc", "StartIndex": "0", "Limit": "500"}
    with pytest.raises(TypeError):
        # The old expression, pinned so the regression is unmistakable.
        _ = params.get("Limit", 0) > 1000
    # The current one is fine.
    assert (_as_int(params.get("Limit")) > 1000) is False


def test_request_layer_uses_the_coercion():
    """Guard against someone reinstating the raw comparison."""
    from pathlib import Path

    from core import jellyfin_client

    src = Path(jellyfin_client.__file__).read_text(encoding="utf-8", errors="replace")
    assert "_as_int(params.get('Limit'))" in src
    assert "params.get('Limit', 0) > 1000" not in src, \
        "the unguarded comparison is back — a string Limit will crash the scan again"
