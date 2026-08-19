"""One Deezer budget for every caller (TheHomeGuy's slow playlist loads).

Deezer publishes no rate limit. Their developer FAQ says only "there is no
limitation on data in the API, but there is a query quota" and links to a page
with no number on it; the API sends no ``X-RateLimit-*`` or ``Retry-After``
header, so nothing tells us how close we are. The community figure — repeated by
every third-party wrapper — is **50 requests per 5 seconds**, and there is no
other. There is also no paid tier to escape to: "There is no paid API", and
whitelisting is "not possible, unless there is a commercial agreement".

So we budget **40 of the 50** and hold the rest back for being wrong about it.

Before this there were three numbers and none agreed. ``@rate_limited`` in
deezer_client enforced 1 req/s on nine methods. The two playlist album loops
called ``session.get`` directly behind their own ``time.sleep(0.3)`` and
``sleep_s=0.2``, obeying nothing and counted by nothing — the call tracker read
~0/min during the exact operation hammering the API hardest. Measured against
the live API, those loops ran at 2.07 req/s while the app declared 1.

Ten modules share this quota (enrichment worker, watchlist scanner, soulid
worker, artist detail, liked-match, metadata service, playlist loading, search),
so a per-caller cap is arithmetic that cannot add up.

Two bugs were found by testing this module rather than by reading it, and both
are pinned below: scheduling every queued caller off ``_TIMES[0]`` (a thundering
herd, measured 15.8 req/s against a target of 8), and dropping the minimum gap
that ``core.slskd_throttle`` uses to keep bursts from smearing across a window
boundary.
"""

import threading
import time

import pytest

from core import deezer_throttle as throttle

# The community ceiling we are budgeting against. Nothing may approach it.
REPORTED_DEEZER_CEILING = 50


@pytest.fixture(autouse=True)
def _clean():
    throttle._reset_for_tests()
    yield
    throttle._reset_for_tests()


def _reserve_offsets(n):
    base = time.monotonic()
    return [throttle.reserve_slot() - base for _ in range(n)]


def _busiest_window(times):
    """Most calls falling inside any 5s window."""
    return max(sum(1 for t in times if x <= t < x + throttle.WINDOW_SECONDS - 1e-9)
               for t in [0] for x in times)


# ── the budget ──────────────────────────────────────────────────────────────

def test_the_budget_leaves_real_headroom_under_the_reported_ceiling():
    """The 50 is folklore, not documentation, and there is no header to warn us
    when we are close. Spending all of it would be betting on a rumour."""
    per_second = throttle.MAX_PER_WINDOW / throttle.WINDOW_SECONDS
    assert per_second == 8.0
    assert throttle.MAX_PER_WINDOW <= REPORTED_DEEZER_CEILING * 0.85


def test_reservations_hold_the_cap():
    """The algorithm on its own, no sleeping — this is the guarantee."""
    offsets = _reserve_offsets(120)
    assert _busiest_window(offsets) <= throttle.MAX_PER_WINDOW


def test_the_sustained_rate_is_the_budget():
    offsets = _reserve_offsets(100)
    rate = (len(offsets) - 1) / (offsets[-1] - offsets[0])
    assert 7.5 <= rate <= 8.5, rate


def test_queued_callers_do_not_all_fire_at_once():
    """The first bug. Scheduling off ``_TIMES[0]`` gave every waiting caller the
    same deadline, so they released together: 15.8 req/s against a target of 8,
    and 41 calls inside a 5s window. The slot has to be spaced from the
    reservation that ages out to make room for THIS one."""
    offsets = _reserve_offsets(90)
    gaps = [b - a for a, b in zip(offsets, offsets[1:])]
    assert min(gaps) >= throttle.MIN_GAP_SECONDS - 1e-6, f"min gap {min(gaps)}"


def test_calls_are_paced_rather_than_bursted():
    """The second bug. A burst of MAX at once is legal on paper — an empty
    window is free — but calls land slightly after their slot, so two adjacent
    bursts smear together and a real window sees over the cap. Pacing at
    WINDOW/MAX keeps the budget under jitter."""
    assert throttle.MIN_GAP_SECONDS == pytest.approx(
        throttle.WINDOW_SECONDS / throttle.MAX_PER_WINDOW)
    offsets = _reserve_offsets(40)
    assert offsets[-1] > throttle.WINDOW_SECONDS * 0.9, "40 calls must not land in one instant"


# ── under real threads ──────────────────────────────────────────────────────

@pytest.mark.parametrize("workers", [1, 4, 12])
def test_concurrent_callers_share_one_budget(workers):
    """Ten modules hit Deezer at once. Two threads arriving together must get
    two DIFFERENT slots, not both compute "no wait"."""
    total = 48
    stamps, lock = [], threading.Lock()

    def work():
        for _ in range(total // workers):
            throttle.wait_for_slot()
            with lock:
                stamps.append(time.monotonic())

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    rate = (len(stamps) - 1) / (stamps[-1] - stamps[0])
    assert 7.0 <= rate <= 9.0, f"{workers} threads gave {rate:.2f} req/s"
    # +1 is the fencepost: at 0.125s spacing, 41 points span exactly 5.0s, so
    # steady-state 8/s measures as 41 per window. Still far under the 50.
    busiest = _busiest_window(stamps)
    assert busiest <= throttle.MAX_PER_WINDOW + 1
    assert busiest < REPORTED_DEEZER_CEILING


# ── backing off together ────────────────────────────────────────────────────

def test_a_quota_error_stops_every_caller_not_just_the_one_that_hit_it():
    """Otherwise one caller walks into the wall and the other nine follow it in
    one at a time."""
    throttle.note_quota_exceeded(retry_after=6)
    start = time.monotonic()
    assert throttle.wait_for_slot() is True
    assert time.monotonic() - start >= 5.0


def test_the_cooldown_is_bounded_at_both_ends():
    for given, low, high in [(None, 5, 15), (0, 5, 15), (1, 5, 15), (9999, 5, 121), ('junk', 5, 15)]:
        throttle._reset_for_tests()
        throttle.note_quota_exceeded(retry_after=given)
        remaining = throttle.status()['cooldown_remaining']
        assert low <= remaining <= high, f"retry_after={given!r} -> {remaining}"


def test_an_interactive_caller_can_decline_to_wait():
    """A request worker must not block for minutes while a background scan
    drains the window."""
    _reserve_offsets(throttle.MAX_PER_WINDOW)
    assert throttle.reserve_slot(max_wait_seconds=0.01) is None
    assert throttle.wait_for_slot(max_wait_seconds=0.01) is False


def test_declining_does_not_consume_a_slot():
    _reserve_offsets(throttle.MAX_PER_WINDOW)
    before = throttle.status()['calls_in_window']
    throttle.reserve_slot(max_wait_seconds=0.01)
    assert throttle.status()['calls_in_window'] == before


# ── deezer reports the quota failure in the BODY, with HTTP 200 ─────────────

@pytest.mark.parametrize("payload,expected", [
    ({'error': {'type': 'Exception', 'code': 4, 'message': 'Quota limit exceeded'}}, True),
    ({'error': {'code': '4'}}, True),
    ({'error': {'code': 800, 'message': 'no data'}}, False),
    ({'error': 'something'}, False),
    ({'data': []}, False),
    ({}, False),
    (None, False),
    ('not a dict', False),
])
def test_the_quota_error_is_recognised_in_the_body(payload, expected):
    """``resp.ok`` is True on a quota failure — Deezer answers 200 and puts the
    error in the body, so a caller checking only the status code sails past it."""
    assert throttle.is_quota_error(payload) is expected


# ── nothing may bypass the budget ───────────────────────────────────────────
#
# The original bug was not a wrong number, it was call sites obeying no number
# at all. An audit found eleven: six in the download client, the two playlist
# album loops, and one-offs in soulid_worker, service_search, web_server and
# connection_test. The last of those was found BY this test while it was being
# written. A test is the only thing that stops the twelfth appearing.

import pathlib
import re as _re

# The request verbs. ``_api_get`` is the download client's own throttled helper.
_CALL = _re.compile(r'(?:\.(?:get|post)|\b_api_get)\(')

# Markers that a url is Deezer's PUBLIC api. deezer_client builds its urls from
# BASE_URL, so the literal host never appears at those call sites.
_PUBLIC_MARKERS = ('api.deezer.com', 'BASE_URL', 'base_url')

# Different infrastructure with its own limits — not this quota.
_NOT_PUBLIC_API = ('gw-light', 'media.deezer.com', '_GW_API', '_MEDIA_API',
                   'media_url', '/image?size=')


def _public_api_call_sites():
    """Every statement issuing a request to the public Deezer API.

    Scanned from the VERB outward, not from the url. Scanning from the url both
    missed the wrapped ``_api_get(...)`` calls and flagged
    ``image_url = f"https://api.deezer.com/artist/…/image"`` — a string handed
    to an <img src> for the BROWSER to fetch, which is not our quota to spend.
    """
    sites = []
    roots = [pathlib.Path('core'), pathlib.Path('web_server.py')]
    files = []
    for root in roots:
        files.extend(sorted(root.rglob('*.py')) if root.is_dir() else [root])
    for path in files:
        if path.name == 'deezer_throttle.py':
            continue
        lines = path.read_text(encoding='utf-8').splitlines()
        deezer_module = 'deezer' in path.name
        for i, line in enumerate(lines):
            if not _CALL.search(line):
                continue
            stmt = '\n'.join(lines[i:i + 4])
            if not any(m in stmt for m in _PUBLIC_MARKERS):
                continue
            if not deezer_module and 'api.deezer.com' not in stmt:
                continue          # BASE_URL in a non-deezer module isn't ours
            if any(bad in stmt for bad in _NOT_PUBLIC_API):
                continue
            sites.append((str(path), i + 1, line.strip()))
    return sites


def _is_covered(path, lineno, line):
    src = pathlib.Path(path).read_text(encoding='utf-8')
    lines = src.splitlines()
    if '_api_get(' in line:
        return True                                   # the throttled helper
    if 'wait_for_slot' in '\n'.join(lines[max(0, lineno - 13):lineno]):
        return True                                   # throttled inline
    # deezer_client's own methods are covered by the @rate_limited decorator
    for j in range(lineno - 1, max(0, lineno - 60), -1):
        stripped = lines[j].lstrip()
        if stripped.startswith('def '):
            return any('@rate_limited' in lines[k] for k in range(max(0, j - 3), j))
    return False


def test_every_public_deezer_call_goes_through_the_shared_budget():
    offenders = [f"{p}:{n}  {ln[:88]}" for p, n, ln in _public_api_call_sites()
                 if not _is_covered(p, n, ln)]
    assert offenders == [], (
        "these Deezer calls bypass core.deezer_throttle:\n  " + "\n  ".join(offenders))


def test_the_audit_actually_finds_the_call_sites():
    """A matcher that quietly matches nothing would make the test above pass
    forever. Ten-plus known public call sites exist across four files."""
    sites = _public_api_call_sites()
    files = {p for p, _, _ in sites}
    assert len(sites) >= 10, f"only found {len(sites)}: {sites}"
    assert len(files) >= 4, files


def test_no_home_made_sleep_survives_as_a_deezer_limiter():
    """Both album loops paced themselves with a private ``time.sleep`` that no
    other caller could see and the call tracker never counted."""
    for name in ('core/deezer_client.py', 'core/deezer_download_client.py'):
        src = pathlib.Path(name).read_text(encoding='utf-8')
        for i, line in enumerate(src.splitlines(), 1):
            if 'time.sleep' not in line or line.lstrip().startswith('#'):
                continue
            near = '\n'.join(src.splitlines()[max(0, i - 6):i + 2])
            # a real request nearby, not prose about one — this module's own
            # docstrings discuss both the old sleeps and the api host
            if not _CALL.search(near):
                continue
            assert 'api.deezer.com' not in near, f"{name}:{i} still sleeps beside a Deezer call"


def test_a_declined_slot_is_honoured_not_ignored():
    """``max_wait_seconds`` only means something if the caller acts on the
    refusal. Reserving nothing and then issuing the request anyway is an
    unbudgeted call wearing a safeguard's clothes — which is what the connection
    test did on the first pass."""
    src = pathlib.Path('core/connection_test.py').read_text(encoding='utf-8')
    i = src.index('wait_for_slot(max_wait_seconds')
    following = src[i:i + 300]
    assert 'if not wait_for_slot' in src[max(0, i - 40):i + 40], \
        "the return value has to be checked"
    assert 'return' in following, "a declined slot must not fall through to the request"
