# Review of the five unpushed performance commits

Reviewed range: `origin/dev..b4c0f1a51`, oldest first:

- `a15e8fd51`: download recovery, image registration, asynchronous manual imports.
- `808c09f14`: LRClib transport timeouts.
- `734a07887`: Plex/video timeouts and offline caches.
- `d684a3178`: watchdog heartbeat and metadata search deadline.
- `b4c0f1a51`: slskd server endpoint compatibility.

## Validated findings and corrections

1. **P1: expired searches left unbounded provider workers behind.** Each request
   created its own pool and returned without waiting for active work. Twelve
   event-blocked requests left twelve workers running in the reproduction.
   Searches now share eight workers, permit at most 24 running/queued provider
   tasks, cancel queued work at expiry, and do not start fallback searches after
   their deadline. Providers already inside blocking I/O cannot be forcibly
   stopped; they retain their capacity slot until they finish.

2. **P1: video status still had a 120-second read timeout.** `(8, 120)` limited
   TCP connection time, not a server accepting TCP then failing to respond.
   Constructor requests now have short connect AND read timeouts. Status uses
   a separately cached short-timeout client; bulk scans retain the 120-second
   read allowance after the initial handshake. This bounds individual network
   operations, not the entire multi-request status handler.

3. **P2: credential changes could reuse an old Plex connection or cached
   failure.** Both keys used only six token characters. The regression changes
   the token suffix while keeping the prefix and URL unchanged. Cache identity
   now hashes the complete credentials. Cache timestamps start after connection
   attempts finish, reconnects are serialized, and old-client failures cannot
   invalidate a newer activity client.

4. **P2: recovery could start after cancellation was already requested.** My
   original identity check handled cancellation during I/O but not cancellation
   present before scheduling. Such requests are now skipped.

5. **P2: recovery rollback could overwrite a newer attempt.** If submission
   raised after another attempt moved into Processing, the old rollback checked
   only status and object identity. It now compares the exact processing attempt
   before restoring state. The test reproduces a new attempt starting inside a
   failed submission and verifies its state survives.

6. **P2: cached Plex section state could report an already-finished scan as
   active.** PlexAPI caches library sections on the server's Library object.
   The reproduction changes a remote refreshing flag from true to false while
   reusing the client; previously the result stayed true. The status path now
   reloads library data before examining section flags.

The first five regression cases (worker growth, status timeout, two credential
cache cases, and already-requested cancellation) all failed before corrections.
The retry rollback and stale-section reproductions were also run and failed
before their respective fixes. Tests are in `tests/test_performance_review.py`.

## Other commits

The LRClib session injection addresses the missing transport timeout in both
import processing and direct player lookups. The watchdog heartbeat reset
prevents a prior run's timestamp being reused at launch. The slskd endpoint
probe retains the requirement for network login before chat. No additional
production changes were needed in those paths during this review.

## Limits

These changes do not demonstrate that every symptom in the supplied logs is
resolved. Transport read timeouts are inactivity limits, not total deadlines;
providers can still be slow. Import job state remains in-process and is lost on
server restart, as documented in `docs/performance-1245.md`. The review does not
claim a zero-regression guarantee or a live-production performance measurement.

## Validation results

- Broader backend review suite: **284 passed** (lyrics, watchdog/automation,
  chat, import/recovery regressions, video APIs, source caches, activity, search).
- Final focused rerun after the last correction: **74 passed**. This overlaps
  the broader run; the numbers are not a unique combined test count.
- Frontend import feature: **18 passed**.
- Ruff and `git diff --check`: passed.

Corrections are left uncommitted in the working tree; the five existing commits
were not rewritten or pushed. Supplied logs and the unrelated release
announcement were not modified.
