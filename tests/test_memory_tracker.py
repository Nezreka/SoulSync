"""Seam tests for the #802 memory-growth diagnostic (core/diagnostics)."""

from __future__ import annotations

from types import SimpleNamespace

import core.diagnostics.memory_tracker as mt


def teardown_function(_fn):
    # Never leave tracemalloc running across tests — it shadows every
    # allocation in the process.
    mt.stop_tracking()


def test_report_without_tracking_is_a_hint_not_an_error():
    mt.stop_tracking()
    out = mt.report()
    assert out['tracking'] is False
    assert 'start' in out['hint']


def test_start_report_stop_roundtrip_captures_growth():
    assert mt.start_tracking()['tracking'] is True
    # Idempotent start
    assert mt.start_tracking()['already_running'] is True

    # Allocate something measurable after the baseline.
    # bytearray(1000) allocates at RUNTIME — a constant expression like
    # 'x' * 1000 gets folded into ONE shared string and traces as ~40KB.
    hog = [bytearray(1000) for _ in range(5000)]  # ~5 MB, genuinely allocated

    out = mt.report(top=10)
    assert out['tracking'] is True
    assert out['elapsed_seconds'] is not None
    assert out['traced_current_mb'] > 0
    assert isinstance(out['top_growth'], list) and out['top_growth']
    # The hog must show up as growth attributed to THIS file.
    top_locations = ' '.join(s['location'] for s in out['top_growth'])
    assert 'test_memory_tracker.py' in top_locations
    assert any(s['size_diff_mb'] > 1 for s in out['top_growth'])
    del hog

    stopped = mt.stop_tracking()
    assert stopped == {'tracking': False, 'was_tracking': True}
    assert mt.is_tracking() is False


def test_format_stat_projects_duck_typed_stat():
    frame = SimpleNamespace(filename='core/foo.py', lineno=42)
    stat = SimpleNamespace(
        size=2 * 1024 * 1024, size_diff=1024 * 1024,
        count=10, count_diff=4,
        traceback=[frame, SimpleNamespace(filename='core/bar.py', lineno=7)],
    )
    out = mt.format_stat(stat)
    assert out['location'] == 'core/bar.py:7'
    assert out['trace'] == ['core/foo.py:42', 'core/bar.py:7']
    assert out['size_mb'] == 2.0 and out['size_diff_mb'] == 1.0
    assert out['count'] == 10 and out['count_diff'] == 4
