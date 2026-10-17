import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Classic initializer and sequential-run invariants still protect live code. */

describe('the sync page binds its listeners exactly once', () => {
  /**
   * initializeSyncPage is called at boot AND from loadPageData's `case 'sync'`,
   * which runs on every navigation to the page. The markup is static, so the
   * listeners only need binding once — binding per visit stacked a duplicate
   * set each time, and one of them is the Start Sync button whose handler is a
   * toggle. After an even number of visits, one click started the sync and
   * immediately cancelled it.
   */
  const SYNC_SERVICES = readFileSync(resolve(__dirname, '../../static/sync-services.js'), 'utf8');

  function initBody(): string {
    const start = SYNC_SERVICES.indexOf('function initializeSyncPage() {');
    expect(start, 'initializeSyncPage should exist').toBeGreaterThan(-1);
    const end = SYNC_SERVICES.indexOf('\n}', start);
    return SYNC_SERVICES.slice(start, end);
  }

  it('returns early once the bindings have run', () => {
    const body = initBody();
    expect(body).toContain('if (_syncPageListenersBound) return;');
    expect(body).toContain('_syncPageListenersBound = true;');
  });

  it('sets the flag BEFORE binding anything', () => {
    // Guard-then-bind, not bind-then-guard: a throw mid-binding must not leave
    // the flag clear and let the next visit stack a second partial set.
    const body = initBody();
    expect(body.indexOf('_syncPageListenersBound = true;')).toBeLessThan(
      body.indexOf('addEventListener'),
    );
  });

  it('keeps the per-visit refreshes OUTSIDE the guard', () => {
    const body = initBody();
    const guard = body.indexOf('if (_syncPageListenersBound) return;');
    expect(guard).toBeGreaterThan(-1);
    // All three are idempotent refreshes, not bindings, and must run every
    // visit.
    // Each is asserted PRESENT before its position is compared: indexOf gives
    // -1 for a deleted call, and -1 is less than the guard index, so a
    // position-only check passes when the call is gone. Caught by mutation.
    for (const call of [
      'ensureBeatportContentLoaded()',
      'updateBeatportClearButtonState()',
      'initializeLiveLogViewer()',
    ]) {
      const at = body.indexOf(call);
      expect(at, `${call} should still be called`).toBeGreaterThan(-1);
      expect(at, `${call} should run on every visit, not once`).toBeLessThan(guard);
    }
  });

  it('restarts log polling on every visit, because navigation always stops it', () => {
    // This one is load-bearing for a cross-file reason, so it gets its own
    // test. `loadPageData` calls `stopLogPolling()` unconditionally at the top,
    // for EVERY page — including sync itself — and `initializeLiveLogViewer` is
    // the only thing that ever calls `startLogPolling`. Leaving it below the
    // binding guard meant polling stopped on the first navigation and never
    // came back. Only visible with the socket down: `tool:logs` writes the same
    // textarea via `updateLogsFromData`, and the HTTP poll is its twin.
    const INIT = readFileSync(resolve(__dirname, '../../static/init.js'), 'utf8');
    const loadPageData = INIT.slice(
      INIT.indexOf('async function loadPageData(pageId) {'),
      INIT.indexOf("case 'sync':"),
    );
    expect(loadPageData, 'loadPageData should still exist ahead of its sync case').not.toBe('');
    expect(loadPageData).toContain('stopLogPolling();');

    // One call site, and it is the hoisted one — a second copy left at the
    // bottom of the bindings would make this test pass while the bug returned.
    const calls = SYNC_SERVICES.match(/^\s*initializeLiveLogViewer\(\);$/gm) || [];
    expect(calls).toHaveLength(1);
  });

  it('declares the flag at module scope, not inside the function', () => {
    // A `let` inside the function would reset on every call and guard nothing.
    expect(SYNC_SERVICES).toMatch(/^let _syncPageListenersBound = false;$/m);
    expect(initBody()).not.toContain('let _syncPageListenersBound');
  });
});

describe('cancelling a sequential sync stays silent', () => {
  /**
   * The pending `setTimeout(() => this.syncNext(), 1000)` from the previous
   * iteration fires AFTER cancel() has zeroed the queue and nulled startTime.
   * Without a guard it reads `0 >= 0`, calls complete(), and pops a green
   * "Sequential sync completed for 0 playlists in <epoch>s" a second after
   * the user cancelled.
   */
  const CORE = readFileSync(resolve(__dirname, '../../static/core.js'), 'utf8');

  function syncNextBody(): string {
    const start = CORE.indexOf('    async syncNext() {');
    expect(start, 'syncNext should exist').toBeGreaterThan(-1);
    const end = CORE.indexOf('\n    }', start);
    expect(end, 'syncNext should be a complete method').toBeGreaterThan(start);
    return CORE.slice(start, end);
  }

  it('bails out of syncNext once the run is no longer running', () => {
    expect(syncNextBody()).toContain('if (!this.isRunning) return;');
  });

  it('bails BEFORE the completion branch, not after', () => {
    // Order is the whole fix: after the `currentIndex >= queue.length` check
    // the bogus complete() has already fired.
    const body = syncNextBody();
    const guard = body.indexOf('if (!this.isRunning) return;');
    const done = body.indexOf('if (this.currentIndex >= this.queue.length)');
    expect(guard).toBeGreaterThan(-1);
    expect(done, 'the completion branch should still be there').toBeGreaterThan(-1);
    expect(guard).toBeLessThan(done);
  });

  it('cancel still clears the state the guard keys off', () => {
    const start = CORE.indexOf('    cancel() {');
    const body = CORE.slice(start, CORE.indexOf('\n    }', start));
    expect(body).toContain('if (!this.isRunning) return;');
    expect(body).toContain('this.isRunning = false;');
  });
});
