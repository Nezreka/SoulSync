"""Regression test: /status must survive concurrent polling.

_get_windowed_calls() appends to and scans a shared per-service deque. Every
/status request walks it for each enrichment service, so two overlapping
requests (e.g. several browser tabs, or the webui E2E sweep) used to race:
one thread appended while another iterated, raising
"RuntimeError: deque mutated during iteration" and turning /status into a 500.
"""

import collections
import threading
import time

import web_server


def test_windowed_calls_survive_concurrent_status_polls():
    key = "_test_windowed_calls_concurrency"
    now = time.time()

    # Entries older than 1h but inside 24h force the scan to walk the whole
    # deque (the 1h cutoff is only reached at the freshly-appended tail),
    # which is what makes the iterate-while-append race likely in practice.
    web_server._enrichment_activity_log[key] = collections.deque(
        ((now - 7200 + i * 0.01, i) for i in range(17000)), maxlen=17300
    )

    errors = []

    def hammer(offset):
        try:
            for i in range(300):
                web_server._get_windowed_calls(key, 20000 + offset + i)
        except RuntimeError as exc:  # noqa: PERF203 - the assertion target
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(t * 1000,)) for t in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        web_server._enrichment_activity_log.pop(key, None)

    assert not errors, f"concurrent /status polls raced on the activity deque: {errors[0]}"
