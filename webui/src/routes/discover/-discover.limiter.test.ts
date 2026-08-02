import { describe, expect, it } from 'vitest';

import { DISCOVER_REQUEST_LIMIT, createLimiter } from './-discover.limiter';

/** A promise you resolve by hand, so concurrency is observable rather than timed. */
function deferred<T = void>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('the shared request limiter', () => {
  it('matches the vanilla pool size', () => {
    // Same number, and for the same reason: Flask + GIL contention, not
    // browser connection limits.
    expect(DISCOVER_REQUEST_LIMIT).toBe(5);
  });

  it('never runs more than `limit` at once', async () => {
    const limiter = createLimiter(3);
    const gates = Array.from({ length: 8 }, () => deferred());
    let started = 0;
    let peak = 0;

    const runs = gates.map((g) =>
      limiter.run(async () => {
        started++;
        peak = Math.max(peak, limiter.active);
        await g.promise;
      }),
    );

    await Promise.resolve();
    await Promise.resolve();
    expect(started).toBe(3); //          only the first three left the gate
    expect(limiter.pending).toBe(5);

    gates.forEach((g) => g.resolve());
    await Promise.all(runs);
    expect(peak).toBeLessThanOrEqual(3);
    expect(started).toBe(8); //          but everything eventually ran
  });

  it('starts a queued operation as soon as a slot frees', async () => {
    const limiter = createLimiter(1);
    const first = deferred();
    let secondStarted = false;

    const a = limiter.run(() => first.promise);
    const b = limiter.run(async () => {
      secondStarted = true;
    });

    await Promise.resolve();
    expect(secondStarted).toBe(false); // still blocked behind the first

    first.resolve();
    await a;
    await b;
    expect(secondStarted).toBe(true);
  });

  it('frees the slot when an operation REJECTS', async () => {
    // Without this, one failing shelf permanently shrinks the pool and the
    // page degrades a little more with every error.
    const limiter = createLimiter(1);
    await expect(limiter.run(() => Promise.reject(new Error('boom')))).rejects.toThrow('boom');
    expect(limiter.active).toBe(0);
    await expect(limiter.run(async () => 'ok')).resolves.toBe('ok');
  });

  it('propagates results and errors untouched', async () => {
    const limiter = createLimiter(2);
    await expect(limiter.run(async () => 42)).resolves.toBe(42);
    await expect(limiter.run(() => Promise.reject(new Error('nope')))).rejects.toThrow('nope');
  });

  it('runs everything in FIFO order', async () => {
    const limiter = createLimiter(1);
    const order: number[] = [];
    const runs = [1, 2, 3, 4].map((n) =>
      limiter.run(async () => {
        order.push(n);
      }),
    );
    await Promise.all(runs);
    expect(order).toEqual([1, 2, 3, 4]);
  });

  it('reset() drops the queue and clears the in-flight count', async () => {
    // Test-only escape hatch for the module-level singleton. Without it,
    // queued work outlives the test that scheduled it.
    const limiter = createLimiter(1);
    const gate = deferred();
    const running = limiter.run(() => gate.promise);
    limiter.run(async () => 'queued');
    limiter.run(async () => 'also queued');
    expect(limiter.pending).toBe(2);

    limiter.reset();
    expect(limiter.pending).toBe(0);
    expect(limiter.active).toBe(0);

    gate.resolve();
    await running;
    // Usable again straight afterwards.
    await expect(limiter.run(async () => 'fresh')).resolves.toBe('fresh');
  });

  it('drains fully even when every operation fails', async () => {
    const limiter = createLimiter(2);
    const runs = Array.from({ length: 6 }, () =>
      limiter.run(() => Promise.reject(new Error('x'))).catch(() => 'handled'),
    );
    await expect(Promise.all(runs)).resolves.toEqual(Array(6).fill('handled'));
    expect(limiter.active).toBe(0);
    expect(limiter.pending).toBe(0);
  });
});
