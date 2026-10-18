import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Classic initializer and sequential-run invariants still protect live code. */

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
