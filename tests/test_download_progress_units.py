"""Every download client reports progress as a PERCENT (0-100).

Found while re-auditing #1197. The notification helper's "a value <= 1 must be
a fraction" guess was fixed there; the same guess lived in
``core.downloads.status._engine_progress_pct``, which feeds the progress bar
and percentage on every active downloads row.

It existed for one reason: amazon_download_client reported ``downloaded/total``
as a 0-1 fraction and used ``1.0`` to mean complete, while soulseek, deezer,
tidal and lidarr all reported 0-100. The guess cannot tell amazon's 1.0 (done)
from deezer's 1.0 (one percent), so it inflated the opening moments of every
non-amazon download: 0.5% rendered as 50%, 1.0% as 100%.

The fix normalised the outlier instead of sharpening the guess. These tests
pin BOTH halves — the unit at each producer, and the absence of any rescaling
downstream — because either one silently reintroduces the bug.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.downloads.status import _engine_progress_pct

_ROOT = Path(__file__).resolve().parents[1]


def test_the_normaliser_never_rescales_a_value():
    """1.0 means one percent, and must survive as one percent."""
    for value in (0, 0.5, 1, 1.0, 1.49, 2, 42, 99.9, 100):
        assert _engine_progress_pct({"progress": value}) == float(value)


def test_a_download_in_its_first_percent_is_not_reported_as_finished():
    # the shape wishx would have seen on a row: a big file barely started
    assert _engine_progress_pct({"progress": 0.5}) == 0.5
    assert _engine_progress_pct({"progress": 1.0}) == 1.0


def test_junk_and_missing_progress_stay_zero():
    assert _engine_progress_pct(None) == 0
    assert _engine_progress_pct({}) == 0
    assert _engine_progress_pct({"progress": "nonsense"}) == 0


def test_amazon_reports_percent_like_everyone_else():
    """The outlier that forced the guess. 1.0 used to mean 'complete' here."""
    src = (_ROOT / "core" / "amazon_download_client.py").read_text(encoding="utf-8")
    assert '"progress": (downloaded / total * 100) if total else 0.0,' in src
    assert '"progress": 100.0' in src
    # the fraction forms must be gone
    assert '"progress": downloaded / total if total else 0.0,' not in src
    assert '"progress": 1.0' not in src


def test_no_client_writes_a_zero_to_one_fraction():
    """A future client reintroducing fractions breaks every consumer quietly,
    so pin the contract across all of them."""
    clients = [
        "amazon_download_client.py",
        "deezer_download_client.py",
        "tidal_download_client.py",
        "lidarr_download_client.py",
    ]
    # a literal fractional progress assignment, e.g. `'progress': 0.42`
    fractional = re.compile(r"""["']progress["']\s*:\s*0\.\d""")
    for name in clients:
        src = (_ROOT / "core" / name).read_text(encoding="utf-8")
        for line in src.splitlines():
            if fractional.search(line):
                # 0.0 is unambiguous (zero percent is zero either way)
                assert re.search(r"""["']progress["']\s*:\s*0\.0\b""", line), (
                    f"{name} writes a fractional progress: {line.strip()}")
