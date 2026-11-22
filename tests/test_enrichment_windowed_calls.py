"""_get_windowed_calls keeps one entry per change of the total and still
counts the calls of the last hour and day like the every-poll history did."""

from __future__ import annotations

import collections

import web_server


def _every_poll(history, now, total):
    """The original: an entry on every poll, first entry inside the window."""
    history.append((now, total))
    oldest_1h = oldest_24h = total
    found_24h = False
    for ts, t in history:
        if not found_24h and ts >= now - 86400:
            oldest_24h, found_24h = t, True
        if ts >= now - 3600:
            oldest_1h = t
            break
    return max(0, total - oldest_1h), max(0, total - oldest_24h)


def test_same_counts_as_the_every_poll_history(monkeypatch):
    clock = {'now': 1_000_000.0}
    monkeypatch.setattr(web_server.time, 'time', lambda: clock['now'])
    monkeypatch.setattr(web_server, '_enrichment_activity_log', {})
    oracle = collections.deque(maxlen=17300)
    total = 0
    for step in range(30 * 3600 // 5):           # 30 hours, a poll every 5 s
        clock['now'] += 5
        hour = step * 5 // 3600
        if hour in (2, 3, 9, 26) and step % 7 == 0:  # bursts of work, idle otherwise
            total += 3
        expected = _every_poll(oracle, clock['now'], total)
        assert web_server._get_windowed_calls('mb', total) == expected, step
    kept = web_server._enrichment_activity_log['mb']
    assert len(kept) < len(oracle) // 20            # idle polls are not stored
